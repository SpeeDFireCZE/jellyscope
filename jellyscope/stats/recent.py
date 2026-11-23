"""Recently added titles and batches of episodes.

Part of the statistics package (see jellyscope/stats/__init__.py)."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .. import db


# The group's key as in the library list: the series, the movie's main copy (scanner/copies.py), or the movie itself.
_KEY = "COALESCE(i.series_id, i.main_id, i.id)"

# The newest groups (a series, or a movie on its own) - the first step of recently_added(). One text for both queries
# that need it.
_NEWEST_GROUPS = f"""
        SELECT {_KEY} AS key,
               MAX(i.date_created)         AS added
          FROM items i
         WHERE i.is_missing = 0
           AND i.date_created IS NOT NULL
      GROUP BY {_KEY}
      -- On the same date the key decides, as in the outer query
      -- (i.id) - otherwise the limit would pick other titles than the whole list.
      ORDER BY MAX(i.date_created) DESC, {_KEY}
         LIMIT ?
"""


def recently_added(limit: int = 18) -> list[dict[str, Any]]:
    """The titles added last.

    A series is to show once, not ten times for every episode.

    It is picked in two steps, and for a good reason. A fixed count of the newest ROWS (limit * 6) used to be
    taken and only those were grouped. But when a series of two hundred episodes is created anew in Jellyfin, all
    the episodes get today's date - and they fill those hundred and eight rows right away. Only one series was
    then left in the list and everything else vanished, never to come back.

    So the newest GROUPS (a series or a movie) are picked first and only then their rows are pulled in. How many
    episodes a series has stops mattering that way.
    """
    group_rows = db.query_all(_NEWEST_GROUPS, (limit,))
    if not group_rows:
        return []

    # Older than this can belong to none of the batches we will list - see _in_same_batch(). Without this limit a
    # series of five hundred episodes would pull in all of them.
    oldest = min(str(group["added"]) for group in group_rows)
    lower_bound = _hours_back(oldest, BATCH_HOURS)

    # The groups go into the second query as a subquery, not as a list of keys `IN (?, ?, ...)`: the API with a
    # filter asks for the whole library and SQLite takes at most 32 766 parameters - above that many movies the
    # query failed.
    rows = db.query_all(
        f"""
        SELECT i.id, i.name, i.type, i.series_id, i.series_name,
               i.production_year, i.date_created, i.height, i.size_bytes,
               i.library_id, i.parent_index_number, i.index_number,
               i.image_tag, i.series_image_tag, i.path, i.tmdb_id, i.main_id,
               l.name AS library_name
        FROM items i
        LEFT JOIN libraries l ON l.id = i.library_id
        WHERE i.is_missing = 0
          AND i.date_created IS NOT NULL
          AND i.date_created >= ?
          AND {_KEY} IN (
              SELECT key FROM ({_NEWEST_GROUPS}) newest)
        -- A tie between groups is decided by the key of the group as in the inner
        -- query - otherwise a series and a movie added at once would come
        -- in another order with the limit than without it.
        ORDER BY i.date_created DESC, {_KEY}, i.id
        """,
        (lower_bound, limit),
    )

    order: list[str] = []
    groups: dict[str, list[dict[str, Any]]] = {}

    for row in rows:
        # The group's key: the series, a movie with copies (the main copy), or the movie on its own.
        key = str(row["series_id"] or row["main_id"] or row["id"])
        if key not in groups:
            if len(groups) >= limit:
                continue
            order.append(key)
            groups[key] = []
        groups[key].append(row)

    result: list[dict[str, Any]] = []
    for key in order:
        episodes = groups[key]
        newest = episodes[0]
        is_episode = bool(newest["series_id"])

        # How many episodes came "in one batch". A whole season does not arrive within one second, but it does not
        # spread over a week either - a window of one day from the newest episode is an honest guess. Without it
        # the list would show episodes added a month ago too, only because no other was added to them for long.
        batch = [d for d in episodes if _in_same_batch(newest["date_created"],
                                                       d["date_created"])]
        batch = _sort_episodes(batch)

        result.append({
            **newest,
            "title": newest["series_name"] if is_episode else newest["name"],
            # The link leads to a specific item - for a series to the newest episode, which is exactly what interests
            # the user.
            "poster_id": newest["series_id"] or newest["id"],
            # The poster's tag. Without it the browser keeps the old version of the picture even after we delete it
            # from the cache - the address stays the same.
            "poster_tag": (newest["series_image_tag"] if newest["series_id"]
                           else newest["image_tag"]),
            "is_episode": is_episode,
            # The batch's other episodes. When only one came, the list is empty and the card behaves as before - a
            # link straight to it.
            "episodes": batch if is_episode and len(batch) > 1 else [],
            "series_url": f"/series/{newest['series_id']}" if is_episode else None,
        })

    return result


def _sort_episodes(batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort the batch from the first episode to the last and drop duplicates.

    Sorting: the list reads as a list of episodes, and that reads top to bottom - S01E01 on top. By the date added
    it does not fit; Jellyfin reads the files in the order it finds them on the disk, so the batch comes mixed.

    Duplicates: the same episode showed twice in the list. A library where an episode is in two files (a different
    quality, an old version left over) is to blame - in Jellyfin those are two items with their own ItemId and both
    really belong to the batch. In the list of news such a row twice is only confusing, though, so we keep the
    one that already has measured technical data (and on a tie the newer of them).
    """
    def episode_order(d: dict[str, Any]) -> tuple[Any, ...]:
        # None to the end: an episode without a number is not to squeeze in before S01E01.
        season, number = d["parent_index_number"], d["index_number"]
        return (season is None, season or 0, number is None, number or 0,
                str(d["name"] or ""))

    best: dict[tuple[Any, Any, Any], dict[str, Any]] = {}
    for d in batch:
        key = (d["series_id"], d["parent_index_number"], d["index_number"])
        if key[1] is None and key[2] is None:
            # An episode without numbers must merge with nothing - without a number there is no telling whether it is
            # the same episode or another one.
            key = (d["id"], None, None)
        current = best.get(key)
        if current is None or _episode_preference(d) > _episode_preference(current):
            best[key] = d

    return sorted(best.values(), key=episode_order)


def _episode_preference(d: dict[str, Any]) -> tuple[int, str]:
    """Which of two items of the same episode is to show."""
    has_data = 1 if (d.get("height") or d.get("size_bytes")) else 0
    return (has_data, str(d["date_created"] or ""))


# How far apart episodes may be to count as one batch.
BATCH_HOURS = 24


def _hours_back(moment_text: Any, hours: int) -> str:
    """A time shifted the given number of hours back, in the database's shape.

    It serves as a query's lower bound. When the time cannot be read, an empty string is returned - it compares
    smaller than anything, so rather nothing is filtered out.
    """
    # OverflowError: Jellyfin can send DateCreated 0001-01-01 (MinValue) and a day earlier there is no date.
    try:
        moment = datetime.strptime(str(moment_text).replace("T", " ")[:19], db.TIME_FORMAT)
        return (moment - timedelta(hours=hours)).strftime(db.TIME_FORMAT)
    except (TypeError, ValueError, OverflowError):
        return ""


def _in_same_batch(newest: Any, examined: Any) -> bool:
    """Did this episode come together with the newest one?"""
    try:
        a = datetime.strptime(str(newest).replace("T", " ")[:19], db.TIME_FORMAT)
        b = datetime.strptime(str(examined).replace("T", " ")[:19], db.TIME_FORMAT)
    except (TypeError, ValueError):
        return False
    return abs((a - b).total_seconds()) <= BATCH_HOURS * 3600
