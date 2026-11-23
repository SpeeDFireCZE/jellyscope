"""Writing items, versions, images and streams; merging the archive into live items; cleaning up phantoms.

Part of the package scanner (see jellyscope/scanner/__init__.py)."""
from __future__ import annotations

import logging
from typing import Any

from .. import db, images
from .item_types import WRITTEN_TYPES
from .merging import _merge_by_tmdb

log = logging.getLogger("jellyscope.scanner")


def write_versions(versions: list[tuple[str, list[dict[str, Any]]]]) -> int:
    """Store the titles' versions. What did not come with a title is deleted.

    The deleting is on purpose: when one of two versions is removed from the disk, Jellyfin stops sending it
    and the detail would keep a row promising a file that is not there.
    """
    if not versions:
        return 0
    now = db.utcnow()
    written = 0
    with db.connect() as conn:
        for item_id, entries in versions:
            if not item_id or not entries:
                # An empty list does not mean "the files are gone" but "Jellyfin did not send them in this
                # answer" - say a request without `MediaSources` in `Fields`. Deleting by what nobody claimed
                # would erase versions in the detail that are still on the disk.
                continue
            conn.execute("DELETE FROM item_versions WHERE item_id = ?",
                         (item_id,))
            for v in entries:
                conn.execute(
                    "INSERT INTO item_versions (item_id, source_id, position,"
                    " name, path, container, video_codec, audio_codec,"
                    " audio_channels, width, height, bitrate, size_bytes,"
                    " video_range, runtime_ticks, audio_languages,"
                    " subtitle_languages, synced_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (item_id, v.get("source_id"), v.get("position") or 0,
                     v.get("name"), v.get("path"), v.get("container"),
                     v.get("video_codec"), v.get("audio_codec"),
                     v.get("audio_channels"), v.get("width"), v.get("height"),
                     v.get("bitrate"), v.get("size_bytes"),
                     v.get("video_range"), v.get("runtime_ticks"),
                     v.get("audio_languages"), v.get("subtitle_languages"),
                     now))
                written += 1
        conn.commit()
    return written


def _write_batch(
    items: list[tuple[Any, ...]],
    streams: list[tuple[str, list[dict[str, Any]]]],
    tmdb_pairs: list[tuple[tuple[str, int, int], str]],
    keep_existing_tech: bool,
    seen_since: str | None = None,
    protected: set[str] | None = None,
) -> None:
    """Write one batch. Runs in a thread, not on the event loop."""
    # Merging must come BEFORE writing the items. If the new record were inserted first, it would have an empty
    # history and the old item would only wait to be marked "missing" - exactly what we want to avoid.
    #
    # The items of this batch are obviously live - Jellyfin sent them a moment ago -, so they go into the
    # merging as protected.
    live = set(protected or set())
    live.update(str(row[0]) for row in items if row[0])
    _merge_by_tmdb(tmdb_pairs, protected=live, seen_since=seen_since)

    if items:
        _write_items(items, keep_existing_tech=keep_existing_tech)
    # Streams only after the items - they refer to them by a foreign key, so the item must exist in the database
    # already.
    for item_id, item_streams in streams:
        save_streams(item_id, item_streams)


