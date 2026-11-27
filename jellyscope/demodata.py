"""Made-up data for the demo mode.

It serves a single purpose: so you can click through the application before you
connect it to a real Jellyfin - and so it is visible what the charts look like
once some history has been collected.

This file has nothing to do with live operation. It writes into its own
database (data/demo.db), so it cannot affect your real data in any way.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from datetime import date, datetime, timedelta, UTC

from . import accounts, db, demo_world, languages

log = logging.getLogger(__name__)

# The account you sign in to the demo with.
DEMO_USERNAME = "demo"
DEMO_PASSWORD = "demodemo"

# The ids below are readable keys ("demo-u1", "demo-movie-3"). What is written is the id Jellyfin would
# give (32 hex characters, demo_world) and a numeric TMDB id - the shape the pairing of the made-up apps
# needs. The same key gives the same id on every seed.
_jellyfin_id = demo_world.jellyfin_id

# How often which member of the household reaches for the original when the
# Czech dubbing is there to choose too. Petr rather watches in the original, Eva
# almost always in Czech - thanks to that the Languages page has something to show.
PREFERS_ORIGINAL = {
    "demo-u1": 0.62, "demo-u2": 0.18, "demo-u3": 0.35, "demo-u4": 0.08,
    "demo-u5": 0.90, "demo-u6": 0.05, "demo-u7": 0.48, "demo-u8": 0.72,
    "demo-u9": 0.25, "demo-u10": 0.55, "demo-u11": 0.12,
}

LIBRARIES = [
    ("demo-movies", "Filmy", "movies"),
    ("demo-tv", "Serialy", "tvshows"),
    # The Library page shows every Jellyfin library (16. 11. 2026): the demo's albums live here.
    ("demo-music", "Hudba", "music"),
    # Books (23. 11. 2026): e-books and audiobooks in two libraries of the type "books".
    ("demo-books", "Knihy", "books"),
    ("demo-audiobooks", "Audioknihy", "books"),
]

# Eleven people on purpose. The card "Who watches in which language" shows the
# first ten right away (see the Interface settings) and hides the rest in a
# window - with four viewers this would never be visible.
USERS = [
    ("demo-u1", "Petr", 1),
    ("demo-u2", "Jana", 0),
    ("demo-u3", "Tomas", 0),
    ("demo-u4", "Eva", 0),
    ("demo-u5", "Marek", 0),
    ("demo-u6", "Lucie", 0),
    ("demo-u7", "David", 0),
    ("demo-u8", "Klara", 0),
    ("demo-u9", "Ondrej", 0),
    ("demo-u10", "Tereza", 0),
    ("demo-u11", "Filip", 0),
]

MOVIES = [
    "Duna", "Blade Runner 2049", "Prichozi", "Interstellar", "Sedmi samuraji",
    "Vetrelec", "Mad Max: Zbesila cesta", "Zivot je krasny", "Pulp Fiction",
    "Sedm", "Pocatek", "Temny rytir", "Prestiz", "Whiplash", "Parazit",
    "Hori, ma panenko", "Marecku, podejte mi pero", "Vrchni, prchni",
    "Kolja", "Samotari", "Pelisky", "Musime si promluvit", "Vratne lahve",
]

SERIES = ["Chernobyl", "Serial killer", "Kancelar", "Pratele", "Simpsonovi"]
# A series only the TV schedule knows (demo_world.AIRING): the household watches it while it airs, so the library has
# just its latest episodes (_seed_airing). Its key goes on from SERIES: "demo-series-5", series 6.
NEW_SERIES = ["Hvezdna stanice"]
# The airing series in the library: {series key: (how many weeks back the library has them, the hours from airing
# until an episode is in the library)}. The made-up Sonarr grabs and imports each in that time (arr/sonarr/demo.py),
# every series at its own pace, so "how soon after release" has a spread. One episode came late: (the series, its
# place from the newest, the hours).
AIRING_LIBRARY = {"demo-series-5": (10, 0.7), "demo-series-1": (4, 3.4), "demo-series-2": (4, 13.0), "demo-series-4": (4, 38.0)}
LATE_AIRING = ("demo-series-5", 4, 9 * 24)
# Movies of the last weeks: {key: (title, the days from its digital release - at midnight UTC - back from the seed's
# day, the hours from it until the library has it)}. The made-up Radarr had waited for each (it knew the movie before
# it came out), grabbed and imported it in those hours (arr/radarr/demo.py, demo_world.movie_release): four in the last
# days, each in another bucket of "how soon after release", three in the weeks before, one of them late (no release
# met the profile for three weeks). Each is in the library before the seed's day begins (hours <= days * 24). In the
# catalog they follow the library's other movies (demo_world: movies 27-33); `random` draws nothing for them.
NEW_MOVIES = {
    "demo-movie-new-0": ("Posledni smena", 1, 0.6),
    "demo-movie-new-1": ("Zimni zahrada", 2, 4.2),
    "demo-movie-new-2": ("Sest dni v srpnu", 3, 16.5),
    "demo-movie-new-3": ("Vlak do Brna", 4, 70.0),
    "demo-movie-new-4": ("Mesto pod hladinou", 12, 30.0),
    "demo-movie-new-5": ("Svetlo na konci", 19, 2.5),
    "demo-movie-new-6": ("Nocni linka", 33, 22 * 24.0),
}
# Movies the library does not have yet, only the made-up Radarr knows them: (key, title, the days from today to their
# digital release, at midnight UTC). Two are out already: Radarr is downloading the first and has not found the second
# (missing); the others are in its calendar (demo_world.coming_movies).
COMING_MOVIES = [
    ("demo-movie-coming-0", "Pisecny hrad", -5),
    ("demo-movie-coming-1", "Druhy breh", -2),
    ("demo-movie-coming-2", "Zlaty dest", 3),
    ("demo-movie-coming-3", "Ledova krajina", 9),
    ("demo-movie-coming-4", "Tri sestry z hor", 12),
]

# The key of the movie whose language only its file's name knows (see seed()).
LANGUAGE_IN_NAME = "demo-movie-language-in-name"

CLIENTS = [
    ("Jellyfin Web", "Chrome na notebooku"),
    ("Jellyfin Android TV", "Shield TV v obyvaku"),
    ("Findroid", "Pixel 8"),
    ("Infuse", "Apple TV"),
    ("Jellyfin Web", "Firefox v praci"),
]

# Where the demo household watches from. Without this the map on the Network
# page would be empty: when everything flows over the home network, there is
# nothing to place.
#
# The addresses are real public ranges - on purpose, because only such can the
# GeoLite2 database find. A made-up number (or 203.0.113.x from the
# documentation range) would end on the map as "unknown from where". We connect
# nowhere meanwhile, only the local .mmdb file is read.
HOME_NETWORK = "192.168.1.20"

OUTSIDE = [
    ("109.81.0.1", "mobil v Praze"),
    ("81.19.0.1", "u rodicu v Brne"),
    ("5.9.0.1", "sluzebka v Nemecku"),
    ("80.58.61.250", "dovolena ve Spanelsku"),
    ("213.129.65.1", "Londyn"),
    ("24.48.0.1", "navsteva v Kanade"),
    ("200.160.2.3", "Brazilie"),
    ("118.127.0.1", "Australie"),
]

# How often which device watches outside the home network. The TV in the living
# room by its nature does not move anywhere, a phone does - and exactly this
# difference makes the map believable.
OUTSIDE_CHANCE = {
    "Shield TV v obyvaku": 0.0,
    "Apple TV": 0.0,
    "Chrome na notebooku": 0.25,
    "Firefox v praci": 1.0,
    "Pixel 8": 0.55,
}


def _address(device: str) -> str:
    """From which address this playback will be.

    There are more plays close to home than far away - so a weighted draw: the
    first items of OUTSIDE (Prague, Brno) come up more often than Australia.
    """
    if random.random() >= OUTSIDE_CHANCE.get(device, 0.2):
        return HOME_NETWORK
    weights = [len(OUTSIDE) - index for index in range(len(OUTSIDE))]
    return random.choices(OUTSIDE, weights=weights, k=1)[0][0]


def _ts(moment: datetime) -> str:
    return moment.strftime(db.TIME_FORMAT)


def already_seeded() -> bool:
    return int(db.query_value("SELECT COUNT(*) FROM items")) > 0


def ensure_demo_account() -> None:
    """Creates the demo/demodemo account, so the demo can be signed in to.

    And puts the demo into English. People come to it from anywhere and a Czech
    label at a chart tells them nothing; switching it back is not possible,
    because nothing is stored in the demo - and what one switched, everybody
    after them would have too.
    """
    if accounts.get_by_name(DEMO_USERNAME) is None:
        accounts.create(DEMO_USERNAME, DEMO_PASSWORD, is_admin=True)

    db.set_setting("ui_language", "en")


# ---------------------------------------------------------------------------
# Refreshing: made-up data age
# ---------------------------------------------------------------------------
#
# The data are generated relative to the moment the demo was prepared. A public
# demo then runs for weeks - and "the last 30 days" empty day by day, until a
# line remains of the chart. So it is remembered WHEN the seed was made, and
# after a day the data are made again: the same number in `random.seed`, the
# same shape, only moved to today. A person who opens the demo in a month sees
# the same as the one who opened it the first day.

SEED_KEY = "demo_seeded_at"
REFRESH_AFTER_HOURS = 24

# The tables the seed fills - in the order in which they can be deleted (foreign
# keys: first what refers, then what is referred to). Accounts and settings
# stay: they are no made-up data, but the demo itself.
MADE_UP_TABLES = ("playback_bin", "forgettings", "playback", "item_streams",
                  "item_versions", "items", "library_snapshot", "library_disk_snapshot", "users",
                  "libraries", "music_track", "music_album", "music_artist", "book")


def age_hours() -> float | None:
    """How many hours since the last seed. None = it is not known (an old demo)."""
    when = db.get_setting(SEED_KEY, "").strip()
    if not when:
        return None
    try:
        moment = datetime.strptime(when, db.TIME_FORMAT).replace(tzinfo=UTC)
    except ValueError:
        return None
    return (datetime.now(UTC) - moment).total_seconds() / 3600


def is_stale() -> bool:
    """Are the data older than a day, or is it not known how old they are?"""
    age = age_hours()
    return age is None or age >= REFRESH_AFTER_HOURS


# What the seed makes - raised by one whenever the made-up content changes (a new
# kind of content, a new app). 1 = the content up to 1.9.1, without music and
# books; 3 = the ids in Jellyfin's shape (demo_world); 4 = two more albums and the
# made-up Jellyfin connected (demo_jellyfin); 5 = the made-up Sonarr connected
# (arr/sonarr/demo.py); 6 = series airing now (demo_world.AIRING): their latest
# episodes in the library and in Sonarr; 7 = the made-up Radarr connected
# (arr/radarr/demo.py), the movies of the last weeks in the library (NEW_MOVIES)
# and those coming out (COMING_MOVIES); 8 = the made-up Lidarr connected
# (arr/lidarr/demo.py), the albums of the last weeks in the library (NEW_ALBUMS)
# and those coming out (COMING_ALBUMS); 9 = the made-up Readarr connected
# (arr/readarr/demo.py), the books of the last weeks in the books libraries
# (NEW_BOOKS) and those coming out (COMING_BOOKS); 10 = the made-up Bazarr connected
# (arr/bazarr/demo.py): subtitles for the series and movies; 11 = the made-up
# Prowlarr connected (arr/prowlarr/demo.py): the indexers' queries and grabs, and
# the samples of their status over the past; 12 = the made-up Seerr connected
# (seerr/demo.py): the viewers' requests for the demo's titles and a few others,
# with the approvals Jellyscope caught; 13 = the flaky indexer's outages are
# demo_world's schedule (Prowlarr's history and samples follow it, Sonarr's and
# Radarr's health warn by it); 14 = the storage (_seed_storage): a capacity
# entered by hand and the daily snapshots of the last weeks. The seed stores the version it made;
# prepare() makes the data again at once when the stored one differs, fresh or
# not. Without it an updated public demo would show the old content for up to a
# day - or for ever, for a database its owner keeps.
DEMO_CONTENT_VERSION = 14
CONTENT_KEY = "demo_content_version"


def content_changed() -> bool:
    """Were the data made with other content than this seed makes? No version = 1 (a demo from before it)."""
    stored = db.get_setting(CONTENT_KEY, "").strip() or "1"
    return stored != str(DEMO_CONTENT_VERSION)


def wipe() -> None:
    """Deletes the made-up data - only those, accounts and settings stay."""
    with db.connect() as conn:
        for table in MADE_UP_TABLES:
            # The bin comes about only with the first forgetting, so it need not
            # exist. Ask in advance, do not catch the error: on PostgreSQL a failing
            # statement would bring down the whole transaction with everything deleted so far.
            if db.columns_of(conn, table):
                conn.execute(f"DELETE FROM {table}")
        conn.commit()


def refresh() -> dict[str, int]:
    """Makes the data again, moved to today. Returns the counts like seed().

    What the made-up apps downloaded goes first, through each app's own deletion (demo_apps.wipe_apps);
    seed() fills them again. All the while demo_world.REFRESHING is set: Prowlarr's background loop keeps
    measuring the made-up Prowlarr in the demo (web/start.py) - which does not tell it the indexers' status
    now, so the loop writes no sample into the tables being deleted and filled (arr/prowlarr/demo.py).
    """
    from . import demo_apps

    demo_world.REFRESHING.set()
    try:
        demo_apps.wipe_apps()
        wipe()
        counts = seed()
    finally:
        demo_world.REFRESHING.clear()
    log.info("demo data generated again: %s titles, %s playbacks",
             counts["items"], counts["plays"])
    return counts


async def keep_refreshing() -> None:
    """The demo's background task: once a day it moves the data to today.

    The demo has no scheduler (there is nothing to synchronise), so it watches
    this itself. The check is cheap and runs every hour; the refresh itself
    runs in a thread, so the server does not freeze for that time.
    """
    while True:
        await asyncio.sleep(3600)
        try:
            if is_stale():
                await asyncio.to_thread(refresh)
        except Exception:                          # noqa: BLE001
            log.exception("refreshing the demo data failed")


# The made-up "running right now" playback - the same key seed() creates it
# with. The "Now playing" card is the first thing a demo visitor sees, so
# something is to really run in it.
LIVE_KEY = "demo-live"
# How often the demo's task revives it - as often as the collector asks in the
# default settings.
LIVE_INTERVAL = 10


def keep_live(now: datetime | None = None) -> None:
    """Keeps the made-up stream alive and moves it by the time that passed.

    The seed creates it once and then it would stand: the position and "last
    seen" from the moment of the seed. And anybody who closes a running
    playback (the collector at start over the same database) would delete it
    from the card altogether.

    The position is computed from the clock, not by adding: it goes round the
    movie, so it never passes its length and it does not matter how long the
    task stood. The first and the last minute are left out, so the progress
    indicator has something to show.
    """
    now = now or datetime.now(UTC)
    length = db.query_value(
        "SELECT i.runtime_ticks FROM playback p JOIN items i ON i.id = p.item_id"
        " WHERE p.session_key = ?", (LIVE_KEY,))
    length_s = int(length or 0) // 10_000_000
    if length_s <= 180:
        return
    position = 60 + int(now.timestamp()) % (length_s - 120)
    with db.connect() as conn:
        conn.execute(
            "UPDATE playback SET is_active = 1, ended_at = NULL, is_paused = 0,"
            " started_at = ?, last_seen_at = ?, watched_seconds = ?,"
            " paused_seconds = 0, position_ticks = ? WHERE session_key = ?",
            (_ts(now - timedelta(seconds=position)), _ts(now), position,
             position * 10_000_000, LIVE_KEY))
        conn.commit()


async def keep_live_forever() -> None:
    """The demo's background task: the made-up stream plays all the time (keep_live)."""
    while True:
        try:
            await asyncio.to_thread(keep_live)
        except Exception:                          # noqa: BLE001
            log.exception("the demo's made-up stream could not be revived")
        await asyncio.sleep(LIVE_INTERVAL)


