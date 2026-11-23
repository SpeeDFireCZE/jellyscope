"""Cleaning duplicates in our own history and capping nonsense lengths.

Part of the package importers (see jellyscope/importers/__init__.py)."""
from __future__ import annotations

import logging
from typing import Any

from .. import db
from .base import _normalize_id
from .matching import _epoch

log = logging.getLogger("jellyscope.importers")


# ---------------------------------------------------------------------------
# Cleaning duplicates in our own history
# ---------------------------------------------------------------------------
#
# This package deals with duplicates already at the import (see `_already_there`). They can come to be
# without an import too, though: when **two collectors run at once** against one database (typically by
# accident - an old and a new version of the app with the same settings), each creates its own row for the
# same movie.
#
# The collector prevents it (see collector._take_over_session) - but that holds only from now on. The
# records already lying in the database need a one-off clean-up, and this function does it.

# How many times the show's length we still take as watching.
#
# A player running for twice an episode's length is still imaginable (one goes back and plays a scene
# again), thirty times no more - that is a TV nobody switched off. Half extra is a compromise: it leaves
# room for rewinding and still catches exactly those overnight.
CAP_MULTIPLE = 1.5


def cap_nonsense_durations() -> dict[str, int]:
    """Imported history where "watching" lasts multiples of the show's length.

    Playback Reporting and Jellystat measure the time **on the clock**: from the start to the end of the
    session. When one falls asleep at an episode and the player stays open till the morning, the source
    reports fifteen hours - and Jellyscope took it over as it came. In the statistics one episode then beat
    a whole month.

    It can be told only by the show's length: when the watched time is a multiple of the title's running
    time, nobody watched - only the player ran.

    **Nothing is thrown away.** The surplus is poured into `paused_seconds`, so the sum of both still
    matches the span from the session's start to its end; only what counts as watching changes. The start
    and end times stay exactly as they were - when it is needed to go back to it, there is something to go
    back to.

    It concerns **only imported history** (session_key starts with "import:"). What the collector
    collected has its increment limited already when measured - see collector.poll_once() and its
    max_gap_seconds.
    """
    candidates = db.query_all(
        """
        SELECT p.id, p.watched_seconds, p.paused_seconds, i.runtime_ticks
          FROM playback p
          JOIN items i ON i.id = p.item_id
         WHERE p.session_key LIKE 'import:%'
           AND i.runtime_ticks IS NOT NULL
           AND i.runtime_ticks > 0
           AND p.watched_seconds > 0
        """
    )

    changed: list[tuple[int, int, int]] = []
    for row in candidates:
        length = float(row["runtime_ticks"]) / 10_000_000
        cap = int(length * CAP_MULTIPLE)
        watched = int(row["watched_seconds"] or 0)
        if cap <= 0 or watched <= cap:
            continue
        surplus = watched - cap
        changed.append((cap, int(row["paused_seconds"] or 0) + surplus,
                        int(row["id"])))

    if not changed:
        return {"rows": 0, "seconds": 0}

    with db.connect() as conn:
        for cap, paused, row_id in changed:
            conn.execute(
                "UPDATE playback SET watched_seconds = ?, paused_seconds = ? "
                " WHERE id = ?",
                (cap, paused, row_id))

    moved = sum(paused for _, paused, _ in changed)
    log.info("shortened %s imported records that ran longer than the title",
             len(changed))
    return {"rows": len(changed), "seconds": moved}


