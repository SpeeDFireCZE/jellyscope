# -*- coding: utf-8 -*-
r"""Logs: what is written into them, the API log, downloading and rotation.

Run:
    .venv/bin/python tests/test_logs.py
"""
from __future__ import annotations

import logging
import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "logy.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

from jellyscope import collector, db  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


class Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.INFO)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


db.init_db()
capture = Capture()
logging.getLogger("jellyscope.db").addHandler(capture)
logging.getLogger("jellyscope.db").setLevel(logging.INFO)

print("--- operational records are not logged ---")
# The collector rewrites the time of the last query at every query to Jellyfin,
# even every three seconds. Nothing but noise would be left of it in the log.
for n in range(3):
    db.set_setting(collector.LAST_POLL_KEY, f"2026-10-18 10:00:0{n}")
    db.set_setting(collector.STATUS_KEY, "ok" if n % 2 else "error")
check(not [z for z in capture.messages if "_collector" in z],
      f"the collector's state is not in the log ({len(capture.messages)} messages)")
db.set_setting("poll_interval", "7")
check(any("poll_interval" in z for z in capture.messages), "an ordinary setting is still logged")

import asyncio  # noqa: E402
from datetime import date, timedelta  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, api, applog, tasks, web  # noqa: E402

accounts.create("admin", "longpassword", is_admin=True)
reader_id = accounts.create("reader", "readerpass")
db.set_setting("reader_may_have_key", "1")


def sign_in(name: str, password: str) -> TestClient:
    client = TestClient(web.app)
    client.post("/login", data={"username": name, "password": password, "method": "local"},
                follow_redirects=False)
    return client


anonymous = TestClient(web.app)
admin = sign_in("admin", "longpassword")
reader = sign_in("reader", "readerpass")


def call(token: str, path: str = "/api/v1/summary"):
    return anonymous.get(path, headers={"Authorization": f"Bearer {token}"})


FOLDER = applog.log_dir()

print()
print("--- the API log is a file ---")
server_key = api.create_token("Grafana")
reader_key = api.create_token("Homepage | doma", reader_id)
call(server_key["token"], "/api/v1/summary?days=7")
call(reader_key["token"])
call("js_neplatny")
rows = applog.read_api(100)["rows"]
check(len(rows) == 2, f"every query with an existing key is written, an invalid one not ({len(rows)})")
check(bool(rows) and rows[0]["key_id"] == reader_key["id"] and rows[0]["owner"] == "reader"
      and rows[0]["key"] == "Homepage / doma",
      "the newest at the top, with the key and the owner (the bar gone from the name)")
check(len(rows) == 2 and rows[1]["owner"] == "" and rows[1]["result"] == 200
      and rows[1]["path"] == "/api/v1/summary?days=7" and rows[1]["ip"] == "testclient",
      "a server key: without an owner, with the address and the IP")
api.disable_token(reader_key["id"])
call(reader_key["token"])
api.enable_token(reader_key["id"])
check(applog.read_api(100)["rows"][0]["result"] == 403, "a refused query is written too")
check(all(s["name"] != applog.API_NAME for s in applog.available_files()),
      "the app log's switch does not have api.log (it has its own view)")
check(not (FOLDER / applog.OWN_NAME).exists()
      or "/api/v1/summary" not in (FOLDER / applog.OWN_NAME).read_text(encoding="utf-8"),
      "the queries are not written into the app's log")

print()
print("--- the API log's filters ---")
check({r["owner"] for r in applog.read_api(100, account="reader")["rows"]} == {"reader"},
      "by user")
check({r["owner"] for r in applog.read_api(100, account="-")["rows"]} == {""},
      "\"-\" = a server key")
check({r["key_id"] for r in applog.read_api(100, key=str(server_key["id"]))["rows"]}
      == {server_key["id"]}, "by key")
check(len(applog.read_api(100, ip="testcl")["rows"]) == 3
      and not applog.read_api(100, ip="10.0.0.")["rows"], "by a part of the IP address")
check(applog.read_api(100, key="nesmysl")["rows"] != [], "nonsense in the key = no filter")

print()
print("--- Settings → Log ---")
page = admin.get("/settings?section=log").text
check('href="/settings?section=log&amp;log_kind=api"' in page, "a switch to the API log")
page = admin.get("/settings?section=log&log_kind=api&log_account=reader").text
check("Homepage / doma" in page and "/api/v1/summary?days=7" not in page,
      "the filter by user shows only their queries")
page = admin.get(f"/settings?section=log&log_kind=api&log_key={server_key['id']}").text
check("/api/v1/summary?days=7" in page, "the filter by key")
check(reader.get("/settings?section=log&log_kind=api", follow_redirects=False).status_code == 403,
      "a reader may not open the log")
