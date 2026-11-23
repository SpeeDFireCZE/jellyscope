# -*- coding: utf-8 -*-
"""The JSON the browser's scripts read: every key a script reads is in the answer (database step 6).

The keys of the dicts the server computes were renamed to English in one go; a script that still read an old key
(`entry.nahled` after the answer said `thumbnail`) would show nothing and raise no error anywhere. So for each JSON a
script reads, the keys read in that block of the script are compared with a real answer:
* the quick sign-in's state (static/js/quick-login.js <- /login/quick/status),
* a chart's tooltip (static/js/app.js <- data-tip-json of the charts on the overview),
* the search of the import window (static/js/app.js <- /archive/search).

Run:
    .venv/bin/python tests/test_scripts_json.py
"""
from __future__ import annotations

import html
import json
import os
import re
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "scripts-json.db")
os.environ["SECRET_KEY"] = "scripts-json-test-key-long-enough"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_DEMO"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from _apps import login  # noqa: E402
from jellyscope import accounts, db, demodata, web  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


SCRIPTS = PROJECT / "jellyscope" / "static" / "js"


def block(name: str, start: str, end: str) -> str:
    text = (SCRIPTS / name).read_text(encoding="utf-8")
    a = text.find(start)
    b = text.find(end, a + 1)
    check(a >= 0 and b > a, f"{name}: the block from {start!r} to {end!r} is there")
    return text[a:b] if a >= 0 and b > a else ""


def read(code: str, *objects: str) -> set[str]:
    """The keys the code reads as <object>.<key> (a call like p.querySelector(...) is no key)."""
    return {key for obj in objects for key in re.findall(r"\b" + obj + r"\.([a-zA-Z_]\w*)\b(?!\s*\()", code)}


db.init_db()
demodata.seed()
accounts.create("admin", "longpassword", is_admin=True)
admin = login("admin", "longpassword")

print("--- the quick sign-in's state ---")
quick = read(block("quick-login.js", "function ask()", "setInterval"), "data")
answer = TestClient(web.app).get("/login/quick/status").json()
check(quick == {"done", "end"} and quick <= set(answer), f"the script reads {sorted(quick)}, the answer has {sorted(answer)}")

print("--- a chart's tooltip ---")
tooltip = block("app.js", "data-tip-json", "tip.appendChild(r);")
top_read, row_read = read(tooltip, "data"), read(tooltip, "row")
page = admin.get("/video/overview?days=0").text
tips = [json.loads(html.unescape(m)) for m in re.findall(r'data-tip-json="([^"]*)"', page)]
top_keys = {k for t in tips for k in t}
row_keys = {k for t in tips for row in t.get("rows", []) for k in row}
check(bool(tips) and top_read == {"heading", "rows"} and top_read >= top_keys,
      f"{len(tips)} tooltips: the script reads {sorted(top_read)}, they have {sorted(top_keys)}")
check({"text", "color"} <= row_read and row_keys <= row_read and "color" in row_keys,
      f"a tooltip's row: the script reads {sorted(row_read)}, the rows have {sorted(row_keys)}")

print("--- the search of the import window ---")
search = block("app.js", "function where(p)", 'field.addEventListener("input"')
item_read, answer_read = read(search, "p", "entry"), read(search, "data")
title = db.query_one("SELECT name FROM items WHERE type = 'Movie' AND is_missing = 0 ORDER BY name LIMIT 1")["name"]
found = admin.get("/archive/search", params={"q": str(title)[:4]}).json()
item_keys = {k for item in found.get("items", []) for k in item}
check(answer_read == {"items", "next"} and answer_read <= set(found), f"the answer: read {sorted(answer_read)}, has {sorted(found)}")
check(bool(found.get("items")) and item_read <= item_keys,
      f"an item: the script reads {sorted(item_read)}, the items have {sorted(item_keys)}")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
