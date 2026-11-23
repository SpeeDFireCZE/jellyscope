"""Made-up data for the demo mode.

It serves a single purpose: so you can click through the application before you
connect it to a real Jellyfin - and so it is visible what the charts look like
once some history has been collected.

This file has nothing to do with live operation. It writes into its own
database (data/demo.db), so it cannot affect your real data in any way.
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timedelta, UTC

from . import accounts, db, languages

log = logging.getLogger(__name__)

# The account you sign in to the demo with.
DEMO_USERNAME = "demo"
DEMO_PASSWORD = "demodemo"

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
                  "item_versions", "items", "library_snapshot", "users",
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
    """Makes the data again, moved to today. Returns the counts like seed()."""
    wipe()
    counts = seed()
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
    """Prepares the demo, if it is not prepared yet - or if it is old.

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
]
MUSIC_CLIENTS = [("Finamp", "Pixel 8"), ("Jellyfin Web", "Chrome na notebooku"), ("Feishin", "Počítač v pracovně"),
                 ("Symfonium", "Galaxy S23")]
MUSIC_LISTENERS = ["demo-u2", "demo-u3", "demo-u4", "demo-u6", "demo-u10"]


def _seed_music(conn, now: datetime) -> int:
    """The music library and a year of listening. Its own randomness: nothing of the other data moves.

    The listening thins out into the past (more in the last weeks), so the
    cards for the last 30 days have something to show. Returns the number of
    listens written.
    """
    rng = random.Random(11)
    names = dict(MUSIC_ARTISTS)
    for interpret_id, title in MUSIC_ARTISTS:
        conn.execute("INSERT INTO music_artist (id, name, synced_at) VALUES (?, ?, ?)", (interpret_id, title, _ts(now)))
    tracks: list[tuple] = []
    for album_id, title, interpret_id, year, genres, days_ago, codec, bitrate, sample_rate, bit_depth, track_count in MUSIC_ALBUMS:
        added = _ts(now - timedelta(days=days_ago))
        conn.execute("INSERT INTO music_album (id, name, artist_id, artist, year, genres, added_at, synced_at,"
                     " library_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'demo-music')",
                     (album_id, title, interpret_id, names[interpret_id], year, genres, added, _ts(now)))
        for number in range(1, track_count + 1):
            seconds = rng.randint(150, 330)
            # The file path, so music lands on its disk in the Disks card (content_kinds.py).
            path = f"/media/music/{names[interpret_id]}/{title}/{number:02d}.flac"
            track = (f"{album_id}-{number}", f"{title} {number}", album_id, title, interpret_id, names[interpret_id], number,
                     seconds * 10_000_000, year, genres, codec, bitrate, sample_rate, bit_depth, bitrate * seconds // 8, added, _ts(now),
                     path)
            conn.execute("INSERT INTO music_track (id, name, album_id, album, artist_id, artist, track_number, runtime_ticks,"
                         " year, genres, codec, bitrate, sample_rate, bit_depth, channels, size_bytes, added_at, synced_at, path,"
                         " library_id)"
                         " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 2, ?, ?, ?, ?, 'demo-music')", track)
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
        listens.append((f"demo-hudba-{index}", user_id, viewer_names[user_id], track[0], track[1], app, device,
                        HOME_NETWORK, "Transcode" if transcoded else "DirectPlay",
                        rng.choice(["AudioCodecNotSupported", "ContainerBitrateExceedsLimit"]) if transcoded else None,
                        192_000 if transcoded else None, track[7], _ts(start), _ts(start + timedelta(seconds=seconds)),
                        _ts(start + timedelta(seconds=seconds)), seconds))
    conn.executemany(
        "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name, item_type, client, device_name,"
        " remote_address, play_method, transcode_reasons, bitrate, media_runtime_ticks, started_at, last_seen_at, ended_at,"
        " watched_seconds, paused_seconds, is_active)"
        " VALUES (?, ?, ?, ?, ?, 'Audio', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0)", listens)
    return len(listens)


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


