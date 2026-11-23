"""Insights - what Jellyscope was made for.

Jellystat knows **what is watched**. MediaLyze knows **what those files are**.
Neither knows both at once, and so neither can answer the questions a person
really has:

    "How many terabytes does content nobody looked at in a year take me?"
    "Which file makes the server transcode video most often?"
    "What do I watch most - and do I have it in decent quality at all?"
    "Which 4K movie eating 60 GB did I watch once and never again?"

Every function in this file joins two tables - `playback` (the behaviour)
and `items` (the technical state of the file) - and the answer falls out of
that join.
"""

from __future__ import annotations

from typing import Any

from . import db, dialect, formatting, stats
from .stats.kinds import video_library_sql

# How many rows the Insights tables take from the database - on the Insights
# page and in the overview too. Only a few are shown right away (Settings ->
# Interface -> Long lists) and the rest is in the window. The table used to
# have 15 to 25 rows hard-coded, so "show all" would show 25 of the 36 titles
# the card's header talks about. The upper bound is for a big library: nobody
# would go through thousands of unwatched episodes in the window.
INSIGHT_ROWS = 100


def _bounds(period: Any) -> tuple[str, str]:
    """The bounds of the chosen period - (start, end).

    `stats` computes them, so the computation is in one place: Insights ask
    for the same days as the rest of the application, own period included.
    """
    return stats._bounds(period)

# How many seconds a playback must last to count. Below this boundary it
# tends to be "clicked by mistake and closed" - and such records would skew the numbers.
MIN_PLAY_SECONDS = 120

# Items added recently make no sense to mark as unwatched yet.
NEW_ITEM_GRACE_DAYS = 60


def dead_storage(limit: int = 25, days: int = 365) -> dict[str, Any]:
    """Files nobody looked at in the chosen period.

    The most valuable single number this application can make: how much
    space content nobody cares about takes.
    """
    common_where = f"""
        WHERE i.is_missing = 0
          AND i.size_bytes IS NOT NULL
          AND (i.date_created IS NULL
               OR substr(i.date_created, 1, 10) < date('now', '-{NEW_ITEM_GRACE_DAYS} days'))
          AND NOT EXISTS (
              SELECT 1 FROM playback p
               WHERE p.item_id = i.id
                 AND p.watched_seconds >= {MIN_PLAY_SECONDS}
                 AND p.started_at >= ? AND p.started_at < ?
                 {stats.watching_only("p.")}
          )
    """

    totals = db.query_one(
        f"""
        SELECT COUNT(*) AS item_count, COALESCE(SUM(i.size_bytes), 0) AS size_bytes
        FROM items i
        {common_where}
        """,
        (*_bounds(days),),
    ) or {}

    rows = db.query_all(
        f"""
        SELECT i.id, i.name, i.type, i.series_name, i.production_year,
               i.parent_index_number, i.index_number, i.path, i.tmdb_id,
               i.size_bytes, i.height, i.width, i.video_codec, i.bitrate,
               i.date_created, l.name AS library_name
        FROM items i
        LEFT JOIN libraries l ON l.id = i.library_id
        {common_where}
        ORDER BY i.size_bytes DESC, i.id
        LIMIT ?
        """,
        (*_bounds(days), limit),
    )

    library_total = db.query_value(
        "SELECT COALESCE(SUM(size_bytes), 0) FROM items WHERE is_missing = 0"
    )
    dead_bytes = totals.get("size_bytes") or 0

    return {
        # The key is on purpose not called "items" - in the template it would
        # clash with the dictionary's method .items() and a dash would quietly
        # show instead of the number. See chapter 9 in `JAK-TO-FUNGUJE.md`.
        "item_count": totals.get("item_count") or 0,
        "size_bytes": dead_bytes,
        "library_bytes": library_total,
        "share": (dead_bytes / library_total * 100) if library_total else 0.0,
        "rows": rows,
        "days": days,
    }


# Two summaries for `least_played()`, each apart: plays in the period
# (two question marks for the bounds) and the last playback over the whole history.
_PLAYED_IN_PERIOD = f"""
    SELECT item_id, COUNT(*) AS plays, SUM(watched_seconds) AS seconds
      FROM playback
     WHERE watched_seconds >= {MIN_PLAY_SECONDS}
       AND started_at >= ? AND started_at < ?
       {stats.by_titles()}
     GROUP BY item_id
"""
_LAST_PLAYED = f"""
    SELECT item_id, MAX(started_at) AS last_played
      FROM playback
     WHERE watched_seconds >= {MIN_PLAY_SECONDS}
     {stats.by_titles()}
     GROUP BY item_id
"""


