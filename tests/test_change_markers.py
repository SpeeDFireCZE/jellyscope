# -*- coding: utf-8 -*-
r"""Cards that refresh by themselves, the server's part: what changed, and what each card depends on.

An open page is to redraw a card once its data changed - without reloading the page and without
recomputing every card every few seconds. The server gives the page two things for that:

A) **the sources of every card** (`Card.sources`): what must change for the card to show something
   else. A card does not have to name them - the default follows from its area and its period (an
   app's card with a period counts the app's stored history, one without it shows the app's live
   state; a card over playback has a period, a library card mostly not). The few that mix two
   sources say so at the card. Every card of the registry is walked here and its sources are
   compared with a rule of this test, so a new card that ends up with a senseless source fails;
B) **the markers** in /health: one cheap answer with a value per source (except "live", which has
   no marker - the app's live state is fresh for a minute). A marker moves exactly when the data
   behind it moved: a play starts or ends (and once a minute while something plays), a task of its
   kind finishes, a manual "Sync now" in Settings ran. Nothing happening = nothing moves, and
   reading the markers writes nothing;
C) **the switch** Settings -> Interface -> Refreshing cards, on by default, in every page's context;
D) **History's bar**: History does not rewrite its table, it asks how many plays its filter finds more than
   it was drawn with (/history/new, the address the page writes) - by the page's own filter and rights, so
   the number is exactly what loading the page again adds: for an administrator, a reader without "who
   listened" and a viewer; with the switch off there is no bar.

Spec: docs/superpowers/specs/2026-11-26-live-cards-design.md (part A)

Run:
    .venv/bin/python tests/test_change_markers.py
"""
from __future__ import annotations

import asyncio
import dataclasses
import html
import json
import os
import re
import sys
import tempfile
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "change_markers.db")
os.environ["SECRET_KEY"] = "test-signing-key-that-is-long-enough-for-it"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

from fastapi.testclient import TestClient  # noqa: E402
from starlette.requests import Request  # noqa: E402

from _apps import connect_seerr, connect_sonarr, no_network, seerr_client, seerr_handler, sonarr_client, sonarr_handler  # noqa: E402
from _package import substitute  # noqa: E402
from jellyscope import access, accounts, cards, cards_base, collector, db, formatting, importers, purge, scanner, seerr, stats, web  # noqa: E402
from jellyscope.arr import sonarr  # noqa: E402
from jellyscope.web import base as web_base  # noqa: E402
from jellyscope.web import history as web_history  # noqa: E402
from jellyscope.web import status as web_status  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


no_network()      # no app is asked here unless the test fakes it

print("--- A) the sources of every card in the registry ---")
ARR_GROUPS = {"Sonarr", "Radarr", "Lidarr", "Readarr", "Bazarr", "Prowlarr"}
# The cards whose sources are written at the card, and why. Everything else follows the rule below.
EXCEPTIONS = {
    "now_playing": {"plays"},                              # a state without a period - of the plays running now
    "insight_unused": {"plays", "library"},                # files of the library nobody played
    "insight_quality": {"plays", "library"},               # watched a lot, in the library's poor quality
    "insight_large": {"plays", "library"},                 # the library's large files, barely played
    "insight_libraries": {"plays", "library"},             # the libraries' size against how much is watched
    "missing_track": {"plays", "library"},                 # watched titles whose library file lacks a track
    "music_unplayed": {"plays", "music"},                  # the music library against what is played
    "books_unread": {"plays", "books"},                    # the books library against what is read
    "seerr_latest": {"live", "seerr"},                     # read from Seerr live, the stored requests when it does not answer
    "seerr_who": {"seerr", "plays"},                       # who requested - and what they watched themselves
    "seerr_watching": {"seerr", "plays"},                  # requested titles: did anybody watch them?
    "seerr_nobody": {"seerr", "plays"},                    # requested titles nobody started
}


