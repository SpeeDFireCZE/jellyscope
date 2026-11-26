"""The page of Seerr's newest requests for the live card "Latest requests".

The table `seerr_request` is filled by the sync (every hour by default), so the card used to lag: a request made a
minute ago was missing, and one approved meanwhile still said "waiting" while "Waiting for approval" next to it
already knew better. Now the card reads one page of the newest requests straight from Seerr. This file reads that
page (`read_page`) and turns it into rows; live.py loads it together with the pending requests under one entry of
its one-minute cache (`live.latest_requests`), every request waiting at most LIVE_TIMEOUT (3 s), and looks up the
names and poster paths of titles the table does not know yet once for both cards. Nothing is stored: the sync stays
the only writer.

The rows have the shape of the table's rows (`sync.request_row`), with the stored title and the requester as a
Jellyscope viewer (`user_id`, `user_name`, as stats.seerr_events gives them); stats.seerr_with_live lays them over
the period's stored requests. A Seerr that does not answer: `available: False`, and the card shows the stored
requests.
"""
from __future__ import annotations

import asyncio
from typing import Any

from .. import db
from ..services import integer
from .client import SeerrClient
from .sync import _page_rows, normalize_id

# One request for as many as the card shows at most (cards_seerr.ROWS, insights.INSIGHT_ROWS) - guarded by
# tests/test_seerr_cards.py.
PAGE_SIZE = 100


def empty() -> dict[str, Any]:
    """The card's state before (or without) an answer."""
    return {"available": False, "error": "", "requests": [], "seen": frozenset(), "span_from": None}


def _rows(records: list[Any]) -> list[dict[str, Any]]:
    """The page's records as rows of the table, the newest first: the stored title, the requester as a viewer.

    A broken record is skipped, as the sync skips it (sync._page_rows). The title only from the table by the
    request's id, as for the pending card - the rest is looked up in Seerr (live._add_titles).
    """
    rows = _page_rows(records)
    titles = {r["id"]: r["title"] for r in db.query_all_in(
        "SELECT id, title FROM seerr_request WHERE title <> '' AND id IN ({})", [r["id"] for r in rows])}
    viewers = {normalize_id(r["id"]): (str(r["id"]), str(r["name"])) for r in db.query_all("SELECT id, name FROM users")}
    for row in rows:
        row["title"] = titles.get(row["id"]) or ""
        row["deleted_at"] = None
        row["user_id"], row["user_name"] = viewers.get(row["requester_jellyfin_id"]) or ("", row["requester_name"])
    return sorted(rows, key=lambda r: (r["created_at"], r["id"]), reverse=True)


def _span_from(ids: list[int], full: bool) -> int | None:
    """The lowest request id the page speaks for: a stored request from it on that the page lacks is gone from Seerr.

    A page with fewer requests than asked for holds them all (0). A full page holds the newest ones, so it speaks
    from its lowest id on - but only when the ids really come from the highest down: a Seerr that sent another
    order would otherwise make "deleted" of requests it simply did not send. Unknown = None (nothing is gone).
    """
    if not full:
        return 0
    return min(ids) if ids and all(a > b for a, b in zip(ids, ids[1:], strict=False)) else None


async def read_page(client: SeerrClient) -> dict[str, Any]:
    """One page of the newest requests (`filter=all`, the newest first) as the card's state, the titles the table
    does not know yet still empty (live.py looks them up for both live cards at once).

    `requests` = rows in the shape of the table's rows, the newest first; `seen` = the ids the page held (a broken
    record too); `span_from` = from which id on the page holds every request (see _span_from). Raises SeerrError.
    """
    answer = await client.requests_page(0, PAGE_SIZE, "all", newest_first=True)
    records = answer.get("results")
    records = records if isinstance(records, list) else []
    ids = [number for r in records if isinstance(r, dict) and (number := integer(r.get("id"), None)) is not None]
    # Queries to the database are blocking work - in a thread, so the web runs meanwhile.
    rows = await asyncio.to_thread(_rows, records)
    return {**empty(), "available": True, "requests": rows, "seen": frozenset(ids),
            "span_from": _span_from(ids, len(records) >= PAGE_SIZE)}