def prepare(quiet: bool = False) -> dict[str, int]:
    """Prepares the demo, if it is not prepared yet - or if it is old or of other content.

    It is called from two places: from `demo.py` (a demo on your own machine)
    and from `run.py`, when the demo mode is on (a demo in a container - that
    runs run.py and does not get to demo.py). Without this a public demo in
    Docker would come up empty and locked: nothing is stored in the demo mode,
    so not even the admin could create it.

    `quiet` only shuts it up - run.py prints its own.
    """
    done = already_seeded()
    if not done:
        if not quiet:
            print("Preparing the made-up data...")
        counts = seed()
    elif content_changed():
        if not quiet:
            print("The made-up content has changed with the update, making it again...")
        counts = refresh()
    elif is_stale():
        if not quiet:
            print("The made-up data has grown old, making it again...")
        counts = refresh()
    else:
        counts = {"items": 0, "plays": 0, "users": 0}
    ensure_demo_account()
    return counts


def _audio_tracks(random_source) -> tuple[str, str, str]:
    """Makes up the language tracks of one title.

    Returns a triple (audio tracks, subtitles, default audio) already in the
    shape it is stored in the database.
    """
    roll = random_source.random()
    if roll < 0.50:
        audio = ["cs", "en"]          # dubbing and the original - the most common
    elif roll < 0.72:
        audio = ["cs"]                # only dubbing
    elif roll < 0.90:
        audio = ["en"]                # only the original
    elif roll < 0.97:
        audio = ["cs", "en", "sk"]
    else:
        audio = ["und"]               # nobody filled in the language

    subtitles = []
    if random_source.random() < 0.7:
        subtitles.append("cs")
    if random_source.random() < 0.5:
        subtitles.append("en")

    return (
        languages.pack(audio),
        languages.pack(subtitles),
        audio[0] if audio else languages.UNKNOWN,
    )


