"""Statistics - all the questions we ask the database.

Every function here does one thing: it asks one SQL query and returns the result as a plain list of dicts. No
function draws anything.

This split ("where the data come from" / "what to do with them" / "how they look") is the best habit you can take
away from the project. When a wrong number shows up somewhere, you know exactly which of the three files to look in.

A note on time: we store time in UTC, but for a person we convert it to local time with the modifier 'localtime'. "The
most active hour" must match the hours on the clock on the wall, not in Greenwich.

The package by topic (jellyscope/stats/), in the order of dependencies - a module imports only modules above it:

* `periods` - The time windows of the statistics: the period, its bounds and the previous period for comparing.
* `kinds` - Kinds of content (movies, series, other) and what item types are called.
* `playback` - What is playing right now and the description of a transcode.
* `recent` - Recently added titles and batches of episodes.
* `titles` - Grouping plays into titles (a series, a movie) and the most watched titles.
* `viewers` - Viewers: summaries by viewer, the viewer table, a viewer's detail.
* `play_history` - The play history: filters, the page and the API.
* `tech` - The library's technique: resolution, codecs, dynamic range.
* `library_page` - The library by titles: rows, a series' detail, the tiles and the library's activity.
* `item_detail` - The detail of one item: versions, streams, plays, siblings, deleting.
* `archive` - The archive: archived items, searching among the existing ones and assigning to another title.
* `network` - The network: how much flowed to the players, the peaks and the live flow.
* `locations` - Where people watch from: the home network or the internet, the viewers' places and countries.
* `content_history` - Other content kinds (music) in the history of the library: computed days and old snapshots.
* `disks` - The library's disks: the pace of filling and in how many days the space runs out.
* `growth` - The library's state and growth over time (snapshots, the backfilled curve).
* `overview_page` - The period's summary: the overview, comparing two periods, watching by day, play methods, players, the heatmap.
* `shared` - Calculations that know no app: time in the zone, the quantile, pairing grabs with imports.
* `sonarr` - Sonarr's statistics over the downloaded history.
* `lidarr` - Lidarr's statistics over the downloaded history (the unit is the album).
* `readarr` - Readarr's statistics over the downloaded history (the unit is the book).
* `radarr` - Radarr's statistics over the downloaded history.
* `bazarr` - Subtitle statistics from Bazarr over the downloaded history.
* `music_listening` - Music statistics: listening, rankings and technique.
* `music_comparison` - Two periods of listening side by side (Music's comparison).
* `music_library` - Statistics of the music library and the data of the artist, album and viewer pages.
* `books_listening` - Books statistics: listening to audiobooks, reading e-books and their rankings.
* `books_library` - Statistics of the books library and the data of the book page and the books library.
* `books_comparison` - Two periods of reading and listening side by side (Books' comparison).
* `prowlarr` - Prowlarr's statistics: queries to the indexers, the indexers' performance and availability and grabbed
  releases.
* `seerr` - Seerr's statistics over the stored requests.

From outside it is called through this door (`stats.top_items(...)`, `from .stats import _bounds`): it exposes the names
code and tests outside the package read, a name only the package itself uses stays in its submodule (both ways guarded by
tests/test_stats_package.py, check a). Tests that substitute a name and expect the effect inside the package substitute
it in the submodule (check e)."""

from __future__ import annotations

from .periods import (
    END_OF_TIME,
    NO_LIMIT,
    OLDEST_HISTORY,
    Period,
    _as_period,
    _bounds,
    can_compare,
    chart_days,
    first_record,
    period_between,
    period_from_instants,
    period_of_days,
    previous_period,
    transcode_condition,
    whole_history_period,
)
from .kinds import (
    ALLOWED_KINDS,
    HISTORY_KINDS,
    KIND_ALL,
    KIND_BOOKS,
    KIND_BOTH,
    KIND_MOVIE,
    KIND_MUSIC,
    KIND_OTHER,
    KIND_SERIES,
    TYPES_OF_KIND,
    by_titles,
    type_label,
    watching_only,
)
from .playback import (
    active_session_count,
    active_sessions,
    plays_state,
    transcode_speed,
)
from .recent import (
    recently_added,
)
from .titles import (
    TITLE_GROUP_COLUMNS,
    TITLE_GROUP_KEY,
    _episodes_by_name,
    _merge_titles,
    extra_copies,
    series_from_name,
    title_key,
    top_items,
)
from .viewers import (
    VIEWER_GROUP,
    VIEWER_NAME,
    top_users,
    user_detail,
    user_genres,
    user_table,
)
from .play_history import (
    PLAY_METHODS,
    filter_values,
    history,
    history_count,
    history_for_api,
    history_last_id,
)
from .tech import (
    RESOLUTION_CASE,
    codec_breakdown,
    item_range,
    resolution_breakdown,
    tech_coverage,
    video_range_breakdown,
)
from .library_page import (
    _PLAYS,
    library,
    library_activity,
    library_cards,
    library_overview,
    library_rows,
    library_rows_count,
    series_detail,
)
from .item_detail import (
    delete_item,
    item,
    item_playback,
    item_playback_summary,
    item_streams,
    item_versions,
    sibling_episodes,
    title_files,
    title_items,
)
from .archive import (
    AssignmentError,
    _ALL_PLAYS,
    archived_count,
    archived_items,
    assign_to_other,
    delete_from_archive,
    find_existing,
    only_archived,
)
from .network import (
    MAX_LIVE_POINTS,
    _seconds,
    bandwidth_by,
    bandwidth_daily_peaks,
    bandwidth_live,
    bandwidth_summary,
    bandwidth_timeline,
    flow_now,
    network_flows,
)
from .locations import (
    hotspots,
    is_home,
    places,
    viewer_countries,
    watching_locations,
)
from .disks import (
    disk_snapshots,
    disks_overview,
    disks_to_choose,
    outside_series,
    runs_out_first,
)
from .growth import (
    _backfilled_curve,
    disk_growth,
    disk_growth_memo,
    disk_growth_series,
    library_growth,
    library_state,
    snapshots,
    space_forecast,
)
from .overview_page import (
    client_breakdown,
    comparison,
    daily_activity_split,
    hourly_heatmap,
    others_breakdown,
    overview,
    play_method_breakdown,
    previous_overview,
)
from .lidarr import (lidarr_additions, lidarr_events, lidarr_failures, lidarr_first, lidarr_heatmap,
                     lidarr_indexer_reliability, lidarr_largest, lidarr_library, lidarr_sources, lidarr_speed,
                     lidarr_upgrades)
