"""Records without an item: counts, stale names, looking up in Jellyfin, analysis and manual assignment.

Part of the package importers (see jellyscope/importers/__init__.py)."""
from __future__ import annotations

import logging
from typing import Any

from .. import db, i18n
from ..jellyfin import JellyfinClient, JellyfinError
from .base import NOT_LISTENING_OR_READING, _normalize_id, split_episode_title
from .creation import create_from_jellyfin
from .duplicates import misplaced_episode_rows
from .linking import _link_by_episode_number, _link_by_name

log = logging.getLogger("jellyscope.importers")


def orphan_playback_count() -> int:
    """How many history records point at an item that is not in the library.

    Such a record has nothing to file under on the overviews: it knows no series, no library, and a link
    from it leads nowhere. The statistics then show the episode "Zvony" as a title of its own beside "Hry o
    truny", instead of merging under it.
    """
    return db.query_value(
        f"""
        SELECT COUNT(*)
          FROM playback p
     LEFT JOIN items i ON i.id = p.item_id
         WHERE p.item_id IS NOT NULL AND p.item_id != ''
           AND i.id IS NULL
           AND p.item_name IS NOT NULL AND p.item_name != ''
           {NOT_LISTENING_OR_READING}
        """,
        default=0,
    ) or 0


def stale_name_rows() -> int:
    """How many history records carry another name than the item in the library has.

    A play's name is stored **together with the record** (denormalised), so that the history of a deleted
    title does not stay nameless. That has a flip side, though: when the title is **renamed** later, the old
    record carries the original name forever.

    The commonest case is not a renaming on a whim but a fix of wrongly identified metadata: Jellyfin
    identifies the file as another movie, you watch it in that state, then fix the metadata in Jellyfin -
    and in Jellyscope that play keeps a foreign name. In the statistics it looks as if the name of another
    title belonged to one id, and the link leads "somewhere else" than the label says.
    """
    return db.query_value(
        """
        SELECT COUNT(*)
          FROM playback p
          JOIN items i ON i.id = p.item_id
         WHERE p.item_name IS NOT NULL AND p.item_name != ''
           AND (p.item_name != i.name
                OR COALESCE(p.series_name, '') != COALESCE(i.series_name, ''))
        """,
        default=0,
    ) or 0


def unify_names() -> dict[str, Any]:
    """Rewrite the names in the history by the library.

    **Only records whose item exists in the library** change. With orphans the name stays as it is - it is
    the only information about what was played, and without it an empty space would be left.

    Why item by item and not with one UPDATE ... FROM: SQLite knows that construction only from version 3.33
    and PostgreSQL writes it differently. The titles that until not fit are a handful, so it does not matter.
    """
    candidates = db.query_all(
        """
        SELECT p.item_id, i.name AS right_item, i.series_name AS right_series,
               COUNT(*) AS row_count
          FROM playback p
          JOIN items i ON i.id = p.item_id
         WHERE p.item_name IS NOT NULL AND p.item_name != ''
           AND (p.item_name != i.name
                OR COALESCE(p.series_name, '') != COALESCE(i.series_name, ''))
      GROUP BY p.item_id, i.name, i.series_name
        """
    )
    if not candidates:
        return {"items": 0, "rows": 0}

    rows = 0
    with db.connect() as conn:
        for row in candidates:
            cursor = conn.execute(
                "UPDATE playback SET item_name = ?, series_name = ?"
                " WHERE item_id = ?",
                (row["right_item"], row["right_series"], row["item_id"]),
            )
            rows += cursor.rowcount or 0

    log.info("aligned names in history: %s items, %s rows",
             len(candidates), rows)
    return {"items": len(candidates), "rows": rows}


def orphan_items_count() -> int:
    """How many DIFFERENT titles are orphans - not how many history rows.

    The rows tend to be several times more (one plays one episode ten times), so the number of rows alone
    looks worse than the reality is.
    """
    return int(db.query_value(
        f"""
        SELECT COUNT(*) FROM (
            SELECT p.item_id
              FROM playback p
         LEFT JOIN items i ON i.id = p.item_id
             WHERE p.item_id IS NOT NULL AND p.item_id != '' AND i.id IS NULL
               AND p.item_name IS NOT NULL AND p.item_name != ''
               {NOT_LISTENING_OR_READING}
          GROUP BY p.item_id
        ) AS t
        """,
        default=0,
    ) or 0)


# How many orphan ids Jellyfin is asked about at once. There are rarely more than a thousand titles of orphan
# history and one request handles fifty, so it is a cap for safety, not for speed.
LOOKUP_LIMIT = 1000


