"""Duplicates between two import sources and episodes under a wrong series.

Part of the package importers (see jellyscope/importers/__init__.py)."""
from __future__ import annotations

import logging
from typing import Any

from .. import db
from .base import _normalize_id
from .matching import _epoch

log = logging.getLogger("jellyscope.importers")


# ---------------------------------------------------------------------------
# Duplicates between two import sources
# ---------------------------------------------------------------------------
#
# `duplicate_playback_groups()` (in cleanup.py) looks for **an overlap in time**. For duplicates from two
# collectors that is enough, since both describe the same session and differ by seconds. Between Jellystat
# and Playback Reporting an overlap is not enough, though: each writes down another moment (the start of the
# play vs. the write into the table), so the same two and a half hours of a movie come out at 18:15 here,
# at 18:49 there - and with night watching even the next morning.
#
# It can be recognised by something else, though: **an equally long play**. When the same person on the
# same device watched the same title for the same time (to a few minutes) and within one day, those are
# two writes about one evening's viewing, not two viewings.
#
# It is an estimate, not a proof - so it is used only where at least one of the records comes from an
# import. Our own collection makes no duplicates.

# How much the watched time may differ for it to still be the same experience.
LENGTH_TOLERANCE_S = 180

# How far apart the starts may be.
MAX_GAP_S = 24 * 3600


def _title_for_comparison(row: dict[str, Any]) -> str:
    """The name the title is recognised by across sources.

    The library's name comes first: every source names the title its own way ("Hra o truny - s08e05 -
    Zvony" vs. "Zvony"), but as soon as both records point at the same item, it has the same name.
    """
    return str(row.get("library_item_name") or row.get("item_name") or "").strip().lower()


def _import_exists() -> bool:
    """Is there at least one row from an import in the history?

    A cheap question instead of an expensive answer. A group counts only when it holds at least one
    imported record - who never imported anything always gets an empty result, only after going through
    the whole history.

    Measured on 150,000 rows: this query 100 ms, the whole pass 2,943 ms. Who has an import pays the extra
    100 ms and the counting goes on.
    """
    return bool(db.query_value(
        "SELECT 1 FROM playback WHERE session_key LIKE 'import:%' LIMIT 1"))


def import_duplicate_groups() -> list[list[dict[str, Any]]]:
    """Groups of records that describe the same viewing in two sources."""
    if not _import_exists():
        return []

    rows = db.query_all(
        """
        SELECT p.id, p.user_id, p.item_id, p.item_name, p.device_name,
               p.session_key, p.started_at, p.ended_at, p.watched_seconds,
               p.paused_seconds, p.audio_language, p.subtitle_language,
               p.library_id, p.series_name, p.is_active,
               i.name AS library_item_name
          FROM playback p
     LEFT JOIN items i ON i.id = p.item_id
         WHERE p.user_id IS NOT NULL AND p.user_id != ''
           AND p.watched_seconds > 0
         ORDER BY p.started_at, p.id
        """
    )

    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in rows:
        start = _epoch(row["started_at"])
        title = _title_for_comparison(row)
        if start is None or not title:
            continue
        row["_since"] = start
        row["_import"] = str(row["session_key"] or "").startswith("import:")
        # The device is NOT in the group's key on purpose - when one of the sources does not report it, the
        # key would drift apart and the duplicate would not be found. It is compared only in
        # `_belong_together()`, where an unknown device contradicts nobody.
        key = (_normalize_id(row["user_id"]), title)
        groups.setdefault(key, []).append(row)

    result: list[list[dict[str, Any]]] = []
    for records in groups.values():
        if len(records) < 2:
            continue
        records.sort(key=lambda r: r["_since"])

        open_group: list[dict[str, Any]] = []
        for record in records:
            if open_group and _belong_together(open_group[-1], record):
                open_group.append(record)
                continue
            if len(open_group) > 1 and any(r["_import"] for r in open_group):
                result.append(open_group)
            open_group = [record]
        if len(open_group) > 1 and any(r["_import"] for r in open_group):
            result.append(open_group)

    return result


