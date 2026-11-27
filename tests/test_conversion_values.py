# -*- coding: utf-8 -*-
r"""Setting keys, stored values and JSON converted to English names (step 5 of the database spec).

The old shape is written as 1.8.4 and the code before the step write it - the Czech keys and values
spelled out here, not made from the map (a key the map forgot would stay unconverted and the test must
see it). What the application shows of it was measured on that code (EXPECTED_RIGHTS: what every
account and group sees, whether names are hidden, own API keys, the history, the answers of three API
keys); after the conversion this code shows exactly the same, and no old key or value is left.

Run:
    .venv/bin/python tests/test_conversion_values.py
    .venv/bin/python .superpowers/tools/test_on_pg.py tests/test_conversion_values.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "conversion-values.db")
os.environ["SECRET_KEY"] = "conversion-values-test-key-long-enough"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "x"
os.environ["JELLYSCOPE_DEMO"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import access, accounts, api, db, i18n, web  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


def settings_like(pattern: str) -> list[str]:
    return [str(dict(r)["key"]) for r in db.query_all("SELECT key FROM settings WHERE key LIKE ?", (pattern,))]


print("--- permission areas, groups and API keys (database step 5, task 2) ---")
# The old shape: the groups' areas, anonymising and own keys as settings; two readers with own
# permissions (one anonymised, one explicitly not), one by the group; a root key with areas and
# the history, a disabled one, a full one. Written as the code before the step wrote it.
AREAS = ["dashboard", "prehled", "srovnani", "prave_hraje", "zebricky", "jazyky", "hudba", "hudba_kdo", "arr",
         "seerr", "seerr_kdo", "knihovna", "knihovna_kdo", "sit", "uzivatele"]
READER_GROUP = {"dashboard", "prehled", "prave_hraje", "jazyky", "seerr", "knihovna"}
VIEWER_GROUP = {"prehled", "zebricky", "hudba", "hudba_kdo", "knihovna", "knihovna_kdo", "sit"}


def write_old(db, accounts, api):
    """Returns {name: account id}, {name: token}."""
    def sql(statement, params=()):
        with db.connect() as conn:
            conn.execute(statement, params)
            conn.commit()
    for who, enabled in (("ctenar", READER_GROUP), ("divak", VIEWER_GROUP)):
        for area in AREAS:
            sql("DELETE FROM settings WHERE key = ?", (f"{who}_vidi_{area}",))
            sql("INSERT INTO settings (key, value) VALUES (?, ?)", (f"{who}_vidi_{area}", "1" if area in enabled else "0"))
    for key, value in (("ctenar_anonymizace", "1"), ("divak_anonymizace", "0"), ("ctenar_smi_klic", "1"), ("divak_smi_klic", "0")):
        sql("DELETE FROM settings WHERE key = ?", (key,))
        sql("INSERT INTO settings (key, value) VALUES (?, ?)", (key, value))
    ids = {name: accounts.create(name, "rights-test-pw") for name in ("reader_own", "reader_open", "reader_group")}
    column = "permissions"
    with db.connect() as conn:
        if "permissions" not in conn.table_columns("accounts"):
            column = "pristup"
    sql(f"UPDATE accounts SET {column} = ? WHERE id = ?",
        (json.dumps({"oblasti": ["prehled", "knihovna", "knihovna_kdo", "dashboard"], "anonymizace": True}), ids["reader_own"]))
    sql(f"UPDATE accounts SET {column} = ? WHERE id = ?",
        (json.dumps({"oblasti": ["prehled", "sit", "uzivatele", "zebricky"], "anonymizace": False}), ids["reader_open"]))
    key_column = "may_have_key" if column == "permissions" else "smi_klic"
    sql(f"UPDATE accounts SET {key_column} = '0' WHERE id = ?", (ids["reader_own"],))
    tokens = {"full": api.create_token("full")["token"]}
    limited = api.create_token("limited")
    tokens["limited"] = limited["token"]
    perm_column, status_column = ("permissions", "status") if column == "permissions" else ("prava", "stav")
    sql(f"UPDATE api_tokens SET {perm_column} = ? WHERE id = ?",
        (json.dumps({"oblasti": ["zebricky", "knihovna"], "anonymizace": False, "historie": True}), limited["id"]))
    disabled = api.create_token("disabled")
    tokens["disabled"] = disabled["token"]
    sql(f"UPDATE api_tokens SET {status_column} = 'vypnuty' WHERE id = ?", (disabled["id"],))
    return ids, tokens


PATHS = ["/api/v1/top-items", "/api/v1/libraries", "/api/v1/users", "/api/v1/history", "/api/v1/now-playing", "/api/v1/languages"]

# The expectations below were measured on the code before the step 5; the dicts that code computed had Czech keys,
# renamed in the step 6 (tests/conversion_step6_keys.json) - the values are what is compared.
STEP6_KEYS = json.loads((PROJECT / "tests/conversion_step6_keys.json").read_text(encoding="utf-8"))


def in_step6_keys(value: Any) -> Any:
    if isinstance(value, dict):
        return {STEP6_KEYS.get(k, k): in_step6_keys(v) for k, v in value.items()}
    if isinstance(value, list):
        return [in_step6_keys(v) for v in value]
    return value


# The areas that existed when the expectations below were measured (the Books areas came later: pinned separately).
BOOKS_AREAS = ("books", "books_comparison", "books_who", "books_insights")
OLD_AREAS = [a.key for a in access.AREAS if a.key not in BOOKS_AREAS and a.key != "network_origin"]
# Measured on the code before the step over this state (areas in the order of access.AREAS without the Books areas).
# /api/v1/languages (the last of PATHS) was a 404 until 27. 11. 2026: now the key with everything opens it, the
# limited one (charts and the library) and the disabled one do not.
EXPECTED_RIGHTS = json.loads('{"reader_own": {"sees": [true, true, false, false, false, false, false, false, false, false, false, false, false, true, true, false, false], "hides": true, "key": false, "history": true}, "reader_open": {"sees": [false, true, false, false, true, false, false, false, false, false, false, false, false, false, false, true, true], "hides": false, "key": true, "history": false}, "reader_group": {"sees": [true, true, false, true, false, true, false, false, false, false, false, true, false, true, false, false, false], "hides": true, "key": true, "history": false}, "viewer": {"sees": [false, true, false, false, true, false, true, true, true, true, false, false, false, true, true, true, false], "hides": false, "key": false, "history": true}, "api full": [200, 200, 200, 200, 200, 200], "api limited": [200, 200, 403, 200, 403, 403], "api disabled": [403, 403, 403, 403, 403, 403]}')

db.init_db()
ids, tokens = write_old(db, accounts, api)
# An account's own key the admin disabled - stored by the code before the step as "vypnuty".
own_key = api.create_token("own disabled", account_id=ids["reader_open"])
with db.connect() as conn:
    conn.execute("UPDATE api_tokens SET status = 'vypnuty' WHERE id = ?", (own_key["id"],))
    conn.commit()
check(len(settings_like("ctenar_vidi_%")) == 15 and settings_like("ctenar_anonymizace") == ["ctenar_anonymizace"],
      "the old shape: the groups' keys under their old names")
db.init_db()
db.forget_settings()
viewer = accounts.from_jellyfin({"id": "aa11bb22cc33dd44ee55ff6677889900", "name": "Divak Jedna", "image": "", "is_admin": False})
client = TestClient(web.app)
# The start of the app converts what the database conversion leaves: Music's comparison and insights (22. 11. 2026)
# follow "music" - so the expectations hold the value of "music" in both new areas.
access.split_music_rights()
# Books' readers' names and comparison follow Music's stored values (23. 11. 2026), once.
access.split_books_rights()
seen: dict[str, Any] = {}
for name, account_id in {**ids, "viewer": viewer["id"]}.items():
    account = accounts.get(account_id)
    seen[name] = {"sees": [access.account_sees(account, a.key) for a in access.AREAS if a.key in OLD_AREAS], "hides": access.hides_names(account),
                  "key": access.may_have_key(account), "history": access.sees_history(account)}
for name, token in tokens.items():
    seen[f"api {name}"] = [client.get(p, headers={"Authorization": f"Bearer {token}"}).status_code for p in PATHS]
books_seen = {name: {key: access.account_sees(accounts.get(account_id), key) for key in BOOKS_AREAS}
              for name, account_id in {**ids, "viewer": viewer["id"]}.items()}
# The Books areas, pinned: an account with its own list gains nothing; the stored group has music_who and
# music_comparison off, so books_who and books_comparison are off too (copied by split_books_rights), books and
# books_insights stay at their defaults; the viewer group had music_who and music_comparison on, so both are copied on.
EXPECTED_BOOKS = {
    "reader_own": dict.fromkeys(BOOKS_AREAS, False), "reader_open": dict.fromkeys(BOOKS_AREAS, False),
    "reader_group": {"books": True, "books_comparison": False, "books_who": False, "books_insights": True},
    "viewer": {"books": True, "books_comparison": True, "books_who": True, "books_insights": True}}
check(all(access.account_sees(accounts.get(account_id), m) == books_seen[name][b]
          for name, account_id in {"reader_group": ids["reader_group"], "viewer": viewer["id"]}.items()
          for m, b in (("music_who", "books_who"), ("music_comparison", "books_comparison"))),
      "and they equal the group's Music values they were copied from")
check(books_seen == EXPECTED_BOOKS, f"the Books areas of every account and group, pinned ({books_seen})")
check(seen == EXPECTED_RIGHTS, f"every account, group and key sees exactly what it did ({[k for k in EXPECTED_RIGHTS if seen.get(k) != EXPECTED_RIGHTS[k]]})")

old_keys = settings_like("%_vidi_%") + settings_like("%anonymizace%") + settings_like("%smi_klic%")
new_keys = settings_like("reader_sees_%") + settings_like("viewer_sees_%")
# 15 areas of each group from the old shape, the start's conversion of Music wrote its two new areas for both and
# the one of Books (23. 11. 2026) copied music_who and music_comparison into the two Books areas of both groups.
check(not old_keys and len(new_keys) == 38 and db.get_setting("reader_anonymise") == "1"
      and db.get_setting("viewer_anonymise") == "0" and db.get_setting("reader_may_have_key") == "1",
      f"the groups' settings under the new keys ({old_keys[:3]}, {len(new_keys)})")
own = {dict(r)["username"]: json.loads(dict(r)["permissions"]) for r in db.query_all(
    "SELECT username, permissions FROM accounts WHERE permissions IS NOT NULL")}
check(own.get("reader_own") == {"areas": ["overview", "library", "library_who", "dashboard"], "anonymise": True}
      and own.get("reader_open") == {"areas": ["overview", "network", "users", "charts"], "anonymise": False},
      f"the accounts' own permissions in English, anonymising kept ({own})")
keys = {dict(r)["name"]: (dict(r)["permissions"], dict(r)["status"]) for r in db.query_all("SELECT name, permissions, status FROM api_tokens")}
check(json.loads(keys["limited"][0]) == {"areas": ["charts", "library"], "anonymise": False, "history": True}
      and keys["disabled"][1] == "disabled" and keys["full"] == (None, "active"),
      f"the API keys' rights and states in English ({keys})")
api_admin = accounts.create("api_page_admin", "rights-test-pw", is_admin=True)
page_client = TestClient(web.app)
page_client.post("/login", data={"username": "api_page_admin", "password": "rights-test-pw", "method": "local"})
api_page = page_client.get("/settings?section=api").text
disabled_row = next((r for r in re.findall(r"<tr>.*?</tr>", api_page, flags=re.S) if ">disabled<" in r), "")
check('action="/settings/api/token/enable"' in disabled_row and "badge warning" in disabled_row
      and 'action="/settings/api/token/disable"' not in disabled_row,
      "Settings -> API shows the disabled key as disabled, with Enable")
reader_client = TestClient(web.app)
reader_client.post("/login", data={"username": "reader_open", "password": "rights-test-pw", "method": "local"})
own_page = reader_client.get("/account").text
own_row = next((r for r in re.findall(r"<tr>.*?</tr>", own_page, flags=re.S) if "own disabled" in r), "")
check(i18n.translate("The admin disabled it. Only the admin can enable it again.") in own_row and "badge good" not in own_row,
      "My account shows the own key the admin disabled as disabled")


print("--- core cards and widths (database step 5, task 3) ---")
from jellyscope import cards  # noqa: E402

CORE_CARDS = {
    "prave_se_hraje": "now_playing", "nedavno_pridane": "recently_added", "souhrn": "summary", "dlazdice": "tiles",
    "sledovanost_po_dnech": "watching_by_day", "nejsledovanejsi": "most_watched", "nejaktivnejsi_uzivatele": "most_active_users",
    "doruceni": "delivery", "prehravace": "players", "kdy_se_sleduje": "when_watched", "zivy_tok": "live_traffic",
    "spicky_po_dnech": "peaks_by_day", "kdo_streamoval": "who_streamed", "podle_prehravace": "by_player", "odkud": "origin",
    "pomer_jazyku": "language_share", "dabing": "dubbing", "titulky": "subtitles", "jazyky_knihovny": "library_languages",
    "knihovna_celkem": "library_total", "disky": "disks", "rust_knihovny": "library_growth", "kodeky": "codecs",
    "rozliseni": "resolutions", "dynamicky_rozsah": "dynamic_range", "zj_nevyuzite": "insight_unused",
    "zj_transcodovane": "insight_transcoded", "zj_duvody": "insight_reasons", "zj_rychlost": "insight_speed",
    "zj_kvalita": "insight_quality", "zj_velke": "insight_large", "zj_knihovny": "insight_libraries",
    "zj_duplicity": "insight_duplicates", "zj_odlozene": "insight_postponed", "kdo_jazyk": "who_language",
    "kombinace": "combinations", "chybi_stopa": "missing_track", "uzivatele_graf": "users_chart",
    "uzivatele_tabulka": "users_table", "knihovny": "libraries", "sit_cisla": "network_numbers", "mapa": "map"}
WIDTHS = {"tretina": "third", "pul": "half", "cela": "full"}
cards_admin = accounts.create("cards_admin", "rights-test-pw", is_admin=True)
OLD_WIDTH = ["tretina", "pul", "cela", None]
OWN = list(CORE_CARDS)[:5][::-1]
with db.connect() as conn:
    conn.execute("DELETE FROM dashboard_layout")
    for position, key in enumerate(CORE_CARDS):
        conn.execute("INSERT INTO dashboard_layout (account_id, card_key, position, width) VALUES (NULL, ?, ?, ?)",
                     (key, position, OLD_WIDTH[position % 4]))
    for position, key in enumerate(OWN):
        conn.execute("INSERT INTO dashboard_layout (account_id, card_key, position, width) VALUES (?, ?, ?, 'pul')",
                     (cards_admin, key, position))
    conn.commit()
db.init_db()
db.forget_settings()
expected_shared = [(new, WIDTHS[OLD_WIDTH[i % 4]] if OLD_WIDTH[i % 4] else cards.BY_KEY[new].width if new in cards.BY_KEY else None)
                   for i, new in enumerate(CORE_CARDS.values())]
shared = [(card.key, card.width) for card in cards.load_layout()]
check(shared == expected_shared, f"the shared layout: all 42 cards in their order with their widths "
                                 f"({[x for x in zip(shared, expected_shared) if x[0] != x[1]][:3]}, {len(shared)})")
check([(card.key, card.width) for card in cards.load_layout(cards_admin)] == [(CORE_CARDS[k], "half") for k in OWN],
      "an own layout too")
stored = {(dict(r)["card_key"], dict(r)["width"]) for r in db.query_all("SELECT card_key, width FROM dashboard_layout")}
check(not {k for k, _ in stored} & set(CORE_CARDS) and not {w for _, w in stored} & set(WIDTHS),
      "no old card key and no old width left")
check(sorted(cards.WIDTHS) == ["full", "half", "third"] and all(card.width in cards.WIDTHS for card in cards.ALL_CARDS),
      f"the registry's widths and every card's width in English ({sorted(cards.WIDTHS)})")
db.set_setting(cards.ENABLED_KEY, "1")
db.forget_settings()
admin_client = TestClient(web.app)
admin_client.post("/login", data={"username": "cards_admin", "password": "rights-test-pw", "method": "local"})
page = admin_client.get("/dashboard").text
check('data-card="now_playing"' in page and "dash-half" in page and "dash-third" in page and "dash-full" in page
      and "dash-pul" not in page, "the dashboard draws the cards with the new keys and width classes")
# A dashboard editor still open from before the step sends the old keys and widths: they are saved under the new ones.
saved = admin_client.post("/dashboard/layout", data={"order": "prave_se_hraje:cela,souhrn:tretina,codecs:pul"},
                          follow_redirects=False)
layout_now = [(dict(r)["card_key"], dict(r)["width"]) for r in db.query_all(
    "SELECT card_key, width FROM dashboard_layout WHERE account_id IS NULL ORDER BY position")]
check(saved.status_code == 303 and layout_now == [("now_playing", "full"), ("summary", "third"), ("codecs", "half")],
      f"an old editor's layout is saved with the new keys and widths ({layout_now})")
shown = re.findall(r'show_card\("(\w+)"\)', (PROJECT / "jellyscope" / "templates" / "video" / "overview.html").read_text(encoding="utf-8"))
check(shown == list(cards.OVERVIEW) and shown[:3] == ["now_playing", "recently_added", "summary"] and set(shown) <= set(cards.BY_KEY),
      f"the Overview page asks for its cards by the new keys, in the registry's order ({shown[:3]})")

print("--- disks, library folders and the detailed map (database step 5, task 4) ---")
from datetime import datetime as _datetime, timedelta as _timedelta  # noqa: E402

import _old_disks  # noqa: E402

from jellyscope import detailed_map, formatting, scanner, stats  # noqa: E402

# Measured on the code before the step over this state (tests/_old_disks.py): the disks' fingerprints, the rows
# of the Disks card (kind, source, total, free, library, outside), the pace and the forecast of each disk.
EXPECTED_DISKS = json.loads('{"prints": {"dev-a": "ff34bc1e2b5e", "dev-b": "fcf7cda7c06a", "virt:v1": "eeacc580a0aa"}, "rows": {"dev-a": ["fyzicky", "jellyfin", 1073741824000, 536870912000, 42949672960, 493921239040], "dev-b": ["fyzicky", "rucne", 2199023255552, 2134598746112, 64424509440, null], "virt:v1": ["virtualni", "rucne", 1099511627776, 992137445376, 107374182400, null]}, "pace": {"dev-a": [5368709120.0, 100], "dev-b": [5368709120.0, 397], "virt:v1": [5368709120.0, 184]}, "first": {"klic": "dev-a", "nazev": "Filmy", "volne": 515396075520, "denne": 5368709120.0, "dnu_do_konce": 96, "disku": 3}}')
KINDS, SOURCES = {"fyzicky": "physical", "virtualni": "virtual"}, {"rucne": "manual", "jellyfin": "jellyfin"}
with db.connect() as conn:
    for table in ("items", "library_disk_snapshot"):
        conn.execute(f"DELETE FROM {table}")
    conn.commit()
# A database of 1.8.x has none of the new keys (the sections above wrote some through this code).
with db.connect() as conn:
    conn.execute("DELETE FROM settings WHERE key IN ('jf_library_folders', 'jf_library_folders_at', 'disks_capacity',"
                 " 'disks_virtual', 'disks_converted', 'disks_same', 'disks_different', 'free_space_source', 'ui_map_base',"
                 " 'detailed_map_at', 'detailed_map_version', 'detailed_map_format', 'detailed_map_tiles', 'detailed_map_bytes')")
    conn.commit()
_old_disks.write_old_settings(db, detailed_map.FORMAT)
_old_disks.write_items(db)
TODAY = _datetime.now(formatting.zone()).date()
_old_disks.write_old_snapshots(db, [(TODAY - _timedelta(days=d)).isoformat() for d in (8, 4)], EXPECTED_DISKS["prints"])
db.init_db()
db.forget_settings()
overview = stats.disks_overview(30)
rows = {r["key"]: [r["kind"], r["source"], r["total"], r["free"], r["library"], r["outside"]] for r in overview["disks"]}
expected_rows = {k: [KINDS[v[0]], SOURCES[v[1]], *v[2:]] for k, v in EXPECTED_DISKS["rows"].items()}
check(rows == expected_rows, f"the Disks card: the same disks, capacities and space, the kinds and sources in English "
                             f"({[k for k in expected_rows if rows.get(k) != expected_rows[k]]})")
check({r["key"]: [r["daily"], r["days_left"]] for r in overview["disks"]} == EXPECTED_DISKS["pace"]
      and json.loads(json.dumps(stats.runs_out_first(30))) == in_step6_keys(EXPECTED_DISKS["first"]),
      "the pace and the forecast go on over the converted days (the same fingerprints)")
check({r["key"]: r["fingerprint"] for r in scanner.disk_state()["disks"]} == EXPECTED_DISKS["prints"],
      "the fingerprints of the disks' settings are the same as before the conversion")
scanner.write_disk_snapshot(TODAY.isoformat())
today_prints = {str(dict(r)["disk"]): str(dict(r)["fingerprint"]) for r in db.query_all(
    "SELECT disk, fingerprint FROM library_disk_snapshot WHERE day = ?", (TODAY.isoformat(),))}
check(today_prints == EXPECTED_DISKS["prints"], f"the next snapshot carries them on ({today_prints})")
stored = {(str(dict(r)["kind"]), str(dict(r)["source"]), str(dict(r)["disk"])) for r in db.query_all(
    "SELECT kind, source, disk FROM library_disk_snapshot")}
check({k for k, _, _ in stored} <= {"physical", "virtual", "single"} and {s for _, s, _ in stored} <= {"manual", "jellyfin", "disk", ""}
      and "knihovna" not in {d for _, _, d in stored} and "library" in {d for _, _, d in stored},
      f"the snapshots' kinds, sources and Jellyfin 10.x's disk key in English ({sorted(stored)[:4]})")
virtual = scanner.virtual_disks()
check([(v["id"], v["paths"], v["capacity"]) for v in virtual] == [("v1", ["/media/serialy/cloud"], 1024 ** 4)],
      f"the virtual disk with its paths and capacity ({virtual})")
check(not settings_like("disky_%") and not settings_like("jf_slozky_%") and not settings_like("mapa_podrobna_%")
      and not settings_like("volne_misto_zdroj") and db.get_setting("free_space_source") == "manual"
      and db.get_setting("ui_map_base") == "osm" and "zdroj" not in (db.get_setting("disks_capacity") or ""),
      f"the settings of disks, folders and the map under the new keys, their JSON in English "
      f"({settings_like('disky_%') + settings_like('jf_slozky_%') + settings_like('mapa_podrobna_%')}, "
      f"{db.get_setting('volne_misto_zdroj')!r}, {db.get_setting('free_space_source')!r}, {db.get_setting('ui_map_base')!r}, "
      f"{db.get_setting('disks_capacity')!r})")
check(detailed_map.state()["valid"] and json.loads(detailed_map.tile(0, 0, 0) or "{}")
      == {"land": "M0 0L9 9", "lakes": "M1 1", "cities": "", "rivers": "M2 2", "borders": "M3 3", "regions": ""},
      "the downloaded map stays valid, its tiles hold the same layers under the new names (no download again)")

print("--- notifications (database step 5, task 5) ---")
import asyncio  # noqa: E402
import logging  # noqa: E402
from datetime import datetime as _when  # noqa: E402

import _old_notifications  # noqa: E402

from jellyscope import notifications  # noqa: E402

# Measured on the code before the step over this state (tests/_old_notifications.py): the channels and events
# in their order, the five messages' subjects and texts (own and default), the texts on the Settings page,
# whether a change is reported (the collector's state was "bad" already), the summary's day and the page.
EXPECTED_NOTIFY = json.loads('{"channels": [true, true, false], "events": [true, false, true], "messages": [["Sběrač stojí 12 min", "Vlastní text: timeout"], ["Jellyscope: the collector is collecting again", "The collector is back and history is being recorded again."], ["Jellyscope: running out of space", "x"], ["Jellyscope: enough space again", "The disk-full forecast is back above the threshold you set."], ["Týden v Jellyscope", ""]], "texts": [["Sběrač stojí {minutes} min", "Vlastní text: {error}"], ["", ""], ["", ""], ["", ""], ["Týden v Jellyscope", ""]], "report": [false, true, ["Jellyscope: running out of space"]], "summary_due": [true, false], "page": [true, false, true, true]}')
_old_notifications.write_old(db)


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


capture = _Capture()
app_log = logging.getLogger("jellyscope")
level = app_log.level
app_log.setLevel(logging.DEBUG)
app_log.addHandler(capture)
db.init_db()
app_log.removeHandler(capture)
app_log.setLevel(level)
db.forget_settings()
values = {"minutes": "12", "threshold": "45", "error": "timeout", "disk": "dev-a", "days": "9", "free": "1 GB"}
sent: list[str] = []


async def _fake_send(subject: str, text: str, **kw: Any) -> list[dict[str, Any]]:
    sent.append(subject)
    return [{"channel": "test", "ok": True, "error": ""}]       # delivered: only then the new state is stored

real_send, notifications.send = notifications.send, _fake_send
seen_notify = {
    "channels": [notifications.channel_enabled(c) for c in notifications.CHANNELS],
    "events": [notifications.event_enabled(e) for e in notifications.EVENTS],
    "messages": [list(notifications.message_text(m.key, detail="x", **values)) for m in notifications.MESSAGES],
    "texts": [[t["subject"], t["text"]] for t in notifications.texts_for_settings()],
}
first = asyncio.run(notifications._report_change(notifications.EVENTS[0], True, "x", **values))
second = asyncio.run(notifications._report_change(notifications.EVENTS[1], True, "x", **values))
notifications.send = real_send
# _report_change answers "" (nothing changed), "sent" or "failed" since 24. 11. 2026; what was sent is the same.
seen_notify["report"] = [bool(first), bool(second), sent]
seen_notify["summary_due"] = [notifications._summary_due(_when(2026, 11, 25, 9, 0)), notifications._summary_due(_when(2026, 11, 26, 9, 0))]
page = admin_client.get("/settings?section=notifications").text
seen_notify["page"] = ["Sběrač stojí {minutes} min" in page, _old_notifications.PASSWORD in page, "smtp.test" in page, "admin@test" in page]
check(seen_notify == EXPECTED_NOTIFY, f"the notifications send and show exactly what they did "
                                      f"({[(k, seen_notify.get(k)) for k in EXPECTED_NOTIFY if seen_notify.get(k) != EXPECTED_NOTIFY[k]]})")
check(list(notifications.EVENTS) == ["collector", "space", "summary"]
      and [m.key for m in notifications.MESSAGES] == ["collector_down", "collector_back", "space_low", "space_ok", "summary"],
      "the events and the messages by their English names")
UNCHANGED = {"notify_smtp_enabled", "notify_smtp_host", "notify_smtp_port", "notify_smtp_tls", "notify_discord_enabled",
             "notify_discord_webhook"}
left = [k for k in _old_notifications.OLD if k not in UNCHANGED and settings_like(k)]
check(not left and db.get_setting("notify_smtp_password") == _old_notifications.PASSWORD
      and db.get_setting("notify_state_collector") == "bad" and db.get_setting("notify_text_collector_down_subject")
      == "Sběrač stojí {minutes} min" and db.get_setting("notify_summary_time") == "08:30"
      and db.get_setting("task_notifications_enabled") == "1", f"the settings under the new keys, the states in English ({left[:4]})")
kinds = {str(dict(r)["kind"]) for r in db.query_all("SELECT DISTINCT kind FROM scan_log")}
check("notifications" in kinds and "notifikace" not in kinds, f"the task's log under its new kind ({sorted(kinds)})")
check(not any(_old_notifications.PASSWORD in line for line in capture.lines) and capture.lines,
      f"the SMTP password is in no line the conversion logs ({len(capture.lines)} lines)")

print("--- the log rotation and the look of the pages (database step 5, task 5) ---")
from jellyscope import applog, seasonal  # noqa: E402
# The key of devtools.DATE_KEY written out: devtools stays in development (it is not in the released code).
from jellyscope.web import base as web_base  # noqa: E402

LOOK = {"logrotate_dni": "9", "logrotate_pocet": "4", "_logrotate_naposledy": "2026-11-20", "ui_cas_presne": "1",
        "ui_udalosti": "0", "ui_skin": "klasicky", "dashboard_nazev": "Naše nástěnka", "vyvoj_udalost_datum": "2026-12-24"}
with db.connect() as conn:
    for key, value in LOOK.items():
        conn.execute("DELETE FROM settings WHERE key = ?", (key,))
        conn.execute("INSERT INTO settings (key, value) VALUES (?, ?)", (key, value))
    for key in ("logrotate_days", "logrotate_count", "_logrotate_last", "ui_exact_time", "ui_events", "dashboard_name",
                "dev_event_date"):
        conn.execute("DELETE FROM settings WHERE key = ?", (key,))
    conn.commit()
db.init_db()
db.forget_settings()
check(db.get_setting(applog.DAYS_KEY) == "9" and db.get_setting(applog.COUNT_KEY) == "4"
      and db.get_setting(applog.LAST_ROTATION_KEY) == "2026-11-20", "the log rotation keeps its days, count and last time")
check(formatting.exact_time() is True, "the exact time stays on")
check(seasonal.enabled() is False and web_base._skin() == "classic" and db.get_setting(cards.NAME_KEY) == "Naše nástěnka"
      and db.get_setting("dev_event_date") == "2026-12-24", "the decorations off, the classic skin, the name, the date")
page = admin_client.get("/settings?section=interface").text
check('data-skin="classic"' in page and 'value="classic"' in page and "Naše nástěnka" in page,
      "the pages draw the classic skin and the Settings show the choices")
check(not [k for k in LOOK if settings_like(k) and k != "ui_skin"], "no old key left")

print("--- where a stream's language comes from (database step 6) ---")
from jellyscope import i18n as _i18n  # noqa: E402

with db.connect() as conn:
    conn.execute("DELETE FROM item_streams WHERE item_id IN ('ls-1', 'ls-2', 'ls-3')")
    conn.execute("DELETE FROM items WHERE id IN ('ls-1', 'ls-2', 'ls-3')")
    for item_id in ("ls-1", "ls-2", "ls-3"):
        conn.execute("INSERT INTO items (id, name, type, is_missing, synced_at) VALUES (?, ?, 'Movie', 0, '2026-11-20 10:00:00')",
                     (item_id, f"Stream {item_id}"))
    # As the code before the step stored it: from the file name, nothing found by Jellyfin, filled in from Jellyfin.
    conn.execute("INSERT INTO item_streams (item_id, stream_index, type, language, language_source) VALUES"
                 " ('ls-1', 1, 'Audio', 'cs', 'nazev'), ('ls-2', 1, 'Audio', 'und', 'neznamy'), ('ls-3', 1, 'Audio', 'en', 'jellyfin')")
    conn.commit()
db.init_db()
sources = {dict(r)["item_id"]: dict(r)["language_source"] for r in db.query_all(
    "SELECT item_id, language_source FROM item_streams WHERE item_id IN ('ls-1', 'ls-2', 'ls-3')")}
check(sources == {"ls-1": "name", "ls-2": "unknown", "ls-3": "jellyfin"}, f"the sources of the languages in English ({sources})")
detail = admin_client.get("/item/ls-1").text
check(_i18n.translate("guessed from the name") in detail, "the item's page still marks the language guessed from the name")

print("--- the states of syncs, the map's task and the forgettings (database step 5, task 5) ---")
from jellyscope import purge, services  # noqa: E402

STATES = {
    "seerr_state": {"kdy": "2026-11-21 10:00:00", "vysledek": "chyba", "novych": 0, "chyba": "Sync failed: timeout",
                    "sablona": "Sync failed: {reason}", "hodnoty": {"reason": "timeout"}},
    "arr_sonarr_state": {"kdy": "2026-11-21 11:00:00", "vysledek": "ok", "novych": 5, "chyba": "", "sablona": "", "hodnoty": {}},
    "music_state": {"kdy": "2026-11-21 12:00:00", "vysledek": "ok", "novych": 2, "chyba": "", "sablona": "", "hodnoty": {}},
}
with db.connect() as conn:
    for key, state in STATES.items():
        conn.execute("DELETE FROM settings WHERE key = ?", (key,))
        conn.execute("INSERT INTO settings (key, value) VALUES (?, ?)", (key, json.dumps(state)))
    conn.execute("DELETE FROM scan_log WHERE kind IN ('mapa', 'map')")
    conn.execute("INSERT INTO scan_log (kind, started_at, finished_at, status, message)"
                 " VALUES ('mapa', '2026-11-20 10:00:00', '2026-11-20 10:05:00', 'done', 'ok')")
    conn.execute("DELETE FROM forgettings")
    conn.execute("INSERT INTO forgettings (user_id, name, session_count, forgotten_at, backup, status)"
                 " VALUES ('u-bin', 'In the bin', 3, '2026-11-21 10:00:00', '', 'kos'),"
                 " ('u-backup', 'In a backup', 4, '2026-11-20 10:00:00', 'jellyscope-x.db', 'zaloha')")
    conn.commit()
db.init_db()
db.forget_settings()
seerr_state = services.read_state("seerr_state")
check(seerr_state == {"at": "2026-11-21 10:00:00", "result": "error", "new_count": 0,
                      "error": services.ServiceError("Sync failed: {reason}", reason="timeout").translated()},
      f"a failed sync's state: when, the result, the error from its template ({seerr_state})")
check(services.read_state("arr_sonarr_state") == {"at": "2026-11-21 11:00:00", "result": "ok", "new_count": 5, "error": ""}
      and services.read_state("music_state")["new_count"] == 2, "a good one with its count")
stored_states = {k: json.loads(db.get_setting(k) or "{}") for k in STATES}
check(all(set(v) == {"at", "result", "new_count", "error", "template", "values"} for v in stored_states.values()),
      "the states stored with the English keys")
kinds = {str(dict(r)["kind"]) for r in db.query_all("SELECT DISTINCT kind FROM scan_log")}
check("map" in kinds and "mapa" not in kinds, f"the map's task log under its new kind ({sorted(kinds)})")
statuses = {str(dict(r)["user_id"]): str(dict(r)["status"]) for r in db.query_all("SELECT user_id, status FROM forgettings")}
check(statuses == {"u-bin": "bin", "u-backup": "backup"}
      and {f["user_id"]: f["in_bin"] for f in purge.forgettings(False)} == {"u-bin": True},
      f"the forgettings' states in English, the one in the bin still is ({statuses})")
print()
print("--- an old default of a column is never relied on ---")
# The conversion renames stored values (schema_names.VALUES), but not a column's DEFAULT: SQLite cannot change it
# without rebuilding the table (and a rebuild is what loses keys). A converted database so keeps the old word as
# the default (api_tokens.status 'aktivni', forgettings.status 'kos' - found again on the user's database of 1.8.4,
# 22. 11. 2026) and an INSERT without the column would write it. Every INSERT into such a table names the column.
import ast  # noqa: E402

from jellyscope import schema_names  # noqa: E402

schema_text = (PROJECT / "jellyscope" / "schema.sql").read_text(encoding="utf-8")


def default_of(table: str, column: str) -> str | None:
    """The column's text DEFAULT in the schema, or in db.MIGRATIONS (api_tokens.status is added there)."""
    block = re.search(rf"CREATE TABLE (?:IF NOT EXISTS )?{table}\s*\((.*?)\n\);", schema_text, re.S)
    found = re.search(rf"^\s*{column}\s+[^\n]*?DEFAULT\s+'([^']*)'", block.group(1), re.M) if block else None
    found = found or re.search(r"DEFAULT\s+'([^']*)'", db.MIGRATIONS.get(table, {}).get(column, ""))
    return found.group(1) if found else None


