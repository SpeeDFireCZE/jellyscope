"""Klient pro Jellyfin API.

Jediny soubor v projektu, ktery vi, jak Jellyfin mluvi. Zbytek aplikace se
pta tohohle modulu a nemusi resit hlavicky, strankovani ani nazvy poli.

Tomuhle se rika **oddeleni zodpovednosti**: kdyz Jellyfin za rok zmeni API,
opravujes jeden soubor, ne dvacet mist rozesetych po projektu.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from . import languages

log = logging.getLogger("jellyscope.jellyfin")

# Pole, ktera chceme u polozek knihovny navic. Bez nich Jellyfin vraci
# jen holy nazev a id.
ITEM_FIELDS = ",".join([
    "Path",
    "MediaSources",
    "MediaStreams",
    "Genres",
    "ProductionYear",
    "DateCreated",
    "ParentId",
    "Overview",
    # Identifikatory z externich databazi (TMDB, IMDB, TVDB). Diky nim
    # poznáme, ze prekodovany soubor s novym ItemId je porad tentyz film -
    # viz scanner._merge_by_tmdb().
    "ProviderIds",
])


class JellyfinError(RuntimeError):
    """Cokoliv, co se pokazi pri komunikaci s Jellyfinem."""


# Jedno cislo pro vsechny faze spojeni bylo malo. Navazani spojeni ma byt
# rychle - kdyz server neodpovida, nema smysl cekat pul minuty. Ale
# ODPOVED na dotaz o tri sta polozkach si Jellyfin u velke knihovny
# pripravuje klidne minutu, zvlast kdyz zaroven prehrava nebo prekoduje.
# Se spolecnym trycetivterinovym stropem plna synchronizace na velke
# knihovne padala - a hlaska pritom tvrdila, ze se nepodarilo spojit,
# takze se chyba hledala v adrese a klici, kde nebyla.
DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=180.0, write=30.0, pool=10.0)

# Kolikrat zopakovat dotaz, ktery skoncil vyprsenim casu. Opakovat GET je
# bezpecne - nic nemeni. Kratke zadrhnuti (Jellyfin zrovna prekoduje) tim
# prezije cely scan misto aby spadl v pulce.
TIMEOUT_RETRIES = 2

# Kratky strop pro dotazy, u kterych ceka clovek u obrazovky (tlacitko
# "Otestovat spojeni") nebo ktere se opakuji kazdych par vterin (collector).
# Tam je dlouhe cekani na skodu: misto odpovedi "server neodpovida" by se
# jen tocilo kolecko a dalsi dotaz by se navrsil na predchozi.
QUICK_TIMEOUT = httpx.Timeout(connect=5.0, read=20.0, write=10.0, pool=5.0)


class JellyfinClient:
    """Tenka obalka nad HTTP voláními do Jellyfinu.

    Pouziva se jako context manager, aby se spojeni vzdy poradne zavrelo:

        async with JellyfinClient(url, key) as jf:
            info = await jf.system_info()
    """

    def __init__(self, base_url: str, api_key: str,
                 timeout: httpx.Timeout | float | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout if timeout is not None else DEFAULT_TIMEOUT,
            headers={
                # Moderni zpusob autentizace. Klic se posila v hlavicce,
                # ne v URL - do URL nepatri, protoze se loguje v proxy,
                # v historii prohlizece a jinde, kde ho nechces mit.
                "Authorization": f'MediaBrowser Token="{api_key}"',
                # Starsi Jellyfiny znaji tuhle. Poslat obe nic nestoji.
                "X-Emby-Token": api_key,
                "Accept": "application/json",
            },
        )

    async def __aenter__(self) -> "JellyfinClient":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """Jedno GET volani. Vsechny chyby prevede na JellyfinError."""
        for pokus in range(TIMEOUT_RETRIES + 1):
            try:
                response = await self._client.get(path, params=params)
                break
            except httpx.TimeoutException as exc:
                # Vyprseni casu neni totez co "nespojil jsem se". Server
                # odpovida, jen pomalu - a to je jina rada pro uzivatele.
                if pokus < TIMEOUT_RETRIES:
                    log.warning("Jellyfin neodpovedel vcas na %s, zkousim znovu"
                                " (%d/%d)", path, pokus + 1, TIMEOUT_RETRIES)
                    await asyncio.sleep(2 * (pokus + 1))
                    continue
                raise JellyfinError(
                    f"Jellyfin neodpovedel vcas na {path} ({exc.__class__.__name__}). "
                    f"Server bezi, ale odpoved trva dele nez povoleny cas - "
                    f"typicky u velke knihovny nebo kdyz zaroven prekoduje."
                ) from exc
            except httpx.RequestError as exc:
                raise JellyfinError(f"Nepodarilo se spojit s Jellyfinem: {exc}") from exc

        if response.status_code == 401:
            raise JellyfinError("Jellyfin odmitl API klic (401). Zkontroluj JELLYFIN_API_KEY.")
        if response.status_code == 403:
            raise JellyfinError("API klic nema opravneni (403).")
        if response.status_code >= 400:
            raise JellyfinError(
                f"Jellyfin vratil chybu {response.status_code} na {path}: "
                f"{response.text[:200]}"
            )

        if not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise JellyfinError(f"Jellyfin vratil neplatny JSON na {path}") from exc

    # -----------------------------------------------------------------
    # Konkretni dotazy
    # -----------------------------------------------------------------

    async def system_info(self) -> dict[str, Any]:
        """Zakladni info o serveru. Slouzi hlavne jako test spojeni."""
        return await self._get("/System/Info") or {}

    async def users(self) -> list[dict[str, Any]]:
        return await self._get("/Users") or []

    async def sessions(self) -> list[dict[str, Any]]:
        """Co se prave ted na serveru deje.

        Tohle je zdroj cele historie prehravani. Jellyfin nam rekne jen
        pritomnost ("Petr hraje ted tuhle epizodu, je na 12. minute").
        Historii z toho poskladame my - viz collector.py.
        """
        return await self._get("/Sessions") or []

    async def virtual_folders(self) -> list[dict[str, Any]]:
        """Knihovny vcetne cest na disku."""
        return await self._get("/Library/VirtualFolders") or []

    async def storage(self) -> dict[str, Any]:
        """Kolik mista maji slozky serveru - podle SAMOTNEHO Jellyfinu.

        Zasadni rozdil proti `shutil.disk_usage`: ta meri disk, na kterem
        bezi JELLYSCOPE. Data ale byvaji jinde - na NASu, na jinem stroji,
        v jinem kontejneru - a pak merime uplne cizi disk. Jellyfin sedi
        u tech souboru, takze se ptame jeho.

        Endpoint pribyl az v novejsim Jellyfinu. Na starsim vrati 404
        a to NENI chyba: vratime prazdno a volajici sahne po zaloznim
        zpusobu. Shodit kvuli tomu synchronizaci by bylo neumerne.
        """
        try:
            return await self._get("/System/Storage") or {}
        except JellyfinError as chyba:
            log.info("Jellyfin nezna /System/Storage (%s) - misto se zjisti jinak",
                     chyba)
            return {}

    async def image_bytes(
        self, item_id: str, kind: str = "Primary", max_width: int = 400
    ) -> tuple[bytes, str] | None:
        """Stahne obrazek polozky z Jellyfinu.

        Obrazky vodime pres nas server, i kdyz by je prohlizec mohl brat
        z Jellyfinu primo. Duvod: takhle se adresa Jellyfinu ani API klic
        nikdy nedostanou do stranky. Kdyby si obrazky tahal prohlizec sam,
        musel by o obojim vedet.
        """
        try:
            response = await self._client.get(
                f"/Items/{item_id}/Images/{kind}",
                params={"maxWidth": max_width, "quality": 90},
            )
        except httpx.RequestError:
            return None

        if response.status_code != 200 or not response.content:
            return None
        return response.content, response.headers.get("content-type", "image/jpeg")

    async def items_page(
        self,
        start_index: int,
        limit: int,
        item_types: str = "Movie,Episode",
        user_id: str | None = None,
        parent_id: str | None = None,
        sort_by: str = "SortName",
        sort_order: str = "Ascending",
    ) -> dict[str, Any]:
        """Jedna stranka polozek knihovny.

        Knihovna muze mit desitky tisic polozek. Kdybychom si o ne rekli
        najednou, Jellyfin i nase pamet by protestovaly - proto strankujeme.
        """
        params: dict[str, Any] = {
            "Recursive": "true",
            "IncludeItemTypes": item_types,
            "Fields": ITEM_FIELDS,
            "StartIndex": start_index,
            "Limit": limit,
            "EnableTotalRecordCount": "true",
            "SortBy": sort_by,
            "SortOrder": sort_order,
        }
        if parent_id:
            params["ParentId"] = parent_id
        path = f"/Users/{user_id}/Items" if user_id else "/Items"
        return await self._get(path, params) or {}

    async def items_by_ids(self, ids: list[str]) -> list[dict[str, Any]]:
        """Polozky podle konkretnich ItemId.

        Pouziva se po importu historie: Jellystat ani Playback Reporting
        neposilaji tmdb ID, ale posilaji ItemId. Kdyz ho Jellyfin jeste
        zna, doptame se na jeho ProviderIds a mame podle ceho zaznamy
        sparovat s knihovnou.

        Ptame se po davkach - adresa s tisici id by narazila na limit
        delky URL, ktery ma kazdy server jiny.
        """
        found: list[dict[str, Any]] = []
        for start in range(0, len(ids), 50):
            davka = [i for i in ids[start:start + 50] if i]
            if not davka:
                continue
            data = await self._get("/Items", {
                "Ids": ",".join(davka),
                "Fields": ITEM_FIELDS,
                "Recursive": "true",
            }) or {}
            found.extend(data.get("Items") or [])
        return found

    async def iter_items(
        self,
        page_size: int = 300,
        item_types: str = "Movie,Episode",
        parent_id: str | None = None,
    ):
        """Postupne prochazi vsechny polozky (volitelne jen v jedne knihovne).

        `yield` z teto funkce dela **generator**: polozky se predavaji po
        strankach, takze v pameti nikdy neni cela knihovna najednou.
        """
        user_id = None
        start = 0

        while True:
            try:
                page = await self.items_page(start, page_size, item_types, user_id, parent_id)
            except JellyfinError:
                # Nektere starsi verze Jellyfinu neumi /Items bez uzivatele.
                # Zkusime to jeste jednou pod uctem prvniho administratora.
                if user_id is not None:
                    raise
                user_id = await self._first_admin_id()
                if user_id is None:
                    raise
                continue

            batch = page.get("Items") or []
            if not batch:
                return
            for item in batch:
                yield item

            start += len(batch)
            total = page.get("TotalRecordCount")
            if total is not None and start >= total:
                return

    async def recent_items(self, od: str | None, strop: int = 2000,
                           item_types: str = "Movie,Episode",
                           parent_id: str | None = None) -> list[dict[str, Any]]:
        """Polozky pridane od zadaneho casu (UTC, tvar "2026-08-14 09:00:00").

        Jellyfin neumi filtrovat podle data pridani primo v dotazu, takze
        si reknem o polozky **serazene od nejnovejsi** a prestaneme cist,
        jakmile narazime na starsi, nez chceme. Diky tomu se stahne presne
        to, co pribylo, a ani polozka navic.

        `strop` je pojistka pro pripad, ze se do knihovny naleje spousta
        titulu najednou (prvni naplneni, hromadny import). Bez nej by
        "rychla" synchronizace stahla celou knihovnu.
        """
        nalezene: list[dict[str, Any]] = []
        user_id = None
        start = 0
        stranka = 200

        while len(nalezene) < strop:
            try:
                page = await self.items_page(
                    start, stranka, item_types, user_id, parent_id,
                    sort_by="DateCreated", sort_order="Descending")
            except JellyfinError:
                if user_id is not None:
                    raise
                user_id = await self._first_admin_id()
                if user_id is None:
                    raise
                continue

            davka = page.get("Items") or []
            if not davka:
                break

            for item in davka:
                vytvoreno = str(item.get("DateCreated") or "")
                # Porovnavame texty, ne data: oba tvary zacinaji
                # "RRRR-MM-DD hh:mm", takze abecedni poradi odpovida
                # casovemu. Prevod na datum by tu byl prace navic.
                #
                # `od = None` znamena "vezmi nejnovejsi, kolik se vejde" -
                # pouziva se pri prvnim behu, kdy jeste nic nemame.
                if od and vytvoreno and vytvoreno.replace("T", " ")[:19] < od:
                    return nalezene          # dal uz jsou jen starsi
                nalezene.append(item)

            start += len(davka)
            total = page.get("TotalRecordCount")
            if total is not None and start >= total:
                break

        return nalezene[:strop]

    async def item_count(self, item_types: str = "Movie,Episode",
                         parent_id: str | None = None) -> int:
        """Kolik polozek knihovna obsahuje - bez jejich stahovani.

        Jellyfin posila `TotalRecordCount` u kazde stranky, takze staci
        poprosit o stranku o velikosti nula. Diky tomu se da jeste pred
        zacatkem synchronizace rict, kolik prace to bude - a ukazat
        smysluplny ukazatel prubehu misto tocitka bez konce.
        """
        try:
            page = await self.items_page(0, 0, item_types, None, parent_id)
        except JellyfinError:
            user_id = await self._first_admin_id()
            if user_id is None:
                return 0
            page = await self.items_page(0, 0, item_types, user_id, parent_id)
        return int(page.get("TotalRecordCount") or 0)

    async def _first_admin_id(self) -> str | None:
        for user in await self.users():
            if (user.get("Policy") or {}).get("IsAdministrator"):
                return user.get("Id")
        return None


# ---------------------------------------------------------------------------
# Vytahovani technickych dat z odpovedi Jellyfinu
# ---------------------------------------------------------------------------

def _dynamicky_rozsah(video: dict[str, Any]) -> str | None:
    """SDR / HDR / DOVI z toho, co o video stope rika Jellyfin.

    `VideoRange` zna jen SDR a HDR, takze Dolby Vision se pod nim schova
    jako obycejne HDR. Rozlisi ho az `VideoRangeType` ("DOVI",
    "DOVIWithHDR10" - to je profil 8.1). Bez nej mel prehled "SDR / HDR /
    Dolby Vision" treti sloupec vzdycky prazdny, prestoze knihovna DV
    obsahovala; a co horsi, tentyz soubor se pocital ruzne podle toho,
    odkud se technicke udaje vzaly - ffprobe DV rozlisuje.

    Neznamy rozsah vraci None, ne "SDR". Jellyfin umi odpovedet "Unknown"
    a to se drive ulozilo tak, jak prislo, takze v grafu pribyl sloupec
    doslova nazvany "Unknown" vedle naseho "nezname".
    """
    typ = str(video.get("VideoRangeType") or "").strip().upper()
    if typ.startswith("DOVI"):
        return "DOVI"
    if typ in {"HDR", "HDR10", "HDR10PLUS", "HLG"}:
        return "HDR"
    if typ == "SDR":
        return "SDR"

    rozsah = str(video.get("VideoRange") or "").strip().upper()
    return rozsah if rozsah in {"HDR", "SDR"} else None


def extract_tech_from_item(item: dict[str, Any]) -> dict[str, Any]:
    """Z odpovedi Jellyfinu vytahne technicke udaje o souboru.

    Struktura je zanorena: item -> MediaSources -> MediaStreams -> jednotlive
    stopy (video, zvuk, titulky). Nas zajima prvni video stopa a prvni
    zvukova stopa.

    Vsude pouzivame `.get()` s vychozi hodnotou. Chybejici pole je v realnych
    datech normalni stav, ne vyjimecna situace - a padat kvuli nemu by bylo
    zbytecne.
    """
    sources = item.get("MediaSources") or []
    if not sources:
        return {}

    source = sources[0]
    streams = source.get("MediaStreams") or []

    video = next((s for s in streams if s.get("Type") == "Video"), None)
    audio_streams = [s for s in streams if s.get("Type") == "Audio"]
    subtitle_streams = [s for s in streams if s.get("Type") == "Subtitle"]
    audio = audio_streams[0] if audio_streams else None

    tech: dict[str, Any] = {
        "container": source.get("Container"),
        "size_bytes": source.get("Size"),
        "bitrate": source.get("Bitrate"),
        # Jazyky sjednotime hned tady, at se do databaze nedostane
        # ctvero ruznych zapisu tehoz jazyka.
        "audio_languages": languages.pack(s.get("Language") for s in audio_streams),
        "subtitle_languages": languages.pack(s.get("Language") for s in subtitle_streams),
        "default_audio_language": languages.normalize(
            (audio or {}).get("Language")
        ) if audio_streams else None,
    }

    if video:
        tech.update({
            "video_codec": video.get("Codec"),
            "width": video.get("Width"),
            "height": video.get("Height"),
            "video_range": _dynamicky_rozsah(video),
        })
        # Kdyz kontejner bitrate neuvedl, vezmi aspon ten z video stopy.
        if not tech.get("bitrate"):
            tech["bitrate"] = video.get("BitRate")

    if audio:
        tech.update({
            "audio_codec": audio.get("Codec"),
            "audio_channels": audio.get("Channels"),
        })

    return tech


def video_range_of(item: dict[str, Any]) -> str | None:
    """Dynamicky rozsah z odpovedi Jellyfinu - i kdyz zbytek udaju nebereme.

    Uklada se vedle zmereneho rozsahu, protoze Dolby Vision v Matrosce
    umi cist az ffmpeg 5. Starsi ffprobe o nem nerekne nic (nema ani
    postranni data, ani znacku kodeku) a soubor se tvari jako obycejne
    HDR - kdezto Jellyfin ho zna. Viz stats.ROZSAH_CASE.
    """
    sources = item.get("MediaSources") or []
    streams = (sources[0].get("MediaStreams") if sources else None) or []
    video = next((s for s in streams if s.get("Type") == "Video"), None)
    return _dynamicky_rozsah(video) if video else None


def extract_streams(item: dict[str, Any]) -> list[dict[str, Any]]:
    """Vytahne jednotlive stopy souboru do podoby, kterou ulozime.

    Zatimco `extract_tech_from_item` dela souhrn ("jake jazyky tam jsou"),
    tady jde o kazdou stopu zvlast - kvuli detailu polozky, kde chce clovek
    videt, ze druha zvukova stopa je ceska 5.1 v AC3.
    """
    sources = item.get("MediaSources") or []
    streams = (sources[0].get("MediaStreams") if sources else None) or []

    result = []
    for index, stream in enumerate(streams):
        stream_type = stream.get("Type")
        if stream_type not in ("Video", "Audio", "Subtitle"):
            continue
        result.append({
            "stream_index": stream.get("Index", index),
            "type": stream_type,
            "codec": stream.get("Codec"),
            "language": languages.normalize(stream.get("Language")),
            "title": stream.get("Title") or stream.get("DisplayTitle"),
            "channels": stream.get("Channels"),
            "channel_layout": stream.get("ChannelLayout"),
            "width": stream.get("Width"),
            "height": stream.get("Height"),
            "bitrate": stream.get("BitRate"),
            "is_default": 1 if stream.get("IsDefault") else 0,
            "is_forced": 1 if stream.get("IsForced") else 0,
            "is_external": 1 if stream.get("IsExternal") else 0,
        })
    return result


def media_streams(item: dict[str, Any]) -> list[dict[str, Any]]:
    """Seznam stop polozky, at uz je Jellyfin posle kdekoli.

    Tady je zakopany pes: `/Items` vraci stopy zabalene v `MediaSources`,
    ale `/Sessions` je posila **rovnou v polozce** jako `MediaStreams`.
    Kod, ktery hleda jen v `MediaSources`, tak u prave hrajici relace
    nenajde nic - a na Prehledu chybi jazyk, kodek i bitrate. U prepoctu
    to videt nebylo, protoze tam se udaje berou z `TranscodingInfo`.
    """
    sources = item.get("MediaSources") or []
    if sources:
        streams = sources[0].get("MediaStreams") or []
        if streams:
            return streams
    return item.get("MediaStreams") or []


def source_bitrate(item: dict[str, Any]) -> int | None:
    """Bitrate zdroje - taky muze prijit ze dvou mist."""
    sources = item.get("MediaSources") or []
    if sources and sources[0].get("Bitrate"):
        return sources[0]["Bitrate"]
    video = next((s for s in media_streams(item) if s.get("Type") == "Video"), {})
    return video.get("BitRate") or item.get("Bitrate")


def video_dimensions(item: dict[str, Any]) -> tuple[int | None, int | None]:
    """Rozmery obrazu, ktery se prave prehrava. Vraci (sirka, vyska)."""
    video = next((s for s in media_streams(item) if s.get("Type") == "Video"), {})
    sirka = video.get("Width") or item.get("Width")
    vyska = video.get("Height") or item.get("Height")
    return (int(sirka) if sirka else None, int(vyska) if vyska else None)


def selected_languages(session: dict[str, Any], item: dict[str, Any]) -> dict[str, str | None]:
    """Zjisti, kterou zvukovou stopu a titulky si divak skutecne pustil.

    Rozdil oproti `extract_tech_from_item` je zasadni: tam zjistujeme,
    co je v souboru **k dispozici**. Tady, co si clovek **vybral**.
    Teprve druhe cislo rika, v jakem jazyce se doopravdy diva.

    Jellyfin to hlasi jako cislo stopy (`AudioStreamIndex`), ktere se musi
    najit v seznamu stop pod klicem `Index`. Poradi v seznamu neni totez
    co Index - proto hledame podle hodnoty, ne podle pozice.
    """
    play_state = session.get("PlayState") or {}
    streams = media_streams(item)

    def language_of(index: Any, stream_type: str) -> str | None:
        if index is None or index < 0:
            return None
        stream = next(
            (s for s in streams if s.get("Index") == index and s.get("Type") == stream_type),
            None,
        )
        if stream is None:
            return None
        return languages.normalize(stream.get("Language"))

    audio_language = language_of(play_state.get("AudioStreamIndex"), "Audio")

    # Kdyz prehravac vybranou stopu nehlasi, vezmeme vychozi stopu souboru -
    # to je to, co by se pustilo samo.
    if audio_language is None:
        audio_streams = [s for s in streams if s.get("Type") == "Audio"]
        default = next((s for s in audio_streams if s.get("IsDefault")), None)
        if default is None and audio_streams:
            default = audio_streams[0]
        if default is not None:
            audio_language = languages.normalize(default.get("Language"))

    return {
        "audio_language": audio_language,
        "subtitle_language": language_of(play_state.get("SubtitleStreamIndex"), "Subtitle"),
    }
