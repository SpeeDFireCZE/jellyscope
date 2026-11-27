"""The one list of the API's addresses: what each opens, for which area of rights, and what it returns.

The rights check (`api._may` through `api.ADDRESS_AREAS`), the index `/api/v1`, the key's tree of rights in
Settings and the window "How to use it" all read this list - so the documentation cannot show another
address than the check lets through, and a new address is described in one place.

`path` is the route's template as the router has it (`/api/v1/item/{item_id}`); `area` the area of rights
that opens it (None = only the key's own ticks: the index, the playback history); `section` the heading of
the menu it belongs to and `label` one sentence of what it returns - both translation keys for the pages;
`summary` the English description in the JSON of the index (integrations read it); `example` a request to
copy; `needs_history` the tick "Playback history" on top of the area; `apps` the *Arr apps that have the
address (None = not an app's address).
"""
from __future__ import annotations

from dataclasses import dataclass

# The index itself: open to every key, described by itself, not listed in the documentation.
INDEX_PATHS = ("/api/v1", "/api/v1/")


@dataclass(frozen=True)
class Endpoint:
    path: str
    area: str | None
    section: str
    summary: str
    label: str
    example: str
    needs_history: bool = False
    apps: tuple[str, ...] | None = None


VIDEO, MUSIC, BOOKS, ARR, SEERR, LIBRARY, NETWORK, USERS, HISTORY = ("Movies & series", "Music", "Books", "*Arr", "Seerr",
                                                                "Library", "Network", "Users", "History")

