# -*- coding: utf-8 -*-
r"""Drobnosti API odložené z revize filtrů (1.8.1).

* čas ve filtru jde zadat i v tom tvaru, v jakém ho API samo vrací;
* `min_size_mb` s obřím číslem je 400, ne 500, a desetinná MB se neořízne;
* 404 a 405 pod /api/ mají kód v `error` jako ostatní chyby;
* čísla jen z ASCII číslic a bez znaku nového řádku na konci;
* stejné pořadí s limitem v SQL i bez něj (remízy);
* text se porovnává i v jiném zápisu téhož znaku (NFC / NFD);
* `/least-played` se `sort` nehlásí `order: size`;
* španělský název typu „čas" ve filtru.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_api_drobnosti.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "drobnosti.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYSCOPE_DEMO"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import api, db  # noqa: E402
from jellyscope.web import app  # noqa: E402

failures = 0


def check(podminka: bool, popis: str) -> None:
    global failures
    print(f"{'OK    ' if podminka else 'CHYBA '} {popis}")
    if not podminka:
        failures += 1


db.init_db()
pred = lambda dnu: (datetime.now(timezone.utc) - timedelta(days=dnu)).strftime(db.TIME_FORMAT)  # noqa: E731
STEJNE = pred(5)
with db.connect() as conn:
    conn.execute("INSERT INTO libraries (id, name, collection_type) VALUES ('lib1', 'Filmy', 'movies')")
    conn.execute("INSERT INTO users (id, name, is_administrator, is_disabled) VALUES ('u1', 'Jana', 0, 0)")
    # Pet titulu se stejnym datem pridani, nazvem i velikosti - remizy.
    for k in range(5):
        conn.execute(
            "INSERT INTO items (id, name, type, size_bytes, library_id, date_created, path,"
            " tech_source, is_missing) VALUES (?, 'Kopie', 'Movie', 1258291, 'lib1', ?, ?, 'jellyfin', 0)",
            (f"k{k}", STEJNE, f"/m/kopie{k}.mkv"))
    # Ctyri stare nehrane soubory stejne velikosti - remizy u nevyuziteho mista.
    for k in range(4):
        conn.execute(
            "INSERT INTO items (id, name, type, size_bytes, library_id, date_created, path,"
            " tech_source, is_missing) VALUES (?, 'Stary', 'Movie', 5000000, 'lib1', ?, ?,"
            " 'jellyfin', 0)", (f"s{k}", pred(500), f"/m/stary{k}.mkv"))
    # Serial a film pridane ve stejnou chvili (remiza mezi skupinami).
    NOVE = pred(2)
    conn.execute(
        "INSERT INTO items (id, name, type, size_bytes, library_id, date_created, path,"
        " tech_source, is_missing) VALUES ('b-film', 'Film B', 'Movie', 1000, 'lib1', ?,"
        " '/m/b.mkv', 'jellyfin', 0)", (NOVE,))
    conn.execute(
        "INSERT INTO items (id, name, type, size_bytes, library_id, date_created, path,"
        " tech_source, is_missing, series_id, series_name, parent_index_number, index_number)"
        " VALUES ('a-dil', 'Dil 1', 'Episode', 1000, 'lib1', ?, '/m/s/d1.mkv', 'jellyfin', 0,"
        " 'z-serial', 'Serial Z', 1, 1)", (NOVE,))
    # Tri velke nehrane soubory stejne velikosti (insights: oversized).
    # Vkladaji se pozpatku, at poradi radku v tabulce nerozhodne za test.
    for k in (2, 1, 0):
        conn.execute(
            "INSERT INTO items (id, name, type, size_bytes, library_id, date_created, path,"
            " tech_source, is_missing) VALUES (?, 'Velky', 'Movie', 5000000000, 'lib1', ?, ?,"
            " 'jellyfin', 0)", (f"v{k}", pred(600), f"/m/velky{k}.mkv"))
    # Tri tituly zacate a nedokoncene stejne (insights: never_finished).
    for k in (2, 1, 0):
        conn.execute(
            "INSERT INTO items (id, name, type, size_bytes, library_id, date_created, path,"
            " tech_source, is_missing, runtime_ticks) VALUES (?, ?, 'Movie', 1000, 'lib1', ?, ?,"
            " 'jellyfin', 0, 72000000000)", (f"n{k}", f"Nedokonceny {k}", pred(600), f"/m/n{k}.mkv"))
        conn.execute(
            "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name, item_type,"
            " started_at, last_seen_at, watched_seconds, paused_seconds, is_paused, is_active,"
            " play_method, client, position_ticks) VALUES (?, 'u1', 'Jana', ?, ?, 'Movie', ?, ?,"
            " 300, 0, 0, 0, 'DirectPlay', 'Web', 3000000000)",
            (f"ns{k}", f"n{k}", f"Nedokonceny {k}", pred(1), pred(1)))
    conn.execute(
        "INSERT INTO items (id, name, type, size_bytes, library_id, date_created, path,"
        " tech_source, is_missing) VALUES ('am', 'Amélie', 'Movie', 2000000000, 'lib1', ?,"
        " '/m/amelie.mkv', 'jellyfin', 0)", (pred(400),))
    conn.execute(
        "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name, item_type,"
        " started_at, last_seen_at, watched_seconds, paused_seconds, is_paused, is_active,"
        " play_method, client) VALUES ('s1', 'u1', 'Jana', 'am', 'Amélie', 'Movie', ?, ?,"
        " 3600, 0, 0, 0, 'DirectPlay', 'Web')", (pred(1), pred(1)))
    conn.commit()

klient = TestClient(app)
H = {"Authorization": f"Bearer {api.vytvor('Testy')['token']}"}


def get(adresa: str):
    return klient.get(adresa, headers=H)


print("--- čas ve tvaru z odpovědi ---")
odpoved = get("/api/v1/history?started_at[gte]=2026-01-01T12:00:00.0000000Z")
check(odpoved.status_code == 200, f"tvar 2026-01-01T12:00:00.0000000Z projde ({odpoved.status_code})")
check(get("/api/v1/history?started_at[gte]=2026-01-01T12:00:00Z").status_code == 200, "i bez zlomků")
check(get("/api/v1/history?started_at[gte]=2026-01-01T12:00").status_code == 400, "neúplný čas je 400")

print("--- min_size_mb ---")
odpoved = get("/api/v1/least-played?min_size_mb=1e20")
check(odpoved.status_code == 400 and odpoved.json().get("error") == "invalid_value",
      f"obří číslo je 400 invalid_value ({odpoved.status_code})")
kopie = [t for t in get("/api/v1/least-played?min_size_mb=1.5&limit=500").json().get("items", [])
         if t["name"] == "Kopie"]
check(not kopie, f"1,5 MB se neořízne na 1 MB - soubor 1,2 MB neprojde ({len(kopie)})")

print("--- 404 a 405 pod /api/ ---")
odpoved = get("/api/v1/neexistuje")
check(odpoved.status_code == 404 and odpoved.json().get("error") == "not_found",
      f"neznámá adresa: 404 not_found ({odpoved.json()})")
odpoved = klient.post("/api/v1/history", headers=H)
check(odpoved.status_code == 405 and odpoved.json().get("error") == "method_not_allowed",
      f"jiná metoda než GET: 405 method_not_allowed ({odpoved.status_code} {odpoved.text[:80]})")

print("--- čísla jen z ASCII ---")
for dotaz in ("days=30%0A", "days=%D9%A3%D9%A0", "limit=5%0A", "limit=%EF%BC%95",
              "min_size_mb=%D9%A3", "min_size_mb=5%0A", "min_size_mb=1_0", "min_size_mb=1e3"):
    odpoved = get(f"/api/v1/least-played?{dotaz}")
    check(odpoved.status_code == 400, f"{dotaz}: 400 ({odpoved.status_code})")
for dotaz in ("watched_seconds[gte]=%EF%BC%95", "watched_seconds[gte]=1_800",
              "watched_seconds[gte]=1800%0A"):
    odpoved = get(f"/api/v1/history?{dotaz}")
    check(odpoved.status_code == 400, f"{dotaz}: 400 ({odpoved.status_code})")
check(get("/api/v1/least-played?min_size_mb=0.5").status_code == 200
      and get("/api/v1/history?watched_seconds[gte]=-1.5").status_code == 200,
      "obyčejné desetinné a záporné číslo projde")

print("--- remízy: s limitem i bez ---")
for adresa, seznam in (("/api/v1/recently-added", "items"), ("/api/v1/least-played", "items"),
                       ("/api/v1/unwatched", "items")):
    rychle = [t.get("id") or t.get("item_id") for t in get(f"{adresa}?limit=3").json().get(seznam, [])]
    plne = [t.get("id") or t.get("item_id")
            for t in get(f"{adresa}?limit=3&name[not]=nic").json().get(seznam, [])]
    check(rychle and rychle == plne, f"{adresa}: stejné pořadí s limitem v SQL i bez ({rychle} / {plne})")

check([t.get("id") for t in get("/api/v1/recently-added?limit=1").json().get("items", [])]
      == [t.get("id") for t in get("/api/v1/recently-added?limit=1&name[not]=nic").json().get("items", [])],
      "seriál a film přidané naráz: stejný první titul s limitem i bez")
# Remizu rozhoduje id (soubory), resp. nazev (tituly) - v SQLite i PostgreSQL
# stejne, ne podle toho, jak to databazi zrovna vyjde.
for seznam, klic, cekam in (("oversized_rarely_watched", "id", ["v0", "v1"]),
                            ("never_finished", "title", ["Nedokonceny 0", "Nedokonceny 1"])):
    rychle = [t.get(klic) for t in get("/api/v1/insights?limit=2").json().get(seznam, [])]
    plne = [t.get(klic) for t in get(f"/api/v1/insights?limit=2&{seznam}.{klic}[not]=nic").json().get(seznam, [])]
    check(rychle == plne == cekam,
          f"insights {seznam}: remízu rozhoduje {klic}, s limitem i bez ({rychle} / {plne})")

print("--- jiný zápis téhož znaku ---")
nfd = "Amélie"
odpoved = get(f"/api/v1/history?title[contains]={nfd}")
check(len(odpoved.json().get("playbacks", [])) == 1, "„Amélie“ zapsaná rozloženě (NFD) se najde")

print("--- order se sort ---")
odpoved = get("/api/v1/least-played?sort=-size_bytes")
check(odpoved.status_code == 200 and odpoved.json().get("order") is None,
      f"se sort není order: size ({odpoved.json().get('order')})")
check(get("/api/v1/least-played").json().get("order") == "size", "bez sort pořád size")

print("--- španělština ---")
es = json.loads((PROJECT / "jellyscope" / "translations" / "es.json").read_text(encoding="utf-8"))
check(es.get("čas") == "fecha y hora", f"typ pole „čas“ je ve španělštině datum a čas ({es.get('čas')})")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
