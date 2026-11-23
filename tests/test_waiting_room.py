# -*- coding: utf-8 -*-
r"""The waiting page after a restart recognises that the process was replaced.

This did not work once already and it cost a person a stuck page:
the waiting page wrote down the starting state only from the **first answer** of /health. But
the restart comes within a second and the first query only after two - so that answer already
belonged to the new process, the waiting page wrote it down as "what I am waiting from"
and waited for a change that never came. The app was running all the while.

What is waited from must therefore be written into the page by the server - the same
reason `tasks_version` is there when waiting for a task.

There are two signals and each covers the weakness of the other:

* `version` is the question itself ("is the new version running yet?"), but it is reported only
  to a signed-in person,
* `started_at` answers anybody, it just says less ("something restarted").

Run:
    .venv/bin/python tests/test_waiting_room.py
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "cekarna.db")
os.environ["SECRET_KEY"] = "testovaci-klic"

from jellyscope import accounts, db  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


db.init_db()
accounts.create("admin", "longpassword", is_admin=True)

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import web  # noqa: E402
from jellyscope.web import settings as web_settings  # noqa: E402

client = TestClient(web.app)

print("--- /health: the version only to a signed-in person ---")
public = client.get("/health").json()
check("started_at" in public, "the process's start is reported to anybody")
check("version" not in public, "the version not")

client.post("/login", data={"username": "admin", "password": "longpassword"},
            follow_redirects=False)
signed_in = client.get("/health").json()
check(signed_in.get("version") == web.__version__,
      f"to a signed-in person yes ({signed_in.get('version')})")

print()
print("--- the waiting page after an update ---")
page = web_settings._update_page("testovaci-nonce")
check(f"var startedAt = {int(web.STARTED_AT)};" in page,
      "the process's start is written in the page")
check(f'var version = "{web.__version__}";' in page, "and the version too")
# This is the bug the test came about for: the starting state must NOT be taken
# from an answer that may already belong to the new process.
check("startedAt === null" not in page,
      "the starting state is not taken from the first answer")
check(page.count("location.href") == 2,
      "it lets on when the start or the version changes")
# Should the process not come up at all, the page says so after a few minutes -
# instead of spinning for ever.
check('id="note"' in page, "and when it does not come up, it speaks up")

print()
print("--- the waiting page after a manual restart ---")
response = client.get("/settings?section=general&wait=restart")
html = response.text
found = re.search(r'data-run-since="(\d+)"', html)
check(found is not None, "the page carries the process's start")
if found:
    check(int(found.group(1)) == int(web.STARTED_AT),
          f"and it is the running one ({found.group(1)})")
check('data-wait-for="restart"' in html, "and it knows what it waits for")

# Without `?wait=` nothing is waited for, so that mark has no business there.
without_waiting = client.get("/settings?section=general").text
check("data-wait-for" not in without_waiting, "without waiting nothing is watched")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
