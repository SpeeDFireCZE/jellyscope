"""Sonarr's series and their counterparts in Jellyfin.

The history Jellyscope fetches from Sonarr knows a series only by its id in Sonarr and by its name.
So that the cards can link a series into the library, the sync also fetches Sonarr's list of series -
with the ids the title databases know the series by (TVDB, TMDB, IMDb).

Pairing goes **by id, not by name**: Jellyfin has the name in the language of the metadata and two
different series may share a name.

Episodes in the library have no ids of the title databases (Jellyfin does not send them for
episodes), they carry only their series' id in Jellyfin. So Jellyfin is asked here for the list of
series with their ids (Tvdb, Tmdb, Imdb) and the series' id in Jellyfin (`jellyfin_id`) is stored with
Sonarr's series.

Sonarr cannot page the list of series (tens of MB with a big library), so it is not fetched every
time: only the first time, when the history brought a series Jellyscope does not know yet, and
otherwise once a day (`REFRESH`). Series are paired with Jellyfin on every sync - that is a small
request and a new series is to get its link within an hour.

None of this is a condition of the history sync: when the list of series or Jellyfin fails, the
history is fetched anyway and the links are filled in next time.
"""
from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Any

from ... import db
from ...jellyfin import QUICK_TIMEOUT, JellyfinClient, JellyfinError, id_without_dashes, digits_only
from ...services import utc_time, integer
from .client import NAME, SETTINGS_PREFIX, SonarrClient, SonarrError

log = logging.getLogger("jellyscope.arr")

COLUMNS = ("title_id", "name", "tmdb_id", "tvdb_id", "imdb_id", "added_at", "synced_at")
# `jellyfin_id` is left out of the upsert on purpose: only the pairing with Jellyfin's series writes
# it. If every list from the app rewrote it, an unavailable Jellyfin would delete a pairing already
# done.
UPSERT = (f"INSERT INTO sonarr_title ({', '.join(COLUMNS)}) VALUES ({', '.join('?' * len(COLUMNS))})"
          " ON CONFLICT (title_id) DO UPDATE SET " + ", ".join(f"{s} = excluded.{s}" for s in COLUMNS[1:]))
_IMDB = re.compile(r"tt\d{5,12}")
# The order the series' ids are tried in: TVDB is the home of series (Sonarr keeps series by it),
# TMDB and IMDb are the fallback for a series Jellyfin has no TVDB id for.
ID_ORDER = (("tvdb_id", "Tvdb"), ("tmdb_id", "Tmdb"), ("imdb_id", "Imdb"))
# How often the list of titles is refreshed even when the history brought nothing new: because of
# corrected ids and titles that have no history yet (the calendar).
REFRESH = timedelta(days=1)
# The version of what is stored from the list of series. A list fetched by an older version is fetched
# again once at once, not after a day: version 2 added the time the title arrived in the app (`added_at`).
LIST_VERSION = "2"
# An added time before this year is no time: .NET sends year 1 for an empty date.
OLDEST_YEAR = 1990


# The settings key with the time of the last fetched list of series (UTC); the list's version is
# under MARK_KEY + "_version".
MARK_KEY = f"{SETTINGS_PREFIX}_titles"


