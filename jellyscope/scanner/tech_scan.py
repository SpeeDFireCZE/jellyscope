"""The technical analysis of files (ffprobe or Jellyfin) and the dynamic range.

Part of the package scanner (see jellyscope/scanner/__init__.py)."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from .. import db, i18n, languages, probe
from ..jellyfin import JellyfinClient, JellyfinError, video_range_of
from .stream_languages import (
    candidates_for_language_from_name,
    fill_languages_from_jellyfin,
    fill_languages_from_name,
)
from .task_state import (
    _add_progress,
    _clear_progress,
    _clear_stop,
    _scan_lock,
    _start_progress,
    finish_task_log,
    start_task_log,
    stop_requested,
)
from .writing import save_streams

log = logging.getLogger("jellyscope.scanner")


# ---------------------------------------------------------------------------
# 2. The technical analysis through ffprobe
# ---------------------------------------------------------------------------

def _path_mappings(settings: dict[str, Any]) -> list[Any]:
    """The path mappings from the settings (a path in Jellyfin → a path here); broken = none."""
    try:
        mappings = json.loads(settings.get("path_mappings") or "[]")
        if not isinstance(mappings, list):
            mappings = []
    except ValueError:
        mappings = []
    return mappings


def _analysis_query(only_missing: bool, limit: int, item_ids: list[str] | None,
                    library_id: str | None) -> tuple[str, tuple[Any, ...]] | None:
    """The SQL and parameters of picking files for the analysis. Runs nothing.

    `None` = the list of items is empty, so there is nothing to pick.
    """
    where = "WHERE is_missing = 0 AND path IS NOT NULL AND path != ''"
    if only_missing:
        where += " AND (tech_source IS NULL OR tech_source != 'ffprobe')"

    params: list[Any] = []
    if library_id:
        where += " AND library_id = ?"
        params.append(library_id)

    if item_ids is not None:
        if not item_ids:
            return None
        # Placeholders, not a glued list - the ids come from Jellyfin's answer and values never go into SQL
        # directly.
        marks = ",".join("?" for _ in item_ids)
        where += f" AND id IN ({marks})"
        params.extend(item_ids)

    sql = f"SELECT id, path FROM items {where} ORDER BY name"
    if limit > 0:
        sql += f" LIMIT {int(limit)}"
    return sql, tuple(params)


async def _analyse_files(
        targets: list[dict[str, Any]], ffprobe_bin: str, mappings: list[Any],
        concurrency: int) -> tuple[dict[str, int], set[str]]:
    """Measure files through ffprobe, at most `concurrency` at once.

    Returns the counts (ok / failed / skipped) and the items whose file does not state a language for at least
    one stream. After a stop the files at hand finish and the waiting ones are skipped.
    """
    # The semaphore limits how many ffprobe processes run at the same time. Without it a big library would start
    # thousands of processes at once and the machine would stand still.
    _start_progress("tech", len(targets))
    semaphore = asyncio.Semaphore(concurrency)
    results = {"ok": 0, "failed": 0, "skipped": 0}
    results_lock = asyncio.Lock()
    # Items whose file does not state a language for at least one stream. We then ask Jellyfin about those - see
    # fill_languages_from_jellyfin().
    without_language: set[str] = set()

    async def analyse(row: dict[str, Any]) -> None:
        async with semaphore:
            # The check only here, behind the semaphore: as many tasks are prepared as there are files, but only a
            # few run at the same time. The waiting ones are quietly skipped after a stop, while those at hand run
            # to the end. So no file ends half analysed.
            if stop_requested():
                async with results_lock:
                    results["skipped"] += 1
                return

            # One file = one step. `_add_progress(1)` used to be called at three places inside this function (after
            # taking the semaphore, after the analysis and after the write), so a finished file moved the bar by three
            # - and it then showed say 135 %. `finally` also makes sure the step counts for a file that ended with an
            # error too.
            try:
                local_path = probe.apply_path_mappings(row["path"], mappings)
                try:
                    tech = await probe.probe_file(local_path, ffprobe_bin)
                except probe.ProbeError as exc:
                    await asyncio.to_thread(_save_tech_error, row["id"], exc)
                    async with results_lock:
                        results["failed"] += 1
                    return

                # The write into a thread too - with a big library this block is in one loop a thousand times and every
                # write would otherwise stop serving the web for a moment.
                await asyncio.to_thread(_save_probe_result, row["id"], tech)
                async with results_lock:
                    results["ok"] += 1
                    if _missing_language(tech.get("streams") or []):
                        without_language.add(row["id"])
            finally:
                _add_progress(1)

    await asyncio.gather(*(analyse(row) for row in targets))
    _clear_progress()
    return results, without_language


async def _additions_after_analysis(without_language: set[str], library_id: str | None,
                                    item_ids: list[str] | None) -> tuple[int, int, int]:
    """Fill in what the files did not say: the language from Jellyfin, a guess from the name and the range.

    Returns (streams filled in, guessed from names, ranges). A Jellyfin that does not answer is only a missing
    addition - it is logged and the measured data still hold.
    """
    # Only after the analysis, not during it: fifty items can be asked about in one request, whereas in the middle
    # of the loop it would be one request per file. And when Jellyfin does not answer right then, it is only a
    # missing addition - the measured data are stored already, so it must not count as a failure of the whole
    # analysis.
    # Backwards to what was measured earlier too. Without this the filling in would hold only for newly analysed
    # files - i.e. for almost nothing, because a library is usually measured whole long ago. We ask only about
    # streams without a mark of origin, so we do not ask about the same ones a second time (see
    # _fill_item_languages).
    condition = ("language = ? AND language_source IS NULL "
                 "AND type IN ('Audio', 'Subtitle')")
    back_params: list[Any] = [languages.UNKNOWN]
    # The same scope as with the analysis: refreshing one item must not start asking about the whole library.
    if library_id:
        condition += (" AND item_id IN (SELECT id FROM items WHERE library_id = ?)")
        back_params.append(library_id)
    if item_ids:
        marks = ",".join("?" for _ in item_ids)
        condition += f" AND item_id IN ({marks})"
        back_params.extend(item_ids)

    for row in db.query_all(
            f"SELECT DISTINCT item_id FROM item_streams WHERE {condition}",
            tuple(back_params)):
        without_language.add(row["item_id"])

    filled = 0
    if without_language and not stop_requested():
        try:
            filled = await fill_languages_from_jellyfin(sorted(without_language))
        except JellyfinError as exc:
            log.warning("languages could not be filled in from Jellyfin: %s", exc)

    # What not even Jellyfin knows we try to guess from the file name. Only now and only on what is left: the name
    # is a guess, whereas the file and the library are data.
    left = set(candidates_for_language_from_name(library_id, item_ids))
    from_name = 0
    if left and not stop_requested():
        from_name = await asyncio.to_thread(fill_languages_from_name, sorted(left))

    # The dynamic range by Jellyfin. The library sync writes it already, but one connects technical data with THIS
    # task - one started "Analyza souboru", saw nothing changed, and had no reason to suspect that another task
    # fills in the missing Dolby Vision.
    ranges = 0
    if not stop_requested():
        missing = candidates_for_range_from_jellyfin(library_id, item_ids)
        if missing:
            try:
                ranges = await fill_range_from_jellyfin(missing)
            except JellyfinError as exc:
                log.warning("the dynamic range could not be filled in from Jellyfin: %s", exc)
    return filled, from_name, ranges


def _additions_text(filled: int, from_name: int, ranges: int) -> i18n.Message:
    """The addition to the message in the task's log: what was filled in other than from ffprobe."""
    parts = []
    if filled:
        parts.append(i18n.Message(", language filled in from Jellyfin for {n} tracks", n=filled))
    if from_name:
        parts.append(i18n.Message(", guessed from the name for {n} titles", n=from_name))
    if ranges:
        parts.append(i18n.Message(", dynamic range from Jellyfin for {n} titles", n=ranges))
    return i18n.join_messages(parts, "")