def _write_items(rows: list[tuple[Any, ...]], keep_existing_tech: bool) -> None:
    """Write a batch of items.

    `keep_existing_tech` handles a fine but important detail: when ffprobe is the source of the technical data,
    a usual library sync must not overwrite the values measured before with empty ones. COALESCE(?, column)
    means "take the new value, and when it is NULL, keep the original".
    """
    if keep_existing_tech:
        tech_update = """
                    container       = COALESCE(excluded.container, items.container),
                    video_codec     = COALESCE(excluded.video_codec, items.video_codec),
                    audio_codec     = COALESCE(excluded.audio_codec, items.audio_codec),
                    audio_channels  = COALESCE(excluded.audio_channels, items.audio_channels),
                    width           = COALESCE(excluded.width, items.width),
                    height          = COALESCE(excluded.height, items.height),
                    bitrate         = COALESCE(excluded.bitrate, items.bitrate),
                    size_bytes      = COALESCE(excluded.size_bytes, items.size_bytes),
                    video_range     = COALESCE(excluded.video_range, items.video_range),
                    video_range_reported = excluded.video_range_reported,
                    audio_languages = COALESCE(excluded.audio_languages, items.audio_languages),
                    subtitle_languages = COALESCE(excluded.subtitle_languages, items.subtitle_languages),
                    default_audio_language = COALESCE(excluded.default_audio_language,
                                                      items.default_audio_language),
                    tech_source     = COALESCE(excluded.tech_source, items.tech_source),
                    tech_updated_at = COALESCE(excluded.tech_updated_at, items.tech_updated_at),
        """
    else:
        tech_update = """
                    container       = excluded.container,
                    video_codec     = excluded.video_codec,
                    audio_codec     = excluded.audio_codec,
                    audio_channels  = excluded.audio_channels,
                    width           = excluded.width,
                    height          = excluded.height,
                    bitrate         = excluded.bitrate,
                    size_bytes      = excluded.size_bytes,
                    video_range     = excluded.video_range,
                    video_range_reported = excluded.video_range_reported,
                    audio_languages = excluded.audio_languages,
                    subtitle_languages = excluded.subtitle_languages,
                    default_audio_language = excluded.default_audio_language,
                    tech_source     = excluded.tech_source,
                    tech_updated_at = excluded.tech_updated_at,
                    tech_error      = NULL,
                    tech_error_values = NULL,
        """

    sql = f"""
        INSERT INTO items (
            id, name, type, library_id, series_id, series_name, season_name,
            index_number, parent_index_number, production_year, runtime_ticks,
            date_created, path, tmdb_id, genres, container, video_codec, audio_codec,
            audio_channels, width, height, bitrate, size_bytes, video_range,
            video_range_reported,
            audio_languages, subtitle_languages, default_audio_language,
            tech_source, tech_updated_at, series_image_tag, image_tag, synced_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
            name = excluded.name,
            type = excluded.type,
            library_id = excluded.library_id,
            series_id = excluded.series_id,
            series_name = excluded.series_name,
            genres = excluded.genres,
            season_name = excluded.season_name,
            index_number = excluded.index_number,
            parent_index_number = excluded.parent_index_number,
            production_year = excluded.production_year,
            runtime_ticks = excluded.runtime_ticks,
            date_created = excluded.date_created,
            path = excluded.path,
            -- COALESCE: kdyz Jellyfin tmdb_id zrovna neposle (nedohledane
            -- metadata), nesmime uz ulozene prepsat na NULL - prisli bychom
            -- o jedinou vec, podle ktere umime polozky slucovat.
            tmdb_id = COALESCE(excluded.tmdb_id, items.tmdb_id),
            {tech_update}
            image_tag = excluded.image_tag,
            series_image_tag = excluded.series_image_tag,
            synced_at = excluded.synced_at,
            is_missing = 0
    """

    # Which items changed their image? We must ask BEFORE writing - afterwards the new tag is in the database and
    # the difference could not be told.
    changed = _items_with_new_image(rows)

    with db.connect() as conn:
        conn.executemany(sql, rows)

    # Only after the write: if the write failed, we would delete images for nothing.
    if changed:
        _forget_images(changed)


# In an item's row (see _item_row) the image tag is the second to last and the id the first.
_ID_COLUMN = 0
_SERIES_COLUMN = 4
_SERIES_IMAGE_TAG_COLUMN = -3
_IMAGE_TAG_COLUMN = -2


def _items_with_new_image(rows: list[tuple[Any, ...]]) -> list[str]:
    """Which images no longer hold - ids of items and ids of series.

    Jellyfin reports the tag (`ImageTags.Primary`) with every item. When it changes, the poster is a different
    one - typically because somebody fixed a wrongly identified item in Jellyfin.

    With a series it is a step more complex: we have no item for it, but the poster is downloaded under its id.
    So Jellyfin also reports `SeriesPrimaryImageTag` with every episode - the tag of its series' poster. When it
    changes, we return **the series' id**, so that its image is dropped too.
    """
    by_id = {str(row[_ID_COLUMN]): row[_IMAGE_TAG_COLUMN]
             for row in rows if row[_ID_COLUMN]}
    series = {str(row[_SERIES_COLUMN]): row[_SERIES_IMAGE_TAG_COLUMN]
              for row in rows if row[_SERIES_COLUMN]}
    if not by_id:
        return []

    changed: list[str] = []
    ids = list(by_id)
    with db.connect() as conn:
        for start in range(0, len(ids), 300):
            batch = ids[start:start + 300]
            marks = ",".join("?" for _ in batch)
            for row in conn.execute(
                    f"SELECT id, image_tag FROM items WHERE id IN ({marks})",
                    tuple(batch)).fetchall():
                old_tag = row["image_tag"]
                new_tag = by_id.get(str(row["id"]))
                if old_tag and new_tag and old_tag != new_tag:
                    changed.append(str(row["id"]))

        # Series: it is enough to ask one episode of each - all have the same tag of the series' poster.
        for series_id, new_tag in series.items():
            if not new_tag:
                continue
            row = conn.execute(
                "SELECT series_image_tag FROM items "
                " WHERE series_id = ? AND series_image_tag IS NOT NULL LIMIT 1",
                (series_id,)).fetchone()
            old_tag = row["series_image_tag"] if row else None
            # No stored tag means either a series we have not seen yet (so nothing can be in the cache), or a
            # database from before this column - and a poster lies there we do not know still holds. The column
            # came to be exactly because of it, so we drop it once; next time there is something to compare.
            if old_tag != new_tag:
                changed.append(series_id)
    return changed