# ---- music ------------------------------------------------------------
# Made-up artists and albums for the Music page. The codecs on purpose across all
# bands of the Sound quality card: hi-res FLAC, FLAC, ALAC, MP3 320 and 192 kb/s,
# AAC below 128, Opus. Nobody played the album "Ticho" - the card
# "What nobody listens to" thus has something to show. There are no covers (the demo
# has no Jellyfin), a substitute is drawn.
MUSIC_ARTISTS = [("demo-int-1", "Severní světla"), ("demo-int-2", "Neonový přístav"), ("demo-int-3", "Půlnoční rádio"),
                 ("demo-int-4", "Kamenný most"), ("demo-int-5", "Tiché údolí")]
# id, title, artist, year, genres, added days ago, codec, bitrate, sample rate, bit depth, tracks
MUSIC_ALBUMS = [
    ("demo-alb-1", "Polární záře", "demo-int-1", 2021, "Ambient", 12, "flac", 2_300_000, 96_000, 24, 8),
    ("demo-alb-2", "Fjordy", "demo-int-1", 2016, "Ambient|Klasika", 200, "flac", 900_000, 44_100, 16, 7),
    ("demo-alb-3", "Přílivová síť", "demo-int-2", 2023, "Synthwave|Pop", 5, "mp3", 320_000, 44_100, None, 9),
    ("demo-alb-4", "Arkáda", "demo-int-2", 1987, "Synthwave", 400, "aac", 96_000, 44_100, None, 6),
    ("demo-alb-5", "Noční vysílání", "demo-int-3", 2019, "Lo-fi|Jazz", 60, "mp3", 192_000, 44_100, None, 10),
    ("demo-alb-6", "Žula", "demo-int-4", 1995, "Rock", 700, "alac", 1_000_000, 48_000, 16, 9),
    ("demo-alb-7", "Kamenná cesta", "demo-int-4", 2004, "Rock|Folk", 300, "opus", 160_000, 48_000, None, 8),
    ("demo-alb-8", "Ticho", "demo-int-5", None, "Folk", 90, "mp3", 256_000, 44_100, None, 5),
    # Two more (content 4), so the made-up Lidarr has ten albums to manage (demo_world.albums).
    ("demo-alb-9", "Městská světla", "demo-int-3", 2022, "Lo-fi|Elektronika", 45, "flac", 1_100_000, 48_000, 24, 7),
    ("demo-alb-10", "Proti proudu", "demo-int-5", 2011, "Folk|Akustika", 150, "mp3", 320_000, 44_100, None, 6),
]
MUSIC_CLIENTS = [("Finamp", "Pixel 8"), ("Jellyfin Web", "Chrome na notebooku"), ("Feishin", "Počítač v pracovně"),
                 ("Symfonium", "Galaxy S23")]
MUSIC_LISTENERS = ["demo-u2", "demo-u3", "demo-u4", "demo-u6", "demo-u10"]
# Albums of the last weeks: {key: (title, artist, genres, codec, bitrate, sample rate, bit depth, tracks, the days from
# its release - at midnight UTC - back from the seed's day, the hours from it until the library has it)}. The made-up
# Lidarr had the artist long before (arr/lidarr/demo.py, demo_world.album_release), grabbed and imported each album in
# those hours: four in the last days, each in another bucket of "how soon after release", two in the weeks before, one
# of them late (no release met the profile for three weeks). Each is in the library before the seed's day begins
# (hours <= days * 24), none on a day test_content_kinds_growth counts on (5-6 and 19-21 days back). In the catalog they
# follow the library's other albums (demo_world: albums 11-16); `random` draws nothing for them (_seed_new_albums).
NEW_ALBUMS = {
    "demo-alb-new-1": ("Ledové květy", "demo-int-1", "Ambient", "flac", 950_000, 44_100, 16, 8, 1, 0.5),
    "demo-alb-new-2": ("Signál z věže", "demo-int-3", "Lo-fi|Elektronika", "flac", 2_100_000, 96_000, 24, 9, 2, 4.0),
    "demo-alb-new-3": ("Kámen a voda", "demo-int-4", "Rock", "flac", 1_000_000, 44_100, 16, 10, 3, 15.0),
    "demo-alb-new-4": ("Pobřežní noci", "demo-int-2", "Synthwave", "mp3", 320_000, 44_100, None, 8, 4, 62.0),
    "demo-alb-new-5": ("Zimní deník", "demo-int-5", "Folk|Akustika", "flac", 880_000, 44_100, 16, 7, 13, 2.0),
    "demo-alb-new-6": ("Ozvěny z mlhy", "demo-int-3", "Lo-fi|Jazz", "mp3", 320_000, 44_100, None, 9, 36, 22 * 24.0),
}
# The album of the last weeks Lidarr took a worse version of first and its better one (the library's file) later:
# (its key, the hours from the first import to the better one). Within the seed's day before, so the last 7 days always
# have an upgrade.
UPGRADED_ALBUM = ("demo-alb-new-2", 26.0)
# Albums the library does not have yet, only the made-up Lidarr knows them: (key, title, artist, tracks, the days from
# today to their release, at midnight UTC). Two are out already: Lidarr is downloading the first and has not found the
# second (missing); the others are in its calendar (demo_world.coming_albums).
COMING_ALBUMS = [
    ("demo-alb-coming-0", "Noční trasa", "demo-int-4", 10, -3),
    ("demo-alb-coming-1", "Hory a údolí", "demo-int-5", 8, -1),
    ("demo-alb-coming-2", "Světlo v okně", "demo-int-1", 9, 4),
    ("demo-alb-coming-3", "Tichý přístav", "demo-int-3", 11, 6),
    ("demo-alb-coming-4", "Neonové srdce", "demo-int-2", 10, 11),
]
# An album, its track and a listen, as the music seeds write them.
_ALBUM_SQL = ("INSERT INTO music_album (id, name, artist_id, artist, year, genres, added_at, synced_at, library_id)"
              " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)")
_TRACK_SQL = ("INSERT INTO music_track (id, name, album_id, album, artist_id, artist, track_number, runtime_ticks, year, genres,"
              " codec, bitrate, sample_rate, bit_depth, channels, size_bytes, added_at, synced_at, path, library_id)"
              " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 2, ?, ?, ?, ?, ?)")
_LISTEN_SQL = ("INSERT INTO playback (session_key, user_id, user_name, item_id, item_name, item_type, client, device_name,"
               " remote_address, play_method, transcode_reasons, bitrate, media_runtime_ticks, started_at, last_seen_at, ended_at,"
               " watched_seconds, paused_seconds, is_active)"
               " VALUES (?, ?, ?, ?, ?, 'Audio', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0)")


def _airing_episodes(now: datetime) -> list[tuple[str, int, int, datetime, datetime]]:
    """The airing series' episodes the library has: (series key, season, episode, aired, in the library), oldest first.

    Every episode of the series' window (AIRING_LIBRARY) that reached the library by `now`; when it came is its air
    time and the series' hours, a few minutes either way (its own random source: the same at every seed).
    """
    found = []
    for key, (weeks, hours) in AIRING_LIBRARY.items():
        number = int(key.rsplit("-", 1)[1]) + 1
        aired = demo_world.airings(number, now - timedelta(weeks=weeks), now)
        for place, (season, episode, moment) in enumerate(reversed(aired), start=1):
            late = (key, place) == LATE_AIRING[:2]
            jitter = demo_world.random_for(f"demo-airing:{key}:{season}:{episode}").uniform(-12, 12)
            arrived = moment + timedelta(hours=LATE_AIRING[2] if late else hours, minutes=jitter)
            if arrived <= now:
                found.append((key, season, episode, moment, arrived))
    return sorted(found, key=lambda e: (e[4], e[0]))


