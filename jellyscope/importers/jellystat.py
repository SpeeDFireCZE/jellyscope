"""Import ze zálohy Jellystatu (JSON).

Část balíku importers (viz jellyscope/importers/__init__.py) - kód přesunutý
beze změny z původního importers.py."""
from __future__ import annotations

import json
import logging
from typing import Any

from .. import db, scanner
from .propojeni import link_imported_history
from .shoda import _index_prehravani, _known_items, _known_users, _uz_tam_je, _zapamatuj
from .zaklad import _build_row, _existing_keys, _insert, _parse_time

log = logging.getLogger("jellyscope.importers")


def _jellystat_item_type(record: dict[str, Any]) -> str | None:
    """Film, nebo epizoda? Jellystat to v zaloze neuvadi primo.

    Tabulka jf_playback_activity zadny sloupec s typem nema - zna ale
    nazev serialu a id epizody. Kdyz je vyplneny kterykoliv z nich, jde
    o epizodu; jinak film.

    Drive se sem omylem cetl `NowPlayingItemName`, tedy **nazev titulu**.
    Do sloupce item_type se pak ukladalo treba "Duna" a vsechno, co
    rozlisuje filmy od serialu, prestalo fungovat - graf sledovanosti
    po dnech zustal prazdny, i kdyz hodiny v souhrnu sedely.
    """
    explicitni = _pick(record, "ItemType", "item_type", "NowPlayingItemType", "Type")
    if explicitni in ("Movie", "Episode", "Series", "Audio"):
        return explicitni

    if _pick(record, "SeriesName", "seriesname", "series_name") \
            or _pick(record, "EpisodeId", "episodeid", "SeasonId", "seasonid"):
        return "Episode"
    return "Movie"


# ---------------------------------------------------------------------------
# 2. Jellystat (JSON zaloha)
# ---------------------------------------------------------------------------

# Jellystat uklada prehravani do tabulky jf_playback_activity. V zaloze
# muze byt bud primo seznam, nebo slovnik s nazvy tabulek.
JELLYSTAT_TABLES = ("jf_playback_activity", "playback_activity", "jf_playback")


def _find_jellystat_rows(payload: Any) -> list[dict[str, Any]]:
    """Najde v JSON zaloze seznam prehravani.

    Jellystat menil format zalohy mezi verzemi, takze se nespolehame na
    jeden tvar a hledame vic moznosti. Kdyz nic nesedi, rekneme to nahlas
    misto abychom naimportovali prazdno a tvarili se, ze je hotovo.
    """
    if isinstance(payload, list):
        # Bud rovnou seznam zaznamu, nebo seznam tabulek.
        if payload and isinstance(payload[0], dict):
            if any(key in payload[0] for key in ("UserId", "userid", "NowPlayingItemId")):
                return payload
            for entry in payload:
                for name in JELLYSTAT_TABLES:
                    if name in entry and isinstance(entry[name], list):
                        return entry[name]
        return []

    if isinstance(payload, dict):
        for name in JELLYSTAT_TABLES:
            value = payload.get(name)
            if isinstance(value, list):
                return value
        # Nekdy je vse zabalene o uroven niz.
        for value in payload.values():
            if isinstance(value, (list, dict)):
                found = _find_jellystat_rows(value)
                if found:
                    return found

    return []


def _pick(record: dict[str, Any], *names: str) -> Any:
    """Vrati prvni vyplnene pole z nekolika moznych nazvu."""
    lowered = {str(k).lower(): v for k, v in record.items()}
    for name in names:
        value = lowered.get(name.lower())
        if value not in (None, ""):
            return value
    return None


def _podle_zdroje(existing: set[str]) -> dict[str, str]:
    """Zdrojový řádek zálohy → klíč, pod kterým už je v databázi.

    Zdrojový řádek pozná předpona klíče `import:jst:<id řádku v záloze>`;
    co následuje za ní, je položka, na kterou záznam ukazuje.
    """
    # Ke kazdemu zdrojovemu radku (prvni dve casti klice) si zapamatujeme,
    # pod jakym klicem uz v databazi je. Podle toho se pozna oprava.
    podle_zdroje: dict[str, str] = {}
    for klic in existing:
        casti = klic.split(":")
        if len(casti) >= 4:
            podle_zdroje[":".join(casti[:3])] = klic
    return podle_zdroje


