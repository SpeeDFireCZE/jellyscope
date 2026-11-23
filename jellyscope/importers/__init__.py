"""The history import from other tools.

Jellyscope records plays only from the moment it runs. When you already have a history somewhere, there
is no point starting from zero - it can be taken over.

We support two sources:

1. **Playback Reporting** - the official Jellyfin plugin. It keeps its data in its own table and offers it
   through an API one can send an SQL query to. So we need not touch any file; it is enough to ask
   Jellyfin, as with everything else. (Jellystat does it the same way.)

2. **Jellystat** - its backup can be exported to JSON. You upload the file in the form and we take the
   table of plays out of it.

## How a repeated import is handled

Duplicates are guarded **twice**, because one guard is not enough.

**1. The record's key.** Every row taken over gets a key that describes it at the source -
`import:pbr:<rowid>:<item>` or `import:jst:<id>:<item>`. Before inserting it is checked that no such key
is in the database yet. Thanks to that the import is **idempotent**: you may upload the same file ten
times and nothing doubles.

**2. A match by content.** The key holds only within one source, though. The same history read from
Jellystat and from Playback Reporting has two different keys, so it would double whole - and the
collector's own records have no import key at all. So the content is compared too: the same user, the
same item and **an overlapping time** mean the same record.

An overlap and not the same start - every tool writes the time a little differently and an exact match
would let most duplicates through. See `_already_there()`.

## The package by areas

From outside it is called as before (`importers.tidy_data()`, `importers.import_jellystat_json(...)`):
this file offers everything that used to be in one importers.py (jellyscope/importers/):

* `base` - the import's shared helpers: time, record keys, the guard "we only read from Jellyfin",
  building and writing a row, splitting an episode title.
* `matching` - the second guard against duplicates: a match by content (user, item, overlapping time).
* `creation` - creating missing items from Jellyfin (episodes under a series, libraries by path).
* `linking` - assigning imported history to items: id, TMDB, name, episode number.
* `pbr` - the import from Playback Reporting (a Jellyfin plugin) - through the API and from a TSV file.
* `jellystat` - the import from a Jellystat backup (JSON).
* `cleanup` - cleaning duplicates in our own history and capping nonsense lengths.
* `duplicates` - duplicates between two import sources and episodes under a wrong series.
* `orphans` - records without an item: counts, stale names, looking up in Jellyfin, analysis and manual
  assignment.
* `tidy` - tidying the data (every repair in a row) and the import summary.

Tests that substitute a name and expect the effect inside the package use substitute() from
tests/_package.py (tests/test_importers_package.py, check e).
"""
from __future__ import annotations

