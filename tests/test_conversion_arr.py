# -*- coding: utf-8 -*-
r"""The *Arr apps (Sonarr, Radarr, Bazarr, Prowlarr) converted to English names end to end (step 2 of the
database spec).

The old shape - the apps' tables with Czech columns and indexes, their setting keys (enabled, state,
sync marks), stored kinds and card keys in a dashboard layout - is made from today's schema by the map
turned around (jellyscope/schema_names.py, new → old). init_db then converts it: the same rows under the
new names, the keys renamed, an unknown card left alone, the pages work, and the next sync downloads
nothing twice.

Run:
    .venv/bin/python tests/test_conversion_arr.py
    .venv/bin/python .superpowers/tools/test_on_pg.py tests/test_conversion_arr.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "conversion-arr.db")
os.environ["SECRET_KEY"] = "conversion-arr-test-key-long-enough"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "x"
os.environ["JELLYSCOPE_DEMO"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, conversion, db, schema_names, services, web  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


def run(sql: str, params: tuple = ()) -> None:
    with db.connect() as conn:
        conn.execute(sql, params)


def setting(key: str) -> str:
    """Straight from the table: db.get_setting keeps a copy in memory, and the test writes past it."""
    row = db.query_one("SELECT value FROM settings WHERE key = ?", (key,))
    return "" if row is None else str(dict(row)["value"])


def tables() -> set[str]:
    with db.connect() as conn:
        return conversion._tables(conn)


def old_tables(apps: tuple[str, ...]) -> None:
    """The apps' tables as a database before the conversion has them: today's tables, their columns and indexes
    renamed back by the map; the layouts of dashboards too (their columns were Czech until step 4)."""
    with db.connect() as conn:
        for table in (t for t in schema_names.TABLES if any(app in t.new for app in apps) or t.new == "dashboard_layout"):
            if table.old != table.new:
                conn.execute(f"ALTER TABLE {table.new} RENAME TO {table.old}")
            for old, new in table.columns.items():
                if new in conn.table_columns(table.old):
                    conn.execute(f"ALTER TABLE {table.old} RENAME COLUMN {new} TO {old}")
            for old_index, new_index in table.indexes.items():
                if conn.is_postgres:
                    conn.execute(f"ALTER INDEX IF EXISTS {new_index} RENAME TO {old_index}")
                    continue
                # SQLite cannot rename an index: it is made again under the old name (over the old table and columns).
                found = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?", (new_index,)).fetchone()
                if found:
                    conn.execute(f"DROP INDEX {new_index}")
                    conn.execute(found[0].replace(new_index, old_index, 1))


def index_names() -> set[str]:
    with db.connect() as conn:
        if conn.is_postgres:
            rows = conn.execute("SELECT indexname FROM pg_indexes WHERE schemaname = current_schema()").fetchall()
        else:
            rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'").fetchall()
    return {next(iter(r.values())) if isinstance(r, dict) else r[0] for r in rows}


def check_indexes(apps: tuple[str, ...], converted: bool) -> None:
    """The old shape has the old names of the apps' indexes, the converted database the new ones (and no old)."""
    pairs = [pair for t in schema_names.TABLES if any(app in t.new for app in apps) for pair in t.indexes.items()]
    want, gone = ({new for _, new in pairs}, {old for old, _ in pairs}) if converted else ({old for old, _ in pairs}, {new for _, new in pairs})
    have = index_names()
    check(bool(want) and want <= have and not gone & have,
          f"{'/'.join(apps)}: the {'new' if converted else 'old'} names of the indexes ({sorted(want - have)} missing, {sorted(gone & have)} left)")


def old_settings(values: dict[str, str]) -> None:
    """Settings under their old keys (and none under the new ones)."""
    by_old = schema_names.SETTING_KEYS
    for key, value in values.items():
        run("DELETE FROM settings WHERE key IN (?, ?)", (key, by_old.get(key, key)))
        run("INSERT INTO settings (key, value) VALUES (?, ?)", (key, value))