stale = sorted((table, column) for (table, column), words in schema_names.VALUES.items()
               if default_of(table, column) in set(words.values()))
INSERT = re.compile(r"INSERT\s+(?:OR\s+\w+\s+)?INTO\s+(\w+)\s*(\(([^)]*)\))?", re.I)


def inserts_without(source: str, where: str) -> tuple[int, list[str]]:
    """How many INSERTs into a stale table the source has, and those that do not name the column."""
    seen, missing = 0, []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            for match in INSERT.finditer(node.value):
                for table, column in stale:
                    if match.group(1).lower() == table:
                        seen += 1
                        if column not in re.findall(r"\w+", match.group(3) or ""):
                            missing.append(f"{where}:{node.lineno} INSERT INTO {table} without {column}")
    return seen, missing


checked, without = 0, []
for path in sorted((PROJECT / "jellyscope").rglob("*.py")):
    seen, missing = inserts_without(path.read_text(encoding="utf-8"), str(path.relative_to(PROJECT)))
    checked, without = checked + seen, without + missing
check({("api_tokens", "status"), ("forgettings", "status")} <= set(stale),
      f"the columns whose default the conversion leaves old are known ({stale})")
check(checked >= 2 and not without, f"every INSERT into them names the column ({checked} checked, {without})")
check(inserts_without('run("INSERT INTO forgettings (user_id, name) VALUES (?, ?)")', "sample")[1] != [],
      "and an INSERT without it is found (the guard can fail)")


print("--- the sub-area of Network after the conversions above (27. 11. 2026) ---")
# Where people watch from got a sub-area of Network (27. 11. 2026): whoever saw Network sees it too, once.
api.drop_unusable_key_areas()
access.split_network_rights()
api.add_network_origin_to_keys()
db.forget_settings()
everyone = {**ids, "viewer": viewer["id"]}
check(all(access.account_sees(accounts.get(i), "network_origin") == access.account_sees(accounts.get(i), "network")
          for i in everyone.values()) and any(access.account_sees(accounts.get(i), "network") for i in everyone.values()),
      "where they watch from: exactly whoever saw Network (someone did)")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
