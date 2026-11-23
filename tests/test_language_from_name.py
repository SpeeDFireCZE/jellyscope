# -*- coding: utf-8 -*-
r"""The last rescue: the language by the file's name.

When neither the file (ffprobe) nor the library (Jellyfin) knows the language, what is left is what
a person wrote into the name - "Duna.2021.CZ.SK.EN.1080p.mkv".

It all stands and falls with precision, so **a whole piece between
separators** is looked for, not an occurrence of letters: neither "Czechacek" nor "enigma" thus passes.
Two-letter marks are taken only in capitals (lower-case "de", "es", "ja" are
common words) and whole names of languages only after the year or an episode's number - otherwise
"The Italian Job" would be in Italian and "Polish Wedding" in Polish.

But the name says only WHICH languages are in the file, not which track is
which. They are therefore assigned in order and marked as a guess; the set of languages -
and with it the statistics - is right, the order is a guess. And when the numbers do not match or
a known track says something else, nothing is filled in.

Run:
    .venv/bin/python tests/test_language_from_name.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "nazvy.db")
os.environ["SECRET_KEY"] = "testovaci-klic"

from jellyscope import db, languages, scanner  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


db.init_db()
db.set_setting("ui_language", "cs")  # the checks read Czech (English is the default)

print("--- what is to be found in a name ---")
FINDS = [
    ("Duna.2021.CZ.SK.EN.1080p.BluRay.x264.mkv", ["cs", "sk", "en"], []),
    ("Uncharted (2022) CZ dabing.mkv", ["cs"], []),
    ("Serial.S01E03.CZdab.720p.mkv", ["cs"], []),
    ("Matrix.1999.ENG.CZ.tit.mkv", ["en"], ["cs"]),
    ("Parasite.2019.KOR.EN.sub.mkv", ["ko"], ["en"]),
    ("Amelie.2001.FRA.CZ.mkv", ["fr", "cs"], []),
    ("Show.S02E01.1080p.WEB-DL.GER.mkv", ["de"], []),
    ("Nejaky.film.2020.[CZ][SK].mkv", ["cs", "sk"], []),
    ("/mnt/filmy/Film.2018.1080p.multi.CZ.SK.mkv", ["cs", "sk"], []),
    # Shapes from a real library - all of them came here as reports that
    # the language was not assigned. Lower case after the year, a mark glued to the year
    # and a year hidden in brackets.
    ("Nejaky film (2004) HD cz.avi", ["cs"], []),
    ("Nejaky film (2004) HD en.avi", ["en"], []),
    ("Nejaky film 1080p - 5.1 CZ.mkv", ["cs"], []),
    ("Nejaky film (1964)(CZ)[TvRip].mp4", ["cs"], []),
    ("Film.2022.CZ.SK.EN.WebRip.1080p.HEVC.C4U.mkv", ["cs", "sk", "en"], []),
    ("Film.2002.DVDRip.XviD.AC3-2.0.CZ.avi", ["cs"], []),
    ("Nejaky film-2003CZ.mp4", ["cs"], []),
    ("Film.1080p.BDRip.x264.CZ.dabing.mkv", ["cs"], []),
    ("Film.2019.DVDRip.eng.avi", ["en"], []),
    ("Serial.S01E02.HDTV.sk.mp4", ["sk"], []),
]
for name, audio, subtitles in FINDS:
    v = languages.from_name(name)
    check(v["audio"] == audio and v["subtitles"] == subtitles,
          f"{name[:44]:46} {v['audio']} / {v['subtitles']}")

print()
print("--- and what must NOT be found ---")
# This is the core of the whole thing. Were a substring looked for, everything
# would fall through here: "Czechacek" has "cze" in it, "enigma" has "en".
NOT_FOUND = [
    "Czechacek.2020.1080p.mkv",              # contains "cze"
    "Enigma.2001.1080p.BluRay.mkv",          # contains "en"
    "IT.2017.1080p.BluRay.x264.mkv",         # the movie IT, not Italian
    "No.Time.To.Die.2021.2160p.mkv",         # "No" is not Norwegian
    "El.Camino.A.Breaking.Bad.Movie.2019.mkv",   # "El" is not Greek
    "Ja.Olga.Hepnarova.2016.1080p.mkv",      # "Ja" is not Japanese
    "Casa.de.Papel.S01E01.1080p.mkv",        # lower-case "de" = a Spanish word
    "The.Italian.Job.2003.1080p.mkv",        # a language's name in a movie's name
    "The.French.Connection.1971.1080p.mkv",
    "Polish.Wedding.1998.720p.mkv",
    "Russian.Doll.S01E01.1080p.mkv",
    "The Italian Job.mkv",                   # without a year: we do not accept words
    "Sk8er.Boi.2002.mkv",
    "Frozen.2013.1080p.DUAL.mkv",            # "DUAL" does not say which languages
    "Denis.Villeneuve.dokument.2020.mkv",
    # A release group at the end of the name. "C4U" must neither fall apart into
    # "C" + "4U" nor be caught as a mark.
    "Film.2019.1080p.x264.C4U.mkv",
    # A lower-case "cz" BEFORE the year is part of the name, not a mark.
    "cz.film.o.nicem.2019.1080p.mkv",
]
for name in NOT_FOUND:
    v = languages.from_name(name)
    check(not v["audio"] and not v["subtitles"], f"{name[:44]:46} {v['audio']} / {v['subtitles']}")

print()
print("--- splitting into tracks ---")


def put_item(item_id: str, path: str) -> None:
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO items (id, name, type, is_missing, synced_at, path)"
            " VALUES (?, ?, 'Movie', 0, '2026-01-01 00:00:00', ?)",
            (item_id, item_id, path))


def put_tracks(item_id: str, *codes: str, kind: str = "Audio") -> None:
    scanner.save_streams(item_id, [
        {"stream_index": i + 1, "type": kind, "language": j, "codec": "ac3"}
        for i, j in enumerate(codes)])


def track_languages(item_id: str) -> list[tuple]:
    return [(r["language"], r["language_source"]) for r in db.query_all(
        "SELECT language, language_source FROM item_streams WHERE item_id = ?"
        " ORDER BY stream_index", (item_id,))]


# The case from the screenshot: three tracks without a language, the name promises three languages.
put_item("f1", "/data/Duna.2021.CZ.SK.EN.1080p.mkv")
put_tracks("f1", "und", "und", "und")
check(scanner.fill_languages_from_name(["f1"]) == 1, "the name helped the item")
check(track_languages("f1") == [("cs", "name"), ("sk", "name"), ("en", "name")],
      f"in order and marked as a guess ({track_languages('f1')})")
summary = db.query_one("SELECT audio_languages FROM items WHERE id = 'f1'")
check(summary["audio_languages"] == "cs,en,sk",
      f"the summary for the statistics is right ({summary['audio_languages']})")

print()
print("--- the name names fewer languages than there are tracks ---")
# The most frequent case from real libraries: the file has a dub
# and the original audio, but the name has only "CZ". Which of the two tracks is
# Czech cannot be guessed - but that Czech IS in the file, we know for sure.
put_item("f2", "/data/Film (2004) HD cz.avi")
put_tracks("f2", "und", "und")
check(scanner.fill_languages_from_name(["f2"]) > 0, "something was got out of the name")
check(track_languages("f2") == [("und", None), ("und", None)],
      f"nothing was assigned to the tracks ({track_languages('f2')})")
summary = db.query_one("SELECT audio_languages, audio_from_name FROM items WHERE id = 'f2'")
check(summary["audio_from_name"] == "cs", f"but Czech is written down ({summary['audio_from_name']})")
check(summary["audio_languages"] == "cs,und",
      f"and the statistics see it beside the unknown track ({summary['audio_languages']})")

print()
print("--- what we already know is subtracted from the name ---")
# Tracks [English, unknown] and the name "CZ.EN": we know English, so
# the name adds a single news - and there is a single unknown track where it belongs.
put_item("f3", "/data/Film.2020.CZ.EN.1080p.mkv")
put_tracks("f3", "en", "und")
check(scanner.fill_languages_from_name(["f3"]) == 1, "one track is filled in")
check(track_languages("f3") == [("en", None), ("und", None)][:1] + [("cs", "name")],
      f"the unknown track got Czech ({track_languages('f3')})")

print()
print("--- four tracks, three marks: nothing to the tracks ---")
put_item("f7", "/data/Film.2020.CZ.SK.EN.1080p.mkv")
put_tracks("f7", "und", "und", "und", "und")
scanner.fill_languages_from_name(["f7"])
check(track_languages("f7") == [("und", None)] * 4, "the tracks stayed unchanged")
summary = db.query_one("SELECT audio_languages FROM items WHERE id = 'f7'")
check(summary["audio_languages"] == "cs,en,sk,und",
      f"but the statistics know all three languages ({summary['audio_languages']})")

print()
print("--- a known track agrees: the rest is filled in ---")
put_item("f4", "/data/Film.2020.CZ.EN.1080p.mkv")
put_tracks("f4", "cs", "und")
check(scanner.fill_languages_from_name(["f4"]) == 1, "only the unknown one is filled in")
check(track_languages("f4") == [("cs", None), ("en", "name")],
      f"and what ffprobe read stays without a mark ({track_languages('f4')})")

print()
print("--- subtitles do not get mixed up with audio ---")
put_item("f5", "/data/Matrix.1999.ENG.CZ.tit.mkv")
scanner.save_streams("f5", [
    {"stream_index": 1, "type": "Audio", "language": "und", "codec": "ac3"},
    {"stream_index": 2, "type": "Subtitle", "language": "und", "codec": "subrip"},
])
check(scanner.fill_languages_from_name(["f5"]) == 1, "both get their own")
check(track_languages("f5") == [("en", "name"), ("cs", "name")],
      f"audio in English, subtitles in Czech ({track_languages('f5')})")

print()
print("--- without a path to the file nothing is guessed ---")
put_item("f6", "")
put_tracks("f6", "und")
check(scanner.fill_languages_from_name(["f6"]) == 0, "nothing to read, nothing to fill in")

print()
print("--- the page shows that it is a guess ---")
# This is half of the whole feature: a detail from the name is not a detail from the file and must not
# look like one. That is why the track says "odhad z názvu"; what ffprobe read
# says nothing.
from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, web  # noqa: E402

accounts.create("viewer", "longpassword", is_admin=True)
client = TestClient(web.app)
client.post("/login", data={"username": "viewer", "password": "longpassword"},
            follow_redirects=False)

page = client.get("/item/f1").text
check("odhad z názvu" in page, "a guessed track has a note")
check(page.count("Jazyk nezná soubor ani Jellyfin") == 3,
      f"for all three tracks ({page.count('Jazyk nezná soubor ani Jellyfin')})")

# An item for which nothing was filled in stays without a note.
page7 = client.get("/item/f7").text
check("odhad z názvu" not in page7,
      "where nothing was assigned to the tracks, the tracks have no note")
# On the other hand the card's header must show what the name promised - otherwise
# the statistics would know a language that is nowhere on the page.
check("podle názvu souboru" in page7, "but the card says what the name promised")
check("Čeština" in page7 and "Slovenština" in page7,
      "and lists them")

# And a track read from the file has no note even on an item where elsewhere
# it was guessed - otherwise the mark would lose its sense.
mixed = client.get("/item/f4").text
check(mixed.count("Jazyk nezná soubor ani Jellyfin") == 1,
      f"only by the guessed one, not by both ({mixed.count('Jazyk nezná soubor ani Jellyfin')})")

print()
print("--- shapes from a real library ---")
# Ten names from a live library. They are not made up: they differ by the separator
# before the mark (a dot, a dash, a space, a bracket, an underscore, a tilde),
# by letter case and by what follows the mark.
for name, expected in (
    ("Vratne lahve 2022.CZ.SK.EN.WebRip.1080p.HEVC.C4U.mkv", ["cs", "sk", "en"]),
    ("Vratne lahve_ (2004) HD cz.avi", ["cs"]),
    ("Vratne lahve (Nejlepší Kvalita) CZ.avi", ["cs"]),
    ("Vratne lahve 1080p - 5.1 CZ.mkv", ["cs"]),
    ("Vratne lahve (1964)(CZ)[TvRip].mp4", ["cs"]),
    ("Vratne lahve ~ (2015) HD cz.avi", ["cs"]),
    ("Vratne lahve.2002.DVDRip.XviD.AC3-2.0.CZ.avi", ["cs"]),
    ("Vratne lahve-2003CZ.mp4", ["cs"]),
    ("Vratne lahve-1990-1080p-cz.mp4", ["cs"]),
    ("Vratne lahve.2015.THEATRiCAL.1080p.BDRip.x264.CZ.dabing.mkv", ["cs"]),
):
    found = languages.from_name(name)["audio"]
    check(found == expected,
          f"{name[13:]:<48} -> {found or 'nothing'}")

print()
print("--- the files must get to the 'from the name' step at all ---")
# This was the bug because of which it did not work in live use:
# the step above marks the tracks Jellyfin did not help with as "unknown" -
# and the search for candidates asked only for tracks WITHOUT a mark. The list was
# therefore always empty and the file's name was not used at all; it worked
# only at a moment when Jellyfin did not answer.
with db.connect() as conn:
    conn.execute("INSERT INTO libraries (id, name) VALUES ('kn1','Filmy')"
                 " ON CONFLICT(id) DO NOTHING")
    # a) a track about which Jellyfin was already asked and did not know
    conn.execute(
        "INSERT INTO items (id, name, type, library_id, is_missing, path)"
        " VALUES ('kand1','Film','Movie','kn1',0,"
        "'/media/Film.2002.DVDRip.XviD.AC3-2.0.CZ.avi')")
    conn.execute("INSERT INTO item_streams (item_id, stream_index, type, language,"
                 " language_source) VALUES ('kand1', 1, 'Audio', 'und', 'unknown')")
    # b) an item without a single track - the file analysis never succeeded
    conn.execute(
        "INSERT INTO items (id, name, type, library_id, is_missing, path)"
        " VALUES ('kand2','Film2','Movie','kn1',0,'/media/Film2-1990-1080p-cz.mp4')")
    # c) what knows its language must not get into the list
    conn.execute(
        "INSERT INTO items (id, name, type, library_id, is_missing, path,"
        " audio_languages) VALUES ('kand3','Film3','Movie','kn1',0,"
        "'/media/Film3.CZ.mkv','cs')")
    conn.commit()

candidates = scanner.candidates_for_language_from_name()
check("kand1" in candidates, "a track marked 'unknown' is a candidate")
check("kand2" in candidates, "an item without tracks too")
check("kand3" not in candidates, "and what knows its language is not added")

check(scanner.fill_languages_from_name(["kand1", "kand2"]) == 2, "both get the language filled in")
first = db.query_one("SELECT language, language_source FROM item_streams"
                     " WHERE item_id = 'kand1'")
check(first["language"] == "cs" and first["language_source"] == "name",
      "the track has Czech from the name")
second = db.query_one("SELECT audio_languages, audio_from_name FROM items"
                      " WHERE id = 'kand2'")
check((second["audio_languages"] or second["audio_from_name"]) == "cs",
      "the item without tracks has Czech at least in the summary")

# The second time there is nothing to do - otherwise it would be counted over and over at every
# analysis of the library.
check("kand2" not in scanner.candidates_for_language_from_name(),
      "a finished item is not offered a second time")

# The scope: refreshing one item must not go through the whole library.
check(scanner.candidates_for_language_from_name(item_ids=["kand1"]) == ["kand1"]
      or scanner.candidates_for_language_from_name(item_ids=["kand1"]) == [],
      "the limit to an item is kept")
check("kand1" not in scanner.candidates_for_language_from_name(library_id="jina"),
      "and the limit to a library too")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