# How `least_played()` orders titles with the same number of plays. Only a
# value from this dictionary goes into SQL, never text from the address. A
# title without a date added is at the end in both directions: SQLite and
# PostgreSQL would otherwise each put NULL elsewhere (see test_dialect).
LEAST_PLAYED_ORDERS = {
    "size": "COALESCE(i.size_bytes, 0) DESC",
    "oldest": "CASE WHEN i.date_created IS NULL THEN 1 ELSE 0 END, i.date_created ASC",
    "newest": "CASE WHEN i.date_created IS NULL THEN 1 ELSE 0 END, i.date_created DESC",
}


def least_played_order(value: Any) -> str:
    """A valid sort key; empty or anything unknown = "size".

    The only place where the default order is decided - the API reports by it
    how it sorted too.
    """
    key = str(value or "").strip().lower()
    return key if key in LEAST_PLAYED_ORDERS else "size"


def least_played(days: int = 365, limit: int = 25, min_size_bytes: int = 0,
                 order: str = "size") -> list[dict[str, Any]]:
    """Titles ordered from the least played - zeros first, then by space.

    Next to `dead_storage()` (what was not played **at all**) it answers a
    wider question: what is played least? A movie played once a year takes
    the same space as one played weekly, and this list puts them side by side.

    `order` changes only how titles with the **same** number of plays are
    ordered: "size" (the default) the biggest first, "oldest" / "newest" by
    the date added. The least played are always first - otherwise it would be
    just a list of the library by date. An unknown value = the default.

    The number of plays is taken only for the chosen period and only from
    plays that count (see `MIN_PLAY_SECONDS`); the last playback however is
    over the whole history - with a zero in the period that is exactly the
    interesting information.

    A summary through `LEFT JOIN` on a precomputed table, not a subquery for
    every row: the **whole** library is ordered and a subquery would be
    computed for every item apart, even for those that do not fit into `limit`.
    """
    since, until = _bounds(days)
    order_sql = LEAST_PLAYED_ORDERS[least_played_order(order)]
    return db.query_all(
        f"""
        SELECT i.id, i.name, i.type, i.series_name, i.production_year,
               i.parent_index_number, i.index_number, i.path, i.tmdb_id,
               i.size_bytes, i.height, i.width, i.video_codec,
               i.date_created, l.name AS library_name,
               COALESCE(s.plays, 0)         AS plays,
               COALESCE(s.seconds, 0)       AS watched_seconds,
               v.last_played
        FROM items i
        LEFT JOIN libraries l ON l.id = i.library_id
        LEFT JOIN ({_PLAYED_IN_PERIOD}) s ON s.item_id = i.id
        LEFT JOIN ({_LAST_PLAYED}) v ON v.item_id = i.id
        WHERE i.is_missing = 0
          AND i.type IN ('Movie', 'Episode')
          AND COALESCE(i.size_bytes, 0) >= ?
        ORDER BY plays ASC, {order_sql}, COALESCE(i.size_bytes, 0) DESC, i.name, i.id
        LIMIT ?
        """,
        (since, until, int(min_size_bytes), int(limit)),
    )