def expected(card: cards_base.Card) -> set[str]:
    """The sources by this test's own rule - by the card's group and area, not by the code's rule."""
    if card.key in EXCEPTIONS:
        return EXCEPTIONS[card.key]
    if card.group in ARR_GROUPS:
        return {"arr"} if card.uses_period else {"live"}
    if card.group == "Seerr":
        return {"seerr"} if card.uses_period else {"live"}
    if card.group == "Music":
        return {"plays"} if card.uses_period else {"music"}
    if card.group == "Books":
        return {"plays"} if card.uses_period else {"books"}
    if card.area == "library":
        return {"library"}
    return {"plays"} if card.uses_period else {"library"}


def kind_of(card: cards_base.Card) -> str:
    """The report's four kinds: live (an app now), plays, sync (one stored source), mixed (more sources)."""
    sources = set(card.sources)
    if len(sources) > 1:
        return "mixed"
    return {"live": "live", "plays": "plays"}.get(next(iter(sources), ""), "sync")


wrong = [(c.key, sorted(c.sources), sorted(expected(c))) for c in cards.ALL_CARDS if set(c.sources) != expected(c)]
check(not wrong, f"every card of the registry has the sources the rule expects ({len(wrong)} differ: {wrong[:6]})")
unknown = [(c.key, c.sources) for c in cards.ALL_CARDS if not c.sources or not set(c.sources) <= set(cards_base.SOURCES)]
check(not unknown, f"every card names at least one source, each a known one ({unknown[:6]})")
counts = Counter(kind_of(c) for c in cards.ALL_CARDS)
# The exploration's table (206 cards: 28 live, 48 plays, 123 sync, 7 mixed) recounted: five cards came since (the four
# Indexer reliability cards and Prowlarr's Indexer availability, all over the stored *Arr history), Latest requests
# reads Seerr live now and three Seerr cards say whether a requested title was watched - over plays too.
# A new card changes this - decide its sources, then the counts.
check(dict(counts) == {"live": 28, "plays": 48, "sync": 124, "mixed": 11} and len(cards.ALL_CARDS) == 211,
      f"211 cards: 28 live, 48 over plays, 124 over one stored source, 11 mixed ({len(cards.ALL_CARDS)}: {dict(counts)})")
by_group = Counter((c.group or "core", kind_of(c)) for c in cards.ALL_CARDS)
check(all(by_group[(app, "live")] == 5 for app in ("Sonarr", "Radarr", "Lidarr", "Readarr"))
      and by_group[("Bazarr", "live")] == 4 and by_group[("Prowlarr", "live")] == 3 and by_group[("Seerr", "live")] == 1,
      "live: five cards of each downloading app, four of Bazarr, three of Prowlarr, Waiting for approval of Seerr")
pointless = [k for k in EXCEPTIONS if set(cards_base.default_sources(cards.BY_KEY[k].area, cards.BY_KEY[k].uses_period))
             == EXCEPTIONS[k]]
check(not pointless, f"an exception is written only where the default would be wrong ({pointless})")
mixed = cards.BY_KEY["insight_unused"]
check(dataclasses.replace(mixed, width="half").sources == mixed.sources,
      "a card keeps its sources in a layout of another width (dataclasses.replace, cards.load_layout)")

sample = cards_base.Card("sample", "Sample", "", "_card.html", lambda o: {}, area="overview")
check(sample.sources == ("plays",), f"a card that names no sources gets the default ({sample.sources})")
try:
    cards_base.Card("sample", "Sample", "", "_card.html", lambda o: {}, area="overview", sources=("weather",))
    refused = False
except ValueError:
    refused = True
check(refused, "a card naming an unknown source is an error already when it is defined")

print("--- B) the markers in /health ---")
db.init_db()
db.set_setting(access.LOGIN_KEY, "1")
accounts.create("admin", "longpassword", is_admin=True)
accounts.create("reader", "longpassword")
accounts.create("viewer", "longpassword")
with db.connect() as conn:
    conn.execute("UPDATE accounts SET jellyfin_user_id = 'viewer-1' WHERE username = 'viewer'")
    conn.commit()
