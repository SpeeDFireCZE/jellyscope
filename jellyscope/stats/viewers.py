"""Viewers: summaries by viewer, the viewer table, a viewer's detail.

Part of the statistics package (see jellyscope/stats/__init__.py)."""
from __future__ import annotations

from typing import Any

from .. import db
from ..i18n import translate as _t
from .kinds import watching_only
from .periods import _bounds, transcode_condition
from .titles import extra_copies

# A summary by viewer: one row per viewer, not per name. The history keeps the name from the time of the play
# (refresh_playback_metadata does not rewrite it on purpose), so grouping by name split a renamed viewer into two
# columns - and merged two different people of the same name. The current name is in `users`; rows without
# user_id (an import without UserId) split by name, otherwise NULL would merge them into one.
#
# The query must have `FROM playback p LEFT JOIN users u ON u.id = p.user_id`. The order `VIEWER_ORDER` has no NULL
# in the key: SQLite puts it on top, PostgreSQL at the bottom, and at the LIMIT boundary each would return other
# rows.
VIEWER_NAME = "COALESCE(MAX(u.name), MAX(p.user_name))"
VIEWER_GROUP = "p.user_id, CASE WHEN p.user_id IS NULL THEN p.user_name END"
VIEWER_ORDER = "p.user_id IS NULL, p.user_id"


def top_users(days: int, limit: int = 8) -> list[dict[str, Any]]:
    return db.query_all(
        f"""
        SELECT {VIEWER_NAME}                   AS label,
               p.user_id,
               SUM(p.watched_seconds) / 3600.0 AS hours,
               COUNT(*)                        AS plays
        FROM playback p
        LEFT JOIN users u ON u.id = p.user_id
        WHERE p.started_at >= ? AND p.started_at < ?
          AND p.watched_seconds > 0
          AND p.user_name IS NOT NULL
          {watching_only("p.")}
        GROUP BY {VIEWER_GROUP}
        ORDER BY hours DESC, {VIEWER_ORDER}, label
        LIMIT ?
        """,
        (*_bounds(days), limit),
    )


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

def user_table(days: int) -> list[dict[str, Any]]:
    """Viewers with hours, plays and the count of different titles (a movie's copy is one title, see extra_copies)."""
    rows = _user_table(days)
    extra = extra_copies(_bounds(days), "p.watched_seconds > 0", group_by="p.user_id")
    for row in rows:
        row["item_count"] = (row.get("item_count") or 0) - extra.get(row["id"], 0)
    return rows


def _user_table(days: int) -> list[dict[str, Any]]:
    return db.query_all(
        f"""
        SELECT u.id,
               u.name,
               u.is_administrator,
               COALESCE(SUM(p.watched_seconds), 0) / 3600.0 AS hours,
               COUNT(p.id)                                  AS plays,
               COUNT(DISTINCT p.item_id)                    AS item_count,
               MAX(p.started_at)                            AS last_seen,
               COALESCE(SUM(CASE WHEN {transcode_condition('p.play_method')}
                                 THEN p.watched_seconds ELSE 0 END), 0) / 3600.0
                                                            AS transcoded_hours
        FROM users u
        LEFT JOIN playback p
               ON p.user_id = u.id
              AND p.started_at >= ? AND p.started_at < ?
              AND p.watched_seconds > 0
              {watching_only("p.")}
        GROUP BY u.id, u.name, u.is_administrator
        ORDER BY hours DESC, u.name
        """,
        (*_bounds(days),),
    )