def transcode_speeds(days: int) -> dict[str, Any]:
    """How fast the server transcodes: the average, the slowest, the fastest.

    The unit is **one playback** and its average speed (the collector keeps it
    as a sum and a number of samples). We do not have instant values and they
    would not help: ffmpeg spins up for a while at the start of a transcode
    and the lowest instant value would almost always be that moment, not a
    real problem.

    The overall average is weighted by the number of samples, that is by the
    time of transcoding - a long movie is to say more than a trailer somebody
    played for ten seconds.

    "Nestíhalo" (could not keep up) = the transcode's average below the video's
    frame rate. Such a playback stuttered, however Jellyfin looked about it.

    Playbacks from before the speed was watched (and imported history) have
    no samples and do not count in the numbers at all - a zero would claim the
    server stood still.
    """
    rows = db.query_all(
        f"""
        SELECT p.id, p.item_id, p.started_at, p.transcode_hw, p.video_fps,
               p.transcode_fps_sum AS sum, p.transcode_fps_samples AS sample_count,
               COALESCE(p.series_name, p.item_name) AS label,
               p.series_name, p.item_name,
               i.id AS in_library, i.series_id AS series_id
        FROM playback p
        -- For the link: only an item Jellyscope knows has a detail.
        LEFT JOIN items i ON i.id = p.item_id
        WHERE p.started_at >= ? AND p.started_at < ?
        {stats.watching_only("p.")}
          AND p.transcode_fps_samples > 0
          -- The picture was copied, only the audio was converted: the "speed" is
          -- the speed of copying here, not of transcoding. The collector does not
          -- write it at all since 1.7.5, this guards the data stored before.
          AND COALESCE(p.transcode_video_direct, 0) = 0
        -- A fixed order: on a tie of speed it is decided by which playback was
        -- earlier (the slowest and the fastest below). Without it PostgreSQL returns
        -- the rows differently every time and the card would show another title between loads.
        ORDER BY p.started_at, p.id
        """,
        (*_bounds(days),),
    )

    def summary(group: list[dict[str, Any]]) -> dict[str, Any]:
        samples = sum(int(r["sample_count"]) for r in group)
        lagging = [r for r in group if r["video_fps"]
                   and r["fps"] < int(r["video_fps"]) - 1]
        return {
            "playbacks": len(group),
            "average": round(sum(int(r["sum"]) for r in group) / samples) if samples else None,
            "least": min(group, key=lambda r: r["fps"]),
            "most": max(group, key=lambda r: r["fps"]),
            "too_slow": len(lagging),
            "too_slow_share": len(lagging) / len(group) * 100 if group else 0.0,
        }

    playbacks = []
    for r in rows:
        r = dict(r)
        r["fps"] = round(int(r["sum"]) / int(r["sample_count"]))
        # An episode leads to the series (the label is the series' name), a movie to itself.
        # An item the database does not know (an import without pairing) has no link.
        r["detail_url"] = (f"/series/{r['series_id']}" if r["series_id"]
                           else f"/item/{r['in_library']}" if r["in_library"] else None)
        playbacks.append(r)
    if not playbacks:
        return {"playbacks": 0, "by_hw": []}

    by_hw: dict[str, list[dict[str, Any]]] = {}
    for r in playbacks:
        # Empty = Jellyfin reported no acceleration, so the processor.
        by_hw.setdefault(str(r["transcode_hw"] or "").lower(), []).append(r)
    groups = [{"hw": hw, **summary(members)} for hw, members in by_hw.items()]
    groups.sort(key=lambda s: s["playbacks"], reverse=True)
    return {**summary(playbacks), "by_hw": groups}


def transcode_offenders(days: int, limit: int = 15) -> list[dict[str, Any]]:
    """Files the server transcodes most often.

    Transcode = the server has to re-encode the video on the fly, because the
    player cannot play it directly. It costs the processor and on a weaker
    server it means a stuttering picture. When you know which files cause it,
    you can convert them to a friendlier format - and the problem vanishes.
    """
    reasons_sql = dialect.group_concat_distinct(db.current_dialect(),
                                                "p.transcode_reasons")
    # The groups are put together by `stats.title_key()` - just like for the
    # most watched titles. It used to be grouped right in SQL by the name and a
    # series fell apart into single episodes wherever `series_name` was missing
    # (an import, older records). It is unified on purpose: two different rules
    # for the same thing would drift apart in time and one overview would show
    # something else than the other.
    rows = db.query_all(
        f"""
        SELECT {stats.TITLE_GROUP_COLUMNS},
               COUNT(*)                       AS transcodes,
               SUM(p.watched_seconds)         AS seconds,
               {reasons_sql}                  AS reasons,
               MAX(i.video_codec)             AS video_codec,
               MAX(i.audio_codec)             AS audio_codec,
               MAX(i.height)                  AS height,
               MAX(i.width)                   AS width,
               MAX(i.bitrate)                 AS bitrate
        FROM playback p
        LEFT JOIN items i ON i.id = p.item_id
        WHERE {stats.transcode_condition('p.play_method')}
          AND p.started_at >= ? AND p.started_at < ?
          {stats.watching_only("p.")}
          AND p.watched_seconds >= ?
    GROUP BY {stats.TITLE_GROUP_KEY}
        """,
        (*_bounds(days), MIN_PLAY_SECONDS),
    )

    # The technical details and reasons are copied from the group's most often
    # transcoded episode - for a series they are practically the same for all episodes anyway.
    titles = stats._merge_titles(
        rows, stats.KIND_BOTH, limit,
        {"transcodes": "transcodes", "seconds": "seconds"},
        carry=("reasons", "video_codec", "audio_codec", "height",
               "width", "bitrate"),
        # A file that is not in the library cannot be converted to a friendlier
        # format - and that is exactly the point of this list.
        hide_unsorted=True,
        # A list of files: two copies of a movie are two rows, each with its own details.
        copies_apart=True,
    )
    for title in titles:
        title["hours"] = title["seconds"] / 3600.0
        title["transcodes"] = int(title["transcodes"])
    return titles


