# -*- coding: utf-8 -*-
r"""Every list on the *Arr pages keeps the row limit, as on Movies & series (the user, 23. 11. 2026).

The tables of statistics (sources, quality, upgrades…) showed only "Rows in *Arr tables" and the rest in the
window "show the whole table"; the live lists did not - a long calendar stretched over the whole screen.
The test watches, for every app:

A) a list longer than the limit: the rows over the limit are hidden (`extra-row`), the rows between the phone
   limit and the limit only on the wide screen (`desktop-only`), the button opens the shared window;
B) a list shorter than the limit: no hidden row, no button;
C) the card asks for the window (`table_window=True`) - the custom dashboard with this card alone then has it;
D) the warnings of the app are a table for the administrator; whoever else reads the page still sees only how many;
E) at most two cards side by side on the page of every app, as on Movies & series; no card got lost.

Spec: docs/superpowers/specs/2026-11-23-arr-layout-design.md

Run:
    .venv/bin/python tests/test_live_lists_rows.py
"""
from __future__ import annotations

import copy
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "live-lists.db")
os.environ["SECRET_KEY"] = "test-key-live-lists"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

import httpx  # noqa: E402

import _apps  # noqa: E402
from _apps import card_html  # noqa: E402
from _package import substitute  # noqa: E402
from jellyscope import accounts, cards, db  # noqa: E402
from jellyscope.arr import bazarr, lidarr, prowlarr, radarr, readarr, sonarr  # noqa: E402

_apps.no_network()
failures = 0
db.init_db()
db.set_setting("ui_language", "cs")       # the checks read Czech
db.set_setting(cards.ENABLED_KEY, "1")    # the custom dashboard is on


