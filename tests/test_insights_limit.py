# -*- coding: utf-8 -*-
r"""Long tables on the Insights page are shortened by the settings.

The table "Místo, které nikdo nevyužívá" had 25 rows,
"Nejčastěji transcodované soubory" 15 - and the card next to them ("Proč se transcoduje")
had an empty area twenty rows high under it. How many rows a card
shows straight away is now set by Settings → Interface → Long lists, separately
for a wide screen and for a phone, the same as with streams and viewers.
The rest is opened by the button "zobrazit všech N" in a dialog.

Watched:

* the default limit (10, 5 on a phone) - the rows above it are hidden,
  the button is on the page and the dialog for the whole table too,
* a higher limit shows everything and the button disappears,
* the setting can be saved and is trimmed to its bounds,
* the fields are in the Interface form.

Run:
    .venv/bin/python tests/test_insights_limit.py
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
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "strop.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic-dostatecne-dlouhy"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_DEMO"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, db, web  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


db.init_db()
accounts.create("admin", "longpassword", is_admin=True)

# Fifteen never played movies with a size - all end up in the table
# "Místo, které nikdo nevyužívá".
with db.connect() as conn:
    conn.execute("INSERT INTO libraries (id, name, collection_type) VALUES ('filmy', 'Filmy', 'movies')")
    for i in range(15):
        conn.execute(
            "INSERT INTO items (id, name, type, library_id, is_missing, size_bytes)"
            " VALUES (?, ?, 'Movie', 'filmy', 0, ?)",
            (f"f{i}", f"Film {i:02d}", (20 - i) * 1_000_000_000))
    conn.commit()

client = TestClient(web.app)
client.post("/login", data={"username": "admin", "password": "longpassword", "method": "local"})


def space_card(html: str) -> str:
    """The whole card "Místo, které nikdo nevyužívá" with the button under the table."""
    start = html.index('id="unused-space"')
    return html[start:html.index('<div class="grid-2', start)]


def rows(html: str) -> list[str]:
    return re.findall(r"<tr[^>]*>\s*<td", html)


print("--- the default limit: 10 straight away, 5 on a phone ---")
html = client.get("/video/insights").text
card = space_card(html)
all_rows = rows(card)
check(len(all_rows) == 15, f"the table has all 15 rows ({len(all_rows)})")
extra = [r for r in all_rows if "extra-row" in r]
desktop_only = [r for r in all_rows if "desktop-only" in r]
check(len(extra) == 5, f"5 rows above the limit are hidden ({len(extra)})")
check(len(desktop_only) == 5, f"5 more hide only on a phone ({len(desktop_only)})")
check("data-table-window" in card, "under the table there is a button for the whole table")
check('id="table-window"' in html, "and the dialog the table opens into")

print()
print("--- a higher limit shows everything ---")
response = client.post("/settings/interface", data={
    "ui_max_streams": "10", "ui_max_streams_mobile": "3",
    "ui_max_viewers": "10", "ui_max_viewers_mobile": "5",
    "ui_max_rows": "20", "ui_max_rows_mobile": "20",
    "ui_map_zoom": "click", "ui_skin": "new", "ui_exact_time": "0", "ui_dashboard": "0",
}, follow_redirects=False)
check(response.status_code == 303, f"the setting was saved ({response.status_code})")
check(db.get_setting("ui_max_rows") == "20", "the rows limit is in the database")
db.forget_settings()
card = space_card(client.get("/video/insights").text)
check(not any("extra-row" in r or "desktop-only" in r for r in rows(card)),
      "nothing is hidden")
check("data-table-window" not in card, "and the button for the whole table disappeared")

print()
print("--- the button is wherever something is hidden ---")


def save_limits(desktop: str, mobile: str) -> None:
    client.post("/settings/interface", data={"ui_max_rows": desktop, "ui_max_rows_mobile": mobile})
    db.forget_settings()


def button(card: str) -> str | None:
    """The classes of the row with the button for the whole table, or None when it is not there."""
    m = re.search(r'<div class="(btn-row[^"]*)"[^>]*>\s*<button[^>]*data-table-window', card)
    return m.group(1) if m else None


# Phone 20, desktop 12: the rows 13-15 are hidden everywhere. The button for them
# must be there anyway - formerly it was drawn only above the mobile limit and three rows
# disappeared with no way to show them.
save_limits("12", "20")
card = space_card(client.get("/video/insights").text)
check(sum("extra-row" in r for r in rows(card)) == 3, "reversed limits: 3 rows hidden")
check(button(card) == "btn-row", f"and the button for them is everywhere ({button(card)})")

# Desktop 20, phone 5: on a wide one all 15 are seen, on a phone only 5.
# The button is only on a phone.
save_limits("20", "5")
card = space_card(client.get("/video/insights").text)
check(button(card) == "btn-row mobile-only", f"hidden only on a phone: the button only on a phone ({button(card)})")

print()
print("--- bounds and the form ---")
client.post("/settings/interface", data={"ui_max_rows": "99999", "ui_max_rows_mobile": "0"})
db.forget_settings()
check(int(db.get_setting("ui_max_rows")) <= web.LIMIT_MAX, "a too big number is trimmed")
check(int(db.get_setting("ui_max_rows_mobile")) >= 1, "zero is not accepted")
form_page = client.get("/settings?section=interface").text
check('name="ui_max_rows"' in form_page and 'name="ui_max_rows_mobile"' in form_page,
      "both fields are in Settings → Interface → Long lists")

print()
print("--- own row counts for *Arr and Seerr ---")
# Separate settings with their own default value (10 and 5) - they have nothing in common with the Insights.
# The fields are in the form only with a connected application: without Sonarr, Radarr or Seerr they have nothing to control.
from _apps import no_network, connect_radarr, connect_seerr  # noqa: E402

no_network()      # the applications are only connected here (because of the form's fields), nobody is to ask them

ARR_FIELDS, SEERR_FIELDS = ("ui_max_rows_arr", "ui_max_rows_arr_mobile"), ("ui_max_rows_seerr", "ui_max_rows_seerr_mobile")


def form_values() -> dict[str, str | None]:
    html = client.get("/settings?section=interface").text
    return {field: (re.search(rf'name="{field}"[^>]*value="(\d+)"', html, re.S) or [None, None])[1] for field in ARR_FIELDS + SEERR_FIELDS}


check(form_values() == dict.fromkeys(ARR_FIELDS + SEERR_FIELDS), f"without a connected application the fields for *Arr and Seerr are not there ({form_values()})")
connect_radarr()
db.forget_settings()
arr_only = form_values()
connect_seerr()
db.forget_settings()
client.post("/settings/interface", data={"ui_max_rows": "17", "ui_max_rows_mobile": "4"})
db.forget_settings()
check(arr_only == {"ui_max_rows_arr": "10", "ui_max_rows_arr_mobile": "5", "ui_max_rows_seerr": None, "ui_max_rows_seerr_mobile": None}
      and form_values() == {"ui_max_rows_arr": "10", "ui_max_rows_arr_mobile": "5", "ui_max_rows_seerr": "10", "ui_max_rows_seerr_mobile": "5"},
      f"the fields only with a connected application, with their own default value - the Insights (17 and 4) do not affect them ({arr_only}, {form_values()})")
form_page = client.get("/settings?section=interface").text
check('name="ui_rows_arr" value="1"' in form_page and 'name="ui_rows_seerr" value="1"' in form_page,
      "the form with the fields sends a mark that they were in it too (an empty field could not be told from a missing one otherwise)")
client.post("/settings/interface", data={"ui_max_rows": "17", "ui_max_rows_mobile": "4", "ui_rows_arr": "1", "ui_rows_seerr": "1",
                                         "ui_max_rows_arr": "7", "ui_max_rows_arr_mobile": "3",
                                         "ui_max_rows_seerr": "99999", "ui_max_rows_seerr_mobile": "0"})
db.forget_settings()
limits = web._limits()["table_limits"]
check(limits == {"insights": (17, 4), "arr": (7, 3), "seerr": (web.LIMIT_MAX, 1), "music": (10, 5)}
      and int(db.get_setting("ui_max_rows_seerr")) <= web.LIMIT_MAX and int(db.get_setting("ui_max_rows_seerr_mobile")) >= 1,
      f"the counts are saved each separately and within bounds ({limits})")
client.post("/settings/interface", data={"ui_max_rows": "12", "ui_max_rows_mobile": "6"})
db.forget_settings()
check(web._limits()["table_limits"]["arr"] == (7, 3) and web._limits()["table_limits"]["insights"] == (12, 6),
      "a form that did not send the fields (the application is not connected) does not overwrite their values")
client.post("/settings/interface", data={"ui_max_rows": "12", "ui_max_rows_mobile": "6", "ui_rows_arr": "1",
                                         "ui_max_rows_arr": "", "ui_max_rows_arr_mobile": "abc"})
db.forget_settings()
check(web._limits()["table_limits"]["arr"] == (10, 5) and form_values()["ui_max_rows_arr"] == "10",
      f"an emptied or nonsensical field = the default value, and the form shows it too ({web._limits()['table_limits']['arr']})")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
