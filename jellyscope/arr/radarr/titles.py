"""Radarr's movies with the ids of the title databases.

The history Jellyscope fetches from Radarr knows a movie only by its id in Radarr and by its name. So
that the cards can link a movie into the library, the sync also fetches Radarr's list of movies - with
the ids the title databases know the movie by (TMDB, IMDb).

Pairing goes **by id, not by name**: Jellyfin has the name in the language of the metadata ("Matrix"
against "The Matrix") and two different movies may share a name. A movie has a TMDB id and Jellyscope
stores it with the movies in the library (`items.tmdb_id`) - nothing more is needed, the pairing
happens only when reading (Radarr's statistics, the library).

Radarr cannot page the list of movies (tens of MB with a big library), so it is not fetched every
time: only the first time, when the history brought a movie Jellyscope does not know yet, and
otherwise once a day (`REFRESH`).

None of this is a condition of the history sync: when the list of movies fails, the history is
fetched anyway and the links are filled in next time.
"""
from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Any

from ... import db
from ...jellyfin import digits_only
from ...services import utc_time, integer
from .client import NAME, SETTINGS_PREFIX, RadarrClient, RadarrError

log = logging.getLogger("jellyscope.arr")

COLUMNS = ("title_id", "name", "tmdb_id", "imdb_id", "added_at", "synced_at")
UPSERT = (f"INSERT INTO radarr_title ({', '.join(COLUMNS)}) VALUES ({', '.join('?' * len(COLUMNS))})"
          " ON CONFLICT (title_id) DO UPDATE SET " + ", ".join(f"{s} = excluded.{s}" for s in COLUMNS[1:]))
_IMDB = re.compile(r"tt\d{5,12}")
# How often the list of titles is refreshed even when the history brought nothing new: because of
# corrected ids and titles that have no history yet (the calendar).
REFRESH = timedelta(days=1)
# The version of what is stored from the list of movies. A list fetched by an older version is fetched
# again once at once, not after a day: version 2 added the time the title arrived in the app (`added_at`).
LIST_VERSION = "2"
# An added time before this year is no time: .NET sends year 1 for an empty date.
OLDEST_YEAR = 1990


# The settings key with the time of the last fetched list of movies (UTC); the list's version is
# under MARK_KEY + "_version".
MARK_KEY = f"{SETTINGS_PREFIX}_titles"


def _needed(seen: set[int]) -> bool:
    """Is the list of movies to be fetched? The first time, after a day, after an upgrade, or when the history brought an unknown movie.

    `seen` are the movie ids from the history just fetched. Only they are asked about, not the whole
    history: a movie Radarr no longer has would be missing from the list for ever and the list would
    be fetched on every sync because of it. After an upgrade (a list fetched by an older version, see
    LIST_VERSION) once at once.
    """
    if (db.get_setting(MARK_KEY + "_version", "") or "") != LIST_VERSION:
        return True
    last = db.get_setting(MARK_KEY, "") or ""
    try:
        age = datetime.strptime(db.utcnow(), db.TIME_FORMAT) - datetime.strptime(last, db.TIME_FORMAT)
    except ValueError:
        return True                 # never yet (or a mark that is no time)
    if age >= REFRESH or age < timedelta(0):
        return True
    known = {r["title_id"] for r in db.query_all_in(
        "SELECT title_id FROM radarr_title WHERE title_id IN ({})", sorted(seen))}
    return bool(seen - known)


def _number(value: Any) -> str:
    """An id from a title database as a text of digits, else empty (0 = the app does not know it)."""
    text = str(value if value is not None else "").strip()
    return text if digits_only(text) and int(text) > 0 else ""


def _imdb(value: Any) -> str:
    text = str(value or "").strip()
    return text if _IMDB.fullmatch(text) else ""


def title_rows(records: Any) -> list[dict[str, Any]]:
    """Radarr's list of movies as rows of the table. A broken record is skipped.

    Another app's answer is input like any other: a record without a numeric id is not used and an id
    from a title database that does not look like an id is stored empty. Only a few fields of every
    movie are taken - the rest (cast, images, files) is let go at once, so the list of a big library
    does not lie in memory as a whole.
    """
    rows = []
    for record in records if isinstance(records, list) else []:
        if not isinstance(record, dict):
            continue
        movie_id = integer(record.get("id"), None)
        if movie_id is None:
            continue
        added = utc_time(record.get("added"))
        # The year is read from the number before the first dash: `strftime` does not pad years under 1000 with zeros.
        if added and not OLDEST_YEAR <= int(added.split("-", 1)[0]) <= 9000:
            added = None
        rows.append({"title_id": movie_id, "name": str(record.get("title") or ""),
                     "tmdb_id": _number(record.get("tmdbId")), "imdb_id": _imdb(record.get("imdbId")),
                     "added_at": added})
    return rows


def _store(rows: list[dict[str, Any]]) -> None:
    """Upsert the movies. A movie Radarr no longer has stays in the table - the history still points at it."""
    now = db.utcnow()
    with db.connect() as conn:
        conn.executemany(UPSERT, [tuple({**r, "synced_at": now}[s] for s in COLUMNS) for r in rows])
        conn.commit()


async def _fetch_list(client: RadarrClient) -> int | None:
    """Fetch and store Radarr's list of movies. Returns their count, None = it failed.

    Radarr's error deletes nothing and does not move the fetch mark: the next sync tries again.
    """
    try:
        records = await client.titles()
    except RadarrError as error:
        log.info("could not download the title list of %s, will retry next time: %s", NAME, error)
        return None
    # Conversion and writing in a thread, so the web runs meanwhile (as with the history).
    rows = await asyncio.to_thread(title_rows, records)
    del records
    await asyncio.to_thread(_store, rows)
    db.set_setting(MARK_KEY, db.utcnow())
    db.set_setting(MARK_KEY + "_version", LIST_VERSION)
    return len(rows)


async def sync_titles(client: RadarrClient, seen: Iterable[Any] = ()) -> int:
    """Refresh Radarr's list of movies, if needed.

    Called by the history sync with its open client; `seen` are the movie ids from the history just
    fetched (see `_needed`). Returns the count of fetched movies (0 = the list was not fetched this time
    or it failed). Radarr's error does not bring the history sync down: the links are extra, the
    numbers do not depend on them.
    """
    if await asyncio.to_thread(_needed, {i for i in seen if isinstance(i, int)}):
        return await _fetch_list(client) or 0
    return 0


def delete_titles() -> None:
    """Delete Radarr's stored movies (together with the downloaded history)."""
    with db.connect() as conn:
        conn.execute("DELETE FROM radarr_title")
        conn.commit()
    db.set_setting(MARK_KEY, "")
    db.set_setting(MARK_KEY + "_version", "")