def _precti_zaznam(record: Any, index: int) -> dict[str, Any] | None:
    """Co je v jednom záznamu zálohy: čas, délka, díl nebo seriál, divák, názvy a klíč.

    `None` = záznam se použít nedá (není to slovník, nebo nemá čas).
    """
    if not isinstance(record, dict):
        return None

    started_at = _parse_time(
        _pick(record, "ActivityDateInserted", "activity_date_inserted",
              "DateCreated", "date_created", "LastActivityDate")
    )
    if not started_at:
        return None

    try:
        duration = int(float(_pick(record, "PlaybackDuration", "playback_duration",
                                   "PlayDuration", "duration") or 0))
    except (TypeError, ValueError):
        duration = 0

    # U epizody posila Jellystat DVE ruzna id a zalezi, ktere se vezme:
    #
    #   NowPlayingItemId  = id SERIALU   ("Tym SEAL")
    #   EpisodeId         = id DILU      (to, co chceme)
    #
    # Drive se bralo to prvni, takze cela historie serialu skoncila
    # na jednom id, ke kteremu v knihovne nic nevede - a protoze se
    # zaznam jmenoval jmenem serialu, nedal se dil dopocitat vubec.
    # Zadne dohledavani to uz nespravi; jedina cesta je vzit spravne
    # id rovnou pri importu.
    dil_id = _pick(record, "EpisodeId", "episodeid", "episode_id")
    item_id = dil_id or _pick(record, "NowPlayingItemId", "nowplayingitemid",
                              "ItemId")
    user_id = _pick(record, "UserId", "userid", "user_id")

    # Stejny pribeh u nazvu: NowPlayingItemName je u epizody jmeno
    # serialu. Jmeno dilu je v EpisodeName - a jmeno serialu si
    # necháme zvlast, at se prehravani zaradi pod svuj serial.
    jmeno_serialu = _pick(record, "SeriesName", "seriesname",
                          "NowPlayingItemName", "ItemName", "item_name")
    jmeno_dilu = _pick(record, "EpisodeName", "episodename", "episode_name")
    nazev = (jmeno_dilu if dil_id else None) or _pick(
        record, "NowPlayingItemName", "ItemName", "item_name")

    # Klic ma tvar "import:jst:<id radku v zaloze>:<id polozky>".
    # Prvni dve casti identifikuji ZDROJOVY RADEK, treti to, na co
    # ukazuje - a prave ta se opravou meni.
    zaklad = f"import:jst:{_pick(record, 'Id', 'id') or index}"
    key = f"{zaklad}:{item_id}"
    return {"started_at": started_at, "duration": duration, "dil_id": dil_id,
            "item_id": item_id, "user_id": user_id, "jmeno_serialu": jmeno_serialu,
            "nazev": nazev, "zaklad": zaklad, "key": key}


def _oprav_presmerovany(zaznam: dict[str, Any], record: dict[str, Any],
                       podle_zdroje: dict[str, str], existing: set[str]) -> bool:
    """Oprava starého importu: tentýž řádek zálohy už v databázi je, jen ukazuje jinam.

    Opraví ho na místě a `podle_zdroje` i `existing` změní tak, jako by se
    záznam importoval rovnou správně. `False` = nebylo co opravovat.
    """
    key, zaklad, dil_id = zaznam["key"], zaznam["zaklad"], zaznam["dil_id"]
    item_id, nazev, jmeno_serialu = zaznam["item_id"], zaznam["nazev"], zaznam["jmeno_serialu"]
    # Tentyz radek zalohy uz v databazi je, jen ukazuje jinam - to je
    # presne stav po importu, ktery bral id serialu misto dilu.
    # Opravime ho na miste; kdybychom vlozili novy, mel by clovek
    # kazde prehravani dvakrat.
    stary_klic = podle_zdroje.get(zaklad)
    if stary_klic:
        with db.connect() as conn:
            conn.execute(
                """UPDATE playback
                      SET session_key = ?, item_id = ?, item_name = ?,
                          series_name = COALESCE(?, series_name),
                          item_type = ?
                    WHERE session_key = ?""",
                (key, item_id, nazev or "?",
                 jmeno_serialu if dil_id else None,
                 _jellystat_item_type(record), stary_klic),
            )
        podle_zdroje[zaklad] = key
        existing.discard(stary_klic)
        existing.add(key)
        return True
    return False


