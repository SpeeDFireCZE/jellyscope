"""The demo's made-up Readarr: the demo's books (demo_world.books) as Readarr's API v1 answers them.

The demo (JELLYSCOPE_DEMO) shows Readarr through its real code: the seed connects it at http://readarr.demo and runs
its real sync and counts (demo_apps.seed_apps), the page asks for the live state and the card "Stahuje se" for the
queue. `handler` answers them inside the process (httpx.MockTransport through demo_switch) in the shape of a real
Readarr 0.4 (tests/readarr_responses/, tests/_apps.py) - every path the package's client asks. The unit is the book,
e-books and audiobooks alike (two quality profiles, two root folders):

* `system/status` - the app's name and version (the connection test, the sync's check; the tab says Readarr);
* `history` (in pages, oldest first) and `history/since` - a grab per book, an import per file (an audiobook in parts
  has several) and one for the download, upgrades (the old files deleted, the better edition's imported) and two
  failed downloads, with the book and the author when asked. Every book of the library was imported before Jellyfin
  took it in (its `added_at`), in its files' quality (format, size, parts); the household's own scan of a comic by
  hand. Readarr knew the books of the last weeks before they came out and grabbed them after their release
  (demo_world.book_release), the older ones once their author was added (the back catalog). Upgrades and failures
  happen over the last year, counted back from the hour - one book of the last weeks got its better edition a day
  after it came (demo_world.book_upgrade);
* `book`, `edition` and `author` - the library's books with their editions (made-up Goodreads ids, ISBN-13s: the one
  the library's file carries when it does - the sync pairs the others by title and author) and their authors, and the
  books coming out (demo_world.coming_books);
* `queue` - four downloads whose progress goes round with the clock (demo_world.cycle_share), one of them stuck with
  a warning; `command` (POST) is accepted and changes nothing;
* `calendar`, `wanted/missing` (out, not downloaded), `wanted/cutoff` (below EPUB, or M4B for an audiobook);
* `health` - one warning: an update is available.

Everything else is 404, the covers (`mediacover/...`) too: the client takes 404 as "no cover", without an error. The
numbers come from the catalog and a random source of its own per book (demo_world.random_for): the same library gives
the same history. The moment is `demo_world.now()`. Registered by demo_apps.load_fakes through the registry's
`App.demo`, in the demo mode only - the door of the package does not import this module.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from ... import demo_world
from ...demo_world import span, weighted
from .client import NAME

API = "/api/v1/"
VERSION = "0.4.18.2805"
UPDATE = "0.4.19.2811"        # the version its health says is available
# (Readarr's id, name, the release's label) from the worst to the best: the e-books' profile, then the audiobooks'.
QUALITIES = ((0, "Unknown Text", ""), (1, "PDF", "PDF"), (2, "MOBI", "MOBI"), (4, "AZW3", "AZW3"), (3, "EPUB", "EPUB"),
             (13, "Unknown Audio", ""), (10, "MP3", "MP3"), (11, "FLAC", "FLAC"), (12, "M4B", "M4B"))
UNKNOWN_TEXT, PDF, MOBI, AZW3, EPUB, UNKNOWN_AUDIO, MP3, FLAC, M4B = range(len(QUALITIES))
BY_FORMAT = {"pdf": PDF, "mobi": MOBI, "azw3": AZW3, "epub": EPUB, "mp3": MP3, "flac": FLAC, "m4b": M4B}
# By "an audiobook?": the quality the profile wants (a book below it is in wanted/cutoff), the profile, the root folder.
CUTOFF = {False: EPUB, True: M4B}
PROFILES = {False: (1, "/books"), True: (2, "/audiobooks")}
# The edition a book had before its upgrade, by the quality its files have now (one of them, by chance).
WORSE = {EPUB: (MOBI, MOBI, PDF), AZW3: (PDF, MOBI), M4B: (MP3,)}
MANUAL_FORMAT = "cbz"         # the household's own scan of a comic: imported by hand, a format Readarr has no quality for
PUBLISHER = "Knihovnicka"
GROUPS = ("KNIHOMOL", "ZALOZKA", "REGAL", "SVITEK", "PERO", "LAMPA")
# How a grab started (data.releaseSource), weighted: a book Readarr waited for, of the back catalog (when its author came), an upgrade.
WAITED_SOURCES = (("Rss", 70), ("Search", 12), ("UserInvokedSearch", 8), ("InteractiveSearch", 6), ("ReleasePush", 4))
BACK_CATALOG_SOURCES = (("Search", 60), ("UserInvokedSearch", 15), ("InteractiveSearch", 25))
UPGRADE_SOURCES = (("Rss", 65), ("Search", 25), ("InteractiveSearch", 10))
# (from, to minutes, weight) from the grab to the import, by "an audiobook?": an e-book is a few MB.
DOWNLOAD = {False: ((1, 4, 60), (4, 20, 30), (20, 90, 10)), True: ((5, 30, 45), (30, 120, 40), (120, 420, 15))}
UPGRADE_SHARE = 0.6                                        # how many books with an EPUB, AZW3 or M4B got it later
UPGRADE_AGE = ((0.5, 7, 15), (7, 30, 20), (30, 330, 65))   # (from, to days ago, weight)
FAILURE_AGE = ((1.5, 5), (40, 200))                        # days ago of the two failed downloads
FAILURE_MESSAGE = "Aborted, cannot be completed"
RELEASE_AGE = 26              # minutes from a release's publishing to its grab
FILE_GAP = 2                  # seconds between the imports of a book's files
DELETION_AHEAD = 90           # seconds the old files are deleted before the better ones are imported
# Record ids: a book's number * 1000 + the record's place - a grab and the download's import or failure (SLOTS), a file's
# record its number in its block (a first import's, a deletion's, an upgrade's): the same record keeps its id at every answer.
SLOTS = {"first": (1, 2), "upgrade": (3, 4), "failed": (5, 6)}
FILES = {"first": 100, "deleted": 200, "upgrade": 300}
PROTOCOL_NUMBERS = {"usenet": "1", "torrent": "2"}
# Readarr's id of the first book coming out: the library's books have their catalog numbers, these come after them.
COMING_IDS = 101
# The queue: (key, which book, the indexer's place in demo_world.INDEXERS, the cycle in minutes; 0 = it does not move): an
# audiobook out already, M4Bs of the newest audiobooks below the cutoff, the EPUB of the newest e-book below it (blocked).
QUEUE = (("out", "coming", 0, 40), ("better", "audio", 1, 30), ("flaky", "audio", 2, 50), ("stuck", "text", 3, 0))
STUCK_MESSAGES = [{"title": "One or more books expected in this release were not imported or missing", "messages": []},
                  {"title": "", "messages": ["Book match is not close enough: 71.4% vs 80%"]}]


@dataclass
class _Author:
    """An author the made-up Readarr has: a number of its own (by the catalog's order), the name, when it was added."""

    number: int
    name: str
    audiobook: bool                            # the author's books are audiobooks (the profile, the root folder)
    added: datetime | None = None


@dataclass
class _Book:
    """A book the made-up Readarr knows: its release, when it came to Readarr, its files."""

    number: int                                # Readarr's id: the catalog's number, COMING_IDS on for those coming out
    title: str
    author: _Author
    audiobook: bool
    released: datetime | None
    isbn: str
    added: datetime | None = None              # when it came to Readarr
    entry: dict[str, Any] | None = None        # the catalog's entry; None = the library does not have it
    file: tuple[int, int, int] | None = None   # (quality, size, files); None = no files


@dataclass
class _Download:
    """One release the made-up Readarr took: where from, and when it was imported (or failed)."""

    kind: str                                  # "first", "upgrade" or "failed" (SLOTS)
    book: _Book
    quality: int                               # the place in QUALITIES
    grabbed: datetime | None                   # None = imported by hand, no download
    done: datetime                             # the import (its first file), or the failure
    size: int
    files: int
    indexer: str = ""
    protocol: str = ""
    download_id: str | None = None
    source: str = ""
    group: str = ""
    replaced: tuple[int, int, int] | None = None   # the upgraded files (see _Book.file)


@dataclass
class _World:
    """The made-up Readarr at one moment: its authors (by name), books and what it downloaded."""

    authors: dict[str, _Author] = field(default_factory=dict)
    books: list[_Book] = field(default_factory=list)
    downloads: list[_Download] = field(default_factory=list)


# ---------------------------------------------------------------------------
# The world: what the made-up Readarr downloaded
# ---------------------------------------------------------------------------

def _midnight(moment: datetime) -> datetime:
    return moment.replace(hour=0, minute=0, second=0, microsecond=0)


def _size(rng: Any, audiobook: bool) -> int:
    """The size of a release the library does not have: an audiobook of a few hundred MB, an e-book of a few."""
    return int(rng.uniform(250, 450) * 1e6) if audiobook else int(rng.uniform(1, 5) * 1e6)


def _download(rng: Any, kind: str, book: _Book, quality: int, grabbed: datetime, done: datetime, source: str,
              size: int, files: int, indexer: str = "") -> _Download:
    """A release with its indexer (by weight, the flaky one less often), client and the client's download id."""
    if not indexer:
        indexer = weighted(rng, ((name, 20 if name == demo_world.FLAKY_INDEXER else 30)
                                 for name, _protocol in demo_world.INDEXERS if name != demo_world.DISABLED_INDEXER))
    protocol = dict(demo_world.INDEXERS)[indexer]
    download_id = f"SABnzbd_nzo_{rng.getrandbits(32):08x}" if protocol == "usenet" else f"{rng.getrandbits(160):040X}"
    return _Download(kind, book, quality, grabbed, done, size, files, indexer, protocol, download_id, source, rng.choice(GROUPS))


def _first(world: _World, entry: dict[str, Any]) -> _Download:
    """The first download of a book of the library: imported before Jellyfin took it in, in its files' quality.

    A book of the last weeks Readarr waited for: it grabbed it after its release. The rest is the back catalog,
    grabbed once its author was in Readarr; their release is a day of their year.
    """
    rng = demo_world.random_for(f"readarr:{entry['number']}")
    released = demo_world.book_release(entry)
    if released:      # Jellyfin took it in a minute or two after the import
        done = entry["added_at"] - timedelta(minutes=rng.uniform(1, 3))
        minutes = min(span(rng, DOWNLOAD[entry["audiobook"]]), (done - released).total_seconds() / 120)
    else:             # Jellyfin's scan took it in up to a few hours later
        done, minutes = entry["added_at"] - timedelta(minutes=rng.uniform(2, 240)), span(rng, DOWNLOAD[entry["audiobook"]])
        if entry["year"]:
            released = min(_midnight(datetime(entry["year"], 1, 1, tzinfo=UTC) + timedelta(days=rng.uniform(0, 360))),
                           _midnight(done - timedelta(days=30)))
    book = _Book(entry["number"], entry["title"], world.authors[entry["author"]], entry["audiobook"], released,
                 entry["isbn"] or demo_world.isbn13(f"readarr:{entry['jellyfin_id']}"), entry=entry)
    if entry["format"] == MANUAL_FORMAT:
        download = _Download("first", book, UNKNOWN_TEXT, None, done, entry["size_bytes"], entry["parts"])
    else:
        quality = BY_FORMAT.get(entry["format"], UNKNOWN_AUDIO if entry["audiobook"] else UNKNOWN_TEXT)
        source = weighted(rng, WAITED_SOURCES if demo_world.book_release(entry) else BACK_CATALOG_SOURCES)
        download = _download(rng, "first", book, quality, done - timedelta(minutes=minutes), done, source, entry["size_bytes"], entry["parts"])
    book.file = (download.quality, download.size, download.files)
    world.books.append(book)
    return download


def _added(world: _World, firsts: list[_Download]) -> None:
    """When the authors and the books came to Readarr.

    An author came before its first grab (Readarr searched for its books then) and long before the release of a book
    of the last weeks: such a book came to Readarr when announced, the others with their author.
    """
    earliest: dict[str, datetime] = {}
    for d in firsts:
        own = d.book.released - timedelta(days=30) if demo_world.book_release(d.book.entry) else (d.grabbed or d.done)
        earliest[d.book.author.name] = min(earliest.get(d.book.author.name, own), own)
    for name, author in world.authors.items():
        author.added = earliest[name] - timedelta(hours=demo_world.random_for(f"readarr-author:{author.number}").uniform(1, 3))
    for d in firsts:
        rng, book = demo_world.random_for(f"readarr-added:{d.book.number}"), d.book
        if demo_world.book_release(book.entry):
            book.added = max(book.author.added + timedelta(hours=1), book.released - timedelta(days=rng.uniform(5, 40)))
        else:
            book.added = book.author.added + timedelta(seconds=rng.uniform(5, 90))


def _upgrade(first: _Download, anchor: datetime) -> _Download | None:
    """A better edition later on - or None (many books keep their first files, and none is older than its files).

    The library's files are the better ones: the first download was worse (WORSE; an M4B was a folder of MP3 chapters
    before). The book of the last weeks demo_world.book_upgrade names got its better edition that long after it came,
    from the flaky indexer (one of its releases that did come through), the others by chance in the last year.
    """
    book = first.book
    rng = demo_world.random_for(f"readarr-upgrade:{book.number}")
    after = demo_world.book_upgrade(book.entry)
    if first.grabbed is None or first.quality not in WORSE or (after is None and rng.random() >= UPGRADE_SHARE):
        return None
    imported = first.done + after if after else anchor - timedelta(days=span(rng, UPGRADE_AGE))
    if imported <= first.done + timedelta(hours=12):
        return None
    grabbed = imported - timedelta(minutes=min(span(rng, DOWNLOAD[book.audiobook]), (imported - first.done).total_seconds() / 120))
    better = _download(rng, "upgrade", book, first.quality, grabbed, imported, weighted(rng, UPGRADE_SOURCES), first.size, first.files,
                       indexer=demo_world.FLAKY_INDEXER if after else "")
    first.quality = rng.choice(WORSE[first.quality])
    first.size = int(first.size * rng.uniform(0.6, 1.3))
    first.files = rng.randint(6, 14) if first.quality == MP3 else first.files
    better.replaced = (first.quality, first.size, first.files)
    return better


def _failures(firsts: list[_Download], anchor: datetime) -> list[_Download]:
    """Two better editions that failed in the client, both from the flaky indexer: one this week, one months ago."""
    candidates = [d for d in firsts if d.grabbed and d.book.file and d.book.file[0] < CUTOFF[d.book.audiobook]
                  and d.done < anchor - timedelta(days=30)]
    result = []
    for place, (low, high) in zip(sorted({1, len(candidates) // 2}), FAILURE_AGE):
        if place >= len(candidates):
            continue
        first = candidates[place]
        rng = demo_world.random_for(f"readarr-failure:{first.book.number}")
        grabbed = anchor - timedelta(days=rng.uniform(low, high))
        if grabbed <= first.done + timedelta(days=1):
            grabbed = first.done + timedelta(days=rng.uniform(1, 5))
        failed = grabbed + timedelta(minutes=rng.uniform(10, 45))
        result.append(_download(rng, "failed", first.book, CUTOFF[first.book.audiobook], grabbed, failed, "Rss",
                                _size(rng, first.book.audiobook), 1, indexer=demo_world.FLAKY_INDEXER))
    return result


def _coming(world: _World, moment: datetime) -> None:
    """The books coming out (demo_world.coming_books): announced to Readarr weeks before, without files."""
    for place, coming in enumerate(demo_world.coming_books(moment)):
        author = world.authors.get(coming["author"])
        if author is None:
            continue
        rng = demo_world.random_for(f"readarr-coming:{coming['key']}")
        added = max(author.added + timedelta(hours=1), coming["released"] - timedelta(days=rng.uniform(10, 40), hours=rng.uniform(0, 20)))
        world.books.append(_Book(COMING_IDS + place, coming["title"], author, coming["audiobook"], coming["released"],
                                 demo_world.isbn13(coming["key"]), added=added))


def _world(moment: datetime) -> _World:
    """What the made-up Readarr has downloaded by `moment`, from the catalog of the demo's books."""
    anchor = moment.replace(minute=0, second=0, microsecond=0)
    world = _World()
    entries = [entry for entry in demo_world.books() if entry["added_at"]]
    for entry in entries:
        world.authors.setdefault(entry["author"], _Author(len(world.authors) + 1, entry["author"], entry["audiobook"]))
    firsts = [_first(world, entry) for entry in entries]
    _added(world, firsts)
    world.downloads += firsts
    world.downloads += [d for d in (_upgrade(first, anchor) for first in firsts) if d is not None]
    world.downloads += _failures(firsts, anchor)
    _coming(world, moment)
    return world


# ---------------------------------------------------------------------------
# Resources: an author, a book, an edition, a quality as Readarr writes them
# ---------------------------------------------------------------------------

def _iso(moment: datetime | None) -> str | None:
    return demo_world.iso(moment) if moment else None


def _quality(quality: int) -> dict[str, Any]:
    number, name = QUALITIES[quality][:2]
    return {"quality": {"id": number, "name": name}, "revision": {"version": 1, "real": 0, "isRepack": False}}


def _release(book: _Book, quality: int) -> str:
    year = f" ({book.released.year})" if book.released else ""
    label = QUALITIES[quality][2]
    return f"{book.author.name} - {book.title}{year}" + (f" {label}" if label else "")


def _goodreads(book: _Book, kind: str = "book") -> str:
    return demo_world.goodreads_id(f"{kind}:{book.author.name}:{book.title}")


def _author(author: _Author, full: bool = True) -> dict[str, Any]:
    """The author as Readarr's API sends it (`author`; shorter embedded in the history, the queue and a book)."""
    brief = {"id": author.number, "authorName": author.name, "foreignAuthorId": demo_world.goodreads_id(f"author:{author.name}"),
             "monitored": True, "status": "continuing"}
    if not full:
        return brief
    profile, root = PROFILES[author.audiobook]
    return {**brief, "authorMetadataId": author.number, "ended": False, "titleSlug": brief["foreignAuthorId"], "overview": "",
            "links": [], "images": [], "path": f"{root}/{author.name}", "qualityProfileId": profile, "metadataProfileId": 1,
            "monitorNewItems": "all", "rootFolderPath": root, "genres": [], "sortName": author.name.lower(), "tags": [],
            "added": _iso(author.added), "ratings": {"votes": 0, "value": 0.0, "popularity": 0.0}}


def _edition(book: _Book, own: bool = True) -> dict[str, Any]:
    """An edition: the one Readarr monitors (the e-book's or the audiobook's) or the printed one, each with its ISBN."""
    slug = _goodreads(book, "edition" if own else "print")
    isbn = book.isbn if own else demo_world.isbn13(f"print:{book.author.name}:{book.title}")
    return {"id": book.number * 10 + (0 if own else 1), "bookId": book.number, "foreignEditionId": slug, "titleSlug": slug,
            "isbn13": isbn, "asin": "B0" + _goodreads(book, "asin").zfill(8)[:8] if own else "", "title": book.title, "language": "cze",
            "overview": "", "format": ("Audiobook" if book.audiobook else "EPUB") if own else "Paperback",
            "isEbook": own and not book.audiobook, "disambiguation": "", "publisher": PUBLISHER, "pageCount": 0 if book.audiobook else 280,
            "releaseDate": _iso(book.released), "images": [], "links": [], "ratings": {"votes": 0, "value": 0.0, "popularity": 0.0},
            "monitored": own, "manualAdd": False}


def _book(book: _Book, full: bool = True) -> dict[str, Any]:
    """The book as Readarr's API sends it (`book`, the calendar, wanted; shorter embedded in the history and the queue)."""
    brief = {"id": book.number, "title": book.title, "authorId": book.author.number, "foreignBookId": _goodreads(book),
             "releaseDate": _iso(book.released), "monitored": True}
    if not full:
        return brief
    _quality_number, size, files = book.file or (0, 0, 0)
    entry = book.entry or {}
    return {**brief, "authorTitle": f"{book.author.name} {book.title}", "seriesTitle": entry.get("series", ""), "disambiguation": "",
            "overview": "", "foreignEditionId": _goodreads(book, "edition"), "titleSlug": brief["foreignBookId"], "anyEditionOk": True,
            "ratings": {"votes": 0, "value": 0.0, "popularity": 0.0}, "pageCount": 0 if book.audiobook else 280,
            "genres": entry.get("genres", []), "author": _author(book.author),
            "images": [{"url": f"/MediaCover/Books/{book.number}/cover.jpg", "coverType": "cover", "extension": ".jpg"}],
            "links": [], "added": _iso(book.added), "editions": [_edition(book)], "grabbed": False,
            "statistics": {"bookFileCount": files, "bookCount": 1, "totalBookCount": 1, "sizeOnDisk": size,
                           "percentOfBooks": 100.0 if files else 0.0}}


# ---------------------------------------------------------------------------
# The history
# ---------------------------------------------------------------------------

def _grab(download: _Download) -> dict[str, Any]:
    """The data of a grab: where it came from, how, how big."""
    published = download.grabbed - timedelta(minutes=RELEASE_AGE)
    indexer_id = [name for name, _protocol in demo_world.INDEXERS].index(download.indexer) + 1
    client = demo_world.DOWNLOAD_CLIENTS[download.protocol]
    return {"indexer": download.indexer, "releaseGroup": download.group, "age": "0", "ageHours": "0", "ageMinutes": str(RELEASE_AGE),
            "publishedDate": demo_world.iso(published), "downloadClient": client, "downloadClientName": client,
            "size": str(download.size), "protocol": PROTOCOL_NUMBERS[download.protocol], "downloadForced": "False",
            "customFormatScore": "0", "releaseSource": download.source, "indexerFlags": "0", "indexerId": str(indexer_id)}


def _file_sizes(size: int, files: int) -> list[int]:
    """A book's size spread over its files: every file the same, the rest of the division on the last one."""
    each = size // max(files, 1)
    return [each] * (files - 1) + [size - each * (files - 1)]


def _file_name(book: _Book, quality: int, place: int, files: int) -> str:
    extension = QUALITIES[quality][2].lower() or (book.entry or {}).get("format", "")
    if files > 1:
        return f"{place:02d} {book.title}.{extension}"
    return f"{book.title}.{extension}" if book.audiobook else f"{book.title} - {book.author.name}.{extension}"


def _files(d: _Download, base: dict[str, Any]) -> list[dict[str, Any]]:
    """The file records of an import: the old files deleted a moment before an upgrade, then every file imported."""
    book, number = d.book, d.book.number * 1000
    folder, release = f"{PROFILES[book.audiobook][1]}/{book.author.name}/{book.title}", _release(book, d.quality)
    records = []
    if d.replaced:
        old_quality, old_size, old_files = d.replaced
        for place, size in enumerate(_file_sizes(old_size, old_files), start=1):
            records.append({**base, "sourceTitle": f"{folder}/{_file_name(book, old_quality, place, old_files)}", "downloadId": None,
                            "quality": _quality(old_quality), "qualityCutoffNotMet": old_quality < CUTOFF[book.audiobook],
                            "date": demo_world.iso(d.done - timedelta(seconds=DELETION_AHEAD)), "eventType": "bookFileDeleted",
                            "id": number + FILES["deleted"] + place, "data": {"reason": "Upgrade", "size": str(size), "indexerFlags": "0"}})
    client = demo_world.DOWNLOAD_CLIENTS[d.protocol] if d.grabbed else None
    for place, size in enumerate(_file_sizes(d.size, d.files), start=1):
        name = _file_name(book, d.quality, place, d.files)
        dropped = f"/downloads/complete/{release}/{name}" if d.grabbed else f"/scans/{name}"
        records.append({**base, "eventType": "bookFileImported", "date": demo_world.iso(d.done + timedelta(seconds=FILE_GAP * (place - 1))),
                        "id": number + FILES[d.kind] + place,
                        "data": {"fileId": str(number + FILES[d.kind] + place), "droppedPath": dropped, "importedPath": f"{folder}/{name}",
                                 "downloadClient": client, "downloadClientName": client, "releaseGroup": d.group or None,
                                 "size": str(size), "indexerFlags": "0"}})
    return records


def _records(world: _World, moment: datetime) -> list[dict[str, Any]]:
    """Every record of the history up to `moment`, oldest first - without the book and the author (see _embedded)."""
    records = []
    for d in world.downloads:
        grab_slot, done_slot = SLOTS[d.kind]
        book, number = d.book, d.book.number * 1000
        base = {"bookId": book.number, "authorId": book.author.number, "sourceTitle": _release(book, d.quality),
                "quality": _quality(d.quality), "customFormats": [], "customFormatScore": 0,
                "qualityCutoffNotMet": d.quality < CUTOFF[book.audiobook], "downloadId": d.download_id}
        if d.grabbed:
            records.append({**base, "date": demo_world.iso(d.grabbed), "eventType": "grabbed", "data": _grab(d), "id": number + grab_slot})
        client = demo_world.DOWNLOAD_CLIENTS.get(d.protocol, "")
        if d.kind == "failed":
            records.append({**base, "date": demo_world.iso(d.done), "eventType": "downloadFailed", "id": number + done_slot,
                            "data": {"downloadClient": client, "downloadClientName": client, "message": FAILURE_MESSAGE,
                                     "releaseGroup": d.group, "size": str(d.size), "indexer": d.indexer}})
            continue
        records += _files(d, base)
        if d.grabbed:      # the download's own record once its files are in
            records.append({**base, "date": demo_world.iso(d.done + timedelta(seconds=FILE_GAP * d.files)),
                            "eventType": "downloadImported", "id": number + done_slot,
                            "data": {"downloadClient": client, "downloadClientName": client}})
    records = [r for r in records if r["date"] <= demo_world.iso(moment)]
    return sorted(records, key=lambda r: (r["date"], r["id"]))


def _embedded(world: _World, records: list[dict[str, Any]], params: httpx.QueryParams) -> list[dict[str, Any]]:
    """The records with their book and author, when the request asks for them (includeBook, includeAuthor)."""
    books = {book.number: _book(book, full=False) for book in world.books}
    authors = {author.number: _author(author, full=False) for author in world.authors.values()}
    with_book, with_author = params.get("includeBook") == "true", params.get("includeAuthor") == "true"
    return [{**r, **({"book": books[r["bookId"]]} if with_book else {}), **({"author": authors[r["authorId"]]} if with_author else {})}
            for r in records]


def _history(params: httpx.QueryParams, moment: datetime) -> dict[str, Any]:
    world = _world(moment)
    page = demo_world.page(_records(world, moment), params, "date")
    return {**page, "records": _embedded(world, page["records"], params)}


def _time(value: Any) -> datetime | None:
    try:
        return datetime.strptime(str(value or "")[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=UTC)
    except ValueError:
        return None


def _history_since(params: httpx.QueryParams, moment: datetime) -> list[dict[str, Any]]:
    since, world = _time(params.get("date")), _world(moment)
    records = [r for r in _records(world, moment) if since is None or r["date"] >= demo_world.iso(since)]
    return _embedded(world, records, params)


# ---------------------------------------------------------------------------
# The live state: queue, calendar, missing, cutoff, health
# ---------------------------------------------------------------------------

def _missing(world: _World, moment: datetime) -> list[_Book]:
    """What is out and has no files: the books coming out that came out already."""
    return [b for b in world.books if not b.file and b.released and b.released <= moment]


def _cutoff(world: _World, moment: datetime) -> list[_Book]:
    """The library's books whose files are below the cutoff (EPUB, M4B)."""
    return [b for b in world.books if b.file and b.file[0] < CUTOFF[b.audiobook]]


def _wanted(which: Callable[[_World, datetime], list[_Book]]) -> Callable[[httpx.QueryParams, datetime], dict[str, Any]]:
    def answer(params: httpx.QueryParams, moment: datetime) -> dict[str, Any]:
        found = sorted(which(_world(moment), moment), key=lambda b: (b.released or datetime.min.replace(tzinfo=UTC), b.number),
                       reverse=True)
        return demo_world.page([_book(b) for b in found], params, "releaseDate", "descending")
    return answer


def _queue_book(world: _World, what: str, taken: set[int], moment: datetime) -> _Book | None:
    """The book of a download of the queue: an audiobook out already without files, or the newest audiobook (e-book)
    below the cutoff - each author once (`taken` are the authors in the queue already)."""
    if what == "coming":
        found = [b for b in _missing(world, moment) if b.entry is None and b.audiobook]
    else:
        found = sorted((b for b in _cutoff(world, moment) if b.entry and b.audiobook == (what == "audio")),
                       key=lambda b: b.entry["added_at"], reverse=True)
    return next((b for b in found if b.author.number not in taken), None)


def _queue(params: httpx.QueryParams, moment: datetime) -> dict[str, Any]:
    world = _world(moment)
    records: list[dict[str, Any]] = []
    taken: set[int] = set()
    for key, what, indexer, minutes in QUEUE:
        book = _queue_book(world, what, taken, moment)
        if book is None:
            continue
        taken.add(book.author.number)
        rng = demo_world.random_for(f"readarr-queue:{key}:{book.number}")
        (name, protocol), quality = demo_world.INDEXERS[indexer], CUTOFF[book.audiobook]
        size = int(book.entry["size_bytes"] * rng.uniform(0.9, 1.3)) if book.entry else _size(rng, book.audiobook)
        release = _release(book, quality)
        if minutes:
            share = demo_world.cycle_share(moment, f"readarr:{key}:{book.number}", minutes)
            left = timedelta(seconds=round((1 - share) * minutes * 60))
            state = {"status": "downloading", "trackedDownloadStatus": "ok", "trackedDownloadState": "downloading",
                     "statusMessages": [], "sizeleft": int(size * (1 - share)),
                     "added": demo_world.iso(moment - timedelta(seconds=minutes * 60 * share))}
        else:
            left = timedelta(0)
            state = {"status": "completed", "trackedDownloadStatus": "warning", "trackedDownloadState": "importBlocked",
                     "statusMessages": [STUCK_MESSAGES[0], {**STUCK_MESSAGES[1], "title": release}], "sizeleft": 0,
                     "added": demo_world.iso(moment.replace(minute=0, second=0, microsecond=0) - timedelta(hours=2))}
        download_id = f"SABnzbd_nzo_{rng.getrandbits(32):08x}" if protocol == "usenet" else f"{rng.getrandbits(160):040X}"
        records.append({"authorId": book.author.number, "bookId": book.number, "author": {**_author(book.author, full=False), "images": []},
                        "book": {**_book(book, full=False), "images": []}, "quality": _quality(quality), "size": size, "title": release,
                        "timeleft": demo_world.clock(left), "estimatedCompletionTime": demo_world.iso(moment + left), **state,
                        "downloadId": download_id, "protocol": protocol, "downloadClient": demo_world.DOWNLOAD_CLIENTS[protocol],
                        "indexer": name, "downloadForced": False, "id": rng.getrandbits(31)})
    hidden = {name for name, flag in (("book", "includeBook"), ("author", "includeAuthor")) if params.get(flag) != "true"}
    return demo_world.page([{k: v for k, v in r.items() if k not in hidden} for r in records], params, "timeleft")


def _calendar(params: httpx.QueryParams, moment: datetime) -> list[dict[str, Any]]:
    """The books with a release from `start` to `end`, the earliest first."""
    start, end = _time(params.get("start")) or moment, _time(params.get("end")) or moment + timedelta(days=7)
    found = [b for b in _world(moment).books if b.released and start <= b.released <= end]
    return [_book(b) for b in sorted(found, key=lambda b: (b.released, b.number))]


def _health(params: httpx.QueryParams, moment: datetime) -> list[dict[str, Any]]:
    return [{"source": "UpdateCheck", "type": "warning", "message": f"New update is available: {UPDATE}",
             "wikiUrl": "https://wiki.servarr.com/readarr/system#new-update-is-available"}]


def _status(params: httpx.QueryParams, moment: datetime) -> dict[str, Any]:
    started = moment.replace(minute=0, second=0, microsecond=0) - timedelta(days=4, hours=7)
    return {"appName": NAME, "instanceName": NAME, "version": VERSION, "buildTime": "2025-06-12T10:12:31Z", "isDebug": False,
            "isProduction": True, "isAdmin": False, "isUserInteractive": False, "startupPath": "/app/readarr/bin", "appData": "/config",
            "osName": "debian", "osVersion": "13", "isNetCore": True, "isLinux": True, "isOsx": False, "isWindows": False, "isDocker": True,
            "mode": "console", "branch": "develop", "authentication": "forms", "databaseVersion": "3.46.1", "databaseType": "sqLite",
            "migrationVersion": 41, "urlBase": "", "runtimeVersion": "8.0.10", "runtimeName": ".NET", "startTime": demo_world.iso(started),
            "packageUpdateMechanism": "docker"}


def _book_list(params: httpx.QueryParams, moment: datetime) -> list[dict[str, Any]]:
    return [_book(book) for book in sorted(_world(moment).books, key=lambda b: b.number)]


def _edition_list(params: httpx.QueryParams, moment: datetime) -> list[dict[str, Any]]:
    """The editions of the books asked about (`bookId` repeated): the one Readarr has and the printed one."""
    wanted = set(params.get_list("bookId"))
    return [edition for book in sorted(_world(moment).books, key=lambda b: b.number) if str(book.number) in wanted
            for edition in (_edition(book), _edition(book, own=False))]


def _author_list(params: httpx.QueryParams, moment: datetime) -> list[dict[str, Any]]:
    world = _world(moment)
    found = []
    for author in sorted(world.authors.values(), key=lambda a: a.number):
        own = [b for b in world.books if b.author is author]
        with_files = [b for b in own if b.file]
        found.append({**_author(author), "statistics": {
            "bookFileCount": sum(b.file[2] for b in with_files), "bookCount": len(own), "availableBookCount": len(with_files),
            "totalBookCount": len(own), "sizeOnDisk": sum(b.file[1] for b in with_files),
            "percentOfBooks": round(100 * len(with_files) / len(own), 1)}})
    return found


def _command(request: httpx.Request, moment: datetime) -> dict[str, Any]:
    """A command (RefreshMonitoredDownloads) taken into the queue of commands - the made-up Readarr does nothing with it."""
    try:
        name = str(json.loads(request.content or b"{}").get("name") or "")
    except (ValueError, AttributeError):      # no JSON, or no object
        name = ""
    return {"name": name, "body": {"sendUpdatesToClient": False}, "priority": "normal", "status": "queued",
            "queued": demo_world.iso(moment), "trigger": "manual", "id": int(moment.timestamp()) % 100_000}


# API path (after /api/v1/) -> its answer to a GET.
ANSWERS: dict[str, Callable[[httpx.QueryParams, datetime], Any]] = {
    "system/status": _status, "history": _history, "history/since": _history_since, "book": _book_list,
    "edition": _edition_list, "author": _author_list, "queue": _queue, "calendar": _calendar, "health": _health,
    "wanted/missing": _wanted(_missing), "wanted/cutoff": _wanted(_cutoff),
}


def handler(request: httpx.Request) -> httpx.Response:
    """The made-up Readarr's answer to one request (httpx.MockTransport)."""
    path = request.url.path.split(API, 1)[-1].strip("/").lower()
    moment = demo_world.now()
    if request.method == "POST":
        return httpx.Response(201, json=_command(request, moment)) if path == "command" else httpx.Response(404)
    answer = ANSWERS.get(path) if request.method == "GET" else None
    if answer is None:
        return httpx.Response(404)
    return httpx.Response(200, json=answer(request.url.params, moment))
