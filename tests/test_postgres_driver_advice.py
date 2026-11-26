# -*- coding: utf-8 -*-
"""Settings -> Database: how to install a missing PostgreSQL driver.

The advice used to be `pip install "psycopg[binary,pool]"`: the newest driver, never the version
requirements-postgres.txt pins (in time a 4.x with another API). And in our Docker image it advised
pip inside the container, where the driver is gone with the next `docker compose up -d --build`. The
advice now installs from requirements-postgres.txt, and in our image it says to rebuild with PSYCOPG=1.
The same advice on the outage page (db._open_raw) is checked in tests/test_dialect.py.

Run:
    .venv/bin/python tests/test_postgres_driver_advice.py
"""
from __future__ import annotations

import dataclasses
import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))

_tmp = tempfile.mkdtemp()
# A home of its own: without it DATABASE_PATH would beat the stored choice
# of the database in data/database.json and the test would run against the live database.
os.environ["JELLYSCOPE_HOME"] = str(_tmp)
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "driver-advice.db")
os.environ["SECRET_KEY"] = "driver-advice-test-key-long-enough-for-it"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_DEMO"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from _package import substitute  # noqa: E402
from jellyscope import accounts, db  # noqa: E402
from jellyscope.web import app  # noqa: E402
from jellyscope.web import settings as web_settings  # noqa: E402
from jellyscope.web.base import config  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


db.init_db()
accounts.create("admin", "longpassword", is_admin=True)
client = TestClient(app)
client.post("/login", data={"username": "admin", "password": "longpassword"})

real_driver, real_pool = db.psycopg_available, db.pool_available
db.psycopg_available = lambda: False
db.pool_available = lambda: False


def database_section(our_image: bool) -> str:
    put_back = substitute(web_settings, "config", dataclasses.replace(config, our_image=our_image))
    try:
        return client.get("/settings?section=database").text
    finally:
        put_back()


try:
    print("--- outside our image: pip from requirements-postgres.txt ---")
    page = database_section(our_image=False)
    command = f"{sys.executable} -m pip install -r {PROJECT / 'requirements-postgres.txt'}"
    check(page.count(command) == 2,
          f"the driver and the pool are installed from the pinned file ({page.count(command)} times: {command})")
    check("psycopg[" not in page, "no unpinned psycopg is advised")

    print("--- in our Docker image: rebuild it with PSYCOPG=1 ---")
    page = database_section(our_image=True)
    check("PSYCOPG=1" in page and "docker compose up -d --build" in page,
          "the page says to rebuild the image with PSYCOPG=1")
    check("pip install" not in page, "and not to run pip in the container")
finally:
    db.psycopg_available, db.pool_available = real_driver, real_pool

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