def _radek_ze_zaznamu(zaznam: dict[str, Any], record: dict[str, Any],
                      users: dict[str, str], items: dict[str, dict[str, Any]]) -> tuple[Any, ...]:
    """Řádek historie pro nový záznam - v pořadí sloupců, jak ho čeká `_insert`."""
    return _build_row(
        key=zaznam["key"],
        user_id=zaznam["user_id"],
        item_id=zaznam["item_id"],
        started_at=zaznam["started_at"],
        duration=zaznam["duration"],
        item_type=_jellystat_item_type(record),
        item_name=zaznam["nazev"],
        series_name=zaznam["jmeno_serialu"] if zaznam["dil_id"] else None,
        client=_pick(record, "Client", "client"),
        device=_pick(record, "DeviceName", "device_name"),
        method=_pick(record, "PlayMethod", "play_method", "PlaybackMethod"),
        users=users,
        items=items,
        user_name=_pick(record, "UserName", "username", "user_name"),
    )


async def import_jellystat_json(raw: bytes, min_seconds: int = 60) -> dict[str, Any]:
    """Prevezme historii z JSON zalohy Jellystatu."""
    scan_id = scanner.start_task_log("import")

    try:
        payload = json.loads(raw.decode("utf-8", errors="replace"))
    except ValueError as exc:
        scanner.finish_task_log(scan_id, "error", message="neplatný JSON")
        return {"status": "error", "message": f"Soubor není platný JSON: {exc}"}

    records = _find_jellystat_rows(payload)
    if not records:
        scanner.finish_task_log(scan_id, "error", message="tabulka přehrávání nenalezena")
        return {
            "status": "error",
            "message": ("V souboru jsem nenašel tabulku přehrávání "
                        "(jf_playback_activity). Je to opravdu záloha Jellystatu?"),
        }

    users = _known_users()
    items = _known_items()
    existing = _existing_keys("import:jst:")
    podle_zdroje = _podle_zdroje(existing)
    opraveno = 0
    index_casu = _index_prehravani()

    rows: list[tuple[Any, ...]] = []
    skipped_short = 0
    skipped_duplicate = 0
    skipped_known = 0

    for index, record in enumerate(records):
        zaznam = _precti_zaznam(record, index)
        if zaznam is None:
            continue
        if zaznam["duration"] < min_seconds:
            skipped_short += 1
            continue

        key = zaznam["key"]
        if key in existing:
            skipped_duplicate += 1
            continue

        if _oprav_presmerovany(zaznam, record, podle_zdroje, existing):
            opraveno += 1
            continue

        existing.add(key)
        podle_zdroje[zaznam["zaklad"]] = key

        # Tentyz zaznam uz muze byt v databazi z Playback Reportingu nebo
        # ze sberace.
        if _uz_tam_je(index_casu, zaznam["user_id"], zaznam["item_id"],
                      zaznam["started_at"], zaznam["duration"]):
            skipped_known += 1
            continue
        _zapamatuj(index_casu, zaznam["user_id"], zaznam["item_id"],
                   zaznam["started_at"], zaznam["duration"])

        rows.append(_radek_ze_zaznamu(zaznam, record, users, items))

    imported = _insert(rows)

    # Zaloha Jellystatu tmdb ID neobsahuje - dohledame ho sami.
    linked = await link_imported_history()

    scanner.finish_task_log(
        scan_id, "done", total=len(records), ok=imported, failed=0,
        message=(f"Jellystat: {imported} nových, {opraveno} opravených, "
                 f"{skipped_duplicate} již existovalo, "
                 f"{skipped_known} známých z jiného zdroje, "
                 f"{skipped_short} příliš krátkých"),
    )
    if opraveno:
        log.info("import: %s zaznamu preneseno ze serialu na konkretni dil",
                 opraveno)
    return {
        "status": "ok",
        "source": "Jellystat",
        "found": len(records),
        "imported": imported,
        "repaired": opraveno,
        "duplicate": skipped_duplicate,
        "known_elsewhere": skipped_known,
        "too_short": skipped_short,
        "linked": linked,
    }
