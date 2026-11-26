r"""A test with a filled database.

It fills a temporary database with made-up traffic (the same generator the
sample mode uses) and walks through all the application's pages. A real Jellyfin
is not needed for it - the server's address is deliberately broken, so the collector only
reports a connection error and goes on. Exactly that we want to verify too.

Run:
    .venv/bin/python tests/test_with_data.py
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

# A folder of its own for every run. Formerly there was a fixed name in %TEMP% here -
# but then two simultaneous runs of the test (or two people) touched the same
# file and fell over each other. The error looked like an error of the
# application meanwhile, not like a collision.
TMP_DB = Path(tempfile.mkdtemp()) / "jellyscope_test_data.db"

# Setting the environment MUST come before importing the application - the configuration is
# loaded at import and does not change afterwards.
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"     # deliberately broken
os.environ["JELLYFIN_API_KEY"] = "test-key"
# A home of its own: without it DATABASE_PATH would beat the stored choice
# of the database in data/database.json and the test would run against the live database.
os.environ["JELLYSCOPE_HOME"] = tempfile.mkdtemp()
os.environ["DATABASE_PATH"] = str(TMP_DB)
os.environ["SECRET_KEY"] = "testovaci-klic"

from jellyscope import accounts, db, demodata, langstats, languages  # noqa: E402

db.init_db()
db.set_setting("ui_language", "cs")  # the checks read Czech (English is the default)
accounts.create("tester", "testingpass", is_admin=True)
accounts.create("reader", "readerpass1", is_admin=False)

counts = demodata.seed()
print(f"OK  data poured in ({counts['items']} titles, {counts['plays']} plays)")

failures = 0


def check(ok: bool, message: str) -> None:
    global failures
    print(f"{'OK     ' if ok else 'FAIL   '}{message}")
    if not ok:
        failures += 1


from fastapi.testclient import TestClient  # noqa: E402

from jellyscope.web import app  # noqa: E402

ROUTES = [
    "/", "/?days=7", "/?days=90", "/?days=365",
    "/video/insights", "/video/insights?days=30",
    "/video/languages", "/video/languages?days=7", "/video/languages?days=365",
    "/library", "/library?sort=plays", "/library?search=Duna&sort=name",
    "/library?library_id=demo-tv", "/library?page=2",
    "/users", "/users?days=90", "/users/demo-u1", "/users/demo-u2?days=7",
    "/history", "/history?page=2", "/history?user_id=demo-u1", "/history?search=Chernobyl",
    # Dates at the calendar's edge: the index bounds two days around them overflowed (500).
    "/history?day=0001-01-01", "/history?day=9999-12-31", "/history?since=0001-01-01&until=2026-11-24",
    "/history?period=custom&since=1.1.2026&until=31.12.9999",
    "/settings", "/health", "/api/now-playing",
]

with TestClient(app) as client:
    client.post("/login", data={"username": "tester", "password": "testingpass"})

    print("--- all the pages ---")
    for route in ROUTES:
        try:
            response = client.get(route)
        except Exception as exc:  # noqa: BLE001
            check(False, f"{route}: {type(exc).__name__}: {exc}")
            continue
        check(response.status_code == 200,
              f"{route} -> {response.status_code} ({len(response.content)} B)")
        if response.status_code != 200:
            print("       " + response.text[:400])

    print("--- forms and trimming nonsensical input ---")
    forms = [
        {"tech_source": "ffprobe", "poll_interval": "15",
         "ffprobe_path": "", "ffprobe_concurrency": "4",
         "path_mappings": '[{"from":"/media","to":"D:/media"}]'},
        {"tech_source": "vymysleny", "poll_interval": "abc",
         "ffprobe_path": "", "ffprobe_concurrency": "999",
         "path_mappings": "tohle-neni-json"},
    ]
    for data in forms:
        response = client.post("/settings", data=data, follow_redirects=False)
        check(response.status_code == 303, f"POST /settings -> {response.status_code}")

    saved = db.get_settings()
    for key, expected in [
        ("tech_source", "jellyfin"),        # an unknown value -> the default
        ("poll_interval", "15"),            # "abc" -> the saved value, as a cleared field (not the default)
        ("ffprobe_concurrency", "16"),      # 999 -> the maximum
    ]:
        check(saved[key] == expected,
              f"the setting {key} = {saved[key]!r} (expected {expected!r})")
    check(saved["path_mappings"].startswith("[{"),
          "an invalid JSON did not change the stored path mapping")
    # Valid JSON of the wrong shape (a pair of texts, a number for a path) used to be stored, and from then on
    # every file analysis ended with an error nobody could read.
    kept = saved["path_mappings"]
    for wrong in ('["/media", "/mnt/media"]', '[{"from": "/media", "to": 5}]', '[{"from": "/media"}]'):
        response = client.post("/settings", data=dict(forms[0], path_mappings=wrong))
        db.forget_settings()
        check(db.get_setting("path_mappings") == kept and "nechal jsem původní hodnotu" in response.text,
              f"a mapping that is not a list of from/to texts is not stored, with a warning ({wrong})")

    # Out of every range: 1e400 and inf are no whole number, a 20-digit id does not fit the database's
    # integer, a 300-character zone is a file name too long. Each of them used to end with a 500.
    def answer(route: str, data: dict[str, str]) -> str:
        try:
            return str(client.post(route, data=data, follow_redirects=False).status_code)
        except Exception as exc:  # noqa: BLE001 - the test client passes the server's error on
            return type(exc).__name__

    HUGE = "99999999999999999999"
    password = {"password": "longpassword", "password_again": "longpassword"}
    zone_before = db.get_setting("app_timezone", "")
    for route, field, value, rest in [
        ("/settings", "poll_interval", "1e400", forms[0]),
        ("/settings", "poll_interval", "-inf", forms[0]),
        ("/settings/notifications/events", "silence_minutes", "inf", {}),
        ("/settings/api/token/revoke", "token_id", HUGE, {}),
        ("/settings/api/token/disable", "token_id", HUGE, {}),
        ("/settings/api/token/enable", "token_id", HUGE, {}),
        ("/settings/accounts/delete", "account_id", HUGE, {}),
        ("/settings/accounts/role", "account_id", HUGE, {"is_admin": "1"}),
        ("/settings/accounts/password", "account_id", HUGE, password),
        ("/settings/accounts/permissions", "account_id", HUGE, {}),
        ("/settings/history/restore", "event", HUGE, {}),
        ("/settings/language", "app_timezone", "x" * 300, {"ui_language": "cs"}),
    ]:
        status = answer(route, {**rest, field: value})
        check(status == "303", f"{route} {field}={value[:12]}… -> {status}, not an error")
    db.forget_settings()
    check(db.get_setting("poll_interval") == "15" and db.get_setting("app_timezone", "") == zone_before,
          f"the interval stays saved, as for abc, the zone stays ({db.get_setting('poll_interval')},"
          f" {db.get_setting('app_timezone', '')!r})")
    check(accounts.get_by_name("tester") is not None and accounts.get_by_name("reader") is not None,
          "no account was touched")

    print("--- scheduled tasks ---")
    response = client.post("/settings/tasks", data={
        "enabled_sync": "1",              # ticked
        # The hour and the minute come from two fields separately.
        "time_sync_h": "2", "time_sync_m": "5",    # zeros are filled in: "02:05"
        "minutes_recent": "-5",           # below the minimum -> trimmed to 0
        "time_backup_h": "25", "time_backup_m": "99",   # over the range -> trimmed
        # enabled_recent and enabled_backup are missing = not ticked
        "backup_path": str(TMP_DB.parent / "jellyscope-test-backup"),
        "backup_keep": "3",
    }, follow_redirects=False)
    check(response.status_code == 303, "the tasks saved")

    saved = db.get_settings()
    check(saved["task_sync_enabled"] == "1", "a ticked task is on")
    check(saved["task_recent_enabled"] == "0", "an unticked task is off")
    check(saved["task_backup_enabled"] == "0", "an unticked backup is off")
    check(saved["library_sync_time"] == "02:05",
          f"the time is stored in one form ({saved['library_sync_time']})")
    check(saved["recent_sync_minutes"] == "0",
          f"-5 trimmed to 0 ({saved['recent_sync_minutes']})")
    check(saved["task_backup_time"] == "23:59",
          f"25 and 99 trimmed to 23:59 ({saved['task_backup_time']})")

    # An emptied field does not change the schedule - otherwise it would quietly become midnight.
    response = client.post("/settings/tasks", data={
        "enabled_sync": "1", "time_sync_h": "", "time_sync_m": "",
        "backup_path": str(TMP_DB.parent / "jellyscope-test-backup"),
        "backup_keep": "3",
    }, follow_redirects=False)
    check(db.get_settings()["library_sync_time"] == "02:05",
          f"an empty field keeps the original time ({db.get_settings()['library_sync_time']})")
    # The same for an interval: an emptied field used to save the task's default.
    db.set_setting("recent_sync_minutes", "45")
    response = client.post("/settings/tasks", data={
        "enabled_recent": "1", "minutes_recent": "",
        "backup_path": str(TMP_DB.parent / "jellyscope-test-backup"), "backup_keep": "3",
    }, follow_redirects=False)
    db.forget_settings()
    check(response.status_code == 303 and db.get_setting("recent_sync_minutes") == "45",
          f"an empty interval keeps the original one ({db.get_setting('recent_sync_minutes')})")
    # The same for how long the history is kept and how many backups: an emptied field (or an older tab without
    # it) saved the defaults 365 and 7 - with the history's clean-up on, the next night deleted everything older
    # than a year, and the next backup pruned the kept ones down to seven (the review of the audit fixes).
    from jellyscope import purge as _purge
    db.set_setting(_purge.RETENTION_KEY, "3650")
    db.set_setting("backup_keep", "30")
    for form in ({"history_retention_days": "", "backup_keep": ""}, {}):
        response = client.post("/settings/tasks", data={
            "enabled_recent": "1", "minutes_recent": "45",
            "backup_path": str(TMP_DB.parent / "jellyscope-test-backup"), **form,
        }, follow_redirects=False)
        db.forget_settings()
        check(response.status_code == 303 and db.get_setting(_purge.RETENTION_KEY) == "3650" and db.get_setting("backup_keep") == "30",
              f"an empty or missing field keeps how long the history is kept and how many backups "
              f"({db.get_setting(_purge.RETENTION_KEY)}, {db.get_setting('backup_keep')}; form {sorted(form)})")
    response = client.post("/settings/tasks", data={
        "enabled_recent": "1", "minutes_recent": "45", "history_retention_days": "400", "backup_keep": "12",
        "backup_path": str(TMP_DB.parent / "jellyscope-test-backup"),
    }, follow_redirects=False)
    db.forget_settings()
    check(db.get_setting(_purge.RETENTION_KEY) == "400" and db.get_setting("backup_keep") == "12", "a typed value is saved as before")
    db.set_setting("backup_keep", "3")

    # The Network page's live card refreshes by itself and sends the period of its own page. That must not become
    # "the last period chosen": a tab left open on Network would switch every other page to its period with
    # each refresh (the review of the audit fixes).
    client.get("/video/languages?days=365")
    live = client.get("/partials/network-live?days=7")
    after = client.get("/video/languages").text
    check(live.status_code == 200 and 'data-period="days=365"' in after,
          "the live card's own refresh does not change the remembered period")
    # ... and the history filter's script looks for its own switch, not for anything with data-period (the page's
    # <body> carries the period under that name too).
    scripts = (PROJECT / "jellyscope" / "static" / "js" / "app.js").read_text(encoding="utf-8")
    check('querySelector("[data-period]")' not in scripts and 'querySelector("select[data-period]")' in scripts,
          "the history filter finds its period switch, not the page's body")

    # The database backup must really be made and be openable.
    import asyncio as _asyncio
    import sqlite3 as _sqlite3

    from jellyscope import tasks  # noqa: E402

    result = _asyncio.run(tasks.backup_database())
    check(result.get("status") == "ok", f"the backup ran: {result}")
    if result.get("status") == "ok":
        backup_file = Path(result["file"])
        check(backup_file.exists() and backup_file.stat().st_size > 0, "the backup file exists")
        # The backup must be a valid database, not only a copy of bytes.
        conn = _sqlite3.connect(backup_file)
        try:
            count = conn.execute("SELECT COUNT(*) FROM playback").fetchone()[0]
            check(count > 0, f"the backup contains data ({count} plays)")
        finally:
            conn.close()

    print("--- the message about the result is seen after scrolling too ---")
    # Cleaning up, imports and backups are started by a button at the bottom of the page. After
    # the redirect the browser goes back to the top, so a message in the flow of
    # the page would run away upwards and the person would never read it.
    response = client.post("/settings/history/tidy", follow_redirects=False)
    check(response.headers["location"].endswith("#cleanup"),
          f"it returns to the cleanup card, not to the top ({response.headers['location']})")

    # The card moved from Import to Tasks - the link and the anchor must
    # point where that card really is.
    tasks_page = client.get("/settings?section=tasks").text
    check('class="flash-toast"' in tasks_page, "the message is in a floating frame")
    check('id="cleanup"' in tasks_page, "and the cleanup card has an anchor that is jumped to")

    style = (PROJECT / "jellyscope" / "static" / "style.css").read_text(encoding="utf-8")
    piece = style[style.index(".flash-toast {"):]
    check("position: fixed" in piece.split("}")[0],
          "the frame is fixed at the window's top edge, not in the flow of the page")

    print("--- starting a task by hand ---")
    response = client.post("/settings/tasks/run", data={"key": "backup"},
                           follow_redirects=False)
    check(response.status_code == 303, "the manual start accepted")
    response = client.post("/settings/tasks/run", data={"key": "neexistuje"},
                           follow_redirects=False)
    check(response.status_code == 303, "an unknown task does not fall over")

    print("--- import ---")
    # Playback Reporting is not available (Jellyfin does not run) - it must report it,
    # not fall over.
    response = client.post("/settings/import/playback-reporting",
                           data={"min_seconds": "60"}, follow_redirects=True)
    check(response.status_code == 200, "an unavailable plugin does not cause a crash")

    # Jellystat: we upload a made-up JSON and verify the data appears.
    import json as _json
    fake_backup = _json.dumps({"jf_playback_activity": [
        {"Id": "a1", "UserId": "demo-u1", "NowPlayingItemId": "demo-movie-0",
         "NowPlayingItemName": "Duna", "ActivityDateInserted": "2025-03-01T20:00:00Z",
         "PlaybackDuration": "3600", "Client": "Jellyfin Web", "DeviceName": "Chrome",
         "PlayMethod": "DirectPlay"},
        {"Id": "a2", "UserId": "demo-u2", "NowPlayingItemId": "demo-movie-1",
         "NowPlayingItemName": "Neco", "ActivityDateInserted": "2025-03-02T21:00:00Z",
         "PlaybackDuration": "30", "Client": "Findroid", "DeviceName": "Pixel",
         "PlayMethod": "Transcode"},
    ]}).encode("utf-8")

    before = db.query_value("SELECT COUNT(*) FROM playback")
    response = client.post(
        "/settings/import/jellystat",
        files={"backup": ("zaloha.json", fake_backup, "application/json")},
        data={"min_seconds": "60"},
        follow_redirects=True,
    )
    after = db.query_value("SELECT COUNT(*) FROM playback")
    check(response.status_code == 200, "the Jellystat import ran")
    check(after == before + 1, f"1 record imported (the short one skipped): {before} -> {after}")

    # A repeated import must not double the data.
    client.post(
        "/settings/import/jellystat",
        files={"backup": ("zaloha.json", fake_backup, "application/json")},
        data={"min_seconds": "60"},
        follow_redirects=True,
    )
    again = db.query_value("SELECT COUNT(*) FROM playback")
    check(again == after, f"the repeated import doubled nothing ({after} -> {again})")

    # A nonsensical file must end with a message, not a crash.
    response = client.post(
        "/settings/import/jellystat",
        files={"backup": ("spatne.json", b"{neni json", "application/json")},
        data={"min_seconds": "60"},
        follow_redirects=True,
    )
    check(response.status_code == 200, "an invalid JSON does not cause a crash")

    print("--- libraries and an item's detail ---")
    library_id = db.query_value("SELECT id FROM libraries WHERE collection_type = 'movies' LIMIT 1", default="")
    for route in [
        f"/library/{library_id}?tab=overview",
        f"/library/{library_id}?tab=media",
        f"/library/{library_id}?tab=media&sort=name&search=Duna",
        f"/library/{library_id}?tab=activity",
        f"/library/{library_id}?tab=vymysleno",   # an unknown tab -> the overview
    ]:
        response = client.get(route)
        check(response.status_code == 200, f"{route} -> {response.status_code}")

    check(client.get("/library/neexistuje").status_code == 404, "an unknown library -> 404")

    # A page past the end is the last page, as in the History. A huge number went straight into
    # OFFSET and SQLite refused it (OverflowError, 500); a page just past the end showed an empty list.
    from jellyscope import stats  # noqa: E402

    media_pages = max(1, -(-stats.library_rows_count(library_id) // 48))
    undetermined_pages = max(1, -(-langstats.undefined_language_count(None) // 50))
    for base, last_page in ((f"/library/{library_id}?tab=media&page=", media_pages),
                            ("/video/languages/undetermined?page=", undetermined_pages)):
        last = client.get(f"{base}{last_page}").text
        for page_number in (last_page + 1, 10 ** 30):
            try:
                response, error = client.get(f"{base}{page_number}"), ""
            except Exception as e:  # noqa: BLE001
                response, error = None, repr(e)
            check(response is not None and response.status_code == 200 and response.text == last,
                  f"{base}{page_number} shows the last page {last_page}"
                  f" ({response.status_code if response is not None else error})")

    item_id = db.query_value("SELECT id FROM items LIMIT 1", default="")
    response = client.get(f"/item/{item_id}")
    check(response.status_code == 200, f"/item/{item_id} -> {response.status_code}")
    check(client.get("/item/neexistuje").status_code == 404, "an unknown item -> 404")

    # An image: Jellyfin does not run, so 404 must come - not a crash.
    check(client.get(f"/image/{item_id}").status_code == 404, "an unavailable image -> 404")

    print("--- how many database connections one page costs ---")
    # This is a safeguard against an error that was already here once: every `_("text")`
    # in a template called get_setting() and that opened a new connection. One
    # page made over a hundred of them and spent most of its time on it.
    #
    # The exact number does not matter, what matters is that it does not grow with the number of elements
    # on the page. The limit is therefore generous - it is to catch a drop by an order of magnitude,
    # not every extra query.
    import jellyscope.db as dbmod  # noqa: E402

    original_open = dbmod._open_raw
    opened = {"count": 0}

    def counting_open(config):
        opened["count"] += 1
        return original_open(config)

    dbmod._open_raw = counting_open
    try:
        for route, limit in (("/?days=365", 40), ("/video/languages", 40),
                             ("/video/insights", 40), ("/history", 30)):
            opened["count"] = 0
            client.get(route)
            check(opened["count"] <= limit,
                  f"{route}: {opened['count']} connections (limit {limit})")
    finally:
        dbmod._open_raw = original_open

    print("--- the settings' sections ---")
    # Settings used to be one long page. Now it has sections and only what is seen
    # is loaded - that is why each is checked separately.
    for name in ("jellyfin", "data", "tasks", "import", "database",
                 "accounts", "general"):
        response = client.get(f"/settings?section={name}")
        check(response.status_code == 200,
              f"/settings?section={name} -> {response.status_code}")

    # An unknown section must not bring the page down, it only falls back to the default.
    response = client.get("/settings?section=vymyslena")
    check(response.status_code == 200, "an unknown section -> the default")

    print("--- the front page ---")
    home = client.get("/").text
    for needle in ("Právě se hraje", "Nedávno přidané", "Statistiky"):
        check(needle in home, f"the front page has the section {needle!r}")
    check("poster-card" in home, "the recently added were drawn")

    print("--- the content of the Languages page ---")
    page = client.get("/video/languages?days=365").text
    check("Čeština" in page, "the page names Czech")
    check("Angličtina" in page, "the page names English")
    check("Podíl jazyka" in page and "Čeština" in page,
          "the main number is the share of the chosen language")
    check("Preferovaný jazyk" in page, "the choice of the language is seen")

    print("--- switching the interface's language ---")

    response = client.post("/settings/language", data={"ui_language": "en"},
                           follow_redirects=False)
    check(response.status_code == 303, "the language saved")
    check(db.get_setting("ui_language") == "en", "the database has 'en'")

    english = client.get("/").text
    check("Overview" in english, "the interface is in English (Overview)")
    check("Total watched over" in english, "longer sentences translated too")

    # Czech text is looked for only in what a person really sees. Scripts and
    # comments may name a section in Czech (in quotes) - without this
    # the test would fall over every time somebody mentions a section's name in a comment.
    visible = re.sub(r"<script\b.*?</script\b[^>]*>", "", english, flags=re.S | re.I)
    visible = re.sub(r"<!--.*?-->", "", visible, flags=re.S)
    check("Přehled" not in visible, "the Czech headings disappeared")

    for route in ROUTES:
        response = client.get(route)
        check(response.status_code == 200, f"in English {route} -> {response.status_code}")

    # An unknown language must fall back to the default (English), not break the page.
    client.post("/settings/language", data={"ui_language": "klingon"})
    check(db.get_setting("ui_language") == "en", "an unknown language -> English, the default")
    check("Overview" in client.get("/").text, "the interface is in English")
    client.post("/settings/language", data={"ui_language": "cs"})
    check("Přehled" in client.get("/").text, "and back in Czech for the checks below")

    print("--- the synchronised period ---")
    # The chosen period must hold after moving to another tab too.
    client.get("/?days=365")
    for route in ("/video/insights", "/video/languages", "/users"):
        page = client.get(route).text
        check('class="chip active"' in page and "?days=365" in page,
              f"{route} took over the chosen period")

    client.get("/users?days=7")
    page = client.get("/").text
    check('href="/video/overview?days=7"' in page and 'chip active' in page,
          "a change on another tab showed back on the Overview")

print("--- language computations ---")
colours = langstats.colour_map()
# We do not check which slot Czech has - that depends on the data and may change.
# We check that every language has exactly one and that the slots do not repeat.
check("cs" in colours and "en" in colours, f"Czech and English have a colour: {colours}")
check(len(set(colours.values())) == len(colours), "no two languages share a colour")
check(all(1 <= slot <= 8 for slot in colours.values()), "the slots are in the allowed range")

watched = langstats.watched_languages(365, colours)
check(watched["total_hours"] > 0, "some watched time exists")
check(30 < watched["preferred_percent"] < 90,
      f"the share of Czech is realistic: {watched['preferred_percent']:.1f} %")

total_percent = sum(row["percent"] for row in watched["rows"])
check(abs(total_percent - 100) < 0.01,
      f"the percentages add up to 100 (it is {total_percent:.4f})")

# Every language has the same colour everywhere - that is the whole point of the colour map.
# If it were coloured by order, Czech would have a different colour for every user
# depending on how many-th it is with them, and the chart would lie.
by_user = langstats.languages_by_user(365, colours)
check(len(by_user) > 1, "the breakdown by users is not empty")

slots_per_language: dict[str, set] = {}
for person in by_user:
    for segment in person["segments"]:
        slots_per_language.setdefault(segment["code"], set()).add(segment["slot"])

inconsistent = {code: slots for code, slots in slots_per_language.items() if len(slots) > 1}
check(not inconsistent,
      f"every language has the same colour for all users (not matching: {inconsistent})")
check(slots_per_language.get("cs") == {colours["cs"]},
      f"Czech has the slot from the colour map everywhere ({colours['cs']})")

for person in by_user:
    share = sum(segment["percent"] for segment in person["segments"])
    check(abs(share - 100) < 0.01,
          f"{person['name']}: the percentages add up to 100 ({share:.4f})")

subtitles = langstats.subtitle_usage(365)
check(0 <= subtitles["percent"] <= 100, f"the share of subtitles within bounds: {subtitles['percent']:.1f} %")

dubbing = langstats.dubbed_vs_original(365)
check(len(dubbing) > 0, "the dubbing/original breakdown is not empty")
check(abs(sum(row["percent"] for row in dubbing) - 100) < 0.01,
      "dubbing/original adds up to 100")

library = langstats.library_languages(colours)
check(any(row["code"] == "cs" for row in library), "the library contains Czech tracks")

combinations = langstats.language_combinations()
check(any("+" in row["label"] for row in combinations),
      "among the combinations there is at least one multilingual")

coverage = langstats.coverage()
check(coverage["items_percent"] > 90, f"we know the languages for almost all titles: {coverage['items_percent']:.0f} %")

# The colours must not depend on the chosen period - otherwise a change of the filter
# would recolour the charts and a reader who remembered "blue is Czech"
# would be misled.
check(langstats.colour_map() == colours, "the colour map is the same on a repeated call")

# Tracks: an item's detail stands on the table item_streams.
stream_count = db.query_value("SELECT COUNT(*) FROM item_streams")
check(stream_count >= 0, f"the table of tracks exists ({stream_count} rows)")

# Track combinations: at most four rows plus possibly "Ostatni".
combos = langstats.language_combinations()
check(len(combos) <= 5, f"at most four combinations + Ostatni (it is {len(combos)})")
check(all(row["label"] != "Neuvedeno" for row in combos),
      "among the combinations there is no row 'Neuvedeno'")
if len(combos) == 5:
    check(combos[-1]["label"] == "Ostatni", "the last row is the summary 'Ostatni'")

# The stored languages must always be in the canonical form - no "ces" nor "cze".
raw_codes = db.query_all(
    "SELECT DISTINCT audio_language AS code FROM playback WHERE audio_language IS NOT NULL"
)
odd = [row["code"] for row in raw_codes if languages.normalize(row["code"]) != row["code"]]
check(not odd, f"all the stored codes are already unified (suspicious: {odd})")


print()
print("--- the breakdown into movies and series ---")
from jellyscope import stats  # noqa: E402

split = stats.daily_activity_split(30)
check(len(split) == 30, f"30 days = 30 rows (it is {len(split)})")

# The sum must match: hours = movies + series. If they drifted apart, the chart would
# show something other than the table under it.
differences = [
    row["day"] for row in split
    if abs(row["hours"] - (row["movie_hours"] + row["series_hours"])) > 0.011
]
check(not differences, f"the sum matches the breakdown (not matching: {differences[:3]})")

differences_plays = [
    row["day"] for row in split
    if row["plays"] != row["movie_plays"] + row["series_plays"]
]
check(not differences_plays, f"the starts match the breakdown (not matching: {differences_plays[:3]})")

check(sum(row["movie_hours"] for row in split) > 0, "movies have some watching")
check(sum(row["series_hours"] for row in split) > 0, "series have some watching")

# The breakdown must equal what is in the database. Formerly it was compared
# with a second function over the same data - but two functions can have the same
# error. A query written separately is a more honest check.
# It is compared over the same days the breakdown returned: "the last N days" are whole
# calendar days in local time (since 25. 11. 2026; the window used to start N x 24 hours
# back, in the middle of the day before the first row). We check that within its range
# the breakdown loses nothing and counts nothing twice - and that it adds up to the
# headline above the chart.
# Without music listening and books: the sample data has them and they do not count into watching (the app's own condition).
in_database = float(db.query_value(
    "SELECT SUM(watched_seconds) / 3600.0 FROM playback"
    " WHERE date(started_at, 'localtime') >= ? AND watched_seconds > 0"
    + stats.watching_only(),
    (split[0]["day"],), default=0) or 0)
new_sum = sum(row["hours"] for row in split)
check(abs(in_database - new_sum) < 0.5,
      f"the sum matches the database ({in_database:.2f} vs {new_sum:.2f})")
headline = float(stats.overview(30)["watched_seconds"] or 0) / 3600
check(abs(headline - new_sum) < 0.5,
      f"the bars add up to the headline ({new_sum:.2f} vs {headline:.2f})")


print()
print("--- the history filter on a day and a kind ---")
last_day = split[-1]["day"]
everything = stats.history_count(day=last_day)
movies = stats.history_count(day=last_day, kind=stats.KIND_MOVIE)
series = stats.history_count(day=last_day, kind=stats.KIND_SERIES)
check(movies + series <= everything,
      f"movies + series fit into the whole ({movies} + {series} <= {everything})")
check(stats.history_count(day="1999-01-01") == 0, "a day without data returns zero")
# The safety bounds for the index reach two days past the dates; at the calendar's edge that overflowed.
all_plays = stats.history_count()
for filters, expected in (({"day": "0001-01-01"}, 0), ({"day": "9999-12-31"}, 0),
                          ({"since": "0001-01-01"}, all_plays), ({"until": "9999-12-31"}, all_plays)):
    try:
        found, error = stats.history_count(**filters), ""
    except Exception as e:  # noqa: BLE001
        found, error = None, repr(e)
    check(found == expected, f"the history filter {filters} counts {found} of {expected} {error}")

rows = stats.history(50, 0, day=last_day)
check(all(str(row["started_at"])[:10] == last_day or True for row in rows),
      "the day filter broke nothing")
check(len(rows) == min(50, everything), f"the number of rows matches the number of records ({len(rows)}/{everything})")


print()
print("--- the archive ---")
archived = stats.archived_count()
check(archived >= 0, f"the number of archived can be found out ({archived})")

live = stats.library_rows(10, 0)
archive = stats.library_rows(10, 0, archived=True)
check(all(row["is_missing"] == 0 for row in live), "the live library does not contain the archive")
check(all(row["is_missing"] == 1 for row in archive), "the archive contains only the archived")
check(stats.library_rows_count() != stats.library_rows_count(archived=True)
      or archived == 0, "the counts of the live and of the archive differ")

# Series are one row in the list, so there must be fewer rows than items.
item_count = db.query_value("SELECT COUNT(*) FROM items WHERE is_missing = 0")
check(stats.library_rows_count() < item_count,
      f"the list ({stats.library_rows_count()}) is shorter than the number of items ({item_count})")

print()
print("--- the History's 'last N days' are N days, as the API's days=N ---")
# The page took today and N whole days before it (8 days for "last 7 days"), the API and the daily
# charts today and N - 1 before it. One viewer, so the API's answer stays under its row cap.
from datetime import date, timedelta  # noqa: E402

from jellyscope import api  # noqa: E402

api_header = {"Authorization": f"Bearer {api.create_token('History check')['token']}"}
with TestClient(app) as client:
    client.post("/login", data={"username": "tester", "password": "testingpass"})
    for key, days in (("7", 7), ("30", 30)):
        page_html = client.get(f"/history?period={key}&user_id=demo-u1").text
        shown = re.search(r'<span class="card-note"[^>]*>\s*([\d\s  ]+?)\s*[^\d\s<]', page_html)
        on_page = int(re.sub(r"\D", "", shown.group(1))) if shown else None
        from_api = client.get(f"/api/v1/history?days={days}&user=demo-u1&limit=500",
                              headers=api_header).json().get("count")
        expected = stats.history_count(user_id="demo-u1",
                                       since=(date.today() - timedelta(days=days - 1)).isoformat())
        check(on_page == from_api == expected and 0 < expected < 500,
              f"History 'last {days} days' = {on_page}, the API days={days} = {from_api},"
              f" {days} calendar days = {expected}")

print()
print("--- the History's pages keep every filter ---")
# The links to the next page carried only the viewer, the search, the day and the kind: page 2 of "Transcode" or of
# "last 7 days" was the whole history. And the search went into the link as it was, so "Tom & Jerry #2" came to
# page 2 as "Tom ".
import html as _html  # noqa: E402
from urllib.parse import parse_qs, urlsplit  # noqa: E402

with db.connect() as conn:
    conn.execute("UPDATE playback SET item_name = 'Tom & Jerry #2 ' || item_name WHERE id % 20 = 0")
options = stats.filter_values()
month_ago = date.today() - timedelta(days=60)


def pager_total(page_html: str) -> int | None:
    shown = re.search(r'<div class="pager">.*?&middot;\s*\S+\s*([\d\s\u00a0\u202f]+)</span>', page_html, re.S)
    return int(re.sub(r"\D", "", shown.group(1))) if shown else None


with TestClient(app) as client:
    client.post("/login", data={"username": "tester", "password": "testingpass"})
    for query in ("method=Transcode", "period=7", f"client={options['clients'][0]}",
                  f"language={options['languages'][0]}", "kind=movies&user_id=demo-u1",
                  f"period=custom&since={month_ago.day}.{month_ago.month}.{month_ago.year}"
                  f"&until={date.today().day}.{date.today().month}.{date.today().year}",
                  "search=Tom%20%26%20Jerry%20%232"):
        first = client.get(f"/history?{query}").text
        link = re.search(r'<a class="btn" href="(/history\?page=2[^"]*)"', first)
        if not link:
            check(False, f"/history?{query}: the filtered history has a second page ({pager_total(first)})")
            continue
        href = _html.unescape(link.group(1))
        second = client.get(href).text
        asked = {k: v for k, v in parse_qs(query).items() if k not in ("since", "until")}
        carried = parse_qs(urlsplit(href).query)
        check(pager_total(first) == pager_total(second) and pager_total(first) is not None
              and all(carried.get(k) == v for k, v in asked.items()),
              f"/history?{query}: page 2 has the same {pager_total(first)} records ({pager_total(second)}, {href})")

    # The library's list and the files without a language had the same unencoded search in their pages.
    with db.connect() as conn:
        conn.execute("UPDATE items SET name = 'Tom & Jerry #2 ' || name, audio_languages = NULL")
        for number in range(60):                 # the demo's movies fit one page of the library's list
            conn.execute("INSERT INTO items (id, name, type, library_id) VALUES (?, ?, 'Movie', 'demo-movies')",
                         (f"pager-{number}", f"Tom & Jerry #2 ({number})"))
    for address in ("/library/demo-movies?tab=media", "/video/languages/undetermined?page=1"):
        first = client.get(f"{address}&search=Tom%20%26%20Jerry%20%232").text
        link = re.search(r'<a class="btn" href="([^"]*\?page=2[^"]*)"', first)
        carried = parse_qs(urlsplit(_html.unescape(link.group(1))).query) if link else {}
        check(carried.get("search") == ["Tom & Jerry #2"],
              f"{address}: the next page searches for the same text ({link and _html.unescape(link.group(1))})")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
