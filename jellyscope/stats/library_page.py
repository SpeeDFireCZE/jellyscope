"""The library by titles: rows, a series' detail, the tiles and the library's activity.

Part of the statistics package (see jellyscope/stats/__init__.py)."""
from __future__ import annotations

from typing import Any

from .. import db
from ..i18n import translate as _t
from .viewers import VIEWER_NAME, VIEWER_ORDER, VIEWER_GROUP
from .kinds import NOT_LIBRARY_TYPES, watching_only, by_titles, video_library_sql
from .periods import _bounds
from .playback import transcode_description
from .tech import RESOLUTION_CASE, RANGE_CASE
from .titles import extra_copies


# ---------------------------------------------------------------------------
# The library by titles, not by episodes
# ---------------------------------------------------------------------------
#
# In the table `items` every episode is a row of its own - that is how Jellyfin sends them and how the statistics
# need them. In the library list it is of no use, though: a series of five seasons takes a hundred rows and
# everything else gets lost among them.
#
# So we group by series. The group's key is `COALESCE(series_id, id)`, so a movie (which has no series_id) forms a
# group on its own and the same query serves both - no two queries that would drift apart over time. `main_id`
# between them: a movie twice in one library forms one group with the main copy (scanner/copies.py) - the group's id
# is the main one's id, the count of copies `episode_count`.
_GROUP = "COALESCE(i.series_id, i.main_id, i.id)"

# The group's columns. For a movie the group has one member, so SUM and MAX simply return its own value - so we do
# not have to tell them apart.
_GROUP_COLUMNS = f"""
        {_GROUP}                                   AS id,
        MAX(CASE WHEN i.series_id IS NULL THEN 0 ELSE 1 END) AS is_series,
        MAX(COALESCE(i.series_name, i.name))         AS name,
        MAX(i.type)                                  AS type,
        MAX(i.library_id)                            AS library_id,
        COUNT(*)                                     AS episode_count,
        -- The group files: an item is one file, an item with more versions
        -- (Jellyfin joined files in one folder) as many as it has versions.
        -- It counts the same as the switch in the detail (item_detail.title_files).
        COALESCE(SUM(COALESCE(v.version_count, 1)), 0)       AS file_count,
        COUNT(DISTINCT i.parent_index_number)        AS season_count,
        COALESCE(SUM(i.size_bytes), 0)               AS size_bytes,
        COALESCE(SUM(i.runtime_ticks), 0)            AS runtime_ticks,
        MAX(i.width)                                 AS width,
        MAX(i.height)                                AS height,
        MAX(i.bitrate)                               AS bitrate,
        MAX(i.video_codec)                           AS video_codec,
        MAX(i.production_year)                       AS production_year,
        MAX(i.date_created)                          AS date_created,
        MAX(i.tech_source)                           AS tech_source,
        -- A group is either all live or all archived - the filter is
        -- in WHERE. The template needs it for the mark "Archived".
        MAX(i.is_missing)                            AS is_missing,
        COALESCE(SUM(COALESCE(p.plays, 0)), 0)       AS plays
"""

# We count the plays with one query in advance and join them. A subquery per row could not be used with grouping and
# for a big library it would mean thousands of extra queries anyway.
_PLAYS = f"""
    LEFT JOIN (SELECT item_id, COUNT(*) AS plays
                 FROM playback WHERE watched_seconds > 60
                 {by_titles()}
                GROUP BY item_id) p ON p.item_id = i.id
"""

_VERSIONS = """
    LEFT JOIN (SELECT item_id, COUNT(*) AS version_count
                 FROM item_versions
                GROUP BY item_id HAVING COUNT(*) > 1) v ON v.item_id = i.id
"""


def _library_filter(library_id: str | None, search: str | None,
                    archived: bool) -> tuple[list[str], list[Any]]:
    where = ["i.is_missing = 1" if archived else "i.is_missing = 0"]
    params: list[Any] = []
    if library_id:
        where.append("i.library_id = ?")
        params.append(library_id)
    if search:
        where.append(f"(i.name LIKE ? {db.LIKE_ESCAPE} OR i.series_name LIKE ? {db.LIKE_ESCAPE})")
        params.extend([db.like_contains(search)] * 2)
    return where, params