# The reader sees the overview, not Now playing; the viewer only the library (no card over anybody's plays).
accounts.save_permissions(accounts.get_by_name("reader")["id"], access.pack_permissions(["overview", "library"], False))
viewer_id = accounts.get_by_name("viewer")["id"]
accounts.save_permissions(viewer_id, access.pack_permissions(["library"], False))
db.forget_settings()


def sign_in(name: str) -> TestClient:
    client = TestClient(web.app)
    client.post("/login", data={"username": name, "password": "longpassword", "method": "local"})
    return client


admin, reader, viewer = sign_in("admin"), sign_in("reader"), sign_in("viewer")
MARKERS = {"plays", "library", "arr", "seerr", "music", "books"}


def markers(client: TestClient = admin) -> dict[str, str]:
    db.forget_settings()
    return client.get("/health").json().get("markers") or {}


def moved(before: dict[str, str], after: dict[str, str]) -> set[str]:
    return {key for key in MARKERS if before.get(key) != after.get(key)}


public = TestClient(web.app).get("/health").json()
check(set(public) == {"status", "started_at"}, f"signed out: only the state and the start, as before ({sorted(public)})")
answer = admin.get("/health").json()
check(set(answer.get("markers") or {}) == MARKERS, f"signed in: a marker per source ({sorted(answer.get('markers') or {})})")
check({"version", "collector", "last_poll", "active_sessions", "task_running", "stop_pending", "progress",
       "library_version", "tasks_version"} <= set(answer), f"/health's fields stay ({sorted(answer)})")
check(answer["markers"]["library"] == answer["library_version"], "the library's marker is the library's version")
first = markers()
check(markers() == first and markers() == first, "nothing happening: nothing moves")


def play(key: str, user: str, seen: str, active: int = 1) -> None:
    with db.connect() as conn:
        conn.execute("INSERT INTO playback (session_key, user_id, user_name, item_id, item_name, item_type, started_at,"
                     " last_seen_at, watched_seconds, is_active) VALUES (?, ?, ?, 'item-1', 'Film', 'Movie', ?, ?, 60, ?)",
                     (key, user, user, seen, seen, active))
        conn.commit()


def seen_at(key: str, seen: str, active: int = 1) -> None:
    with db.connect() as conn:
        conn.execute("UPDATE playback SET last_seen_at = ?, is_active = ?, ended_at = CASE WHEN ? = 0 THEN ? END"
                     " WHERE session_key = ?", (seen, active, active, seen, key))
        conn.commit()


print("- plays -")
before = markers()
play("s1", "user-a", "2026-11-25 12:00:10")
after = markers()
check(moved(before, after) == {"plays"}, f"a play starts: only the plays' marker moves ({sorted(moved(before, after))})")
seen_at("s1", "2026-11-25 12:00:40")
check(markers() == after, "the collector writes the play again within the same minute: nothing moves")
seen_at("s1", "2026-11-25 12:01:05")
next_minute = markers()
check(moved(after, next_minute) == {"plays"}, "while it plays, the next minute moves the plays' marker")
seen_at("s1", "2026-11-25 12:01:30", active=0)
ended = markers()
check(moved(next_minute, ended) == {"plays"}, "the play ends: the plays' marker moves")
check(markers() == ended, "nothing plays and nothing happens: nothing moves")
play("s2", "user-a", "2026-11-25 12:05:00", active=0)
check(moved(ended, markers()) == {"plays"}, "a play written whole at once (an import) moves it too")
check(re.fullmatch(r"[0-9a-f]{12}", markers()["plays"] or "") is not None,
      f"the plays' marker is opaque - it tells no count ({markers()['plays']})")


def paused(key: str, value: int = 1) -> None:
    with db.connect() as conn:
        conn.execute("UPDATE playback SET is_paused = ? WHERE session_key = ?", (value, key))
        conn.commit()


