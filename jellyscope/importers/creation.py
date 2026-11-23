"""Creating missing items from Jellyfin (episodes under a series, libraries by path).

Part of the package importers (see jellyscope/importers/__init__.py)."""
from __future__ import annotations

import json
import logging
from typing import Any

from .. import db, scanner
from ..jellyfin import extract_streams as jellyfin_streams
from ..jellyfin import extract_tech_from_item as jellyfin_tech
from ..stats.kinds import video_library_sql
from .base import split_episode_title

log = logging.getLogger("jellyscope.importers")


def create_from_jellyfin(found: list[dict[str, Any]]) -> dict[str, int]:
    """Make from Jellyfin's answer what the orphan records lack.

    Two steps for every item:

      1. **We have the same episode in the library already, only under another ItemId** (the file was
         re-encoded, the library rescanned) - the records are rewritten to it and so get a link to the
         right detail.
      2. **It is not in the library, but Jellyfin knows it - we create it.** This is the essential part:
         the id in the imported history **is** the Jellyfin id, so as soon as the item exists, the records
         point at it by themselves. And with the item come the tmdb_id, the series, the episode numbers
         and the technical data, so everything else starts working - merging by tmdb, filing under the
         series, statistics, the link.

    An item without a library is no tragedy: the first full sync fills it in, since it knows which
    library the item came from. And when it disappears from Jellyfin meanwhile, it is marked as archived
    - like any other.

    **Only a movie or an episode is created** (`scanner.MANAGED_TYPES`). When a record hangs on a
    SERIES' id - and that happens with imported history - Jellyfin answers with an item of the kind
    "Series". We must not create such one: the sync asks only for movies and episodes, it would never see
    this item again and `_mark_missing()` would send it to the archive on every run. An "archived" series
    would then haunt the library, while it is in Jellyfin as normal (and beside it the right one, put
    together from its episodes).

    Instead of creating, at least the series' **name** is taken and filled into the history records. The
    episode then stops posing as a separate movie and merges under its series on the overviews - even
    when it cannot be linked to the particular episode.
    """
    libraries = _libraries_by_path()
    use_jellyfin_tech = db.get_setting("tech_source") == "jellyfin"

    linked = created = filled = rows = 0
    for item in found:
        item_id = str(item.get("Id") or "")
        if not item_id:
            continue

        # How many records still hang on that id. When none, there is nothing to deal with - Jellyfin may
        # also return an item someone else linked meanwhile, and creating it for nothing would be worse than
        # doing nothing.
        waiting = int(db.query_value(
            "SELECT COUNT(*) FROM playback WHERE item_id = ?", (item_id,),
            default=0) or 0)
        if not waiting:
            continue
        if db.query_one("SELECT id FROM items WHERE id = ?", (item_id,)):
            continue

        # A series or a season: they have no file, the sync does not know them and as a library item they
        # would end up in the archive. See the docstring.
        if str(item.get("Type") or "") not in scanner.MANAGED_TYPES:
            result = _episodes_under_series(item_id, item)
            linked += result["linked"]
            filled += result["filled"]
            rows += result["row_count"]
            continue

        target = _episode_in_library(item)
        if target:
            with db.connect() as conn:
                cursor = conn.execute(
                    """UPDATE playback SET item_id = ?, item_name = ?,
                                           series_name = ?
                        WHERE item_id = ?""",
                    (target["id"], target["name"], target["series_name"], item_id),
                )
            if cursor.rowcount:
                linked += 1
                rows += cursor.rowcount
            continue

        tech = jellyfin_tech(item) if use_jellyfin_tech else {}
        row = scanner._item_row(
            item, _library_for(item, libraries), tech, db.utcnow())
        scanner._write_items([row], keep_existing_tech=not use_jellyfin_tech)
        if use_jellyfin_tech:
            streams = jellyfin_streams(item)
            if streams:
                scanner.save_streams(item_id, streams)

        created += 1
        rows += waiting

    return {"linked": linked, "created_count": created,
            "filled": filled, "row_count": rows}


