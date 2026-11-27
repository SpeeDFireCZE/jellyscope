# -*- coding: utf-8 -*-
"""The one list of the API's addresses (jellyscope/api_endpoints.py).

The rights check, the index /api/v1, the key's tree of rights and the window "How to use it" read the same
list, so the documentation cannot say another address than the check lets through. This test holds:
(a) every route of the API router is in the list and every entry has a route; (b) the 14 addresses of 1.9
keep their areas and (c) their descriptions in the index; (d) every label and section is a translation key in
en, cs and es; (e) a key with chosen areas may exactly the addresses of those areas - an address added later
to another area stays closed to it.

Run:
    .venv/bin/python tests/test_api_endpoints.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "endpoints.db")
os.environ["SECRET_KEY"] = "endpoints-test-key-long-enough-0123456789"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_DEMO"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from _package import flat_routes  # noqa: E402
from jellyscope import api, db, web  # noqa: E402

failures = 0


def check(condition: bool, label: str, detail: str = "") -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label} {detail if not condition else ''}")
    if not condition:
        failures += 1


# The 14 addresses as 1.9.2 had them: the areas and the index's descriptions do not change.
TODAY_AREAS = {'/api/v1': None,
 '/api/v1/': None,
 '/api/v1/libraries': 'library',
 '/api/v1/library': 'library',
 '/api/v1/recently-added': 'library',
 '/api/v1/item/{item_id}': 'library',
 '/api/v1/top-items': 'charts',
 '/api/v1/least-played': 'charts',
 '/api/v1/unwatched': 'charts',
 '/api/v1/insights': 'charts',
 '/api/v1/summary': 'overview',
 '/api/v1/play-methods': 'overview',
 '/api/v1/now-playing': 'now_playing',
 '/api/v1/users': 'users',
 '/api/v1/history': None,
 '/api/v1/bandwidth': 'network'}
TODAY_INDEX = {'/api/v1/summary': 'watching over a period (?days=30)',
 '/api/v1/now-playing': 'what is playing right now',
 '/api/v1/library': 'size of the library, growth and free space',
 '/api/v1/libraries': 'every library: items, size, codecs',
 '/api/v1/top-items': 'most watched titles (?days=30&limit=10&kind=both|movies|series)',
 '/api/v1/least-played': 'least played files, never played first; within the same play count biggest first, '
                         'or by date added with order=oldest|newest '
                         '(?days=365&limit=25&min_size_mb=0&order=size)',
 '/api/v1/unwatched': 'files nobody watched over the period, and how much space they take '
                      '(?days=365&limit=25)',
 '/api/v1/users': 'viewers with watch time over the period (?days=30)',
 '/api/v1/history': 'recent playbacks (?days=30&limit=50&user=<id>&kind=both|movies|series)',
 '/api/v1/recently-added': 'what came into the library last (?limit=18)',
 '/api/v1/play-methods': 'direct play vs. transcode, and the clients used (?days=30)',
 '/api/v1/insights': 'transcode offenders, upgrade candidates, oversized and never finished titles, '
                     'conversion speed in fps (?days=30&limit=15)',
 # Where to only with ...where they watch from (27. 11. 2026, the Network split).
 '/api/v1/bandwidth': 'how much data went out (?days=30); where to - home, internet, countries - only with ...where they watch from',
 '/api/v1/item/{id}': 'one title: file, versions and who watched it'}


db.init_db()
from jellyscope import api_endpoints  # noqa: E402

print("--- (a) the router and the list are the same addresses ---")
routes = {route.path for route in flat_routes(api.router.routes)}
listed = {endpoint.path for endpoint in api_endpoints.ENDPOINTS}
check(routes - set(api_endpoints.INDEX_PATHS) == listed,
      "every route of the API is in the list and every entry has a route",
      f"(only routes: {sorted(routes - listed)}, only listed: {sorted(listed - routes)})")
check(all(api_endpoints.by_path(path) is endpoint for path, endpoint in
          ((e.path, e) for e in api_endpoints.ENDPOINTS)), "by_path finds every entry")
check(api_endpoints.by_path("/api/v1/nothing-here") is None, "by_path of an unknown address is None")

print("--- (b) the areas of the 14 addresses of 1.9 ---")
check({path: api.ADDRESS_AREAS.get(path, "missing") for path in TODAY_AREAS} == TODAY_AREAS,
      "ADDRESS_AREAS keeps every address of 1.9 with its area",
      f"({ {p: api.ADDRESS_AREAS.get(p, 'missing') for p, a in TODAY_AREAS.items() if api.ADDRESS_AREAS.get(p, 'missing') != a} })")
check({p: a for p, a in api.ADDRESS_AREAS.items() if p not in api_endpoints.INDEX_PATHS}
      == {e.path: e.area for e in api_endpoints.ENDPOINTS}, "ADDRESS_AREAS is made from the list (and the index)")

print("--- (c) the index says the same as in 1.9 ---")
everything = api.create_token("all")["token"]
client = TestClient(web.app)
index = client.get("/api/v1", headers={"Authorization": f"Bearer {everything}"}).json()
check({path: index["endpoints"].get(path) for path in TODAY_INDEX} == TODAY_INDEX,
      "the index describes the addresses of 1.9 word for word as before",
      f"({[p for p in TODAY_INDEX if index['endpoints'].get(p) != TODAY_INDEX[p]]})")

print("--- (d) labels and sections are translated ---")
dictionaries = {language: json.loads((PROJECT / "jellyscope" / "translations" / f"{language}.json").read_text(encoding="utf-8"))
                for language in ("en", "cs", "es")}
missing = sorted({(language, key) for endpoint in api_endpoints.ENDPOINTS for key in (endpoint.label, endpoint.section)
                  for language, words in dictionaries.items() if key not in words})
check(not missing, "every label and section is a key in en, cs and es", f"({missing[:6]})")
check(all(endpoint.example.startswith("GET /api/v1") for endpoint in api_endpoints.ENDPOINTS
          if endpoint.path not in ("/api/v1", "/api/v1/")), "every example is a GET of its address")
check([heading for heading, _ in api_endpoints.sections()] == list(dict.fromkeys(e.section for e in api_endpoints.ENDPOINTS)),
      "sections() keep the order of the list")
check(api_endpoints.for_area("library") == [e for e in api_endpoints.ENDPOINTS if e.area == "library"],
      "for_area gives the addresses of one area in the list's order")

print("--- (e) a key with chosen areas may exactly their addresses ---")
chosen = api.create_token("chosen", permissions=api.pack_key_permissions({"library", "charts"}, False, False))["token"]
headers = {"Authorization": f"Bearer {chosen}"}
opened = {path for path in (e.path for e in api_endpoints.ENDPOINTS) if path not in ("/api/v1", "/api/v1/")
          if client.get(path.replace("{item_id}", "nothing"), headers=headers).status_code != 403}
expected = {e.path for e in api_endpoints.ENDPOINTS if e.area in ("library", "charts")}
check(opened == expected, "the key opens exactly the addresses of library and charts",
      f"(opened {sorted(opened - expected)}, closed {sorted(expected - opened)})")
check(set(api.root_key_paths(api.pack_key_permissions({"library", "charts"}, False, False))) ==
      {p.replace("{item_id}", "{id}") for p in expected}, "root_key_paths lists the same addresses")

print("--- (f) the key's tree and the guide window are drawn from the list ---")
import re  # noqa: E402

from jellyscope import accounts  # noqa: E402

accounts.create("admin", "longpassword", is_admin=True)
admin = TestClient(web.app)
admin.post("/login", data={"username": "admin", "password": "longpassword", "method": "local"})
page = admin.get("/settings?section=api").text


def block(html: str, attribute: str) -> str:
    """The content of the element with this data- attribute (to its closing tag of the same name)."""
    m = re.search(r"<(\w+)[^>]*" + re.escape(attribute) + r"[^>]*>(.*?)</\1>", html, re.S)
    return m.group(2) if m else ""


library = block(page, 'data-area-addresses="library"')
check(all(f"<code>{p}</code>" in library for p in ("/api/v1/libraries", "/api/v1/library", "/api/v1/recently-added",
                                                  "/api/v1/item/{id}")),
      "the key's tree lists the four addresses of the Library area", f"({library[:200]!r})")
check(block(page, 'data-area-addresses="charts"').count("<code>") == 4, "and four under Charts")
history = block(page, "data-history-addresses")
check("<code>/api/v1/history</code>" in history, "the tick of the playback history names its addresses", f"({history[:120]!r})")
guide = page[page.index('id="modal-api-docs"'):]
listed_once = all(guide.count(f'data-endpoint="{e.path.replace("{item_id}", "{id}")}"') == 1 for e in api_endpoints.ENDPOINTS)
check(listed_once, "the guide window has every address of the list exactly once")
import html  # noqa: E402

sections_drawn = [html.unescape(x) for x in re.findall(r'data-endpoint-section="([^"]+)"', guide)]
check(sections_drawn == [heading for heading, _ in api_endpoints.sections()],
      "the guide window has a heading per section of the list, in its order", f"({sections_drawn})")
accounts_page = admin.get("/settings?section=accounts").text
check("data-area-addresses" not in accounts_page, "the tree of an account's rights shows no addresses")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
