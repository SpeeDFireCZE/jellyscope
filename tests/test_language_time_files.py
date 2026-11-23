# -*- coding: utf-8 -*-
"""The delay for the language, the local time in the day filter and the list of files without a language.

Three independent things, each with its own trap:

  * **The delay for the language.** The start of a movie is logos and jingles
    and the viewer only looks for the track during them. So the first few
    minutes are ignored when deciding — but the watched time counts to the
    language whole.

  * **The local time.** A day in the chart must end where the listing of times
    ends it. When "the local time" is computed in two places from different
    sources, the link to a day shows records too that by the shown time belong
    elsewhere.

  * **Files without a language.** The list exists for a single thing — the path
    to the file. Without it it would say "something is wrong" and leave the
    searching to the user.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
# An own home: without it the stored choice of the database in
# data/database.json would override DATABASE_PATH and the test would run
# against the live database.
os.environ["JELLYSCOPE_HOME"] = str(_tmp)
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "jazyk.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic"
os.environ["JELLYSCOPE_DEMO"] = "0"

from jellyscope import collector, db, langstats, stats  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


db.init_db()

STREAMS = [
    {"Type": "Video", "Index": 0, "Codec": "h264", "Width": 1920, "Height": 1080},
    {"Type": "Audio", "Index": 1, "Codec": "aac", "Language": "eng", "IsDefault": True},
    {"Type": "Audio", "Index": 2, "Codec": "ac3", "Language": "cze"},
]


def snapshot(stream: int) -> dict[str, Any]:
    return {
        "Id": "relace", "UserId": "u1", "UserName": "Karel",
        "PlayState": {"PlayMethod": "DirectPlay", "IsPaused": False,
                      "AudioStreamIndex": stream},
        "NowPlayingItem": {"Id": "film", "Name": "Film", "Type": "Movie",
                           "RunTimeTicks": 72_000_000_000, "MediaStreams": STREAMS},
    }


def age(seconds: int) -> None:
    """Moves the session into the past - the test cannot wait five minutes."""
    with db.connect() as conn:
        conn.execute(
            """UPDATE playback
                  SET last_seen_at   = datetime(last_seen_at, ?),
                      language_since = datetime(language_since, ?)""",
            (f"-{seconds} seconds", f"-{seconds} seconds"),
        )


def session() -> dict[str, Any]:
    return stats.active_sessions()[0]


print("--- the first minutes are ignored when deciding ---")
check(collector.LANGUAGE_GRACE_SECONDS == 240, "the intro is four minutes")
check(collector.MIN_LANGUAGE_SECONDS == 60, "and then a minute to settle")

# The logos play in English, at 2:30 the viewer switches to Czech.
collector._store_sessions([snapshot(1)], max_gap_seconds=600)
for step in range(1, 40):
    age(10)
    collector._store_sessions([snapshot(1 if step < 15 else 2)], max_gap_seconds=600)

    r = session()
    watched = int(r["watched_seconds"])
    if watched < 300:
        if r["audio_language"] is not None:
            check(False, f"the language was written too early (at {watched} s)")
            break
    else:
        break

r = session()
check(r["audio_language"] == "cs",
      f"after five minutes Czech is written ({r['audio_language']})")
check(int(r["watched_seconds"]) >= 300,
      f"and not earlier than in the fifth minute ({r['watched_seconds']} s)")
check(r["current_audio_language"] == "cs", "the card shows the same")

# English from the intro did not get into the statistics at all - that is the whole point.
check(r["audio_language"] != "en", "the English of the logos was not written")

# But the watched time is not shortened: the whole playback belongs to the
# language, including those first minutes.
check(int(r["watched_seconds"]) >= collector.LANGUAGE_GRACE_SECONDS,
      f"the watched time includes the intro ({r['watched_seconds']} s)")


print()
print("--- the boundary for the statistics follows from it ---")
# Were it left at two minutes, a three-minute playback would enter the
# statistics without a language - and "Neuvedeno" would grow where we only did
# not manage to learn the answer yet.
check(langstats.MIN_PLAY_SECONDS >= collector.LANGUAGE_GRACE_SECONDS
      + collector.MIN_LANGUAGE_SECONDS,
      f"only a playback whose language could be determined goes into the statistics "
      f"({langstats.MIN_PLAY_SECONDS} s)")


print()
print("--- the filter for a day computes the same local time as the listing ---")
# The stored time is always UTC. A day must be computed in the local time - and
# the same on both sides, otherwise the link to a day shows foreign records.
# The statistics are a package by topics (jellyscope/stats/) - it is read whole.
source = "\n".join(p.read_text(encoding="utf-8") for p in sorted((PROJECT / "jellyscope" / "stats").glob("*.py")))
check(source.count("date(p.started_at, 'localtime') = ?") == 1,
      "the history filters the day in the local time")
check("date(started_at, 'localtime')" in source,
      "the chart by days groups in the local time too")

# With PostgreSQL "local" follows the connection's zone - and we have to set it
# ourselves, otherwise SQL would compute by the database's server and Python by
# the application's server.
db_source = (PROJECT / "jellyscope" / "db.py").read_text(encoding="utf-8")
check("SET TIME ZONE" in db_source,
      "the connection to PostgreSQL gets the application's time zone")
check('os.environ.get("TZ"' in db_source, "and takes it from TZ, which the service sets")

# The zone's name is put into a string in SQL, so it must pass a check.
check("re.fullmatch" in db_source, "the zone's name is checked before it is put in")

with db.connect() as conn:
    conn.execute(
        """INSERT INTO playback (session_key, user_id, item_id, item_name,
                                 started_at, last_seen_at, watched_seconds, is_active)
           VALUES ('den-test', 'u1', 'film', 'Film',
                   '2026-08-11 22:30:00', '2026-08-11 23:00:00', 1800, 0)""")

# Whatever the server's zone, both sides must agree: the day the record falls
# into by the filter must match the day in its listed time.
day = db.query_one(
    "SELECT date(started_at, 'localtime') AS day FROM playback"
    " WHERE session_key = 'den-test'")["day"]
from jellyscope.formatting import datetime_human  # noqa: E402
listed = datetime_human("2026-08-11 22:30:00")
check(listed.startswith(f"{day[8:10]}.{day[5:7]}."),
      f"the day from the filter ({day}) matches the listed time ({listed})")


print()
print("--- the list of files without a language ---")
with db.connect() as conn:
    conn.execute("INSERT INTO libraries (id, name, synced_at) VALUES ('l','Filmy',?)",
                 (db.utcnow(),))
    for ident, langs, path in (
        ("ok", "cs,en", "/media/Matrix.mkv"),
        ("prazdne", None, "/media/Duna.mkv"),
        ("und", "und", "/media/Neznamy.mp4"),
        ("bez-textu", "", "/media/serial/S01E01.mkv"),
    ):
        conn.execute(
            """INSERT INTO items (id, name, type, library_id, audio_languages,
                                  path, size_bytes, is_missing, synced_at)
               VALUES (?,?,'Movie','l',?,?,3000000000,0,?)""",
            (ident, ident, langs, path, db.utcnow()),
        )

check(langstats.undefined_language_count() == 3,
      f"three files of four without a language ({langstats.undefined_language_count()})")
files = langstats.undefined_language_files(10, 0)
check({r["id"] for r in files} == {"prazdne", "und", "bez-textu"},
      "empty, 'und' and an empty string all count")
check(all(r["path"] for r in files), "each has the path to the file")
check(langstats.undefined_language_count(search="serial") == 1,
      "it can be searched by the path too")
check(langstats.undefined_language_count(search="Neznamy") == 1, "and by the name")

# Not only to count, but to list too. The list joins the libraries and they have
# a `name` column too, so an unprefixed condition was ambiguous - and the page
# failed with a 500 the moment somebody typed something into the search.
found = langstats.undefined_language_files(10, 0, search="Neznamy")
check([r["id"] for r in found] == ["und"],
      f"and the listing with a search passes too ({[r['id'] for r in found]})")
check([r["id"] for r in langstats.undefined_language_files(10, 0, search="Duna")]
      == ["prazdne"], "a search by the file name returns its row")
check([r["id"] for r in langstats.undefined_language_files(10, 0, search="serial")]
      == ["bez-textu"], "a search by the path returns a row too")
check(langstats.undefined_language_files(10, 0, search="Filmy") == [],
      "the library's name is not searched")

# Archived files do not belong here - they are no longer on the disk, there is nothing to fix.
with db.connect() as conn:
    conn.execute("UPDATE items SET is_missing = 1 WHERE id = 'und'")
check(langstats.undefined_language_count() == 2,
      f"archived ones are not offered for fixing ({langstats.undefined_language_count()})")


print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
