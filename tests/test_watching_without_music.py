# -*- coding: utf-8 -*-
r"""The watching numbers do not count music and books; the History and the traffic count everything.

Listening to music is a row of the playback history (`playback`) of the kind
`Audio`; listening to an audiobook (`AudioBook`) and reading (`Book`) are rows of their own kinds
and watching does not count them either. Whoever has music on all day would otherwise outweigh all watching on
the Overview, in the viewers' ranking and in the Comparison. Guarded:

A) the watcher: every function (and statement at module level) with a query over
   the history either leaves music and books out (`watching_only`), picks only music
   (`only_music`) or only books (`only_books`), follows the kind (`_kind_condition`), or is in the listed
   "counts everything" set with a reason. A new query without a decision does not
   pass the test; an entry of the set that has no query any more does not either;
B) behaviour: the watching statistics over the demo data give the same before
   and after listening is added (different viewers, apps, transcoding);
C) kinds in the History: "all" (`all`) has music too, "music" only it, "other"
   and watching (`both`) do not have it; the history listing in the API still
   shows everything.

Spec: docs/superpowers/specs/2026-11-15-hudba-design.md

Run:
    .venv/bin/python tests/test_watching_without_music.py
"""
from __future__ import annotations

import ast
import os
from datetime import UTC, datetime, timedelta
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parent.parent
PACKAGE = PROJECT / "jellyscope"
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "sledovani.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


print("--- A) the watcher: every query over the history made a decision ---")
QUERY = re.compile(r"\b(FROM|JOIN)\s+playback\b")
# by_titles: a sum by items that is paired with items (movies and episodes) - a track pairs with nothing in it.
DECISIONS = ("watching_only(", "only_music(", "only_books(", "_kind_condition(", "by_titles(")
# Modules (or single functions `module:function`) that count everything on purpose, with a reason.
COUNTS_ALL = {
    "collector.py": "the collector writes and resumes sessions - all kinds",
    "db.py": "managing the tables (the bin of the forgotten)",
    "demodata.py": "the demo data",
    "importers/": "importing and fixing imported history",
    "music/sync.py": "imported history without a kind with a track is marked as listening",
    "books/sync.py": "imported history without a kind with a book is marked as listening / reading",
    "purge.py": "clearing out the history and forgetting a viewer concern everything",
    "access.py": "the order of devices for anonymising",
    "scanner/": "merging and archiving the library's items",
    "stats/play_history.py": "History: the kind is chosen by a person (`kind`)",
    "web/history.py:_filter_options": "History: the highest row number tells a new play of any kind came",
    "stats/locations.py": "where who connects from - traffic is traffic",
    "stats/network.py": "the network - traffic is traffic",
    "stats/playback.py": "playing right now",
    "stats/item_detail.py": "the plays of one library item",
    "images.py:known_tags": "whether an id is being played right now (any kind) - an image of it may then be fetched",
    # The base of the stats package imports nothing of the package (test_stats_package), so it cannot use the condition.
    # The start of "the whole history" is the first record of anything; the Music page has its own start.
    "stats/periods.py:first_record": "since when there is any history - the start of \"the whole history\"",
    "stats/seerr.py": "watching a requested title from the library",
}


def places_with_query() -> dict[str, str]:
    """{'module.py:function': source} for every function and module statement with a query over the history."""
    places: dict[str, str] = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        if not QUERY.search(text):
            continue
        rel = path.relative_to(PACKAGE).as_posix()
        tree = ast.parse(text)
        functions = [u for u in ast.walk(tree) if isinstance(u, (ast.FunctionDef, ast.AsyncFunctionDef))]
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            source = ast.get_source_segment(text, node) or ""
            if QUERY.search(source):
                target = node.targets[0] if isinstance(node, ast.Assign) else None
                places[f"{rel}:{getattr(target, 'id', node.lineno)}"] = source
        for f in functions:
            nested = [g for g in functions if g is not f and f.lineno < g.lineno <= g.end_lineno <= f.end_lineno]
            source = ast.get_source_segment(text, f) or ""
            for g in nested:                       # a query in a nested function belongs to it
                source = source.replace(ast.get_source_segment(text, g) or "", "")
            if QUERY.search(source):
                places[f"{rel}:{f.name}"] = source
    return places


def exception_for(place: str) -> str | None:
    path = place.split(":", 1)[0]
    for key in COUNTS_ALL:
        if place == key or path == key or (key.endswith("/") and path.startswith(key)):
            return key
    return None