def library_rows(limit: int, offset: int, library_id: str | None = None,
                 search: str | None = None, sort: str = "size",
                 archived: bool = False) -> list[dict[str, Any]]:
    """The library list: movies one by one, series as one row."""
    where, params = _library_filter(library_id, search, archived)

    # The sorting must not be pasted into SQL straight from the user's input. A fixed list of choices and we take from
    # it.
    # A title without technical data (bitrate, height NULL) goes last in both databases - PostgreSQL puts NULL first
    # with DESC. The aggregate, not the alias: PostgreSQL takes an alias in ORDER BY only as a bare name. The group's
    # id decides a tie, otherwise PostgreSQL can order equal rows differently for every page and repeat or skip one.
    sort_options = {
        "size": "size_bytes DESC, id",
        "bitrate": "MAX(i.bitrate) IS NULL, bitrate DESC, id",
        "name": "name ASC, id",
        "plays": "plays DESC, id",
        "resolution": "MAX(i.height) IS NULL, height DESC, id",
    }
    order_by = sort_options.get(sort, sort_options["size"])

    params.extend([limit, offset])
    return db.query_all(
        f"""
        SELECT {_GROUP_COLUMNS}
        FROM items i
        {_PLAYS}
        {_VERSIONS}
        WHERE {' AND '.join(where)}
        GROUP BY {_GROUP}
        ORDER BY {order_by}
        LIMIT ? OFFSET ?
        """,
        tuple(params),
    )


def library_rows_count(library_id: str | None = None, search: str | None = None,
                       archived: bool = False) -> int:
    """How many rows the list has - i.e. groups, not items."""
    where, params = _library_filter(library_id, search, archived)
    return int(db.query_value(
        f"""
        SELECT COUNT(*) FROM (
            SELECT {_GROUP} AS id FROM items i
             WHERE {' AND '.join(where)}
             GROUP BY {_GROUP}
        ) grouped
        """,
        tuple(params),
    ))


def series_detail(series_id: str) -> dict[str, Any]:
    """A series split into seasons and their episodes.

    Returns an empty dict when we do not know such a series - the route makes a 404 of it.

    **Archived episodes do not count among the live ones.** The archive is a list of files that are no longer in
    Jellyfin - the play history stays with them, but the title itself vanished. The series' detail used to list them
    beside the live ones without a difference, whereas the library list left them out; a series then showed a hundred
    episodes in Jellyfin and a hundred and twenty here, with no way to tell why.

    So we list them separately and say how many there are.
    """
    all_items = db.query_all(
        f"""
        SELECT i.*, l.name AS library_name, COALESCE(p.plays, 0) AS plays
        FROM items i
        LEFT JOIN libraries l ON l.id = i.library_id
        {_PLAYS}
        WHERE i.series_id = ?
        ORDER BY i.parent_index_number, i.index_number, i.name
        """,
        (series_id,),
    )
    if not all_items:
        return {}

    episodes = [d for d in all_items if not d["is_missing"]]
    archived_episodes = [d for d in all_items if d["is_missing"]]

    # A series of which only the archive is left still makes sense to show - otherwise a link from the history would
    # end in 404.
    if not episodes:
        episodes, archived_episodes = archived_episodes, []

    seasons: list[dict[str, Any]] = []
    by_season: dict[Any, dict[str, Any]] = {}
    for episode in episodes:
        number = episode["parent_index_number"]
        season = by_season.get(number)
        if season is None:
            season = {
                "number": number,
                # We have the season's name only with some items; when it is missing, we put it together from the
                # number.
                "name": episode["season_name"] or (
                    f"{_t('Season')} {number}" if number is not None else _t("Other")),
                "episodes": [],
                "size_bytes": 0,
                "plays": 0,
            }
            by_season[number] = season
            seasons.append(season)
        season["episodes"].append(episode)
        season["size_bytes"] += int(episode["size_bytes"] or 0)
        season["plays"] += int(episode["plays"] or 0)

    first = episodes[0]
    return {
        "id": series_id,
        "name": first["series_name"] or first["name"],
        # The series poster's tag. It goes into the picture's address, so after the poster changes in Jellyfin a new
        # address is loaded - and the browser cannot slip in its stored copy.
        "image_tag": first["series_image_tag"],
        "library_id": first["library_id"],
        "seasons": seasons,
        "episode_count": len(episodes),
        "season_count": len(seasons),
        "size_bytes": sum(int(d["size_bytes"] or 0) for d in episodes),
        "runtime_ticks": sum(int(d["runtime_ticks"] or 0) for d in episodes),
        "plays": sum(int(d["plays"] or 0) for d in episodes),
        "missing": all(d["is_missing"] for d in episodes),
        # Episodes that are no longer in Jellyfin. They are listed separately, so that the difference against Jellyfin
        # makes clear right away what causes it.
        "archived": archived_episodes,
        "archived_count": len(archived_episodes),
        # The summary shown at the top for a series - the same as for a movie.
        "library_name": first["library_name"],
        "path": _common_folder(d["path"] for d in episodes),
        "added": min((d["date_created"] for d in episodes if d["date_created"]),
                       default=None),
        "last_episode": max((d["date_created"] for d in episodes if d["date_created"]),
                            default=None),
        "measured": max((d["tech_updated_at"] for d in episodes
                        if "tech_updated_at" in d.keys() and d["tech_updated_at"]),
                       default=None),
    }