def check(condition: Any, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


accounts.create("admin", "longpassword", is_admin=True)
accounts.create("reader", "longpassword")
admin = _apps.login("admin")
reader = _apps.login("reader")
BY_KEY = {card.key: card for card in cards.ALL_CARDS}
ROWS = 3                                  # every list gets three rows
WARNING = "A warning of the app number {n}"


def three(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Three copies of the first record, each with its own id."""
    out = []
    for n in range(ROWS):
        record = copy.deepcopy(records[0])
        record["id"] = 9000 + n
        out.append(record)
    return out


def warnings() -> httpx.Response:
    return httpx.Response(200, json=[{"source": "IndexerStatusCheck", "type": "warning",
                                      "message": WARNING.format(n=n), "wikiUrl": ""} for n in range(ROWS)])


def rows(html: str) -> int:
    """How many rows the card's table has (without its header)."""
    return html.count("<tr") - html.count("<thead")


def check_lists(app: str, page_of: Any, keys: tuple[str, ...]) -> None:
    """A, B, C for the list cards `keys` of one app; `page_of()` reads its page (a fresh live state)."""
    db.set_setting("ui_max_rows_arr", "2")
    db.set_setting("ui_max_rows_arr_mobile", "1")
    page = page_of(admin)
    for key in keys:
        html = card_html(page, key)
        # the limit 2, on a phone 1: the second row desktop-only, from the third on extra-row
        check(rows(html) >= ROWS and html.count("extra-row") == rows(html) - 2 and html.count("desktop-only") == 1
              and "data-table-window" in html,
              f"{key}: two rows, one on the phone, the rest in the window ({rows(html)} rows)")
        check(BY_KEY[key].has_table_window, f"{key}: the card asks for the table window")
    db.set_setting("ui_max_rows_arr", "50")
    db.set_setting("ui_max_rows_arr_mobile", "50")
    page = page_of(admin)
    for key in keys:
        html = card_html(page, key)
        check(html and "extra-row" not in html and "desktop-only" not in html and "data-table-window" not in html,
              f"{key}: a list shorter than the limit hides nothing and has no button")
    db.set_setting("ui_max_rows_arr", "2")
    db.set_setting("ui_max_rows_arr_mobile", "1")
    for key in keys:
        cards.save_layout([key])
        alone = admin.get("/dashboard?days=365").text
        check('id="table-window"' in alone and "data-table-window" in card_html(alone, key),
              f"{key}: alone in the custom dashboard, with the button and the window")
    cards.save_layout(list(keys))
    together = admin.get("/dashboard?days=365").text
    check(together.count('id="table-window"') == 1
          and all("data-table-window" in card_html(together, key) for key in keys),
          f"{app}: all the lists together in the custom dashboard, one window")
    cards.save_layout([])


APPS: list[tuple[str, Any, str, Any, Any, Path]] = [
    ("sonarr", sonarr, "SonarrClient", _apps.sonarr_handler, _apps.sonarr_client, _apps.SONARR_RESPONSES),
    ("radarr", radarr, "RadarrClient", _apps.radarr_handler, _apps.radarr_client, _apps.RADARR_RESPONSES),
    ("lidarr", lidarr, "LidarrClient", _apps.lidarr_handler, _apps.lidarr_client, _apps.LIDARR_RESPONSES),
    ("readarr", readarr, "ReadarrClient", _apps.readarr_handler, _apps.readarr_client, _apps.READARR_RESPONSES),
]

for app, module, client_name, handler_of, client_of, responses in APPS:
    print()
    print(f"=== {app} ===")
    queue = _apps.recorded_response(responses, "queue")
    stuck = [r for r in queue["records"] if r.get("trackedDownloadStatus") in ("warning", "error")]
    instead = {
        "queue": httpx.Response(200, json={**queue, "totalRecords": ROWS, "records": three(stuck)}),
        "calendar": httpx.Response(200, json=three(_apps.recorded_response(responses, "calendar"))),
        "health": warnings(),
    }
    restore = substitute(module, client_name, client_of(handler_of(None, instead)))
    getattr(_apps, f"connect_{app}")()

    def page_of(client: Any, module: Any = module, app: str = app) -> str:
        module.live.CACHE.clear()
        module.progress._memory = None
        return client.get(f"/arr/{app}?days=365").text

    check_lists(app, page_of, tuple(f"arr_{app}_{card}" for card in ("queue", "calendar", "health")))

    print("--- D) the warnings ---")
    health = card_html(page_of(admin), f"arr_{app}_health")
    check("<table" in health and WARNING.format(n=2) in health, f"{app}: the warnings are a table for the administrator")
    health = card_html(page_of(reader), f"arr_{app}_health")
    check("<table" not in health and WARNING.format(n=0) not in health and "Aplikace hlásí varování" in health,
          f"{app}: whoever else sees only how many")
    sources = ("indexers", "groups", "clients", "protocols", "qualities") + (("resolutions",) if app in ("sonarr", "radarr") else ())
    page = page_of(admin)
    check(all(f"<!-- card: arr_{app}_{name} -->" in page for name in sources),
          f"{app}: the page has every card of Sources and quality")
    module.save_settings(False, f"http://{app}.test", "tajny-klic")
    restore()


def warnings_of(app: str, page_of: Any, key: str) -> None:
    """D: the warnings are a table for the administrator, whoever else sees only how many."""
    print("--- D) the warnings ---")
    health = card_html(page_of(admin), key)
    check("<table" in health and "number 2" in health, f"{app}: the warnings are a table for the administrator")
    health = card_html(page_of(reader), key)
    check("<table" not in health and "number 0" not in health and "Aplikace hlásí varování" in health,
          f"{app}: whoever else sees only how many")


def fresh_page(module: Any, app: str) -> Any:
    def page_of(client: Any) -> str:
        module.live.CACHE.clear()
        return client.get(f"/arr/{app}?days=365").text
    return page_of


print()
print("=== bazarr ===")
restore = substitute(bazarr, "BazarrClient", _apps.bazarr_client(_apps.bazarr_handler(None, {
    "providers": {"data": [{"name": f"provider{n}", "status": "Good", "retry": "-"} for n in range(ROWS)]},
    "system/health": {"data": [{"object": f"/media/{n}", "issue": WARNING.format(n=n)} for n in range(ROWS)]},
})))
_apps.connect_bazarr()
check_lists("bazarr", fresh_page(bazarr, "bazarr"), ("bazarr_providers_now", "bazarr_health"))
warnings_of("bazarr", fresh_page(bazarr, "bazarr"), "bazarr_health")
bazarr.save_settings(False, "http://bazarr.test", "tajny-klic")
restore()

print()
print("=== prowlarr ===")
restore = substitute(prowlarr, "ProwlarrClient", _apps.prowlarr_client(_apps.prowlarr_handler(None, {
    "health": [{"source": "IndexerStatusCheck", "type": "warning", "message": WARNING.format(n=n), "wikiUrl": ""}
               for n in range(ROWS)],
})))
_apps.connect_prowlarr()
_apps.fill_prowlarr()                     # "Who asks" is from the database: Radarr, Sonarr and Prowlarr itself
check_lists("prowlarr", fresh_page(prowlarr, "prowlarr"),
            ("prowlarr_indexers_now", "prowlarr_who", "prowlarr_health"))
warnings_of("prowlarr", fresh_page(prowlarr, "prowlarr"), "prowlarr_health")
restore()

print()
print("--- E) at most two cards side by side ---")
for app in ("sonarr", "radarr", "lidarr", "readarr", "bazarr", "prowlarr"):
    template = (PROJECT / "jellyscope" / "templates" / f"arr_{app}.html").read_text(encoding="utf-8")
    bands = [re.findall(r'\{\{ c\("(\w+)"\) \}\}', band)
             for band in re.findall(r'<div class="grid-2[^"]*">(.*?)</div>', template, re.S)]
    called = re.findall(r'\{\{ c\("(\w+)"\) \}\}', template)
    check(bands and all(len(band) <= 2 for band in bands), f"{app}: at most two cards side by side ({[len(b) for b in bands]})")
    check(len(called) == len(set(called)), f"{app}: every card once")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