async def look_up_orphans_in_jellyfin() -> dict[str, Any]:
    """Try to link orphan records with the help of Jellyfin itself.

    Why it works: the identifier in imported history **is the real Jellyfin ItemId**. The import knows it,
    only nothing leads to it here - typically because Jellystat carries only the episode's name ("7.
    epizoda", "Pilot") and says nothing about the series. Every series has such a name, so pairing by name
    refuses to file it - and rightly so.

    Jellyfin knows that id, though. It is enough to ask (GET /Items?Ids=...) and out come the series, the
    season and the episode's number - exactly what was missing for an unambiguous filing.

    Two things are done:
      1. **the series and the name are filled in** in the history record. That alone stops the episode
         being a "movie" of its own on the overviews, and it merges under its series,
      2. when the same episode is found in the library (by tmdb + season + episode, or by the series' id and
         the numbers), the record is **linked** to it. That gives it a link too.

    Jellyfin is only read. When the title is no longer there, the record stays as it was - it is valid
    history, only nothing leads to it.
    """
    candidates = db.query_all(
        f"""
        SELECT p.item_id, MAX(p.item_name) AS item_name
          FROM playback p
     LEFT JOIN items i ON i.id = p.item_id
         WHERE p.item_id IS NOT NULL AND p.item_id != '' AND i.id IS NULL
           {NOT_LISTENING_OR_READING}
      GROUP BY p.item_id
         LIMIT ?
        """,
        (LOOKUP_LIMIT,),
    )
    if not candidates:
        return {"status": "ok", "queried": 0, "found": 0,
                "linked": 0, "created_count": 0, "filled": 0, "row_count": 0}

    ids = [str(k["item_id"]) for k in candidates]
    try:
        async with JellyfinClient(*db.jellyfin_connection()) as client:
            found = await client.items_by_ids(ids)
    except JellyfinError as exc:
        return {"status": "error", "message": i18n.error_text(exc)}

    result = create_from_jellyfin(found)
    log.info("looked up in Jellyfin: %s of %s asked, linked %s, "
             "created %s, series filled in for %s",
             len(found), len(ids), result["linked"],
             result["created_count"], result["filled"])
    return {"status": "ok", "queried": len(ids), "found": len(found),
            **result}


# The reasons why a record could not be filed. The order goes from "something can be done about it" to
# "really nothing any more" - they are listed in that order too.
REASON_ID = "id"
REASON_ONE_MATCH = "one_match"
REASON_SEVERAL_MATCHES = "several_matches"
REASON_SERIES_EXISTS = "series_exists"
REASON_NO_SERIES = "no_series"
REASON_KNOWS_SERIES = "knows_series"
REASON_NOTHING = "nothing"

# What each reason means and whether something can be done about it. The texts are here, not in the
# template: they belong to the division, not to its drawing.
REASON_LABELS: dict[str, dict[str, Any]] = {
    REASON_ID: {
        "heading": "The identifier matches, just written differently",
        "description": "The title is in the library, only its id is written differently (with or without "
                 "dashes). Clean up history fixes it.",
        "solvable": True,
    },
    REASON_ONE_MATCH: {
        "heading": "The name matches exactly one title",
        "description": "This should have linked itself. If it survives a "
                 "cleanup, it is a bug worth reporting.",
        "solvable": True,
    },
    REASON_SEVERAL_MATCHES: {
        "heading": "The name matches several titles",
        "description": 'Typically "Episode 7" or "Pilot": every series has one. The machine must not '
                 "guess, but you can tell where it belongs. Try the Jellyfin lookup, or assign "
                 "it manually.",
        "solvable": True,
    },
    REASON_SERIES_EXISTS: {
        "heading": "The series is in the library, the episode is not",
        "description": "The name carries the series and an episode number, but no such episode is in the library "
                 "because the numbering differs between sources. Manual assignment solves it.",
        "solvable": True,
    },
    REASON_KNOWS_SERIES: {
        "heading": "We know the series, not the episode",
        "description": "The record knows its series, but no episode of that name "
                 "is in the library.",
        "solvable": True,
    },
    REASON_NO_SERIES: {
        "heading": "The series is not in the library",
        "description": "You probably deleted the series. The history stays valid, there is "
                 "just nothing behind it.",
        "solvable": False,
    },
    REASON_NOTHING: {
        "heading": "Nothing to go on",
        "description": "Only a name is left and it appears nowhere in the "
                 "library. The title is gone.",
        "solvable": False,
    },
}


# How many orphan titles are analysed at once. More is no longer a list but a dump - and the page would become
# unreadable.
ANALYSIS_LIMIT = 500