db.init_db()
db.set_setting("backup_path", str(Path(_tmp) / "backups"))
accounts.create("arr_admin", "conversion-arr-pw", is_admin=True)
client = TestClient(web.app)
client.post("/login", data={"username": "arr_admin", "password": "conversion-arr-pw", "method": "local"})
ENABLED = 'name="enabled" value="1" checked'

print("--- the enabled keys of the services ---")
old_settings({"arr_sonarr_zapnuto": "1", "seerr_zapnuto": "1"})
db.init_db()
db.forget_settings()
check(setting("arr_sonarr_enabled") == "1" and setting("seerr_enabled") == "1",
      f"under the new keys ({setting('arr_sonarr_enabled')!r}, {setting('seerr_enabled')!r})")
check(setting("arr_sonarr_zapnuto") == "" and setting("seerr_zapnuto") == "", "no old key left")
check(services.service_settings("arr_sonarr")["enabled"] is True and services.service_settings("seerr")["enabled"] is True,
      "the services read them")
arr_page, seerr_page = client.get("/settings?section=arr").text, client.get("/settings?section=seerr").text
check(arr_page.count(ENABLED) == 1 and ENABLED in seerr_page, "the settings show Sonarr and Seerr switched on")

print()
print("--- Sonarr and Radarr ---")
import asyncio  # noqa: E402
import json  # noqa: E402
from datetime import UTC, datetime, timedelta  # noqa: E402

import httpx  # noqa: E402

from jellyscope import cards  # noqa: E402
from jellyscope.arr import radarr, sonarr  # noqa: E402
from jellyscope.arr.radarr import titles as radarr_titles  # noqa: E402
from jellyscope.arr.sonarr import titles as sonarr_titles  # noqa: E402

RECENT = (datetime.now(UTC) - timedelta(days=2)).strftime(db.TIME_FORMAT)
NOW = datetime.now(UTC).strftime(db.TIME_FORMAT)
MARK = (datetime.now(UTC) - timedelta(hours=3)).strftime(db.TIME_FORMAT)
admin_id = int(dict(db.query_one("SELECT id FROM accounts WHERE username = 'arr_admin'"))["id"])
old_tables(("sonarr", "radarr"))
check_indexes(("sonarr", "radarr"), converted=False)
check({"sonarr_udalost", "radarr_udalost", "sonarr_titul", "radarr_titul"} <= tables(), "the old tables are there")
run("INSERT INTO sonarr_udalost (arr_id, typ, datum, titul, epizoda, polozka, titul_id, kvalita, rozliseni, velikost, indexer,"
    " release_group, klient, protokol, zdroj, duvod, download_id, vydano, cf_skore) VALUES (11, 'downloadFolderImported', ?,"
    " 'Dark', 'S01E01', 146, 3, 'WEBDL-1080p', 1080, 5368709120, 'IndexerArrQX', 'GroupQX', 'qBittorrent', 'torrent',"
    " 'interactive', '', 'DL-1', ?, 120)", (RECENT, RECENT))
run("INSERT INTO sonarr_titul (titul_id, nazev, tmdb_id, tvdb_id, imdb_id, jellyfin_id, pridano, synchronizovano)"
    " VALUES (3, 'Dark', '70523', '334824', 'tt5753856', 'a427aea771c45a7d71be634b62b36e34', ?, ?)", (RECENT, NOW))
run("INSERT INTO radarr_udalost (arr_id, typ, datum, titul, polozka, titul_id, kvalita, rozliseni, velikost, indexer,"
    " release_group, klient, protokol, zdroj, duvod, download_id, vydano, cf_skore) VALUES (21, 'downloadFolderImported', ?,"
    " 'Arrival', 7, 7, 'Bluray-2160p', 2160, 42949672960, 'IndexerArrQX', 'GroupQX', 'SABnzbd', 'usenet', '', '', 'DL-2',"
    " ?, 0)", (RECENT, RECENT))
run("INSERT INTO radarr_titul (titul_id, nazev, tmdb_id, imdb_id, pridano, synchronizovano)"
    " VALUES (7, 'Arrival', '329865', 'tt2543164', ?, ?)", (RECENT, NOW))