def _common_folder(paths: Any) -> str:
    """The folder the series lies in - the common start of its episodes' paths.

    For a movie the path to the file is shown. A series has no one file, so the folder is shown: the longest common
    start of all episodes' paths is taken and cut at the last separator. With a library put together the usual way
    that gives the series' folder.
    """
    path_list = [str(c) for c in paths if c]
    if not path_list:
        return ""
    common = path_list[0]
    for path in path_list[1:]:
        # Character by character, while they match.
        i = 0
        while i < min(len(common), len(path)) and common[i] == path[i]:
            i += 1
        common = common[:i]
        if not common:
            return ""
    cut = max(common.rfind("/"), common.rfind("\\"))
    return common[:cut] if cut > 0 else common


# Labels of Jellyfin library types (translated when the tiles are built).
LIBRARY_TYPE_LABELS = {
    "movies": "Movies", "tvshows": "TV shows", "homevideos": "Home videos", "mixed": "Mixed content",
    "music": "Music", "books": "Books", "photos": "Photos", "musicvideos": "Music videos",
}
TICKS_PER_SECOND = 10_000_000
# The music tiles' pieces (library_cards), one row per music library: present albums, present
# tracks with their size and length, and the newest album with a cover (poradi = 1).
_MUSIC_ALBUMS = "SELECT library_id, COUNT(*) AS albums FROM music_album WHERE missing = 0 GROUP BY library_id"
_MUSIC_TRACKS = ("SELECT library_id, COUNT(*) AS tracks, SUM(size_bytes) AS size_bytes, SUM(runtime_ticks) AS ticks"
                 " FROM music_track WHERE missing = 0 GROUP BY library_id")
# The books tiles: e-books and audiobooks as works (the files of an audiobook are one), size, audio length, and the
# newest book with a cover. A work is the kind and the stored title key; a file without one is its own work.
_BOOK_WORK = "CASE WHEN title_key = '' THEN id ELSE title_key END"
_BOOKS = (f"SELECT library_id, COUNT(DISTINCT CASE WHEN kind = 'book' THEN {_BOOK_WORK} END) AS ebooks,"
          f" COUNT(DISTINCT CASE WHEN kind = 'audiobook' THEN {_BOOK_WORK} END) AS audiobooks,"
          " SUM(size_bytes) AS size_bytes, SUM(CASE WHEN kind = 'audiobook' THEN runtime_ticks END) AS ticks"
          " FROM book WHERE missing = 0 GROUP BY library_id")
_BOOK_COVERS = ("SELECT library_id, id, cover_tag,"
                " ROW_NUMBER() OVER (PARTITION BY library_id ORDER BY COALESCE(added_at, '') DESC, id) AS rank"
                " FROM book WHERE missing = 0 AND cover_tag <> ''")
_MUSIC_COVERS = ("SELECT library_id, id, cover_tag,"
                 " ROW_NUMBER() OVER (PARTITION BY library_id ORDER BY COALESCE(added_at, '') DESC, id) AS rank"
                 " FROM music_album WHERE missing = 0 AND cover_tag <> ''")


