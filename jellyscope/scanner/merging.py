"""Merging by TMDB, duplicates of new items, moving history, the archive.

Part of the package scanner (see jellyscope/scanner/__init__.py)."""
from __future__ import annotations

import logging
from typing import Any

from .. import db

log = logging.getLogger("jellyscope.scanner")


def _old_to_merge(
    pairs: list[tuple[tuple[str, int, int], str]],
    protected: set[str],
    seen_since: str | None = None,
) -> dict[str, str]:
    """Which stored items the merging would take. Returns {old id: new id}.

    By reading only - it serves to check in advance whether the "old" item really is no longer in Jellyfin.
    See `_merge_by_tmdb`.
    """
    if not pairs:
        return {}

    found: dict[str, str] = {}
    with db.connect() as conn:
        for (tmdb, season, episode), new_id in pairs:
            condition = ""
            params: list[Any] = [tmdb, season, episode, new_id]
            if seen_since:
                # An item Jellyfin sent in this run we must not merge - it obviously still exists.
                condition = " AND (i.synced_at IS NULL OR i.synced_at < ?)"
                params.append(seen_since)

            row = conn.execute(
                f"""
                SELECT i.id
                  FROM items i
             LEFT JOIN playback p ON p.item_id = i.id
                 WHERE i.tmdb_id = ?
                   AND COALESCE(i.parent_index_number, -1) = ?
                   AND COALESCE(i.index_number, -1) = ?
                   AND i.id != ?{condition}
              GROUP BY i.id
              ORDER BY COALESCE(SUM(p.watched_seconds), 0) DESC
                 LIMIT 1
                """,
                tuple(params),
            ).fetchone()
            if row is not None and str(row["id"]) not in protected:
                found[str(row["id"])] = new_id
    return found


def _merge_by_tmdb(pairs: list[tuple[tuple[str, int, int], str]],
                   protected: set[str] | None = None,
                   seen_since: str | None = None) -> int:
    """Move the history from an old item to the new one when it is the same show.

    The situation it solves: you transcode a movie to HEVC and replace the original file. Jellyfin does not
    see it as a change - it creates **a new item with a new ItemId** and the old one disappears. Without this
    function the play history would fall apart into two titles, one of them ending in the archive.

    **It merges only when the old item really disappeared.** That is how replacing a file differs from a
    second copy of the same episode: when you have an episode in the library twice (another quality, an old
    version left over), both are in Jellyfin - and merging would delete one of them. On the next scan it would
    come back and delete the other; the items would take turns and links to them would stop holding. So:
      * `protected` = ids we know are in Jellyfin (the batch being written, with the recent sync also a check by
        asking),
      * `seen_since` = the start of the run; an item with `synced_at` from this run came from Jellyfin just now,
        so it is not merged either.

    The mark of recognition is `item_identity()`: tmdb_id for a movie, the series' tmdb_id together with the
    season and episode number for an episode. tmdb_id alone is not enough for an episode - all episodes of a
    series have the same one, so they would merge with each other. See the comment at `item_identity()`.

    The procedure is careful and in this order:
      1. we delete the old item's streams - they do not belong to the new file anyway, and the foreign key
         would not let us change the id while they exist,
      2. we rewrite the play history to the new id,
      3. only then do we change the item's id,
      4. and we delete the technical data - they describe the old file, which no longer exists. The date added
         and the rest stay.

    Returns the count of merges.
    """
    # The old item = the same identity but another ItemId. When there are several (e.g. a movie was in the
    # library twice), the one with the most watched is taken - we do not want to lose that one. The choice is
    # made by `_old_to_merge()`.
    old_to_new = _old_to_merge(pairs, protected or set(), seen_since)
    if not old_to_new:
        return 0

    merged = 0
    with db.connect() as conn:
        for old_id, new_id in old_to_new.items():
            # When the new item is in the database already, we must not rewrite the old id to it - a duplicate id
            # would come up. In that case we only move the history and drop the old item.
            exists = conn.execute(
                "SELECT 1 FROM items WHERE id = ?", (new_id,)
            ).fetchone()

            # The streams and versions describe the OLD file - it is no longer on the disk, otherwise there would
            # be no merging. They must go before the item is deleted or renamed: they refer to it by a foreign key.
            conn.execute("DELETE FROM item_streams WHERE item_id = ?", (old_id,))
            conn.execute("DELETE FROM item_versions WHERE item_id = ?", (old_id,))
            conn.execute(
                "UPDATE playback SET item_id = ? WHERE item_id = ?", (new_id, old_id)
            )
            # The old id will not exist in a moment (it is deleted or renamed): copies that pointed at it as at the
            # main one (scanner/copies.py) would lead to a non-existent item until the next recount.
            conn.execute("UPDATE items SET main_id = NULL WHERE main_id = ?", (old_id,))

            if exists:
                conn.execute("DELETE FROM items WHERE id = ?", (old_id,))
            else:
                conn.execute(
                    "UPDATE items SET id = ?, is_missing = 0 WHERE id = ?",
                    (new_id, old_id),
                )
                # And the technical data go. Renaming the item would otherwise leave them hanging on the new file -
                # but they describe the OLD one, which is no longer on the disk. We merge exactly because the file
                # was replaced.
                #
                # The worst of it was `tech_source`: the analysis of files takes only items that have no data, so
                # it skipped this one forever. The detail then shone with the original file's resolution (or
                # nothing) and only a manual "Nacist metadata znovu" helped.
                conn.execute(
                    """
                    UPDATE items
                       SET container = NULL, video_codec = NULL, audio_codec = NULL,
                           audio_channels = NULL, width = NULL, height = NULL,
                           bitrate = NULL, size_bytes = NULL, video_range = NULL,
                           video_range_reported = NULL,
                           audio_languages = NULL, subtitle_languages = NULL,
                           default_audio_language = NULL,
                           audio_from_name = NULL, subtitle_from_name = NULL,
                           tech_source = NULL, tech_updated_at = NULL, tech_error = NULL,
                           tech_error_values = NULL
                     WHERE id = ?
                    """,
                    (new_id,),
                )

            log.info("merged: %s -> %s", old_id, new_id)
            merged += 1

    if merged:
        # The history moved to other items: an open page draws its cards over plays again (the plays' marker).
        db.note_plays_rewritten()
    return merged