print("- a paused play -")
play("p1", "user-a", "2026-11-25 12:10:00")
paused("p1")
at_pause = markers()
# The collector writes a paused play again at every poll (its last_seen_at), with no watched time added.
seen_at("p1", "2026-11-25 12:11:05")
seen_at("p1", "2026-11-25 12:12:05")
check(markers() == at_pause, "a paused play: the collector writing it again in the next minutes moves nothing")
paused("p1", 0)
seen_at("p1", "2026-11-25 12:13:05")
playing_again = markers()
check(moved(at_pause, playing_again) == {"plays"}, "playing again: its minute moves the marker")
seen_at("p1", "2026-11-25 12:14:05", active=0)
check(moved(playing_again, markers()) == {"plays"}, "and its end moves it at once")

print("- tasks -")
for kind in ("arr", "seerr", "music", "books"):
    before = markers()
    scan_id = scanner.start_task_log(kind)
    running = markers()
    scanner.finish_task_log(scan_id, "ok")
    after = markers()
    check(running == before and moved(before, after) == {kind},
          f"a {kind} task: nothing moves while it runs, its marker when it finishes ({sorted(moved(before, after))})")
before = markers()
scanner.finish_task_log(scanner.start_task_log("recent"), "ok")
check(moved(before, markers()) == {"library"}, "a finished sync of the library moves the library's marker")
before = markers()
scanner.finish_task_log(scanner.start_task_log("tech"), "ok")
check(moved(before, markers()) == {"library"},
      "a finished analysis of the files (codecs, resolutions) moves the library's marker - it runs after the sync")
for kind in ("purge", "tidy", "import"):
    before = markers()
    scanner.finish_task_log(scanner.start_task_log(kind), "ok")
    check(moved(before, markers()) == {"plays"},
          f"a finished {kind} task (it deletes or rewrites plays) moves the plays' marker")
before = markers()
scanner.finish_task_log(scanner.start_task_log("notifications"), "ok")
check(markers() == before, "a task no card shows the result of moves nothing")

print("- Sync now in Settings -")
connect_sonarr()
restore = substitute(sonarr, "SonarrClient", sonarr_client(sonarr_handler()))
before = markers()
answer = admin.post("/settings/arr/sonarr/sync")
db.forget_settings()
check(answer.status_code == 200 and sonarr.sync_state()["result"] == "ok", "Sync now of Sonarr ran")
check(moved(before, markers()) == {"arr"}, f"Sync now of an *Arr app moves the arr marker ({sorted(moved(before, markers()))})")
restore()
connect_seerr()
restore = substitute(seerr, "SeerrClient", seerr_client(seerr_handler()))
before = markers()
answer = admin.post("/settings/seerr/sync")
db.forget_settings()
check(answer.status_code == 200 and seerr.sync_state()["result"] == "ok", "Sync now of Seerr ran")
check(moved(before, markers()) == {"seerr"}, f"Sync now of Seerr moves the seerr marker ({sorted(moved(before, markers()))})")
restore()
check(markers() == markers(), "and then nothing moves again")

print("- reading writes nothing -")


def database() -> list:
    return [db.query_all(f"SELECT * FROM {table} ORDER BY 1")
            for table in ("settings", "scan_log", "playback", "web_sessions", "accounts")]


# The sessions were used a minute ago: a session's use is written at most every five minutes
# (web_sessions.TOUCH_SECONDS), so a write of the time now - by the route or by a touch of the session - shows.
with db.connect() as conn:
    conn.execute("UPDATE web_sessions SET last_used_at = ?",
                 ((datetime.now(UTC) - timedelta(minutes=1)).strftime(db.TIME_FORMAT),))
    conn.commit()
stored = database()
answers = []
for client in (admin, reader, viewer):
    for _ in range(3):
        db.forget_settings()
        answers.append(client.get("/health").json().get("markers"))
check(all(answers) and database() == stored, "reading the markers writes nothing: /health asked again and again leaves the settings,"
      " the tasks, the plays, the sessions and the accounts as they were")

