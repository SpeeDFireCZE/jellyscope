"""The library sync and the technical analysis of files.

Two tasks that run by hand from Settings or now and then by themselves:

1. `sync_library()` - downloads the list of users, libraries and items from Jellyfin.
2. `run_tech_scan()` - fills in technical data at the items (codec, bitrate, ...).

For the second task the user picks the source in the settings:
  * "jellyfin" - the data Jellyfin knows already. Fast, always works, less precise.
  * "ffprobe"  - reading the files on the disk. Precise, but needs access to the files.

The package by areas (jellyscope/scanner/). From outside it is called as before (`scanner.sync_library()`,
`scanner.is_scan_running()`): this file offers everything that used to be in one scanner.py.

* `item_types` - the kinds of items Jellyscope keeps (a movie, an episode) and the wrappers that have no file.
* `task_state` - running tasks: the lock, stopping, progress and the record of a task (scan_log).
* `free_space` - free space on the disk (from Jellyfin or from the capacity) and the library's daily snapshot.
* `merging` - merging by TMDB, duplicates of new items, moving history, the archive.
* `stream_languages` - filling in the languages of streams from Jellyfin and from file names.
* `copies` - copies of the same movie in one library: which item stands for the record.
* `disks` - the library's disks: device ids, virtual disks, files by disk.
* `disk_settings` - what the admin changes in the Disks card.
* `disk_snapshots` - the daily disk snapshot and the library's one free-space number.
* `writing` - writing items, versions, images and streams; merging the archive into live items; phantoms.
* `tech_scan` - the technical analysis of files (ffprobe or Jellyfin) and the dynamic range.
* `sync` - the sync of the whole library: users, libraries and items from Jellyfin.
* `refresh` - refreshing one item and a series, recent changes and their review.

Tests that substitute a name and expect the effect inside the package use substitute() from tests/_package.py:
it substitutes the name in every submodule (tests/test_scanner_package.py, check e).

Scheduling these tasks is not here but in tasks.py - shared by every scheduled activity. This package can do the
tasks; when they are to be done is decided by someone else. The same split as "what" versus "when" with an alarm
clock.
"""
from __future__ import annotations

from ..jellyfin import JellyfinClient  # noqa: F401 - tests substitute it as scanner.JellyfinClient
from .item_types import (
    WRAPPER_TYPES,
    MANAGED_TYPES,
    WRITTEN_TYPES,
    NOT_YET_CLASSIFIED,
    is_wrapper,
)
# _stop_requested (the Stop flag) is not offered outside: it is a bool and an import would make a copy
# that never changes. It is asked through stop_requested().
from .task_state import (
    RECORDS_PER_KIND,
    _add_progress,
    _clear_progress,
    _clear_stop,
    _progress,
    _thin_records,
    _scan_lock,
    _start_progress,
    finish_task_log,
    is_scan_running,
    last_scan,
    library_version,
    versions,
    progress,
    request_stop,
    start_task_log,
    stop_requested,
    tasks_version,
    task_lock,
)
from .free_space import (
    JF_TOTAL_KEY,
    JF_FREE_KEY,
    CAPACITY_KEY,
    SOURCE_KEY,
    _number,
    _library_free_space,
    _free_from_capacity,
    space_from_jellyfin,
    remember_space_from_jellyfin,
    write_snapshot,
)
from .merging import (
    _merge_by_tmdb,
    _by_name,
    _by_tmdb,
    _equal_or_empty,
    _old_to_merge,
    _select,
    archive,
    duplicates_of_new,
    move_history,
)
from .stream_languages import (
    _fill_item_languages,
    _fill_item_from_name,
    _recount_item_languages,
    _split_by_name,
    _pair_streams,
    fill_languages_from_jellyfin,
    fill_languages_from_name,
    candidates_for_language_from_name,
)
from .copies import recount_copies, recount_copies_after_sync
from .disks import (
    DISK_CAPACITY_KEY,
    CONVERTED_KEY,
    DIFFERENT_KEY,
    FOLDERS_TIME_KEY,
    FOLDERS_KEY,
    MAX_AGE_DAYS,
    SAME_KEY,
    VIRTUAL_KEY,
    disk_paths,
    physical_disks,
    is_inside,
    normalize_path,
    suspicions,
    convert_old_capacity,
    file_assignment,
    folders_from_jellyfin,
    files_by_disk,
    disk_state,
    virtual_disks,
    remember_folders,
    stale_since,
)
from .disk_settings import (
    set_capacity,
    add_virtual,
    decide_same,
    delete_virtual,
    edit_virtual,
    undo_decision,
)
from .disk_snapshots import free_space_now, write_disk_snapshot, write_daily_snapshots
from .writing import (
    _ID_COLUMN,
    _IMAGE_TAG_COLUMN,
    _SERIES_IMAGE_TAG_COLUMN,
    _SERIES_COLUMN,
    _mark_missing,
    _items_with_new_image,
    _write_batch,
    _write_items,
    _forget_images,
    save_streams,
    merge_archive_into_live,
    archive_overview,
    clean_up_phantoms,
    write_versions,
)
from .tech_scan import (
    _missing_language,
    _save_probe_result,
    _save_tech,
    _save_tech_error,
    fill_range_from_jellyfin,
    candidates_for_range_from_jellyfin,
    run_tech_scan,
)
from .sync import (
    _already_known,
    _item_row,
    _sync_items_of_library,
    _sync_libraries,
    _sync_users,
    _save_new_items,
    item_identity,
    sync_library,
    tmdb_id_of,
)
from .refresh import (
    RECENT_OVERLAP_MINUTES,
    REVIEW_CAP,
    _successors_by_path,
    _last_added,
    _review_recent,
    _recent_message,
    refresh_item,
    refresh_series,
    straighten_after_new,
    sync_recent,
)

