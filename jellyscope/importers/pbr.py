"""Import z Playback Reporting (plugin Jellyfinu) - přes API i ze souboru TSV.

Část balíku importers (viz jellyscope/importers/__init__.py) - kód přesunutý
beze změny z původního importers.py."""
from __future__ import annotations

import logging
from typing import Any

from .. import db, scanner
from ..jellyfin import JellyfinClient, JellyfinError
from .propojeni import link_imported_history
from .shoda import _index_prehravani, _known_items, _known_users, _uz_tam_je, _zapamatuj
from .zaklad import (
    ImportError_,
    _build_row,
    _existing_keys,
    _insert,
    _jazyk_ze_zdroje,
    _parse_time,
    jen_cteni,
)

log = logging.getLogger("jellyscope.importers")


# Dotaz do tabulky pluginu Playback Reporting.
PBR_QUERY = (
    "SELECT rowid, DateCreated, UserId, ItemId, ItemType, ItemName, "
    "PlaybackMethod, ClientName, DeviceName, PlayDuration "
    "FROM PlaybackActivity ORDER BY DateCreated"
)

# Zaloha bez `rowid`. Nektere verze pluginu ten sloupec ve svem dotazovaci
# nepovoli a cely dotaz odmitnou chybou 500, takze by import nesel spustit
# vubec. Bez rowid prijdeme jen o cast klice proti opakovanemu importu -
# a tu dnes zastoupi porovnani podle obsahu (viz _uz_tam_je).
PBR_QUERY_BEZ_ROWID = (
    "SELECT DateCreated, UserId, ItemId, ItemType, ItemName, "
    "PlaybackMethod, ClientName, DeviceName, PlayDuration "
    "FROM PlaybackActivity ORDER BY DateCreated"
)


# ---------------------------------------------------------------------------
# 1. Playback Reporting (plugin Jellyfinu)
# ---------------------------------------------------------------------------

async def playback_reporting_available() -> tuple[bool, str]:
    """Zjisti, jestli je plugin nainstalovany a odpovida."""
    try:
        async with JellyfinClient(*db.jellyfin_connection()) as client:
            response = await client._client.post(
                "/user_usage_stats/submit_custom_query",
                json={"CustomQueryString":
                          jen_cteni("SELECT COUNT(*) FROM PlaybackActivity"),
                      "ReplaceUserId": False},
            )
    except Exception as exc:  # noqa: BLE001
        return False, f"Nepodařilo se zeptat: {exc}"

    if response.status_code == 404:
        return False, "Plugin Playback Reporting v Jellyfinu není nainstalovaný."
    if response.status_code >= 400:
        # I tady vypisujeme telo odpovedi - "chyba 500" bez neho nikomu
        # neporadi, kde hledat.
        return False, (f"Plugin odpověděl chybou {response.status_code}: "
                       f"{(response.text or '').strip()[:300]}")

    try:
        results = (response.json() or {}).get("results") or []
        count = int(results[0][0]) if results and results[0] else 0
    except (ValueError, IndexError, TypeError):
        return True, "Plugin odpovídá, ale počet záznamů se nepodařilo přečíst."

    return True, f"Plugin nalezen, obsahuje {count} záznamů."


