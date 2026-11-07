"""Technická analýza souborů (ffprobe nebo Jellyfin) a dynamický rozsah.

Část balíku scanner (viz jellyscope/scanner/__init__.py) - kód přesunutý
beze změny z původního scanner.py."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from .. import db, languages, probe
from ..i18n import translate as _t
from ..jellyfin import JellyfinClient, JellyfinError, video_range_of
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
from .jazyky import doplnit_jazyky_z_jellyfinu, doplnit_jazyky_z_nazvu, kandidati_na_jazyk_z_nazvu
from .zapis import save_streams

log = logging.getLogger("jellyscope.scanner")


# ---------------------------------------------------------------------------
# 2. Technicka analyza pres ffprobe
# ---------------------------------------------------------------------------

def _mapovani_cest(settings: dict[str, Any]) -> list[Any]:
    """Mapování cest z nastavení (cesta v Jellyfinu → cesta tady); vadné = žádné."""
    try:
        mappings = json.loads(settings.get("path_mappings") or "[]")
        if not isinstance(mappings, list):
            mappings = []
    except ValueError:
        mappings = []
    return mappings


def _dotaz_k_analyze(only_missing: bool, limit: int, item_ids: list[str] | None,
                     library_id: str | None) -> tuple[str, tuple[Any, ...]] | None:
    """SQL a parametry výběru souborů k analýze. Nic nespouští.

    `None` = výčet položek je prázdný, takže není co vybírat.
    """
    where = "WHERE is_missing = 0 AND path IS NOT NULL AND path != ''"
    if only_missing:
        where += " AND (tech_source IS NULL OR tech_source != 'ffprobe')"

    parametry: list[Any] = []
    if library_id:
        where += " AND library_id = ?"
        parametry.append(library_id)

    if item_ids is not None:
        if not item_ids:
            return None
        # Otazniky, ne slepeny seznam - id prichazi z odpovedi Jellyfinu
        # a do SQL se hodnoty nikdy nevkladaji primo.
        otazniky = ",".join("?" for _ in item_ids)
        where += f" AND id IN ({otazniky})"
        parametry.extend(item_ids)

    sql = f"SELECT id, path FROM items {where} ORDER BY name"
    if limit > 0:
        sql += f" LIMIT {int(limit)}"
    return sql, tuple(parametry)


async def _analyzuj_soubory(
        targets: list[dict[str, Any]], ffprobe_bin: str, mappings: list[Any],
        concurrency: int) -> tuple[dict[str, int], set[str]]:
    """Změří soubory přes ffprobe, nejvýš `concurrency` najednou.

    Vrátí počty (ok / failed / skipped) a položky, u kterých soubor
    aspoň u jedné stopy jazyk neuvádí. Po zastavení rozpracované soubory
    doběhnou a čekající se přeskočí.
    """
    # Semafor omezi, kolik ffprobe procesu bezi soucasne. Bez nej by se
    # u velke knihovny spustily tisice procesu naraz a stroj by stal.
    _start_progress("tech", len(targets))
    semaphore = asyncio.Semaphore(concurrency)
    results = {"ok": 0, "failed": 0, "skipped": 0}
    results_lock = asyncio.Lock()
    # Položky, u kterých soubor aspoň u jedné stopy jazyk neuvádí.
    # Na ty se pak doptáme Jellyfinu - viz doplnit_jazyky_z_jellyfinu().
    bez_jazyka: set[str] = set()

    async def analyse(row: dict[str, Any]) -> None:
        async with semaphore:
            # Kontrola az tady, za semaforem: uloh je nachystano tolik,
            # kolik je souboru, ale soucasne jich bezi jen par. Ty
            # cekajici se po zastaveni jen tise preskoci, zatimco
            # rozpracovane doprobihaji. Zadny soubor tak neskonci
            # analyzovany napul.
            if stop_requested():
                async with results_lock:
                    results["skipped"] += 1
                return

            # Jeden soubor = jeden krok. Drive se `_add_progress(1)`
            # volalo na trech mistech uvnitr teto funkce (po prevzeti
            # semaforu, po analyze a po zapisu), takze hotovy soubor
            # posunul ukazatel o tri - a ten pak ukazoval treba 135 %.
            # `finally` navic zajisti, ze se krok zapocita i u souboru,
            # ktery skoncil chybou.
            try:
                local_path = probe.apply_path_mappings(row["path"], mappings)
                try:
                    tech = await probe.probe_file(local_path, ffprobe_bin)
                except probe.ProbeError as exc:
                    await asyncio.to_thread(_save_tech_error, row["id"], str(exc))
                    async with results_lock:
                        results["failed"] += 1
                    return

                # Take zapis do vlakna - u velke knihovny je tenhle blok
                # v jedne smycce tisickrat a kazdy zapis by jinak na chvili
                # zastavil obsluhu webu.
                await asyncio.to_thread(_save_probe_result, row["id"], tech)
                async with results_lock:
                    results["ok"] += 1
                    if _chybi_jazyk(tech.get("streams") or []):
                        bez_jazyka.add(row["id"])
            finally:
                _add_progress(1)

    await asyncio.gather(*(analyse(row) for row in targets))
    _clear_progress()
    return results, bez_jazyka


async def _doplnky_po_analyze(bez_jazyka: set[str], library_id: str | None,
                              item_ids: list[str] | None) -> tuple[int, int, int]:
    """Doplní, co soubory neřekly: jazyk z Jellyfinu, odhad z názvu a rozsah.

    Vrátí (doplněno stop, odhadnuto z názvu, rozsahů). Jellyfin, který
    neodpovídá, je jen chybějící doplněk - zapíše se do logu a změřená
    data platí dál.
    """
    # Až po analýze, ne během ní: doptat se dá na padesát položek
    # jedním dotazem, kdežto uprostřed smyčky by to byl jeden dotaz
    # na soubor. A když Jellyfin zrovna neodpovídá, je to jen chybějící
    # doplněk - změřená data už jsou uložená, takže se to nesmí počítat
    # jako neúspěch celé analýzy.
    # Zpětně i na to, co je změřené z dřívějška. Bez tohohle by doplnění
    # platilo jen pro nově analyzované soubory - tedy skoro pro nic,
    # protože knihovna je obvykle změřená celá už dávno. Ptáme se jen
    # na stopy bez značky o původu, takže na tytéž se podruhé
    # nedoptáváme (viz _doplnit_jazyky_polozky).
    podminka = ("language = ? AND language_source IS NULL "
                "AND type IN ('Audio', 'Subtitle')")
    parametry_zpetne: list[Any] = [languages.UNKNOWN]
    # Stejný rozsah jako u analýzy: obnova jedné položky nesmí spustit
    # doptávání na celou knihovnu.
    if library_id:
        podminka += (" AND item_id IN (SELECT id FROM items WHERE library_id = ?)")
        parametry_zpetne.append(library_id)
    if item_ids:
        otazniky = ",".join("?" for _ in item_ids)
        podminka += f" AND item_id IN ({otazniky})"
        parametry_zpetne.extend(item_ids)

    for radek in db.query_all(
            f"SELECT DISTINCT item_id FROM item_streams WHERE {podminka}",
            tuple(parametry_zpetne)):
        bez_jazyka.add(radek["item_id"])

    doplneno = 0
    if bez_jazyka and not stop_requested():
        try:
            doplneno = await doplnit_jazyky_z_jellyfinu(sorted(bez_jazyka))
        except JellyfinError as exc:
            log.warning("jazyky se z Jellyfinu doplnit nepodarilo: %s", exc)

    # Co nezná ani Jellyfin, zkusíme uhodnout z názvu souboru. Až
    # teď a jen na tom, co zbylo: název je odhad, kdežto soubor
    # a knihovna jsou údaj.
    zbyle = set(kandidati_na_jazyk_z_nazvu(library_id, item_ids))
    z_nazvu = 0
    if zbyle and not stop_requested():
        z_nazvu = await asyncio.to_thread(doplnit_jazyky_z_nazvu, sorted(zbyle))

    # Dynamicky rozsah podle Jellyfinu. Zapisuje ho sice uz
    # synchronizace knihovny, jenze technicka data si clovek spojuje
    # s TOUHLE ulohou - spustil "Analyzu souboru", videl, ze se nic
    # nezmenilo, a nemel duvod tusit, ze chybejici Dolby Vision
    # doplni az jina uloha.
    rozsahy = 0
    if not stop_requested():
        chybejici = kandidati_na_rozsah_z_jellyfinu(library_id, item_ids)
        if chybejici:
            try:
                rozsahy = await doplnit_rozsah_z_jellyfinu(chybejici)
            except JellyfinError as exc:
                log.warning("rozsah se z Jellyfinu doplnit nepodarilo: %s", exc)
    return doplneno, z_nazvu, rozsahy


def _text_doplnku(doplneno: int, z_nazvu: int, rozsahy: int) -> str:
    """Dovětek zprávy v logu úlohy: co se doplnilo jinak než z ffprobe."""
    doplnek = (_t(", jazyk doplněn z Jellyfinu u {n} stop").format(n=doplneno)
               if doplneno else "")
    if z_nazvu:
        doplnek += _t(", odhadnut z názvu u {n} titulů").format(n=z_nazvu)
    if rozsahy:
        doplnek += _t(", rozsah z Jellyfinu u {n} titulů").format(n=rozsahy)
    return doplnek


async def run_tech_scan(only_missing: bool = True, limit: int = 0,
                        item_ids: list[str] | None = None,
                        library_id: str | None = None) -> dict[str, Any]:
    """Projde polozky a doplni technicke udaje pomoci ffprobe.

    `only_missing=True` znamena "jen ty, ktere jeste nemaji ffprobe data".
    Diky tomu se dlouhy scan da poustet po castech a nezacina vzdy od nuly.

    `item_ids` omezi beh na vyjmenovane polozky. Pouziva to rychla
    synchronizace: nove pridany titul tak dostane technicka data hned,
    misto aby na ne cekal az do dalsiho behu denni ulohy.

    `library_id` omezi beh na jednu knihovnu - to pouziva tlacitko primo
    u hlasky "nekolik souboru nema technicka data" na detailu knihovny.
    """
    if _scan_lock.locked():
        return {"status": "busy", "message": "Jiná úloha už běží."}

    async with _scan_lock:
        _clear_stop()
        settings = db.get_settings()
        ffprobe_bin = probe.find_ffprobe(settings.get("ffprobe_path", ""))
        if not ffprobe_bin:
            return {
                "status": "error",
                "message": (
                    "ffprobe se nepodařilo najít. Nainstaluj ffmpeg, nebo v Nastavení "
                    "vyplň plnou cestu k ffprobe.exe."
                ),
            }

        mappings = _mapovani_cest(settings)

        concurrency = db.get_int_setting("ffprobe_concurrency", 1, 16, 3)

        dotaz = _dotaz_k_analyze(only_missing, limit, item_ids, library_id)
        if dotaz is None:
            return {"status": "ok", "total": 0, "ok": 0, "failed": 0}

        targets = db.query_all(*dotaz)
        scan_id = start_task_log("tech")

        if not targets:
            finish_task_log(scan_id, "done", message=_t("Není co analyzovat."))
            return {"status": "ok", "total": 0, "ok": 0, "failed": 0}

        results, bez_jazyka = await _analyzuj_soubory(targets, ffprobe_bin, mappings, concurrency)
        doplneno, z_nazvu, rozsahy = await _doplnky_po_analyze(bez_jazyka, library_id, item_ids)
        doplnek = _text_doplnku(doplneno, z_nazvu, rozsahy)

        if results["skipped"]:
            finish_task_log(
                scan_id, "stopped",
                total=len(targets), ok=results["ok"], failed=results["failed"],
                message=(f"Zastaveno na tvůj pokyn. Zbylo {results['skipped']} souborů - "
                         f"příští analýza na ně naváže. ffprobe: {ffprobe_bin}"
                         f"{doplnek}"),
            )
            return {"status": "stopped", "total": len(targets), **results}

        finish_task_log(
            scan_id, "done",
            total=len(targets), ok=results["ok"], failed=results["failed"],
            message=f"ffprobe: {ffprobe_bin}{doplnek}",
        )
        return {"status": "ok", "total": len(targets), "doplneno": doplneno,
                "z_nazvu": z_nazvu, **results}


def _chybi_jazyk(streams: list[dict[str, Any]]) -> bool:
    """Má aspoň jedna zvuková stopa nebo titulky "neuvedeno"?

    Video se neřeší: u obrazu jazyk nikdo nečeká a "neuvedeno" tam není
    chybějící údaj, ale správná odpověď.
    """
    return any(s.get("type") in ("Audio", "Subtitle")
               and s.get("language") == languages.UNKNOWN
               for s in streams)


def kandidati_na_rozsah_z_jellyfinu(library_id: str | None = None,
                                    item_ids: list[str] | None = None) -> list[str]:
    """Polozky, u kterych jeste nevime, co o rozsahu rika Jellyfin.

    Jen ty s prazdnym udajem: jinak by kazda analyza tahala z Jellyfinu
    celou knihovnu znovu, prestoze se ta hodnota meni jen pri prekodovani
    souboru - a to zachyti synchronizace.
    """
    kde = ["is_missing = 0", "video_range_reported IS NULL"]
    hodnoty: list[Any] = []
    if library_id:
        kde.append("library_id = ?")
        hodnoty.append(library_id)
    if item_ids:
        otazniky = ",".join("?" for _ in item_ids)
        kde.append(f"id IN ({otazniky})")
        hodnoty.extend(item_ids)
    return [radek["id"] for radek in db.query_all(
        f"SELECT id FROM items WHERE {' AND '.join(kde)}", tuple(hodnoty))]


async def doplnit_rozsah_z_jellyfinu(item_ids: list[str]) -> int:
    """Zapise k polozkam dynamicky rozsah tak, jak ho vidi Jellyfin.

    Vedle toho, co zmeril ffprobe. Dolby Vision v Matrosce umi cist az
    ffmpeg 5, takze starsi ffprobe hlasi jen "HDR" - a Jellyfin je pak
    jediny, kdo o DV vi. Viz stats.ROZSAH_CASE.

    Vraci pocet polozek, u kterych Jellyfin nejaky rozsah rekl.
    """
    if not item_ids:
        return 0

    async with JellyfinClient(*db.jellyfin_connection()) as client:
        polozky = await client.items_by_ids(list(item_ids))

    zmeny = [(video_range_of(item), item.get("Id"))
             for item in polozky if item.get("Id")]
    if not zmeny:
        return 0

    def _uloz() -> None:
        with db.connect() as conn:
            conn.executemany(
                "UPDATE items SET video_range_reported = ? WHERE id = ?", zmeny)

    await asyncio.to_thread(_uloz)
    nalezeno = sum(1 for rozsah, _ in zmeny if rozsah)
    if nalezeno:
        log.info("rozsah z Jellyfinu doplnen u %s polozek", nalezeno)
    return nalezeno


def _save_probe_result(item_id: str, tech: dict[str, Any]) -> None:
    """Uloží výsledek jedné analýzy. Běží ve vlákně."""
    _save_tech(item_id, tech)
    save_streams(item_id, tech.get("streams") or [])


def _save_tech(item_id: str, tech: dict[str, Any]) -> None:
    with db.connect() as conn:
        conn.execute(
            """
            UPDATE items
               SET container = ?, video_codec = ?, audio_codec = ?, audio_channels = ?,
                   width = ?, height = ?, bitrate = ?, size_bytes = ?, video_range = ?,
                   audio_languages = ?, subtitle_languages = ?, default_audio_language = ?,
                   tech_source = 'ffprobe', tech_updated_at = ?, tech_error = NULL
             WHERE id = ?
            """,
            (
                tech.get("container"), tech.get("video_codec"), tech.get("audio_codec"),
                tech.get("audio_channels"), tech.get("width"), tech.get("height"),
                tech.get("bitrate"), tech.get("size_bytes"), tech.get("video_range"),
                tech.get("audio_languages"), tech.get("subtitle_languages"),
                tech.get("default_audio_language"),
                db.utcnow(), item_id,
            ),
        )


def _save_tech_error(item_id: str, message: str) -> None:
    with db.connect() as conn:
        conn.execute(
            "UPDATE items SET tech_error = ?, tech_updated_at = ? WHERE id = ?",
            (message[:300], db.utcnow(), item_id),
        )