from ..jellyfin import JellyfinClient  # noqa: F401 - tests substitute it as importers.JellyfinClient
from .base import (
    FORBIDDEN_WORDS,
    ImportError_,
    LANGUAGE_COLUMNS,
    _EPISODE_TITLE,
    _build_row,
    _existing_keys,
    _insert,
    _language_from_source,
    _normalize_id,
    _parse_time,
    read_only,
    split_episode_title,
)
from .matching import (
    _already_there,
    _epoch,
    _epoch_slow,
    _known_items,
    _known_users,
    _playback_index,
    _remember,
)
from .creation import (
    _episode_in_library,
    _episodes_under_series,
    _libraries_by_path,
    _library_for,
    create_from_jellyfin,
)
from .linking import (
    _link_by_episode_number,
    _link_by_name,
    _link_by_tmdb,
    _orphan_item_ids,
    _orphan_records,
    _relink,
    _unify_ids,
    link_imported_history,
    refresh_playback_metadata,
)
from .pbr import (
    PBR_QUERY,
    PBR_QUERY_WITHOUT_ROWID,
    PBR_TSV_COLUMNS,
    _process_pbr,
    _tsv_columns,
    import_playback_reporting,
    import_playback_reporting_tsv,
    playback_reporting_available,
)
from .jellystat import (
    JELLYSTAT_TABLES,
    _find_jellystat_rows,
    _jellystat_item_type,
    _pick,
    import_jellystat_json,
)
from .cleanup import (
    CAP_MULTIPLE,
    cap_nonsense_durations,
    duplicate_playback_count,
    duplicate_playback_groups,
    merge_duplicate_playback,
)
from .duplicates import (
    LENGTH_TOLERANCE_S,
    MAX_GAP_S,
    _belong_together,
    _completeness,
    _import_exists,
    _title_for_comparison,
    import_duplicate_count,
    import_duplicate_groups,
    merge_import_duplicates,
    misplaced_episode_rows,
)
from .orphans import (
    ANALYSIS_LIMIT,
    LOOKUP_LIMIT,
    REASON_ID,
    REASON_KNOWS_SERIES,
    REASON_LABELS,
    REASON_NOTHING,
    REASON_NO_SERIES,
    REASON_ONE_MATCH,
    REASON_SERIES_EXISTS,
    REASON_SEVERAL_MATCHES,
    analyse_orphans,
    assign_manually,
    candidates_for_orphan,
    look_up_orphans_in_jellyfin,
    orphan_items_count,
    orphan_playback_count,
    relink_orphans,
    repair_episode_links,
    stale_name_rows,
    unify_names,
)
from .tidy import (
    import_summary,
    tidy_data,
)

__all__ = [
    "ANALYSIS_LIMIT",
    "CAP_MULTIPLE",
    "FORBIDDEN_WORDS",
    "ImportError_",
    "JELLYSTAT_TABLES",
    "JellyfinClient",
    "LANGUAGE_COLUMNS",
    "LENGTH_TOLERANCE_S",
    "LOOKUP_LIMIT",
    "MAX_GAP_S",
    "PBR_QUERY",
    "PBR_QUERY_WITHOUT_ROWID",
    "PBR_TSV_COLUMNS",
    "REASON_ID",
    "REASON_KNOWS_SERIES",
    "REASON_LABELS",
    "REASON_NOTHING",
    "REASON_NO_SERIES",
    "REASON_ONE_MATCH",
    "REASON_SERIES_EXISTS",
    "REASON_SEVERAL_MATCHES",
    "_EPISODE_TITLE",
    "_already_there",
    "_belong_together",
    "_build_row",
    "_completeness",
    "_episode_in_library",
    "_episodes_under_series",
    "_epoch",
    "_epoch_slow",
    "_existing_keys",
    "_find_jellystat_rows",
    "_import_exists",
    "_insert",
    "_jellystat_item_type",
    "_known_items",
    "_known_users",
    "_language_from_source",
    "_libraries_by_path",
    "_library_for",
    "_link_by_episode_number",
    "_link_by_name",
    "_link_by_tmdb",
    "_normalize_id",
    "_orphan_item_ids",
    "_orphan_records",
    "_parse_time",
    "_pick",
    "_playback_index",
    "_process_pbr",
    "_relink",
    "_remember",
    "_title_for_comparison",
    "_tsv_columns",
    "_unify_ids",
    "analyse_orphans",
    "assign_manually",
    "candidates_for_orphan",
    "cap_nonsense_durations",
    "create_from_jellyfin",
    "duplicate_playback_count",
    "duplicate_playback_groups",
    "import_duplicate_count",
    "import_duplicate_groups",
    "import_jellystat_json",
    "import_playback_reporting",
    "import_playback_reporting_tsv",
    "import_summary",
    "link_imported_history",
    "look_up_orphans_in_jellyfin",
    "merge_duplicate_playback",
    "merge_import_duplicates",
    "misplaced_episode_rows",
    "orphan_items_count",
    "orphan_playback_count",
    "playback_reporting_available",
    "read_only",
    "refresh_playback_metadata",
    "relink_orphans",
    "repair_episode_links",
    "split_episode_title",
    "stale_name_rows",
    "tidy_data",
    "unify_names",
]