async def import_playback_reporting(min_seconds: int = 60) -> dict[str, Any]:
    """Prevezme historii z pluginu Playback Reporting."""
    scan_id = scanner.start_task_log("import")

    try:
        async with JellyfinClient(*db.jellyfin_connection()) as client:
            # Zkusime oba tvary dotazu. Prvni je bohatsi (obsahuje rowid,
            # ze ktereho skladame klic proti opakovanemu importu), druhy
            # projde i tam, kde plugin rowid odmita.
            posledni_chyba = ""
            payload = None

            for dotaz in (PBR_QUERY, PBR_QUERY_BEZ_ROWID):
                response = await client._client.post(
                    "/user_usage_stats/submit_custom_query",
                    json={"CustomQueryString": jen_cteni(dotaz), "ReplaceUserId": False},
                )
                if response.status_code == 404:
                    raise ImportError_(
                        "Plugin Playback Reporting není nainstalovaný. "
                        "Najdeš ho v Jellyfinu: Ovládací panel -> Pluginy -> Katalog."
                    )
                if response.status_code < 400:
                    payload = response.json() or {}
                    if dotaz is PBR_QUERY_BEZ_ROWID:
                        log.info("plugin odmitl rowid, pouzit zjednoduseny dotaz")
                    break

                # Telo odpovedi obsahuje, co se pluginu nelibilo. Bez nej
                # je "chyba 500" hlaska, se kterou se neda nic delat.
                posledni_chyba = (
                    f"{response.status_code}: {(response.text or '').strip()[:300]}"
                )
                log.warning("plugin odmitl dotaz (%s)", posledni_chyba)

            if payload is None:
                raise ImportError_(
                    f"Plugin Playback Reporting odpověděl chybou {posledni_chyba}\n\n"
                    "Nejčastější příčina je NESOULAD VERZÍ: plugin přeložený "
                    "proti staršímu Jellyfinu spadne dřív, než se ke svým "
                    "datům vůbec dostane, a vrátí jen \"Error processing "
                    "request\". Z Jellyscope to opravit nejde - endpoint "
                    "pluginu je pak mrtvý pro jakýkoliv dotaz.\n\n"
                    "Jistotu dá log Jellyfinu; hledej v něm "
                    "submit_custom_query. Stojí-li tam MissingMethodException, "
                    "je to přesně tenhle případ.\n\n"
                    "Co s tím:\n"
                    "  * nahraj data pluginu ze souboru - viz \"Nefunguje "
                    "import přes API?\" hned pod tímhle tlačítkem,\n"
                    "  * nebo přenes historii ze zálohy Jellystatu,\n"
                    "  * nebo počkej na verzi pluginu pro tvůj Jellyfin - pak "
                    "začne import přes API fungovat sám."
                )

    except (JellyfinError, ImportError_) as exc:
        scanner.finish_task_log(scan_id, "error", message=str(exc))
        return {"status": "error", "message": str(exc)}
    except Exception as exc:  # noqa: BLE001
        log.exception("import z Playback Reporting selhal")
        scanner.finish_task_log(scan_id, "error", message=str(exc))
        return {"status": "error", "message": f"Neočekávaná chyba: {exc}"}

    columns = [str(c).lower() for c in (payload.get("colums") or payload.get("columns") or [])]
    results = payload.get("results") or []

    return await _zpracuj_pbr(scan_id, columns, results, min_seconds,
                              "Playback Reporting")


# Poradi sloupcu v zaloze pluginu Playback Reporting. Odpovida tabulce
# PlaybackActivity - plugin ji do souboru vypisuje tak, jak je.
PBR_TSV_SLOUPCE = ["datecreated", "userid", "itemid", "itemtype", "itemname",
                   "playbackmethod", "clientname", "devicename", "playduration"]


def _tsv_sloupce(prvni_radek: list[str]) -> tuple[list[str], bool]:
    """Rozhodne, jestli je prvni radek hlavicka, nebo uz data.

    Plugin zalohu podle verze zapisuje obojím zpusobem. Pozname to podle
    obsahu: hlavicka nese nazvy sloupcu, datovy radek zacina datem.
    Hadat podle poctu sloupcu by nestacilo - ten je stejny.
    """
    male = [str(bunka).strip().lower() for bunka in prvni_radek]
    if "datecreated" in male or "userid" in male:
        return male, True

    # Bez hlavicky se drzime poradi z tabulky. Kdyz ma radek sloupcu vic
    # (nektere verze pridavaji na zacatek rowid), prebytek na zacatku
    # pojmenujeme, at se dvojice nerozjedou.
    navic = len(prvni_radek) - len(PBR_TSV_SLOUPCE)
    if navic > 0:
        return ["rowid"] * navic + PBR_TSV_SLOUPCE, False
    return PBR_TSV_SLOUPCE, False


async def import_playback_reporting_tsv(raw: bytes,
                                        min_seconds: int = 60) -> dict[str, Any]:
    """Prevezme historii ze zalohy pluginu Playback Reporting (soubor TSV).

    Zaloha pro pripad, kdy plugin pres API nefunguje. Stava se to
    u nesouladu verzi: plugin prelozeny proti starsimu Jellyfinu spadne
    na `MissingMethodException` jeste driv, nez se dostane ke svym datum,
    a vrati jen "Error processing request".

    Soubor si vyrobis primo v pluginu: **Jellyfin -> Ovladaci panel ->
    Playback Reporting -> Backup -> Save backup**. Vysledkem je obycejny
    textovy soubor, kde jsou hodnoty oddelene tabulatorem.

    Proc TSV a ne `playback_reporting.db`: zalohu si umi vyrobit sam
    plugin jednim kliknutim, kdezto k databazovemu souboru se clovek musi
    dostat pres SSH a jeste vedet, kde lezi. Do Jellyfinu se pritom
    nesaha ani v jednom pripade - cteme kopii, kterou nahrajes ty.

    Radky se zpracuji uplne stejne jako z API, vcetne klice proti
    duplicitam - kdyz se pak API opravi a spustis import znovu, nic se
    nezdvoji.
    """
    scan_id = scanner.start_task_log("import")

    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        # Zalohy z Windows byvaji v cp1250; a kdyz nesedi ani to, radeji
        # nahradime nectene znaky, nez abychom import odmitli kvuli
        # jedinemu pismenu v nazvu filmu.
        text = raw.decode("cp1250", errors="replace")

    radky = [radek for radek in text.splitlines() if radek.strip()]
    if not radky:
        scanner.finish_task_log(scan_id, "error", message="prazdny soubor")
        return {"status": "error", "message": "Soubor je prázdný."}

    rozdelene = [radek.split("\t") for radek in radky]

    # Soubor bez tabulatoru je skoro jiste neco jineho, nez si clovek mysli.
    # Rict to rovnou je lepsi nez naimportovat nula zaznamu bez vysvetleni.
    if max(len(r) for r in rozdelene) < 2:
        scanner.finish_task_log(scan_id, "error", message="neni to TSV")
        return {
            "status": "error",
            "message": ("V souboru nejsou tabulátory, takže to není záloha "
                        "Playback Reportingu. Vyrob ji v Jellyfinu: Ovládací "
                        "panel → Playback Reporting → Backup → Save backup."),
        }

    sloupce, ma_hlavicku = _tsv_sloupce(rozdelene[0])
    hodnoty = rozdelene[1:] if ma_hlavicku else rozdelene

    return await _zpracuj_pbr(scan_id, sloupce, hodnoty, min_seconds,
                              "Playback Reporting (záloha TSV)")


