# -*- coding: utf-8 -*-
r"""What the file does not say about the language, Jellyfin fills in - but only that.

ffprobe reads a track's language from the file's metadata, and many files simply
have none there. Jellyfin often knows it though: it works it out from the file's name,
from the folder or from what it keeps about the title itself. An item's detail then reported
„Neuvedeno" for three audio tracks, while Jellyfin offered Czech and twice Slovak
for the same file.

Both tools were right - each only looked elsewhere.

That is why **only the gaps** are filled in. What ffprobe read stays; and when
the numbers of tracks do not match, nothing is filled in: a wrongly determined language is worse than
„neuvedeno", because with „neuvedeno" it is at least seen that it is not known.

Run:
    .venv/bin/python tests/test_language_from_jellyfin.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))
from _package import substitute  # noqa: E402 - a substitute in all the submodules of the scanner package

_tmp = tempfile.mkdtemp()
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "jazyky.db")
os.environ["SECRET_KEY"] = "testovaci-klic"

from jellyscope import db, scanner  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


db.init_db()
db.set_setting("ui_language", "cs")  # the checks read Czech (English is the default)


def put_item(item_id: str) -> None:
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO items (id, name, type, is_missing, synced_at)"
            " VALUES (?, ?, 'Movie', 0, '2026-01-01 00:00:00')",
            (item_id, f"Film {item_id}"))


def track(index: int, kind: str, language: str, **extra) -> dict:
    """A track the way ffprobe stores it."""
    return {"stream_index": index, "type": kind, "language": language,
            "codec": "ac3", "channels": 2, **extra}


def jellyfin_item(item_id: str, tracks: list[dict]) -> dict:
    """Jellyfin's answer - the tracks wrapped in MediaSources."""
    return {"Id": item_id, "MediaSources": [{"MediaStreams": tracks}]}


def jf(index: int, kind: str, language: str | None, **extra) -> dict:
    return {"Index": index, "Type": kind, "Language": language,
            "Codec": "ac3", "Channels": 2, **extra}


def languages_of(item_id: str) -> list[tuple]:
    return [(r["stream_index"], r["language"], r["language_source"])
            for r in db.query_all(
                "SELECT stream_index, language, language_source FROM item_streams"
                " WHERE item_id = ? ORDER BY stream_index", (item_id,))]


print("--- the file is silent, the library knows ---")
# Exactly the case from the screenshot: three audio tracks without a language, Jellyfin
# reports Czech and twice Slovak for them.
put_item("film1")
scanner.save_streams("film1", [
    track(0, "Video", "und"),
    track(1, "Audio", "und"),
    track(2, "Audio", "und"),
    track(3, "Audio", "und"),
])
filled = scanner._fill_item_languages(jellyfin_item("film1", [
    jf(0, "Video", None),
    jf(1, "Audio", "ces"),
    jf(2, "Audio", "slo"),
    jf(3, "Audio", "slo"),
]))
check(filled == 3, f"three tracks were filled in ({filled})")
check(languages_of("film1") == [(0, "und", None), (1, "cs", "jellyfin"),
                                (2, "sk", "jellyfin"), (3, "sk", "jellyfin")],
      f"and it can be told where it is from ({languages_of('film1')})")
# The picture stayed "neuvedeno" on purpose - nobody expects a language with video.

summary = db.query_one("SELECT audio_languages, default_audio_language FROM items"
                       " WHERE id = 'film1'")
check(summary["audio_languages"] == "cs,sk",
      f"the summary of languages was recounted ({summary['audio_languages']})")
check(summary["default_audio_language"] == "cs",
      f"the default language too ({summary['default_audio_language']})")

print()
print("--- what the file states is not overwritten ---")
# Here the file is the source of truth. Were Jellyfin to beat filled-in
# tracks too, the file analysis would stop making sense.
put_item("film2")
scanner.save_streams("film2", [track(1, "Audio", "cs"), track(2, "Audio", "und")])
filled = scanner._fill_item_languages(jellyfin_item("film2", [
    jf(1, "Audio", "eng"),
    jf(2, "Audio", "eng"),
]))
check(filled == 1, f"only the empty one is filled in ({filled})")
check(languages_of("film2") == [(1, "cs", None), (2, "en", "jellyfin")],
      f"the first track stayed Czech ({languages_of('film2')})")

print()
print("--- when the numbers do not match, rather nothing ---")
put_item("film3")
scanner.save_streams("film3", [track(1, "Audio", "und"), track(2, "Audio", "und")])
filled = scanner._fill_item_languages(jellyfin_item("film3", [
    jf(1, "Audio", "ces"),
]))
check(filled == 0, f"nothing is filled in ({filled})")
check([(i, j) for i, j, _ in languages_of("film3")] == [(1, "und"), (2, "und")],
      "the languages stayed unchanged")