print("- rights -")
answer = reader.get("/health").json()
check("active_sessions" not in answer and set(answer.get("markers") or {}) == MARKERS,
      "without Now playing: the markers, not the number of plays")
check(set(TestClient(web.app).get("/health").json()) == {"status", "started_at"}, "signed out still nothing more")
before_viewer, before_reader = markers(viewer), markers(reader)
play("s3", "user-b", "2026-11-25 13:00:00")
check(markers(viewer) == before_viewer, "a viewer who sees no card over plays: another viewer's play does not move their marker")
check(moved(before_reader, markers(reader)) == {"plays"}, "a reader with the overview: anybody's play moves it")
play("s4", "viewer-1", "2026-11-25 13:00:20")
check(moved(before_viewer, markers(viewer)) == {"plays"}, "the viewer's own play moves it")
accounts.save_permissions(viewer_id, access.pack_permissions(["library", "overview"], False))
db.forget_settings()
before_viewer = markers(viewer)
play("s5", "user-b", "2026-11-25 13:01:00")
check(moved(before_viewer, markers(viewer)) == {"plays"}, "a viewer with the overview (everybody's plays): another's play moves it")

accounts.create("arr-reader", "longpassword")
accounts.save_permissions(accounts.get_by_name("arr-reader")["id"], access.pack_permissions(["arr"], False))
db.forget_settings()
arr_reader = sign_in("arr-reader")
before_arr, before_reader = markers(arr_reader), markers(reader)
play("s6", "user-b", "2026-11-25 13:02:00")
seen_at("s6", "2026-11-25 13:03:05")
after_start = markers(arr_reader)
seen_at("s6", "2026-11-25 13:04:05", active=0)
check(markers(arr_reader)["plays"] == after_start["plays"] == before_arr["plays"]
      and moved(before_reader, markers(reader)) == {"plays"},
      "a reader who sees no card over plays and not History (only *Arr): somebody's play starting, playing and"
      " ending does not move their plays' marker - it is not their business that somebody watches")
check(set(before_arr) == MARKERS, f"they still get every marker ({sorted(before_arr)})")

print("- plays deleted or rewritten -")
play("gone-1", "user-gone", "2026-11-25 13:10:00", active=0)
play("kept-1", "user-a", "2026-11-25 13:11:00", active=0)
before = markers()
purge.forget_user("user-gone")
check(moved(before, markers()) == {"plays"}, "forgetting a viewer (Settings) moves the plays' marker")
# A title renamed in the library: the tidy-up writes the new name into its plays.
with db.connect() as conn:
    conn.execute("INSERT INTO items (id, name, type, is_missing, synced_at) VALUES ('item-1', 'Renamed film', 'Movie', 0, ?)",
                 (db.utcnow(),))
    conn.commit()
before = markers()
asyncio.run(importers.tidy_data())
check(moved(before, markers()) == {"plays"}, "tidying the history (the button in Settings) moves the plays' marker")
before = markers()
asyncio.run(importers.tidy_data())
check(markers() == before, "a tidy-up that changes nothing moves nothing")
with db.connect() as conn:
    conn.execute("INSERT INTO items (id, name, type, tmdb_id, is_missing, synced_at) VALUES"
                 " ('old-copy', 'Film', 'Movie', '777', 0, ?)", (db.utcnow(),))
    conn.execute("INSERT INTO items (id, name, type, is_missing, synced_at) VALUES"
                 " ('deleted-film', 'Film', 'Movie', 0, ?)", (db.utcnow(),))
    conn.execute("UPDATE playback SET item_id = 'old-copy' WHERE session_key = 's1'")
    conn.execute("UPDATE playback SET item_id = 'deleted-film' WHERE session_key = 's2'")
    conn.commit()
before = markers()
check(scanner._merge_by_tmdb([(("777", -1, -1), "new-copy")]) == 1 and moved(before, markers()) == {"plays"},
      "a replaced file whose history moves to the new item (the library sync) moves the plays' marker")
