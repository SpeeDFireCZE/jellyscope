"""The cards of the Books page - each is a card of the registry and can go into a custom dashboard too.

Books are laid out like Music and Movies & series: `PAGES` says which page each card is on - the overview
(/books/overview, web/books/overview.py), the insights (/books/insights: the full charts and what nobody reads, area
`books_insights`) and the library's cards (the Library page's Books section and a books library); in a dashboard they
carry the prefix "Books · ". They are available only when Jellyfin has some book (books.has_books) - otherwise they
are not offered and a dashboard leaves them out.

The statistics compute the data (stats/books_listening.py - listening and reading, stats/books_library.py - the
library). The period's rows are selected once for the whole computation (`_analysis`, the memo of one computation
`_once`) and every card reads from it.

The readers' names are shown only by the "Readers and listeners" card with its own area "...including who read and
listened" (`books_who`); whoever does not have it does not get the card and its data are not computed. The other cards
carry no names at all.

Reading e-books: Jellyfin may not report it. Then the numbers card says so (`reading_reported`) instead of showing an
empty tile.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from . import books, insights, stats
from .cards_base import PLAYS, Card, _once

NAME = books.NAME

# The pages of Books and the names of their cards, in the order on the page (the layout of each is in its template;
# that they agree is guarded by tests/test_books_cards.py and tests/test_books_web.py).
PAGES: dict[str, tuple[str, ...]] = {
    "overview": ("new", "numbers", "by_day", "readers", "most_read", "method", "clients", "when"),
    "insights": ("works", "authors", "genres", "unread"),
    "library": ("library", "formats", "years", "library_genres", "largest"),
}
NAMES: tuple[str, ...] = tuple(name for names in PAGES.values() for name in names)
# The library's cards describe what is in it now - the period is not their business.
WITHOUT_PERIOD = {"library", "formats", "years", "new", "library_genres", "largest", "unread"}

ROWS = insights.INSIGHT_ROWS        # how many rows of a long list a card gets at most (the rest in a window)
RECENT_BOOKS = 20                   # the strip of newly added books
MOST_READ = 5                       # the short chart of the overview; the whole on Insights
COVER_WIDTH = 300                   # like the "Recently added" posters - the same file in the image cache


def card_key(name: str) -> str:
    """The card's key in the registry: `books_<name>` - stored in the layouts of dashboards."""
    return f"books_{name}"


def books_period(period: Any) -> Any:
    """The period of the books cards: "the whole history" starts with the first row of books, not the first playback."""
    if stats._as_period(period).whole_history:
        return stats.whole_history_period(first=stats.books_first())
    return period


def cover(book_id: Any, tag: Any) -> str:
    """The address of the book's cover (an image from Jellyfin through /image/), or "" - then a substitute is drawn."""
    return f"/image/{book_id}?kind=Primary&w={COVER_WIDTH}&tag={tag}" if book_id and tag else ""


def book_link(book_id: Any) -> str | None:
    """The book's page (the Library's, like an album)."""
    return f"/book/{book_id}" if book_id else None


def _analysis(period: Any) -> dict[str, Any]:
    return _once(stats.books_analysis, period)


def _hours(rows: list[dict[str, Any]], label_key: str) -> list[dict[str, Any]]:
    """Rows for a bar chart in hours (charts.hbar_chart)."""
    return [{"label": r[label_key], "hours": r["seconds"] / 3600} for r in rows]


def _with_links(works: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{**w, "cover": cover(w["id"], w["cover_tag"]), "link": book_link(w["id"])} for w in works]


# ---------------------------------------------------------------------------
# The data of the single cards - a dict the template reads as `d`
# ---------------------------------------------------------------------------

def _d_numbers(period: Any) -> dict[str, Any]:
    return {**stats.books_numbers(_analysis(period)), "reading_reported": _once(stats.reading_reported)}


def _d_by_day(period: Any) -> dict[str, Any]:
    by_day = [{"day": r["day"], "hours": (r["listening"] + r["reading"]) / 3600} for r in stats.books_by_day(_analysis(period))]
    return {"body": stats.chart_days(by_day, period, ("hours",))}


def _d_when(period: Any) -> dict[str, Any]:
    return {"heatmap_data": stats.books_heatmap(_analysis(period))}


def _d_readers(period: Any) -> dict[str, Any]:
    rows = stats.books_readers(_analysis(period))[:ROWS]
    return {"rows": [{**r, "work_link": book_link(r["work_id"])} for r in rows]}


def _d_works(period: Any) -> dict[str, Any]:
    return {"rows": _with_links(stats.books_works(_analysis(period), ROWS))}


def _d_most_read(period: Any) -> dict[str, Any]:
    return {"rows": _d_works(period)["rows"][:MOST_READ]}


def _d_authors(period: Any) -> dict[str, Any]:
    return {"rows": stats.books_authors(_analysis(period), ROWS)}


