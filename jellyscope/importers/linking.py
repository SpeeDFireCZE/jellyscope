"""Assigning imported history to items: id, TMDB, name, episode number.

Part of the package importers (see jellyscope/importers/__init__.py)."""
from __future__ import annotations

import logging
from typing import Any

from .. import db, scanner
from ..jellyfin import JellyfinClient, JellyfinError
from .base import NOT_LISTENING_OR_READING, _normalize_id, split_episode_title
from .creation import create_from_jellyfin

log = logging.getLogger("jellyscope.importers")


# ---------------------------------------------------------------------------
# Looking up items for imported history
# ---------------------------------------------------------------------------
#
# Neither Playback Reporting nor Jellystat sends a tmdb ID. They send Jellyfin's ItemId - but that changes
# every time you transcode or move a file. Imported history therefore often points at items that are no
# longer in the library under that name.
#
# Without a look-up such records would hang in the air: they would be seen in the history, but nothing in
# the library would lead to them and they would not count in the statistics by library or codec.
#
# We look up in two steps, the most reliable first:
#
#   1. **By tmdb ID.** We ask Jellyfin about the old ItemId. When it still knows it, we get ProviderIds and
#      tmdb in them. Then we only find the item with the same tmdb in our library.
#
#   2. **By name.** When Jellyfin does not know the id (the file is gone completely), we try a match of
#      names. We pair only when the result is **unambiguous** - with two equally named titles we rather join
#      nothing than wrongly.


def _unify_ids() -> dict[str, int]:
    """Rewrite identifiers from the import to the shape the library uses.

    We do it with a one-off rewrite in the history, not with a smarter comparison in every query. The
    reason: this way it is unified once and **all** other queries in the app can stay plain. If we compared
    "up to the dashes" everywhere instead, we would have to think of it in every new query - and sooner or
    later it would be forgotten.

    Only records that **would not pair otherwise** change. When an identifier matches the table
    `users`/`items` exactly, we do not touch it.
    """
    changes = {"user_id": 0, "item_id": 0}

    for table, column in (("users", "user_id"), ("items", "item_id")):
        with db.connect() as conn:
            correct: dict[str, str] = {}
            disputed: set[str] = set()
            for row in conn.execute(f"SELECT id FROM {table}").fetchall():
                key = _normalize_id(row["id"])
                if not key:
                    continue
                if key in correct and correct[key] != str(row["id"]):
                    # Two different records with the same normalised shape. It should not happen - and when it
                    # does, we rather do not guess.
                    disputed.add(key)
                correct[key] = str(row["id"])

            if not correct:
                continue

            unpaired = conn.execute(
                f"""
                SELECT DISTINCT p.{column} AS value
                  FROM playback p
             LEFT JOIN {table} t ON t.id = p.{column}
                 WHERE p.{column} IS NOT NULL AND p.{column} != '' AND t.id IS NULL
                """
            ).fetchall()

            for row in unpaired:
                old = str(row["value"])
                key = _normalize_id(old)
                new = correct.get(key)
                if not new or new == old or key in disputed:
                    continue
                cursor = conn.execute(
                    f"UPDATE playback SET {column} = ? WHERE {column} = ?",
                    (new, old),
                )
                changes[column] += cursor.rowcount or 0

    if any(changes.values()):
        log.info("import identifiers unified: %s", changes)
    return changes


def refresh_playback_metadata() -> dict[str, int]:
    """Fill in the history what was not known yet at the time of the import.

    The import can run before the app connects to Jellyfin - then we know neither the users' names nor the
    libraries yet, and "?" stays in the history. This function evens it out by the tables `users` and
    `items` once they fill. It runs after every import and after a library synchronization, so it is enough
    to connect Jellyfin and the old data straighten out by themselves.

    We rewrite only what is missing (or is "?"). Values filled in already are not touched: the history is to
    keep what was true at the moment of playing - say the name of a user who has renamed themselves since.
    """
    changes = {"user_names": 0, "item_types": 0, "libraries": 0, "series": 0}

    # First straighten the identifiers' shape, otherwise nothing below would pair.
    _unify_ids()

    with db.connect() as conn:
        cursor = conn.execute(
            """
            UPDATE playback
               SET user_name = (SELECT u.name FROM users u WHERE u.id = playback.user_id)
             WHERE (user_name IS NULL OR user_name = '' OR user_name = '?')
               AND user_id IS NOT NULL
               AND EXISTS (SELECT 1 FROM users u WHERE u.id = playback.user_id)
            """
        )
        changes["user_names"] = cursor.rowcount or 0

        # We always take the item's type from the library when we have it there - the import sources report
        # it unreliably and our table is authoritative.
        cursor = conn.execute(
            """
            UPDATE playback
               SET item_type = (SELECT i.type FROM items i WHERE i.id = playback.item_id)
             WHERE item_id IS NOT NULL
               AND EXISTS (SELECT 1 FROM items i WHERE i.id = playback.item_id
                             AND i.type IS NOT NULL
                             AND (playback.item_type IS NULL
                                  OR playback.item_type NOT IN ('Movie', 'Episode')
                                  OR playback.item_type != i.type))
            """
        )
        changes["item_types"] = cursor.rowcount or 0

        cursor = conn.execute(
            """
            UPDATE playback
               SET library_id = (SELECT i.library_id FROM items i WHERE i.id = playback.item_id)
             WHERE library_id IS NULL
               AND item_id IS NOT NULL
               AND EXISTS (SELECT 1 FROM items i WHERE i.id = playback.item_id
                             AND i.library_id IS NOT NULL)
            """
        )
        changes["libraries"] = cursor.rowcount or 0

        cursor = conn.execute(
            """
            UPDATE playback
               SET series_name = (SELECT i.series_name FROM items i WHERE i.id = playback.item_id)
             WHERE series_name IS NULL
               AND item_id IS NOT NULL
               AND EXISTS (SELECT 1 FROM items i WHERE i.id = playback.item_id
                             AND i.series_name IS NOT NULL)
            """
        )
        changes["series"] = cursor.rowcount or 0

    if any(changes.values()):
        log.info("history filled in: %s", changes)
    return changes