ENDPOINTS: tuple[Endpoint, ...] = (
    Endpoint("/api/v1/summary", "overview", VIDEO,
             "watching over a period (?days=30)",
             "Watching over a period: hours watched, plays, viewers, titles, the transcode share and how much is playing right now.",
             "GET /api/v1/summary?days=30"),
    Endpoint("/api/v1/now-playing", "now_playing", VIDEO,
             "what is playing right now",
             "Playback running right now: who, what, on what, whether it is being transcoded and how far in it is.",
             "GET /api/v1/now-playing"),
    Endpoint("/api/v1/top-items", "charts", VIDEO,
             "most watched titles (?days=30&limit=10&kind=both|movies|series)",
             "Most watched titles; episodes of a series add up. kind = both / movies / series.",
             "GET /api/v1/top-items?days=30&limit=10&kind=both"),
    Endpoint("/api/v1/least-played", "charts", VIDEO,
             "least played files, never played first; within the same play count biggest first, or by date added with order=oldest|newest (?days=365&limit=25&min_size_mb=0&order=size)",
             "Files from the least played up, never played first. With the same play count the largest first, or by date added: order = oldest / newest. With the last time each was played, over the whole history.",
             "GET /api/v1/least-played?days=365&limit=25&min_size_mb=0&order=size"),
    Endpoint("/api/v1/unwatched", "charts", VIDEO,
             "files nobody watched over the period, and how much space they take (?days=365&limit=25)",
             "What nobody watched over the period and how much space it takes, as a share of the library too.",
             "GET /api/v1/unwatched?days=365&limit=25"),
    Endpoint("/api/v1/insights", "charts", VIDEO,
             "transcode offenders, upgrade candidates, oversized and never finished titles, conversion speed in fps (?days=30&limit=15)",
             "The Insights page in numbers: what gets transcoded most, what deserves better quality, large and rarely watched, never finished, and the conversion speed in frames per second.",
             "GET /api/v1/insights?days=30&limit=15"),
    Endpoint("/api/v1/play-methods", "overview", VIDEO,
             "direct play vs. transcode, and the clients used (?days=30)",
             "Direct play against transcoding, and what people watch with.",
             "GET /api/v1/play-methods?days=30"),
    Endpoint("/api/v1/languages", "languages", VIDEO,
             "the audio and subtitle languages watched over a period (?days=30)",
             "The audio and subtitle languages watched over a period, with their share.",
             "GET /api/v1/languages?days=30"),
    Endpoint("/api/v1/music/summary", "music", MUSIC,
             "music listening over a period: time, plays, listeners, artists, albums, tracks (?days=30)",
             "Music listening over a period: listening time, plays, listeners, artists, albums and tracks.",
             "GET /api/v1/music/summary?days=30"),
    Endpoint("/api/v1/music/top", "music_insights", MUSIC,
             "the most listened artists, albums or tracks (?days=30&kind=artists|albums|tracks&limit=10)",
             "The most listened artists, albums or tracks over a period. kind = artists / albums / tracks.",
             "GET /api/v1/music/top?days=30&kind=artists&limit=10"),
    Endpoint("/api/v1/music/history", "music_who", MUSIC,
             "recent listens, newest first (?days=30&limit=50&user=<id>)",
             "Recent listens, newest first: who listened to which track, when and on what.",
             "GET /api/v1/music/history?days=30&limit=50", needs_history=True),
    Endpoint("/api/v1/books/summary", "books", BOOKS,
             "audiobook listening and e-book reading over a period: times, plays, readers, works, authors (?days=30)",
             "Books over a period: audiobook listening and e-book reading time, plays, readers, works and authors.",
             "GET /api/v1/books/summary?days=30"),
    Endpoint("/api/v1/books/top", "books_insights", BOOKS,
             "the most read books or authors (?days=30&kind=books|authors&limit=10)",
             "The most read books or authors over a period. kind = books / authors.",
             "GET /api/v1/books/top?days=30&kind=books&limit=10"),
    Endpoint("/api/v1/books/history", "books_who", BOOKS,
             "recent readings and audiobook listens, newest first (?days=30&limit=50&user=<id>)",
             "Recent readings and audiobook listens, newest first: who read what, when and on what.",
             "GET /api/v1/books/history?days=30&limit=50", needs_history=True),
    Endpoint("/api/v1/arr", "arr", ARR,
             "the *Arr apps: connected, warnings, the last sync",
             "The *Arr apps: which are connected, how many warnings they report and their last sync.",
             "GET /api/v1/arr"),
    Endpoint("/api/v1/arr/{app}/queue", "arr", ARR,
             "live: an app's downloads with progress, and the queue's errors (app = sonarr|radarr|lidarr|readarr)",
             "Live: what an app is downloading, with progress and time left, and the errors in its queue.",
             "GET /api/v1/arr/<app>/queue", apps=("sonarr", "radarr", "lidarr", "readarr")),
    Endpoint("/api/v1/arr/{app}/calendar", "arr", ARR,
             "live: what an app expects to come out (?days=14; app = sonarr|radarr|lidarr|readarr)",
             "Live: what an app expects to come out in the next days.",
             "GET /api/v1/arr/<app>/calendar?days=14", apps=("sonarr", "radarr", "lidarr", "readarr")),
    Endpoint("/api/v1/arr/{app}/stats", "arr", ARR,
             "an app's statistics over a period: downloads, speed, upgrades, failures, missing, the indexers' reliability (?days=30)",
             "An app's statistics over a period: downloads, how soon after release, upgrades, failures and missing.",
             "GET /api/v1/arr/<app>/stats?days=30", apps=("sonarr", "radarr", "lidarr", "readarr", "bazarr", "prowlarr")),
    Endpoint("/api/v1/arr/{app}/indexers", "arr", ARR,
             "every indexer's availability over a period and its state now (?days=30; app = prowlarr)",
             "Every indexer's availability over a period, its outages and its state right now.",
             "GET /api/v1/arr/<app>/indexers?days=30", apps=("prowlarr",)),
    Endpoint("/api/v1/arr/{app}/health", "arr", ARR,
             "live: an app's warnings (messages only for an administrator's key)",
             "Live: the warnings an app reports; their texts only for an administrator's key.",
             "GET /api/v1/arr/<app>/health", apps=("sonarr", "radarr", "lidarr", "readarr", "bazarr", "prowlarr")),
    Endpoint("/api/v1/seerr/requests", "seerr", SEERR,
             "requests over a period, newest first; the requester only with ...who requested (?days=30&status=waiting|approved|available|declined)",
             "Requests over a period, newest first, with their state; the requester only with the right to see who requested.",
             "GET /api/v1/seerr/requests?days=30"),
    Endpoint("/api/v1/seerr/pending", "seerr", SEERR,
             "live: the requests waiting for approval",
             "Live: the requests waiting for approval and how long they have waited.",
             "GET /api/v1/seerr/pending"),
    Endpoint("/api/v1/seerr/stats", "seerr", SEERR,
             "requests over a period: counts by state, how soon approved and in the library, watched by the requester (?days=30)",
             "Requests over a period: counts by state, how soon they were approved and in the library, and whether the requester watched.",
             "GET /api/v1/seerr/stats?days=30"),
    Endpoint("/api/v1/library", "library", LIBRARY,
             "size of the library, growth and free space",
             "The size of the library, item counts, free space and the growth over a period.",
             "GET /api/v1/library"),
    Endpoint("/api/v1/libraries", "library", LIBRARY,
             "every library: items, size, codecs",
             "Every library on its own: items, size, hours of content.",
             "GET /api/v1/libraries"),
    Endpoint("/api/v1/recently-added", "library", LIBRARY,
             "what came into the library last (?limit=18)",
             "What came into the library last.",
             "GET /api/v1/recently-added?limit=18"),
    Endpoint("/api/v1/item/{item_id}", "library", LIBRARY,
             "one title: file, versions and who watched it",
             "One title: the file, its versions and who watched it.",
             "GET /api/v1/item/<id>"),
    Endpoint("/api/v1/disks", "library", LIBRARY,
             "the library's disks: capacity, free space, growth and the day they run full (?days=30)",
             "The library's disks: capacity, free space, growth and the day each runs full.",
             "GET /api/v1/disks?days=30"),
    Endpoint("/api/v1/history", None, HISTORY,
             "recent playbacks (?days=30&limit=50&user=<id>&kind=both|movies|series)",
             "Recent playbacks, newest first. No IP addresses; the API never hands those out.",
             "GET /api/v1/history?days=30&limit=50&kind=both", needs_history=True),
    Endpoint("/api/v1/bandwidth", "network", NETWORK,
             "how much data went out (?days=30); where to - home, internet, countries - only with ...where they watch from",
             "Data sent out, the peak, transcode share, home against internet and by country. Without addresses.",
             "GET /api/v1/bandwidth?days=30"),
    Endpoint("/api/v1/users", "users", USERS,
             "viewers with watch time over the period (?days=30)",
             "Viewers and their watching over the period: hours, plays, titles, transcodes.",
             "GET /api/v1/users?days=30"),
)

_BY_PATH = {endpoint.path: endpoint for endpoint in ENDPOINTS}


def by_path(path: str) -> Endpoint | None:
    """The entry of one address (the route's template), or None."""
    return _BY_PATH.get(path)


def for_area(area: str) -> list[Endpoint]:
    """The addresses one area of rights opens, in the list's order."""
    return [endpoint for endpoint in ENDPOINTS if endpoint.area == area]


def sections() -> list[tuple[str, list[Endpoint]]]:
    """The addresses by the menu's sections, in the order of the list - for the window "How to use it"."""
    grouped: dict[str, list[Endpoint]] = {}
    for endpoint in ENDPOINTS:
        grouped.setdefault(endpoint.section, []).append(endpoint)
    return list(grouped.items())


# What a sub-area without an address of its own adds to its parent's addresses (the key's tree says it under the box).
SUB_AREA_NOTES: dict[str, str] = {
    "network_origin": "Adds home against internet and the countries to /api/v1/bandwidth.",
}


def sub_area_note(key: str) -> str:
    """The note under a sub-area's box in the key's tree; "" = none (the "...who" names have their own sentence)."""
    return SUB_AREA_NOTES.get(key, "")
