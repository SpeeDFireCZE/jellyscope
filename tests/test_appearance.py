# -*- coding: utf-8 -*-
r"""Switching between the new and the classic appearance.

The new appearance changed colours and shapes too; whoever is used to the old look can
go back to it in Settings → Interface. The chosen appearance is put into
`<html data-skin>` **on the server**, not by JavaScript only: otherwise the page
would flash for a moment in one appearance and switch to the other.

The whole classic appearance is only CSS - the charts are drawn through variables
(--accent, --series-*, --good), so they recolour themselves and Python knows nothing about
any appearance. The test watches it: there must be no condition on the appearance in the templates
nor in the charts' code.

Run:
    .venv/bin/python tests/test_appearance.py
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "vzhled.db")
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

client = TestClient(web.app)
client.post("/login", data={"username": "admin", "password": "longpassword"},
            follow_redirects=False)

print("--- the default is the new one ---")
# Any page carries the theme in <html>; My account exists for every account and needs no content
# (since 16. 11. 2026 / opens a section, and an empty database has none).
check(db.get_setting("ui_skin", "") == "new", "the setting has 'new'")
check('data-skin="new"' in client.get("/account").text, "and the page says so straight in <html>")

print()
print("--- switching to the classic one ---")
client.post("/settings/interface",
            data={"ui_max_streams": "10", "ui_max_viewers": "10",
                  "ui_map_zoom": "click", "ui_skin": "classic"},
            follow_redirects=False)
check(db.get_setting("ui_skin", "") == "classic", "it was saved")
check('data-skin="classic"' in client.get("/account").text, "and the page switched")

print()
print("--- a forged value is not saved ---")
# The appearance is put into the HTML, so anything from here must pass a fixed
# list - otherwise anybody could smuggle whatever they want into the attribute.
client.post("/settings/interface",
            data={"ui_max_streams": "10", "ui_max_viewers": "10",
                  "ui_map_zoom": "click", "ui_skin": '"><script>x()</script>'},
            follow_redirects=False)
check(db.get_setting("ui_skin", "") == "new", "an unknown appearance falls back to 'new'")
check("<script>x()</script>" not in client.get("/account").text, "and it did not get into the page")

print()
print("--- the classic appearance is only CSS ---")
# If the appearance started to be decided in the templates or in the charts, it would stop
# being a switch and would become a second code that has to be maintained.
# Exactly two templates may know the appearance: base.html puts it into <html>
# and the Interface section offers the switch. If it appeared elsewhere, it would mean
# that a second form of a page is made somewhere - and that one has to be maintained.
#
# The templates are looked for in depth (`rglob`): Settings are split by
# sections into `templates/settings/`, so a look only at the top of the folder would
# not find the switch - and the test would stay silent about exactly what it is to watch.
ROOT = PROJECT / "jellyscope" / "templates"
templates = {str(p.relative_to(ROOT)).replace("\\", "/"):
             p.read_text(encoding="utf-8") for p in ROOT.rglob("*.html")}
APPEARANCE_BELONGS_IN = ("base.html", "settings/interface.html")
check(templates["base.html"].count("ui_skin") == 1, "base.html only puts the appearance in")
check(templates["settings/interface.html"].count("ui_skin") == 4,
      "the Interface section offers it for choosing "
      f"({templates['settings/interface.html'].count('ui_skin')}×)")
elsewhere = {name: text.count("ui_skin") for name, text in templates.items()
             if name not in APPEARANCE_BELONGS_IN and "ui_skin" in text}
check(not elsewhere, f"and nothing elsewhere is decided by it ({elsewhere})")
charts_source = (PROJECT / "jellyscope" / "charts.py").read_text(encoding="utf-8")
check("skin" not in charts_source and "klasick" not in charts_source, "the charts know nothing about the appearance")

style = (PROJECT / "jellyscope" / "static" / "style.css").read_text(encoding="utf-8")
check('data-skin="classic"' in style, "and it lives whole in style.css")
# The classic appearance must cover both themes, otherwise the new colours would stay
# in the dark mode.
check(style.count(':root[data-skin="classic"]') >= 3,
      f"for the light and the dark mode ({style.count(chr(58) + 'root[data-skin=' + chr(34) + 'classic' + chr(34) + ']')}×)")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