def analyse_orphans(limit: int = ANALYSIS_LIMIT) -> list[dict[str, Any]]:
    """Orphan records together with the reason why they could not be filed.

    Computed anew every time, stored nowhere. A stored list would show the state after the last clean-up -
    i.e. something that need not hold any more, since a synchronization or another import ran meanwhile.
    Rather a few queries more and the certainty that one looks at the truth.

    Returns one row per title (not per history record): one plays one episode ten times, and in a list where
    one is to recognise something by name it would be the same row ten times.
    """
    orphans = db.query_all(
        f"""
        SELECT p.item_id,
               MAX(p.item_name)   AS item_name,
               MAX(p.series_name) AS series_name,
               MAX(p.item_type)   AS item_type,
               COUNT(*)           AS row_count,
               MIN(p.started_at)  AS since,
               MAX(p.started_at)  AS until
          FROM playback p
     LEFT JOIN items i ON i.id = p.item_id
         WHERE p.item_id IS NOT NULL AND p.item_id != '' AND i.id IS NULL
           AND p.item_name IS NOT NULL AND p.item_name != ''
           {NOT_LISTENING_OR_READING}
      GROUP BY p.item_id
      ORDER BY COUNT(*) DESC
         LIMIT ?
        """,
        (limit,),
    )
    if not orphans:
        return []

    # We load the library into memory once. A query for every title separately would mean thousands of
    # queries with a few hundred orphans.
    by_name: dict[str, list[dict[str, Any]]] = {}
    by_id: set[str] = set()
    series: set[str] = set()
    for item in db.query_all(
            "SELECT id, name, series_name FROM items WHERE name IS NOT NULL"):
        by_name.setdefault(str(item["name"]).strip().lower(), []).append(item)
        by_id.add(_normalize_id(item["id"]))
        if item["series_name"]:
            series.add(str(item["series_name"]).strip().lower())

    result = []
    for row in orphans:
        name = str(row["item_name"] or "").strip()
        matches = by_name.get(name.lower(), [])
        parsed = split_episode_title(name)
        series_from_record = str(row["series_name"] or "").strip()

        if _normalize_id(row["item_id"]) in by_id:
            reason = REASON_ID
        elif len(matches) == 1:
            reason = REASON_ONE_MATCH
        elif len(matches) > 1:
            reason = REASON_SEVERAL_MATCHES
        elif parsed and parsed["series"].lower() in series:
            reason = REASON_SERIES_EXISTS
        elif parsed:
            reason = REASON_NO_SERIES
        elif series_from_record and series_from_record.lower() in series:
            reason = REASON_KNOWS_SERIES
        else:
            reason = REASON_NOTHING

        result.append({**row, "reason": reason, "match_count": len(matches),
                       "series_from_name": parsed["series"] if parsed else None})
    return result


def candidates_for_orphan(search: str, limit: int = 25) -> list[dict[str, Any]]:
    """Library items one can choose from when assigning by hand."""
    search = (search or "").strip()
    if len(search) < 2:
        return []
    pattern = db.like_contains(search)
    return db.query_all(
        f"""
        SELECT i.id, i.name, i.series_name, i.type, i.parent_index_number,
               i.index_number, i.production_year, i.is_missing,
               l.name AS library_name
          FROM items i
     LEFT JOIN libraries l ON l.id = i.library_id
         WHERE i.name LIKE ? {db.LIKE_ESCAPE} OR i.series_name LIKE ? {db.LIKE_ESCAPE}
      ORDER BY i.is_missing, i.series_name, i.parent_index_number,
               i.index_number, i.name
         LIMIT ?
        """,
        (pattern, pattern, limit),
    )


def assign_manually(old_id: str, target_id: str) -> dict[str, Any]:
    """Rewrite orphan records by hand to the chosen library item.

    The last rescue for the case where the machine cannot tell but a person can - typically an episode
    called "7. epizoda" when the library has fifteen of them.
    """
    target = db.query_one("SELECT id, name, series_name FROM items WHERE id = ?",
                          (target_id,))
    if target is None:
        return {"status": "error", "message": "There is no such item in the library."}

    with db.connect() as conn:
        cursor = conn.execute(
            """UPDATE playback SET item_id = ?, item_name = ?, series_name = ?
                WHERE item_id = ?""",
            (target["id"], target["name"], target["series_name"], old_id),
        )
    rows = cursor.rowcount or 0
    if not rows:
        return {"status": "error", "message": "Those records are no longer anywhere."}

    log.info("manually assigned: %s -> %s (%s rows)", old_id, target["id"], rows)
    return {"status": "ok", "rows": rows, "name": target["name"]}


def relink_orphans() -> dict[str, Any]:
    """Try to link orphan history records to items by name.

    Uses the same pairing as the import (`_link_by_name`) - an unambiguous match of names, otherwise nothing.
    Here it is useful outside the import too, though: records also become orphans when a file is deleted in
    Jellyfin and added again with a new ItemId, or when the history was taken over before the first library
    synchronization ran.
    """
    # By the episode's number first - that is unambiguous. Only then by the bare name, where one can only hope
    # it is unique.
    items, rows = _link_by_episode_number()
    items_by_name, rows_by_name = _link_by_name()
    items += items_by_name
    rows += rows_by_name

    if items:
        log.info("linked %d history items (%d rows)", items, rows)
    return {"status": "ok", "items": items, "rows": rows}


def repair_episode_links() -> dict[str, Any]:
    """Return merged episode history back to the episodes it belongs to."""
    repairs = misplaced_episode_rows()
    if not repairs:
        return {"status": "ok", "moved": 0}

    with db.connect() as conn:
        for repair in repairs:
            conn.execute("UPDATE playback SET item_id = ? WHERE id = ?",
                         (repair["right_episode"], repair["id"]))

    log.info("moved %d records back to the right episodes", len(repairs))
    return {"status": "ok", "moved": len(repairs)}