def _seed_books(conn, now: datetime) -> int:
    """The books libraries and a year of listening and reading. Its own randomness: nothing else moves.

    Returns the number of rows written into the playback history.
    """
    from .books import book_title_key

    rng = random.Random(13)
    items: list[tuple] = []        # (id, name, kind, runtime_ticks)
    for book_id, title, author, series, year, genres, file_format, days_ago, megabytes, parts, hours in BOOKS:
        audio = parts > 0
        library = "demo-audiobooks" if audio else "demo-books"
        added = _ts(now - timedelta(days=days_ago))
        for part in range(1, max(parts, 1) + 1):
            item_id = f"{book_id}-{part}" if parts > 1 else book_id
            name = f"Kapitola {part}" if parts > 1 else title
            ticks = (hours * 3600 * 10_000_000) // parts if audio else None
            folder = "audiobooks" if audio else "books"
            path = f"/media/{folder}/{author}/{title}/{part:02d}.{file_format}" if parts > 1 else f"/media/{folder}/{author}/{title}.{file_format}"
            conn.execute("INSERT INTO book (id, kind, name, title, author, title_key, series, year, genres, format, size_bytes, runtime_ticks,"
                         " path, added_at, library_id, synced_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (item_id, "audiobook" if audio else "book", name, title, author, book_title_key(title, author), series, year, genres, file_format,
                          megabytes * 1_000_000 // max(parts, 1), ticks, path, added, library, _ts(now)))
            if title not in ("Tajemstvi mlyna", "Zapomenuty atlas"):
                items.append((item_id, name, "AudioBook" if audio else "Book", ticks))
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
        listens.append((f"demo-knihy-{index}", user_id, viewer_names[user_id], item_id, name, item_type, app, device,
                        HOME_NETWORK, "Transcode" if transcoded else "DirectPlay", "AudioCodecNotSupported" if transcoded else None,
                        ticks or 0, _ts(start), _ts(start + timedelta(seconds=seconds)), _ts(start + timedelta(seconds=seconds)), seconds))
    conn.executemany(
        "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name, item_type, client, device_name,"
        " remote_address, play_method, transcode_reasons, media_runtime_ticks, started_at, last_seen_at, ended_at,"
        " watched_seconds, paused_seconds, is_active) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0)", listens)
    return len(listens)


def seed() -> dict[str, int]:
    """Fills the database with made-up traffic over the last year."""
    random.seed(42)          # the same number = the same data on every run
    # The transcode speed is drawn apart - it was added later and must not move
    # anything of what is drawn from `random` already (see below).
    fps_random = random.Random(7)
    now = datetime.now(UTC)

    items: list[tuple] = []
    plays: list[tuple] = []

    with db.connect() as conn:
        for library_id, name, collection_type in LIBRARIES:
            conn.execute(
                "INSERT INTO libraries (id, name, collection_type, paths, synced_at)"
                " VALUES (?,?,?,?,?) ON CONFLICT (id) DO NOTHING",
                (library_id, name, collection_type, '["D:/media"]', _ts(now)),
            )

        for user_id, name, is_admin in USERS:
            conn.execute(
                "INSERT INTO users"
                " (id, name, is_administrator, is_disabled, last_activity, synced_at)"
                " VALUES (?,?,?,0,?,?) ON CONFLICT (id) DO NOTHING",
                (user_id, name, is_admin, _ts(now), _ts(now)),
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

            items.append((
                f"demo-movie-{index}", title, "Movie", "demo-movies",
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
        items.append((
            "demo-movie-nazev", "Sedmikrasky", "Movie", "demo-movies",
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
            items.append((
                f"demo-movie-dup-{suffix}", "Interstellar", "Movie", "demo-movies",
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
                items.append((
                    f"demo-ep-{series_index}-{episode}",
                    f"{episode}. dil", "Episode", "demo-tv",
                    f"demo-series-{series_index}", series_name, "Serie 1",
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
            if item_id == "demo-movie-nazev":
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
            "demo-movie-nazev", 0, "Video", "h264", languages.UNKNOWN, None,
            None, None, 1920, 1080, 8_200_000, 1, 0, 0,
        ))
        for stream_index, channel_count in enumerate((2, 6, 6), start=1):
            stream_rows.append((
                "demo-movie-nazev", stream_index, "Audio", "ac3", languages.UNKNOWN, None,
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
            [(f"demo-tmdb-{index}", entry[0]) for index, entry in enumerate(items)],
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
                user[0], user[1],
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
               VALUES (?,'demo-u2','Jana','demo-movie-0','Duna','Movie',NULL,
                       'demo-movies','Jellyfin Web','Chrome na notebooku','Transcode',
                       'VideoCodecNotSupported',0,1,'qsv',148,39600,264,24,
                       'h264','eac3',8600000,
                       1280,720,
                       'cs','cs','cs','en',
                       ?,?,2640,0,26400000000,1)""",
            (LIVE_KEY, _ts(now - timedelta(minutes=44)), _ts(now)),
        )

        # Music only at the end and with its own randomness - movies and series stay the same as before.
        _seed_music(conn, now)
        _seed_books(conn, now)

    # At the very end, outside the block with the connection: the detection opens
    # its own. We do it the way the "File analysis" task would do it in live
    # operation - we do not fill in the result by hand, so the demo shows the
    # real feature, not its imitation.
    from . import scanner

    scanner.fill_languages_from_name(["demo-movie-nazev"])

    # Since when the data hold - by that it is told they aged (see refresh()).
    db.set_setting(SEED_KEY, _ts(now))
    db.forget_settings()

    return {"items": len(items), "plays": len(plays) + 1, "users": len(USERS)}
