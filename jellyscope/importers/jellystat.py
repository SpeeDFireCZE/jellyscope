"""The import from a Jellystat backup (JSON).

Part of the package importers (see jellyscope/importers/__init__.py)."""
from __future__ import annotations

import json
import logging
from typing import Any

from .. import db, i18n, scanner
from .base import _build_row, _existing_keys, _insert, _parse_time
from .linking import link_imported_history
from .matching import (
    _already_there,
    _known_items,
    _known_users,
    _playback_index,
    _remember,
)

log = logging.getLogger("jellyscope.importers")


def _jellystat_item_type(record: dict[str, Any]) -> str | None:
    """A movie or an episode? Jellystat does not say it in its backup directly.

    The table jf_playback_activity has no column with the type - it knows the series' name and the
    episode's id, though. When either is filled in, it is an episode; otherwise a movie.

    `NowPlayingItemName`, i.e. **the title's name**, used to be read here by mistake. Then for example
    "Duna" was stored into the column item_type and everything that tells movies from series stopped
    working - the chart of watching by days stayed empty, although the hours in the summary matched.
    """
    explicit = _pick(record, "ItemType", "item_type", "NowPlayingItemType", "Type")
    if explicit in ("Movie", "Episode", "Series", "Audio", "Book", "AudioBook"):
        return explicit

    if _pick(record, "SeriesName", "seriesname", "series_name") \
            or _pick(record, "EpisodeId", "episodeid", "SeasonId", "seasonid"):
        return "Episode"
    return "Movie"


# ---------------------------------------------------------------------------
# 2. Jellystat (a JSON backup)
# ---------------------------------------------------------------------------

# Jellystat stores plays in the table jf_playback_activity. The backup may hold either a list directly, or a
# dict with the tables' names.
JELLYSTAT_TABLES = ("jf_playback_activity", "playback_activity", "jf_playback")


def _find_jellystat_rows(payload: Any) -> list[dict[str, Any]]:
    """Find the list of plays in a JSON backup.

    Jellystat changed the backup's format between versions, so we do not rely on one shape and look for
    several possibilities. When nothing fits, we say so out loud instead of importing emptiness and
    pretending it is done.
    """
    if isinstance(payload, list):
        # Either a list of records directly, or a list of tables.
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
        # Sometimes everything is wrapped one level lower.
        for value in payload.values():
            if isinstance(value, (list, dict)):
                found = _find_jellystat_rows(value)
                if found:
                    return found

    return []


def _pick(record: dict[str, Any], *names: str) -> Any:
    """Return the first filled-in field of several possible names."""
    lowered = {str(k).lower(): v for k, v in record.items()}
    for name in names:
        value = lowered.get(name.lower())
        if value not in (None, ""):
            return value
    return None


def _by_source(existing: set[str]) -> dict[str, str]:
    """A source row of the backup → the key it is in the database under already.

    The source row is recognised by the key's prefix `import:jst:<the row's id in the backup>`; what
    follows is the item the record points at.
    """
    # For every source row (the first two parts of the key) we remember under which key it is in the
    # database already. A repair is recognised by it.
    by_source: dict[str, str] = {}
    for key in existing:
        parts = key.split(":")
        if len(parts) >= 4:
            by_source[":".join(parts[:3])] = key
    return by_source