from .readarr import (readarr_additions, readarr_events, readarr_failures, readarr_first, readarr_heatmap,
                      readarr_indexer_reliability, readarr_largest, readarr_library, readarr_sources, readarr_speed,
                      readarr_upgrades)
from .sonarr import (sonarr_additions, sonarr_detail_links, sonarr_events, sonarr_failures, sonarr_first,
                     sonarr_heatmap, sonarr_imports, sonarr_indexer_reliability, sonarr_largest, sonarr_library,
                     sonarr_sources, sonarr_speed, sonarr_upgrades)
from .radarr import (
    radarr_additions,
    radarr_detail_links,
    radarr_events,
    radarr_failures,
    radarr_first,
    radarr_heatmap,
    radarr_imports,
    radarr_indexer_reliability,
    radarr_largest,
    radarr_library,
    radarr_sources,
    radarr_speed,
    radarr_upgrades,
)
from .bazarr import (
    bazarr_actions,
    bazarr_analysis,
    bazarr_by_day,
    bazarr_changed,
    bazarr_first,
    bazarr_heatmap,
    bazarr_languages,
    bazarr_numbers,
    bazarr_providers,
    bazarr_scores,
    bazarr_speed,
    bazarr_upgrades,
)
from .music_listening import (
    PLAY_FROM_SECONDS,
    music_albums,
    music_analysis,
    music_apps,
    music_artists,
    music_by_day,
    music_first,
    music_genres,
    music_heatmap,
    music_listeners,
    music_network,
    music_numbers,
    music_play_method,
    music_tracks,
)
from .music_comparison import music_comparison
from .music_library import (
    LOSSLESS,
    library_genres,
    library_tracks,
    music_artist,
    music_decades,
    music_formats,
    music_largest,
    music_library_summary,
    music_newest,
    music_quality,
    music_unplayed,
    music_viewer,
)
from .books_listening import (
    books_analysis, books_apps, books_authors, books_by_day, books_first, books_genres, books_heatmap,
    books_numbers, books_play_method, books_readers, books_works, reading_reported
)
from .books_comparison import books_comparison
from .books_library import (
    book_detail, books_decades, books_formats, books_largest, books_library_genres, books_library_summary,
    books_newest, books_unread, books_library_works
)
from .prowlarr import (
    prowlarr_analysis,
    prowlarr_availability,
    prowlarr_by_day,
    prowlarr_first,
    prowlarr_grabbed,
    prowlarr_grabbed_indexers,
    prowlarr_grabbed_latest,
    prowlarr_heatmap,
    prowlarr_indexer_names,
    prowlarr_indexers,
    prowlarr_numbers,
    prowlarr_who,
)
from .seerr import (
    seerr_by_day,
    seerr_events,
    seerr_first,
    seerr_handling,
    seerr_heatmap,
    seerr_latest,
    seerr_posters,
    seerr_requesters,
    seerr_summary,
    seerr_synced_at,
    seerr_watching,
    seerr_with_live,
    seerr_with_posters,
)