print()
print("--- external subtitles do not count in the pairing ---")
# Jellyfin numbers the subtitles lying beside the file too. They are not in the file,
# so they would shift the order and the languages would land on the wrong tracks.
put_item("film4")
scanner.save_streams("film4", [track(1, "Subtitle", "und", codec="subrip")])
filled = scanner._fill_item_languages(jellyfin_item("film4", [
    jf(1, "Subtitle", "ces", Codec="subrip"),
    jf(2, "Subtitle", "eng", Codec="srt", IsExternal=True),
]))
check(filled == 1, f"the inner subtitles are paired ({filled})")
check(languages_of("film4") == [(1, "cs", "jellyfin")], f"and get Czech ({languages_of('film4')})")

print()
print("--- when not even Jellyfin knows, we do not make things up ---")
put_item("film5")
scanner.save_streams("film5", [track(1, "Audio", "und")])
filled = scanner._fill_item_languages(jellyfin_item("film5", [jf(1, "Audio", "und")]))
check(filled == 0, f"it stays neuvedeno ({filled})")
check(languages_of("film5") == [(1, "und", "unknown")],
      f"but it is noted that we asked ({languages_of('film5')})")

print()
print("--- we do not ask about the same a second time ---")
# The answer would be the same every time. By the mark it is recognised that it was already asked -
# and the asking does not grow with every further analysis.
not_asked = db.query_all(
    "SELECT DISTINCT item_id FROM item_streams WHERE language = 'und'"
    " AND language_source IS NULL AND type IN ('Audio', 'Subtitle')")
check("film5" not in [r["item_id"] for r in not_asked],
      "film5 is no longer among the not asked")
check("film3" not in [r["item_id"] for r in not_asked],
      "nor film3, whose numbers did not match")

# After a new measurement the tracks are overwritten with the mark too, so after fixing
# the metadata in Jellyfin we ask again.
scanner.save_streams("film5", [track(1, "Audio", "und")])
check(languages_of("film5") == [(1, "und", None)], "a new measurement deletes the mark")

print()
print("--- whom to ask at all ---")
check(scanner._missing_language([track(1, "Audio", "und")]) is True, "audio without a language yes")
check(scanner._missing_language([track(1, "Subtitle", "und")]) is True, "subtitles too")
check(scanner._missing_language([track(0, "Video", "und")]) is False, "video not")
check(scanner._missing_language([track(1, "Audio", "cs")]) is False, "filled-in audio not")

print()
print("--- a batch: one query for fifty items ---")
# We ask only after the analysis, because that way fifty items go in one
# query. In the middle of the loop it would be one query per file.
import asyncio  # noqa: E402


class FakeClient:
    """A Jellyfin that does not exist - it answers from a prepared dictionary."""

    queries: list[list[str]] = []

    def __init__(self, *a, **kw) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def items_by_ids(self, ids):
        FakeClient.queries.append(list(ids))
        return [jellyfin_item("film6", [jf(1, "Audio", "ces")]),
                jellyfin_item("film7", [jf(1, "Audio", "ger")])]


put_item("film6")
put_item("film7")
scanner.save_streams("film6", [track(1, "Audio", "und")])
scanner.save_streams("film7", [track(1, "Audio", "und")])

original = scanner.JellyfinClient
substitute(scanner, "JellyfinClient", FakeClient)
try:
    filled = asyncio.run(scanner.fill_languages_from_jellyfin(["film6", "film7"]))
finally:
    substitute(scanner, "JellyfinClient", original)

check(filled == 2, f"both items were filled in ({filled})")
check(FakeClient.queries == [["film6", "film7"]],
      f"and one query was enough for it ({FakeClient.queries})")
check(languages_of("film6") == [(1, "cs", "jellyfin")], "film6 has Czech")
check(languages_of("film7") == [(1, "de", "jellyfin")], "film7 German")
check(asyncio.run(scanner.fill_languages_from_jellyfin([])) == 0,
      "without items we do not call Jellyfin at all")

print()
print("--- the page shows that the detail is not from the file ---")
from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, web  # noqa: E402

accounts.create("viewer", "longpassword", is_admin=True)
client = TestClient(web.app)
client.post("/login", data={"username": "viewer", "password": "longpassword"},
            follow_redirects=False)
page = client.get("/item/film1").text
check("z Jellyfinu" in page, "a filled-in track has a note about its origin")
# Twice per track: once in the label, once in the bubble on hovering with the mouse.
count = page.count('Soubor jazyk neuvádí')
check(count == 3, f"for all three tracks ({count})")
not_filled = client.get("/item/film5").text
check("z Jellyfinu" not in not_filled, "a track not filled in has no note")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
