"""The detail of one item: versions, streams, plays, siblings, deleting.

Part of the statistics package (see jellyscope/stats/__init__.py)."""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any
from urllib.parse import quote

from .. import db, formatting
from .playback import transcode_description

# The logger's name as before the split: it shows in every line of the log.
log = logging.getLogger("jellyscope.stats")


# ---------------------------------------------------------------------------
# The detail of one item
# ---------------------------------------------------------------------------

def item(item_id: str) -> dict[str, Any] | None:
    return db.query_one(
        """
        SELECT i.*, l.name AS library_name, l.collection_type
        FROM items i
        LEFT JOIN libraries l ON l.id = i.library_id
        WHERE i.id = ?
        """,
        (item_id,),
    )


def item_versions(item_id: str) -> list[dict[str, Any]]:
    """All files of one title - 4K beside 1080p.

    An empty list means "one file" (or a library not synced since versions came in). The detail then behaves as
    before and switches nothing - one version is nothing to choose from.
    """
    return db.query_all(
        "SELECT source_id, position, name, path, container, video_codec,"
        " audio_codec, audio_channels, width, height, bitrate, size_bytes,"
        " video_range, runtime_ticks, audio_languages, subtitle_languages"
        " FROM item_versions WHERE item_id = ? ORDER BY position",
        (item_id,))


def title_items(item_id: str) -> list[str]:
    """The ids of all the title's copies that are in Jellyfin - the main one first. Without copies `[item_id]`.

    A copy is a movie that is twice in one library (two items with the same TMDB id): the secondary copy carries
    the main one's id in `main_id` (scanner/copies.py). The title's record is one - plays are summed over all the
    copies and the detail of any of them shows the same. After the main one come the others, the longest added
    first. Neither an episode nor an archived item has copies - and if `main_id` did not hold just now (the main
    copy disappeared and the recount has not run yet), the item stands on its own too.
    """
    row = db.query_one("SELECT id, main_id, is_missing FROM items WHERE id = ?", (item_id,))
    if row is None or row["is_missing"]:
        return [item_id]
    main_id = str(row["main_id"] or row["id"])
    live = db.query_all("SELECT id, date_created FROM items WHERE (id = ? OR main_id = ?) AND is_missing = 0",
                        (main_id, main_id))
    others = sorted((k for k in live if str(k["id"]) != main_id),
                    key=lambda k: (k["date_created"] is None, str(k["date_created"] or ""), str(k["id"])))
    if not others or len(others) == len(live):
        return [item_id]
    return [main_id, *(str(k["id"]) for k in others)]


def title_files(item_id: str, chosen_version: str | None = None) -> list[dict[str, Any]]:
    """The title's files for the switch on the "File" card. A title with a single file returns `[]`.

    A file is either a version inside the item (Jellyfin joined two files in one folder, `item_versions`), or a
    whole other item (a movie's copy in another folder of the same library, see title_items). Each carries the
    address at which it is chosen: `/item/<id>`, for a version `/item/<id>?version=<source_id>`, and its path
    (`path`) - two copies in the same quality otherwise have the same label. `active` is the file of the open item
    - its chosen version, without a choice the first one (Jellyfin considers it the main one).
    """
    ids = title_items(item_id)
    items_by_id = {str(r["id"]): r for r in db.query_all_in(
        "SELECT id, path, width, height, size_bytes FROM items WHERE id IN ({})", ids)}
    versions: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for v in db.query_all_in("SELECT item_id, source_id, position, name, path, width, height, size_bytes FROM item_versions"
                             " WHERE item_id IN ({}) ORDER BY position", ids):
        versions[str(v["item_id"])].append(v)
    files: list[dict[str, Any]] = []
    for copy_id in ids:
        if len(versions[copy_id]) > 1:
            for index, v in enumerate(versions[copy_id]):
                chosen = v["source_id"] == chosen_version if chosen_version else index == 0
                files.append({"item_id": copy_id, "source_id": v["source_id"], "name": v["name"], "path": v["path"], "width": v["width"],
                              "height": v["height"], "size_bytes": v["size_bytes"],
                              "href": f"/item/{copy_id}?version={quote(str(v['source_id']), safe='')}",
                              "active": copy_id == item_id and chosen})
        elif copy_id in items_by_id:
            row = items_by_id[copy_id]
            files.append({"item_id": copy_id, "source_id": None, "name": None, "path": row["path"], "width": row["width"],
                          "height": row["height"], "size_bytes": row["size_bytes"],
                          "href": f"/item/{copy_id}", "active": copy_id == item_id})
    return files if len(files) > 1 else []


def item_streams(item_id: str) -> dict[str, list[dict[str, Any]]]:
    """The item's streams split into video, sound and subtitles."""
    rows = db.query_all(
        "SELECT * FROM item_streams WHERE item_id = ? ORDER BY type, stream_index",
        (item_id,),
    )
    grouped: dict[str, list[dict[str, Any]]] = {"Video": [], "Audio": [], "Subtitle": []}
    for row in rows:
        grouped.setdefault(row["type"], []).append(row)
    return grouped