def transcode_reasons(days: int) -> list[dict[str, Any]]:
    """Why it is transcoded at all. Jellyfin reports the reason - only nobody reads it."""
    rows = db.query_all(
        f"""
        SELECT transcode_reasons AS reasons, COUNT(*) AS plays
        FROM playback
        WHERE {stats.transcode_condition()}
          AND transcode_reasons IS NOT NULL AND transcode_reasons != ''
          AND started_at >= ? AND started_at < ?
          {stats.watching_only()}
        GROUP BY transcode_reasons
        """,
        (*_bounds(days),),
    )

    # Jellyfin can send several reasons at once ("VideoCodecNotSupported,
    # AudioCodecNotSupported"). We split them and add up each apart.
    counter: dict[str, int] = {}
    for row in rows:
        for reason in str(row["reasons"]).split(","):
            reason = reason.strip()
            if reason:
                counter[reason] = counter.get(reason, 0) + int(row["plays"])

    # `label` is the code as Jellyfin sent it (it can be looked up in its
    # documentation by it), `name` a sentence for a person that the page shows.
    return [
        {"label": label, "name": formatting.transcode_reasons_text(label), "plays": plays}
        for label, plays in sorted(counter.items(), key=lambda pair: -pair[1])
    ]


def upgrade_candidates(days: int, limit: int = 15) -> list[dict[str, Any]]:
    """Much watched, but in a weak quality.

    Exactly the question neither of the two models can answer:
    "what do I watch most - and is it worth it?"

    ## What "weak quality" is

    A resolution **below 1080p**, or a low bitrate. And below 1080p is told by
    both sides of the picture, not only by the height - just like
    `RESOLUTION_CASE` in stats/tech.py.

    There used to stand only `i.height < 1000`, which marked every widescreen
    movie as weak quality: a usual 1080p release in the scope ratio has the
    dimensions 1920x800, so the height is below the boundary, even though it is
    full 1080p. So the width decides.

    And the width did not get into the template at all, so the column
    "Rozlišení" showed something else than what the file really is too.
    """
    rows = db.query_all(
        f"""
        SELECT {stats.TITLE_GROUP_COLUMNS},
               SUM(p.watched_seconds)   AS seconds,
               COUNT(*)                 AS plays,
               MAX(i.height)            AS height,
               MAX(i.width)             AS width,
               MAX(i.bitrate)           AS bitrate,
               MAX(i.video_codec)       AS video_codec,
               MAX(i.size_bytes)        AS size_bytes
        FROM playback p
        JOIN items i ON i.id = p.item_id
        WHERE p.started_at >= ? AND p.started_at < ?
        {stats.watching_only("p.")}
          AND p.watched_seconds >= ?
          AND i.is_missing = 0
          AND (
                (
                     COALESCE(i.width, 0)  < 1800
                 AND COALESCE(i.height, 0) < 1000
                 AND (i.width IS NOT NULL OR i.height IS NOT NULL)
                )
             OR (i.bitrate IS NOT NULL AND i.bitrate < 3000000)
          )
     GROUP BY {stats.TITLE_GROUP_KEY}
        """,
        (*_bounds(days), MIN_PLAY_SECONDS),
    )

    titles = stats._merge_titles(
        rows, stats.KIND_BOTH, limit,
        # The size adds up: for a series the sum over all episodes is interesting.
        {"seconds": "seconds", "plays": "plays", "size_bytes": "size_bytes"},
        carry=("height", "width", "bitrate", "video_codec"),
        hide_unsorted=True,
        # A list of files: a weak copy of a movie is its own row, not a sum with the good one.
        copies_apart=True,
    )
    for title in titles:
        title["hours"] = title["seconds"] / 3600.0
        title["plays"] = int(title["plays"])
    return titles