def duplicates_of_new(new_ids: list[str]) -> dict[str, list[str]]:
    """For every newcomer, older items that look like the same title.

    **The TMDB id decides**, because it survives transcoding, renaming the file and Jellyfin filling in another
    name. Only when the title has none - and that is exactly the case that fell through here - is it looked up
    by name and year, for an episode by the series and the season and episode numbers.

    For an episode **the tmdb id alone is not enough**: Jellyfin reports the id of the whole SERIES for an
    episode, so all episodes would merge into one by it. That is why the season and episode numbers are
    always added to it.

    Returns {new id: [older ids]}. Whether it really is a replacement and not a second copy is decided only by
    Jellyfin - see `straighten_after_new`.
    """
    if not new_ids:
        return {}

    found: dict[str, list[str]] = {}
    with db.connect() as conn:
        for new_id in new_ids:
            row = conn.execute(
                "SELECT type, name, series_id, index_number,"
                " parent_index_number, production_year, tmdb_id"
                " FROM items WHERE id = ?", (new_id,)).fetchone()
            if row is None:
                continue
            new = dict(row)

            candidates = _by_tmdb(conn, new_id, new)
            if not candidates:
                candidates = _by_name(conn, new_id, new)
            if candidates:
                found[new_id] = candidates
    return found


def _by_tmdb(conn: Any, new_id: str,
             new: dict[str, Any]) -> list[str]:
    """Duplicates by TMDB id. An empty list = it has none, or nobody fits."""
    tmdb = str(new.get("tmdb_id") or "").strip()
    if not tmdb:
        return []

    conditions = ["tmdb_id = ?", "type = ?"]
    params: list[Any] = [new_id, tmdb, new.get("type")]
    if new.get("type") == "Episode":
        # A series has one tmdb id for all episodes - without the numbers a whole season would merge into one.
        _equal_or_empty(conditions, params, "index_number",
                        new.get("index_number"))
        _equal_or_empty(conditions, params, "parent_index_number",
                        new.get("parent_index_number"))
        if new.get("index_number") is None:
            # An episode without a number cannot be told from the others by tmdb - rather nothing than merging
            # the whole series.
            return []
    return _select(conn, conditions, params)


