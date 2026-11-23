"""A books library in the Library: /library/<id> of a books library, with three tabs.

Part of the web package (see jellyscope/web/__init__.py), registered in web/library_kinds.py. Like a music library, a
books library has

* **Overview** - what it holds now: e-books, audiobooks, authors, size, length of the audiobooks, and the books
  library cards (formats, decades, genres, the largest books) over this one library;
* **Books** - the grid of its books (covers, search by title, author or series, sorting, 60 to a page); a book leads
  to its page, /book/<id> (web/book.py);
* **Activity** - listening and reading of the chosen period: time, readers, books, the most read books; the readers and
  the last rows only with the area "...including who read and listened" (the same rule as the book page).

Overview and Activity need the Books area; the Books tab is the Library's.

The module also gives the Books section of the Library page (library_summary).
"""
from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse

from .. import access, cards_books
from ..stats import reading_reported
from ..stats.books_library import (
    WORK_SORTS,
    books_library_listening,
    books_library_overview,
    library_book_works,
)
from .base import _context, templates
from .periods import _days

# How long a search may be - the page puts it back into the form and the pager's links.
SEARCH_MAX = 200
TABS = ("overview", "books", "activity")
# The permission area of the books statistics (Overview, Activity, the Library page section) - one place, read by
# web/library_kinds.py too. The Books tab is the Library's.
AREA = "books"
# The library's cards of Books (cards_books.PAGES["library"]) without the numbers card - those are the tiles above them.
# One list for a library's Overview and for the Library page's section; the templates get it as `card_keys`.
LIBRARY_CARDS = tuple(cards_books.card_key(name) for name in cards_books.PAGES["library"] if name != "library")


def _cards() -> dict[str, Any]:
    """The registry entries of the library cards (their template and name), by key."""
    return {card.key: card for card in cards_books.CARDS if card.key in LIBRARY_CARDS}


def _card_data(overview: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    """The cards' data; long lists capped like on the Books page (the rest is in the window)."""
    data = {key: overview[key] for key in keys}
    if "books_library_genres" in data:
        data["books_library_genres"] = {"rows": data["books_library_genres"]["rows"][:cards_books.ROWS]}
    if "books_largest" in data:              # with the book's page and the cover, like on the Books page
        data["books_largest"] = {"rows": cards_books._with_links(data["books_largest"]["rows"][:cards_books.ROWS])}
    return data


def _int(value: str | None) -> int | None:
    try:
        return int(value) if value else None
    except ValueError:
        return None


def _books_tab(request: Request, library: dict[str, Any]) -> dict[str, Any]:
    query = request.query_params
    search = (query.get("search") or "").strip()[:SEARCH_MAX]
    sort = query.get("sort") if query.get("sort") in WORK_SORTS else "added"
    result = library_book_works(library["id"], search=search, sort=sort, page=_int(query.get("page")) or 1)
    books = [{**work, "cover": cards_books.cover(work["id"], work["cover_tag"]), "link": cards_books.book_link(work["id"])}
             for work in result["works"]]
    return {"books": books, "search": search, "sort": sort, "page": result["page"],
            "pages": result["pages"], "total": result["total"]}


def _activity_tab(request: Request, library: dict[str, Any], account: dict[str, Any]) -> dict[str, Any]:
    query = request.query_params
    # "Whole history" starts with the first row of books, as everywhere in Books.
    days = cards_books.books_period(_days(request, _int(query.get("days")), query.get("since"), query.get("until")))
    activity = books_library_listening(library["id"], days)
    if not access.account_sees(account, "books_who"):
        activity["readers"] = None
        activity["recent"] = None
    else:
        activity["recent"] = [{**row, "link": cards_books.book_link(row["item_id"])} for row in activity["recent"]]
    # Reading is reported by Jellyfin or not; when never, the tile says so instead of a zero.
    return {"days": days, "activity": activity, "reading_reported": reading_reported()}


def library_page(request: Request, library: dict[str, Any], account: dict[str, Any]) -> HTMLResponse:
    """One books library: the tab from the address (?tab=), Overview by default.

    Overview and Activity are books statistics - the Books area; without it the account has the Books tab only (the
    grid is the Library's).
    """
    tabs = TABS if access.account_sees(account, AREA) else ("books",)
    tab = request.query_params.get("tab")
    tab = tab if tab in tabs else tabs[0]
    context: dict[str, Any] = {"library": library, "tab": tab, "tabs": tabs}
    if tab == "books":
        context.update(_books_tab(request, library))
    elif tab == "activity":
        context.update(_activity_tab(request, library, account))
    else:
        overview = books_library_overview(library["id"], LIBRARY_CARDS)
        context.update(overview=overview["numbers"], books_cards=_cards(), card_keys=LIBRARY_CARDS,
                       books_card_data=_card_data(overview, LIBRARY_CARDS))
    return templates.TemplateResponse(request, "library/books.html", _context(request, account, **context))


def library_summary() -> dict[str, Any]:
    """The Books section of the Library page: every books library together (only its cards, LIBRARY_CARDS)."""
    overview = books_library_overview(cards=LIBRARY_CARDS)
    return {"numbers": overview["numbers"], "cards": _cards(), "card_data": _card_data(overview, LIBRARY_CARDS),
            "card_keys": LIBRARY_CARDS}