def oversized_rarely_watched(days: int, limit: int = 15) -> list[dict[str, Any]]:
    """Big files with little use - candidates for shrinking.

    We measure "gigabytes per one watched hour". The higher the number, the
    worse the ratio of space to use.
    """
    # The divisor must not be zero, otherwise an unwatched title would get an
    # infinite ratio. A quarter of an hour as the lower bound is an agreed limit, not a measurement.
    watched_hours = dialect.greatest(
        db.current_dialect(), "COALESCE(SUM(p.watched_seconds), 0) / 3600.0", "0.25"
    )

    # Beware of HAVING: PostgreSQL does not allow a column alias (`plays`) in it,
    # even though SQLite does. So the whole expression is repeated.
    return db.query_all(
        f"""
        SELECT i.id,
               COALESCE(i.series_name, i.name) AS label,
               i.type,
               i.parent_index_number,
               i.index_number,
               i.path,
               i.tmdb_id,
               i.height,
               i.width,
               i.bitrate,
               i.video_codec,
               i.size_bytes,
               COALESCE(SUM(p.watched_seconds), 0) / 3600.0 AS hours,
               COUNT(p.id) AS plays,
               i.size_bytes / 1073741824.0 / {watched_hours} AS gb_per_hour
        FROM items i
        LEFT JOIN playback p
               ON p.item_id = i.id
              AND p.watched_seconds >= ?
              AND p.started_at >= ? AND p.started_at < ?
              {stats.watching_only("p.")}
        WHERE i.is_missing = 0
          AND i.size_bytes IS NOT NULL
          AND i.size_bytes > 2147483648      -- bigger than 2 GB
        GROUP BY i.id, i.series_name, i.name, i.type, i.parent_index_number,
                 i.index_number, i.path, i.tmdb_id, i.height, i.width, i.bitrate,
                 i.video_codec, i.size_bytes
        HAVING COUNT(p.id) <= 2
        ORDER BY gb_per_hour DESC, i.id
        LIMIT ?
        """,
        (MIN_PLAY_SECONDS, *_bounds(days), limit),
    )


def storage_efficiency(days: int) -> list[dict[str, Any]]:
    """For every library: how much space it takes and how much of it is really watched.

    Which library a playback belongs to is **derived from the item**
    (`i.library_id`), not from the column in the playback record. That is only
    a fallback for the case that the item is no longer in the library.

    Only `p.library_id` used to be read - and that is empty for a big part of
    the records: imported history does not know the library at all and our own
    collection does not fill it for an item we did not synchronise yet. The
    watched time and the number of watched titles therefore came out much lower
    than they really were, while the content and the size (those are read from
    `items`) matched. Exactly the contradiction one could see in the table.
    """
    return db.query_all(
        f"""
        SELECT l.name                                       AS label,
               COUNT(i.id)                                  AS item_count,
               COALESCE(SUM(i.size_bytes), 0)               AS size_bytes,
               (SELECT COALESCE(SUM(p.watched_seconds), 0) / 3600.0
                  FROM playback p
             LEFT JOIN items pi ON pi.id = p.item_id
                 WHERE COALESCE(pi.library_id, p.library_id) = l.id
                   AND p.started_at >= ? AND p.started_at < ?
                   {stats.watching_only("p.")}) AS hours,
               -- Only items that **really are** in the library. The total
               -- count above is from the live ones only too (is_missing = 0), so
               -- without this condition apples would be compared with pears:
               -- the watched ones including the archive against the total without it.
               -- For a series of which some episodes vanished meanwhile the
               -- use thus came out over 100 %.
               (SELECT COUNT(DISTINCT p.item_id)
                  FROM playback p
                  JOIN items pi ON pi.id = p.item_id
                 WHERE pi.library_id = l.id
                   AND pi.is_missing = 0
                   AND p.watched_seconds >= ?
                   AND p.started_at >= ? AND p.started_at < ?
                   {stats.watching_only("p.")}) AS watched_items
        FROM libraries l
        LEFT JOIN items i ON i.library_id = l.id AND i.is_missing = 0
        WHERE {video_library_sql("l")}
        GROUP BY l.id, l.name
        ORDER BY size_bytes DESC
        """,
        (*_bounds(days), MIN_PLAY_SECONDS, *_bounds(days)),
    )