__all__ = [
    "ALLOWED_KINDS", "END_OF_TIME",
    "HISTORY_KINDS", "KIND_ALL", "KIND_BOOKS", "KIND_BOTH", "KIND_MOVIE",
    "KIND_MUSIC", "KIND_OTHER", "KIND_SERIES", "LOSSLESS", "MAX_LIVE_POINTS",
    "NO_LIMIT", "OLDEST_HISTORY", "PLAY_FROM_SECONDS", "PLAY_METHODS",
    "RESOLUTION_CASE", "TITLE_GROUP_COLUMNS",
    "TITLE_GROUP_KEY", "TYPES_OF_KIND", "VIEWER_GROUP", "VIEWER_NAME", "_ALL_PLAYS",
    "_PLAYS",
    "AssignmentError", "Period", "_as_period",
    "_backfilled_curve", "_bounds",
    "_episodes_by_name",
    "_merge_titles", "_seconds",
    "active_session_count",
    "active_sessions", "archived_count", "archived_items", "assign_to_other", "bandwidth_by",
    "bandwidth_daily_peaks", "bandwidth_live", "bandwidth_summary", "bandwidth_timeline", "bazarr_actions",
    "bazarr_analysis", "bazarr_by_day", "bazarr_changed", "bazarr_first", "bazarr_heatmap", "bazarr_languages",
    "bazarr_numbers", "bazarr_providers", "bazarr_scores", "bazarr_speed", "bazarr_upgrades", "book_detail",
    "books_analysis", "books_apps", "books_authors", "books_by_day", "books_comparison", "books_decades", "books_first",
    "books_formats", "books_genres", "books_heatmap", "books_largest", "books_library_genres",
    "books_library_summary", "books_library_works", "books_newest", "books_numbers", "books_play_method", "books_readers", "books_unread",
    "books_works", "by_titles", "can_compare", "chart_days", "client_breakdown", "codec_breakdown",
    "comparison", "daily_activity_split", "delete_from_archive", "delete_item", "disk_growth", "disk_growth_memo", "disk_growth_series",
    "disk_snapshots", "disks_overview", "disks_to_choose", "extra_copies", "filter_values",
    "find_existing", "first_record", "flow_now", "history", "history_count", "history_for_api", "history_last_id", "hotspots",
    "hourly_heatmap", "is_home", "item", "item_playback", "item_playback_summary", "item_range",
    "item_streams", "item_versions", "library", "library_activity",
    "library_cards", "library_genres", "library_growth", "library_overview", "library_rows", "library_rows_count",
    "library_state", "library_tracks", "music_albums", "music_analysis", "music_apps",
    "music_artist", "music_artists", "music_by_day", "music_comparison", "music_decades", "music_first",
    "music_formats", "music_genres", "music_heatmap", "music_largest", "music_library_summary", "music_listeners",
    "music_network", "music_newest", "music_numbers", "music_play_method", "music_quality", "music_tracks",
    "music_unplayed", "music_viewer", "network_flows", "only_archived", "others_breakdown",
    "outside_series", "overview", "period_between", "period_from_instants", "period_of_days", "places",
    "play_method_breakdown", "plays_state", "previous_overview", "previous_period", "prowlarr_analysis", "prowlarr_availability",
    "prowlarr_by_day", "prowlarr_first", "prowlarr_grabbed", "prowlarr_grabbed_indexers", "prowlarr_grabbed_latest",
    "prowlarr_heatmap", "prowlarr_indexer_names", "prowlarr_indexers", "prowlarr_numbers", "prowlarr_who",
    "radarr_additions", "radarr_detail_links", "radarr_events", "radarr_failures", "radarr_first", "radarr_heatmap",
    "radarr_imports", "radarr_indexer_reliability", "radarr_largest", "radarr_library", "radarr_sources", "radarr_speed",
    "radarr_upgrades",
    "readarr_additions", "readarr_events", "readarr_failures", "readarr_first", "readarr_heatmap",
    "readarr_indexer_reliability", "readarr_largest", "readarr_library", "readarr_sources", "readarr_speed",
    "readarr_upgrades",
    "reading_reported", "recently_added", "resolution_breakdown", "runs_out_first", "seerr_by_day", "seerr_events",
    "seerr_first", "seerr_handling", "seerr_heatmap", "seerr_latest", "seerr_posters", "seerr_requesters",
    "seerr_summary", "seerr_synced_at", "seerr_watching", "seerr_with_live", "seerr_with_posters", "series_detail",
    "series_from_name",
    "sibling_episodes", "snapshots", "space_forecast", "lidarr_additions", "lidarr_events", "lidarr_failures", "lidarr_first",
    "lidarr_heatmap", "lidarr_indexer_reliability", "lidarr_largest", "lidarr_library", "lidarr_sources", "lidarr_speed",
    "lidarr_upgrades",
    "sonarr_additions", "sonarr_detail_links", "sonarr_events", "sonarr_failures", "sonarr_first", "sonarr_heatmap",
    "sonarr_imports", "sonarr_indexer_reliability", "sonarr_largest", "sonarr_library", "sonarr_sources", "sonarr_speed",
    "sonarr_upgrades",
    "tech_coverage", "title_files", "title_items", "title_key", "top_items", "top_users", "transcode_condition",
    "transcode_speed", "type_label", "user_detail", "user_genres", "user_table",
    "video_range_breakdown", "viewer_countries", "watching_locations", "watching_only", "whole_history_period",
]
