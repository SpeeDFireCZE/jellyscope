"""A music library in the Library: /library/<id> of a music library, with three tabs.

Part of the web package (see jellyscope/web/__init__.py), registered in web/library_kinds.py.
Like a video library, a music library has

* **Overview** - what it holds now: albums, tracks, artists, size, length, and Music's library
  cards (formats, sound quality, genres, decades, the largest albums) over this one library;
* **Albums** - the grid of its albums (covers, search by album or artist, sorting, 60 to a
  page); an album leads to its page, /album/<id> (web/album.py);
* **Activity** - listening of the chosen period: time, plays, listeners, albums, the most
  played albums; the listeners and the last listens only with the area "...including who
  listened" (the same rule as the album page).

Overview and Activity need the Music area; the Albums tab is the Library's.

The module also gives the Music section of the Library page (library_summary).
"""
from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse

from .. import access, cards_music
from ..stats.music_library import ALBUM_SORTS, LIBRARY_CARDS, library_albums, library_listening, library_overview
from .base import _context, templates
from .periods import _days

# How long a search may be - the page puts it back into the form and the pager's links.
SEARCH_MAX = 200
TABS = ("overview", "albums", "activity")
# The permission area of music statistics (Overview, Activity, the Library page section) - one
# place, read by web/library_kinds.py too. The Albums tab is the Library's.
AREA = "music"
# The cards of the Library page's Music section: the library's cards of Music (moved from its overview, 22. 11. 2026,
# like Movies & series); the numbers of the music library are the tiles above them.
SUMMARY_CARDS = ("music_formats", "music_quality", "music_years", "music_library_genres", "music_largest")


def _cards() -> dict[str, Any]:
    """The registry entries of the library cards (their template and name), by key."""
    return {card.key: card for card in cards_music.CARDS if card.key in LIBRARY_CARDS}


def _card_data(overview: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    """The cards' data; long lists capped like on the Music page (the rest is in the window)."""
    data = {key: overview[key] for key in keys}
    if "music_library_genres" in data:
        data["music_library_genres"] = {"rows": data["music_library_genres"]["rows"][:cards_music.ROWS]}
    if "music_largest" in data:              # with the album's page, the artist's and the cover, like on the Music page
        data["music_largest"] = {"rows": cards_music._with_links(data["music_largest"]["rows"][:cards_music.ROWS])}
    return data


def _int(value: str | None) -> int | None:
    try:
        return int(value) if value else None
    except ValueError:
        return None


def _albums_tab(request: Request, library: dict[str, Any]) -> dict[str, Any]:
    query = request.query_params
    search = (query.get("search") or "").strip()[:SEARCH_MAX]
    sort = query.get("sort") if query.get("sort") in ALBUM_SORTS else "added"
    result = library_albums(library["id"], search=search, sort=sort, page=_int(query.get("page")) or 1)
    albums = [{**album, "cover": cards_music.cover(album["id"], album["cover_tag"]),
               "link": cards_music.album_link(album["id"])} for album in result["albums"]]
    return {"albums": albums, "search": search, "sort": sort, "page": result["page"],
            "pages": result["pages"], "total": result["total"]}


def _activity_tab(request: Request, library: dict[str, Any], account: dict[str, Any]) -> dict[str, Any]:
    query = request.query_params
    # "Whole history" starts with the first listen, as everywhere in music.
    days = cards_music.music_period(_days(request, _int(query.get("days")), query.get("since"), query.get("until")))
    activity = library_listening(library["id"], days)
    if not access.account_sees(account, "music_who"):
        activity["listeners"] = None
        activity["recent"] = None
    else:
        activity["recent"] = [{**row, "link": cards_music.album_link(row["album_id"], row["item_id"])}
                              for row in activity["recent"]]
    return {"days": days, "activity": activity}


def library_page(request: Request, library: dict[str, Any], account: dict[str, Any]) -> HTMLResponse:
    """One music library: the tab from the address (?tab=), Overview by default.

    Overview and Activity are music statistics - the Music area ("…the music library and how it
    is played"); without it the account has the Albums tab only (the grid is the Library's).
    """
    tabs = TABS if access.account_sees(account, AREA) else ("albums",)
    tab = request.query_params.get("tab")
    tab = tab if tab in tabs else tabs[0]
    context: dict[str, Any] = {"library": library, "tab": tab, "tabs": tabs}
    if tab == "albums":
        context.update(_albums_tab(request, library))
    elif tab == "activity":
        context.update(_activity_tab(request, library, account))
    else:
        overview = library_overview(library["id"])
        context.update(overview=overview["numbers"], music_cards=_cards(),
                       music_card_data=_card_data(overview, LIBRARY_CARDS))
    return templates.TemplateResponse(request, "library/music.html", _context(request, account, **context))


def library_summary() -> dict[str, Any]:
    """The Music section of the Library page: every music library together (only its cards, SUMMARY_CARDS)."""
    overview = library_overview(cards=SUMMARY_CARDS)
    return {"numbers": overview["numbers"], "cards": _cards(), "card_data": _card_data(overview, SUMMARY_CARDS)}