def library_cards() -> list[dict[str, Any]]:
    """Tiles of the Library page: every library Jellyfin has, of four kinds (`kind`).

    * `video` - movies and episodes: files, size, length. `poster_id` is the item whose
      backdrop is the tile's background - libraries rarely have an image of their own, so
      the largest title inside (a series, not an episode: an episode usually has no
      backdrop and the tile stayed grey).
    * `music` - albums, tracks, size and length from the music tables (music/sync.py);
      `cover_id` is the most recently added album with a cover.
    * `books` - e-books and audiobooks, size and audio length from the book table (books/sync.py); `cover_id`
      is the most recently added book with a cover.
    * `unsupported` - a type Jellyscope does not collect yet (photos, ...): name and type.

    A library missing from two library syncs in a row is not shown (`missing_runs`).
    """
    video = db.query_all(
        f"""
        SELECT l.id,
               l.name,
               l.collection_type,
               COUNT(i.id)                    AS item_count,
               COALESCE(SUM(i.size_bytes), 0) AS size_bytes,
               COALESCE(SUM(i.runtime_ticks), 0) / 36000000000.0 AS hours,
               (SELECT COALESCE(s.series_id, s.id) FROM items s
                 WHERE s.library_id = l.id AND s.is_missing = 0
                 ORDER BY COALESCE(s.size_bytes, 0) DESC, s.id LIMIT 1) AS poster_id
        FROM libraries l
        LEFT JOIN items i ON i.library_id = l.id AND i.is_missing = 0
        WHERE l.missing_runs < 2 AND {video_library_sql("l")}
        GROUP BY l.id, l.name, l.collection_type
        ORDER BY item_count DESC, l.name
        """
    )
    # One pass over the tracks and one over the albums for all music libraries: per-library
    # totals as grouped subqueries, the cover as the newest album with one (ROW_NUMBER).
    music = db.query_all(
        f"""
        SELECT l.id,
               l.name,
               l.collection_type,
               COALESCE(a.albums, 0) AS albums,
               COALESCE(t.tracks, 0) AS tracks,
               COALESCE(t.size_bytes, 0) AS size_bytes,
               COALESCE(t.ticks, 0) / {TICKS_PER_SECOND} AS seconds,
               c.id AS cover_id,
               c.cover_tag AS cover_tag
        FROM libraries l
        LEFT JOIN ({_MUSIC_ALBUMS}) a ON a.library_id = l.id
        LEFT JOIN ({_MUSIC_TRACKS}) t ON t.library_id = l.id
        LEFT JOIN ({_MUSIC_COVERS}) c ON c.library_id = l.id AND c.rank = 1
        WHERE l.missing_runs < 2 AND l.collection_type = 'music'
        ORDER BY l.name
        """
    )
    books = db.query_all(
        f"""
        SELECT l.id,
               l.name,
               l.collection_type,
               COALESCE(b.ebooks, 0) AS ebooks,
               COALESCE(b.audiobooks, 0) AS audiobooks,
               COALESCE(b.size_bytes, 0) AS size_bytes,
               COALESCE(b.ticks, 0) / {TICKS_PER_SECOND} AS seconds,
               c.id AS cover_id,
               c.cover_tag AS cover_tag
        FROM libraries l
        LEFT JOIN ({_BOOKS}) b ON b.library_id = l.id
        LEFT JOIN ({_BOOK_COVERS}) c ON c.library_id = l.id AND c.rank = 1
        WHERE l.missing_runs < 2 AND l.collection_type = 'books'
        ORDER BY l.name
        """
    )
    # Collections and playlists are no library: a row 1.9.0 stored is not shown even before the next library
    # sync deletes it (stats.kinds.NOT_LIBRARY_TYPES).
    not_shown = ", ".join(f"'{kind}'" for kind in ("music", "books", *NOT_LIBRARY_TYPES))
    other = db.query_all(
        f"""
        SELECT l.id, l.name, l.collection_type
        FROM libraries l
        WHERE l.missing_runs < 2 AND NOT {video_library_sql("l")} AND l.collection_type NOT IN ({not_shown})
        ORDER BY l.name
        """
    )
    rows = ([{**row, "kind": "video"} for row in video] + [{**row, "kind": "music"} for row in music]
            + [{**row, "kind": "books"} for row in books] + [{**row, "kind": "unsupported"} for row in other])
    for row in rows:
        # The key - the card translates it when it draws (translating here too made a Spanish "Series",
        # itself an English key, into "Serie").
        row["type_label"] = LIBRARY_TYPE_LABELS.get(row["collection_type"] or "", "Other")
    return rows