def _orphan_item_ids(limit: int = 2000) -> list[str]:
    """ItemIds from the history that nothing in the library belongs to."""
    rows = db.query_all(
        """
        SELECT DISTINCT p.item_id
          FROM playback p
     LEFT JOIN items i ON i.id = p.item_id
         WHERE p.item_id IS NOT NULL
           AND p.item_id != ''
           AND i.id IS NULL
         LIMIT ?
        """,
        (limit,),
    )
    return [str(row["item_id"]) for row in rows]


def _relink(old_id: str, new_id: str) -> int:
    """Rewrite history from an old ItemId to an item that is in the library."""
    with db.connect() as conn:
        cursor = conn.execute(
            "UPDATE playback SET item_id = ? WHERE item_id = ?", (new_id, old_id)
        )
        return cursor.rowcount or 0


def _link_by_tmdb(client_items: list[dict[str, Any]]) -> tuple[int, int]:
    """Pair by the item's identity. Returns (how many items, how many history rows).

    The key is `scanner.item_identity()`, not the bare tmdb_id. For an episode the tmdb_id is shared by
    the whole series - only one of twenty episodes would fit into the dict and the history of all the others
    would be rewritten to it. The same trap as with `scanner._merge_by_tmdb()`.
    """
    items = rows = 0

    with db.connect() as conn:
        by_identity = {
            (str(row["tmdb_id"]),
             int(row["parent_index_number"]) if row["parent_index_number"] is not None else -1,
             int(row["index_number"]) if row["index_number"] is not None else -1): str(row["id"])
            for row in conn.execute(
                "SELECT id, tmdb_id, parent_index_number, index_number"
                "  FROM items WHERE tmdb_id IS NOT NULL"
            ).fetchall()
        }

    for item in client_items:
        old_id = str(item.get("Id") or "")
        identity = scanner.item_identity(item)
        if not old_id or not identity:
            continue

        new_id = by_identity.get(identity)
        if not new_id or new_id == old_id:
            continue

        rows += _relink(old_id, new_id)
        items += 1
        log.info("import: %s -> %s (by identity %s)", old_id, new_id, identity)

    return items, rows


def _orphan_records() -> list[dict[str, Any]]:
    """History nothing in the library leads to - one row per title.

    The starting point of both fallback ways of linking (by name and by episode number). We take the name
    through MAX(): there are several records and the name may differ among them (the title was renamed over
    time), but one is enough to look for a match.
    """
    return db.query_all(
        f"""
        SELECT p.item_id,
               MAX(p.item_name)   AS item_name,
               MAX(p.series_name) AS series_name
          FROM playback p
     LEFT JOIN items i ON i.id = p.item_id
         WHERE p.item_id IS NOT NULL AND p.item_id != '' AND i.id IS NULL
           AND p.item_name IS NOT NULL AND p.item_name != ''
           {NOT_LISTENING_OR_READING}
      GROUP BY p.item_id
        """
    )


def _link_by_name() -> tuple[int, int]:
    """The fallback when Jellyfin no longer knows the old ItemId - a match of names.

    Pairs only unambiguous matches. When you have two titles of the same name in the library (a director's
    cut beside the theatrical version), we rather leave the record unpaired - wrongly assigned history is
    worse than none.
    """
    candidates = _orphan_records()

    items = rows = 0
    for row in candidates:
        if row["series_name"]:
            matches = db.query_all(
                "SELECT id FROM items WHERE name = ? AND series_name = ? LIMIT 2",
                (row["item_name"], row["series_name"]),
            )
        else:
            # Without a known series we search by name, whether the item belongs to some series or not.
            #
            # `AND series_name IS NULL` used to stand here and so cut off exactly the group this fallback
            # exists for: an orphan record of an EPISODE. It does not know the series (otherwise it would
            # have paired earlier), but every episode in the library has a series - so a match was never
            # found. The overview then kept the episode "Zvony" as a title of its own instead of filing it
            # under "Hra o truny".
            matches = db.query_all(
                "SELECT id FROM items WHERE name = ? LIMIT 2",
                (row["item_name"],),
            )

        if len(matches) != 1:
            continue  # nothing, or more than one option - we do not guess

        rows += _relink(str(row["item_id"]), str(matches[0]["id"]))
        items += 1

    return items, rows