before = markers()
check(stats.delete_item("deleted-film")["status"] == "ok" and moved(before, markers()) == {"plays", "library"},
      "deleting a title with its plays moves the plays' marker (and the library's: a title fewer)")
check(db.PLAYS_REWRITTEN_KEY in db.UNLOGGED_SETTINGS or any(db.PLAYS_REWRITTEN_KEY.startswith(p) for p in db.UNLOGGED_SETTINGS),
      "the mark of rewritten plays is not written into the log")
before = markers()
check(markers() == before, "and then nothing moves")

print("- the page carries the markers it was drawn with -")


def page_markers(client: TestClient, path: str) -> dict | None:
    found = re.search(r"data-markers='([^']*)'", client.get(path).text)
    return json.loads(html.unescape(found.group(1))) if found else None


check(page_markers(admin, "/video/overview?days=7") == markers(),
      "nothing changed since: the page carries the markers /health gives")
check(page_markers(viewer, "/library") == markers(viewer), "a viewer's page carries the viewer's own markers")
# A task finishes while the page computes its cards (a slow *Arr page, a sync ending at that moment): the page carries
# the markers from before its data, so its first question finds the task's marker moved.
original_data_for = cards.data_for


def data_for_while_a_task_ends(*args, **kwargs):
    data = original_data_for(*args, **kwargs)
    scanner.finish_task_log(scanner.start_task_log("arr"), "ok")
    return data


before = markers()
restore = substitute(cards, "data_for", data_for_while_a_task_ends)
try:
    drawn_with = page_markers(admin, "/video/overview?days=7")
finally:
    restore()
check(drawn_with == before and moved(drawn_with or {}, markers()) == {"arr"},
      f"a task ending while the page computes its data: the page carries the markers from before, so the change is"
      f" not lost ({sorted(moved(drawn_with or {}, markers()))})")

print("- the left panel -")
db.set_setting(collector.LAST_POLL_KEY, db.utcnow())
answer = admin.get("/health").json()
check(answer.get("last_poll_text") == formatting.relative_human(answer.get("last_poll")) and answer.get("last_poll_text"),
      f"the collector's last run as the left panel writes it ({answer.get('last_poll_text')!r})")

print("--- C) the switch Refreshing cards ---")
page = admin.get("/video/overview?days=7").text
bar = re.search(r'<div class="flash-toast" id="session-expired" hidden>\s*<div class="([^"]+)"', page)
check(bar is not None and "warning" not in bar.group(1).split() and "flash warning" not in page,
      f"the hidden bar of an expired sign-in has a class of its own: no check for a shown warning mistakes it for one"
      f" ({bar and bar.group(1)})")
request = Request({"type": "http", "method": "GET", "path": "/", "query_string": b"", "headers": [],
                   "session": {}, "app": web.app, "router": web.app.router})
check(web_base._context(request, None).get("ui_self_refresh") is True, "on by default, in every page's context")
page = admin.get("/settings?section=interface").text
check(re.search(r'name="ui_self_refresh" value="1"\s+checked', page) is not None
      and re.search(r'name="ui_self_refresh" value="0"\s*>', page) is not None,
      "Settings -> Interface: the switch, On chosen")
admin.post("/settings/interface", data={"ui_self_refresh": "0"}, follow_redirects=False)
db.forget_settings()
check(db.get_setting("ui_self_refresh", "") == "0" and web_base._context(request, None).get("ui_self_refresh") is False,
      "switched off: stored and in the context")
page = admin.get("/settings?section=interface").text
check(re.search(r'name="ui_self_refresh" value="0"\s+checked', page) is not None, "and the page shows Off")
answer = admin.get("/health").json()
check("markers" not in answer and {"version", "tasks_version", "library_version"} <= set(answer),
      f"switched off: /health gives no markers, the rest as before ({sorted(answer)})")
check(page_markers(admin, "/video/overview?days=7") is None, "switched off: the page carries no markers")
admin.post("/settings/interface", data={"ui_self_refresh": "1"}, follow_redirects=False)
db.forget_settings()
check(web_base._context(request, None).get("ui_self_refresh") is True, "and on again")

