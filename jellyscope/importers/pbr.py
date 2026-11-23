"""The import from Playback Reporting (a Jellyfin plugin) - through the API and from a TSV file.

Part of the package importers (see jellyscope/importers/__init__.py)."""
from __future__ import annotations

import logging
from typing import Any

from .. import db, i18n, scanner
from ..jellyfin import JellyfinClient, JellyfinError
from .base import (
    ImportError_,
    _build_row,
    _existing_keys,
    _insert,
    _language_from_source,
    _parse_time,
    read_only,
)
from .linking import link_imported_history
from .matching import (
    _already_there,
    _known_items,
    _known_users,
    _playback_index,
    _remember,
)

log = logging.getLogger("jellyscope.importers")


# The query into the Playback Reporting plugin's table.
PBR_QUERY = (
    "SELECT rowid, DateCreated, UserId, ItemId, ItemType, ItemName, "
    "PlaybackMethod, ClientName, DeviceName, PlayDuration "
    "FROM PlaybackActivity ORDER BY DateCreated"
)

# The fallback without `rowid`. Some versions of the plugin do not allow that column in their query interface
# and refuse the whole query with error 500, so the import could not run at all. Without rowid we lose only a
# part of the key against a repeated import - and today a comparison by content stands in for it (see
# _already_there).
PBR_QUERY_WITHOUT_ROWID = (
    "SELECT DateCreated, UserId, ItemId, ItemType, ItemName, "
    "PlaybackMethod, ClientName, DeviceName, PlayDuration "
    "FROM PlaybackActivity ORDER BY DateCreated"
)


# ---------------------------------------------------------------------------
# 1. Playback Reporting (a Jellyfin plugin)
# ---------------------------------------------------------------------------

async def playback_reporting_available() -> tuple[bool, str]:
    """Find out whether the plugin is installed and answers."""
    try:
        async with JellyfinClient(*db.jellyfin_connection()) as client:
            response = await client._client.post(
                "/user_usage_stats/submit_custom_query",
                json={"CustomQueryString":
                          read_only("SELECT COUNT(*) FROM PlaybackActivity"),
                      "ReplaceUserId": False},
            )
    except Exception as exc:  # noqa: BLE001
        return False, i18n.translate_with("Could not ask Jellyfin: {error}", None, error=i18n.error_text(exc))

    if response.status_code == 404:
        return False, "The Playback Reporting plugin is not installed in Jellyfin."
    if response.status_code >= 400:
        # Here too we write out the answer's body - "error 500" without it tells nobody where to look.
        return False, i18n.translate_with("The plugin answered with error {status}: {body}", None,
                                          status=response.status_code, body=(response.text or "").strip()[:300])

    try:
        results = (response.json() or {}).get("results") or []
        count = int(results[0][0]) if results and results[0] else 0
    except (ValueError, IndexError, TypeError):
        return True, "The plugin answers, but the number of records could not be read."

    return True, i18n.translate_plural("The plugin was found, it holds {n} records.", count)


async def import_playback_reporting(min_seconds: int = 60) -> dict[str, Any]:
    """Take over the history from the Playback Reporting plugin."""
    scan_id = scanner.start_task_log("import")

    try:
        async with JellyfinClient(*db.jellyfin_connection()) as client:
            # We try both shapes of the query. The first is richer (it holds rowid, from which we compose the
            # key against a repeated import), the second passes also where the plugin refuses rowid.
            last_error = ""
            payload = None

            for query in (PBR_QUERY, PBR_QUERY_WITHOUT_ROWID):
                response = await client._client.post(
                    "/user_usage_stats/submit_custom_query",
                    json={"CustomQueryString": read_only(query), "ReplaceUserId": False},
                )
                if response.status_code == 404:
                    raise ImportError_(
                        "The Playback Reporting plugin is not installed. "
                        "You will find it in Jellyfin: Dashboard → Plugins → Catalog.")
                if response.status_code < 400:
                    payload = response.json() or {}
                    if query is PBR_QUERY_WITHOUT_ROWID:
                        log.info("the plugin refused rowid, using the simplified query")
                    break

                # The answer's body says what the plugin did not like. Without it "error 500" is a message one
                # can do nothing with.
                last_error = (
                    f"{response.status_code}: {(response.text or '').strip()[:300]}"
                )
                log.warning("the plugin refused the query (%s)", last_error)

            if payload is None:
                raise ImportError_(
                    "The Playback Reporting plugin answered with error {error}\n\n"
                    "The most common cause is a VERSION MISMATCH: a plugin built against an older Jellyfin "
                    "crashes before it even gets to its data and returns only \"Error processing request\". "
                    "It cannot be fixed from Jellyscope - the plugin's endpoint is then dead for any query.\n\n"
                    "Jellyfin's log tells for sure; look for submit_custom_query in it. If "
                    "MissingMethodException stands there, it is exactly this case.\n\n"
                    "What to do:\n"
                    "  * upload the plugin's data from a file - see \"API import not working?\" right below "
                    "this button,\n"
                    "  * or bring the history over from a Jellystat backup,\n"
                    "  * or wait for a version of the plugin for your Jellyfin - then the import through the "
                    "API starts working by itself.",
                    error=last_error)

    except (JellyfinError, ImportError_) as exc:
        scanner.finish_task_log(scan_id, "error", message=exc)
        return {"status": "error", "message": i18n.error_text(exc)}
    except Exception as exc:  # noqa: BLE001
        log.exception("import from Playback Reporting failed")
        scanner.finish_task_log(scan_id, "error", message=exc)
        return {"status": "error", "message": i18n.translate_with("Unexpected error: {error}", None, error=exc)}

    columns = [str(c).lower() for c in (payload.get("colums") or payload.get("columns") or [])]
    results = payload.get("results") or []

    return await _process_pbr(scan_id, columns, results, min_seconds,
                              "Playback Reporting")