def _by_name(conn: Any, new_id: str,
             new: dict[str, Any]) -> list[str]:
    """The fallback for titles without a TMDB id: name and year, for an episode the series and the numbers."""
    if new.get("type") == "Episode" and new.get("series_id"):
        conditions = ["type = 'Episode'", "series_id = ?"]
        params: list[Any] = [new_id, new["series_id"]]
        _equal_or_empty(conditions, params, "index_number",
                        new.get("index_number"))
        _equal_or_empty(conditions, params, "parent_index_number",
                        new.get("parent_index_number"))
    else:
        conditions = ["type = ?", "name = ?"]
        params = [new_id, new.get("type"), new.get("name")]
        _equal_or_empty(conditions, params, "production_year",
                        new.get("production_year"))
    return _select(conn, conditions, params)


def _select(conn: Any, conditions: list[str], params: list[Any]) -> list[str]:
    rows = conn.execute(
        "SELECT id FROM items WHERE id <> ? AND is_missing = 0 AND "
        + " AND ".join(conditions), tuple(params)).fetchall()
    return [str(dict(r)["id"]) for r in rows]


def move_history(old_to_new: dict[str, str]) -> int:
    """The history of a replaced file belongs to the new one. {old: new}.

    The item stays in the archive - it really is not in Jellyfin and pretending it is would be a lie. Only the
    history moves: "how many times I saw it" is to be about the movie, not about a file that was transcoded
    meanwhile.

    The technical data leave with the old file too. They described it, not the new one, and if they stayed,
    the analysis would never rewrite them - it takes only items that have none.
    """
    if not old_to_new:
        return 0
    moved = 0
    with db.connect() as conn:
        for old, new in old_to_new.items():
            row = conn.execute(
                "SELECT COUNT(*) AS count FROM playback WHERE item_id = ?",
                (old,)).fetchone()
            count = int(dict(row or {}).get("count") or 0)
            if not count:
                continue
            conn.execute("UPDATE playback SET item_id = ? WHERE item_id = ?",
                         (new, old))
            conn.execute("DELETE FROM item_streams WHERE item_id = ?", (old,))
            conn.execute("DELETE FROM item_versions WHERE item_id = ?", (old,))
            moved += count
            log.info("replaced file: history %s -> %s (%s rows)",
                     old, new, count)
        conn.commit()
    return moved


def _equal_or_empty(conditions: list[str], params: list[Any],
                    column: str, value: Any) -> None:
    """A condition "the column equals" that handles an empty value too.

    `column IS ?` is a shortcut only SQLite knows - PostgreSQL does not let a parameter after `IS` and ends with
    a syntax error. And `= NULL` never equals anything in SQL, so an empty year or a missing episode number
    would quietly find nothing. That is why the condition is composed by the value.
    """
    if value is None:
        conditions.append(f"{column} IS NULL")
    else:
        conditions.append(f"{column} = ?")
        params.append(value)


def archive(ids: list[str]) -> int:
    """Mark items as gone. Deletes nothing - the history refers to them."""
    if not ids:
        return 0
    with db.connect() as conn:
        for start in range(0, len(ids), 200):
            batch = ids[start:start + 200]
            marks = ",".join("?" for _ in batch)
            # With `main_id` too (copies of a movie, scanner/copies.py): a gone item is no copy, and nobody may
            # point at a gone main one.
            conn.execute(
                f"UPDATE items SET is_missing = 1, main_id = NULL WHERE id IN ({marks})",
                tuple(batch))
            conn.execute(
                f"UPDATE items SET main_id = NULL WHERE main_id IN ({marks})",
                tuple(batch))
        conn.commit()
    return len(ids)
