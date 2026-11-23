"""Copies of the same movie in one library: which item stands for the record.

Two files of the same movie in two folders of one library are two items with the same TMDB id in Jellyfin.
Jellyscope kept them as two records - two tiles, two titles in a chart. An episode of a series twice is under
one record all the same (it belongs to the series).

Nothing is merged in the database: both items and their plays stay as they are (the technical statistics
stand on files). The scanner only notes the main one at the secondary copy - the column `items.main_id` -
and the lists of titles group by it (`COALESCE(series_id, main_id, id)`).

Copies = movies that are in Jellyfin (`is_missing = 0`), in the same library and with the same TMDB id. A
movie without a TMDB id is not merged (two different movies without an id cannot be told apart), nor is a
movie in another library (the user's decision: every library has its own record). The main one is the copy
that has been in the library the longest - it does not change with plays, so links to the title hold.

The module is a leaf of the package: it reaches no other submodule of the scanner.
"""
from __future__ import annotations

import logging
from typing import Any

from .. import db

log = logging.getLogger("jellyscope.scanner")


def _order(item: dict[str, Any]) -> tuple[bool, str, str]:
    """The order of copies: the longest in the library first; without a date after the others, a lower id on a tie.

    In Python, not in SQL: where an empty date goes when sorting differs between SQLite and PostgreSQL.
    """
    return (item["date_created"] is None, str(item["date_created"] or ""), str(item["id"]))


def _expected() -> dict[str, str]:
    """{id of a secondary copy: id of the main one} by today's state of the library."""
    groups = {(str(r["library_id"]), str(r["tmdb_id"])) for r in db.query_all(
        "SELECT library_id, tmdb_id FROM items WHERE type = 'Movie' AND is_missing = 0"
        " AND tmdb_id IS NOT NULL AND tmdb_id <> '' AND library_id IS NOT NULL"
        " GROUP BY library_id, tmdb_id HAVING COUNT(*) > 1")}
    copies: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for r in db.query_all_in(
            "SELECT id, library_id, tmdb_id, date_created FROM items"
            " WHERE type = 'Movie' AND is_missing = 0 AND library_id IS NOT NULL AND tmdb_id IN ({})",
            sorted({tmdb for _, tmdb in groups})):
        key = (str(r["library_id"]), str(r["tmdb_id"]))
        if key in groups:           # the same TMDB id in another library, where the movie is only once
            copies.setdefault(key, []).append(r)
    expected: dict[str, str] = {}
    for items in copies.values():
        main, *secondary = sorted(items, key=_order)
        for item in secondary:
            expected[str(item["id"])] = str(main["id"])
    return expected


def recount_copies() -> int:
    """Write which copy of a movie stands for the record. Returns how many items changed `main_id`.

    Compares how it should be with what is in the column and writes only the differences - a usual recount
    (nothing changed) writes nothing. An item that stopped being a copy (gone from Jellyfin, moved to another
    library, left alone) gets the column emptied.
    """
    expected = _expected()
    current = {str(r["id"]): str(r["main_id"]) for r in db.query_all(
        "SELECT id, main_id FROM items WHERE main_id IS NOT NULL")}
    changes = [(expected.get(i), i) for i in sorted(set(expected) | set(current)) if expected.get(i) != current.get(i)]
    if changes:
        with db.connect() as conn:
            conn.executemany("UPDATE items SET main_id = ? WHERE id = ?", changes)
            conn.commit()
        log.info("copies of a movie: %s items changed their main copy (%s copies in total)", len(changes), len(expected))
    return len(changes)


def recount_copies_after_sync() -> int:
    """The recount at the end of a sync and at start-up: an error is logged and brings nothing down.

    The library is downloaded and stored by then; that the copies could not be marked is no reason to report
    the sync as failed. It is tried again at the next one.
    """
    try:
        return recount_copies()
    except Exception as error:  # noqa: BLE001 - must not bring down the sync nor the start-up
        log.warning("marking the copies of movies failed: %s", error)
        return 0