PLACES = places_with_query()
undecided = sorted(m for m, source in PLACES.items() if exception_for(m) is None and not any(r in source for r in DECISIONS))
check(len(PLACES) > 80, f"the watcher finds the queries ({len(PLACES)} places)")
check(not undecided, f"every query over the history leaves music out, picks it, follows the kind, or is in the "
                     f"\"counts everything\" set ({len(undecided)}: {', '.join(undecided[:8])}{' …' if len(undecided) > 8 else ''})")
dead = sorted(k for k in COUNTS_ALL if not any(exception_for(m) == k for m in PLACES))
check(not dead, f"the \"counts everything\" set has no entry without a query ({dead})")
# The watcher itself: a query without a decision in a stats function would have to be caught.
trial = {"stats/overview_page.py:zkouska": "SELECT COUNT(*) FROM playback WHERE watched_seconds > 60"}
check(all(exception_for(m) is None and not any(r in z for r in DECISIONS) for m, z in trial.items()),
      "the watcher would report a query without a decision in a stats module")

print()
print("--- B) the watching statistics: the same with listening and without it ---")
from jellyscope import db, demodata, insights, langstats, stats  # noqa: E402

db.init_db()
demodata.seed()
# Since 15. 11. 2026 the demo data have music too. Here "without listening × with listening"
# is compared, so it starts without it.
with db.connect() as conn:
    conn.execute("DELETE FROM playback WHERE item_type = 'Audio'")
    conn.commit()
USERS = [r["id"] for r in db.query_all("SELECT id FROM users ORDER BY id")]
LIBRARIES = [r["id"] for r in db.query_all("SELECT id FROM libraries ORDER BY id")]


def results() -> dict[str, Any]:
    colours = langstats.colour_map()
    v: dict[str, Any] = {"prvni_zaznam": stats.first_record(), "coverage": langstats.coverage(), "barvy": colours,
                         "imported_plays": langstats.imported_plays()}
    for days in (1, 7, 30, 365, 0):
        for name in ("overview", "previous_overview", "others_breakdown", "daily_activity_split", "play_method_breakdown",
                     "client_breakdown", "hourly_heatmap", "top_users"):
            v[f"{name}({days})"] = getattr(stats, name)(days)
        for kind in ("both", "movies", "series"):
            v[f"top_items({days},{kind})"] = stats.top_items(days, limit=50, kind=kind)
        for user in USERS:
            v[f"user_detail({user},{days})"] = stats.user_detail(user, days)
            v[f"user_genres({user},{days})"] = stats.user_genres(user, days)
        for library in LIBRARIES:
            v[f"library_activity({library},{days})"] = stats.library_activity(library, days)
        for name in ("transcode_speeds", "transcode_offenders", "transcode_reasons", "upgrade_candidates",
                     "oversized_rarely_watched", "storage_efficiency", "never_finished"):
            v[f"insights.{name}({days})"] = getattr(insights, name)(days)
        v[f"dead_storage({days})"] = insights.dead_storage(days=days or 365)
        for name in ("short_plays", "subtitle_usage", "dubbed_vs_original"):
            v[f"langstats.{name}({days})"] = getattr(langstats, name)(days)
        v[f"imported_plays({days})"] = langstats.imported_plays(days)
        v[f"watched_languages({days})"] = langstats.watched_languages(days, colours)
        v[f"languages_by_user({days})"] = langstats.languages_by_user(days, colours)
        v[f"missing_preferred({days})"] = langstats.missing_preferred(days, "cs")
    return v


before = results()

# The pages (Overview, Users, a viewer, Comparison, the API summary) read before any book is added; the numbers of
# the music listening on the viewer's page are its own, so the pages are compared only for books.
from _apps import login  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from jellyscope import accounts, api  # noqa: E402
from jellyscope.web import app as web_app  # noqa: E402

accounts.create("admin", "longpassword", is_admin=True)
client = login("admin")
API_HEADER = {"Authorization": f"Bearer {api.create_token('books-test')['token']}"}
PAGES = ("/?days=30", "/users", f"/users/{USERS[0]}?days=30", "/video/compare")


def page_content(text: str) -> str:
    """The page without what is not its content: the charts' gradient names (a counter numbers them) and the markers the
    page's refresher starts from (data-markers: the plays' marker moves with any play, music and books too)."""
    return re.sub(r"data-markers='[^']*'", "", re.sub(r"gradient-[\w-]+", "gradient", re.sub(r"\s+", " ", text)))


def pages_snapshot() -> dict[str, Any]:
    snap: dict[str, Any] = {path: page_content(client.get(path).text) for path in PAGES}
    snap["api summary"] = TestClient(web_app).get("/api/v1/summary?days=30", headers=API_HEADER).json()
    return snap


