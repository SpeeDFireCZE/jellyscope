"""Sberac prehravani - srdce historie.

Jellyfin ti rekne jen to, co se deje **prave ted**. Zadnou historii si
nepamatuje. Kdyz chces vedet, kdo se na co dival minuly tyden, musis si tu
historii postavit sam.

Princip je jednoduchy a stoji za to ho pochopit, protoze stejne funguje
Jellystat i Tautulli:

    kazdych N sekund se zeptej "co se hraje?"
    -> nova relace, kterou jsem drive nevidel  = zacatek prehravani
    -> relace, kterou uz znam                  = pricti uplynuly cas
    -> relace, ktera zmizela                   = konec prehravani

Z posloupnosti okamzitych snimku tak vznikne souvisly zaznam.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from . import db
from .config import load_config
from .jellyfin import QUICK_TIMEOUT, JellyfinClient, JellyfinError
from .jellyfin import media_streams as jellyfin_streams
from .jellyfin import selected_languages as jellyfin_languages
from .jellyfin import source_bitrate as jellyfin_bitrate
from .jellyfin import video_dimensions as jellyfin_dimensions

log = logging.getLogger("jellyscope.collector")

# Klice, pod kterymi si do tabulky settings ukladame provozni stav.
# Podtrzitko na zacatku je nase znameni "tohle neni uzivatelske nastaveni".
STATUS_KEY = "_collector_status"
ERROR_KEY = "_collector_error"
LAST_POLL_KEY = "_collector_last_poll"

# Kolik prvnich minut prehravani se pri urcovani jazyka ignoruje.
#
# Zacatek filmu jsou loga distributoru, znelky a upoutavky - a divak
# behem nich stopu teprve hleda. Cokoliv, co hraje v tehle dobe, o jeho
# volbe nevypovida.
#
# Odsledovany cas se tim neztraci: k jazyku se pripocita cele prehravani
# vcetne techto minut. Ignoruje se jen pri ROZHODOVANI, ktery jazyk to byl.
LANGUAGE_GRACE_SECONDS = 240

# Jak dlouho musi stopa hrat, nez se zapocita do jazykovych statistik.
#
# Duvod je z praxe: soubor se casto spusti se spatnou stopou a divak ji
# hned prepne. Bez teto lhuty by se do statistik dostal jazyk, ktery nikdo
# neposlouchal - a "60 % anglicky" by rikalo neco jineho, nez se doopravdy
# stalo. Zapocita se az kombinace, u ktere divak zustal.
#
# Pocita se cela kombinace (zvuk + titulky) najednou: kdo si opravuje
# jazyk, obvykle rovnou srovna i titulky, takze davaji smysl spolu.
MIN_LANGUAGE_SECONDS = 60


def _parse(timestamp: str) -> datetime:
    """Opak db.utcnow() - z textu zpatky na datum a cas."""
    return datetime.strptime(timestamp, db.TIME_FORMAT).replace(tzinfo=timezone.utc)


def _session_key(session: dict[str, Any], item: dict[str, Any]) -> str:
    """Jednoznacne oznaceni "tahle relace hraje tuhle polozku".

    Proc ne jen id relace? Protoze kdyz ti dobehne epizoda a spusti se dalsi,
    Jellyfin pouzije **stejnou** relaci. Bez id polozky v klici by nam obe
    epizody splynuly do jednoho zaznamu.
    """
    return f"{session.get('Id')}::{item.get('Id')}"


# Jak daleko zpatky se hleda uz bezici zaznam tehoz prehravani. Musi to
# byt vic nez interval dotazovani, ale ne tolik, aby se za pokracovani
# povazovalo prehravani od vcerejska.
PREVZETI_MINUT = 15


def _prevzit_cizi_relaci(conn: Any, key: str, session: dict[str, Any],
                         item: dict[str, Any], now: str) -> Any:
    """Nebeži uz tohle prehravani pod jinym klicem? Pak ho prevezmi.

    Proc to tu je: proti jedne databazi muze omylem bezet **vic sberacu
    naraz** - typicky kdyz zustane spustena stara verze aplikace vedle
    nove. Kazdy si zalozi vlastni radek pro tentyz film a v historii
    vzniknou duplicity, ktere uz nikdo nerozplete.

    Klic relace na to nestaci: kazda verze si ho muze skladat jinak
    a prave proto se ty dva radky nepotkaly. Hledame proto podle toho,
    co je na obou stranach stejne - **kdo** co hraje a **co** hraje.

    Kdyz se takovy zaznam najde, prepiseme mu klic na nas a pokracujeme
    v nem. Duplicita tim nevznikne vubec, misto aby se pak uklizela.
    """
    uzivatel = session.get("UserId")
    polozka = item.get("Id")
    if not uzivatel or not polozka:
        return None

    # Hranici pocitame v Pythonu, ne v SQL. `datetime(?, ?)` je funkce
    # SQLite; PostgreSQL ji nezna a prekladac dialektu resi jen tvar
    # `datetime('now', ?)`. Hotovy cas jako parametr funguje v obou.
    od = (_parse(now) - timedelta(minutes=PREVZETI_MINUT)).strftime(db.TIME_FORMAT)

    radek = conn.execute(
        """SELECT id, last_seen_at, watched_seconds,
                  audio_language, subtitle_language,
                  current_audio_language, current_subtitle_language,
                  language_since, language_confirmed
             FROM playback
            WHERE is_active = 1
              AND user_id = ? AND item_id = ?
              AND last_seen_at >= ?
         ORDER BY last_seen_at DESC
            LIMIT 1""",
        (str(uzivatel), str(polozka), od),
    ).fetchone()

    if radek is None:
        return None

    conn.execute("UPDATE playback SET session_key = ? WHERE id = ?",
                 (key, radek["id"]))
    log.info("prevzat bezici zaznam %s (jiny klic relace) - duplicita nevznikla",
             radek["id"])
    return radek


# Jak dlouho po prerusení se prehravani jeste povazuje za totez.
#
# Pulhodina je kompromis: pauza na kafe nebo na uspani televize se vejde,
# ale film puštěný vecer a znovu pred spanim uz jsou dve ruzne podivane.
NAVAZANI_MINUT = 30

# Od jake pozice ma smysl rozlisovat "pokracuje" a "zacal znovu".
# Pod peti minutami je to jedno - kdo si film pusti od zacatku po dvou
# minutach, ten ho spis jen restartoval.
ZNOVU_OD_ZACATKU_TIKY = 5 * 60 * 10_000_000


def _navaz_na_prerusene(conn: Any, key: str, session: dict[str, Any],
                        item: dict[str, Any], pozice: Any, now: str) -> Any:
    """Nepokracuje tenhle divak v tom, co pred chvili prerusil?

    Proc to tu je: nektere prehravace pri pauze z `/Sessions` zmizi
    uplne. Sberac takovy zaznam uzavre - a kdyz se prehravani rozjede
    dal, zalozi novy. Jedno sledovani je pak v historii dvakrat a ve
    statistikach jako dve spusteni.

    Za pokracovani se to povazuje, kdyz sedi vsechno tohle:

      * tentyz divak a tentyz titul,
      * od posledniho snimku uplynulo min nez NAVAZANI_MINUT,
      * a **nezacalo se od zacatku** - kdyz je prehravac na nule, kdezto
        predtim byl v pulce filmu, je to nove sledovani, ne pokracovani.

    Odsledovany cas se tim nenafoukne: mezera mezi snimky je oriznuta na
    nekolikanasobek intervalu dotazovani (viz max_gap_seconds), takze se
    doba pauzy nikam nezapocita.
    """
    uzivatel = session.get("UserId")
    polozka = item.get("Id")
    if not uzivatel or not polozka:
        return None

    od = (_parse(now) - timedelta(minutes=NAVAZANI_MINUT)).strftime(db.TIME_FORMAT)
    radek = conn.execute(
        """SELECT id, last_seen_at, watched_seconds, position_ticks,
                  audio_language, subtitle_language,
                  current_audio_language, current_subtitle_language,
                  language_since, language_confirmed
             FROM playback
            WHERE is_active = 0
              AND user_id = ? AND item_id = ?
              AND last_seen_at >= ?
         ORDER BY last_seen_at DESC
            LIMIT 1""",
        (str(uzivatel), str(polozka), od),
    ).fetchone()

    if radek is None:
        return None

    try:
        nova_pozice = int(pozice or 0)
        stara_pozice = int(radek["position_ticks"] or 0)
    except (TypeError, ValueError):
        nova_pozice = stara_pozice = 0

    # Zacal znovu od zacatku? Pak je to druhe sledovani teho z filmu.
    if stara_pozice > ZNOVU_OD_ZACATKU_TIKY and nova_pozice < stara_pozice / 2:
        return None

    conn.execute(
        """UPDATE playback
              SET is_active = 1, ended_at = NULL, session_key = ?
            WHERE id = ?""",
        (key, radek["id"]),
    )
    log.info("navazano na prerusene prehravani %s - pauza, ne nove spusteni",
             radek["id"])
    return radek


def _describe_stream(session: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    """Co konkretne tece k prehravaci - kodeky a bitrate.

    Kdyz server transcoduje, zajima nas vysledny (prepocitany) tok.
    Kdyz hraje primo, je to kodek puvodniho souboru.
    """
    # Rozmery bereme ze zdroje i pri prepoctu: `TranscodingInfo` je hlasi
    # jen nekdy a rozliseni zdroje je pro cloveka uzitecnejsi informace
    # ("mam 4K film") nez to, na co ho server zrovna zmensuje.
    sirka, vyska = jellyfin_dimensions(item)
    # Delku bereme z relace, ne z knihovny: u epizody, kterou jsme jeste
    # nesynchronizovali, by se ukazatel postupu nemel z ceho spocitat.
    delka = item.get("RunTimeTicks")

    transcoding = session.get("TranscodingInfo") or {}
    if transcoding:
        reasons = transcoding.get("TranscodeReasons") or []
        if isinstance(reasons, str):
            reasons = [reasons]
        return {
            "video_codec": transcoding.get("VideoCodec"),
            "audio_codec": transcoding.get("AudioCodec"),
            "bitrate": transcoding.get("Bitrate"),
            "transcode_reasons": ", ".join(reasons) or None,
            "video_width": sirka,
            "video_height": vyska,
            "runtime_ticks": delka,
        }

    streams = jellyfin_streams(item)
    video = next((s for s in streams if s.get("Type") == "Video"), {})
    audio = next((s for s in streams if s.get("Type") == "Audio"), {})
    return {
        "video_codec": video.get("Codec"),
        "audio_codec": audio.get("Codec"),
        "bitrate": jellyfin_bitrate(item),
        "transcode_reasons": None,
        "video_width": sirka,
        "video_height": vyska,
        "runtime_ticks": delka,
    }


def close_orphans() -> int:
    """Uzavre zaznamy, ktere zustaly viset po predchozim behu aplikace.

    Kdyz Jellyscope spadne nebo ho vypnes uprostred filmu, zustane v databazi
    zaznam s is_active = 1. Pri dalsim startu ho uzavreme casem, kdy jsme ho
    naposled videli - to je nejpoctivejsi odhad, jaky mame.
    """
    with db.connect() as conn:
        cursor = conn.execute(
            """
            UPDATE playback
               SET is_active = 0,
                   ended_at  = COALESCE(ended_at, last_seen_at)
             WHERE is_active = 1
            """
        )
        return cursor.rowcount


async def poll_once(client: JellyfinClient, max_gap_seconds: int) -> dict[str, int]:
    """Jeden snimek: nacti aktivni relace a promitni je do databaze.

    Cekani na Jellyfin je asynchronni, ale zapis do databaze uz ne -
    sqlite3 zadne `await` nema. Kdyby se zapisovalo primo tady, zmrazil by
    na tu dobu celou aplikaci: nikdo by si nenacetl stranku, dokud sberac
    nedopise. Proto jde zapis do vlakna (`asyncio.to_thread`).
    """
    sessions = await client.sessions()
    return await asyncio.to_thread(_store_sessions, sessions, max_gap_seconds)


def _store_sessions(sessions: list[dict[str, Any]], max_gap_seconds: int) -> dict[str, int]:
    """Zapis snimku do databaze. Bezi ve vlakne, ne na smycce udalosti."""
    now = db.utcnow()
    now_dt = _parse(now)

    active_keys: list[str] = []
    started = 0

    with db.connect() as conn:
        for session in sessions:
            item = session.get("NowPlayingItem")
            if not item:
                continue  # relace je pripojena, ale nic nehraje

            key = _session_key(session, item)
            active_keys.append(key)

            play_state = session.get("PlayState") or {}
            is_paused = bool(play_state.get("IsPaused"))
            stream = _describe_stream(session, item)
            chosen = jellyfin_languages(session, item)

            existing = conn.execute(
                "SELECT id, last_seen_at, watched_seconds,"
                "       audio_language, subtitle_language,"
                "       current_audio_language, current_subtitle_language,"
                "       language_since, language_confirmed"
                "  FROM playback WHERE session_key = ? AND is_active = 1",
                (key,),
            ).fetchone()

            if existing is None:
                existing = _prevzit_cizi_relaci(conn, key, session, item, now)

            # Az kdyz nic nebezi: nenavazujeme na neco, co divak pred
            # chvili pauznul? Musi to byt az tady - bezici zaznam ma
            # prednost pred uzavrenym.
            if existing is None:
                existing = _navaz_na_prerusene(
                    conn, key, session, item, play_state.get("PositionTicks"), now)

            if existing is None:
                library_row = conn.execute(
                    "SELECT library_id FROM items WHERE id = ?", (item.get("Id"),)
                ).fetchone()

                conn.execute(
                    """
                    INSERT INTO playback (
                        session_key, user_id, user_name,
                        item_id, item_name, item_type, series_name, library_id,
                        client, device_name, device_id, remote_address,
                        play_method, transcode_reasons, video_codec, audio_codec, bitrate,
                        audio_language, subtitle_language,
                        current_audio_language, current_subtitle_language,
                        language_since, language_confirmed,
                        media_runtime_ticks, video_width, video_height,
                        started_at, last_seen_at, watched_seconds, paused_seconds,
                        position_ticks, is_paused, is_active
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,0,?,?,1)
                    """,
                    (
                        key,
                        session.get("UserId"),
                        session.get("UserName"),
                        item.get("Id"),
                        item.get("Name"),
                        item.get("Type"),
                        item.get("SeriesName"),
                        library_row["library_id"] if library_row else None,
                        session.get("Client"),
                        session.get("DeviceName"),
                        session.get("DeviceId"),
                        session.get("RemoteEndPoint"),
                        play_state.get("PlayMethod"),
                        stream["transcode_reasons"],
                        stream["video_codec"],
                        stream["audio_codec"],
                        stream["bitrate"],
                        # Do statistik zatim nic - jazyk se zapocita az po
                        # MIN_LANGUAGE_SECONDS, viz vyse. Prvni vteriny
                        # casto hraje stopa, kterou si divak hned prepne.
                        None,
                        None,
                        chosen["audio_language"],
                        chosen["subtitle_language"],
                        now,
                        0,
                        stream["runtime_ticks"],
                        stream["video_width"],
                        stream["video_height"],
                        now,
                        now,
                        play_state.get("PositionTicks"),
                        1 if is_paused else 0,
                    ),
                )
                started += 1
                continue

            # Relaci uz znam - pricti cas, ktery uplynul od minuleho snimku.
            gap = (now_dt - _parse(existing["last_seen_at"])).total_seconds()
            # Orez: kdyz aplikace na hodinu vypadla, nechceme si tu hodinu
            # zapsat jako "sledoval". Zapocteme jen rozumny interval.
            gap = max(0, min(int(gap), max_gap_seconds))

            # Kdy se jazyk zapocita do statistik.
            #
            # Dokud se stopy meni, hodiny se resetuji - do statistik se
            # dostane az kombinace, u ktere divak vydrzel aspon
            # MIN_LANGUAGE_SECONDS. Jakmile se jednou zapocita, uz se
            # nemeni: prehravani patri jazyku, se kterym se doopravdy
            # sledovalo, i kdyz si ho nekdo na konci prepne.
            #
            # Pocitame to tady a ne v SQL zamerne - je to rozhodovani,
            # ne ukladani, a v Pythonu je videt na prvni pohled.
            zmena_stop = (
                chosen["audio_language"] != existing["current_audio_language"]
                or chosen["subtitle_language"] != existing["current_subtitle_language"]
            )
            potvrzeno = bool(existing["language_confirmed"])
            jazyk_od = existing["language_since"] or now

            # Prvni minuty prehravani se preskakuji - viz
            # LANGUAGE_GRACE_SECONDS. Pocitame odsledovany cas, ne cas od
            # spusteni: kdyz si divak film pusti a odejde na hodinu k
            # obedu, pauza se do tech ctyr minut pocitat nema.
            odsledovano = int(existing["watched_seconds"] or 0) + (0 if is_paused else gap)
            po_uvodu = odsledovano >= LANGUAGE_GRACE_SECONDS

            if not potvrzeno:
                if zmena_stop or not po_uvodu:
                    # Behem uvodu hodiny porad resetujeme, takze se zacne
                    # merit az od prvniho snimku po nem.
                    jazyk_od = now
                elif ((now_dt - _parse(jazyk_od)).total_seconds() >= MIN_LANGUAGE_SECONDS
                      and odsledovano >= LANGUAGE_GRACE_SECONDS + MIN_LANGUAGE_SECONDS):
                    # Dve podminky, ne jedna. Sama o sobe by prvni potvrdila
                    # jazyk driv: hodiny se resetuji pri kazdem snimku uvodu,
                    # takze posledni reset padne nekam TESNE PRED ctvrtou
                    # minutu - a minuta mereni pak skonci pred patou.
                    #
                    # Druha podminka rika prosty pozadavek: nez se jazyk
                    # zapocita, musi prehravani trvat aspon uvod plus tu
                    # minutu. Kolik snimku se mezitim stihlo, uz nehraje roli.
                    potvrzeno = True

            # Co se zapise do statistik. Prave potvrzena kombinace, jinak
            # to, co uz tam je.
            #
            # Drive to resil `CASE WHEN ? THEN ? ELSE ... END` primo v SQL.
            # Fungovalo to na SQLite, ale PostgreSQL potrebuje ve WHEN
            # pravdivostni hodnotu, ne cislo ("argument of CASE/WHEN must
            # be type boolean, not type smallint"). Rozhodnuti stejne patri
            # sem - SQL ma ukladat, ne rozhodovat.
            prave_potvrzeno = potvrzeno and not existing["language_confirmed"]
            zapsany_zvuk = (chosen["audio_language"] if prave_potvrzeno
                            else existing["audio_language"])
            zapsane_titulky = (chosen["subtitle_language"] if prave_potvrzeno
                               else existing["subtitle_language"])

            conn.execute(
                """
                UPDATE playback
                   SET last_seen_at      = ?,
                       watched_seconds   = watched_seconds + ?,
                       paused_seconds    = paused_seconds + ?,
                       position_ticks    = ?,
                       play_method       = COALESCE(?, play_method),
                       transcode_reasons = COALESCE(?, transcode_reasons),
                       bitrate           = COALESCE(?, bitrate),
                       -- Dve dvojice sloupcu, protoze jsou to dve ruzne
                       -- otazky a jedna hodnota by na obe odpovedet nemohla:
                       --
                       --   audio_language          "v cem to sledoval"
                       --   current_audio_language  "co hraje prave ted"
                       --
                       -- Prvni dvojice se zapise jednou - ve chvili, kdy
                       -- jazyk "vydrzel" dost dlouho (viz vypocet vyse).
                       -- Druha se prepisuje pri kazdem snimku, protoze
                       -- karta "Prave se hraje" ma ukazovat skutecnost.
                       --
                       -- Obe dvojice se zapisuji primo, bez COALESCE:
                       -- co se ma zapsat, uz je rozhodnute vyse. U druhe
                       -- dvojice je prepis navic zamer - vypnute titulky
                       -- se maji projevit tim, ze znacka zmizi, ne tim,
                       -- ze zustane viset posledni znamy jazyk.
                       audio_language    = ?,
                       subtitle_language = ?,
                       language_since    = ?,
                       language_confirmed = ?,
                       current_audio_language    = ?,
                       current_subtitle_language = ?,
                       -- Rozmery se doplni, jakmile je Jellyfin posle.
                       -- U prvniho snimku je nekdy jeste nema.
                       -- Delka se doplni, jakmile ji Jellyfin posle;
                       -- u prvniho snimku ji nekdy jeste nema.
                       media_runtime_ticks = COALESCE(media_runtime_ticks, ?),
                       video_width       = COALESCE(video_width, ?),
                       video_height      = COALESCE(video_height, ?),
                       -- Tenhle se naopak prepisuje pokazde: zajima nas
                       -- stav ted, ne ten pri spusteni.
                       is_paused         = ?
                 WHERE id = ?
                """,
                (
                    now,
                    0 if is_paused else gap,
                    gap if is_paused else 0,
                    play_state.get("PositionTicks"),
                    play_state.get("PlayMethod"),
                    stream["transcode_reasons"],
                    stream["bitrate"],
                    zapsany_zvuk,
                    zapsane_titulky,
                    jazyk_od,
                    1 if potvrzeno else 0,
                    chosen["audio_language"],
                    chosen["subtitle_language"],
                    stream["runtime_ticks"],
                    stream["video_width"],
                    stream["video_height"],
                    1 if is_paused else 0,
                    existing["id"],
                ),
            )

        # Cokoliv, co je v databazi aktivni, ale Jellyfin uz to nehlasi, skoncilo.
        if active_keys:
            placeholders = ",".join("?" for _ in active_keys)
            cursor = conn.execute(
                f"""
                UPDATE playback
                   SET is_active = 0, ended_at = ?
                 WHERE is_active = 1 AND session_key NOT IN ({placeholders})
                """,
                (now, *active_keys),
            )
        else:
            cursor = conn.execute(
                "UPDATE playback SET is_active = 0, ended_at = ? WHERE is_active = 1",
                (now,),
            )
        ended = cursor.rowcount

    return {"active": len(active_keys), "started": started, "ended": ended}


async def run_forever() -> None:
    """Nekonecna smycka, ktera bezi na pozadi po celou dobu behu aplikace.

    Nesmi nikdy spadnout. Kdyz Jellyfin vypadne, zapiseme chybu, pockame
    a zkusime to znovu - aplikace jako celek bezi dal.
    """
    config = load_config()
    close_orphans()

    backoff = 0  # kolik sekund navic cekat po chybe

    while True:
        interval = db.get_int_setting("poll_interval", minimum=2, maximum=300, fallback=10)

        try:
            async with JellyfinClient(*db.jellyfin_connection(),
                                      timeout=QUICK_TIMEOUT) as client:
                # Uvnitr jednoho spojeni odbehneme vic snimku - navazovat
                # HTTP spojeni pokazde znovu je zbytecna prace.
                for _ in range(60):
                    result = await poll_once(client, max_gap_seconds=interval * 3)
                    db.set_setting(STATUS_KEY, "ok")
                    db.set_setting(ERROR_KEY, "")
                    db.set_setting(LAST_POLL_KEY, db.utcnow())
                    backoff = 0

                    if result["started"] or result["ended"]:
                        log.info(
                            "prehravani: %d aktivnich, %d zacalo, %d skoncilo",
                            result["active"], result["started"], result["ended"],
                        )

                    interval = db.get_int_setting("poll_interval", 2, 300, 10)
                    await asyncio.sleep(interval)

        except asyncio.CancelledError:
            # Aplikace se vypina - uklidime po sobe a skoncime.
            close_orphans()
            raise
        except JellyfinError as exc:
            db.set_setting(STATUS_KEY, "error")
            db.set_setting(ERROR_KEY, str(exc))
            log.warning("sberac: %s", exc)
        except Exception as exc:  # noqa: BLE001 - smycka nesmi umrit na nicem
            db.set_setting(STATUS_KEY, "error")
            db.set_setting(ERROR_KEY, f"Neočekávaná chyba: {exc}")
            log.exception("sberac: neocekavana chyba")

        # Po chybe cekame postupne dele (2, 4, 8 ... max 60 s). Tomuhle se
        # rika exponencialni backoff a je to slusne chovani vuci serveru,
        # ktery ma zrovna problem.
        backoff = min(60, (backoff * 2) or 2)
        await asyncio.sleep(backoff)