state = json.dumps({"kdy": NOW, "vysledek": "ok", "novych": 1, "chyba": ""})
old_settings({"arr_sonarr_stav": state, "arr_sonarr_synchronizace": MARK, "arr_sonarr_tituly": NOW,
              "arr_sonarr_tituly_verze": sonarr_titles.LIST_VERSION,
              "arr_radarr_stav": state, "arr_radarr_synchronizace": MARK, "arr_radarr_tituly": NOW,
              "arr_radarr_tituly_verze": radarr_titles.LIST_VERSION,
              "arr_radarr_url": "http://radarr.test", "arr_radarr_api_key": "radarr-key", "arr_sonarr_url": "http://sonarr.test",
              "arr_sonarr_api_key": "sonarr-key", "arr_radarr_zapnuto": "1"})
run("DELETE FROM dashboard_layout")
run("INSERT INTO dashboard_layout (account_id, sekce, poradi, sirka) VALUES (NULL, 'arr_sonarr_cisla', 0, 'cela'),"
    " (NULL, 'neznama_karta', 1, NULL), (?, 'arr_radarr_kvality', 0, NULL)", (admin_id,))

db.init_db()
check_indexes(("sonarr", "radarr"), converted=True)
db.forget_settings()
check({"sonarr_event", "radarr_event", "sonarr_title", "radarr_title"} <= tables()
      and not ({"sonarr_udalost", "radarr_udalost", "sonarr_titul", "radarr_titul"} & tables()), "the tables have their new names")
event = dict(db.query_one("SELECT * FROM sonarr_event WHERE arr_id = 11") or {})
check({k: event.get(k) for k in ("event_type", "happened_at", "title", "episode", "item_id", "title_id", "quality", "resolution",
                                 "size_bytes", "indexer", "release_group", "client", "protocol", "source", "reason",
                                 "download_id", "released_at", "cf_score")}
      == {"event_type": "downloadFolderImported", "happened_at": RECENT, "title": "Dark", "episode": "S01E01", "item_id": 146,
          "title_id": 3, "quality": "WEBDL-1080p", "resolution": 1080, "size_bytes": 5368709120, "indexer": "IndexerArrQX",
          "release_group": "GroupQX", "client": "qBittorrent", "protocol": "torrent", "source": "interactive", "reason": "",
          "download_id": "DL-1", "released_at": RECENT, "cf_score": 120}, f"a Sonarr event under its new columns ({event})")
movie = dict(db.query_one("SELECT title, item_id, size_bytes, client FROM radarr_event WHERE arr_id = 21") or {})
check(movie == {"title": "Arrival", "item_id": 7, "size_bytes": 42949672960, "client": "SABnzbd"}, f"a Radarr event too ({movie})")
check(dict(db.query_one("SELECT name, tvdb_id, jellyfin_id, added_at, synced_at FROM sonarr_title WHERE title_id = 3") or {})
      == {"name": "Dark", "tvdb_id": "334824", "jellyfin_id": "a427aea771c45a7d71be634b62b36e34", "added_at": RECENT, "synced_at": NOW}
      and dict(db.query_one("SELECT name FROM radarr_title WHERE title_id = 7") or {}) == {"name": "Arrival"}, "and the titles")
check(setting("arr_sonarr_last_sync") == MARK and setting("arr_radarr_last_sync") == MARK
      and json.loads(setting("arr_sonarr_state") or "{}").get("result") == "ok"
      and setting("arr_sonarr_titles") == NOW and setting("arr_sonarr_titles_version") == sonarr_titles.LIST_VERSION
      and setting("arr_radarr_titles_version") == radarr_titles.LIST_VERSION and setting("arr_radarr_enabled") == "1",
      "the state, the sync marks and the title list's mark under their new keys")
check(not [k for k in ("arr_sonarr_stav", "arr_sonarr_synchronizace", "arr_sonarr_tituly", "arr_sonarr_tituly_verze",
                       "arr_radarr_stav", "arr_radarr_synchronizace", "arr_radarr_tituly", "arr_radarr_tituly_verze") if setting(k)],
      "no old key left")