def _belong_together(first: dict[str, Any], second: dict[str, Any]) -> bool:
    """Do these two records describe the same viewing?

    At least one must come from an import - our own collection makes no duplicates, and two honest records
    of two different evenings should not merge because the movie happened to have the same length.
    """
    if not (first["_import"] or second["_import"]):
        return False
    if abs(second["_since"] - first["_since"]) > MAX_GAP_S:
        return False

    # The devices must not contradict each other. One person may play the same movie on the TV and on the
    # phone; an unknown device contradicts nobody, otherwise a record from a source that does not send it
    # would never pair.
    device_a = str(first.get("device_name") or "").strip().lower()
    device_b = str(second.get("device_name") or "").strip().lower()
    if device_a and device_b and device_a != device_b:
        return False

    length_a = int(first["watched_seconds"] or 0)
    length_b = int(second["watched_seconds"] or 0)
    return abs(length_a - length_b) <= LENGTH_TOLERANCE_S


def _completeness(row: dict[str, Any]) -> tuple[int, ...]:
    """How rich the record is - the one that stays is chosen by it."""
    return (
        0 if row["_import"] else 1,          # our own collection first
        1 if row.get("audio_language") else 0,
        1 if row.get("subtitle_language") else 0,
        1 if row.get("library_id") else 0,
        1 if row.get("series_name") else 0,
        int(row.get("watched_seconds") or 0),
    )


def merge_import_duplicates() -> dict[str, Any]:
    """Merge records that describe the same viewing twice.

    The richest stays - typically the one from our own collection, which knows the language and the way of
    playing. They are not added up: two writes about one evening's movie are not two viewings.

    The start is taken as the group's earliest; it is closer to when the playing really started than the
    moment some tool wrote it down.
    """
    groups = import_duplicate_groups()
    if not groups:
        return {"status": "ok", "groups": 0, "removed": 0}

    deleted = 0
    with db.connect() as conn:
        for group in groups:
            group.sort(key=_completeness, reverse=True)
            main, others = group[0], group[1:]

            starts = [r["started_at"] for r in group if r["started_at"]]
            conn.execute(
                "UPDATE playback SET started_at = ?, watched_seconds = ? WHERE id = ?",
                (min(starts) if starts else main["started_at"],
                 # The longer of both: the shorter record tends to be the one somebody stopped before the
                 # end was written.
                 max(int(r["watched_seconds"] or 0) for r in group),
                 main["id"]),
            )
            for record in others:
                conn.execute("DELETE FROM playback WHERE id = ?", (record["id"],))
                deleted += 1

    log.info("merged %d duplicate groups from the import, deleted %d rows",
             len(groups), deleted)
    return {"status": "ok", "groups": len(groups), "removed": deleted}


def import_duplicate_count() -> int:
    """How many rows the merge across sources would delete."""
    return sum(len(group) - 1 for group in import_duplicate_groups())


def misplaced_episode_rows() -> list[dict[str, Any]]:
    """Records that hang on another episode than the one the viewer saw.

    The remedy after a bug in merging by tmdb: it took the SERIES' id for an episode (all episodes have the
    same one), so every scan merged the history of all episodes into one. In the statistics it looked as if
    the viewer saw one episode twenty times.

    It can be fixed because the record kept **the episode's name** as it was played. When it fits another
    episode of the same series than the one the record hangs on, it belongs there - and only when there is
    exactly one such episode. An ambiguous match we rather leave alone: wrongly assigned history is worse
    than one we know is shifted.
    """
    rows = db.query_all(
        """
        SELECT p.id, p.item_id, p.item_name,
               i.series_id  AS series
          FROM playback p
          JOIN items i ON i.id = p.item_id
         WHERE i.series_id IS NOT NULL
           AND p.item_name IS NOT NULL AND p.item_name != ''
           AND p.item_name != i.name
        """
    )
    if not rows:
        return []

    # Episodes by series and name. A name may exceptionally fit several episodes in one series ("Cast 1");
    # we skip such groups.
    by_name: dict[tuple[str, str], list[str]] = {}
    for episode in db.query_all(
        "SELECT id, name, series_id FROM items WHERE series_id IS NOT NULL"
    ):
        key = (str(episode["series_id"]), str(episode["name"]).strip().lower())
        by_name.setdefault(key, []).append(str(episode["id"]))

    repairs = []
    for row in rows:
        key = (str(row["series"]), str(row["item_name"]).strip().lower())
        options = by_name.get(key, [])
        if len(options) != 1 or options[0] == str(row["item_id"]):
            continue
        repairs.append({**row, "right_episode": options[0]})
    return repairs