def _with_links(rows: list[dict[str, Any]],
                representatives: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Adds to the merged rows the id of an item one can click through to.

    `representatives` are sorted from the most watched, so the first find for a name is the right one.
    """
    best: dict[str, str] = {}
    for row in representatives:
        best.setdefault(str(row["label"]), str(row["item_id"]))
    for row in rows:
        row["item_id"] = best.get(str(row["label"]))
    return rows


def user_genres(user_id: str, days: int, limit: int = 8) -> list[dict[str, Any]]:
    """The genres a user watches - by the watched time.

    One title has more genres ("Action, Sci-Fi, Adventure"), so its time counts into each of them. The sum is
    therefore not the total time and the percentages do not add up to a hundred - the UI has a note on it.

    Splitting the time between the genres equally would look fairer, but it would be no truer: nobody knows how
    much of a movie was "action" and how much "sci-fi". We rather say out loud that a title counts more times.
    """
    rows = db.query_all(
        f"""
        SELECT i.genres, SUM(p.watched_seconds) / 3600.0 AS hours
          FROM playback p
          JOIN items i ON i.id = p.item_id
         WHERE p.user_id = ? AND p.started_at >= ? AND p.started_at < ?
           AND p.watched_seconds > 0
           {watching_only("p.")}
           AND i.genres IS NOT NULL AND i.genres != ''
         GROUP BY i.genres
        """,
        (user_id, *_bounds(days)),
    )

    totals: dict[str, float] = {}
    for row in rows:
        hours = float(row["hours"] or 0)
        for genre in str(row["genres"]).split("|"):
            genre = genre.strip()
            if genre:
                totals[genre] = totals.get(genre, 0.0) + hours

    if not totals:
        return []

    ordered = sorted(totals.items(), key=lambda pair: -pair[1])
    total = sum(totals.values())

    result = [
        {"label": genre, "hours": hours, "value": hours,
         "percent": hours / total * 100 if total else 0.0}
        for genre, hours in ordered[:limit]
    ]
    # The rest into one row. Nobody reads a long list of genres each of which has half an hour - and the main thing
    # gets lost in it.
    rest = ordered[limit:]
    if rest:
        hours = sum(h for _genre, h in rest)
        result.append({
            "label": _t("Other"), "hours": hours, "value": hours,
            "percent": hours / total * 100 if total else 0.0,
            "genres": len(rest),
        })
    return result


def user_detail(user_id: str, days: int) -> dict[str, Any]:
    user = db.query_one("SELECT * FROM users WHERE id = ?", (user_id,))
    if user is None:
        return {}

    totals = db.query_one(
        f"""
        SELECT COUNT(*) AS plays,
               COALESCE(SUM(watched_seconds), 0) / 3600.0 AS hours,
               COUNT(DISTINCT item_id) AS item_count
        FROM playback
        WHERE user_id = ? AND started_at >= ? AND started_at < ? AND watched_seconds > 0
        {watching_only()}
        """,
        (user_id, *_bounds(days)),
    ) or {}
    totals["item_count"] = (totals.get("item_count") or 0) - extra_copies(_bounds(days), "p.watched_seconds > 0 AND p.user_id = ?", (user_id,))
    return {
        "user": user,
        "totals": totals,
        # `item_id` is here for the link into the library. For a series the rows merge by name, so no one right id
        # exists - we take the most watched one, which is also the one a person looks for.
        "top_items": _with_links(
            db.query_all(
                f"""
                SELECT COALESCE(series_name, item_name) AS label,
                       SUM(watched_seconds) / 3600.0 AS hours,
                       COUNT(*) AS plays
                FROM playback
                WHERE user_id = ? AND started_at >= ? AND started_at < ?
                  AND watched_seconds > 0
                  {watching_only()}
                GROUP BY label
                ORDER BY hours DESC
                LIMIT 10
                """,
                (user_id, *_bounds(days)),
            ),
            db.query_all(
                f"""
                SELECT COALESCE(series_name, item_name) AS label, item_id,
                       SUM(watched_seconds) AS total
                FROM playback
                WHERE user_id = ? AND started_at >= ? AND started_at < ?
                  AND watched_seconds > 0 AND item_id IS NOT NULL
                  {watching_only()}
                GROUP BY label, item_id
                ORDER BY total DESC
                """,
                (user_id, *_bounds(days)),
            ),
        ),
        "genres": user_genres(user_id, days),
        # The device also under its own name and with the viewer: by that the anonymising knows the label is this
        # person's device (access.DEVICE_KEYS).
        "devices": [{**r, "device_name": r["label"], "user_id": user_id,
                     # the page's word for a play without its device, in the reader's language
                     "label": _t("unknown") if r["label"] == "unknown" else r["label"]} for r in db.query_all(
            f"""
            SELECT COALESCE(device_name, 'unknown') AS label,
                   COALESCE(client, '') AS client,
                   SUM(watched_seconds) / 3600.0 AS hours
            FROM playback
            WHERE user_id = ? AND started_at >= ? AND started_at < ? AND watched_seconds > 0
            {watching_only()}
            GROUP BY label, client
            ORDER BY hours DESC
            LIMIT 10
            """,
            (user_id, *_bounds(days)),
        )],
    }
