# -*- coding: utf-8 -*-
r"""Rounding times in the charts - the switch in Settings -> Interface.

The axis labels drop the decimals above ten hours, because on an axis
"40" reads better than "39,6". For a particular value in the tooltip, though,
that means up to half an hour of difference - and "39,6 h" tells nobody half an
hour anyway, whereas "39:38" does.

The default stays rounding; whoever wants precision switches it.

Run:
    .venv/bin/python tests/test_time_rounding.py
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "cas.db")
os.environ["SECRET_KEY"] = "testovaci-klic"

from jellyscope import accounts, charts, db, formatting  # noqa: E402

failures = 0


def check(condition: bool, label: str, detail: str = "") -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label} {detail}")
    if not condition:
        failures += 1


def switch(exact: bool) -> None:
    db.set_setting("ui_exact_time", "1" if exact else "0")
    db.forget_settings()


db.init_db()
accounts.create("admin", "longpassword", is_admin=True)

print("--- converting hours to hours and minutes ---")
check(formatting.hours_hhmm(12.4) == "12:24", "12.4 h is 12:24")
check(formatting.hours_hhmm(34.52) == "34:31", "34.52 h is 34:31",
      f"({formatting.hours_hhmm(34.52)})")
check(formatting.hours_hhmm(0.83) == "0:50", "less than an hour too")
check(formatting.hours_hhmm(40) == "40:00", "a whole hour has zeros")
check(formatting.hours_hhmm(0) == "0:00", "zero")
check(formatting.hours_hhmm("nesmysl") == "-", "nonsense does not fall over")

print()
print("--- the default is rounding ---")
check(formatting.exact_time() is False, "without the setting it rounds")
check(charts._value_label(34.52, "h") == "35 h", "and 34.52 h is reported as 35 h",
      f"({charts._value_label(34.52, 'h')})")

print()
print("--- after switching it reads in minutes ---")
switch(True)
check(formatting.exact_time() is True, "the setting holds")
check(charts._value_label(34.52, "h") == "34:31", "the value is exact",
      f"({charts._value_label(34.52, 'h')})")

# The switch concerns time, not everything else: neither Mbit/s nor counts of plays
# can be turned into hours and minutes.
check(charts._value_label(139.77, "Mbit/s") == "140 Mbit/s", "Mbit/s stays",
      f"({charts._value_label(139.77, 'Mbit/s')})")
check(charts._value_label(12.0, "") == "12", "a number without a unit stays")

print()
print("--- it really is in the chart ---")
points = [{"day": "2026-08-30", "movie_hours": 34.52},
          {"day": "2026-08-31", "movie_hours": 12.4}]
series = [{"key": "movie_hours", "label": "Filmy"}]
switch(True)
exact_chart = charts.area_chart_multi(points, "day", series)
check("34:31" in exact_chart, "the tooltip shows 34:31")
check("35 h" not in exact_chart, "and no longer 35 h")

switch(False)
round_chart = charts.area_chart_multi(points, "day", series)
check("35 h" in round_chart, "after switching off 35 h again")
check("34:31" not in round_chart, "and not 34:31")

# The axis is a scale, not a value - there rounding always stays.
switch(True)
axis = re.findall(r'class="axis-label">([^<]+)<', exact_chart)
check(all(":" not in p for p in axis), "the axis labels stay without a colon",
      f"({', '.join(axis[:6])})")

print()
print("--- the bars under a chart too ---")
switch(True)
bars = charts.hbar_chart([{"label": "Tereza", "value": 34.52}],
                         "label", "value", unit="h")
check("34:31" in bars, "the value at a bar is exact")
switch(False)
check("35 h" in charts.hbar_chart([{"label": "Tereza", "value": 34.52}],
                                 "label", "value", unit="h"),
      "and after switching off rounded")

print()
print("--- the setting can be switched from the page ---")
from fastapi.testclient import TestClient  # noqa: E402

from jellyscope.web import app  # noqa: E402

with TestClient(app) as client:
    client.post("/login", data={"username": "admin", "password": "longpassword"},
                follow_redirects=False)
    page = client.get("/settings?section=interface").text
    check('name="ui_exact_time"' in page, "the switch is in Interface")
    check(page.count('name="ui_exact_time"') == 2, "it has both choices")
    # The switch concerns only the notation - so it is seen from the setting that the data
    # and the calculations stay the same, whatever a person picks.
    check("no calculation" in page or "žádná data ani výpočty" in page,
          "and it says the calculations do not change")

    client.post("/settings/interface",
                data={"ui_max_streams": "10", "ui_max_viewers": "10",
                      "ui_map_zoom": "click", "ui_skin": "new",
                      "ui_exact_time": "1"}, follow_redirects=False)
    db.forget_settings()
    check(formatting.exact_time() is True, "saving switches the exact time on")

    after = client.get("/settings?section=interface").text
    check('value="1"\n                           checked' in after
          or re.search(r'name="ui_exact_time" value="1"\s+checked', after) is not None,
          "and the form remembers it")

    client.post("/settings/interface",
                data={"ui_max_streams": "10", "ui_max_viewers": "10",
                      "ui_map_zoom": "click", "ui_skin": "new",
                      "ui_exact_time": "0"}, follow_redirects=False)
    db.forget_settings()
    check(formatting.exact_time() is False, "and off again")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
