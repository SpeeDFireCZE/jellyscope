"""The cards of the Music page - each is a card of the registry and can go into a custom dashboard too.

Music is laid out like Movies & series (the user, 22. 11. 2026): `PAGES` says which page each card is on - the
overview (/music/overview, web/music/overview.py), the insights (/music/insights: the charts and what nobody
listens to, area `music_insights`) and the library's cards; in a dashboard they carry the prefix "Hudba · ". They are
available only when Jellyfin has some music (music.has_music: at least one track that did
not vanish) - otherwise they are not offered and a dashboard leaves them out.

The statistics compute the data (stats/music_listening.py - listening, stats/music_library.py
- the library). The period's listening is selected once for the whole computation
(`_analysis`, the memo of one computation `_once`) and every card reads from it.

The listeners' names are shown only by the "Listeners" card with its own area
"...including who listened" (`music_who`); whoever does not have it does not get the card
and its data are not computed. The other cards carry no viewers' names at all.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from . import insights, music, stats
from .cards_base import PLAYS, Card, _once

NAME = music.NAME

# The pages of Music and the names of their cards, in the order on the page (the layout of each is in its template;
# that they agree is guarded by tests/test_music_cards.py and tests/test_music_web.py). Like Movies & series: the
# overview at a glance in the order of the video one (a short chart beside the listeners), the full charts and what
# nobody listens to on the insights, the library's cards on the Library page (web/music_library.py).
PAGES: dict[str, tuple[str, ...]] = {
    "overview": ("new", "numbers", "by_day", "listeners", "most_played", "method", "clients", "network", "when"),
    "insights": ("albums", "artists", "tracks", "genres", "unplayed"),
    "library": ("library", "formats", "quality", "years", "library_genres", "largest"),
}
NAMES: tuple[str, ...] = tuple(name for names in PAGES.values() for name in names)
# The library's cards describe what is in it now - the period is not their business.
WITHOUT_PERIOD = {"library", "formats", "quality", "years", "new", "library_genres", "largest", "unplayed"}

ROWS = insights.INSIGHT_ROWS        # how many rows of a long list a card gets at most (the rest in a window)
RECENT_ALBUMS = 20                    # the strip of newly added albums
MOST_PLAYED = 5                       # the short chart of the overview (like "Most watched titles"); the whole on Insights
COVER_WIDTH = 300                     # like the "Recently added" posters - the same file in the image cache


def card_key(name: str) -> str:
    """The card's key in the registry: `music_<name>` - stored in the layouts of dashboards (before 1.9.0
    `hudba_<Czech name>`, converted by schema_names)."""
    return f"music_{name}"


def music_period(period: Any) -> Any:
    """The period of the music cards: "the whole history" starts with the first listening, not the first playback."""
    if stats._as_period(period).whole_history:
        return stats.whole_history_period(first=stats.music_first())
    return period


def cover(album_id: Any, tag: Any) -> str:
    """The address of the album's cover (an image from Jellyfin through /image/), or "" - then a substitute is drawn."""
    return f"/image/{album_id}?kind=Primary&w={COVER_WIDTH}&tag={tag}" if album_id and tag else ""


def artist_link(artist_id: Any) -> str | None:
    return f"/music/artist/{artist_id}" if artist_id else None


def album_link(album_id: Any, track_id: Any = None) -> str | None:
    """The album's page; for a track right at its row (a track has no page of its own)."""
    if not album_id:
        return None
    return f"/album/{album_id}" + (f"#track-{track_id}" if track_id else "")


def _analysis(period: Any) -> dict[str, Any]:
    return _once(stats.music_analysis, period)


def _hours(rows: list[dict[str, Any]], label_key: str) -> list[dict[str, Any]]:
    """Rows for a bar chart in hours (charts.hbar_chart)."""
    return [{"label": r[label_key], "hours": r["seconds"] / 3600} for r in rows]


# ---------------------------------------------------------------------------
# The data of the single cards - a dict the template reads as `d`
# ---------------------------------------------------------------------------

def _d_numbers(period: Any) -> dict[str, Any]:
    return stats.music_numbers(_analysis(period))


def _d_by_day(period: Any) -> dict[str, Any]:
    by_day = [{"day": r["day"], "hours": r["seconds"] / 3600, "plays": r["plays"]}
              for r in stats.music_by_day(_analysis(period))]
    return {"body": stats.chart_days(by_day, period, ("hours", "plays"))}


def _d_when(period: Any) -> dict[str, Any]:
    return {"heatmap_data": stats.music_heatmap(_analysis(period))}


def _d_listeners(period: Any) -> dict[str, Any]:
    rows = stats.music_listeners(_analysis(period))[:ROWS]
    return {"rows": [{**r, "artist_link": artist_link(r["artist_id"])} for r in rows]}


def _d_albums(period: Any) -> dict[str, Any]:
    return {"rows": [{**a, "cover": cover(a["id"], a["cover_tag"]), "link": album_link(a["id"]),
                       "artist_link": artist_link(a["artist_id"])}
                      for a in stats.music_albums(_analysis(period), ROWS)]}


def _d_most_played(period: Any) -> dict[str, Any]:
    return {"rows": _d_albums(period)["rows"][:MOST_PLAYED]}


def _d_artists(period: Any) -> dict[str, Any]:
    return {"rows": [{**i, "link": artist_link(i["id"])} for i in stats.music_artists(_analysis(period), ROWS)]}


def _d_tracks(period: Any) -> dict[str, Any]:
    return {"rows": [{**s, "link": album_link(s["album_id"], s["id"]), "artist_link": artist_link(s["artist_id"])}
                      for s in stats.music_tracks(_analysis(period), ROWS)]}