__all__ = [
    "CAPACITY_KEY",
    "CONVERTED_KEY",
    "DIFFERENT_KEY",
    "DISK_CAPACITY_KEY",
    "FOLDERS_KEY",
    "FOLDERS_TIME_KEY",
    "JF_FREE_KEY",
    "JF_TOTAL_KEY",
    "JellyfinClient",
    "MANAGED_TYPES",
    "MAX_AGE_DAYS",
    "NOT_YET_CLASSIFIED",
    "RECENT_OVERLAP_MINUTES",
    "RECORDS_PER_KIND",
    "REVIEW_CAP",
    "SAME_KEY",
    "SOURCE_KEY",
    "VIRTUAL_KEY",
    "WRAPPER_TYPES",
    "WRITTEN_TYPES",
    "_ID_COLUMN",
    "_IMAGE_TAG_COLUMN",
    "_SERIES_COLUMN",
    "_SERIES_IMAGE_TAG_COLUMN",
    "_add_progress",
    "_already_known",
    "_by_name",
    "_by_tmdb",
    "_clear_progress",
    "_clear_stop",
    "_equal_or_empty",
    "_fill_item_from_name",
    "_fill_item_languages",
    "_forget_images",
    "_free_from_capacity",
    "_item_row",
    "_items_with_new_image",
    "_last_added",
    "_library_free_space",
    "_mark_missing",
    "_merge_by_tmdb",
    "_missing_language",
    "_number",
    "_old_to_merge",
    "_pair_streams",
    "_progress",
    "_recent_message",
    "_recount_item_languages",
    "_review_recent",
    "_save_new_items",
    "_save_probe_result",
    "_save_tech",
    "_save_tech_error",
    "_scan_lock",
    "_select",
    "_split_by_name",
    "_start_progress",
    "_successors_by_path",
    "_sync_items_of_library",
    "_sync_libraries",
    "_sync_users",
    "_thin_records",
    "_write_batch",
    "_write_items",
    "add_virtual",
    "archive",
    "archive_overview",
    "candidates_for_language_from_name",
    "candidates_for_range_from_jellyfin",
    "clean_up_phantoms",
    "convert_old_capacity",
    "decide_same",
    "delete_virtual",
    "disk_paths",
    "disk_state",
    "duplicates_of_new",
    "edit_virtual",
    "file_assignment",
    "files_by_disk",
    "fill_languages_from_jellyfin",
    "fill_languages_from_name",
    "fill_range_from_jellyfin",
    "finish_task_log",
    "folders_from_jellyfin",
    "free_space_now",
    "is_inside",
    "is_scan_running",
    "is_wrapper",
    "item_identity",
    "last_scan",
    "library_version",
    "merge_archive_into_live",
    "move_history",
    "normalize_path",
    "physical_disks",
    "progress",
    "recount_copies",
    "recount_copies_after_sync",
    "refresh_item",
    "refresh_series",
    "remember_folders",
    "remember_space_from_jellyfin",
    "request_stop",
    "run_tech_scan",
    "save_streams",
    "set_capacity",
    "space_from_jellyfin",
    "stale_since",
    "start_task_log",
    "stop_requested",
    "straighten_after_new",
    "suspicions",
    "sync_library",
    "sync_recent",
    "task_lock",
    "tasks_version",
    "tmdb_id_of",
    "undo_decision",
    "versions",
    "virtual_disks",
    "write_daily_snapshots",
    "write_disk_snapshot",
    "write_snapshot",
    "write_versions",
]