shared = [dict(r)["card_key"] for r in db.query_all("SELECT card_key FROM dashboard_layout WHERE account_id IS NULL ORDER BY position")]
own = [dict(r)["card_key"] for r in db.query_all("SELECT card_key FROM dashboard_layout WHERE account_id = ?", (admin_id,))]
check(shared == ["arr_sonarr_numbers", "neznama_karta"] and own == ["arr_radarr_qualities"],
      f"the cards renamed in the shared and the own layout, the unknown left alone ({shared}, {own})")
check([card.key for card in cards.load_layout()] == ["arr_sonarr_numbers"]
      and [card.key for card in cards.load_layout(admin_id)] == ["arr_radarr_qualities"], "the dashboards still show them")
for address in ("/arr/sonarr", "/arr/radarr"):
    answer = client.get(address)
    check(answer.status_code == 200 and "GroupQX" in answer.text, f"{address} answers with the data ({answer.status_code})")

asked: list[str] = []


def app_since(request: httpx.Request) -> httpx.Response:
    """Sonarr or Radarr with nothing new: notes what is asked."""
    if request.url.path.endswith("/api/v3/history/since"):
        asked.append(f"since {request.url.params.get('date', '')}")
        return httpx.Response(200, json=[])
    if request.url.path.endswith("/api/v3/system/status"):      # the sync first makes sure the right app answers
        return httpx.Response(200, json={"appName": answering["name"], "version": "1"})
    asked.append(request.url.path)
    return httpx.Response(200, json=[])


answering = {"name": ""}