async def run_tech_scan(only_missing: bool = True, limit: int = 0,
                        item_ids: list[str] | None = None,
                        library_id: str | None = None) -> dict[str, Any]:
    """Go through the items and fill in the technical data with ffprobe.

    `only_missing=True` means "only those that do not have ffprobe data yet". Thanks to it a long scan can be run
    in parts and does not always start from zero.

    `item_ids` limits the run to the listed items. The quick sync uses it: a newly added title gets its technical
    data right away instead of waiting for the next run of the daily task.

    `library_id` limits the run to one library - the button right at the message "several files have no technical
    data" on a library's detail uses it.
    """
    if _scan_lock.locked():
        return {"status": "busy", "message": "Another task is already running."}

    async with _scan_lock:
        _clear_stop()
        settings = db.get_settings()
        ffprobe_bin = probe.find_ffprobe(settings.get("ffprobe_path", ""))
        if not ffprobe_bin:
            return {
                "status": "error",
                "message": "ffprobe could not be found. Install ffmpeg, or fill in the full path to ffprobe "
                           "(ffprobe.exe on Windows) in Settings.",
            }

        mappings = _path_mappings(settings)

        concurrency = db.get_int_setting("ffprobe_concurrency", 1, 16, 3)

        query = _analysis_query(only_missing, limit, item_ids, library_id)
        if query is None:
            return {"status": "ok", "total": 0, "ok": 0, "failed": 0}

        targets = db.query_all(*query)
        scan_id = start_task_log("tech")

        if not targets:
            finish_task_log(scan_id, "done", message="Nothing to analyse.")  # the key: the task log is read in the reader's language
            return {"status": "ok", "total": 0, "ok": 0, "failed": 0}

        results, without_language = await _analyse_files(targets, ffprobe_bin, mappings, concurrency)
        filled, from_name, ranges = await _additions_after_analysis(without_language, library_id, item_ids)
        addition = _additions_text(filled, from_name, ranges)

        if results["skipped"]:
            finish_task_log(
                scan_id, "stopped",
                total=len(targets), ok=results["ok"], failed=results["failed"],
                message=i18n.Message(
                    "Stopped at your request. {n} files are left - the next analysis goes on with them. ffprobe: "
                    "{path}{addition}", n=results["skipped"], path=ffprobe_bin, addition=addition),
            )
            return {"status": "stopped", "total": len(targets), **results}

        finish_task_log(
            scan_id, "done",
            total=len(targets), ok=results["ok"], failed=results["failed"],
            message=i18n.join_messages([f"ffprobe: {ffprobe_bin}", addition], ""),
        )
        return {"status": "ok", "total": len(targets), "filled": filled,
                "from_name": from_name, **results}