def _needed(seen: set[int]) -> bool:
    """Is the list of series to be fetched? The first time, after a day, after an upgrade, or when the history brought an unknown series.

    `seen` are the series ids from the history just fetched. Only they are asked about, not the whole
    history: a series Sonarr no longer has would be missing from the list for ever and the list would
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
        "SELECT title_id FROM sonarr_title WHERE title_id IN ({})", sorted(seen))}
    return bool(seen - known)


def _number(value: Any) -> str:
    """An id from a title database as a text of digits, else empty (0 = the app does not know it)."""
    text = str(value if value is not None else "").strip()
    return text if digits_only(text) and int(text) > 0 else ""


def _imdb(value: Any) -> str:
    text = str(value or "").strip()
    return text if _IMDB.fullmatch(text) else ""


def title_rows(records: Any) -> list[dict[str, Any]]:
    """Sonarr's list of series as rows of the table. A broken record is skipped.

    Another app's answer is input like any other: a record without a numeric id is not used and an id
    from a title database that does not look like an id is stored empty. Only a few fields of every
    series are taken - the rest (cast, images, seasons) is let go at once, so the list of a big library
    does not lie in memory as a whole.
    """
    rows = []
    for record in records if isinstance(records, list) else []:
        if not isinstance(record, dict):
            continue
        series_id = integer(record.get("id"), None)
        if series_id is None:
            continue
        added = utc_time(record.get("added"))
        # The year is read from the number before the first dash: `strftime` does not pad years under 1000 with zeros.
        if added and not OLDEST_YEAR <= int(added.split("-", 1)[0]) <= 9000:
            added = None
        rows.append({"title_id": series_id, "name": str(record.get("title") or ""),
                     "tmdb_id": _number(record.get("tmdbId")), "tvdb_id": _number(record.get("tvdbId")),
                     "imdb_id": _imdb(record.get("imdbId")),
                     "added_at": added})
    return rows


def _store(rows: list[dict[str, Any]]) -> None:
    """Upsert the series. A series Sonarr no longer has stays in the table - the history still points at it."""
    now = db.utcnow()
    with db.connect() as conn:
        conn.executemany(UPSERT, [tuple({**r, "synced_at": now}[s] for s in COLUMNS) for r in rows])
        conn.commit()


async def _jellyfin_series() -> dict[str, dict[str, str]] | None:
    """Jellyfin's series by the ids of the title databases: {"Tvdb": {id: series id}, "Tmdb": …, "Imdb": …}.

    None = Jellyfin did not answer (or is not set up): nothing is known about what is in it and the
    earlier pairing is to stay.
    """
    by: dict[str, dict[str, str]] = {key: {} for _, key in ID_ORDER}
    try:
        # A short timeout: the list of series is a small request, and a Jellyfin that accepts the
        # connection and does not answer must not hold Sonarr's sync for minutes.
        async with JellyfinClient(*db.jellyfin_connection(), timeout=QUICK_TIMEOUT) as client:
            async for series in client.iter_items(item_types="Series"):
                if not isinstance(series, dict):
                    continue
                jellyfin_id = id_without_dashes(series.get("Id"))
                ids = series.get("ProviderIds")
                if not jellyfin_id or not isinstance(ids, dict):
                    continue
                # Provider names regardless of case: Jellyfin returns them as whoever wrote them
                # ("Tvdb", but also "tvdb" from a plugin).
                ids = {str(name).lower(): value for name, value in ids.items()}
                for _, key in ID_ORDER:
                    value = str(ids.get(key.lower()) or "").strip()
                    if value:
                        # Two series with the same id (the same series in two libraries): the first holds.
                        by[key].setdefault(value, jellyfin_id)
    except JellyfinError as error:
        log.info("could not load the series from Jellyfin, the matching of %s titles stays as it was: %s", NAME, error)
        return None
    return by


def _pair_series(by: dict[str, dict[str, str]]) -> int:
    """Give every Sonarr series its series id in Jellyfin (or empty). Returns the count of paired ones.

    It walks every stored series, those Sonarr no longer has too: a series that left Jellyfin loses its
    pairing - the link would lead nowhere.
    """
    rows = db.query_all("SELECT title_id, tmdb_id, tvdb_id, imdb_id, jellyfin_id FROM sonarr_title")
    changes, paired = [], 0
    for r in rows:
        found = next((by[key][str(r[column])] for column, key in ID_ORDER
                      if r[column] and str(r[column]) in by[key]), "")
        paired += bool(found)
        if found != (r["jellyfin_id"] or ""):
            changes.append((found, r["title_id"]))
    if changes:
        with db.connect() as conn:
            conn.executemany("UPDATE sonarr_title SET jellyfin_id = ? WHERE title_id = ?", changes)
            conn.commit()
    return paired


async def _fetch_list(client: SonarrClient) -> int | None:
    """Fetch and store Sonarr's list of series. Returns their count, None = it failed.

    Sonarr's error deletes nothing and does not move the fetch mark: the next sync tries again.
    """
    try:
        records = await client.titles()
    except SonarrError as error:
        log.info("could not download the title list of %s, will retry next time: %s", NAME, error)
        return None
    # Conversion and writing in a thread, so the web runs meanwhile (as with the history).
    rows = await asyncio.to_thread(title_rows, records)
    del records
    await asyncio.to_thread(_store, rows)
    db.set_setting(MARK_KEY, db.utcnow())
    db.set_setting(MARK_KEY + "_version", LIST_VERSION)
    return len(rows)


async def sync_titles(client: SonarrClient, seen: Iterable[Any] = ()) -> int:
    """Refresh Sonarr's list of series (if needed) and pair them with Jellyfin's series.

    Called by the history sync with its open client; `seen` are the series ids from the history just
    fetched (see `_needed`). Returns the count of fetched series (0 = the list was not fetched this time
    or it failed). Neither Sonarr's nor Jellyfin's error brings the history sync down: the links are
    extra, the numbers do not depend on them.
    """
    fetched = 0
    if await asyncio.to_thread(_needed, {i for i in seen if isinstance(i, int)}):
        fetched = await _fetch_list(client) or 0
    by = await _jellyfin_series()
    if by is not None:
        paired = await asyncio.to_thread(_pair_series, by)
        log.debug("%s titles: %s matched to Jellyfin series", NAME, paired)
    return fetched


def delete_titles() -> None:
    """Delete Sonarr's stored series (together with the downloaded history)."""
    with db.connect() as conn:
        conn.execute("DELETE FROM sonarr_title")
        conn.commit()
    db.set_setting(MARK_KEY, "")
    db.set_setting(MARK_KEY + "_version", "")