def duplicate_playback_groups() -> list[list[dict[str, Any]]]:
    """Find groups of records that describe the same watching experience.

    ## When two records are the same experience

    **Everything** must hold at once:

      1. **the same user** and **the same item**,
      2. their time spans **overlap**,
      3. **they do not contradict each other by device** - when both know `device_id` and it differs, they
         are two different plays,
      4. neither comes from an import.

    On point 2: an overlap, not the same start. Two collectors each write a second elsewhere, so an exact
    match would let most duplicates through. Consecutive spans (end == start) are no overlap - who finished
    a movie and played it again has two records and keeps two. And a movie started last night and finished
    today does not overlap at all.

    On point 3: one person may play the same movie on two devices at once - on the TV and on the phone.
    Without this condition such two plays would merge into one, since they meet conditions 1 and 2. The
    device is the only thing that tells them apart; two collectors over one session, on the contrary,
    report the same device.

    We leave imported records out - they have their own protection by the key already (see
    `_already_there`) and we do not want to mix them with our own collection.
    """
    rows = db.query_all(
        """
        SELECT id, user_id, item_id, session_key, device_id, started_at,
               ended_at, watched_seconds, paused_seconds, is_active
          FROM playback
         WHERE user_id IS NOT NULL AND user_id != ''
           AND item_id IS NOT NULL AND item_id != ''
           AND session_key NOT LIKE 'import:%'
         ORDER BY started_at, id
        """
    )

    by_item: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        start = _epoch(row["started_at"])
        if start is None:
            continue
        end = _epoch(row["ended_at"])
        if end is None or end <= start:
            end = start + max(int(row["watched_seconds"] or 0), 1)
        row["_since"], row["_until"] = start, end
        key = (_normalize_id(row["user_id"]), _normalize_id(row["item_id"]))
        by_item.setdefault(key, []).append(row)

    def same_device(a: dict[str, Any], b: dict[str, Any]) -> bool:
        """Do they not contradict each other by device? An unknown device contradicts nobody."""
        first = str(a.get("device_id") or "").strip()
        second = str(b.get("device_id") or "").strip()
        if not first or not second:
            return True
        return first == second

    groups: list[list[dict[str, Any]]] = []
    for records in by_item.values():
        if len(records) < 2:
            continue
        records.sort(key=lambda r: r["_since"])

        # The classic merging of intervals: we go from left to right and what overlaps with the open group
        # belongs to it. A record from another device does not belong to the group, even if it overlaps in
        # time.
        group = [records[0]]
        end = records[0]["_until"]
        for record in records[1:]:
            if (record["_since"] < end
                    and all(same_device(record, member) for member in group)):
                group.append(record)
                end = max(end, record["_until"])
            else:
                if len(group) > 1:
                    groups.append(group)
                group = [record]
                end = record["_until"]
        if len(group) > 1:
            groups.append(group)

    return groups


def merge_duplicate_playback() -> dict[str, Any]:
    """Merge duplicate records in the history. Returns what happened.

    One row of the group stays: the one with the longest watched time - i.e. the more complete one. It
    takes over the group's earliest start and latest end, so the span is not lost. The others are deleted.

    **Not added up.** Two records of the same play are not two plays; a sum would make a viewer who saw the
    movie twice in a row.
    """
    groups = duplicate_playback_groups()
    if not groups:
        return {"status": "ok", "groups": 0, "removed": 0}

    deleted = 0
    with db.connect() as conn:
        for group in groups:
            # We keep the most complete record; on a tie the older one (a lower id), so that the result is
            # the same on every run.
            group.sort(key=lambda r: (-int(r["watched_seconds"] or 0), int(r["id"])))
            main, others = group[0], group[1:]

            starts = [r["started_at"] for r in group if r["started_at"]]
            ends = [r["ended_at"] for r in group if r["ended_at"]]
            conn.execute(
                """UPDATE playback
                      SET started_at = ?,
                          ended_at = ?,
                          paused_seconds = ?,
                          is_active = ?
                    WHERE id = ?""",
                (min(starts) if starts else main["started_at"],
                 max(ends) if ends else main["ended_at"],
                 max(int(r["paused_seconds"] or 0) for r in group),
                 # When any of the records was still running, the merged one runs too - otherwise the movie
                 # playing right now would disappear from "Prave se hraje".
                 1 if any(int(r["is_active"] or 0) for r in group) else 0,
                 main["id"]),
            )
            for record in others:
                conn.execute("DELETE FROM playback WHERE id = ?", (record["id"],))
                deleted += 1

    log.info("merged %d duplicate groups in history, deleted %d rows",
             len(groups), deleted)
    return {"status": "ok", "groups": len(groups), "removed": deleted}


def duplicate_playback_count() -> int:
    """How many rows the clean-up would delete - so that one can tell beforehand whether it makes sense."""
    return sum(len(group) - 1 for group in duplicate_playback_groups())