api_page = admin.get("/settings?section=api").text
check(f"log_key={server_key['id']}" in api_page, "Settings → API links to the key's API log")

print()
print("--- downloading ---")
applog.setup()
logging.getLogger("jellyscope.test").warning("pokus password=tajne123 konec")
response = admin.get(f"/settings/log/download?file={applog.OWN_NAME}")
check(response.status_code == 200 and "attachment" in response.headers.get("content-disposition", "")
      and "pokus password=***" in response.text and "tajne123" not in response.text,
      "the app's log downloads whole, with the password replaced by asterisks")
response = admin.get(f"/settings/log/download?file={applog.API_NAME}")
check(response.status_code == 200 and "Homepage / doma" in response.text, "the API log too")
# The control: a request of the same shape with a log's name downloads it - otherwise the 404s below
# would say nothing (a parameter the route does not read gives 404 for any name).
check(admin.get("/settings/log/download", params={"file": applog.API_NAME}).status_code == 200,
      "the same request with a log's name downloads it (the checks below use the parameter that is read)")
for bad in ("../.env", "..\\logy.db", "logy.db", "api.log.x", "/etc/passwd", ""):
    check(admin.get("/settings/log/download", params={"file": bad}).status_code == 404,
          f"nothing outside the logs folder: {bad!r}")
check(reader.get(f"/settings/log/download?file={applog.API_NAME}",
                 follow_redirects=False).status_code == 403, "a reader downloads nothing")

print()
print("--- rotation ---")
before = (FOLDER / applog.API_NAME).read_text(encoding="utf-8")
result = applog.rotate(2)
check(sorted(result["rotated"]) == sorted(applog.OWN_FILES) and not result["errors"],
      f"it rotates the app's log and the API's ({result})")
check((FOLDER / f"{applog.API_NAME}.1").read_text(encoding="utf-8") == before
      and (FOLDER / applog.API_NAME).stat().st_size == 0,
      "the log so far is now .1, the new one starts empty")
call(server_key["token"], "/api/v1/summary?po=rotaci")
check("po=rotaci" in (FOLDER / applog.API_NAME).read_text(encoding="utf-8"),
      "writing goes on into the new file")
applog.rotate(2)
applog.rotate(2)
check([z["number"] for z in applog.backups(applog.API_NAME)] == [1, 2],
      "only the set number of old logs is kept")
applog.rotate(1)
check([z["number"] for z in applog.backups(applog.API_NAME)] == [1], "a smaller number deletes the extra ones")
check(admin.get(f"/settings/log/download?file={applog.API_NAME}.1").status_code == 200,
      "an older log can be downloaded")
check(f"{applog.API_NAME}.1" in admin.get("/settings?section=log&log_kind=api").text,
      "the older logs are in Log to download")

print()
print("--- the Log rotation task ---")
task = tasks.TASKS.get("logrotate")
check(task is not None and task.is_daily and tasks.is_enabled(task),
      "it is among the tasks, daily and switched on by default")
db.set_setting(applog.DAYS_KEY, "7")
db.set_setting(applog.LAST_ROTATION_KEY, date.today().isoformat())
check(asyncio.run(task.runner()).get("skipped") is True,
      "a scheduled run: 7 days have not passed since the last rotation, nothing rotates")
check(tasks.status(task)["next_date"] == (date.today() + timedelta(days=7)).strftime("%d.%m."),
      "Next run shows the day of the next rotation")
db.set_setting(applog.LAST_ROTATION_KEY, (date.today() - timedelta(days=7)).isoformat())
check(asyncio.run(task.runner()).get("status") == "ok"
      and db.get_setting(applog.LAST_ROTATION_KEY) == date.today().isoformat(),
      "after 7 days it rotates and remembers today")
before = (FOLDER / applog.API_NAME).read_text(encoding="utf-8")
call(server_key["token"], "/api/v1/summary?rucne=1")
check(asyncio.run(tasks.run_now("logrotate")).get("status") == "ok"
      and "rucne=1" in (FOLDER / f"{applog.API_NAME}.1").read_text(encoding="utf-8"),
      "a manual Run now rotates at once, even when it has already rotated today")
admin.post("/settings/tasks", data={"enabled_logrotate": "1", "logrotate_days": "0",
                                    "logrotate_count": "999"})
check(db.get_setting(applog.DAYS_KEY) == "1" and db.get_setting(applog.COUNT_KEY) == "100",
      "saving: the days and the count are clipped into their bounds")
page = admin.get("/settings?section=tasks").text
check('name="logrotate_days"' in page and 'name="logrotate_count"' in page,
      "Tasks have both fields")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