# The order of columns in the Playback Reporting plugin's backup. It matches the table PlaybackActivity - the
# plugin writes it into the file as it is.
PBR_TSV_COLUMNS = ["datecreated", "userid", "itemid", "itemtype", "itemname",
                   "playbackmethod", "clientname", "devicename", "playduration"]


def _tsv_columns(first_row: list[str]) -> tuple[list[str], bool]:
    """Decide whether the first row is a header or data already.

    The plugin writes the backup both ways, depending on the version. We recognise it by the content: a
    header carries the columns' names, a data row starts with a date. Guessing by the count of columns would
    not do - it is the same.
    """
    lower = [str(cell).strip().lower() for cell in first_row]
    if "datecreated" in lower or "userid" in lower:
        return lower, True

    # Without a header we keep the table's order. When a row has more columns (some versions add rowid at the
    # start), we name the surplus at the start, so the pairs do not drift apart.
    extra = len(first_row) - len(PBR_TSV_COLUMNS)
    if extra > 0:
        return ["rowid"] * extra + PBR_TSV_COLUMNS, False
    return PBR_TSV_COLUMNS, False


async def import_playback_reporting_tsv(raw: bytes,
                                        min_seconds: int = 60) -> dict[str, Any]:
    """Take over the history from a backup of the Playback Reporting plugin (a TSV file).

    The fallback for when the plugin does not work through the API. It happens with a version mismatch:
    a plugin built against an older Jellyfin fails with `MissingMethodException` before it gets to its data
    and returns only "Error processing request".

    You make the file right in the plugin: **Jellyfin -> Dashboard -> Playback Reporting -> Backup -> Save
    backup**. The result is a plain text file with the values separated by tabs.

    Why TSV and not `playback_reporting.db`: the plugin makes the backup by itself with one click, whereas
    to get to the database file one must go through SSH and know where it lies. Jellyfin is touched in
    neither case - we read the copy you upload.

    The rows are processed exactly as from the API, the key against duplicates included - when the API is
    fixed later and you run the import again, nothing doubles.
    """
    scan_id = scanner.start_task_log("import")

    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        # Backups from Windows tend to be in cp1250; and when not even that fits, we rather replace the unread
        # characters than refuse the import because of a single letter in a movie's name.
        text = raw.decode("cp1250", errors="replace")

    # Only a line feed ends a record (\r\n of Windows too): str.splitlines() also ends a line at U+2028, NEL or
    # a form feed, and a title with one of them would fall apart into two halves (found 22. 11. 2026).
    lines = [line.removesuffix("\r") for line in text.split("\n") if line.strip()]
    if not lines:
        scanner.finish_task_log(scan_id, "error", message="The file is empty.")
        return {"status": "error", "message": "The file is empty."}

    split = [line.split("\t") for line in lines]

    # A file without tabs is almost surely something other than one thinks. Saying so right away is better
    # than importing zero records without an explanation.
    if max(len(r) for r in split) < 2:
        not_tsv = ("The file has no tabs, so it is not a Playback Reporting backup. Make one in Jellyfin: "
                   "Dashboard → Playback Reporting → Backup → Save backup.")
        scanner.finish_task_log(scan_id, "error", message=not_tsv)   # the key, translated for the reader
        return {"status": "error", "message": not_tsv}

    columns, has_header = _tsv_columns(split[0])
    values = split[1:] if has_header else split

    return await _process_pbr(scan_id, columns, values, min_seconds,
                              "Playback Reporting (TSV backup)")


async def _process_pbr(scan_id: int, columns: list[str], results: list[Any],
                       min_seconds: int, source: str) -> dict[str, Any]:
    """The shared processing of Playback Reporting's rows.

    One function for both ways (the API and the file). If there were two copies, a fix in one would not get
    into the other in time - and nobody would notice duplicates or wrong keys until someone used exactly the
    other way.
    """
    users = _known_users()
    items = _known_items()
    existing = _existing_keys("import:pbr:")
    time_index = _playback_index()

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

        # The record's key. `rowid` is the best, since the plugin does not change it - but when it refused it
        # (see PBR_QUERY_WITHOUT_ROWID), we compose the key from what describes the record unambiguously.
        # Without this fallback all rows of the same title would get the same key and only one of them would be
        # imported.
        label = data.get("rowid")
        if label is None:
            label = f"{data.get('datecreated')}:{data.get('userid')}"
        key = f"import:pbr:{label}:{data.get('itemid')}"
        if key in existing:
            skipped_duplicate += 1
            continue
        existing.add(key)

        # The same record may be in the database already from Jellystat or from the collector.
        if _already_there(time_index, data.get("userid"), data.get("itemid"),
                          started_at, duration):
            skipped_known += 1
            continue
        _remember(time_index, data.get("userid"), data.get("itemid"),
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
            audio_language=_language_from_source(data),
            users=users,
            items=items,
        ))

    imported = _insert(rows)

    # The import sends no tmdb ID - we look it up ourselves, so the imported history joins the real titles in
    # the library.
    linked = await link_imported_history()

    scanner.finish_task_log(
        scan_id, "done", total=len(results), ok=imported, failed=0,
        message=i18n.Message(
            "{source} - new: {imported}, already there: {duplicates}, from another source: {known}, too short: {short}",
            source=i18n.Message(source), imported=imported, duplicates=skipped_duplicate, known=skipped_known,
            short=skipped_short),
    )
    return {
        "status": "ok",
        "source": source,
        "found": len(results),
        "imported": imported,
        "duplicate": skipped_duplicate,
        "known_elsewhere": skipped_known,
        "too_short": skipped_short,
        "linked": linked,
    }