def library(library_id: str) -> dict[str, Any] | None:
    return db.query_one("SELECT * FROM libraries WHERE id = ?", (library_id,))


def library_overview(library_id: str) -> dict[str, Any]:
    """The summary numbers of one library."""
    return db.query_one(
        f"""
        SELECT COUNT(*)                       AS item_count,
               COALESCE(SUM(size_bytes), 0)   AS size_bytes,
               COALESCE(SUM(runtime_ticks), 0) / 36000000000.0 AS hours,
               COALESCE(AVG(bitrate), 0)      AS avg_bitrate,
               SUM(CASE WHEN {RESOLUTION_CASE} = '4K' THEN 1 ELSE 0 END) AS uhd_count,
               SUM(CASE WHEN {RANGE_CASE} IN ('HDR', 'DOVI') THEN 1 ELSE 0 END) AS hdr_count,
               SUM(CASE WHEN tech_source IS NULL THEN 1 ELSE 0 END) AS without_tech
        FROM items
        WHERE is_missing = 0 AND library_id = ?
        """,
        (library_id,),
    ) or {}


def library_activity(library_id: str, days: Any, with_viewers: bool = True) -> dict[str, Any]:
    """What was watched in this library in the chosen period.

    `days` is a count of days or a custom `Period`, the same as elsewhere. Without a default on purpose: there used to
    be a fixed 90 and the page thus quietly computed for another period than the person had chosen. The last plays
    are outside the period - always the last 25.

    `with_viewers=False` (an account without "...including who watched") leaves out the viewers and their last
    plays - the queries do not run at all then.
    """
    recent = [] if not with_viewers else db.query_all(
        f"""
        SELECT p.*, i.height, i.video_codec AS source_codec,
               i.audio_codec AS source_audio_codec
        FROM playback p
        LEFT JOIN items i ON i.id = p.item_id
        WHERE p.library_id = ? AND p.watched_seconds > 0
        {watching_only("p.")}
        ORDER BY p.started_at DESC, p.id DESC LIMIT 25
        """,
        (library_id,),
    )
    for row in recent:
        row["recount"] = transcode_description(row)
    totals = db.query_one(
        f"""
        SELECT COUNT(*) AS plays,
               COALESCE(SUM(watched_seconds), 0) / 3600.0 AS hours,
               COUNT(DISTINCT user_id) AS users,
               COUNT(DISTINCT item_id) AS item_count
        FROM playback
        WHERE library_id = ? AND started_at >= ? AND started_at < ?
          AND watched_seconds > 0
          {watching_only()}
        """,
        (library_id, *_bounds(days)),
    ) or {}
    totals["item_count"] = (totals.get("item_count") or 0) - extra_copies(_bounds(days), "p.watched_seconds > 0 AND p.library_id = ?", (library_id,))
    return {
        "totals": totals,
        "top_items": db.query_all(
            f"""
            SELECT COALESCE(series_name, item_name) AS label,
                   SUM(watched_seconds) / 3600.0 AS hours,
                   COUNT(*) AS plays
            FROM playback
            WHERE library_id = ? AND started_at >= ? AND started_at < ?
              AND watched_seconds > 0
              {watching_only()}
            GROUP BY label ORDER BY hours DESC LIMIT 10
            """,
            (library_id, *_bounds(days)),
        ),
        # With user_id: by it the anonymising knows the label is a viewer's name (without it the chart would show the
        # real names even with hidden ones). One column per viewer - see VIEWER_NAME.
        "top_users": [] if not with_viewers else db.query_all(
            f"""
            SELECT {VIEWER_NAME} AS label, p.user_id,
                   SUM(p.watched_seconds) / 3600.0 AS hours
            FROM playback p
            LEFT JOIN users u ON u.id = p.user_id
            WHERE p.library_id = ? AND p.started_at >= ? AND p.started_at < ?
              AND p.watched_seconds > 0 AND p.user_name IS NOT NULL
              {watching_only("p.")}
            GROUP BY {VIEWER_GROUP}
            ORDER BY hours DESC, {VIEWER_ORDER}, label LIMIT 10
            """,
            (library_id, *_bounds(days)),
        ),
        "recent": recent,
    }