def duplicate_candidates(limit: int = 30) -> list[dict[str, Any]]:
    """Movies that look like you have them twice.

    The word "candidates" is on purpose here. A match of the name and the year
    is no proof - it can be a director's cut next to the cinema version. So the
    application deletes nothing and suggests deleting nothing, it only shows
    where to look.
    """
    current = db.current_dialect()
    # The resolution is put together by the same rule as everywhere else - by the
    # width and the height (see stats.RESOLUTION_CASE). There used to stand a plain
    # `height || 'p'`, so a 4K movie in the scope ratio (3840x1608) showed in the
    # variant's label as "hevc / 1608p".
    variants = dialect.group_concat(
        current,
        "COALESCE(video_codec, '?') || ' / ' || " + stats.RESOLUTION_CASE,
        "' | '",
    )
    paths = dialect.group_concat(current, "path", "char(10)")

    groups = db.query_all(
        f"""
        SELECT LOWER(TRIM(name))                AS key_name,
               MAX(name)                        AS label,
               production_year,
               COUNT(*)                         AS copies,
               SUM(size_bytes)                  AS size_bytes,
               {variants}                       AS variants,
               {paths}                          AS paths
        FROM items
        WHERE is_missing = 0
          AND type = 'Movie'
          AND name IS NOT NULL
        GROUP BY LOWER(TRIM(name)), production_year
        HAVING COUNT(*) > 1
        ORDER BY size_bytes DESC
        LIMIT ?
        """,
        (limit,),
    )
    # The copies one by one, for the links: the group's name leads to the first
    # copy and every path to its file. With a second query, not another joined
    # column - the database does not guarantee the order of values in two
    # GROUP_CONCATs side by side and a path could get the id of another copy.
    copies: dict[tuple[str, Any], list[dict[str, Any]]] = {}
    for r in db.query_all_in(
            "SELECT id, path, LOWER(TRIM(name)) AS key_name, production_year FROM items"
            # By id, not by path: SQLite puts NULL up and PostgreSQL down,
            # "the first copy" would then be a different one in each database.
            " WHERE is_missing = 0 AND type = 'Movie' AND LOWER(TRIM(name)) IN ({}) ORDER BY id",
            sorted({s["key_name"] for s in groups})):
        copies.setdefault((r["key_name"], r["production_year"]), []).append({"id": r["id"], "path": r["path"] or ""})
    for group in groups:
        group["copy_items"] = copies.get((group["key_name"], group["production_year"]), [])
        group["detail_url"] = f"/item/{group['copy_items'][0]['id']}" if group["copy_items"] else None
    return groups


def never_finished(days: int, limit: int = 15) -> list[dict[str, Any]]:
    """Started, but not finished.

    We compare how far the playback got with the item's total length. Below
    15 % of the length we take as "tried and gave up".
    """
    return db.query_all(
        f"""
        SELECT COALESCE(p.series_name, p.item_name) AS label,
               COUNT(*) AS attempts,
               MAX(p.position_ticks * 100.0 / i.runtime_ticks) AS best_percent,
               i.runtime_ticks / 600000000.0 AS runtime_minutes
        FROM playback p
        JOIN items i ON i.id = p.item_id
        WHERE p.started_at >= ? AND p.started_at < ?
        {stats.watching_only("p.")}
          AND p.watched_seconds >= ?
          AND i.runtime_ticks > 0
          AND p.position_ticks IS NOT NULL
        GROUP BY COALESCE(p.series_name, p.item_name), i.runtime_ticks
        HAVING MAX(p.position_ticks * 100.0 / i.runtime_ticks) < 15
        -- On a tie the name and the length (= the key of the group): without them the limit
        -- picks other titles in SQLite than in PostgreSQL.
        ORDER BY attempts DESC, best_percent ASC, label, i.runtime_ticks
        LIMIT ?
        """,
        (*_bounds(days), MIN_PLAY_SECONDS, limit),
    )