async def link_imported_history() -> dict[str, Any]:
    """Look up items for imported history. Runs after every import.

    Never fails so as to bring the import down - when Jellyfin does not answer, the first step is skipped
    and it goes straight to the match by name.
    """
    # Before we look anything up, we straighten the identifiers' shape - otherwise the whole imported history
    # would pose as "unknown item".
    _unify_ids()

    orphans = _orphan_item_ids()
    if not orphans:
        # Nothing to look up - but filling in names and types always makes sense. (Say after the first library
        # synchronization, when the identifiers fit and only the users' names are missing.)
        refresh_playback_metadata()
        return {"orphans": 0, "by_tmdb": 0, "by_name": 0, "rows": 0}

    tmdb_items = tmdb_rows = 0
    try:
        async with JellyfinClient(*db.jellyfin_connection()) as client:
            found = await client.items_by_ids(orphans)
        tmdb_items, tmdb_rows = _link_by_tmdb(found)
        # What did not pair with an item stored already but Jellyfin knows, we create right away. Without that
        # the history stayed orphaned, although we knew everything about the title - we only wrote it down
        # nowhere.
        from_jellyfin = create_from_jellyfin(found)
        tmdb_items += from_jellyfin["linked"] + from_jellyfin["created_count"]
        tmdb_rows += from_jellyfin["row_count"]
    except (JellyfinError, Exception) as exc:  # noqa: BLE001
        # The import has run and the data are stored. When the look-up fails, it is a pity, not a reason to
        # report an error - it is tried again at the next import.
        log.warning("lookup by tmdb failed: %s", exc)

    # By the episode's number first - it is unambiguous. Jellystat and Playback Reporting both put "Serial -
    # s02e07 - Nazev" into the name, so this puts most of the imported series history in its place.
    number_items, number_rows = _link_by_episode_number()
    name_items, name_rows = _link_by_name()

    # Only now, when the history points at the right items, does it make sense to take the type, the library
    # and the series' name from them.
    refresh_playback_metadata()

    result = {
        "orphans": len(orphans),
        "by_tmdb": tmdb_items,
        "by_episode": number_items,
        "by_name": name_items,
        "rows": tmdb_rows + number_rows + name_rows,
    }
    if result["rows"]:
        log.info("import: matched %(by_tmdb)s by tmdb, %(by_episode)s by "
                 "episode number, %(by_name)s by name (%(rows)s rows)", result)
    return result


def _link_by_episode_number() -> tuple[int, int]:
    """Link orphan records by the series and the episode's number.

    This is the strongest trace we have with imported history - and it stayed unused for a long time.
    Pairing by name looked in the library for the whole string "Seal Team 6 - s02e07 - 7. epizoda", but the
    episode is called simply "7. epizoda" there. So a match was never found.

    Series + season + episode number, on the other hand, determine one particular episode. It is no guessing
    by name: every series has a "7. epizoda", but there is only one "seventh episode of the second season of
    Seal Team 6".

    When more such episodes come out (two copies of the same), rather do not touch - wrongly assigned
    history is worse than history we know is aside.
    """
    candidates = _orphan_records()

    items = rows = 0
    for candidate in candidates:
        parsed = split_episode_title(candidate["item_name"])
        if not parsed:
            continue

        matches = db.query_all(
            """
            SELECT id FROM items
             WHERE LOWER(series_name) = LOWER(?)
               AND parent_index_number = ?
               AND index_number = ?
             LIMIT 2
            """,
            (parsed["series"], parsed["season"], parsed["episode"]),
        )

        # When the episode's number does not fit, we also try the episode's name within the same series -
        # sometimes the numbering differs between sources.
        if len(matches) != 1 and parsed["name"]:
            matches = db.query_all(
                "SELECT id FROM items WHERE LOWER(series_name) = LOWER(?)"
                "   AND LOWER(name) = LOWER(?) LIMIT 2",
                (parsed["series"], parsed["name"]),
            )

        if len(matches) != 1:
            continue

        rows += _relink(str(candidate["item_id"]), str(matches[0]["id"]))
        items += 1
        log.info("history: %s -> %s (by %s S%02dE%02d)",
                 candidate["item_id"], matches[0]["id"], parsed["series"],
                 parsed["season"], parsed["episode"])

    return items, rows