def _d_genres(period: Any) -> dict[str, Any]:
    genres = stats.music_genres(_analysis(period))
    # The chart shows the first twelve; the table in the window all of them (at most ROWS) with the plays.
    return {"rows": _hours(genres, "genre"), "table": genres[:ROWS]}


def _library_tracks() -> list[dict[str, Any]]:
    """The library's tracks once for the whole computation - four library cards compute each their own from them."""
    return _once(stats.library_tracks)


def _d_library(period: Any) -> dict[str, Any]:
    return stats.music_library_summary(_library_tracks())


def _d_formats(period: Any) -> dict[str, Any]:
    return {"rows": stats.music_formats(_library_tracks())}


def _d_quality(period: Any) -> dict[str, Any]:
    return stats.music_quality(_library_tracks())


def _d_years(period: Any) -> dict[str, Any]:
    return {"rows": stats.music_decades()}


def _with_links(albums: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{**a, "cover": cover(a["id"], a["cover_tag"]), "link": album_link(a["id"]),
             "artist_link": artist_link(a["artist_id"])} for a in albums]


def _d_new(period: Any) -> dict[str, Any]:
    return {"rows": _with_links(stats.music_newest(RECENT_ALBUMS))}


def _d_library_genres(period: Any) -> dict[str, Any]:
    return {"rows": stats.library_genres(_library_tracks())[:ROWS]}


def _d_largest(period: Any) -> dict[str, Any]:
    return {"rows": _with_links(stats.music_largest(ROWS))}


def _d_unplayed(period: Any) -> dict[str, Any]:
    return {"rows": _with_links(stats.music_unplayed(ROWS))}


def _d_method(period: Any) -> dict[str, Any]:
    return stats.music_play_method(_analysis(period))


def _d_clients(period: Any) -> dict[str, Any]:
    return {"rows": stats.music_apps(_analysis(period))}


def _d_network(period: Any) -> dict[str, Any]:
    return stats.music_network(_analysis(period))


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def _card(name: str, title: str, description: str, data: Callable[[Any], dict[str, Any]], *,
          width: str = "full", area: str = "music", table_window: bool = False, sources: tuple[str, ...] = ()) -> Card:
    """One music card. `data(period)` returns a dict for the template; the registry gets it under the card's key.
    `sources` only where the default (cards_base.default_sources) does not hold."""
    key = card_key(name)

    def compute(period: Any) -> dict[str, Any]:
        return {key: data(_once(music_period, period))}

    return Card(key, title, description, f"_card_music_{name}.html", compute, uses_period=name not in WITHOUT_PERIOD,
                width=width, area=area, has_table_window=table_window, group=NAME, available=music.has_music,
                sources=sources)


CARDS: tuple[Card, ...] = (
    _card("new", "Newly added albums", "Albums added to the library most recently, newest first.", _d_new, table_window=True),
    _card("numbers", "Listening numbers", "How much was listened to: hours, plays, listeners, artists and tracks.", _d_numbers),
    _card("by_day", "Listening by day", "Hours of listening day by day.", _d_by_day),
    # An area of its own: the card is all about people.
    _card("listeners", "Listeners", "Who listens how much and which artist the most.", _d_listeners,
          width="half", area="music_who", table_window=True),
    # The short chart of the overview, like "Most watched titles" of Movies & series; the whole on Insights.
    _card("most_played", "Most played", "The five albums with the most hours of listening in the period; the whole charts are on Insights.",
          _d_most_played, width="half"),
    _card("method", "Direct or transcoded", "How much listening played directly and how much had to be transcoded, and why.", _d_method, width="half"),
    _card("clients", "Apps and devices", "What people listen in: hours, plays, how many devices.", _d_clients,
          width="half", table_window=True),
    _card("network", "Data over the network", "How much data listening sent over the network (estimated from bitrate).", _d_network),
    _card("when", "When people listen", "A grid of days and hours: when people listen the most.", _d_when),
    _card("albums", "Most played albums", "Albums with the most hours of listening, with their cover.", _d_albums, area="music_insights", table_window=True),
    _card("artists", "Most played artists", "Artists with the most hours of listening.", _d_artists,
          width="half", area="music_insights", table_window=True),
    _card("tracks", "Most played tracks", "Tracks with the most hours of listening.", _d_tracks, width="half", area="music_insights", table_window=True),
    _card("genres", "Genres by listening", "How many hours each genre was listened to.", _d_genres, area="music_insights", table_window=True),
    _card("unplayed", "What nobody listens to", "Albums without a single play in the whole history, longest in the library first.",
          _d_unplayed, area="music_insights", table_window=True, sources=(PLAYS, "music")),
    _card("library", "Music library numbers", "How many artists, albums and tracks there are, how big the library is and how long it would play.",
          _d_library),
    _card("formats", "File formats", "FLAC, MP3, AAC…: how many tracks and how much space.", _d_formats, width="third", table_window=True),
    _card("quality", "Sound quality", "Lossless and lossy sound, hi-res and bitrate.", _d_quality, width="third"),
    _card("years", "Release years", "Albums by decade of release.", _d_years, width="third"),
    _card("library_genres", "Genres in the library", "How many tracks each genre has.", _d_library_genres, width="half", table_window=True),
    _card("largest", "Largest albums", "Albums that take up the most space.", _d_largest, width="half", table_window=True),
)

# Not `assert`: Python with -O leaves it out and a difference between PAGES and the cards would show only on the page.
if [card.key for card in CARDS] != [card_key(name) for name in NAMES]:
    raise RuntimeError("the music cards do not match the pages (cards_music.PAGES)")