print("--- D) History's bar: how many plays more than the page was drawn with ---")
accounts.create("historian", "longpassword")
# A reader with History ("...including who watched it"), without "who listened" and "who read".
accounts.save_permissions(accounts.get_by_name("historian")["id"],
                          access.pack_permissions(["library", "library_who"], False))
db.forget_settings()
historian = sign_in("historian")


def history_play(key: str, user: str, name: str = "Kronika", item_type: str = "Movie", client: str = "Web") -> None:
    """A finished play an hour ago: the History of the last 7 days holds it."""
    with db.connect() as conn:
        conn.execute("INSERT INTO playback (session_key, user_id, user_name, item_id, item_name, item_type, client,"
                     " started_at, last_seen_at, watched_seconds, is_active) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 120, 0)",
                     (key, user, user, f"item-{key}", name, item_type, client, db.utcnow(), db.utcnow()))
        conn.commit()


def drawn(client: TestClient, query: str = "") -> tuple[int | None, str | None]:
    """History as a person opens it: the number of plays it shows, and the address its bar asks."""
    page = client.get("/history" + query).text
    total = re.search(r"(\d[\d,]*) records?\b", page)
    address = re.search(r'data-history-new="([^"]+)"', page)
    return (int(re.sub(r"\D", "", total.group(1))) if total else None,
            html.unescape(address.group(1)) if address else None)


def new_plays(client: TestClient, address: str | None) -> dict:
    answer = client.get(address or "/history/new", follow_redirects=False)
    return answer.json() if answer.status_code == 200 else {"status": answer.status_code}


print("- the count is what loading the page again adds -")
history_play("h0", "user-a", name="Earlier")      # the player "Web" is in the filter's offer before the page opens
QUERIES = ("", "?kind=movies&search=Kron&period=7", "?kind=music", "?client=Web")
before = {(name, query): drawn(client, query) for name, client in (("admin", admin), ("historian", historian),
                                                                   ("viewer", viewer)) for query in QUERIES}
check(all(address and address.startswith(f"/history/new?known={total}") for total, address in before.values()),
      f"the page writes the address of its bar with the number it shows ({list(before.values())[:2]})")
check(all(new_plays(client, before[(name, "")][1]) == {"count": 0, "text": ""}
          for name, client in (("admin", admin), ("historian", historian), ("viewer", viewer))),
      "nothing new: nought, and no text")
history_play("h1", "user-a")
history_play("h2", "user-b", name="Kronika 2", client="TV")
history_play("h3", "user-a", name="Song", item_type="Audio")
history_play("h4", "user-a", name="Kronika audiobook", item_type="AudioBook")
history_play("h5", "viewer-1", name="Own film")
history_play("h6", "user-a", name="Other film")
for (name, query), (total, address) in before.items():
    client = {"admin": admin, "historian": historian, "viewer": viewer}[name]
    after = drawn(client, query)[0]
    answer = new_plays(client, address)
    check(answer.get("count") == after - total,
          f"{name}, History{query or ' unfiltered'}: the count equals what the reload adds ({answer}, {total} -> {after})")
counted = {name: new_plays(client, before[(name, "")][1]).get("count")
           for name, client in (("admin", admin), ("historian", historian), ("viewer", viewer))}
check(counted == {"admin": 6, "historian": 4, "viewer": 1},
      f"an administrator counts all six, a reader without who listened and who read not the song and the audiobook,"
      f" a viewer only their own play ({counted})")
counted = {query: new_plays(admin, before[("admin", query)][1]).get("count") for query in QUERIES[1:]}
check(counted == {"?kind=movies&search=Kron&period=7": 2, "?kind=music": 1, "?client=Web": 5},
      f"the filters, the period and the search count too ({counted})")
check(new_plays(historian, before[("historian", "?kind=music")][1]).get("count") == 4,
      "a reader without who listened asking for music gets History's answer for it: everything else")
