"""Refreshing one item and a series, recent changes and their review.

Part of the package scanner (see jellyscope/scanner/__init__.py)."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

from .. import db, i18n
from ..jellyfin import (
    JellyfinClient,
    JellyfinError,
    extract_streams,
    extract_tech_from_item,
)
from .copies import recount_copies_after_sync
from .merging import archive, duplicates_of_new, move_history
from .sync import _item_row, _save_new_items, _sync_libraries, _sync_users
from .task_state import (
    _clear_progress,
    _clear_stop,
    _scan_lock,
    _start_progress,
    finish_task_log,
    start_task_log,
)
from .tech_scan import run_tech_scan
from .writing import _forget_images, _write_items, save_streams

log = logging.getLogger("jellyscope.scanner")


# How many minutes the quick sync goes back before the last known title. Originally five minutes - a guard
# against falling through at the boundary. A day is here because of what Jellyfin does during a scan: an
# episode we stored already may still be filed under its series or created again under a new id - with the same
# date added. With five minutes the quick sync never came back to it and it stood outside its series in Recently
# added until the full sync rewrote it. See `_review_recent()`.
RECENT_OVERLAP_MINUTES = 24 * 60

# How many recent items are checked by id in one run at most (those Jellyfin did not send in this run). A cap
# against the day a whole new collection pours into the library.
REVIEW_CAP = 500


def _last_added() -> str | None:
    """The date of the last title we have in the library already.

    The quick sync starts from there: what is newer we do not know yet. Returns None when the library is empty -
    then simply the newest items are taken, as many as fit under the cap.

    A day is subtracted (`RECENT_OVERLAP_MINUTES`). Going through the same title a second time costs nothing - it
    is written through ON CONFLICT - and exactly that fixes an episode Jellyfin had not filed under its series at
    the first pass.
    """
    newest = db.query_value(
        "SELECT MAX(date_created) FROM items WHERE date_created IS NOT NULL")
    if not newest:
        return None

    # Jellyfin sends the time with a T and fractions of a second; we store it as it came. For comparing we bring it
    # to our shape.
    text = str(newest).replace("T", " ")[:19]
    try:
        when = datetime.strptime(text, db.TIME_FORMAT)
    except ValueError:
        return None
    return (when - timedelta(minutes=RECENT_OVERLAP_MINUTES)).strftime(db.TIME_FORMAT)


async def refresh_item(item_id: str) -> dict[str, Any]:
    """Download one single item from Jellyfin again.

    What it is for: you fix the metadata of one movie in Jellyfin (a wrong year, a swapped name, a missing stream
    language) and want to see it here right away, without starting the sync of the whole library for it.

    We do exactly what the sync does - only for one id: the values are read again, the streams rewritten, and
    with the source "ffprobe" the file is measured again right away. Jellyfin is only **read** meanwhile (GET
    /Items).

    We do not take the lock: it is one request and one write. When a big sync is running, they will not get in
    each other's way - this item is written twice in a row the same at most.
    """
    stored = db.query_one("SELECT * FROM items WHERE id = ?", (item_id,))
    if stored is None:
        return {"status": "error", "message": "There is no such item in the database."}

    try:
        async with JellyfinClient(*db.jellyfin_connection()) as client:
            found = await client.items_by_ids([item_id])
    except JellyfinError as exc:
        return {"status": "error", "message": i18n.error_text(exc)}

    if not found:
        # The item is no longer in Jellyfin. We do not delete it - the play history refers to it -, we only say it
        # out loud.
        return {"status": "error",
                "message": "Jellyfin no longer knows this item. It disappeared from the library, or its ItemId changed."}

    item = found[0]
    use_jellyfin_tech = db.get_setting("tech_source") == "jellyfin"
    tech = extract_tech_from_item(item) if use_jellyfin_tech else {}

    # We take the library from the stored record: Jellyfin does not send it in the answer and overwriting it with
    # an empty one would drop the item from the overviews.
    row = _item_row(item, stored["library_id"], tech, db.utcnow())
    await asyncio.to_thread(_write_items, [row], not use_jellyfin_tech)

    if use_jellyfin_tech:
        streams = extract_streams(item)
        if streams:
            await asyncio.to_thread(save_streams, item_id, streams)

    result = {"status": "ok", "name": item.get("Name") or stored["name"]}

    if not use_jellyfin_tech:
        # With the source ffprobe the technical data are not taken from Jellyfin, so they would stay old after the
        # refresh. We measure right away - it is one file.
        tech_result = await run_tech_scan(only_missing=False, item_ids=[item_id])
        result["tech"] = tech_result

    return result


async def refresh_series(series_id: str) -> dict[str, Any]:
    """Download all episodes of one series from Jellyfin again.

    What it is for: when a wrongly identified series is fixed in Jellyfin, it changes at all episodes at once -
    names, numbers and posters. Refreshing them one by one would mean clicking fifty times.

    Images: the tag (`ImageTags`) is compared for every episode and when it differs, its image is deleted from
    the cache - otherwise Jellyscope would keep showing the wrong one. The poster of the series itself is always
    forgotten: we keep no item for it, so there is nothing to compare with, and it is exactly the image one came
    here to look at.

    Jellyfin is only read (GET /Items).
    """
    episodes = db.query_all(
        "SELECT id, library_id FROM items WHERE series_id = ?", (series_id,))
    if not episodes:
        return {"status": "error", "message": "There is no such series in the database."}

    library = next((d["library_id"] for d in episodes if d["library_id"]), None)
    ids = [str(d["id"]) for d in episodes]
    use_jellyfin_tech = db.get_setting("tech_source") == "jellyfin"

    try:
        async with JellyfinClient(*db.jellyfin_connection()) as client:
            found = await client.items_by_ids(ids)
    except JellyfinError as exc:
        return {"status": "error", "message": i18n.error_text(exc)}

    if not found:
        return {"status": "error",
                "message": "Jellyfin no longer knows any episode of this series."}

    rows = []
    streams: list[tuple[str, list[dict[str, Any]]]] = []
    now = db.utcnow()
    for item in found:
        tech = extract_tech_from_item(item) if use_jellyfin_tech else {}
        rows.append(_item_row(item, library, tech, now))
        if use_jellyfin_tech:
            found_streams = extract_streams(item)
            if found_streams:
                streams.append((str(item.get("Id")), found_streams))

    # _write_items compares the image tags itself and forgets the changed ones.
    await asyncio.to_thread(_write_items, rows, not use_jellyfin_tech)
    for item_id, found_streams in streams:
        await asyncio.to_thread(save_streams, item_id, found_streams)

    # The series' poster: we have no item for it, so the tag has nothing to compare with - we forget it outright.
    # Next time it is downloaded again.
    _forget_images([series_id])

    result: dict[str, Any] = {
        "status": "ok",
        "episode_count": len(found),
        "name": next((i.get("SeriesName") for i in found if i.get("SeriesName")),
                     ""),
    }
    if not use_jellyfin_tech:
        # With the source ffprobe the technical data from Jellyfin would stay old.
        result["tech"] = await run_tech_scan(
            only_missing=False, item_ids=[str(i.get("Id")) for i in found])
    log.info("series %s refreshed: %s episodes", series_id, len(found))
    return result


def _recent_message(since: str | None, added: int, seen: int,
                    replaced: int = 0, recreated: int = 0) -> i18n.Message:
    """The message for the log of the task "recently added titles".

    The sentences about the archive end with a full stop, the start of the message does not ("Nic noveho
    (zkontrolovano 40)"). They used to be glued with ". ", and when something was archived for both reasons at
    once, two full stops stood in a row in the log.
    """
    if not since:
        message = i18n.Message("{n} newest titles (the library was empty)", n=added)
    elif added:
        message = i18n.Message("{n} new titles ({total} checked)", n=added, total=seen)
    else:
        # Zero is a completely usual result - the task runs every few minutes. It must look like it too, otherwise
        # one looks in vain for what was added.
        message = i18n.Message("Nothing new ({total} checked)", total=seen)
    sentences: list[i18n.Message] = []
    # When it was about replaced files, the task's log is to show it - otherwise "3 new titles" is confusing: they
    # are not new, only of another size.
    if since and added and replaced:
        sentences.append(i18n.Message(
            "{n} of them replaced an older file. Those are archived and their history "
            "is attached.", n=replaced))
    if recreated:
        sentences.append(i18n.Message(
            "{n} items stored earlier were created again by Jellyfin in the meantime; the old ones are in "
            "the archive.", n=recreated))
    if not sentences:
        return message
    # The first sentence has no full stop of its own ("3 new titles (40 checked)").
    return i18n.join_messages([message, i18n.join_messages(sentences)], ". ")


async def sync_recent(max_items: int = 2000) -> dict[str, Any]:
    """The quick sync: only titles we do not have in the library yet.

    Why this one beside the full sync:

    The full sync goes through the **whole** library. With tens of thousands of items that means hundreds of calls
    to Jellyfin and several minutes of work, so it runs once in several hours - and a newly added movie may show
    in Jellyscope only half a day later. This task looks only at what was added and can run every few minutes.

    **The boundary is set by the last title we have already** (see `_last_added()`), not by the clock. Originally
    it was a time window derived from the task's interval - but then it mattered whether the task ran on schedule.
    When the app stood for half a day, the window was shorter than the outage and the titles of that time fell
    through until the full sync. This way nothing is skipped, however irregularly the task runs.

    **It goes library by library.** Jellyfin does not send in the answer which library an item belongs to, so
    when we ask for everything at once, we have nowhere to file it. The extra calls are a few - libraries tend to
    be single digits.

    **_mark_missing() is NOT called here.** That function marks as gone everything the run did not see - and this
    run sees only a handful of the newest items. The rest of the library would disappear into the archive. So
    cleaning up deleted titles stays with the full sync, which really goes through everything.
    """
    if _scan_lock.locked():
        return {"status": "busy", "message": "Another task is already running."}

    since = _last_added()

    async with _scan_lock:
        _clear_stop()
        scan_id = start_task_log("recent")
        seen = 0
        new_ids: list[str] = []
        sent: set[str] = set()
        straightened: dict[str, Any] = {}
        review: dict[str, int] = {}

        try:
            async with JellyfinClient(*db.jellyfin_connection()) as client:
                # The users too - it is one call and thanks to it the account of a removed person disappears within a few
                # minutes, not only after the nightly full sync.
                await _sync_users(client)
                libraries = await _sync_libraries(client)
                use_jellyfin_tech = db.get_setting("tech_source") == "jellyfin"
                _start_progress("recent", 0)

                for library in libraries:
                    items = await client.recent_items(
                        since, cap=max_items, parent_id=library["id"])
                    seen += len(items)
                    sent.update(str(i["Id"]) for i in items if i.get("Id"))
                    new_ids.extend(await _save_new_items(
                        items, library["id"], use_jellyfin_tech, client))

                # What we stored during that day and Jellyfin did not send now is checked by id - otherwise an episode
                # from the time Jellyfin had not filed it under its series would hang there.
                if since:
                    review = await _review_recent(client, since, sent,
                                                  use_jellyfin_tech)

                # New titles often are not new titles but new FILES of the same title. It must be straightened right
                # away - otherwise the same movie stands in the library twice until the full sync, and that is overnight.
                #
                # Inside `async with`: it needs to ask Jellyfin whether it still knows the old ItemId.
                if new_ids:
                    straightened = await straighten_after_new(client, new_ids)

                # Items archived by the review without a successor at the same path may have had history. The tidy tries
                # to attach it by the series and the episode number - unless the straightening a few lines above
                # started it already.
                if review.get("without_successor") and not straightened.get("tidied"):
                    from .. import importers  # only here: importers reach the scanner

                    await importers.tidy_data()

        except JellyfinError as exc:
            _clear_progress()
            finish_task_log(scan_id, "error", message=exc)
            return {"status": "error", "message": i18n.error_text(exc)}
        except Exception as exc:  # noqa: BLE001
            _clear_progress()
            log.exception("the quick sync failed")
            finish_task_log(scan_id, "error", message=exc)
            return {"status": "error", "message": i18n.translate_with("Unexpected error: {error}", None, error=exc)}

        _clear_progress()
        added = len(new_ids)
        message = _recent_message(since, added, seen,
                                  replaced=straightened.get("archived", 0),
                                  recreated=review.get("archived", 0))
        finish_task_log(scan_id, "done", total=seen, ok=added, message=message)


    # Only after the lock: the tech analysis takes it itself.
    #
    # With the data source "ffprobe" the technical values are not taken from Jellyfin on purpose, so a new title
    # stayed completely empty - without a container, resolution or size - and waited for the daily task. Now it is
    # measured right away, and only the one just added: it is a few files, not the whole library.
    tech = {}
    if new_ids and db.get_setting("tech_source") == "ffprobe":
        tech = await run_tech_scan(only_missing=True, item_ids=new_ids)
        if tech.get("ok"):
            log.info("quick sync: measured %s new files", tech["ok"])

    # A new movie may be the second copy of one that is in the library already.
    await asyncio.to_thread(recount_copies_after_sync)

    return {"status": "ok", "items": len(new_ids), "checked": seen,
            "since": since, "tech": tech}


async def _review_recent(client: JellyfinClient, since: str, sent: set[str],
                         use_jellyfin_tech: bool) -> dict[str, int]:
    """Recently stored items Jellyfin did not send in this run.

    The quick sync sees only what Jellyfin sends - and during a scan the items change under one's hands. An
    episode first shows up not yet filed under its series, or Jellyfin creates it again under a new id when filing
    it. That first record then stayed with us: it stood in Recently added as an episode of its own beside the
    series it belongs to, and only the full sync cleaned it up (it archives everything it did not see).

    Here the same is done, only for the last day: every item added since `since` that did not come in this run
    is checked by id.

    * Jellyfin does not know it -> into the archive. Exactly what the full sync would do with it; the history is
      then attached to the live episode by the tidy.
    * Jellyfin knows it (it only did not send it, say because of the cap) -> it is written again, with everything
      Jellyfin knows about it now.

    Nothing is deleted here and nothing is merged by a guess - only what Jellyfin says about a particular id
    decides.
    """
    rows = await asyncio.to_thread(
        db.query_all,
        "SELECT id, library_id FROM items"
        " WHERE is_missing = 0 AND date_created IS NOT NULL"
        "   AND REPLACE(date_created, 'T', ' ') >= ?"
        " ORDER BY date_created DESC",
        (since,))
    candidates = {str(r["id"]): r["library_id"] for r in rows
                  if str(r["id"]) not in sent}
    if not candidates:
        return {"verified": 0, "archived": 0, "refreshed": 0}
    ids = list(candidates)[:REVIEW_CAP]

    known = {str(i["Id"]): i for i in await client.items_by_ids(ids) if i.get("Id")}

    # The known ones are written again library by library - Jellyfin does not carry `library_id` in the answer, the
    # stored one is taken.
    by_library: dict[Any, list[dict[str, Any]]] = {}
    for item_id, item in known.items():
        by_library.setdefault(candidates[item_id], []).append(item)
    for library_id, items in by_library.items():
        await _save_new_items(items, library_id, use_jellyfin_tech, client)

    unknown = [i for i in ids if i not in known]
    archived = await asyncio.to_thread(archive, unknown)
    pairs = await asyncio.to_thread(_successors_by_path, unknown)
    moved = await asyncio.to_thread(move_history, pairs)

    if archived:
        log.info("quick sync: Jellyfin no longer knows %s recent items; they "
                 "are in the archive", archived)
    return {"verified": len(ids), "archived": archived,
            "refreshed": len(known), "history": moved,
            "without_successor": len(unknown) - len(pairs)}


def _successors_by_path(old_ids: list[str]) -> dict[str, str]:
    """A live item with the same path to the file - {old id: new id}.

    When Jellyfin creates an episode again, the old record often has no series filled in (that is why it was
    created again), so the tidy by "series + episode number" does not pair it. The path is the same, though - it is
    still the same file - and that can be relied on: two different titles do not lie in one file.
    """
    if not old_ids:
        return {}
    pairs: dict[str, str] = {}
    for old in old_ids:
        row = db.query_one(
            "SELECT n.id FROM items s JOIN items n"
            "  ON n.path = s.path AND n.id <> s.id AND n.is_missing = 0"
            " WHERE s.id = ? AND s.path IS NOT NULL AND s.path <> ''"
            # COALESCE: PostgreSQL would put NULL first with DESC, SQLite last - and LIMIT 1 would pick a different row
            # each time.
            " ORDER BY COALESCE(n.date_created, '') DESC LIMIT 1", (old,))
        if row:
            pairs[old] = str(row["id"])
    return pairs


async def straighten_after_new(client: JellyfinClient,
                               new_ids: list[str]) -> dict[str, Any]:
    """After new titles, straighten the library so that no duplicates stand in it.

    Three steps, and each is here for another reason:

    1. **find duplicates** - older items that look like the same thing,
    2. **ask Jellyfin** whether it still knows them. This is the whole decision: what Jellyfin knows is a second copy
       (4K beside 1080p) and must not be merged; what it does not know is a replaced file,
    3. **tidy the data** - attach the archived item's history to the live one. There is a finished task for that,
       so it is called, not copied.

    Without step 2 "I have a movie in two qualities" would become one movie and half the history would disappear.
    """
    from .. import importers  # only here: importers reach the scanner

    suspected = await asyncio.to_thread(duplicates_of_new, new_ids)
    if not suspected:
        return {"duplicate_count": 0, "archived": 0, "tidied": None}

    candidates = sorted({old for olds in suspected.values()
                         for old in olds})
    live = await client.items_by_ids(candidates)
    known = {str(i["Id"]) for i in live if i.get("Id")}
    replaced_ids = [old for old in candidates if old not in known]

    archived = await asyncio.to_thread(archive, replaced_ids)
    if archived:
        log.info("after the new titles: %s replaced files went to the archive",
                 archived)

    # We must move the history ourselves: the tidy can attach an episode from the archive (by the season and episode
    # number), but for a movie without a tmdb id it has nothing to tell it is the same title by. We know it - we just
    # asked Jellyfin.
    pairs = {old: new for new, olds in suspected.items()
             for old in olds if old in set(replaced_ids)}
    moved = await asyncio.to_thread(move_history, pairs)

    # And then the usual tidy: episodes of series, orphan records, duplicates.
    tidied = await importers.tidy_data() if archived else None
    return {"duplicate_count": len(candidates), "archived": archived,
            "history": moved, "tidied": tidied}