def _d_genres(period: Any) -> dict[str, Any]:
    genres = stats.books_genres(_analysis(period))
    # The chart shows the first twelve; the table in the window all of them (at most ROWS).
    return {"rows": _hours(genres, "genre"), "table": genres[:ROWS]}


def _library_works() -> list[dict[str, Any]]:
    """The library's works once for the whole computation - the library cards each compute from them."""
    return _once(stats.books_library_works)


def _d_library(period: Any) -> dict[str, Any]:
    return stats.books_library_summary(_library_works())


def _d_formats(period: Any) -> dict[str, Any]:
    return {"rows": stats.books_formats(_library_works())}


def _d_years(period: Any) -> dict[str, Any]:
    return {"rows": stats.books_decades(_library_works())}


def _d_new(period: Any) -> dict[str, Any]:
    return {"rows": _with_links(stats.books_newest(_library_works(), RECENT_BOOKS))}


def _d_library_genres(period: Any) -> dict[str, Any]:
    return {"rows": stats.books_library_genres(_library_works())[:ROWS]}


def _d_largest(period: Any) -> dict[str, Any]:
    return {"rows": _with_links(stats.books_largest(_library_works(), ROWS))}


def _d_unread(period: Any) -> dict[str, Any]:
    return {"rows": _with_links(stats.books_unread(_library_works(), ROWS))}


def _d_method(period: Any) -> dict[str, Any]:
    return stats.books_play_method(_analysis(period))


def _d_clients(period: Any) -> dict[str, Any]:
    return {"rows": stats.books_apps(_analysis(period))}


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def _card(name: str, title: str, description: str, data: Callable[[Any], dict[str, Any]], *,
          width: str = "full", area: str = "books", table_window: bool = False, sources: tuple[str, ...] = ()) -> Card:
    """One books card. `data(period)` returns a dict for the template; the registry gets it under the card's key.
    `sources` only where the default (cards_base.default_sources) does not hold."""
    key = card_key(name)

    def compute(period: Any) -> dict[str, Any]:
        return {key: data(_once(books_period, period))}

    return Card(key, title, description, f"_card_books_{name}.html", compute, uses_period=name not in WITHOUT_PERIOD,
                width=width, area=area, has_table_window=table_window, group=NAME, available=books.has_books,
                sources=sources)


CARDS: tuple[Card, ...] = (
    _card("new", "Newly added books", "Books and audiobooks added to the library most recently, newest first.", _d_new, table_window=True),
    _card("numbers", "Reading and listening numbers",
          "How much was listened to and read: hours of audiobooks, reading time, readers, books and authors.", _d_numbers),
    _card("by_day", "Reading and listening by day", "Hours of listening and reading day by day.", _d_by_day),
    # An area of its own: the card is all about people.
    _card("readers", "Readers and listeners", "Who reads and listens how much and which book the most.", _d_readers,
          width="half", area="books_who", table_window=True),
    _card("most_read", "Most read", "The five books with the most hours in the period; the whole charts are on Insights.",
          _d_most_read, width="half"),
    _card("method", "Audiobooks: direct or transcoded",
          "How much listening to audiobooks played directly and how much had to be transcoded, and why.", _d_method, width="half"),
    _card("clients", "Apps and devices", "What people read and listen in: hours, how many devices.", _d_clients,
          width="half", table_window=True),
    _card("when", "When people read and listen", "A grid of days and hours: when people read and listen the most.", _d_when),
    _card("works", "Most read books", "Books and audiobooks with the most hours, with their cover; the files of one audiobook are one book.",
          _d_works, area="books_insights", table_window=True),
    _card("authors", "Most read authors", "Authors with the most hours of reading and listening.", _d_authors,
          width="half", area="books_insights", table_window=True),
    _card("genres", "Genres by reading", "How many hours each genre was read and listened to.", _d_genres,
          width="half", area="books_insights", table_window=True),
    _card("unread", "What nobody reads", "Books without a single opening in the whole history, longest in the library first.",
          _d_unread, area="books_insights", table_window=True, sources=(PLAYS, "books")),
    _card("library", "Books library numbers", "How many e-books, audiobooks and authors there are, how big the library is and how long the audiobooks play.",
          _d_library),
    _card("formats", "File formats", "EPUB, PDF, M4B, MP3…: how many files and how much space.", _d_formats, width="third", table_window=True),
    _card("years", "Release years", "Books by decade of release.", _d_years, width="third"),
    _card("library_genres", "Genres in the library", "How many books each genre has.", _d_library_genres, width="third", table_window=True),
    _card("largest", "Largest books", "Books that take up the most space.", _d_largest, table_window=True),
)

# Not `assert`: Python with -O leaves it out and a difference between PAGES and the cards would show only on the page.
if [card.key for card in CARDS] != [card_key(name) for name in NAMES]:
    raise RuntimeError("the books cards do not match the pages (cards_books.PAGES)")