def item_playback(item_id: str, limit: int = 25) -> list[dict[str, Any]]:
    """The title's last plays - of all its copies together (title_items).

    The list of copies goes to the database in batches (db.query_all_in) and `ORDER BY` with `LIMIT` holds only
    inside a batch: the newest `limit` are taken from each and the order and the limit are finished here, over all.
    """
    # The file's codecs for the bubble at the transcode mark - the same as in History (see transcode_description).
    rows = db.query_all_in(
        f"""
        SELECT p.*, i.video_codec AS source_codec,
               i.audio_codec AS source_audio_codec
        FROM playback p
        LEFT JOIN items i ON i.id = p.item_id
        WHERE p.item_id IN ({{}}) AND p.watched_seconds > 0
        ORDER BY p.started_at DESC, p.id DESC LIMIT {int(limit)}
        """,
        title_items(item_id),
    )
    rows = sorted(rows, key=lambda r: (str(r["started_at"]), r["id"]), reverse=True)[:int(limit)]
    for row in rows:
        row["recount"] = transcode_description(row)
    return rows


def item_playback_summary(item_id: str) -> dict[str, Any]:
    """A summary of the title's plays - of all its copies together (title_items).

    By viewer and summed here: the list of copies goes to the database in batches and one summary row would hold
    only for the first of them (and a viewer who played copies from different batches would be counted twice by
    `COUNT(DISTINCT)`). A play without a viewer's id (from an import) counts, an extra viewer does not - as with
    `COUNT(DISTINCT user_id)`, which leaves NULL out.
    """
    rows = db.query_all_in(
        """
        SELECT user_id, COUNT(*) AS plays, COALESCE(SUM(watched_seconds), 0) AS seconds, MAX(started_at) AS last_played
        FROM playback
        WHERE item_id IN ({}) AND watched_seconds > 0
        GROUP BY user_id
        """,
        title_items(item_id),
    )
    return {"plays": sum(r["plays"] for r in rows), "hours": sum(float(r["seconds"] or 0) for r in rows) / 3600.0,
            "users": len({r["user_id"] for r in rows if r["user_id"] is not None}),
            "last_played": max((r["last_played"] for r in rows), default=None)}


def sibling_episodes(item_row: dict[str, Any], limit: int = 40) -> list[dict[str, Any]]:
    """The other episodes of the same series - navigation in an episode's detail."""
    if not item_row.get("series_id"):
        return []
    return db.query_all(
        """
        SELECT id, name, index_number, parent_index_number, size_bytes, height
        FROM items
        WHERE series_id = ? AND is_missing = 0
        ORDER BY parent_index_number, index_number
        LIMIT ?
        """,
        (item_row["series_id"], limit),
    )


def delete_item(item_id: str) -> dict[str, Any]:
    """Deletes the item and its play history for good.

    This is the only way something really disappears from Jellyscope, and it is manual on purpose. Automatically
    items are only ever archived - when Jellyfin drops out for a while or the storage is reconnected, you would
    otherwise lose the history over a passing outage.

    Returns what was deleted, so that the user can be told what they lost.
    """
    with db.connect() as conn:
        row = conn.execute(
            "SELECT name, series_name FROM items WHERE id = ?", (item_id,)
        ).fetchone()
        if row is None:
            return {"status": "error", "message": "The item does not exist."}
        plays = _delete_item(conn, item_id)

    log.info("item %s deleted (%s plays)", row["name"], plays)
    return {
        "status": "ok",
        "name": row["name"],
        "series_name": row["series_name"],
        "plays": plays,
    }


def _delete_item(conn: Any, item_id: str) -> int:
    """Deletes the item, its history, streams and versions. Returns the count of plays.

    The history is deleted by hand - there is no foreign key on purpose, so that an outage of Jellyfin can never
    delete statistics. It is deleted from the bin of forgotten viewers too: if the viewer came back before the
    night, their plays of the deleted title would come back as records without a title.
    """
    plays = int(conn.execute(
        "SELECT COUNT(*) AS c FROM playback WHERE item_id = ?", (item_id,)
    ).fetchone()["c"])
    conn.execute("DELETE FROM playback WHERE item_id = ?", (item_id,))
    if db.columns_of(conn, db.PLAYBACK_BIN):           # the bin comes to be only with the first forgetting
        conn.execute(f"DELETE FROM {db.PLAYBACK_BIN} WHERE item_id = ?", (item_id,))
    conn.execute("DELETE FROM item_streams WHERE item_id = ?", (item_id,))
    conn.execute("DELETE FROM item_versions WHERE item_id = ?", (item_id,))
    conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
    return plays


def item_thumbnail(row: dict[str, Any], width: int = 80) -> str:
    """The address of the target title's small poster in the assignment menus.

    Only for live items - an item from the archive has no picture in Jellyfin any more.

    For an episode the series' poster, not a still from the episode: that one is landscape and beside the name in
    the row it would look different from the movies' posters. Which episode it is, the label says (S01E02). The
    tag goes into the address, so that the browser does not keep an old picture.
    """
    if row.get("series_id"):
        owner_id, tag = row["series_id"], row.get("series_image_tag") or ""
    else:
        owner_id, tag = row["id"], row.get("image_tag") or ""
    return f"/image/{owner_id}?kind=Primary&w={width}" + (f"&tag={tag}" if tag else "")


def file_name(path: Any) -> str:
    """Only the file's name from a path - from a Windows one too (D:\\media\\…).

    The whole path gives away more about the server's disk than the choice needs.
    """
    return str(path or "").replace("\\", "/").rsplit("/", 1)[-1]


def item_label(row: dict[str, Any]) -> str:
    """The title's name as it shows in the menus: the series, S01E02, the episode."""
    if row.get("series_name"):
        code = formatting.episode_code(row)
        numbers = f" {code}" if code else ""
        return f"{row['series_name']}{numbers} – {row.get('name') or '?'}"
    year = f" ({row['production_year']})" if row.get("production_year") else ""
    return f"{row.get('name') or '?'}{year}"
