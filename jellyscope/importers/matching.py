"""The second guard against duplicates: a match by content (user, item, overlapping time).

Part of the package importers (see jellyscope/importers/__init__.py)."""
from __future__ import annotations

import calendar
from datetime import UTC, datetime
from typing import Any

from .. import db
from .base import _normalize_id

# ---------------------------------------------------------------------------
# The second guard against duplicates: a match by content
# ---------------------------------------------------------------------------
#
# The key `import:pbr:<rowid>:<itemid>` guards a repeated import **of the same source**. It is not enough,
# though, for two cases that happen easily in practice:
#
#   1. The same history is imported from Jellystat **and** from Playback Reporting. The keys have another
#      shape (jst / pbr), so they do not know about each other and everything doubles.
#   2. A period Jellyscope collected by itself is imported. Its own records have a completely different
#      session_key.
#
# So we compare the content too. We take as the same record a play with the same user, the same item and
# **an overlapping time**.
#
# Why an overlap and not the same start: every source writes the time a little differently (the start of
# the play vs. the moment of writing into its table), so an exact match would let most duplicates through.
# An overlap is more robust and safe at the same time - one person cannot watch the same movie twice at once.


def _epoch_slow(time: str | None) -> float | None:
    """An honest conversion through strptime. The fallback for anything unexpected."""
    try:
        return datetime.strptime(str(time), db.TIME_FORMAT).replace(
            tzinfo=UTC).timestamp()
    except (TypeError, ValueError):
        return None


def _epoch(time: str | None) -> float | None:
    """A time from the database converted to seconds, so that it can be compared.

    A fast path for the shape that really is in the database (`db.TIME_FORMAT`, i.e.
    "2026-09-09 10:20:30"): it is computed from the characters' positions. `strptime` is comfortable,
    but parses the format string again on every call - and this function is called 300,000 times when
    Settings open once, which took 1.6 seconds.

    Anything else goes the old way. That is no extra caution: if the fast path accepted something
    strptime refuses (say a time with milliseconds), it would change which rows count as duplicates -
    and that is a change of the data, not a speed-up.
    """
    if not time:
        return None
    if (type(time) is str and len(time) == 19
            and time[4] == "-" and time[7] == "-" and time[10] == " "
            and time[13] == ":" and time[16] == ":"):
        try:
            return float(calendar.timegm((
                int(time[0:4]), int(time[5:7]), int(time[8:10]),
                int(time[11:13]), int(time[14:16]), int(time[17:19]),
                0, 0, 0)))
        except ValueError:
            # E.g. "2026-13-45 99:99:99" - the shape fits, the values do not.
            return _epoch_slow(time)
    return _epoch_slow(time)


def _playback_index() -> dict[tuple[str, str], list[tuple[float, float]]]:
    """The time spans of the plays stored already, by user and item."""
    index: dict[tuple[str, str], list[tuple[float, float]]] = {}

    for row in db.query_all(
        """
        SELECT user_id, item_id, started_at, ended_at, watched_seconds
          FROM playback
         WHERE user_id IS NOT NULL AND user_id != ''
           AND item_id IS NOT NULL AND item_id != ''
        """
    ):
        start = _epoch(row["started_at"])
        if start is None:
            continue
        end = _epoch(row["ended_at"])
        if end is None or end <= start:
            end = start + max(int(row["watched_seconds"] or 0), 1)

        key = (_normalize_id(row["user_id"]), _normalize_id(row["item_id"]))
        index.setdefault(key, []).append((start, end))

    return index


def _already_there(index: dict[tuple[str, str], list[tuple[float, float]]],
                   user_id: Any, item_id: Any, started_at: str, duration: int) -> bool:
    """Do we have this play already - say from another source?

    When we do not know who played it or what, we rather claim nothing: without both values two different
    plays could be mixed up and we would lose data. The key `session_key` still holds in such a case.
    """
    user = _normalize_id(user_id)
    item = _normalize_id(item_id)
    if not user or not item:
        return False

    start = _epoch(started_at)
    if start is None:
        return False
    end = start + max(int(duration or 0), 1)

    for old_start, old_end in index.get((user, item), []):
        # Touching spans (end == start) are no overlap - the viewer may have started the next episode right
        # after finishing the previous one.
        if start < old_end and old_start < end:
            return True
    return False


def _remember(index: dict[tuple[str, str], list[tuple[float, float]]],
              user_id: Any, item_id: Any, started_at: str, duration: int) -> None:
    """Add the record just accepted to the index.

    Without this step duplicates **inside one file** would not be recognised - and backups hold them now
    and then.
    """
    user = _normalize_id(user_id)
    item = _normalize_id(item_id)
    start = _epoch(started_at)
    if not user or not item or start is None:
        return
    index.setdefault((user, item), []).append(
        (start, start + max(int(duration or 0), 1)))


def _known_users() -> dict[str, str]:
    return {row["id"]: row["name"] for row in db.query_all("SELECT id, name FROM users")}


def _known_items() -> dict[str, dict[str, Any]]:
    return {
        row["id"]: row
        for row in db.query_all(
            "SELECT id, name, type, series_name, library_id, default_audio_language"
            " FROM items"
        )
    }