answer = new_plays(viewer, f"/history/new?known={before[('viewer', '')][0]}&user_id=user-a")
check(answer.get("count") == 1, f"a viewer counts only their own plays, whatever the address says ({answer})")
check(new_plays(admin, before[("admin", "")][1]).get("text") == "6 new plays"
      and new_plays(viewer, before[("viewer", "")][1]).get("text") == "1 new play",
      "the text in the reader's language, with the number's form")

print("- rows that leave the filter do not hide new plays -")
# A play that runs since before the page was drawn, still without watched time (History shows only what was watched).
with db.connect() as conn:
    conn.execute("INSERT INTO playback (session_key, user_id, user_name, item_id, item_name, item_type, started_at,"
                 " last_seen_at, watched_seconds, is_active) VALUES ('r0', 'user-a', 'user-a', 'item-r0', 'Retention',"
                 " 'Movie', ?, ?, 0, 1)", (db.utcnow(), db.utcnow()))
    conn.commit()
for key in ("r1", "r2", "r3"):
    history_play(key, "user-a", name="Retention")
total, address = drawn(admin, "?search=Retention")
newest = db.query_value("SELECT MAX(id) FROM playback WHERE session_key = 'r3'")
check(total == 3 and f"known_id={newest}" in (address or ""),
      f"the bar's address carries the newest play the page shows ({address})")
# The night's history retention deletes two of the shown plays, and one new play arrives.
with db.connect() as conn:
    conn.execute("DELETE FROM playback WHERE session_key IN ('r1', 'r2')")
    conn.commit()
history_play("r4", "user-a", name="Retention new")
after = drawn(admin, "?search=Retention")[0]
answer = new_plays(admin, address)
check(after == 2 and answer.get("count") == 1,
      f"two shown plays deleted and one new: the bar counts the new play the reload adds on top, not the"
      f" difference ({total} -> {after}, {answer})")
# The play running since before the page was drawn gets its watched time: older than the newest play shown,
# it shows after a reload all the same.
total, address = drawn(admin, "?search=Retention")
with db.connect() as conn:
    conn.execute("UPDATE playback SET watched_seconds = 120 WHERE session_key = 'r0'")
    conn.commit()
check(total == 2 and new_plays(admin, address).get("count") == 1,
      f"a play older than the newest shown that became visible since is counted too ({total}, {address})")
answer = new_plays(admin, "/history/new?known=0&search=Retention")
check(answer.get("count") == 3, f"an address without the newest play (a page drawn before this) counts as before ({answer})")

print("- rights -")
answer = TestClient(web.app).get("/history/new?known=0", follow_redirects=False)
check(answer.status_code in (302, 303, 307) and "count" not in answer.text,
      f"signed out: no count, only the way to sign in ({answer.status_code})")
check(new_plays(reader, "/history/new?known=0") == {"status": 403} and reader.get("/history").status_code == 403,
      "a reader without History: closed like History")
check(admin.get("/history/new?known=0").headers.get("Cache-Control") == "no-store", "not kept by the browser")

print("- cheap -")
web_history._FILTER_OPTIONS["at"] = None
new_plays(admin, before[("admin", "?kind=movies&search=Kron&period=7")][1])
check(web_history._FILTER_OPTIONS["at"] is None,
      "without a method, a player or a language chosen the count does not read the filter's offer (the whole history)")
new_plays(admin, before[("admin", "?client=Web")][1])
check(web_history._FILTER_OPTIONS["at"] is not None, "with a player chosen it checks it against the offer, as the page")

print("- the switch -")
check(drawn(admin)[1] is not None and 'id="history-new"' in admin.get("/history").text, "on: the page has the bar")
db.set_setting("ui_self_refresh", "0")
db.forget_settings()
check(drawn(admin)[1] is None and 'id="history-new"' not in admin.get("/history").text,
      "Refreshing cards off: no bar")
db.set_setting("ui_self_refresh", "1")
db.forget_settings()

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