def _forget_images(ids: list[str]) -> int:
    """Delete these items' images from the cache.

    Where the images lie and what they are called only the store knows (jellyscope/images.py). All kinds and
    sizes are deleted - when the poster changed, there is no point trusting even the backdrop.

    It is not just a clean-up: while the file lies there, we serve the old image and the user has no way to get
    to the new one.
    """
    deleted = images.forget_items(ids)
    if deleted:
        log.info("forgot %s images that no longer match Jellyfin", deleted)
    return deleted


def save_streams(item_id: str, streams: list[dict[str, Any]]) -> None:
    """Rewrite one item's streams.

    Delete first, then insert. If we only inserted, streams that are no longer in the file would stay hanging
    after it was transcoded.
    """
    if not streams:
        return

    with db.connect() as conn:
        conn.execute("DELETE FROM item_streams WHERE item_id = ?", (item_id,))
        # A freshly measured file beats the guess from the name: if the guess stayed, it would be added to the
        # summary even after the language is known.
        conn.execute("UPDATE items SET audio_from_name = NULL, "
                     "subtitle_from_name = NULL WHERE id = ?", (item_id,))
        # ON CONFLICT ... DO UPDATE instead of SQLite's INSERT OR REPLACE - SQLite and PostgreSQL both understand
        # it. And it shows what exactly happens on a conflict, which was never clear with "REPLACE".
        conn.executemany(
            """
            INSERT INTO item_streams (
                item_id, stream_index, type, codec, language, language_source,
                title, channels, channel_layout, width, height, bitrate,
                is_default, is_forced, is_external
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT (item_id, stream_index) DO UPDATE SET
                type = excluded.type,
                codec = excluded.codec,
                language = excluded.language,
                language_source = excluded.language_source,
                title = excluded.title,
                channels = excluded.channels,
                channel_layout = excluded.channel_layout,
                width = excluded.width,
                height = excluded.height,
                bitrate = excluded.bitrate,
                is_default = excluded.is_default,
                is_forced = excluded.is_forced,
                is_external = excluded.is_external
            """,
            [
                (
                    item_id, s.get("stream_index"), s.get("type"), s.get("codec"),
                    s.get("language"), s.get("language_source"), s.get("title"),
                    s.get("channels"), s.get("channel_layout"), s.get("width"),
                    s.get("height"), s.get("bitrate"), s.get("is_default", 0),
                    s.get("is_forced", 0), s.get("is_external", 0),
                )
                for s in streams
            ],
        )


def _mark_missing(started_at: str) -> None:
    with db.connect() as conn:
        # `main_id` goes in the same step: a gone item is no copy...
        conn.execute(
            "UPDATE items SET is_missing = 1, main_id = NULL WHERE synced_at < ?", (started_at,)
        )
        # ...and nobody may point at a gone main copy - the tile would lead into the archive until the next
        # recount (scanner/copies.py).
        conn.execute(
            "UPDATE items SET main_id = NULL WHERE main_id IS NOT NULL"
            " AND main_id IN (SELECT id FROM items WHERE is_missing = 1)"
        )


