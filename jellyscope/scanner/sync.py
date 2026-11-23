"""The sync of the whole library: users, libraries and items from Jellyfin.

Part of the package scanner (see jellyscope/scanner/__init__.py)."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from .. import db, i18n
from ..jellyfin import (
    JellyfinClient,
    JellyfinError,
    extract_streams,
    extract_tech_from_item,
    extract_versions,
    video_range_of,
)
from ..stats.kinds import is_video_library
from .copies import recount_copies_after_sync
from .disk_snapshots import write_daily_snapshots
from .disks import remember_folders
from .free_space import remember_space_from_jellyfin
from .item_types import is_wrapper
from .merging import _old_to_merge
from .task_state import (
    _add_progress,
    _clear_progress,
    _clear_stop,
    _scan_lock,
    _start_progress,
    finish_task_log,
    start_task_log,
    stop_requested,
)
from .writing import _mark_missing, _write_batch, clean_up_phantoms, write_versions

log = logging.getLogger("jellyscope.scanner")


# ---------------------------------------------------------------------------
# 1. The library sync
# ---------------------------------------------------------------------------

async def sync_library() -> dict[str, Any]:
    """Download the users, the libraries and all items from Jellyfin."""
    if _scan_lock.locked():
        return {"status": "busy", "message": "Another task is already running."}

    async with _scan_lock:
        _clear_stop()
        scan_id = start_task_log("library")
        started_at = db.utcnow()
        counts = {"users": 0, "libraries": 0, "items": 0}
        stopped = False

        try:
            async with JellyfinClient(*db.jellyfin_connection()) as client:
                counts["users"] = await _sync_users(client)
                libraries = await _sync_libraries(client)
                counts["libraries"] = len(libraries)

                use_jellyfin_tech = db.get_setting("tech_source") == "jellyfin"

                # First we ask how much there will be - one quick call per library. Without it the progress bar
                # would have nothing to compute percentages against.
                #
                # When it fails, the sync goes on. It is extra information for the progress bar, not a condition of
                # the work - bringing the whole scan down because of it would be out of proportion.
                total = 0
                try:
                    for library in libraries:
                        total += await client.item_count(parent_id=library["id"])
                except Exception as exc:      # noqa: BLE001
                    log.warning("the item count could not be determined: %s", exc)
                    total = 0
                _start_progress("library", total)

                for library in libraries:
                    counts["items"] += await _sync_items_of_library(
                        client, library, use_jellyfin_tech
                    )
                    if stop_requested():
                        stopped = True
                        break

                # Items we did not see in this run are no longer in Jellyfin. We do not delete them - the play history
                # refers to them - we only mark them.
                #
                # After a stop this step MUST be skipped. We have not gone through the rest of the library yet, so we
                # would mark as "unseen" titles that are in Jellyfin as usual - they would disappear from the library
                # and end in the archive. An unfinished sync rather changes nothing than lies.
                if not stopped:
                    _mark_missing(started_at)
                    # Items of a kind that does not belong here (collections from Jellyfin 12 before the fix) go right
                    # away - not only after a restart, when clean_up_phantoms() runs at start-up.
                    await asyncio.to_thread(clean_up_phantoms)

                # Which copy of a movie stands for the record (a movie twice in one library) - only after the gone items
                # are marked, those are no copies. After a stop too: the recount only reads what is in the database.
                await asyncio.to_thread(recount_copies_after_sync)

                # How much space is left - by Jellyfin ITSELF. We ask here while the client is open: the snapshot is
                # written only afterwards and is synchronous. When it does not work out (an older Jellyfin lacks the
                # endpoint), it goes on - it is extra information, not a condition of the work.
                try:
                    answer = await client.storage()
                    remember_space_from_jellyfin(answer)
                    # The library folders with the disk's id - the Disks card tells from them which library lies on
                    # which disk.
                    remember_folders(answer)
                except Exception as exc:      # noqa: BLE001
                    log.info("could not get the free space from Jellyfin: %s", exc)

            # Now we know the users and the items - we straighten the history that was imported before Jellyfin was
            # connected. Such an import has "?" instead of names in the history, does not know the item's type, and
            # its ItemId need not match any title in the library.
            #
            # We call the whole link_imported_history(), not just filling in the values: it also takes care of the
            # look-up by tmdb ID, which could not be done at the time of the import (Jellyfin was not connected
            # yet).
            #
            # The import only here, not at the top: importers imports the scanner already, and a circular import
            # would bring the app down at start-up.
            from .. import importers
            try:
                await importers.link_imported_history()
            except Exception as exc:      # noqa: BLE001
                # The library is downloaded and stored already. When straightening the history fails, it is a pity -
                # not a reason to report the whole sync as failed. It is tried at the next one.
                log.warning("aligning the imported history failed: %s", exc)

        except JellyfinError as exc:
            _clear_progress()
            finish_task_log(scan_id, "error", message=exc)
            return {"status": "error", "message": i18n.error_text(exc)}
        except Exception as exc:  # noqa: BLE001
            log.exception("the library sync failed")
            _clear_progress()
            finish_task_log(scan_id, "error", message=exc)
            return {"status": "error", "message": i18n.translate_with("Unexpected error: {error}", None, error=exc)}

        _clear_progress()
        if stopped:
            finish_task_log(
                scan_id, "stopped", total=counts["items"], ok=counts["items"],
                message=i18n.Message(
                    "Stopped at your request after {n} items (libraries: {libraries}, users: {users}). What was "
                    "downloaded is saved; the rest stayed unchanged.", n=counts["items"],
                    libraries=counts["libraries"], users=counts["users"]),
            )
            return {"status": "stopped", **counts}

        # The library snapshot is written only here: after a successful sync, when the state is complete. After a
        # stopped or failed one it would capture half the library and the growth chart would show a drop that never
        # happened.
        await asyncio.to_thread(write_daily_snapshots)

        finish_task_log(
            scan_id, "done", total=counts["items"], ok=counts["items"],
            message=i18n.Message("libraries: {libraries}, users: {users}",
                                 libraries=counts["libraries"], users=counts["users"]),
        )
        return {"status": "ok", **counts}


async def _sync_users(client: JellyfinClient) -> int:
    users = await client.users()
    now = db.utcnow()
    # The administrator's rights of the users whose Policy says - for their accounts (see the end).
    rights: dict[str, bool] = {}
    with db.connect() as conn:
        for user in users:
            # What the answer does not say is not changed: a user without Policy (or without one of its two
            # values) keeps the stored state - a missing IsDisabled must not enable a disabled person again.
            policy = user.get("Policy") if isinstance(user.get("Policy"), dict) else {}
            admin, disabled = policy.get("IsAdministrator"), policy.get("IsDisabled")
            if admin is not None:
                rights[str(user.get("Id") or "")] = bool(admin)
            # Decided here, not by CASE WHEN in SQL (test_dialect): a value the answer does not say is not updated.
            updated = ["name"] + (["is_administrator"] if admin is not None else []) \
                + (["is_disabled"] if disabled is not None else []) + ["last_activity", "synced_at", "image_tag"]
            conn.execute(
                "INSERT INTO users (id, name, is_administrator, is_disabled, last_activity, synced_at, image_tag)"
                " VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                + ", ".join(f"{column} = excluded.{column}" for column in updated),
                (
                    user.get("Id"),
                    user.get("Name") or "?",
                    1 if admin else 0,
                    1 if disabled else 0,
                    user.get("LastActivityDate"),
                    now,
                    # The tag of the profile picture; without a picture Jellyfin does not send it at all. An empty string =
                    # none too.
                    user.get("PrimaryImageTag") or None,
                ),
            )

    # Rows in `users` are not deleted - the history of a removed person still has someone to belong to. What is
    # deleted is their LOGIN account to Jellyscope: whom the admin removed in Jellyfin is not to have a door here.
    from .. import accounts  # only here, so that no cycle comes up at import

    removed = accounts.remove_without_jellyfin(
        {str(user.get("Id") or "") for user in users})
    if removed:
        log.info("accounts without a user in Jellyfin removed: %s",
                 ", ".join(removed))
    # An administrator's rights follow Jellyfin now, not only at the next sign-in (only users whose Policy says).
    accounts.follow_jellyfin_rights(rights)
    return len(users)


# How long Jellyfin may answer with no library at all before the answer counts (see _sync_libraries).
EMPTY_ANSWER_COUNTS_AFTER = timedelta(hours=1)


async def _sync_libraries(client: JellyfinClient) -> list[dict[str, Any]]:
    """Store every Jellyfin library; return the video ones, whose items the caller reads.

    The Library page shows every library Jellyfin has (16. 11. 2026) - music, and also types
    Jellyscope does not collect yet, with a sentence saying so. Movies and episodes still come
    only from video libraries (stats.kinds.VIDEO_LIBRARY_TYPES).

    A library Jellyfin did not return this time counts one more run without it
    (`missing_runs`); the Library page hides it from the second run in a row, and a library
    that comes back starts over. An empty answer is what a starting Jellyfin gives (the
    frequent "recently added" sync can get it twice within minutes), so it counts only for
    libraries Jellyfin has not listed for over EMPTY_ANSWER_COUNTS_AFTER - that is a Jellyfin
    whose libraries were really all removed.
    """
    folders = await client.virtual_folders()
    now = db.utcnow()
    result = []
    seen: list[str] = []

    with db.connect() as conn:
        for folder in folders:
            collection_type = folder.get("CollectionType")
            library = {
                "id": folder.get("ItemId"),
                "name": folder.get("Name") or "?",
                "collection_type": collection_type,
            }
            if not library["id"]:
                continue

            conn.execute(
                """
                INSERT INTO libraries (id, name, collection_type, paths, synced_at, missing_runs)
                VALUES (?,?,?,?,?,0)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    collection_type = excluded.collection_type,
                    paths = excluded.paths,
                    synced_at = excluded.synced_at,
                    missing_runs = 0
                """,
                (
                    library["id"],
                    library["name"],
                    collection_type,
                    json.dumps(folder.get("Locations") or []),
                    now,
                ),
            )
            seen.append(library["id"])
            if is_video_library(collection_type):
                result.append(library)

        if seen:
            marks = ",".join("?" * len(seen))
            conn.execute(f"UPDATE libraries SET missing_runs = missing_runs + 1 WHERE id NOT IN ({marks})", seen)
        else:
            cutoff = (datetime.now(UTC) - EMPTY_ANSWER_COUNTS_AFTER).strftime(db.TIME_FORMAT)
            conn.execute("UPDATE libraries SET missing_runs = missing_runs + 1 WHERE synced_at < ?", (cutoff,))

    return result


def tmdb_id_of(item: dict[str, Any]) -> str | None:
    """Take the TMDB identifier out of an item.

    Jellyfin sends them in the dict ProviderIds, but it writes the keys differently by version and item type:
    "Tmdb", "TmdbId", for a series sometimes "tmdb". So we compare in lower case, so that it does not matter.

    For episodes we take the **series'** id (`SeriesProviderIds`) when it has none of its own - an episode itself
    often has no id in TMDB and without it the merging would not catch on for series.

    CAREFUL: for an episode this id does NOT IDENTIFY the episode but the whole series - all episodes of
    Kancelar have the same one. Whoever wants to recognise one item by it must use `item_identity()`.
    """
    for source in (item.get("ProviderIds"), item.get("SeriesProviderIds")):
        if not isinstance(source, dict):
            continue
        for key, value in source.items():
            if str(key).lower() in ("tmdb", "tmdbid") and value:
                return str(value)
    return None


def item_identity(item: dict[str, Any]) -> tuple[str, int, int] | None:
    """What tells that two different files are the same show.

    It serves the merging: you transcode a file, Jellyfin creates a new item with a new ItemId and the old one
    disappears. We want to move the play history over.

    For a movie tmdb_id is enough. **Not for an episode** - and it was an ugly bug: `tmdb_id_of()` returns the
    SERIES' id for an episode, so all twenty episodes of Kancelar had the same one. The merging then took every
    two episodes for the same file and on every scan merged the history of all episodes into one. In the
    statistics it looked as if the viewer saw one episode twenty times - and the "watched titles" were twenty
    times fewer than they should be.

    So we identify an episode by a triple: series + season + episode number. When we do not know the episode
    number, we return None and rather do not merge at all - a wrong merge can never be taken back.
    """
    tmdb = tmdb_id_of(item)
    if not tmdb:
        return None

    if str(item.get("Type") or "") != "Episode":
        # A movie: it has neither a season nor an episode number, we put -1 into the comparison.
        return (tmdb, -1, -1)

    season = item.get("ParentIndexNumber")
    episode = item.get("IndexNumber")
    if season is None or episode is None:
        return None
    try:
        return (tmdb, int(season), int(episode))
    except (TypeError, ValueError):
        return None


def _item_row(item: dict[str, Any], library_id: Any,
              tech: dict[str, Any], now: str) -> tuple[Any, ...]:
    """Compose one row of the table `items` from Jellyfin's answer.

    A function of its own, because two different tasks need it - the full sync and the quick one over newly
    added titles. If they were two copies, adding a column would be forgotten once and one of the ways would
    quietly store less.
    """
    return (
        item.get("Id"),
        item.get("Name") or "?",
        item.get("Type") or "?",
        library_id,
        item.get("SeriesId"),
        item.get("SeriesName"),
        item.get("SeasonName"),
        item.get("IndexNumber"),
        item.get("ParentIndexNumber"),
        item.get("ProductionYear"),
        item.get("RunTimeTicks"),
        item.get("DateCreated"),
        item.get("Path"),
        tmdb_id_of(item),
        # We join genres with a vertical bar - a comma occurs in genre names.
        "|".join(str(z).strip() for z in (item.get("Genres") or []) if str(z).strip())
            or None,
        tech.get("container"),
        tech.get("video_codec"),
        tech.get("audio_codec"),
        tech.get("audio_channels"),
        tech.get("width"),
        tech.get("height"),
        tech.get("bitrate"),
        tech.get("size_bytes"),
        tech.get("video_range"),
        # What Jellyfin says about the range. It is stored in the ffprobe mode too, where the rest of the technical
        # data is not taken from Jellyfin: Dolby Vision in Matroska can be read only by ffmpeg 5, so an older
        # ffprobe reports just "HDR" - and Jellyfin is then the only one who knows about DV.
        video_range_of(item),
        tech.get("audio_languages"),
        tech.get("subtitle_languages"),
        tech.get("default_audio_language"),
        "jellyfin" if tech else None,
        now if tech else None,
        # The tag of the poster of the series the episode belongs to. Empty for a movie.
        item.get("SeriesPrimaryImageTag"),
        # The poster's tag. When Jellyfin does not report it, it stays empty - the image then behaves as before, only
        # without recognising a change.
        (item.get("ImageTags") or {}).get("Primary"),
        now,
    )


def _already_known(ids: list[str]) -> set[str]:
    """Which of these IDs are in the table items already.

    We ask in batches: SQL has a cap on the count of parameters in one query (with SQLite it tends to be 999)
    and the quick sync can come with thousands of items at once on its first run.
    """
    known: set[str] = set()
    for start in range(0, len(ids), 400):
        chunk = ids[start:start + 400]
        if not chunk:
            continue
        marks = ",".join("?" for _ in chunk)
        for row in db.query_all(
                f"SELECT id FROM items WHERE id IN ({marks})", tuple(chunk)):
            known.add(str(row["id"]))
    return known


async def _live_among_old(client: JellyfinClient, tmdb_pairs: list[tuple[tuple[str, int, int], str]],
                          being_written: set[str], seen_since: str | None = None) -> set[str]:
    """Items the merging would take for "old" but which are still in Jellyfin - they must not be merged.

    The merging (`_merge_by_tmdb`) looks for a stored item of the same identity as a new item and takes it for
    an old version of the same file. But a **second copy** has the same identity too: a movie twice in one
    library, or the same movie in two libraries. The stamp `synced_at` does not tell - a copy whose turn has not
    come yet in this run (a later batch, another library) has a stamp from last time just like an item that
    really disappeared. Without asking, such a copy would be deleted or renamed and its plays credited to the
    first one.

    So Jellyfin is told about the candidates (`_old_to_merge`, reading only), and those it knows are not merged.
    The request goes only when there is a candidate - i.e. after a file was replaced or for a title with several
    copies.
    """
    candidates = await asyncio.to_thread(_old_to_merge, tmdb_pairs, being_written, seen_since)
    if not candidates:
        return set()
    return {str(i["Id"]) for i in await client.items_by_ids(list(candidates)) if i.get("Id")}


async def _save_new_items(items: list[dict[str, Any]], library_id: Any,
                          use_jellyfin_tech: bool,
                          client: JellyfinClient | None = None) -> list[str]:
    """Store the items of one library. Used by the quick sync.

    `library_id` is passed from outside, because Jellyfin does not carry it in the answer. It used to be taken
    from the record stored already - but for a NEW title there is none, so it stayed empty. And on the next run
    the empty value was only copied, so it never mended itself.

    **All are written, but only the ids of the really new ones are returned.** The boundary for downloading is
    moved a few minutes back on purpose (see `_last_added()`), so the newest title known already is downloaded
    again - and it used to count as new. The result then never showed zero, even when nothing was added since
    last time. Rewriting a title known already makes sense, though: exactly that fixes a record Jellyfin had not
    filed into the library at its first pass.
    """
    # The same guard as in the full sync: what is neither a movie nor an episode (collections from Jellyfin 12)
    # is not written.
    items = [i for i in items if not is_wrapper(i)]
    if not items:
        return []

    now = db.utcnow()
    known = _already_known([str(i["Id"]) for i in items if i.get("Id")])
    new_ids = [str(i["Id"]) for i in items
               if i.get("Id") and str(i["Id"]) not in known]

    rows: list[tuple[Any, ...]] = []
    stream_rows: list[tuple[str, list[dict[str, Any]]]] = []
    versions: list[tuple[str, list[dict[str, Any]]]] = []
    tmdb_pairs: list[tuple[tuple[str, int, int], str]] = []

    for item in items:
        tech = extract_tech_from_item(item) if use_jellyfin_tech else {}
        if use_jellyfin_tech:
            streams = extract_streams(item)
            if streams:
                stream_rows.append((item.get("Id"), streams))
        versions.append((str(item.get("Id") or ""), extract_versions(item)))

        identity = item_identity(item)
        if identity and item.get("Id"):
            tmdb_pairs.append((identity, str(item["Id"])))

        rows.append(_item_row(item, library_id, tech, now))
        _add_progress(1)

    # The quick sync sees only the newcomers, so it cannot tell by `synced_at` what is still in the library -
    # everything else has the stamp of the last run. So when the merging points at some stored item, we ask
    # Jellyfin directly whether it is still there. If it is, it is a second copy of the same episode and must not
    # be merged.
    protected: set[str] = set()
    if client is not None:
        protected = await _live_among_old(client, tmdb_pairs, {str(i["Id"]) for i in items if i.get("Id")})

    await asyncio.to_thread(_write_batch, rows, stream_rows, tmdb_pairs,
                            not use_jellyfin_tech, None, protected)
    # Only after the items: the versions refer to them by a foreign key.
    await asyncio.to_thread(write_versions, versions)
    return new_ids


async def _sync_items_of_library(
    client: JellyfinClient,
    library: dict[str, Any],
    use_jellyfin_tech: bool,
) -> int:
    """Read and store all items of one library."""
    now = db.utcnow()
    count = 0
    batch: list[tuple[Any, ...]] = []
    stream_batch: list[tuple[str, list[dict[str, Any]]]] = []
    version_batch: list[tuple[str, list[dict[str, Any]]]] = []
    # Pairs (tmdb_id, the new ItemId) for merging transcoded files.
    seen_tmdb: list[tuple[tuple[str, int, int], str]] = []

    async def flush_batch() -> None:
        # The write goes into a thread. Without that the sync of a big library would hold the event loop and the
        # whole web would not answer during the scan.
        #
        # Before that a question to Jellyfin about which "old" items it still knows: a second copy of the same movie
        # in a later batch or in another library has a stamp from last time just like an item that disappeared
        # (_live_among_old).
        protected = await _live_among_old(client, list(seen_tmdb), {str(r[0]) for r in batch if r[0]}, now)
        await asyncio.to_thread(
            _write_batch, list(batch), list(stream_batch),
            list(seen_tmdb), not use_jellyfin_tech, now, protected
        )
        await asyncio.to_thread(write_versions, list(version_batch))
        _add_progress(len(batch))

    skipped = 0
    async for item in client.iter_items(parent_id=library["id"]):
        # A guard on the item's kind. The server asks only for movies and episodes, but Jellyfin 12 can send a
        # collection instead of the movie in it (see CollapseBoxSetItems in jellyfin.py). What is neither a movie
        # nor an episode is not written - otherwise `_mark_missing()` would send it to the archive every night and an
        # "archived" collection would haunt the library.
        if is_wrapper(item):
            skipped += 1
            continue
        tech = extract_tech_from_item(item) if use_jellyfin_tech else {}
        if use_jellyfin_tech:
            streams = extract_streams(item)
            if streams:
                stream_batch.append((item.get("Id"), streams))
        # Versions are collected always, with the source "ffprobe" too: they are files, not measured values - and
        # only Jellyfin tells that a title lies on the disk twice.
        version_batch.append((str(item.get("Id") or ""), extract_versions(item)))

        identity = item_identity(item)
        if identity and item.get("Id"):
            seen_tmdb.append((identity, str(item["Id"])))

        batch.append(_item_row(item, library["id"], tech, now))
        count += 1

        # We write in batches. One INSERT per item would mean tens of thousands of separate transactions with a big
        # library, and the scan would drag.
        if len(batch) >= 200:
            await flush_batch()
            batch.clear()
            stream_batch.clear()
            version_batch.clear()
            seen_tmdb.clear()

        # We stop only here - the item at hand is done and written. With a big library this is the only place to end
        # in time; a check between libraries would mean waiting even tens of minutes with one big library.
        if stop_requested():
            break

    if batch or stream_batch or seen_tmdb:
        await flush_batch()

    if skipped:
        log.warning("library %s: Jellyfin sent %s items that are neither "
                    "movies nor episodes (collections?), skipped",
                    library.get("name"), skipped)
    return count
