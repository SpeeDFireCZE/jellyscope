"""Content outside video: where the files of music (and later books) live.

Disks, the total size and the daily snapshots used to count only `items` (movies and episodes).
Other kinds of content keep their files in tables of their own; this registry says which table
and which columns, so that those places read every kind the same way instead of writing their
own SQL for each. A new kind (books) is one more `ContentKind` - and a sync that fills the columns.

Spec: docs/superpowers/specs/2026-11-16-content-kinds-size-design.md.
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from . import db


@dataclass(frozen=True)
class ContentKind:
    """One kind of content outside `items` and the columns of its files."""

    key: str        # in the `kinds` JSON of snapshots
    label: str      # translation key of its name ("Music")
    share: str      # translation key of "of it" with {size} ("of which music {size}")
    table: str
    path: str       # file path; NULL or '' = unknown, such a file is on no disk
    size: str       # bytes; NULL = unknown
    added: str      # when the file came, UTC text (the day is its first 10 characters)
    last_seen: str  # the last sync that saw it - for a missing one, the day it went
    missing: str    # 1 = no longer in Jellyfin
    first_sync_key: str = ""    # a setting set once the kind's first sync ran; with it, an empty table is "measured"


KINDS: tuple[ContentKind, ...] = (
    ContentKind("music", "Music", "of which music {size}", "music_track", path="path",
                size="size_bytes", added="added_at", last_seen="synced_at", missing="missing"),
    ContentKind("books", "Books", "of which books {size}", "book", path="path",
                size="size_bytes", added="added_at", last_seen="synced_at", missing="missing",
                first_sync_key="books_first_sync"),
)
BY_KEY: dict[str, ContentKind] = {kind.key: kind for kind in KINDS}


def kinds_with_paths() -> set[str]:
    """Kinds whose files in Jellyfin all have their path asked for (NULL = not asked yet).

    Right after the upgrade tracks have none until the first full music sync. A kind counts on
    disks only once the sync asked for every path - partly known, a disk would measure a part of
    it, and a snapshot would store that as the kind's measurement (final review 16. 11. 2026).
    A kind with a `first_sync_key` (books) counts only once something is stored or its first sync ran: before that
    an empty table is "not measured yet", not "0 bytes" - otherwise the first snapshot would store 0 and the growth
    history would jump later.
    """
    return {kind.key for kind in KINDS
            if not db.query_value(f"SELECT 1 FROM {kind.table} WHERE {kind.missing} = 0 AND {kind.path} IS NULL LIMIT 1",
                                  default=None)
            and (not kind.first_sync_key or db.get_setting(kind.first_sync_key, "")
                 or db.query_value(f"SELECT 1 FROM {kind.table} LIMIT 1", default=None))}


def current_files() -> Iterator[tuple[str, str, int]]:
    """(kind, path, size or 0) of files in Jellyfin that have a path - for the Disks card.

    Only kinds whose paths are all known (kinds_with_paths); until then the kind stays in
    "outside Jellyfin". Straight from the cursor: tens of thousands of tracks would make a dict
    per row noticeable.
    """
    known = kinds_with_paths()
    for kind in KINDS:
        if kind.key not in known:
            continue
        with db.connect() as conn:
            for row in conn.execute(
                    f"SELECT {kind.path} AS path, COALESCE({kind.size}, 0) AS size FROM {kind.table}"
                    f" WHERE {kind.missing} = 0 AND {kind.path} IS NOT NULL AND {kind.path} != ''"):
                yield kind.key, str(row["path"]), int(row["size"] or 0)


def totals() -> dict[str, dict[str, int]]:
    """{kind: {"items", "size", "without_size"}} of files in Jellyfin; a kind with none has zeros."""
    result: dict[str, dict[str, int]] = {}
    for kind in KINDS:
        row = db.query_one(
            f"SELECT COUNT(*) AS items, COALESCE(SUM(COALESCE({kind.size}, 0)), 0) AS size,"
            f"       SUM(CASE WHEN {kind.size} IS NULL OR {kind.size} = 0 THEN 1 ELSE 0 END) AS without_size"
            f"  FROM {kind.table} WHERE {kind.missing} = 0") or {}
        result[kind.key] = {name: int(row.get(name) or 0) for name in ("items", "size", "without_size")}
    return result


def fingerprint() -> str:
    """A cheap summary of every kind's files that changes whenever they do.

    A sync rewrites the last-seen time of every file it saw; a path, a size, a day of adding or a
    file going changes a sum. The costly history (below) is computed again only when this changes
    (stats/content_history.py).
    """
    parts = []
    for kind in KINDS:
        row = db.query_one(
            f"SELECT COUNT(*) AS files, COALESCE(SUM({kind.size}), 0) AS size, COALESCE(SUM({kind.missing}), 0) AS missing,"
            f"       MAX({kind.last_seen}) AS last_seen, COALESCE(SUM(LENGTH({kind.last_seen})), 0) AS last_seen_len,"
            f"       COALESCE(SUM(LENGTH({kind.added})), 0) AS added_len, COALESCE(SUM(LENGTH({kind.path})), 0) AS path_len,"
            f"       SUM(CASE WHEN {kind.path} IS NULL THEN 1 ELSE 0 END) AS no_path"
            f"  FROM {kind.table}") or {}
        parts.append(json.dumps(row, sort_keys=True, default=str))
    return "|".join(parts)


def history() -> list[dict[str, Any]]:
    """Files of every kind for computing the past: {"kind", "added", "gone", "size", "path"}.

    `added` and `gone` are days (YYYY-MM-DD, UTC like the video backfill); `gone` only for a file
    no longer in Jellyfin, "" otherwise. A file without a day of adding, or gone on an unknown
    day, is left out - kept in, it would stay in the curve forever (stats.growth._backfilled_curve).
    """
    rows: list[dict[str, Any]] = []
    for kind in KINDS:
        for row in db.query_all(
                f"SELECT substr({kind.added}, 1, 10) AS added, {kind.missing} AS missing,"
                f"       substr({kind.last_seen}, 1, 10) AS last_seen,"
                f"       COALESCE({kind.size}, 0) AS size, COALESCE({kind.path}, '') AS path"
                f"  FROM {kind.table} WHERE {kind.added} IS NOT NULL AND {kind.added} != ''"):
            added, last_seen = str(row["added"] or ""), str(row["last_seen"] or "")
            gone = last_seen if int(row["missing"] or 0) else ""
            if len(added) != 10 or (int(row["missing"] or 0) and len(gone) != 10):
                continue
            rows.append({"kind": kind.key, "added": added, "gone": gone,
                         "size": int(row["size"] or 0), "path": str(row["path"])})
    return rows
