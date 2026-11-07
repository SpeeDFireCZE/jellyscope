"""Synchronizace celé knihovny: uživatelé, knihovny a položky z Jellyfinu.

Část balíku scanner (viz jellyscope/scanner/__init__.py) - kód přesunutý
beze změny z původního scanner.py."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from .. import db
from ..jellyfin import (
    JellyfinClient,
    JellyfinError,
    extract_streams,
    extract_tech_from_item,
    extract_verze,
    video_range_of,
)
from .beh import (
    _add_progress,
    _clear_progress,
    _clear_stop,
    _scan_lock,
    _start_progress,
    finish_task_log,
    start_task_log,
    stop_requested,
)
from .misto import zapamatuj_misto_z_jellyfinu, zapis_snimek
from .slouceni import _stare_ke_slouceni
from .typy import je_obal
from .zapis import _mark_missing, _write_batch, uklid_fantomu, zapis_verze

log = logging.getLogger("jellyscope.scanner")


# ---------------------------------------------------------------------------
# 1. Synchronizace knihovny
# ---------------------------------------------------------------------------

async def sync_library() -> dict[str, Any]:
    """Stahne z Jellyfinu uzivatele, knihovny a vsechny polozky."""
    if _scan_lock.locked():
        return {"status": "busy", "message": "Jiná úloha už běží."}

    async with _scan_lock:
        _clear_stop()
        scan_id = start_task_log("library")
        started_at = db.utcnow()
        counts = {"users": 0, "libraries": 0, "items": 0}
        zastaveno = False

        try:
            async with JellyfinClient(*db.jellyfin_connection()) as client:
                counts["users"] = await _sync_users(client)
                libraries = await _sync_libraries(client)
                counts["libraries"] = len(libraries)

                use_jellyfin_tech = db.get_setting("tech_source") == "jellyfin"

                # Nejdriv se zeptame, kolik toho bude - jedno rychle volani
                # na knihovnu. Bez toho by ukazatel prubehu nemel k cemu
                # pocitat procenta.
                #
                # Kdyz se to nepovede, synchronizace bezi dal. Je to udaj
                # navic pro ukazatel prubehu, ne podminka prace - shodit
                # kvuli nemu cely scan by bylo neumerne.
                celkem = 0
                try:
                    for library in libraries:
                        celkem += await client.item_count(parent_id=library["id"])
                except Exception as exc:      # noqa: BLE001
                    log.warning("pocet polozek se nepodarilo zjistit: %s", exc)
                    celkem = 0
                _start_progress("library", celkem)

                for library in libraries:
                    counts["items"] += await _sync_items_of_library(
                        client, library, use_jellyfin_tech
                    )
                    if stop_requested():
                        zastaveno = True
                        break

                # Polozky, ktere jsme v tomhle behu nevideli, uz v Jellyfinu
                # nejsou. Nemazeme je - historie prehravani na ne odkazuje -
                # jen si je oznacime.
                #
                # Po zastaveni se tenhle krok MUSI vynechat. Zbytek knihovny
                # jsme totiz jeste nestihli projit, takze bychom "nevidene"
                # oznacili i tituly, ktere v Jellyfinu normalne jsou -
                # zmizely by z knihovny a skoncily v archivu. Nedokoncena
                # synchronizace radeji nezmeni nic, nez aby lhala.
                if not zastaveno:
                    _mark_missing(started_at)
                    # Polozky druhu, ktery sem nepatri (kolekce z Jellyfinu
                    # 12 pred opravou), at zmizi hned - ne az po restartu,
                    # kdy bezi uklid_fantomu() ze startu.
                    await asyncio.to_thread(uklid_fantomu)

                # Kolik zbyva mista - podle SAMOTNEHO Jellyfinu. Ptame se
                # tady, dokud je klient otevreny: snimek se pise az potom
                # a je synchronni. Kdyz to nevyjde (starsi Jellyfin ten
                # endpoint nema), jde se dal - je to udaj navic, ne
                # podminka prace.
                try:
                    zapamatuj_misto_z_jellyfinu(await client.storage())
                except Exception as exc:      # noqa: BLE001
                    log.info("misto z Jellyfinu se nepodarilo zjistit: %s", exc)

            # Ted uz zname uzivatele i polozky - srovname s nimi historii,
            # ktera se naimportovala driv, nez bylo pripojeni k Jellyfinu.
            # Takovy import ma v historii "?" misto jmen, nezna typ polozky
            # a jeho ItemId nemusi odpovidat zadnemu titulu v knihovne.
            #
            # Volame cele link_imported_history(), ne jen doplneni udaju:
            # ono se postara i o dohledani podle tmdb ID, ktere v dobe
            # importu nebylo jak udelat (Jellyfin jeste nebyl pripojeny).
            #
            # Import az tady, ne nahore: importers uz importuje scanner
            # a kruhovy import by aplikaci pri startu polozil.
            from .. import importers
            try:
                await importers.link_imported_history()
            except Exception as exc:      # noqa: BLE001
                # Knihovna uz je stazena a ulozena. Kdyz se srovnani
                # historie nepovede, je to skoda - ne duvod hlasit celou
                # synchronizaci jako neuspesnou. Zkusi se pri te dalsi.
                log.warning("srovnani prevzate historie se nepovedlo: %s", exc)

        except JellyfinError as exc:
            _clear_progress()
            finish_task_log(scan_id, "error", message=str(exc))
            return {"status": "error", "message": str(exc)}
        except Exception as exc:  # noqa: BLE001
            log.exception("synchronizace knihovny selhala")
            _clear_progress()
            finish_task_log(scan_id, "error", message=str(exc))
            return {"status": "error", "message": f"Neočekávaná chyba: {exc}"}

        _clear_progress()
        popis = f"{counts['libraries']} knihoven, {counts['users']} uživatelů"
        if zastaveno:
            finish_task_log(
                scan_id, "stopped", total=counts["items"], ok=counts["items"],
                message=(f"Zastaveno na tvůj pokyn. Stihlo se {counts['items']} položek "
                         f"({popis}). Co se stáhlo, je uložené; zbytek zůstal beze změny."),
            )
            return {"status": "stopped", **counts}

        # Snimek knihovny se zapisuje az sem: po uspesne synchronizaci,
        # kdy je stav uplny. Po zastavene nebo spadle by zachytil pulku
        # knihovny a v grafu rustu by z toho byl propad, ktery se nestal.
        await asyncio.to_thread(zapis_snimek)

        finish_task_log(
            scan_id, "done", total=counts["items"], ok=counts["items"], message=popis,
        )
        return {"status": "ok", **counts}


async def _sync_users(client: JellyfinClient) -> int:
    users = await client.users()
    now = db.utcnow()
    with db.connect() as conn:
        for user in users:
            policy = user.get("Policy") or {}
            conn.execute(
                """
                INSERT INTO users (id, name, is_administrator, is_disabled, last_activity,
                                   synced_at, image_tag)
                VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    is_administrator = excluded.is_administrator,
                    is_disabled = excluded.is_disabled,
                    last_activity = excluded.last_activity,
                    synced_at = excluded.synced_at,
                    image_tag = excluded.image_tag
                """,
                (
                    user.get("Id"),
                    user.get("Name") or "?",
                    1 if policy.get("IsAdministrator") else 0,
                    1 if policy.get("IsDisabled") else 0,
                    user.get("LastActivityDate"),
                    now,
                    # Otisk profiloveho obrazku; bez obrazku ho Jellyfin
                    # vubec neposle. Prazdny retezec = taky zadny.
                    user.get("PrimaryImageTag") or None,
                ),
            )

    # Radky v `users` se nemazou - historie smazaneho cloveka ma dal
    # komu patrit. Co se maze, je jeho PRIHLASOVACI ucet do Jellyscope:
    # koho spravce v Jellyfinu odebral, nema tady mit dvere.
    from .. import accounts  # az tady, at nevznikne kruh pri importu

    smazane = accounts.odeber_bez_jellyfinu(
        {str(user.get("Id") or "") for user in users})
    if smazane:
        log.info("ucty bez uzivatele v Jellyfinu odebrany: %s",
                 ", ".join(smazane))
    return len(users)


async def _sync_libraries(client: JellyfinClient) -> list[dict[str, Any]]:
    folders = await client.virtual_folders()
    now = db.utcnow()
    result = []

    with db.connect() as conn:
        for folder in folders:
            collection_type = folder.get("CollectionType")
            # Hudbu a fotky zatim vynechavame - statistiky jsou stavene
            # na video obsah.
            if collection_type not in (None, "movies", "tvshows", "homevideos", "mixed"):
                continue

            library = {
                "id": folder.get("ItemId"),
                "name": folder.get("Name") or "?",
                "collection_type": collection_type,
            }
            if not library["id"]:
                continue

            conn.execute(
                """
                INSERT INTO libraries (id, name, collection_type, paths, synced_at)
                VALUES (?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    collection_type = excluded.collection_type,
                    paths = excluded.paths,
                    synced_at = excluded.synced_at
                """,
                (
                    library["id"],
                    library["name"],
                    collection_type,
                    json.dumps(folder.get("Locations") or []),
                    now,
                ),
            )
            result.append(library)

    return result


def tmdb_id_of(item: dict[str, Any]) -> str | None:
    """Vytahne z polozky identifikator TMDB.

    Jellyfin je posila ve slovniku ProviderIds, jenze klice pise ruzne
    podle verze a typu polozky: "Tmdb", "TmdbId", u serialu obcas "tmdb".
    Porovnavame proto malymi pismeny, at na tom nezalezi.

    U epizod bereme id **serialu** (`SeriesProviderIds`), kdyz vlastni
    nema - epizoda sama v TMDB casto zadne id nema a bez toho by se
    slucovani u serialu nechytlo.

    POZOR: u epizody tohle id NEIDENTIFIKUJE dil, ale cely serial - vsechny
    dily Kancelare maji stejne. Kdo podle nej chce poznat jednu polozku,
    musi pouzit `identita_polozky()`.
    """
    for source in (item.get("ProviderIds"), item.get("SeriesProviderIds")):
        if not isinstance(source, dict):
            continue
        for key, value in source.items():
            if str(key).lower() in ("tmdb", "tmdbid") and value:
                return str(value)
    return None


def identita_polozky(item: dict[str, Any]) -> tuple[str, int, int] | None:
    """Podle ceho poznat, ze dva ruzne soubory jsou tentyz poradek.

    Slouzi ke slucovani: prekodujes soubor, Jellyfin zalozi novou polozku
    s novym ItemId a stara zmizi. Historii prehravani chceme prenest.

    U filmu na to staci tmdb_id. **U epizody ne** - a byla to skarada
    chyba: `tmdb_id_of()` u epizody vraci id SERIALU, takze vsech dvacet
    dilu Kancelare melo stejne. Slucovani pak povazovalo kazde dva dily
    za tentyz soubor a pri kazdem skenu slilo historii vsech dilu na
    jediny. Ve statistikach to vypadalo, ze divak videl jednu epizodu
    dvacetkrat - a "sledovanych titulu" bylo dvacetkrat min, nez melo.

    Dil proto identifikujeme trojici: serial + rada + cislo dilu.
    Kdyz cislo dilu neznáme, vracime None a neslucujeme radsi vubec -
    spatne slouceni se uz nijak nevrati zpatky.
    """
    tmdb = tmdb_id_of(item)
    if not tmdb:
        return None

    if str(item.get("Type") or "") != "Episode":
        # Film: cislo rady ani dilu nema, do porovnani dosadime -1.
        return (tmdb, -1, -1)

    rada = item.get("ParentIndexNumber")
    dil = item.get("IndexNumber")
    if rada is None or dil is None:
        return None
    try:
        return (tmdb, int(rada), int(dil))
    except (TypeError, ValueError):
        return None


def _radek_polozky(item: dict[str, Any], library_id: Any,
                   tech: dict[str, Any], now: str) -> tuple[Any, ...]:
    """Poskladá jeden řádek tabulky `items` z odpovědi Jellyfinu.

    Vlastní funkce, protože ji potřebují dvě různé úlohy - plná
    synchronizace i ta rychlá nad nově přidanými. Kdyby to byly dvě kopie,
    přidání sloupce by se jednou zapomnělo a jedna z cest by tiše
    ukládala míň.
    """
    return (
        item.get("Id"),
        item.get("Name") or "?",
        item.get("Type") or "?",
        library_id,
        item.get("SeriesId"),
        item.get("SeriesName"),
        item.get("SeasonName"),
        item.get("IndexNumber"),
        item.get("ParentIndexNumber"),
        item.get("ProductionYear"),
        item.get("RunTimeTicks"),
        item.get("DateCreated"),
        item.get("Path"),
        tmdb_id_of(item),
        # Zanry spojime svislitkem - carka se v nazvech zanru objevuje.
        "|".join(str(z).strip() for z in (item.get("Genres") or []) if str(z).strip())
            or None,
        tech.get("container"),
        tech.get("video_codec"),
        tech.get("audio_codec"),
        tech.get("audio_channels"),
        tech.get("width"),
        tech.get("height"),
        tech.get("bitrate"),
        tech.get("size_bytes"),
        tech.get("video_range"),
        # Co o rozsahu rika Jellyfin. Uklada se i v rezimu ffprobe, kde
        # se zbytek technickych udaju z Jellyfinu nebere: Dolby Vision
        # v Matrosce umi cist az ffmpeg 5, takze starsi ffprobe hlasi jen
        # "HDR" - a Jellyfin je pak jediny, kdo o DV vi.
        video_range_of(item),
        tech.get("audio_languages"),
        tech.get("subtitle_languages"),
        tech.get("default_audio_language"),
        "jellyfin" if tech else None,
        now if tech else None,
        # Otisk plakatu serialu, ke kteremu dil patri. U filmu prazdny.
        item.get("SeriesPrimaryImageTag"),
        # Otisk plakatu. Kdyz ho Jellyfin nehlasi, zustane prazdny -
        # obrazek se pak chova jako driv, jen bez rozpoznani zmeny.
        (item.get("ImageTags") or {}).get("Primary"),
        now,
    )


def _ktere_uz_zname(ids: list[str]) -> set[str]:
    """Ktera z techto ID uz v tabulce items jsou.

    Ptame se po davkach: SQL ma strop na pocet parametru v jednom dotazu
    (u SQLite jich byva 999) a rychla synchronizace muze pri prvnim behu
    prijit s tisici polozkami najednou.
    """
    znama: set[str] = set()
    for start in range(0, len(ids), 400):
        davka = ids[start:start + 400]
        if not davka:
            continue
        otazniky = ",".join("?" for _ in davka)
        for radek in db.query_all(
                f"SELECT id FROM items WHERE id IN ({otazniky})", tuple(davka)):
            znama.add(str(radek["id"]))
    return znama


async def _uloz_nove_polozky(polozky: list[dict[str, Any]], library_id: Any,
                             use_jellyfin_tech: bool,
                             client: JellyfinClient | None = None) -> list[str]:
    """Uloží položky jedné knihovny. Používá rychlá synchronizace.

    `library_id` se predava zvenci, protoze Jellyfin ho v odpovedi nenese.
    Drive se bralo z uz ulozeneho zaznamu - jenze u NOVEHO titulu zadny
    neexistuje, takze zustalo prazdne. A pri dalsim behu se prazdna
    hodnota jen zkopirovala, takze se to nikdy nespravilo samo.

    **Zapisuji se vsechny, ale vraci se jen id tech opravdu novych.**
    Hranice pro stahovani se schvalne posouva o par minut zpatky (viz
    `_posledni_pridano()`), takze se nejnovejsi uz znamy titul stahne
    znovu - a driv se zapocital jako novy. Vysledek pak nikdy neukazal
    nulu, i kdyz od minule nic nepribylo. Prepsat uz znamy titul ma pritom
    smysl: prave tim se opravi zaznam, ktery Jellyfin pri prvnim pruchodu
    jeste nemel zaradeny do knihovny.
    """
    # Stejna pojistka jako v plne synchronizaci: co neni film ani dil
    # (kolekce z Jellyfinu 12), se nezapisuje.
    polozky = [i for i in polozky if not je_obal(i)]
    if not polozky:
        return []

    now = db.utcnow()
    znama = _ktere_uz_zname([str(i["Id"]) for i in polozky if i.get("Id")])
    nova_id = [str(i["Id"]) for i in polozky
               if i.get("Id") and str(i["Id"]) not in znama]

    radky: list[tuple[Any, ...]] = []
    stopy: list[tuple[str, list[dict[str, Any]]]] = []
    verze: list[tuple[str, list[dict[str, Any]]]] = []
    tmdb_dvojice: list[tuple[tuple[str, int, int], str]] = []

    for item in polozky:
        tech = extract_tech_from_item(item) if use_jellyfin_tech else {}
        if use_jellyfin_tech:
            streams = extract_streams(item)
            if streams:
                stopy.append((item.get("Id"), streams))
        verze.append((str(item.get("Id") or ""), extract_verze(item)))

        identita = identita_polozky(item)
        if identita and item.get("Id"):
            tmdb_dvojice.append((identita, str(item["Id"])))

        radky.append(_radek_polozky(item, library_id, tech, now))
        _add_progress(1)

    # Rychla synchronizace vidi jen novinky, takze podle `synced_at` nepozna,
    # co v knihovne porad je - vsechno ostatni ma razitko z minuleho behu.
    # Kdyz tedy slucovani na nejakou ulozenou polozku ukazuje, zeptame se
    # Jellyfinu primo, jestli tam jeste je. Kdyz ano, jde o druhou kopii
    # tehoz dilu a slucovat se nesmi.
    chranena: set[str] = set()
    if client is not None:
        kandidati = await asyncio.to_thread(
            _stare_ke_slouceni, tmdb_dvojice,
            {str(i["Id"]) for i in polozky if i.get("Id")},
        )
        if kandidati:
            zive = await client.items_by_ids(list(kandidati))
            chranena = {str(i["Id"]) for i in zive if i.get("Id")}

    await asyncio.to_thread(_write_batch, radky, stopy, tmdb_dvojice,
                            not use_jellyfin_tech, None, chranena)
    # Az po polozkach: verze se na ne odkazuji cizim klicem.
    await asyncio.to_thread(zapis_verze, verze)
    return nova_id


async def _sync_items_of_library(
    client: JellyfinClient,
    library: dict[str, Any],
    use_jellyfin_tech: bool,
) -> int:
    """Nacte a ulozi vsechny polozky jedne knihovny."""
    now = db.utcnow()
    count = 0
    batch: list[tuple[Any, ...]] = []
    stream_batch: list[tuple[str, list[dict[str, Any]]]] = []
    verze_batch: list[tuple[str, list[dict[str, Any]]]] = []
    # Dvojice (tmdb_id, nove ItemId) pro slucovani prekodovanych souboru.
    seen_tmdb: list[tuple[tuple[str, int, int], str]] = []

    preskoceno = 0
    async for item in client.iter_items(parent_id=library["id"]):
        # Pojistka na druh polozky. Server se pta jen na filmy a dily,
        # jenze Jellyfin 12 umi poslat kolekci misto filmu v ni (viz
        # CollapseBoxSetItems v jellyfin.py). Co neni film ani dil, se
        # nezapisuje - jinak by to `_mark_missing()` kazdou noc posilalo
        # do archivu a v knihovne by strasila "archivovana" kolekce.
        if je_obal(item):
            preskoceno += 1
            continue
        tech = extract_tech_from_item(item) if use_jellyfin_tech else {}
        if use_jellyfin_tech:
            streams = extract_streams(item)
            if streams:
                stream_batch.append((item.get("Id"), streams))
        # Verze se sbiraji vzdycky, i pri zdroji "ffprobe": jsou to
        # soubory, ne zmerene udaje - a to, ze titul lezi na disku
        # dvakrat, rekne jen Jellyfin.
        verze_batch.append((str(item.get("Id") or ""), extract_verze(item)))

        identita = identita_polozky(item)
        if identita and item.get("Id"):
            seen_tmdb.append((identita, str(item["Id"])))

        batch.append(_radek_polozky(item, library["id"], tech, now))
        count += 1

        # Zapisujeme po davkach. Jeden INSERT na polozku by u velke knihovny
        # znamenal desetitisice samostatnych transakci a scan by se vlekl.
        if len(batch) >= 200:
            # Zapis jde do vlakna. Bez toho by synchronizace velke knihovny
            # drzela smycku udalosti a cely web by po dobu scanu neodpovidal.
            await asyncio.to_thread(
                _write_batch, list(batch), list(stream_batch),
                list(seen_tmdb), not use_jellyfin_tech, now
            )
            await asyncio.to_thread(zapis_verze, list(verze_batch))
            _add_progress(len(batch))
            batch.clear()
            stream_batch.clear()
            verze_batch.clear()
            seen_tmdb.clear()

        # Zastavujeme az tady - rozdelana polozka je hotova a zapsana.
        # U velke knihovny je tohle jedine misto, kde se da skoncit vcas;
        # kontrola mezi knihovnami by u jedne velke znamenala cekat
        # klidne desitky minut.
        if stop_requested():
            break

    if batch or stream_batch or seen_tmdb:
        await asyncio.to_thread(
            _write_batch, list(batch), list(stream_batch),
            list(seen_tmdb), not use_jellyfin_tech, now
        )
        await asyncio.to_thread(zapis_verze, list(verze_batch))
        _add_progress(len(batch))

    if preskoceno:
        log.warning("knihovna %s: Jellyfin poslal %s polozek jineho druhu "
                    "nez film nebo dil (kolekce?) - preskoceny",
                    library.get("name"), preskoceno)
    return count
