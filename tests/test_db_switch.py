# -*- coding: utf-8 -*-
"""Switching the database in Settings and copying the data over.

Two bugs this test came about from:

1. After saving PostgreSQL the form switched back to SQLite. The setting
   was saved correctly — only the form was given the **running**
   configuration, which is cached in memory and changes only with a restart.

2. Copying the data ended with a bare "Internal Server Error". Creating the schema
   in the target database was outside `try`, so the exception got all the way to the web.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
# A home of its own: without it DATABASE_PATH would beat the stored choice of the
# database in data/database.json and the test would run against the live database.
os.environ["JELLYSCOPE_HOME"] = str(_tmp)
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "switch.db")

from jellyscope import db, dbmigrate, dialect  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


base = Path(_tmp)

print("--- the stored vs. the running configuration ---")
# The app runs on SQLite...
sqlite_cfg = dialect.DatabaseConfig(kind=dialect.SQLITE, path=str(base / "bezici.db"))
dialect.save_config(base, sqlite_cfg)
running = dialect.load_config(base, "data/jellyscope.db")
check(running.kind == dialect.SQLITE, "the starting state is SQLite")

# ...and the user saves PostgreSQL.
pg_cfg = dialect.DatabaseConfig(kind=dialect.POSTGRES, host="db", port=5432,
                                database="js", user="u", password="p")
dialect.save_config(base, pg_cfg)

stored = dialect.load_config(base, "data/jellyscope.db")
check(stored.kind == dialect.POSTGRES,
      f"the stored configuration is PostgreSQL (it is {stored.kind})")
check(stored.host == "db" and stored.database == "js",
      "the server's details were stored too")

# This is the core of that bug: `db.database_config()` keeps the configuration
# in memory, so after saving it still returns the old one. It therefore must not
# go into the form - the form is to show what is stored.
check(stored.to_dict() != running.to_dict(),
      "the stored and the running one differ until a restart (and the page must be able to tell them apart)")


print()
print("--- copying the data: errors instead of Internal Server Error ---")
# The same source and target - it is to be handled, not to fall over.
same = dialect.DatabaseConfig(kind=dialect.SQLITE, path=str(base / "a.db"))
result = dbmigrate.copy_all(same, same)
check(result["status"] == "error", "the same source and target end with an error")
check("same database" in result["message"], f"and it explains why: {result['message']}")

# An unreachable PostgreSQL as the target: an exception used to fly out here from
# creating the schema and the user got 500.
source = dialect.DatabaseConfig(kind=dialect.SQLITE, path=str(base / "zdroj.db"))
db.init_db(source)
target = dialect.DatabaseConfig(kind=dialect.POSTGRES, host="127.0.0.1", port=1,
                                database="neexistuje", user="u", password="p",
                                use_pool=False)
try:
    result = dbmigrate.copy_all(source, target)
    fell_over = False
except Exception as exc:  # noqa: BLE001
    result = {"status": f"EXCEPTION {type(exc).__name__}", "message": str(exc)}
    fell_over = True

check(not fell_over, f"an unreachable target throws no exception (I got {result['status']})")
check(result.get("status") == "error", "an error state comes back")
check(bool(result.get("message")), f"with an explanation: {result.get('message', '')[:90]}")


print()
print("--- the settings being filled in survive a connection test ---")
# The "Test connection" button saves nothing. Without keeping the values being filled
# in, the form would go back to the stored settings after every test
# and the user would fill in the server, the port, the user and the password again.
from jellyscope.web import settings as web_settings  # noqa: E402

account = {"id": 1, "username": "test", "is_admin": 1}
web_settings._draft_clear(account, "database")
check(web_settings._draft_read(account, "database") is None, "without a draft nothing comes back")

draft = dialect.DatabaseConfig(kind=dialect.POSTGRES, host="muj-server",
                               port=5433, database="js", user="tomas",
                               password="tajne")
web_settings._draft_save(account, "database", draft)
loaded = web_settings._draft_read(account, "database")
check(loaded is not None, "the draft comes back")
check(loaded.host == "muj-server" and loaded.port == 5433,
      "including the server and the port")
check(loaded.password == "tajne", "and the password, so it need not be typed again")

# The password must not end up in the session - ours is a signed cookie the
# browser carries along.
check("_DB_DRAFT" in "\n".join(p.read_text(encoding="utf-8")
         for p in sorted((PROJECT / "jellyscope" / "web").rglob("*.py"))),
      "the draft is kept in the process's memory")
check("request.session[\"db_draft\"]" not in
      "\n".join(p.read_text(encoding="utf-8")
         for p in sorted((PROJECT / "jellyscope" / "web").rglob("*.py"))),
      "the draft does NOT go into the session (it would be a cookie with a password)")

# It must not mix with a second account.
other = {"id": 2, "username": "jiny", "is_admin": 1}
check(web_settings._draft_read(other, "database") is None, "another account does not see somebody else's draft")

# The sections do not get mixed up - Jellyfin and the database have a drawer of their own.
web_settings._draft_save(account, "jellyfin", {"url": "http://x", "api_key": "k"})
check(web_settings._draft_read(account, "database") is not None,
      "saving for Jellyfin did not overwrite the database's draft")
check(web_settings._draft_read(account, "jellyfin")["url"] == "http://x",
      "and Jellyfin has its own")

web_settings._draft_clear(account, "database")
check(web_settings._draft_read(account, "database") is None, "after saving the draft is thrown away")
check(web_settings._draft_read(account, "jellyfin") is not None,
      "throwing away the database did not touch Jellyfin")
web_settings._draft_clear(account, "jellyfin")


print()
print("--- advice for missing rights ---")
# The most frequent snag on PostgreSQL 15+: the account connects, but may not
# create tables. The message must point that out, not just repeat the
# database's message.
source_code = (PROJECT / "jellyscope" / "dbmigrate.py").read_text(encoding="utf-8")
check("permission denied" in source_code, "the code recognises the error about rights")
check("GRANT CREATE ON SCHEMA public" in source_code,
      "and advises exactly what to run")

# Creating the schema must be inside try - otherwise the exception gets
# to the web as an Internal Server Error.
lines = source_code.splitlines()
init_at = next(i for i, r in enumerate(lines) if "db.init_db(target)" in r)
before = [r.strip() for r in lines[max(0, init_at - 5):init_at]]
check(any(r.startswith("try:") for r in before),
      f"init_db(target) is inside try (around it: {before})")


print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