def _episodes_under_series(series_id: str, item: dict[str, Any]) -> dict[str, int]:
    """Try to file the records hanging on a SERIES' id under particular episodes.

    This is the part that makes the look-up worth it for series too. A record from imported history often
    carries the series' id (not the episode's) and a name like "Kancelar - S02E05 - Nakup" or "5. dil".
    The name alone is useless - every series has a "5. dil" - but **now we know which series it is**, so
    it is looked for only among its episodes. And there it usually comes out unambiguous.

    It goes row by ROW, not in bulk by item_id: different episodes hang under one series id, each with
    another name.

    When the episode cannot be told, at least the series' name is left - that is an improvement too,
    since the record then stops posing as a separate movie on the overviews and merges under its series.
    """
    series_name = str(item.get("Name") or "").strip()

    # The series' episodes as the library knows them. Archived ones too - the history may point at them,
    # they just cannot be played any more.
    episodes = db.query_all(
        """
        SELECT id, name, parent_index_number AS season, index_number AS episode
          FROM items
         WHERE type = 'Episode' AND series_id = ?
        """,
        (series_id,),
    )
    by_number: dict[tuple[int, int], dict[str, Any]] = {}
    by_name: dict[str, list[dict[str, Any]]] = {}
    for row in episodes:
        if row["season"] is not None and row["episode"] is not None:
            by_number.setdefault((int(row["season"]), int(row["episode"])), row)
        key = str(row["name"] or "").strip().lower()
        if key:
            by_name.setdefault(key, []).append(row)

    records = db.query_all(
        "SELECT id, item_name FROM playback WHERE item_id = ?", (series_id,))

    linked = filled = rows = 0
    with db.connect() as conn:
        for record in records:
            name = str(record["item_name"] or "").strip()
            target = None

            parts = split_episode_title(name)
            if parts:
                target = by_number.get((parts["season"], parts["episode"]))
            if target is None:
                matches = by_name.get(name.lower(), [])
                # Several episodes of the same name in one series happen (two-part episodes). We must not
                # guess - wrongly assigned history is worse than unassigned.
                target = matches[0] if len(matches) == 1 else None

            if target is not None:
                conn.execute(
                    """UPDATE playback SET item_id = ?, item_name = ?,
                                           series_name = ?, item_type = 'Episode'
                        WHERE id = ?""",
                    (target["id"], target["name"], series_name or None, record["id"]),
                )
                linked += 1
                rows += 1
            elif series_name:
                # The episode cannot be told, the series can.
                cursor = conn.execute(
                    """UPDATE playback SET series_name = ?
                        WHERE id = ? AND (series_name IS NULL OR series_name = ''
                                          OR series_name != ?)""",
                    (series_name, record["id"], series_name),
                )
                if cursor.rowcount:
                    filled += 1
                    rows += cursor.rowcount

    if linked or filled:
        log.info("series %s: %s episodes linked, %s left with just the series name",
                 series_name or series_id, linked, filled)
    return {"linked": linked, "filled": filled, "row_count": rows}


def _libraries_by_path() -> list[tuple[str, str]]:
    """Pairs (a path on the disk, a library id), the longest path first.

    Jellyfin does not send which library an item belongs to - it sends the path to the file. The
    libraries know their paths, so it can be assigned by the start of the path. The longest first, so
    that with two libraries where one is a subfolder of the other the more specific one wins.
    """
    pairs: list[tuple[str, str]] = []
    # Only video libraries: items (movies, episodes) live there - a music or book library
    # under the same disk folder must not take an imported film over (16. 11. 2026).
    for row in db.query_all(f"SELECT id, paths FROM libraries l WHERE {video_library_sql('l')}"):
        try:
            paths = json.loads(row["paths"] or "[]")
        except ValueError:
            continue
        for path in paths if isinstance(paths, list) else []:
            if path:
                pairs.append((str(path).rstrip("/\\"), str(row["id"])))
    return sorted(pairs, key=lambda d: len(d[0]), reverse=True)


def _library_for(item: dict[str, Any],
                 libraries: list[tuple[str, str]]) -> str | None:
    """Which library the item belongs to. None when it cannot be told.

    An empty library is no tragedy: the first full sync fills it in, since it knows which library the
    item came from.
    """
    path = str(item.get("Path") or "")
    for start, library_id in libraries:
        if start and path.startswith(start):
            return library_id
    return None


def _episode_in_library(item: dict[str, Any]) -> dict[str, Any] | None:
    """Find in the library the same episode Jellyfin has just described.

    First by identity (the series' tmdb + season + episode) - the same measure a re-encoded file is
    recognised by. When tmdb is missing, the series' id and the numbers are tried. When more than one comes
    out, we do not touch it: wrongly assigned history is worse than orphaned.
    """
    season = item.get("ParentIndexNumber")
    episode = item.get("IndexNumber")
    if season is None or episode is None:
        return None

    identity = scanner.item_identity(item)
    if identity:
        tmdb, r, d = identity
        matches = db.query_all(
            """SELECT id, name, series_name FROM items
                WHERE tmdb_id = ? AND COALESCE(parent_index_number, -1) = ?
                  AND COALESCE(index_number, -1) = ? LIMIT 2""",
            (tmdb, r, d),
        )
        if len(matches) == 1:
            return matches[0]

    series = item.get("SeriesId")
    if series:
        matches = db.query_all(
            """SELECT id, name, series_name FROM items
                WHERE series_id = ? AND parent_index_number = ?
                  AND index_number = ? LIMIT 2""",
            (series, season, episode),
        )
        if len(matches) == 1:
            return matches[0]

    series_name = item.get("SeriesName")
    if series_name:
        matches = db.query_all(
            """SELECT id, name, series_name FROM items
                WHERE LOWER(series_name) = LOWER(?) AND parent_index_number = ?
                  AND index_number = ? LIMIT 2""",
            (series_name, season, episode),
        )
        if len(matches) == 1:
            return matches[0]
    return None