def _missing_language(streams: list[dict[str, Any]]) -> bool:
    """Does at least one audio or subtitle stream have "not stated"?

    Video is not handled: nobody expects a language for the picture, and "not stated" is no missing value there
    but the right answer.
    """
    return any(s.get("type") in ("Audio", "Subtitle")
               and s.get("language") == languages.UNKNOWN
               for s in streams)


def candidates_for_range_from_jellyfin(library_id: str | None = None,
                                       item_ids: list[str] | None = None) -> list[str]:
    """Items for which we do not know yet what Jellyfin says about the range.

    Only those with an empty value: otherwise every analysis would pull the whole library from Jellyfin again,
    although the value changes only when a file is transcoded - and the sync catches that.
    """
    where_parts = ["is_missing = 0", "video_range_reported IS NULL"]
    values: list[Any] = []
    if library_id:
        where_parts.append("library_id = ?")
        values.append(library_id)
    if item_ids:
        marks = ",".join("?" for _ in item_ids)
        where_parts.append(f"id IN ({marks})")
        values.extend(item_ids)
    return [row["id"] for row in db.query_all(
        f"SELECT id FROM items WHERE {' AND '.join(where_parts)}", tuple(values))]


async def fill_range_from_jellyfin(item_ids: list[str]) -> int:
    """Write the dynamic range to the items as Jellyfin sees it.

    Beside what ffprobe measured. Dolby Vision in Matroska can be read only by ffmpeg 5, so an older ffprobe
    reports just "HDR" - and Jellyfin is then the only one who knows about DV. See stats.RANGE_CASE.

    Returns the count of items for which Jellyfin stated some range.
    """
    if not item_ids:
        return 0

    async with JellyfinClient(*db.jellyfin_connection()) as client:
        items = await client.items_by_ids(list(item_ids))

    changes = [(video_range_of(item), item.get("Id"))
               for item in items if item.get("Id")]
    if not changes:
        return 0

    def _save() -> None:
        with db.connect() as conn:
            conn.executemany(
                "UPDATE items SET video_range_reported = ? WHERE id = ?", changes)

    await asyncio.to_thread(_save)
    found = sum(1 for video_range, _ in changes if video_range)
    if found:
        log.info("dynamic range from Jellyfin filled in for %s items", found)
    return found


def _save_probe_result(item_id: str, tech: dict[str, Any]) -> None:
    """Store the result of one analysis. Runs in a thread."""
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
                   tech_source = 'ffprobe', tech_updated_at = ?, tech_error = NULL, tech_error_values = NULL
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


def _save_tech_error(item_id: str, error: BaseException) -> None:
    """Why the analysis failed - a key and its values (i18n.message_parts), read in the reader's language."""
    template, values = i18n.message_parts(error)
    with db.connect() as conn:
        conn.execute(
            "UPDATE items SET tech_error = ?, tech_error_values = ?, tech_updated_at = ? WHERE id = ?",
            (template[:300], values, db.utcnow(), item_id),
        )