def _read_record(record: Any, index: int) -> dict[str, Any] | None:
    """What one record of the backup holds: the time, the length, the episode or the series, the viewer, the names and the key.

    `None` = the record cannot be used (it is not a dict, or it has no time).
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

    # For an episode Jellystat sends TWO different ids and it matters which is taken:
    #
    #   NowPlayingItemId  = the SERIES' id    ("Tym SEAL")
    #   EpisodeId         = the EPISODE's id  (what we want)
    #
    # The first one used to be taken, so the whole history of a series ended up on one id nothing in the
    # library leads to - and since the record was called by the series' name, the episode could not be
    # worked out at all. No look-up fixes that any more; the only way is to take the right id right at the
    # import.
    episode_id = _pick(record, "EpisodeId", "episodeid", "episode_id")
    item_id = episode_id or _pick(record, "NowPlayingItemId", "nowplayingitemid",
                                  "ItemId")
    user_id = _pick(record, "UserId", "userid", "user_id")

    # The same story with the name: NowPlayingItemName is the series' name for an episode. The episode's
    # name is in EpisodeName - and we keep the series' name apart, so that the play files under its series.
    series_name = _pick(record, "SeriesName", "seriesname",
                        "NowPlayingItemName", "ItemName", "item_name")
    episode_name = _pick(record, "EpisodeName", "episodename", "episode_name")
    name = (episode_name if episode_id else None) or _pick(
        record, "NowPlayingItemName", "ItemName", "item_name")

    # The key has the shape "import:jst:<the row's id in the backup>:<the item's id>". The first two parts
    # identify the SOURCE ROW, the third what it points at - and exactly that one changes with a repair.
    base = f"import:jst:{_pick(record, 'Id', 'id') or index}"
    key = f"{base}:{item_id}"
    return {"started_at": started_at, "duration": duration, "episode_id": episode_id,
            "item_id": item_id, "user_id": user_id, "series_name": series_name,
            "name": name, "base": base, "key": key}


def _fix_redirected(entry: dict[str, Any], record: dict[str, Any],
                    by_source: dict[str, str], existing: set[str]) -> bool:
    """A repair of an old import: the same row of the backup is in the database already, it only points elsewhere.

    It repairs it in place and changes `by_source` and `existing` as if the record had been imported right
    straight away. `False` = there was nothing to repair.
    """
    key, base, episode_id = entry["key"], entry["base"], entry["episode_id"]
    item_id, name, series_name = entry["item_id"], entry["name"], entry["series_name"]
    # The same row of the backup is in the database already, it only points elsewhere - exactly the state
    # after an import that took the series' id instead of the episode's. We repair it in place; if we
    # inserted a new one, one would have every play twice.
    old_key = by_source.get(base)
    if old_key:
        with db.connect() as conn:
            conn.execute(
                """UPDATE playback
                      SET session_key = ?, item_id = ?, item_name = ?,
                          series_name = COALESCE(?, series_name),
                          item_type = ?
                    WHERE session_key = ?""",
                (key, item_id, name or "?",
                 series_name if episode_id else None,
                 _jellystat_item_type(record), old_key),
            )
        by_source[base] = key
        existing.discard(old_key)
        existing.add(key)
        return True
    return False


def _row_from_record(entry: dict[str, Any], record: dict[str, Any],
                     users: dict[str, str], items: dict[str, dict[str, Any]]) -> tuple[Any, ...]:
    """A history row for a new record - in the order of columns `_insert` expects."""
    return _build_row(
        key=entry["key"],
        user_id=entry["user_id"],
        item_id=entry["item_id"],
        started_at=entry["started_at"],
        duration=entry["duration"],
        item_type=_jellystat_item_type(record),
        item_name=entry["name"],
        series_name=entry["series_name"] if entry["episode_id"] else None,
        client=_pick(record, "Client", "client"),
        device=_pick(record, "DeviceName", "device_name"),
        method=_pick(record, "PlayMethod", "play_method", "PlaybackMethod"),
        users=users,
        items=items,
        user_name=_pick(record, "UserName", "username", "user_name"),
    )


async def import_jellystat_json(raw: bytes, min_seconds: int = 60) -> dict[str, Any]:
    """Take over the history from a Jellystat JSON backup."""
    scan_id = scanner.start_task_log("import")
    try:  # from the run's start: an odd file (a number among the records, JSON too deep) must not stay "running"
        try:
            payload = json.loads(raw.decode("utf-8", errors="replace"))
        except ValueError as exc:
            scanner.finish_task_log(scan_id, "error", message="The file is not valid JSON.")  # the key, translated
            return {"status": "error",
                    "message": i18n.translate_with("The file is not valid JSON: {error}", None, error=exc)}

        records = _find_jellystat_rows(payload)
        if not records:
            no_table = "There is no playback table (jf_playback_activity) in the file. Is it really a Jellystat backup?"
            scanner.finish_task_log(scan_id, "error", message=no_table)
            return {"status": "error", "message": no_table}

        users = _known_users()
        items = _known_items()
        existing = _existing_keys("import:jst:")
        by_source = _by_source(existing)
        repaired = 0
        time_index = _playback_index()

        rows: list[tuple[Any, ...]] = []
        skipped_short = 0
        skipped_duplicate = 0
        skipped_known = 0

        for index, record in enumerate(records):
            entry = _read_record(record, index)
            if entry is None:
                continue
            if entry["duration"] < min_seconds:
                skipped_short += 1
                continue

            key = entry["key"]
            if key in existing:
                skipped_duplicate += 1
                continue

            if _fix_redirected(entry, record, by_source, existing):
                repaired += 1
                continue

            existing.add(key)
            by_source[entry["base"]] = key

            # The same record may be in the database already from Playback Reporting or from the collector.
            if _already_there(time_index, entry["user_id"], entry["item_id"],
                              entry["started_at"], entry["duration"]):
                skipped_known += 1
                continue
            _remember(time_index, entry["user_id"], entry["item_id"],
                      entry["started_at"], entry["duration"])

            rows.append(_row_from_record(entry, record, users, items))

        imported = _insert(rows)

        # A Jellystat backup holds no tmdb ID - we look it up ourselves.
        linked = await link_imported_history()

        scanner.finish_task_log(
            scan_id, "done", total=len(records), ok=imported, failed=0,
            message=i18n.Message(
                "Jellystat - new: {imported}, repaired: {repaired}, already there: {duplicates}, from another source: "
                "{known}, too short: {short}", imported=imported, repaired=repaired, duplicates=skipped_duplicate,
                known=skipped_known, short=skipped_short),
        )
    except Exception as exc:  # noqa: BLE001 - the run in the task log must not stay "running"
        scanner.finish_task_log(scan_id, "error", message=exc)
        raise
    if repaired:
        log.info("import: %s records moved from the series to a specific episode",
                 repaired)
    return {
        "status": "ok",
        "source": "Jellystat",
        "found": len(records),
        "imported": imported,
        "repaired": repaired,
        "duplicate": skipped_duplicate,
        "known_elsewhere": skipped_known,
        "too_short": skipped_short,
        "linked": linked,
    }
