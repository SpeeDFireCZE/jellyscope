# -*- coding: utf-8 -*-
"""Imported history must not skew the language statistics.

Neither Jellystat nor the Playback Reporting plugin records the language of the
played track — they simply do not have that detail. Were the imported records
counted, they would add only a heap of "Neuvedeno" to the statistics and drown
out the real data from the collector.

And it is not just cosmetics: "60 % Czech" computed including records whose
language nobody knows is simply **a wrong number**.

The statistics over the `items` table ("what is available in the library") are
not affected — they have nothing to do with the import and must keep counting.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
# An own home: without it the stored choice of the database in
# data/database.json would override DATABASE_PATH and the test would run
# against the live database.
os.environ["JELLYSCOPE_HOME"] = str(_tmp)
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "lang.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic"

from jellyscope import db, langstats  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


db.init_db()

with db.connect() as conn:
    conn.execute(
        "INSERT INTO items (id, name, type, audio_languages, subtitle_languages,"
        " is_missing, synced_at) VALUES ('film-1','Matrix','Movie','cs,en','cs',0,?)",
        (db.utcnow(),),
    )
    conn.execute(
        "INSERT INTO items (id, name, type, audio_languages, is_missing, synced_at)"
        " VALUES ('film-2','Duna','Movie','en',0,?)",
        (db.utcnow(),),
    )


def insert(session_key: str, language: str | None, seconds: int = 3600,
           user: str = "u1", item: str = "film-1") -> None:
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO playback (session_key, user_id, user_name, item_id,
                                     item_name, item_type, audio_language,
                                     subtitle_language, started_at, last_seen_at,
                                     ended_at, watched_seconds, is_active)
               VALUES (?,?,'Karel',?,'Matrix','Movie',?,NULL,
                       datetime('now','-2 days'), datetime('now','-2 days'),
                       datetime('now','-2 days'), ?, 0)""",
            (session_key, user, item, language, seconds),
        )


print("--- our own collection: two hours in Czech, one in English ---")
insert("relace-a:film-1", "cs")
insert("relace-b:film-1", "cs")
insert("relace-c:film-1", "en")

colours = langstats.colour_map()
w = langstats.watched_languages(90, colours)
check(round(w["total_hours"]) == 3, f"three hours in total ({w['total_hours']})")
check(round(w["preferred_percent"]) == 67,
      f"Czech 67 % ({w['preferred_percent']:.0f})")
check(len(w["rows"]) == 2, f"two languages ({len(w['rows'])})")
check(langstats.imported_plays(90) == 0, "nothing from an import so far")

before_coverage = langstats.coverage()
before_subtitles = langstats.subtitle_usage(90)
before_users = langstats.languages_by_user(90, colours)
before_dubbing = langstats.dubbed_vs_original(90)
before_library = langstats.library_languages(colours)
before_combinations = langstats.language_combinations()
before_missing = langstats.missing_preferred(90, "cs")


print()
print("--- now 50 imported records without a language are added ---")
for i in range(50):
    insert(f"import:jst:{i}:film-1", None)

check(langstats.imported_plays(90) == 50,
      f"the page knows there are 50 of them ({langstats.imported_plays(90)})")

w = langstats.watched_languages(90, langstats.colour_map())
check(round(w["total_hours"]) == 3, f"the hours did not change ({w['total_hours']})")
check(round(w["preferred_percent"]) == 67,
      f"Czech still 67 % ({w['preferred_percent']:.0f})")
check(len(w["rows"]) == 2, f"no \"Neuvedeno\" was added ({len(w['rows'])} languages)")
check(all(row["code"] != "und" for row in w["rows"]),
      "there is no 'und' among the languages")


print()
print("--- the other statistics over playback too ---")
after = langstats.coverage()
check(after["plays_total"] == before_coverage["plays_total"],
      f"the coverage counts the same sample ({after['plays_total']})")
check(after["plays_percent"] == before_coverage["plays_percent"],
      f"and the same percentage ({after['plays_percent']:.0f} %)")
check(langstats.subtitle_usage(90) == before_subtitles, "subtitles unchanged")
check(langstats.languages_by_user(90, colours) == before_users,
      "the breakdown by users unchanged")
check(langstats.dubbed_vs_original(90) == before_dubbing, "dubbing/original unchanged")


print()
print("--- but what is in the library keeps counting ---")
# These overviews read the items table - they have nothing to do with the import.
check(langstats.library_languages(colours) == before_library,
      "the languages in the library unchanged")
check(langstats.language_combinations() == before_combinations,
      "the track combinations unchanged")
check(langstats.undefined_language_items() == 0, "there are 0 titles without a language")


print()
print("--- an import that CARRIES the language counts in the numbers ---")
# Some versions of Playback Reporting store the track's language and
# Jellyscope takes it over at import. Such a record is as good as one from our
# own collection - and the message above the charts must not claim it is missing.
insert("import:pbr:500:film-1", "cs")
insert("import:pbr:501:film-1", "cs")

check(langstats.imported_plays(90) == 50,
      f"50 stay uncounted, not 52 ({langstats.imported_plays(90)})")

w = langstats.watched_languages(90, langstats.colour_map())
check(round(w["total_hours"]) == 5, f"the hours grew by two ({w['total_hours']})")
check(round(w["preferred_percent"]) == 80,
      f"Czech 80 % - 4 of 5 hours ({w['preferred_percent']:.0f})")
check(all(row["code"] != "und" for row in w["rows"]),
      "and \"Neuvedeno\" still is not among the languages")

# For subtitles it does not hold: a record with an audio language says nothing
# about whether subtitles were on, so it would look like "without subtitles".
check(langstats.subtitle_usage(90) == before_subtitles,
      "for subtitles the import does not count even with an audio language")


print()
print("--- \"the Czech track is missing\" counts imported plays ---")
# Here we take the language from the library, not from the playback. An import
# is valid proof that somebody watched the title - throwing it away would be a pity.
insert("import:jst:900:film-2", None, item="film-2")
missing = langstats.missing_preferred(90, "cs")
names = {row["label"] for row in missing["rows"]}
check("Duna" in names, f"a title watched only by the import is in the list: {names}")
check(len(missing["rows"]) > len(before_missing["rows"]),
      "the list grew, the import was not thrown away")


print()
print("--- the filter really is in every query over playback ---")
import ast  # noqa: E402

source = (PROJECT / "jellyscope" / "langstats.py").read_text(encoding="utf-8")
tree = ast.parse(source)

# `ast.walk` visits the inner pieces of an f-string too. By themselves they
# look like a query without the filter, even though the whole has it - so we
# leave them out.
inside_fstring = {
    id(piece)
    for node in ast.walk(tree) if isinstance(node, ast.JoinedStr)
    for piece in node.values
}

for node in ast.walk(tree):
    if not isinstance(node, ast.FunctionDef) or node.name.startswith("_"):
        continue
    # missing_preferred and imported_plays are the exceptions explained above.
    if node.name in ("missing_preferred", "imported_plays", "library_language_options",
                     "preferred_language"):
        continue
    for sub in ast.walk(node):
        if isinstance(sub, ast.JoinedStr):
            text = "".join(c.value if isinstance(c, ast.Constant) else "{X}"
                           for c in sub.values)
        elif (isinstance(sub, ast.Constant) and isinstance(sub.value, str)
                and id(sub) not in inside_fstring):
            text = sub.value
        else:
            continue
        if "FROM playback" not in text:
            continue
        check("{X}" in text, f"{node.name}: the query over playback has the filter")


print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