since = (datetime.strptime(MARK, db.TIME_FORMAT) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
for name, package in (("sonarr", sonarr), ("radarr", radarr)):
    asked.clear()
    answering["name"] = name.capitalize()
    asyncio.run(package.synchronize(transport=httpx.MockTransport(app_since)))
    # Since 23. 11. 2026 the sync also counts missing and cutoff (services.count_wanted) - not the history.
    check([a for a in asked if "/wanted/" not in a] == [f"since {since}"],
          f"{name}: the next sync asks since the mark, not the whole history, and not for the title list ({asked})")
check(db.query_value("SELECT COUNT(*) FROM sonarr_event") == 1 and db.query_value("SELECT COUNT(*) FROM radarr_event") == 1,
      "and doubles nothing")

print()
print("--- Bazarr ---")
old_tables(("bazarr",))
check_indexes(("bazarr",), converted=False)
check("bazarr_udalost" in tables(), "the old table is there")
run("INSERT INTO bazarr_udalost (otisk, druh, titul, epizoda, titul_id, polozka, akce, jazyk, hi, vynucene, poskytovatel, skore,"
    " datum, datum_presne, synchronizovano) VALUES ('fp-episode', 'dil', 'Dark', 'S01E01', 3, 146, 1, 'cs', 0, 0,"
    " 'opensubtitlescom', 86.67, ?, 1, ?), ('fp-movie', 'film', 'Arrival', '', 7, 7, 3, 'en', 1, 1, 'manual', NULL, NULL, 0, ?)",
    (RECENT, NOW, NOW))
old_settings({"arr_bazarr_stav": state, "arr_bazarr_synchronizace": MARK, "arr_bazarr_zapnuto": "1",
              "arr_bazarr_url": "http://bazarr.test", "arr_bazarr_api_key": "bazarr-key"})
run("DELETE FROM dashboard_layout")
run("INSERT INTO dashboard_layout (account_id, sekce, poradi, sirka) VALUES (NULL, 'bazarr_jazyky', 0, NULL),"
    " (NULL, 'neznama_karta', 1, NULL), (?, 'bazarr_rychlost', 0, NULL)", (admin_id,))

db.init_db()
check_indexes(("bazarr",), converted=True)
db.forget_settings()
check("bazarr_event" in tables() and "bazarr_udalost" not in tables(), "the table has its new name")
subtitles = {dict(r)["fingerprint"]: dict(r) for r in db.query_all("SELECT * FROM bazarr_event")}
check({k: subtitles.get("fp-episode", {}).get(k) for k in ("kind", "title", "episode", "title_id", "item_id", "action", "language",
                                                           "hi", "forced", "provider", "score", "happened_at", "time_exact",
                                                           "synced_at")}
      == {"kind": "episode", "title": "Dark", "episode": "S01E01", "title_id": 3, "item_id": 146, "action": 1, "language": "cs",
          "hi": 0, "forced": 0, "provider": "opensubtitlescom", "score": 86.67, "happened_at": RECENT, "time_exact": 1,
          "synced_at": NOW}
      and subtitles.get("fp-movie", {}).get("kind") == "movie" and subtitles["fp-movie"]["score"] is None
      and subtitles["fp-movie"]["time_exact"] == 0, f"the events under their new columns, the kinds in English ({subtitles})")
with db.connect() as conn:
    check("idx_bazarr_event_inexact" in conversion._indexes(conn), "the partial index under its new name")
check(setting("arr_bazarr_last_sync") == MARK and json.loads(setting("arr_bazarr_state") or "{}").get("result") == "ok"
      and not setting("arr_bazarr_stav") and not setting("arr_bazarr_synchronizace"), "the state and the mark under their new keys")
layout = [dict(r)["card_key"] for r in db.query_all("SELECT card_key FROM dashboard_layout WHERE account_id IS NULL ORDER BY position")]
own = [dict(r)["card_key"] for r in db.query_all("SELECT card_key FROM dashboard_layout WHERE account_id = ?", (admin_id,))]
check(layout == ["bazarr_languages", "neznama_karta"] and own == ["bazarr_speed"],
      f"the cards renamed in the shared and the own layout, the unknown left alone ({layout}, {own})")
check([card.key for card in cards.load_layout()] == ["bazarr_languages"]
      and [card.key for card in cards.load_layout(admin_id)] == ["bazarr_speed"], "the dashboards still show them")
answer = client.get("/arr/bazarr")
check(answer.status_code == 200 and "opensubtitlescom" in answer.text, f"/arr/bazarr answers with the data ({answer.status_code})")

print()
print("--- Prowlarr ---")
old_tables(("prowlarr",))
check_indexes(("prowlarr",), converted=False)
check({"prowlarr_hodina", "prowlarr_vyzadane"} <= tables(), "the old tables are there")
HOUR = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%d %H:00:00")
# Two rows of one hour and indexer that differ only by the kind - the kind is in the primary key.
run("INSERT INTO prowlarr_hodina (hodina, indexer_id, aplikace, druh, pocet, selhalo, odezva_soucet, odezva_pocet, s_vysledkem)"
    " VALUES (?, 1, 'Sonarr', 'dotaz', 12, 2, 3400, 10, 7), (?, 1, 'Sonarr', 'rss', 30, 0, 0, 0, 30),"
    " (?, 2, '', 'auth', 1, 1, 0, 0, 0)", (HOUR, HOUR, HOUR))
run("INSERT INTO prowlarr_vyzadane (id, datum, indexer_id, aplikace, titul, zpusob, uspesne, odezva, klient)"
    " VALUES (23, ?, 1, 'Radarr', 'Film.2024.1080p.WEB-DL', 'proxy', 1, 41, 'SABnzbd'),"
    " (24, ?, 2, 'Sonarr', 'Serial.S01E01.720p', 'presmerovani', 1, NULL, '')", (RECENT, RECENT))
run("INSERT INTO prowlarr_indexer (id, nazev, protokol, soukromi, zapnuty, odebran, videno)"
    " VALUES (1, 'IndexerProwlarrQX', 'usenet', 'private', 1, 0, ?), (2, 'Gone', 'torrent', 'public', 0, 1, ?)", (NOW, NOW))
old_settings({"arr_prowlarr_stav": state, "arr_prowlarr_synchronizace": MARK, "arr_prowlarr_posledni_id": "24",
              "arr_prowlarr_pocet": "44", "arr_prowlarr_zapnuto": "1", "arr_prowlarr_url": "http://prowlarr.test",
              "arr_prowlarr_api_key": "prowlarr-key"})
run("DELETE FROM dashboard_layout")
run("INSERT INTO dashboard_layout (account_id, sekce, poradi, sirka) VALUES (NULL, 'prowlarr_odezva', 0, NULL),"
    " (NULL, 'neznama_karta', 1, NULL), (?, 'prowlarr_vyzadana_posledni', 0, NULL)", (admin_id,))

db.init_db()
check_indexes(("prowlarr",), converted=True)
db.forget_settings()
check({"prowlarr_hour", "prowlarr_grab", "prowlarr_indexer"} <= tables() and not ({"prowlarr_hodina", "prowlarr_vyzadane"} & tables()),
      "the tables have their new names")
hours = sorted((dict(r)["kind"], dict(r)["event_count"], dict(r)["failed_count"], dict(r)["response_sum"], dict(r)["response_count"],
                dict(r)["with_results"], dict(r)["app"]) for r in db.query_all("SELECT * FROM prowlarr_hour WHERE hour = ?", (HOUR,)))
check(hours == [("auth", 1, 1, 0, 0, 0, ""), ("query", 12, 2, 3400, 10, 7, "Sonarr"), ("rss", 30, 0, 0, 0, 30, "Sonarr")],
      f"the hourly sums under their new columns, the query's kind in English ({hours})")
grabs = {dict(r)["id"]: dict(r) for r in db.query_all("SELECT * FROM prowlarr_grab")}
check(grabs.get(23, {}).get("method") == "proxy" and grabs.get(23, {}).get("response") == 41 and grabs[23]["client"] == "SABnzbd"
      and grabs.get(24, {}).get("method") == "redirect" and grabs[24]["response"] is None and grabs[24]["happened_at"] == RECENT
      and grabs[24]["title"] == "Serial.S01E01.720p" and grabs[24]["succeeded"] == 1, f"the grabbed releases ({grabs})")
indexers = {dict(r)["id"]: dict(r) for r in db.query_all("SELECT * FROM prowlarr_indexer")}
check(indexers.get(1, {}).get("name") == "IndexerProwlarrQX" and indexers[1]["protocol"] == "usenet" and indexers[1]["privacy"] == "private"
      and indexers[1]["enabled"] == 1 and indexers.get(2, {}).get("removed") == 1 and indexers[2]["seen_at"] == NOW,
      f"the indexers under their new columns ({indexers})")
check(setting("arr_prowlarr_last_id") == "24" and setting("arr_prowlarr_counted") == "44" and setting("arr_prowlarr_last_event_at") == MARK
      and json.loads(setting("arr_prowlarr_state") or "{}").get("result") == "ok"
      and not [k for k in ("arr_prowlarr_posledni_id", "arr_prowlarr_pocet", "arr_prowlarr_synchronizace", "arr_prowlarr_stav") if setting(k)],
      "the mark, the count, the time and the state under their new keys")
layout = [dict(r)["card_key"] for r in db.query_all("SELECT card_key FROM dashboard_layout WHERE account_id IS NULL ORDER BY position")]
own = [dict(r)["card_key"] for r in db.query_all("SELECT card_key FROM dashboard_layout WHERE account_id = ?", (admin_id,))]
check(layout == ["prowlarr_response_time", "neznama_karta"] and own == ["prowlarr_grabbed_latest"],
      f"the cards renamed in the shared and the own layout, the unknown left alone ({layout}, {own})")
check([card.key for card in cards.load_layout()] == ["prowlarr_response_time"]
      and [card.key for card in cards.load_layout(admin_id)] == ["prowlarr_grabbed_latest"], "the dashboards still show them")
answer = client.get("/arr/prowlarr")
check(answer.status_code == 200 and "IndexerProwlarrQX" in answer.text, f"/arr/prowlarr answers with the data ({answer.status_code})")

print()
print("--- Seerr (database step 3) ---")
from jellyscope import access  # noqa: E402

old_tables(("seerr",))
check_indexes(("seerr",), converted=False)
check("seerr_zadost" in tables(), "the old table is there")
# Pending, approved after "pending", declined, deleted in Seerr; a 4K and a normal request of one title; posters
# found, none at Seerr ('-') and not looked up yet ('').
run("INSERT INTO seerr_zadost (id, typ, tmdb_id, tvdb_id, jellyfin_id, titul, serii, je_4k, automaticka, stav, stav_titulu,"
    " zadal_seerr_id, zadal_jmeno, zadal_jellyfin_id, vyridil_jmeno, vytvoreno, upraveno, dostupne, videno_cekat, schvaleno_v,"
    " zamitnuto_v, smazano_v, synchronizovano, plakat) VALUES"
    " (1, 'movie', '603', '', 'jf-matrix', 'Matrix', NULL, 0, 0, 1, 2, 4, 'jana', 'jf-jana', '', ?, ?, NULL, 1, NULL, NULL, NULL, ?, '/m.jpg'),"
    " (2, 'movie', '603', '', 'jf-matrix', 'Matrix', NULL, 1, 1, 2, 3, 4, 'jana', 'jf-jana', 'admin', ?, ?, NULL, 1, ?, NULL, NULL, ?, '-'),"
    " (3, 'tv', '70523', '334824', 'jf-dark', 'Dark', 2, 0, 0, 3, 1, 5, 'petr', 'jf-petr', 'admin', ?, ?, NULL, 1, NULL, ?, NULL, ?, ''),"
    " (4, 'tv', '1399', '121361', '', 'Game of Thrones', 8, 0, 0, 5, 5, 5, 'petr', 'jf-petr', 'admin', ?, ?, ?, 0, NULL, NULL, ?, ?, '/g.jpg')",
    (RECENT, NOW, NOW, RECENT, NOW, RECENT, NOW, RECENT, NOW, RECENT, NOW, RECENT, NOW, RECENT, RECENT, NOW))
old_settings({"seerr_stav": state, "seerr_synchronizace": MARK, "seerr_obrazky": "1", "seerr_plakaty_znovu": NOW,
              "seerr_url": "http://seerr.test", "seerr_api_key": "seerr-key"})
reader_id = accounts.create("seerr_reader", "conversion-arr-pw")
accounts.save_permissions(reader_id, access.pack_permissions(["seerr", "seerr_who", "dashboard"], False))
run("DELETE FROM dashboard_layout")
run("INSERT INTO dashboard_layout (account_id, sekce, poradi, sirka) VALUES (NULL, 'seerr_who', 0, NULL), (NULL, 'neznama_karta', 1, NULL),"
    " (?, 'seerr_cekajici', 0, NULL)", (admin_id,))

db.init_db()
check_indexes(("seerr",), converted=True)
db.forget_settings()
check("seerr_request" in tables() and "seerr_zadost" not in tables(), "the table has its new name")
requests = {dict(r)["id"]: dict(r) for r in db.query_all("SELECT * FROM seerr_request")}
check({k: requests.get(1, {}).get(k) for k in ("media_type", "tmdb_id", "jellyfin_id", "title", "seasons", "is_4k", "auto_request",
                                              "status", "media_status", "requester_seerr_id", "requester_name",
                                              "requester_jellyfin_id", "handled_by_name", "created_at", "updated_at",
                                              "available_at", "seen_pending", "approved_at", "declined_at", "deleted_at",
                                              "synced_at", "poster")}
      == {"media_type": "movie", "tmdb_id": "603", "jellyfin_id": "jf-matrix", "title": "Matrix", "seasons": None, "is_4k": 0,
          "auto_request": 0, "status": 1, "media_status": 2, "requester_seerr_id": 4, "requester_name": "jana",
          "requester_jellyfin_id": "jf-jana", "handled_by_name": "", "created_at": RECENT, "updated_at": NOW, "available_at": None,
          "seen_pending": 1, "approved_at": None, "declined_at": None, "deleted_at": None, "synced_at": NOW, "poster": "/m.jpg"},
      f"a request under its new columns ({requests.get(1)})")
check(requests[2]["is_4k"] == 1 and requests[2]["approved_at"] == RECENT and requests[2]["poster"] == "-"
      and requests[3]["declined_at"] == RECENT and requests[3]["poster"] == "" and requests[3]["seasons"] == 2
      and requests[4]["deleted_at"] == RECENT and requests[4]["available_at"] == RECENT and requests[4]["tvdb_id"] == "121361",
      "the 4K one, the approval, the decline, the deletion and the posters as they were")
check(setting("seerr_last_sync") == MARK and setting("seerr_images") == "1" and setting("seerr_posters_retry") == NOW
      and json.loads(setting("seerr_state") or "{}").get("result") == "ok"
      and not [k for k in ("seerr_stav", "seerr_synchronizace", "seerr_obrazky", "seerr_plakaty_znovu") if setting(k)],
      "the state, the mark and the image keys under their new names")
layout = [dict(r)["card_key"] for r in db.query_all("SELECT card_key FROM dashboard_layout WHERE account_id IS NULL ORDER BY position")]
own = [dict(r)["card_key"] for r in db.query_all("SELECT card_key FROM dashboard_layout WHERE account_id = ?", (admin_id,))]
check(layout == ["seerr_who", "neznama_karta"] and own == ["seerr_pending"],
      f"the cards renamed in the shared and the own layout, the unknown left alone ({layout}, {own})")
reader = accounts.get(reader_id)
check([card.key for card in cards.load_layout()] == ["seerr_who"] and "seerr_who" in [card.key for card in cards.allowed(cards.load_layout(), reader)],
      "the card \"who requests\" keeps its permission area: a reader with the area seerr_who still sees it")
answer = client.get("/seerr")
check(answer.status_code == 200 and "Game of Thrones" in answer.text, f"/seerr answers with the data ({answer.status_code})")
latest = answer.text.split("<!-- card: seerr_latest -->", 1)[-1].split("<!-- card:", 1)[0]
check("<!-- card: seerr_latest -->" in answer.text and latest.count("Matrix") >= 2 and latest.count("· 4K") == 1,
      f"the latest requests tell the 4K request of Matrix from the normal one ({latest.count('· 4K')} × 4K)")

print("--- Seerr: a table from before jellyfin_id, tvdb_id and the poster (the dev of 13. 11.) ---")
LATER = ("jellyfin_id", "tvdb_id", "plakat")
old_tables(("seerr",))
run("DELETE FROM seerr_zadost")
for column in LATER:
    run(f"ALTER TABLE seerr_zadost DROP COLUMN {column}")
with db.connect() as conn:
    check(not set(LATER) & conn.table_columns("seerr_zadost"), "the old table is without the three later columns")
run("INSERT INTO seerr_zadost (id, typ, tmdb_id, titul, je_4k, automaticka, stav, stav_titulu, zadal_seerr_id, zadal_jmeno,"
    " vytvoreno, upraveno, videno_cekat, schvaleno_v, synchronizovano) VALUES (7, 'movie', '27205', 'Inception', 0, 0, 2, 5, 4,"
    " 'jana', ?, ?, 1, ?, ?)", (RECENT, NOW, RECENT, NOW))
db.init_db()
check_indexes(("seerr",), converted=True)
with db.connect() as conn:
    after_columns = conn.table_columns("seerr_request")
inception = dict(db.query_one("SELECT * FROM seerr_request WHERE id = 7") or {})
check({k: inception.get(k) for k in ("title", "tmdb_id", "status", "requester_name", "seen_pending", "approved_at",
                                     "jellyfin_id", "tvdb_id", "poster")}
      == {"title": "Inception", "tmdb_id": "27205", "status": 2, "requester_name": "jana", "seen_pending": 1,
          "approved_at": RECENT, "jellyfin_id": "", "tvdb_id": "", "poster": ""}
      and {"jellyfin_id", "tvdb_id", "poster"} <= after_columns and not {"plakat", "titul"} & after_columns,
      f"the request kept, the later columns added only under the new names ({inception})")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