def _archive_pairs() -> list[tuple[str, str]]:
    """Pairs (an archived item, a live item) the tidy joins. Changes nothing.

    What is paired and why is described at `merge_archive_into_live()`. Here is only the search, so that the
    card of tidying the data (`archive_overview()`) uses it too and shows in advance exactly what will be
    merged.
    """
    # Two queries and pairing in Python, not one nested query: in SQL it would be a subquery with its own GROUP
    # BY inside, and that reads worse than it saves.
    #
    # We keep the live episodes under two keys at once:
    #   by the series' id   - the normal case, the file of one episode was replaced,
    #   by the series' name - when Jellyfin created the whole series again (a deleted and re-added directory),
    #                         so the episodes have another series_id and would not be found by it.
    live: dict[tuple[str, int, int], tuple[str, float, Any]] = {}
    by_name: dict[tuple[str, int, int], tuple[str, float, Any]] = {}
    for row in db.query_all(
        """
        SELECT i.id, i.series_id, i.series_name, i.tmdb_id,
               i.parent_index_number, i.index_number,
               COALESCE(SUM(p.watched_seconds), 0) AS watched
          FROM items i
     LEFT JOIN playback p ON p.item_id = i.id
         WHERE i.is_missing = 0
           AND i.series_id IS NOT NULL
           AND i.parent_index_number IS NOT NULL
           AND i.index_number IS NOT NULL
      GROUP BY i.id, i.series_id, i.series_name, i.tmdb_id,
               i.parent_index_number, i.index_number
        """
    ):
        season, episode = int(row["parent_index_number"]), int(row["index_number"])
        watched = float(row["watched"] or 0)
        # When there are several live copies (4K and 1080p side by side), the more watched one is taken - we do
        # not want to lose that one.
        for target, key in (
            (live, (str(row["series_id"]), season, episode)),
            (by_name, (str(row["series_name"] or "").strip().lower(), season, episode)),
        ):
            if not key[0]:
                continue
            if key not in target or watched > target[key][1]:
                target[key] = (str(row["id"]), watched, row["tmdb_id"])

    # Series that still live under their id. Their archived episodes MUST NOT be paired by name: when the series
    # is in Jellyfin and one of its episodes is missing, then that episode really is missing - and a match of
    # names would send the history to somebody else (two series may have the same name).
    live_series = {
        str(row["series_id"]) for row in db.query_all(
            "SELECT DISTINCT series_id FROM items "
            " WHERE is_missing = 0 AND series_id IS NOT NULL")
    }

    pairs: list[tuple[str, str]] = []
    for row in db.query_all(
        """
        SELECT id, series_id, series_name, tmdb_id,
               parent_index_number, index_number
          FROM items
         WHERE is_missing = 1
           AND parent_index_number IS NOT NULL
           AND index_number IS NOT NULL
        """
    ):
        season, episode = int(row["parent_index_number"]), int(row["index_number"])
        series_id = str(row["series_id"] or "")
        # By the series' id first; only when the series does not exist under that id at all, we try the name. The
        # order matters: the id is a certainty, the name only a match.
        found = live.get((series_id, season, episode))
        if not found and series_id not in live_series:
            candidate = by_name.get(
                (str(row["series_name"] or "").strip().lower(), season, episode))
            # When both sides know tmdb_id and it differs, it is provably another series - that tells "Kancelar
            # (US)" from "Kancelar (UK)". When one of them lacks it, the name and the episode number decide; we
            # know nothing better about those records.
            old_tmdb, new_tmdb = row["tmdb_id"], (candidate or (None,) * 3)[2]
            if candidate and not (old_tmdb and new_tmdb
                                  and str(old_tmdb) != str(new_tmdb)):
                found = candidate
        if found and found[0] != str(row["id"]):
            pairs.append((str(row["id"]), found[0]))
    return pairs