# The demo has audiobook listening and reading of its own (books, 23. 11. 2026) - it is in the history already.
DEMO_BOOK_ROWS = db.query_value("SELECT COUNT(*) FROM playback WHERE item_type IN ('AudioBook', 'Book')")
pages_before = pages_snapshot()
check(all(pages_before[p] for p in PAGES), "the pages answer before the listening")
with db.connect() as conn:
    # An audiobook and a read book: same shapes, other kinds. Watching must not count them (books in the Books section).
    for i, (who, kind, seconds, hours_ago) in enumerate(((0, "AudioBook", 5400, 5), (1, "AudioBook", 90, 50), (2, "Book", 1800, 9),
                                                         (0, "Book", 600, 100))):
        when = (datetime.now(UTC) - timedelta(hours=hours_ago)).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name, item_type, client, device_name, device_id,"
            " play_method, audio_codec, audio_language, media_runtime_ticks, started_at, last_seen_at, ended_at,"
            " watched_seconds, paused_seconds, is_paused, is_active, remote_address)"
            " VALUES (?, ?, 'čtenář', ?, ?, ?, 'Jellyfin Web', 'Telefon', 'dev-knihy', 'DirectPlay', 'aac', 'en', 36000000000,"
            " ?, ?, ?, ?, 0, 0, 0, '10.0.0.8')",
            (f"kniha-{i}", USERS[who], f"kniha-{i}", f"Kniha {i}", kind, when, when, when, seconds))
    conn.commit()
pages_after = pages_snapshot()
for page in pages_before:
    check(pages_before[page] == pages_after[page], f"{page} is the same with the audiobooks and the reading in the history")

LISTENING = [  # viewer (index), app, play method, transcode reason, hours ago, seconds
    (0, "Finamp", "DirectPlay", None, 2, 240), (0, "Finamp", "DirectPlay", None, 3, 25),
    (1, "Jellyfin Web", "Transcode", "AudioCodecNotSupported", 30, 200), (1, "Jellyfin Web", "DirectStream", None, 80, 7200),
    (2, "Feishin", "DirectPlay", None, 24 * 20, 3600), (-1, "Finamp", "DirectPlay", None, 24 * 200, 180),
]
with db.connect() as conn:
    for i, (who, app, method, reason, hours_ago, seconds) in enumerate(LISTENING):
        when = (datetime.now(UTC) - timedelta(hours=hours_ago)).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name, item_type, client, device_name, device_id,"
            " play_method, transcode_reasons, audio_codec, audio_language, media_runtime_ticks, started_at, last_seen_at, ended_at,"
            " watched_seconds, paused_seconds, is_paused, is_active, remote_address)"
            " VALUES (?, ?, 'posluchač', ?, ?, 'Audio', ?, 'Telefon', 'dev-hudba', ?, ?, 'flac', 'en', 2400000000, ?, ?, ?, ?, 0, 0, 0, '10.0.0.9')",
            (f"hudba-{i}", USERS[who], f"skladba-{i}", f"Skladba {i}", app, method, reason, when, when, when, seconds))
    conn.commit()
check(db.query_value("SELECT COUNT(*) FROM playback WHERE item_type = 'Audio'") == len(LISTENING), "the listening is in the history")
BOOK_ROWS = DEMO_BOOK_ROWS + 4
check(db.query_value("SELECT COUNT(*) FROM playback WHERE item_type IN ('AudioBook', 'Book')") == BOOK_ROWS,
      "the audiobook and the reading are in the history")
after = results()
different = sorted(k for k in before if before[k] != after[k])
check(not different, f"the watching statistics are the same with listening and books as without them ({len(different)} different: {different[:6]})")

print()
print("--- C) kinds in the History and in the API ---")
music_count = len(LISTENING)
all_count = stats.history_count(kind=stats.KIND_ALL) if hasattr(stats, "KIND_ALL") else -1
watching = stats.history_count(kind=stats.KIND_BOTH)
check(all_count == watching + music_count + BOOK_ROWS,
      f"History \"all\" has the listening and the books too ({all_count} = {watching} + {music_count} + {BOOK_ROWS})")
check(getattr(stats, "KIND_MUSIC", None) == "music" and stats.history_count(kind="music") == music_count
      and all(r["item_type"] == "Audio" for r in stats.history(limit=50, kind="music")), "History \"music\" has only listening")
others = stats.history(limit=500, kind=stats.KIND_OTHER)
check(not [r for r in others if r["item_type"] in ("AudioBook", "Book")] and not [r for r in stats.history(limit=500, kind=stats.KIND_BOTH)
                                                                                    if r["item_type"] in ("AudioBook", "Book")],
      "neither \"other\" nor watching contains books")