# A new title of the library (an airing episode, a movie of the last weeks) as a row of items, in this order.
_NEW_ITEM_SQL = (
    "INSERT INTO items (id, name, type, library_id, series_id, series_name, season_name, index_number,"
    " parent_index_number, production_year, runtime_ticks, date_created, path, tmdb_id, container, video_codec,"
    " audio_codec, audio_channels, width, height, bitrate, size_bytes, video_range, audio_languages,"
    " subtitle_languages, default_audio_language, tech_source, tech_updated_at, synced_at)"
    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT (id) DO NOTHING")


def _new_streams(item: tuple) -> list[tuple]:
    """The streams of a new title (a row of _NEW_ITEM_SQL): its picture, then a track for each language it has."""
    item_id = item[0]
    tracks = [("Audio", code) for code in languages.unpack(item[23])] + [("Subtitle", code) for code in languages.unpack(item[24])]
    return [(item_id, 0, "Video", item[15], languages.UNKNOWN, None, None, None, item[18], item[19], item[20], 1, 0, 0)] + [
        (item_id, place, kind, "eac3" if kind == "Audio" else "subrip", code, None, 6 if kind == "Audio" else None,
         "5.1" if kind == "Audio" else None, None, None, None, 1 if place == 1 else 0, 0, 0)
        for place, (kind, code) in enumerate(tracks, start=1)]


def _watched(rng: random.Random, plays: list[tuple], prefix: str, item: tuple, arrived: datetime, now: datetime,
             how_many: tuple[int, int], hours: tuple[float, float]) -> None:
    """Who of the household watched a new title (a row of _NEW_ITEM_SQL) in the hours after it came - never before.

    A play that would not have ended by `now` is left out. The plays go to `plays`, numbered on from those there.
    """
    viewers = {user_id: name for user_id, name, _ in USERS}
    item_id, runtime, audio = item[0], item[10] // 10_000_000, item[23]
    for user_id in rng.sample(sorted(viewers), rng.randint(*how_many)):
        start = arrived + timedelta(hours=rng.uniform(*hours))
        seconds = int(runtime * rng.uniform(0.6, 1.0))
        if start + timedelta(seconds=seconds) < now:
            client, device = rng.choice(CLIENTS)
            plays.append((f"{prefix}-{len(plays)}::{item_id}", _jellyfin_id(user_id), viewers[user_id], item_id,
                          item[1], item[2], item[5], item[3], client, device,
                          f"demo-dev-{user_id}", _address(device), "DirectPlay", item[15], item[16], item[20],
                          languages.unpack(audio)[0] if audio else None, item[10],
                          _ts(start), _ts(start + timedelta(seconds=seconds)), _ts(start + timedelta(seconds=seconds)), seconds))


def _store_new(conn, items: list[tuple], plays: list[tuple]) -> None:
    """Write new titles (rows of _NEW_ITEM_SQL) with their streams, and their plays."""
    conn.executemany(_NEW_ITEM_SQL, items)
    conn.executemany(
        "INSERT INTO item_streams (item_id, stream_index, type, codec, language, title, channels, channel_layout, width,"
        " height, bitrate, is_default, is_forced, is_external) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
        " ON CONFLICT (item_id, stream_index) DO NOTHING", [row for item in items for row in _new_streams(item)])
    conn.executemany(
        "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name, item_type, series_name, library_id,"
        " client, device_name, device_id, remote_address, play_method, video_codec, audio_codec, bitrate, audio_language,"
        " media_runtime_ticks, started_at, last_seen_at, ended_at, watched_seconds, paused_seconds, is_active)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,0)", plays)


def _seed_airing(conn, now: datetime) -> tuple[int, int]:
    """The airing series' latest episodes in the library, and who watched them. Returns (episodes, plays).

    After everything else and with its own randomness: the library and the history drawn before stay as they were.
    """
    rng = random.Random(17)
    names = dict(enumerate([*SERIES, *NEW_SERIES]))
    items: list[tuple] = []
    plays: list[tuple] = []
    for key, season, episode, aired, arrived in _airing_episodes(now):
        index = int(key.rsplit("-", 1)[1])
        item_id, series_name = _jellyfin_id(f"demo-ep-{index}-s{season}e{episode}"), names[index]
        height, codec = rng.choice([1080, 1080, 720, 2160]), rng.choice(["h264", "hevc"])
        size, runtime = int(rng.uniform(0.9, 4.5) * 1024 ** 3), int(rng.uniform(38, 58) * 60)
        audio, subtitles, default = _audio_tracks(rng)
        items.append((item_id, f"{episode}. dil", "Episode", _jellyfin_id("demo-tv"), _jellyfin_id(key), series_name,
                      f"Serie {season}", episode, season, aired.year, runtime * 10_000_000,
                      arrived.strftime("%Y-%m-%dT%H:%M:%S.0000000Z"), f"D:/media/serialy/{series_name}/S{season:02d}E{episode:02d}.mkv",
                      demo_world.tmdb_id(key), "matroska", codec, "eac3", 6, int(height * 16 / 9), height,
                      int(size * 8 / runtime), size, "HDR" if height > 2000 else "SDR", audio, subtitles, default,
                      "ffprobe", _ts(now), _ts(now)))
        # Two or three of the household watch a new episode in the days after it came (never before).
        _watched(rng, plays, "demo-airing", items[-1], arrived, now, (2, 3), (1, 72))
    _store_new(conn, items, plays)
    return len(items), len(plays)


def _seed_new_movies(conn, now: datetime) -> tuple[int, int]:
    """The movies of the last weeks in the library (NEW_MOVIES), and who watched them. Returns (movies, plays).

    After everything else and with its own randomness: the library and the history drawn before stay as they were.
    Each came out at midnight UTC and reached the library its hours later; a new movie is a WEB release (1080p, the
    fourth in 4K), not a Blu-ray yet.
    """
    rng = random.Random(29)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    items: list[tuple] = []
    plays: list[tuple] = []
    for place, (key, (title, days, hours)) in enumerate(NEW_MOVIES.items()):
        released = today - timedelta(days=days)
        arrived = released + timedelta(hours=hours)
        height = 2160 if place == 3 else 1080
        codec = "hevc" if height > 2000 else rng.choice(["h264", "hevc"])
        size = int(rng.uniform(15, 28) * 1024 ** 3) if height > 2000 else int(rng.uniform(4, 8.5) * 1024 ** 3)
        runtime = int(rng.uniform(92, 148) * 60)
        audio, subtitles, default = _audio_tracks(rng)
        items.append((_jellyfin_id(key), title, "Movie", _jellyfin_id("demo-movies"), None, None, None, None, None,
                      released.year, runtime * 10_000_000, arrived.strftime("%Y-%m-%dT%H:%M:%S.0000000Z"),
                      f"D:/media/filmy/{title}.mkv", demo_world.tmdb_id(key), "matroska", codec, "eac3", 6,
                      int(height * 16 / 9), height, int(size * 8 / runtime), size, "HDR" if height > 2000 else "SDR",
                      audio, subtitles, default, "ffprobe", _ts(now), _ts(now)))
        # Two to four of the household watch a new movie in the days after it came (never before).
        _watched(rng, plays, "demo-new-movie", items[-1], arrived, now, (2, 4), (2, 96))
    _store_new(conn, items, plays)
    return len(items), len(plays)


def _seed_music(conn, now: datetime) -> int:
    """The music library and a year of listening. Its own randomness: nothing of the other data moves.

    The listening thins out into the past (more in the last weeks), so the
    cards for the last 30 days have something to show. Returns the number of
    listens written.
    """
    rng = random.Random(11)
    names = dict(MUSIC_ARTISTS)
    library_id = _jellyfin_id("demo-music")
    for interpret_id, title in MUSIC_ARTISTS:
        conn.execute("INSERT INTO music_artist (id, name, synced_at) VALUES (?, ?, ?)",
                     (_jellyfin_id(interpret_id), title, _ts(now)))
    tracks: list[tuple] = []
    for album_id, title, interpret_id, year, genres, days_ago, codec, bitrate, sample_rate, bit_depth, track_count in MUSIC_ALBUMS:
        added = _ts(now - timedelta(days=days_ago))
        conn.execute(_ALBUM_SQL, (_jellyfin_id(album_id), title, _jellyfin_id(interpret_id), names[interpret_id], year, genres, added,
                                  _ts(now), library_id))
        for number in range(1, track_count + 1):
            seconds = rng.randint(150, 330)
            # The file path, so music lands on its disk in the Disks card (content_kinds.py).
            path = f"/media/music/{names[interpret_id]}/{title}/{number:02d}.flac"
            track = (_jellyfin_id(f"{album_id}-{number}"), f"{title} {number}", _jellyfin_id(album_id), title,
                     _jellyfin_id(interpret_id), names[interpret_id], number,
                     seconds * 10_000_000, year, genres, codec, bitrate, sample_rate, bit_depth, bitrate * seconds // 8, added, _ts(now),
                     path, library_id)
            conn.execute(_TRACK_SQL, track)
            if album_id != "demo-alb-8":
                tracks.append(track)
    viewer_names = {user_id: name for user_id, name, _ in USERS}
    listens: list[tuple] = []
    for index in range(700):
        track = rng.choice(tracks)
        user_id = rng.choice(MUSIC_LISTENERS)
        app, device = rng.choice(MUSIC_CLIENTS)
        start = now - timedelta(days=int(rng.random() ** 2 * 360), hours=rng.randint(0, 23), minutes=rng.randint(0, 59))
        length = track[7] // 10_000_000
        seconds = rng.randint(5, 25) if rng.random() < 0.15 else length      # a skipped track now and then
        transcoded = track[10] in ("flac", "alac") and rng.random() < 0.2
        listens.append((f"demo-hudba-{index}", _jellyfin_id(user_id), viewer_names[user_id], track[0], track[1], app, device,
                        HOME_NETWORK, "Transcode" if transcoded else "DirectPlay",
                        rng.choice(["AudioCodecNotSupported", "ContainerBitrateExceedsLimit"]) if transcoded else None,
                        192_000 if transcoded else None, track[7], _ts(start), _ts(start + timedelta(seconds=seconds)),
                        _ts(start + timedelta(seconds=seconds)), seconds))
    conn.executemany(_LISTEN_SQL, listens)
    return len(listens)


def _seed_new_albums(conn, now: datetime) -> tuple[int, int]:
    """The albums of the last weeks in the music library (NEW_ALBUMS), and who listened to them. Returns (albums, listens).

    After everything else and with its own randomness: the library and the history drawn before stay as they were.
    Each came out at midnight UTC and reached the library its hours later; two or three of the household play it
    through in the days after it came (never before), a listen that would not have ended by `now` is left out.
    """
    rng = random.Random(31)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    names, viewers = dict(MUSIC_ARTISTS), {user_id: name for user_id, name, _ in USERS}
    library_id = _jellyfin_id("demo-music")
    listens: list[tuple] = []
    for key, (title, artist, genres, codec, bitrate, sample_rate, bit_depth, count, days, hours) in NEW_ALBUMS.items():
        released = today - timedelta(days=days)
        arrived = released + timedelta(hours=hours)
        conn.execute(_ALBUM_SQL, (_jellyfin_id(key), title, _jellyfin_id(artist), names[artist], released.year, genres, _ts(arrived),
                                  _ts(now), library_id))
        tracks = []
        for number in range(1, count + 1):
            seconds = rng.randint(150, 330)
            tracks.append((_jellyfin_id(f"{key}-{number}"), f"{title} {number}", _jellyfin_id(key), title, _jellyfin_id(artist),
                           names[artist], number, seconds * 10_000_000, released.year, genres, codec, bitrate, sample_rate,
                           bit_depth, bitrate * seconds // 8, _ts(arrived), _ts(now),
                           f"/media/music/{names[artist]}/{title}/{number:02d}.flac", library_id))
        conn.executemany(_TRACK_SQL, tracks)
        for user_id in rng.sample(MUSIC_LISTENERS, rng.randint(2, 3)):
            app, device = rng.choice(MUSIC_CLIENTS)
            start = arrived + timedelta(hours=rng.uniform(1, 40))
            for track in tracks:
                length = track[7] // 10_000_000
                if start + timedelta(seconds=length) >= now:
                    break
                listens.append((f"demo-new-album-{len(listens)}", _jellyfin_id(user_id), viewers[user_id], track[0], track[1], app,
                                device, HOME_NETWORK, "DirectPlay", None, None, track[7], _ts(start),
                                _ts(start + timedelta(seconds=length)), _ts(start + timedelta(seconds=length)), length))
                start += timedelta(seconds=length + rng.randint(1, 4))
    conn.executemany(_LISTEN_SQL, listens)
    return len(NEW_ALBUMS), len(listens)


# ---- books ------------------------------------------------------------
# Made-up e-books and audiobooks for the Books section. An audiobook in several files (Cesta na jih,
# four parts) is one book with four rows; the e-books come in the common formats. Nobody reads "Zapomenuty
# atlas" and nobody listens to "Tajemstvi mlyna" - the cards of what nobody reads have something to show.
# id, title, author, series, year, genres, format, added days ago, size (MB), parts (audiobooks), hours of audio
BOOKS = [
    ("demo-kn-1", "Pisek a vitr", "Marie Dvorakova", "", 2018, "Roman|Drama", "epub", 400, 2, 0, 0),
    ("demo-kn-2", "Hvezdna brana", "Jan Navratil", "Hvezdna kronika", 2021, "Science Fiction", "epub", 30, 3, 0, 0),
    ("demo-kn-3", "Hvezdna prisaha", "Jan Navratil", "Hvezdna kronika", 2022, "Science Fiction", "epub", 9, 3, 0, 0),
    ("demo-kn-4", "Algoritmy bez vzorcu", "Vaclav Kral", "", 2019, "Informatika", "pdf", 250, 14, 0, 0),
    ("demo-kn-5", "Kuchyne pro lenochy", "Eva Sladka", "", 2020, "Kucharka", "pdf", 120, 38, 0, 0),
    ("demo-kn-6", "Pribehy z Trolli hory", "Karel Maly", "", 2016, "Komiks", "cbz", 600, 52, 0, 0),
    ("demo-kn-7", "Zapomenuty atlas", "Petra Horska", "", 2012, "Cestopis", "mobi", 800, 1, 0, 0),
    ("demo-kn-8", "Stare povesti", "Alois Jirasek", "", 1894, "Klasika|Legendy", "azw3", 900, 1, 0, 0),
    ("demo-ab-1", "Cesta na jih", "Karel Vypravec", "", 2020, "Dobrodruzstvi", "mp3", 70, 28, 4, 11),
    ("demo-ab-2", "Hobit", "J. R. R. Tolkien", "Stredozem", 1937, "Fantasy", "m4b", 200, 410, 1, 11),
    ("demo-ab-3", "Vrazda v expresu", "Agatha Christie", "Poirot", 1934, "Detektivka", "m4b", 14, 260, 1, 7),
    ("demo-ab-4", "Pohadky na dobrou noc", "Lenka Ticha", "", 2017, "Pro deti", "mp3", 300, 90, 6, 4),
    ("demo-ab-5", "Dejiny evropy", "Prof. Milan Kovar", "", 2015, "Historie", "mp3", 45, 320, 5, 14),
    ("demo-ab-6", "Tajemstvi mlyna", "Anna Vetrna", "", 2023, "Detektivka|Pro deti", "m4b", 5, 150, 1, 6),
]
BOOK_CLIENTS = [("Finamp", "Pixel 8"), ("Jellyfin Web", "Chrome na notebooku"), ("Symfonium", "Galaxy S23"),
                ("Jellyfin Web", "Tablet v loznici")]
BOOK_READERS = ["demo-u2", "demo-u3", "demo-u4", "demo-u6", "demo-u9"]
# Books of the last weeks: {key: (title, author, series, genres, format, size (MB), parts (audiobooks), hours of audio,
# the days from its release - at midnight UTC - back from the seed's day, the hours from it until the library has it)}.
# By the demo's authors, e-books and audiobooks alike. The made-up Readarr had each before it came out
# (arr/readarr/demo.py, demo_world.book_release), grabbed and imported it in those hours: four in the last days, each in
# another bucket of "how soon after release", two in the weeks before, one of them late (no release met the profile for
# three weeks). Each is in the library before the seed's day begins (hours <= days * 24). Their files carry an ISBN
# (demo_world.isbn13 of the key), so Readarr pairs them by it, the older ones by title and author. In the catalog they
# follow the library's other books (demo_world: books 15-20); `random` draws nothing for them (_seed_new_books).
NEW_BOOKS = {
    "demo-kn-new-1": ("Hvezdny pristav", "Jan Navratil", "Hvezdna kronika", "Science Fiction", "epub", 3, 0, 0, 1, 0.5),
    "demo-ab-new-1": ("Pohadky pod snehem", "Lenka Ticha", "", "Pro deti", "mp3", 140, 3, 5, 2, 4.5),
    "demo-kn-new-2": ("Kava o pulnoci", "Marie Dvorakova", "", "Roman|Drama", "epub", 2, 0, 0, 3, 15.0),
    "demo-ab-new-2": ("Stin nad mlynem", "Anna Vetrna", "", "Detektivka|Pro deti", "m4b", 170, 1, 6, 4, 62.0),
    "demo-kn-new-3": ("Databaze bez vzorcu", "Vaclav Kral", "", "Informatika", "pdf", 12, 0, 0, 13, 2.0),
    "demo-ab-new-3": ("Dejiny stredoveku", "Prof. Milan Kovar", "", "Historie", "mp3", 380, 6, 16, 36, 22 * 24.0),
}
# The book of the last weeks Readarr took a worse edition of first (a MOBI or a PDF) and its better one (the library's
# EPUB) later: (its key, the hours from the first import to the better one). Within the seed's day before, so the last
# 7 days always have an upgrade.
UPGRADED_BOOK = ("demo-kn-new-2", 26.0)
# Books the library does not have yet, only the made-up Readarr knows them: (key, title, author, an audiobook?, the days
# from today to their release, at midnight UTC). Two are out already: Readarr is downloading the first and has not
# found the second (missing); the others are in its calendar (demo_world.coming_books).
COMING_BOOKS = [
    ("demo-ab-coming-0", "Cesta na sever", "Karel Vypravec", True, -3),
    ("demo-kn-coming-1", "Posledni hvezda", "Jan Navratil", False, -1),
    ("demo-kn-coming-2", "Kuchyne pro spechajici", "Eva Sladka", False, 3),
    ("demo-kn-coming-3", "Mapy bez hranic", "Petra Horska", False, 6),
    ("demo-kn-coming-4", "Navrat do Trolli hory", "Karel Maly", False, 11),
]
# A book's row and a reading (or a listening), as the books seeds write them.
_BOOK_SQL = ("INSERT INTO book (id, kind, name, title, author, title_key, series, year, genres, format, size_bytes, runtime_ticks,"
             " path, isbn, added_at, library_id, synced_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)")
_READ_SQL = ("INSERT INTO playback (session_key, user_id, user_name, item_id, item_name, item_type, client, device_name,"
             " remote_address, play_method, transcode_reasons, media_runtime_ticks, started_at, last_seen_at, ended_at,"
             " watched_seconds, paused_seconds, is_active) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0)")


def _book_rows(conn, now: datetime, book: tuple, added: datetime, isbn: str = "") -> list[tuple]:
    """Write a book (a row of BOOKS without its days) as the library has it: an e-book or an audiobook, in parts or not.

    Returns its rows as (id, name, item type, runtime ticks), the parts of an audiobook in their order.
    """
    from .books import book_title_key

    book_id, title, author, series, year, genres, file_format, megabytes, parts, hours = book
    audio = parts > 0
    library = "demo-audiobooks" if audio else "demo-books"
    rows = []
    for part in range(1, max(parts, 1) + 1):
        item_id = _jellyfin_id(f"{book_id}-{part}" if parts > 1 else book_id)
        name = f"Kapitola {part}" if parts > 1 else title
        ticks = (hours * 3600 * 10_000_000) // parts if audio else None
        folder = "audiobooks" if audio else "books"
        path = f"/media/{folder}/{author}/{title}/{part:02d}.{file_format}" if parts > 1 else f"/media/{folder}/{author}/{title}.{file_format}"
        conn.execute(_BOOK_SQL, (item_id, "audiobook" if audio else "book", name, title, author, book_title_key(title, author), series, year,
                                 genres, file_format, megabytes * 1_000_000 // max(parts, 1), ticks, path, isbn, _ts(added),
                                 _jellyfin_id(library), _ts(now)))
        rows.append((item_id, name, "AudioBook" if audio else "Book", ticks))
    return rows


def _seed_books(conn, now: datetime) -> int:
    """The books libraries and a year of listening and reading. Its own randomness: nothing else moves.

    Returns the number of rows written into the playback history.
    """
    rng = random.Random(13)
    items: list[tuple] = []        # (id, name, kind, runtime_ticks)
    for book in BOOKS:
        rows = _book_rows(conn, now, (*book[:7], *book[8:]), now - timedelta(days=book[7]))
        if book[1] not in ("Tajemstvi mlyna", "Zapomenuty atlas"):
            items += rows
    viewer_names = {user_id: name for user_id, name, _ in USERS}
    listens: list[tuple] = []
    for index in range(420):
        item_id, name, item_type, ticks = rng.choice(items)
        reading = item_type == "Book"
        if reading and rng.random() < 0.6:           # e-books are read less than books are listened to
            continue
        user_id = rng.choice(BOOK_READERS)
        app, device = rng.choice(BOOK_CLIENTS)
        start = now - timedelta(days=int(rng.random() ** 2 * 360), hours=rng.randint(0, 23), minutes=rng.randint(0, 59))
        seconds = rng.randint(300, 2400) if reading else rng.randint(600, 4800)
        transcoded = not reading and rng.random() < 0.1
        listens.append((f"demo-knihy-{index}", _jellyfin_id(user_id), viewer_names[user_id], item_id, name, item_type, app, device,
                        HOME_NETWORK, "Transcode" if transcoded else "DirectPlay", "AudioCodecNotSupported" if transcoded else None,
                        ticks or 0, _ts(start), _ts(start + timedelta(seconds=seconds)), _ts(start + timedelta(seconds=seconds)), seconds))
    conn.executemany(_READ_SQL, listens)
    return len(listens)


def _seed_new_books(conn, now: datetime) -> tuple[int, int]:
    """The books of the last weeks in the books libraries (NEW_BOOKS), and who read them. Returns (books, readings).

    After everything else and with its own randomness: the library and the history drawn before stay as they were.
    Each came out at midnight UTC and reached the library its hours later, with its ISBN; two or three of the
    household read it (an audiobook part after part) in the days after it came, never before - a reading that would
    not have ended by `now` is left out.
    """
    rng = random.Random(37)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    viewers = {user_id: name for user_id, name, _ in USERS}
    readings: list[tuple] = []
    for key, (title, author, series, genres, file_format, megabytes, parts, hours_of_audio, days, hours) in NEW_BOOKS.items():
        released = today - timedelta(days=days)
        arrived = released + timedelta(hours=hours)
        rows = _book_rows(conn, now, (key, title, author, series, released.year, genres, file_format, megabytes, parts, hours_of_audio),
                          arrived, demo_world.isbn13(key))
        for user_id in rng.sample(BOOK_READERS, rng.randint(2, 3)):
            app, device = rng.choice(BOOK_CLIENTS)
            start = arrived + timedelta(hours=rng.uniform(1, 40))
            for item_id, name, item_type, ticks in rows:
                seconds = rng.randint(1200, 4800) if ticks is None else int(ticks // 10_000_000 * rng.uniform(0.5, 1.0))
                if start + timedelta(seconds=seconds) >= now:
                    break
                readings.append((f"demo-new-book-{len(readings)}", _jellyfin_id(user_id), viewers[user_id], item_id, name, item_type, app,
                                 device, HOME_NETWORK, "DirectPlay", None, ticks or 0, _ts(start), _ts(start + timedelta(seconds=seconds)),
                                 _ts(start + timedelta(seconds=seconds)), seconds))
                start += timedelta(seconds=seconds, hours=rng.uniform(2, 20))
    conn.executemany(_READ_SQL, readings)
    return len(NEW_BOOKS), len(readings)


# The demo's storage, in the demo mode (a visitor's Disks card and Library growth; demodata.seed outside it - the tests'
# library - stays without). The made-up Jellyfin is a 10.11 (demo_jellyfin), which does not break the disks down: the
# Disks card has its one row "Library", whose capacity the owner entered by hand in Settings -> Data (what its form
# stores). After every daily sync the app writes the library's snapshot and the disk row's (scanner.write_daily_snapshots);
# the demo has run for MEASURED_DAYS, so Library growth is measured over the last weeks (reconstructed before) and both
# show the free space and when it runs out.
STORAGE_CAPACITY = 1024 ** 4            # 1 TB (the form's unit TB, stored in bytes)
MEASURED_DAYS = 60


def _seed_storage(now: datetime) -> None:
    """The capacity and the daily snapshots up to today: the past days as the app measured them, today through its own
    writer. Outside the demo mode nothing."""
    from . import content_kinds, formatting, scanner, stats
    from .config import load_config

    if not load_config().demo_mode:
        return
    db.set_setting("library_capacity_unit", "TB")
    db.set_setting(scanner.CAPACITY_KEY, str(STORAGE_CAPACITY))
    today = now.astimezone(formatting.zone()).date()
    # Before the first snapshot the library's past is the app's own backfill from when its files came (stats.snapshots):
    # what a snapshot measured on each day. The counts of 4K, HDR and titles without technical data are only stored
    # (nothing draws them): today's.
    past = [row for row in stats.snapshots(MEASURED_DAYS) if row["day"] < today.isoformat()]
    files, state, disk = content_kinds.history(), stats.library_state(), scanner.disk_state()["disks"][0]
    with db.connect() as conn:
        for row in past:
            day, size = row["day"], int(row["size_bytes"])
            parts = {kind.key: int((row.get("kinds") or {}).get(kind.key, 0)) for kind in content_kinds.KINDS}
            present = {kind.key: sum(1 for f in files if f["kind"] == kind.key and f["added"] <= day
                                     and not (f["gone"] and f["gone"] <= day)) for kind in content_kinds.KINDS}
            written = _ts(now - timedelta(days=(today - date.fromisoformat(day)).days))
            free = max(0, STORAGE_CAPACITY - size)
            conn.execute(
                "INSERT INTO library_snapshot (day, item_count, movie_count, episode_count, size_bytes, uhd, hdr,"
                " without_tech, free_space, written_at, kinds) VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT (day) DO NOTHING",
                (day, row["item_count"], row["movie_count"], row["episode_count"], size, state["uhd"], state["hdr"],
                 state["without_tech"], free, written,
                 json.dumps({key: {"items": present[key], "size": parts[key]} for key in parts}, sort_keys=True)))
            conn.execute(
                "INSERT INTO library_disk_snapshot (day, disk, name, kind, source, total, free, library, outside, fingerprint,"
                " written_at, kinds) VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT (day, disk) DO NOTHING",
                (day, disk["key"], disk["name"], disk["kind"], disk["source"], STORAGE_CAPACITY, free, size, None,
                 disk["fingerprint"], written, json.dumps(parts, sort_keys=True)))
        conn.commit()
    scanner.write_daily_snapshots()


def seed() -> dict[str, int]:
    """Fills the database with made-up traffic over the last year."""
    random.seed(42)          # the same number = the same data on every run
    # The transcode speed is drawn apart - it was added later and must not move
    # anything of what is drawn from `random` already (see below).
    fps_random = random.Random(7)
    now = datetime.now(UTC)

    items: list[tuple] = []
    plays: list[tuple] = []
    # The key each title's TMDB id is made from, in the order of `items`: a movie its own, an episode its
    # series' - Jellyfin gives an episode the TMDB id of its series (scanner.tmdb_id_of).
    tmdb_keys: list[str] = []
    movies_library, tv_library = _jellyfin_id("demo-movies"), _jellyfin_id("demo-tv")
    language_in_name = _jellyfin_id(LANGUAGE_IN_NAME)

    with db.connect() as conn:
        for library_id, name, collection_type in LIBRARIES:
            conn.execute(
                "INSERT INTO libraries (id, name, collection_type, paths, synced_at)"
                " VALUES (?,?,?,?,?) ON CONFLICT (id) DO NOTHING",
                (_jellyfin_id(library_id), name, collection_type, '["D:/media"]', _ts(now)),
            )

        for user_id, name, is_admin in USERS:
            conn.execute(
                "INSERT INTO users"
                " (id, name, is_administrator, is_disabled, last_activity, synced_at)"
                " VALUES (?,?,?,0,?,?) ON CONFLICT (id) DO NOTHING",
                (_jellyfin_id(user_id), name, is_admin, _ts(now), _ts(now)),
            )

        # ---- movies ------------------------------------------------
        for index, title in enumerate(MOVIES):
            # A few movies big on purpose and in 4K, so the Insights page has
            # something to show as "takes a lot, is watched little".
            is_huge = index % 7 == 0
            height = 2160 if is_huge else random.choice([1080, 1080, 1080, 720, 576])
            size = int(random.uniform(45, 68) * 1024 ** 3) if is_huge \
                else int(random.uniform(1.5, 14) * 1024 ** 3)
            codec = "hevc" if is_huge else random.choice(["h264", "h264", "hevc", "mpeg4"])
            created = now - timedelta(days=random.randint(90, 1200))

            tmdb_keys.append(f"demo-movie-{index}")
            items.append((
                _jellyfin_id(f"demo-movie-{index}"), title, "Movie", movies_library,
                None, None, None, None, None,
                random.randint(1965, 2024),
                int(random.uniform(85, 165) * 60 * 10_000_000),
                created.strftime("%Y-%m-%dT%H:%M:%S.0000000Z"),
                f"D:/media/filmy/{title}.mkv",
                "matroska", codec,
                random.choice(["aac", "ac3", "eac3", "dts"]),
                random.choice([2, 6, 6, 8]),
                int(height * 16 / 9), height,
                int(size * 8 / (random.uniform(90, 160) * 60)),
                size,
                # Some of the big movies are Dolby Vision - in libraries that is
                # common and without it the third column "SDR / HDR / Dolby
                # Vision" would never show anything in the demo.
                (random.choice(["HDR", "HDR", "DOVI"]) if is_huge else "SDR"),
                *_audio_tracks(random),
                random.choice(["ffprobe", "ffprobe", "jellyfin"]),
                _ts(now), _ts(now),
            ))

        # A title whose language neither the file nor the library knows - only
        # the file name knows it. Exactly this case is common in libraries and it
        # is the only way to see the "guess from the name" in the demo.
        tmdb_keys.append(LANGUAGE_IN_NAME)
        items.append((
            language_in_name, "Sedmikrasky", "Movie", movies_library,
            None, None, None, None, None, 1966,
            74 * 60 * 10_000_000, "2022-06-11T09:20:00.0000000Z",
            "D:/media/filmy/Sedmikrasky.1966.CZ.SK.EN.1080p.mkv",
            "matroska", "h264", "ac3", 2,
            1920, 1080, 8_200_000, int(4.1 * 1024 ** 3), "SDR",
            # Nothing in the summary yet - it is computed from the name only at the end.
            languages.UNKNOWN, "", languages.UNKNOWN,
            "ffprobe", _ts(now), _ts(now),
        ))

        # one movie twice on purpose - for detecting duplicates
        for suffix, height, size, codec in (("1080p", 1080, 9, "h264"), ("2160p", 2160, 54, "hevc")):
            tmdb_keys.append(f"demo-movie-dup-{suffix}")
            items.append((
                _jellyfin_id(f"demo-movie-dup-{suffix}"), "Interstellar", "Movie", movies_library,
                None, None, None, None, None, 2014,
                169 * 60 * 10_000_000, "2021-03-02T12:00:00.0000000Z",
                f"D:/media/filmy/Interstellar ({suffix}).mkv",
                "matroska", codec, "eac3", 6,
                int(height * 16 / 9), height,
                int(size * 1024 ** 3 * 8 / (169 * 60)), size * 1024 ** 3,
                "DOVI" if height > 2000 else "SDR",
                "cs,en", "cs,en", "cs",
                "ffprobe", _ts(now), _ts(now),
            ))

        # ---- episodes ----------------------------------------------
        #
        # The first series of the list comes WHOLE AT ONCE and as the very last
        # addition. It is the most common way series come into a library (a whole
        # season is downloaded), and thanks to it the Overview shows how the
        # episodes merge under one card instead of each taking its own tile.
        for series_index, series_name in enumerate(SERIES):
            whole_season = series_index == 0
            season_arrived = now - timedelta(days=1, hours=3)
            for episode in range(1, random.randint(8, 16)):
                height = random.choice([1080, 1080, 720])
                size = int(random.uniform(0.9, 3.5) * 1024 ** 3)
                if whole_season:
                    # The episodes of one batch do not arrive in the same second,
                    # but within a while - exactly as downloading adds them.
                    created = season_arrived + timedelta(minutes=episode * 4)
                else:
                    created = now - timedelta(days=random.randint(30, 900))
                tmdb_keys.append(f"demo-series-{series_index}")
                items.append((
                    _jellyfin_id(f"demo-ep-{series_index}-{episode}"),
                    f"{episode}. dil", "Episode", tv_library,
                    _jellyfin_id(f"demo-series-{series_index}"), series_name, "Serie 1",
                    episode, 1,
                    random.randint(2005, 2023),
                    int(random.uniform(22, 55) * 60 * 10_000_000),
                    created.strftime("%Y-%m-%dT%H:%M:%S.0000000Z"),
                    f"D:/media/serialy/{series_name}/S01E{episode:02d}.mkv",
                    "matroska", random.choice(["h264", "h264", "hevc"]),
                    random.choice(["aac", "ac3"]), 2,
                    int(height * 16 / 9), height,
                    int(size * 8 / (35 * 60)), size,
                    "SDR",
                    *_audio_tracks(random),
                    random.choice(["ffprobe", "jellyfin"]),
                    _ts(now), _ts(now),
                ))

        # ---- single streams ----------------------------------------
        # Without them the item detail would show emptiness. We put them together
        # so they match the languages the title has in the summary.
        stream_rows: list[tuple] = []
        for entry in items:
            item_id = entry[0]
            index = 0

            # A title with a language only in the name has its streams put
            # together by hand a bit below - the general loop derives them from the
            # summary, and that has no language for it.
            if item_id == language_in_name:
                continue

            stream_rows.append((
                item_id, index, "Video", entry[14], languages.UNKNOWN, None,
                None, None, entry[17], entry[18], entry[19], 1, 0, 0,
            ))
            index += 1

            for code in languages.unpack(entry[22]):
                channels = random.choice([2, 6, 6, 8])
                stream_rows.append((
                    item_id, index, "Audio",
                    random.choice(["aac", "ac3", "eac3", "dts"]),
                    code,
                    f"{code.upper()} {channels}.0" if code != languages.UNKNOWN else None,
                    channels,
                    {2: "stereo", 6: "5.1", 8: "7.1"}.get(channels),
                    None, None, None,
                    1 if index == 1 else 0, 0, 0,
                ))
                index += 1

            for code in languages.unpack(entry[23]):
                stream_rows.append((
                    item_id, index, "Subtitle",
                    random.choice(["subrip", "ass", "pgssub"]),
                    code, None, None, None, None, None, None,
                    0, 1 if random.random() < 0.2 else 0,
                    1 if random.random() < 0.3 else 0,
                ))
                index += 1

        conn.executemany(
            """INSERT INTO items (id, name, type, library_id, series_id,
                series_name, season_name, index_number, parent_index_number,
                production_year, runtime_ticks, date_created, path, container,
                video_codec, audio_codec, audio_channels, width, height, bitrate,
                size_bytes, video_range, audio_languages, subtitle_languages,
                default_audio_language, tech_source, tech_updated_at, synced_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT (id) DO NOTHING""",
            items,
        )

        # The streams only here: they refer to the items by a foreign key, so the
        # items must be in the database already. The opposite order would end in an error.
        # Three audio streams without a language - they could not be put together
        # from the summary, because that must not have languages twice. So by hand.
        stream_rows.append((
            language_in_name, 0, "Video", "h264", languages.UNKNOWN, None,
            None, None, 1920, 1080, 8_200_000, 1, 0, 0,
        ))
        for stream_index, channel_count in enumerate((2, 6, 6), start=1):
            stream_rows.append((
                language_in_name, stream_index, "Audio", "ac3", languages.UNKNOWN, None,
                channel_count, {2: "stereo", 6: "5.1"}[channel_count], None, None, None,
                1 if stream_index == 1 else 0, 0, 0,
            ))

        conn.executemany(
            """INSERT INTO item_streams (
                item_id, stream_index, type, codec, language, title,
                channels, channel_layout, width, height, bitrate,
                is_default, is_forced, is_external
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT (item_id, stream_index) DO NOTHING""",
            stream_rows,
        )

        # ---- tmdb_id and the archive -------------------------------
        # We fill it in only here with a separate UPDATE, not in the tuples above.
        # Those are positional (entry[14] is the codec, entry[22] the languages,
        # ...) and inserting a column in the middle would move them all. This is
        # duller, but it does not break.
        conn.executemany(
            "UPDATE items SET tmdb_id = ? WHERE id = ?",
            [(demo_world.tmdb_id(key), entry[0]) for key, entry in zip(tmdb_keys, items, strict=True)],
        )

        # Two titles in the archive on purpose - so the library shows what a
        # record looks like that is no longer in Jellyfin, but the history refers to it.
        conn.executemany(
            "UPDATE items SET is_missing = 1 WHERE id = ?",
            [(entry[0],) for entry in items[:2]],
        )

        # ---- playback history ---------------------------------------
        # We watch only a part of the library - so "space nobody uses" shows a
        # realistic value, not zero.
        watchable = [item for item in items if random.random() < 0.55]

        # Two and a half thousand plays, not nine hundred.
        #
        # The reason is not "more is more": the concurrent flow chart on the
        # Network page was so sparse with nine hundred that over a week it showed
        # a few lonely peaks and plain zero between them. A real server where a
        # family watches looks different - in the evening three streams run at
        # once and the chart has a shape, not scraps. The demo is to show what it
        # looks like in operation.
        for session in range(2500):
            item = random.choice(watchable)
            user = random.choice(USERS)
            client, device = random.choice(CLIENTS)

            # The evening peak: most plays between 18 and 23 o'clock.
            # Half falls on the last month - there a person watches most often
            # and the chart is to be dense there.
            day_offset = (random.uniform(0, 30) if random.random() < 0.5
                          else random.uniform(0, 360))
            hour = random.choice([19, 20, 20, 21, 21, 22, 18, 23, 14, 10])
            started = (now - timedelta(days=day_offset)).replace(
                hour=hour, minute=random.randint(0, 59)
            )
            if started > now:
                # An evening hour of today is often still in the future.
                # Such a record used to be moved to "two hours ago" - but that
                # hit hundreds of records at once and in the chart it made one
                # nonsense tower of a few hundred Mbit/s at the moment the demo
                # was seeded. So we move it a day back.
                started -= timedelta(days=1, minutes=random.randint(0, 240))

            runtime_seconds = item[10] / 10_000_000
            watched = int(runtime_seconds * random.uniform(0.08, 1.0))

            # Big HEVC files are transcoded more often - exactly the phenomenon
            # the Insights page is to uncover.
            if item[14] == "hevc" and random.random() < 0.55:
                method = "Transcode"
            else:
                method = random.choice(
                    ["DirectPlay", "DirectPlay", "DirectPlay", "DirectStream", "Transcode"]
                )

            reasons = None
            # What exactly is transcoded. We derive it from the reason, so the
            # tooltip at the "transcode" badge says the same as the reason under
            # it - in real operation Jellyfin reports it in TranscodingInfo.
            video_direct = audio_direct = None
            hw = None
            if method == "Transcode":
                reasons = random.choice([
                    "VideoCodecNotSupported",
                    "AudioCodecNotSupported",
                    "VideoCodecNotSupported,AudioCodecNotSupported",
                    "ContainerBitrateExceedsLimit",
                    "SubtitleCodecNotSupported",
                ])
                video_direct = 0 if ("Video" in reasons or "Bitrate" in reasons
                                     or "Subtitle" in reasons) else 1
                audio_direct = 0 if "Audio" in reasons else 1
                if not video_direct and random.random() < 0.5:
                    hw = random.choice(["qsv", "nvenc", "vaapi"])

            # The codec that really flowed to the player. When transcoding it is
            # the transcode's target (browsers can do H264 and AAC almost all),
            # otherwise the original file's codec. Without this difference the
            # tooltip would report "HEVC -> HEVC" and make no sense.
            video_codec = item[14] if video_direct != 0 else "h264"
            audio_codec = item[15] if audio_direct != 0 else "aac"

            # The transcode speed - only when the picture is transcoded. An own
            # generator, not `random`: every extra call would move everything
            # drawn after it, and the demo would look different.
            # Hardware runs many times faster than the video; the processor
            # sometimes does not keep up, so the tooltip shows a warning too.
            video_fps = 24 if item[2] == "Movie" else 25
            fps_sum = fps_samples = 0
            if video_direct == 0:
                fps_samples = max(1, watched // 10)
                speed = (fps_random.randint(90, 240) if hw
                         else fps_random.choice([18, 21, 32, 45, 60, 75]))
                fps_sum = speed * fps_samples

            # We pick the language only from what the title really has - just like
            # a real viewer. When Czech and the original are there to choose, every
            # member of the household decides differently: Petr more often the
            # original, Eva more often the dubbing. Thanks to that the Languages page has something to show.
            available = languages.unpack(item[22])
            real_options = [code for code in available if code != languages.UNKNOWN]

            if not real_options:
                audio_language = None
            elif len(real_options) == 1:
                audio_language = real_options[0]
            else:
                if random.random() < PREFERS_ORIGINAL.get(user[0], 0.4):
                    audio_language = next(
                        (code for code in real_options if code != "cs"), real_options[0]
                    )
                else:
                    audio_language = "cs" if "cs" in real_options else real_options[0]

            # Subtitles are turned on mainly with foreign-language audio.
            subtitle_options = languages.unpack(item[23])
            subtitle_language = None
            if subtitle_options:
                chance = 0.55 if audio_language not in (None, "cs") else 0.12
                if random.random() < chance:
                    subtitle_language = (
                        "cs" if "cs" in subtitle_options else subtitle_options[0]
                    )

            plays.append((
                f"demo-sess-{session}::{item[0]}",
                _jellyfin_id(user[0]), user[1],
                item[0], item[1], item[2], item[5], item[3],
                client, device, f"demo-dev-{user[0]}", _address(device),
                method, reasons, video_direct, audio_direct, hw,
                fps_sum, fps_samples, video_fps,
                video_codec, audio_codec, item[19],
                audio_language, subtitle_language,
                _ts(started), _ts(started + timedelta(seconds=watched)),
                _ts(started + timedelta(seconds=watched)),
                watched, random.randint(0, 400),
                int(watched * 10_000_000), 0,
            ))

        conn.executemany(
            """INSERT INTO playback (session_key, user_id, user_name, item_id, item_name,
                item_type, series_name, library_id, client, device_name, device_id,
                remote_address, play_method, transcode_reasons,
                transcode_video_direct, transcode_audio_direct, transcode_hw,
                transcode_fps_sum, transcode_fps_samples, video_fps,
                video_codec, audio_codec,
                bitrate, audio_language, subtitle_language,
                started_at, last_seen_at, ended_at, watched_seconds,
                paused_seconds, position_ticks, is_active)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            plays,
        )

        # one "running right now", so the card on the Overview is visible too.
        #
        # It is filled in whole on purpose: the flow, the resolution and the
        # streams the viewer watches with. The "Now playing" card is the only
        # one a person sees before anything else - and in the demo it is to show
        # everything we know about a playback, not empty places.
        conn.execute(
            """INSERT INTO playback (session_key, user_id, user_name, item_id, item_name,
                item_type, series_name, library_id, client, device_name, play_method,
                transcode_reasons, transcode_video_direct, transcode_audio_direct,
                transcode_hw, transcode_fps, transcode_fps_sum,
                transcode_fps_samples, video_fps,
                video_codec, audio_codec, bitrate,
                video_width, video_height,
                audio_language, subtitle_language,
                current_audio_language, current_subtitle_language,
                started_at, last_seen_at,
                watched_seconds, paused_seconds, position_ticks, is_active)
               VALUES (?,?,'Jana',?,'Duna','Movie',NULL,
                       ?,'Jellyfin Web','Chrome na notebooku','Transcode',
                       'VideoCodecNotSupported',0,1,'qsv',148,39600,264,24,
                       'h264','eac3',8600000,
                       1280,720,
                       'cs','cs','cs','en',
                       ?,?,2640,0,26400000000,1)""",
            (LIVE_KEY, _jellyfin_id("demo-u2"), _jellyfin_id("demo-movie-0"), movies_library,
             _ts(now - timedelta(minutes=44)), _ts(now)),
        )

        # Music only at the end and with its own randomness - movies and series stay the same as before.
        _seed_music(conn, now)
        _seed_books(conn, now)
        airing_items, airing_plays = _seed_airing(conn, now)
        new_items, new_plays = _seed_new_movies(conn, now)
        _seed_new_albums(conn, now)
        _seed_new_books(conn, now)

    # At the very end, outside the block with the connection: the detection opens
    # its own. We do it the way the "File analysis" task would do it in live
    # operation - we do not fill in the result by hand, so the demo shows the
    # real feature, not its imitation.
    from . import scanner

    scanner.fill_languages_from_name([language_in_name])
    _seed_storage(now)

    # The made-up apps (*Arr, Seerr) last: their real syncs pair what they report with the library above. Only in
    # the demo mode; one app's failure is a line in the log, not a failed seed (demo_apps).
    from . import demo_apps

    demo_apps.load_fakes()
    demo_apps.seed_apps()

    # Since when the data hold - by that it is told they aged (see refresh()) -
    # and what content they are (see prepare()).
    db.set_setting(SEED_KEY, _ts(now))
    db.set_setting(CONTENT_KEY, str(DEMO_CONTENT_VERSION))
    db.forget_settings()

    return {"items": len(items) + airing_items + new_items, "plays": len(plays) + 1 + airing_plays + new_plays,
            "users": len(USERS)}