async def _zpracuj_pbr(scan_id: int, columns: list[str], results: list[Any],
                 min_seconds: int, zdroj: str) -> dict[str, Any]:
    """Spolecne zpracovani radku z Playback Reportingu.

    Jedna funkce pro obe cesty (API i soubor). Kdyby to byly dve kopie,
    oprava v jedne by se do druhe casem nedostala - a duplicity nebo
    spatne klice by si nikdo nevsiml, dokud by nekdo nepouzil zrovna
    tu druhou cestu.
    """
    users = _known_users()
    items = _known_items()
    existing = _existing_keys("import:pbr:")
    index_casu = _index_prehravani()

    rows: list[tuple[Any, ...]] = []
    skipped_short = 0
    skipped_duplicate = 0
    skipped_known = 0

    for record in results:
        data = dict(zip(columns, record)) if columns else {}
        started_at = _parse_time(data.get("datecreated"))
        if not started_at:
            continue

        try:
            duration = int(float(data.get("playduration") or 0))
        except (TypeError, ValueError):
            duration = 0
        if duration < min_seconds:
            skipped_short += 1
            continue

        # Klic zaznamu. `rowid` je nejlepsi, protoze ho plugin nemeni -
        # kdyz ho ale odmitl (viz PBR_QUERY_BEZ_ROWID), poskladame klic
        # z toho, co zaznam jednoznacne popisuje. Bez teto zalozky by
        # vsechny radky tehoz titulu dostaly stejny klic a naimportoval
        # by se z nich jediny.
        oznaceni = data.get("rowid")
        if oznaceni is None:
            oznaceni = f"{data.get('datecreated')}:{data.get('userid')}"
        key = f"import:pbr:{oznaceni}:{data.get('itemid')}"
        if key in existing:
            skipped_duplicate += 1
            continue
        existing.add(key)

        # Tentyz zaznam uz muze byt v databazi z Jellystatu nebo ze sberace.
        if _uz_tam_je(index_casu, data.get("userid"), data.get("itemid"),
                      started_at, duration):
            skipped_known += 1
            continue
        _zapamatuj(index_casu, data.get("userid"), data.get("itemid"),
                   started_at, duration)

        rows.append(_build_row(
            key=key,
            user_id=data.get("userid"),
            item_id=data.get("itemid"),
            started_at=started_at,
            duration=duration,
            item_type=data.get("itemtype"),
            item_name=data.get("itemname"),
            client=data.get("clientname"),
            device=data.get("devicename"),
            method=data.get("playbackmethod"),
            audio_language=_jazyk_ze_zdroje(data),
            users=users,
            items=items,
        ))

    imported = _insert(rows)

    # Import neposila tmdb ID - dohledame ho sami, at se prevzata historie
    # spoji se skutecnymi tituly v knihovne.
    linked = await link_imported_history()

    scanner.finish_task_log(
        scan_id, "done", total=len(results), ok=imported, failed=0,
        message=(f"{zdroj}: {imported} nových, "
                 f"{skipped_duplicate} již existovalo, "
                 f"{skipped_known} známých z jiného zdroje, "
                 f"{skipped_short} příliš krátkých"),
    )
    return {
        "status": "ok",
        "source": zdroj,
        "found": len(results),
        "imported": imported,
        "duplicate": skipped_duplicate,
        "known_elsewhere": skipped_known,
        "too_short": skipped_short,
        "linked": linked,
    }