check(getattr(stats, "KIND_BOOKS", None) == "books" and stats.history_count(kind="books") == BOOK_ROWS
      and {r["item_type"] for r in stats.history(limit=BOOK_ROWS + 50, kind="books")} == {"AudioBook", "Book"}
      and len(stats.history(limit=BOOK_ROWS + 50, kind="books")) == BOOK_ROWS,
      "History \"books\" has only the audiobooks and the reading")
check(not [r for r in stats.history(limit=500, kind="music") if r["item_type"] != "Audio"], "History \"music\" has no books")
check(not [r for r in others if r["item_type"] == "Audio"] and not [r for r in stats.history(limit=500, kind=stats.KIND_BOTH)
                                                                if r["item_type"] == "Audio"],
      "neither \"other\" nor watching contains listening")
check(not any("Hudba" in str(r) for r in stats.others_breakdown(0)), "the breakdown of \"Other\" does not name music")
check(getattr(stats, "HISTORY_KINDS", None) == ("all", "movies", "series", "music", "books", "other"), "the History kinds in the switch's order")
api_rows = [r for batch in stats.history_for_api(kind=getattr(stats, "KIND_ALL", "both")) for r in batch]
check(sum(r["item_type"] == "Audio" for r in api_rows) == music_count
      and sum(r["item_type"] in ("AudioBook", "Book") for r in api_rows) == BOOK_ROWS,
      "the history listing in the API shows the listening and the books too")

# Pages: the History switch and the link from a day on the Overview (the list must show the same numbers that were clicked).
history_html = client.get("/history").text
check('<option value="music"' in history_html and '<option value="all">' in history_html and "Skladba 0" in history_html,
      "the History without a choice shows the listening too and the switch has \"Hudba\"")
music_only_html = client.get("/history?kind=music").text
books_html = client.get("/history?kind=books").text
# (Kniha 2, not Kniha 3: the demo's own readings of the last days fill the page, the one 100 hours ago is on the next.)
check("Kniha 0" in books_html and "Kniha 2" in books_html and "Skladba 0" not in books_html,
      "History \"books\" shows the audiobooks and the reading, no music")
check('<option value="books"' in history_html and "Kniha 0" in history_html, "the History switch has \"Books\" and \"all\" shows books too")
check("Kniha" not in music_only_html, "History \"music\" shows no books")
check("Skladba 0" in music_only_html and "Skladba 5" in music_only_html and music_only_html.count('<tr') <= music_count + 2,
      "History \"music\" shows the listening")
overview_html = client.get("/?days=30&kind=both").text
day_links = re.findall(r'href="(/history\?day=[0-9-]+[^"]*)"', overview_html)
check(bool(day_links) and all("kind=both" in link for link in day_links), f"the link from a day on the Overview carries the kind for watching too ({day_links[:2]})")
listening_day = db.query_value("SELECT date(started_at, 'localtime') FROM playback WHERE item_type = 'Audio' ORDER BY started_at DESC LIMIT 1")
check("Skladba" not in client.get(f"/history?day={listening_day}&kind=both").text
      and "Skladba" in client.get(f"/history?day={listening_day}").text,
      "the link with watching does not show listening, the History without a choice does")

print()
print("--- D) the sums by titles stay on the covering index ---")
# Review of 15. 11. 2026: the music condition in the sums by items (which are then paired with items)
# took the covering index from the queries - SQLite then read the table for every play, about ten times
# slower (see the project memory, measured on 150 000 plays). A track is not in items, the condition is not needed there.
from jellyscope import stats as _stats  # noqa: E402

if db.current_dialect() == "sqlite":
    now = db.utcnow()
    for name, sql, params in (("insights._PLAYED_IN_PERIOD", insights._PLAYED_IN_PERIOD, ("2000-01-01 00:00:00", now)),
                              ("insights._LAST_PLAYED", insights._LAST_PLAYED, ()),
                              ("stats._ALL_PLAYS", _stats._ALL_PLAYS, ()),
                              ("stats._PLAYS", "SELECT * FROM items i" + _stats._PLAYS, ())):
        plan = " | ".join(str(r["detail"]) for r in db.query_all("EXPLAIN QUERY PLAN " + sql, params))
        playback = [part for part in plan.split(" | ") if "playback" in part]
        check(bool(playback) and all("COVERING INDEX" in part for part in playback), f"{name} reads the history only from the covering index ({playback})")
else:
    print("(the query plan is checked only on SQLite)")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