def merge_archive_into_live() -> int:
    """Join an archived episode that exists in the library again to the live one.

    What happened: the episode's file was replaced in Jellyfin (another quality, a new rip, a move between
    folders). Jellyfin did not record a change but **a new item with a new ItemId** - and the old one fell into
    the archive at the next sync. The series' detail then said "3 more episodes are in the archive", although
    those episodes are in the library as usual.

    Why the merging during a sync (`_merge_by_tmdb`) did not catch it: it compares the stored `tmdb_id`, and
    older records (typically from a history import or from before that column) have none. Without it there is
    nothing to find the pair by.

    Here the identity is taken from elsewhere - **the series plus the season and episode number**. The series
    is recognised by its id, and when that does not fit, by its name: when somebody deletes and re-adds a
    series' whole directory in Jellyfin, the episodes get new series_ids and the old item would never be found
    by them. Only an archived item is merged into a live one, never the other way round: the archived one
    provably is not in Jellyfin, so we lose nothing. When there are several live copies (4K and 1080p side by
    side), the history goes to the more watched one.

    The play history is rewritten to the new item - that is exactly why it is done: so that "how many times I
    saw it" does not end on a record nobody will see any more.

    Returns the count of merges.
    """
    pairs = _archive_pairs()
    if not pairs:
        return 0

    with db.connect() as conn:
        for old, new in pairs:
            # The order is given by the foreign key: the streams refer to the item, so they must go before the item
            # is deleted.
            conn.execute("DELETE FROM item_streams WHERE item_id = ?", (old,))
            conn.execute("DELETE FROM item_versions WHERE item_id = ?", (old,))
            conn.execute("UPDATE playback SET item_id = ? WHERE item_id = ?",
                         (new, old))
            conn.execute("DELETE FROM items WHERE id = ?", (old,))
            log.info("merged from the archive: %s -> %s", old, new)

    log.info("archive: merged %s episodes that exist in the library again",
             len(pairs))
    return len(pairs)


def archive_overview() -> dict[str, Any]:
    """What the tidy does with the archive - for the card of tidying the data. Changes nothing.

    `to_return`: how many episodes the next tidy returns to the live ones (the same pairing as
    `merge_archive_into_live()`). `left`: the rest of the archive - titles that are not in the library, or came
    back so that the automation does not recognise them (another name, another numbering). Those are assigned
    by hand in the library's archive, hence `libraries`: how many are where (id, name, count).
    """
    pairs = _archive_pairs()
    returned = {old for old, _new in pairs}
    by_library: dict[str, dict[str, Any]] = {}
    left = 0
    for row in db.query_all(
        "SELECT i.id, i.library_id, l.name AS name FROM items i"
        "  LEFT JOIN libraries l ON l.id = i.library_id"
        " WHERE i.is_missing = 1"
    ):
        if str(row["id"]) in returned:
            continue
        left += 1
        if row["library_id"]:
            library = by_library.setdefault(
                str(row["library_id"]),
                {"id": str(row["library_id"]), "name": row["name"] or "?", "count": 0})
            library["count"] += 1
    return {"to_return": len(pairs), "left": left,
            "libraries": sorted(by_library.values(), key=lambda k: (-k["count"], k["name"]))}


def clean_up_phantoms() -> int:
    """Throw out of the library items of a kind the sync does not know.

    The remedy after a bug: `importers.create_from_jellyfin()` created an item from anything Jellyfin returned
    - a whole SERIES included, when a history record hung on its id. The sync asks only for movies and episodes,
    so it never saw such an item and `_mark_missing()` sent it to the archive on every run. The library then had
    an "archived" series that is in Jellyfin - and beside it the right one, put together from episodes.

    Deleting is the right solution, not just switching a flag: such an item should never have come to be. The
    history is not lost by it - the records stay and return among the orphans, which is the truth they told
    before.

    Called at start-up. It is a cheap query on an indexed column and a usual database has nothing to clean up,
    so nothing is delayed.
    """
    # Everything the sync cannot write is deleted - a kind nobody thought of here too (a music video, a trailer
    # from an old import). The exception is `Video`: a freshly added file is exactly that for a while and is to
    # stay until Jellyfin files it. Since 1.6.8 the clean-up runs after every full sync too, so it must not
    # delete what the sync just stored.
    marks = ",".join("?" for _ in WRITTEN_TYPES)
    with db.connect() as conn:
        phantoms = [str(r["id"]) for r in conn.execute(
            f"SELECT id FROM items WHERE type NOT IN ({marks})",
            tuple(WRITTEN_TYPES)).fetchall()]
        if not phantoms:
            return 0
        for start in range(0, len(phantoms), 200):
            batch = phantoms[start:start + 200]
            placeholders = ",".join("?" for _ in batch)
            conn.execute(
                f"DELETE FROM item_streams WHERE item_id IN ({placeholders})", tuple(batch))
            conn.execute(
                f"DELETE FROM item_versions WHERE item_id IN ({placeholders})",
                tuple(batch))
            conn.execute(f"DELETE FROM items WHERE id IN ({placeholders})", tuple(batch))

    log.info("removed %s items that do not belong in the library (collections, series, seasons)",
             len(phantoms))
    return len(phantoms)
