# -*- coding: utf-8 -*-
r"""Nové přehledy v API: nejméně hrané, nesledované, diváci, historie…

`test_api.py` hlídá závoru - token, otisk, samá GET. Tenhle hlídá, co
za závorou je:

* každá adresa z rozcestníku odpovídá, a naopak každá routa pod `/api/`
  je v rozcestníku - API se dokumentuje samo a nesmí lhát,
* nesmysl v parametrech (`days`, `limit`, `kind`, `min_size_mb`, id
  titulu) nic neshodí a nikam neprojde,
* v odpovědích **nejsou IP adresy** - historie ani provoz po síti je
  neprozradí, ačkoli je databáze má,
* nejméně hrané jsou opravdu od nuly nahoru a nesledované sedí s tím,
  kolik místa zabírají.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_api_prehledy.py
"""
from __future__ import annotations

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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "api2.db")
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

ADRESA = "203.0.113.77"      # verejna adresa, ktera se NESMI objevit v API
GB = 1024 ** 3
pred = lambda dnu: (datetime.now(timezone.utc) - timedelta(days=dnu)).strftime(db.TIME_FORMAT)  # noqa: E731
with db.connect() as conn:
    conn.execute("INSERT INTO libraries (id, name, collection_type) VALUES"
                 " ('lib1', 'Filmy', 'movies')")
    conn.execute("INSERT INTO users (id, name, is_administrator, is_disabled)"
                 " VALUES ('u1', 'Jana', 0, 0), ('u2', 'Petr', 0, 0)")
    for cislo, (jmeno, velikost, pridano) in enumerate((
            ("Duna", 30 * GB, 400), ("Amelie", 4 * GB, 400),
            ("Nikdo", 12 * GB, 400), ("Novinka", 20 * GB, 1)), start=1):
        conn.execute(
            "INSERT INTO items (id, name, type, size_bytes, width, height,"
            " video_codec, library_id, date_created, tech_source, is_missing)"
            " VALUES (?, ?, 'Movie', ?, 1920, 1080, 'h264', 'lib1', ?,"
            " 'jellyfin', 0)",
            (f"i{cislo}", jmeno, velikost, pred(pridano)))
    # Duna 3x, Amelie 1x, Nikdo a Novinka nikdy.
    for k, (item, kdo, kdy) in enumerate((
            ("i1", "u1", 1), ("i1", "u2", 3), ("i1", "u1", 10), ("i2", "u2", 5))):
        conn.execute(
            "INSERT INTO playback (session_key, user_id, user_name, item_id,"
            " item_name, item_type, started_at, last_seen_at, watched_seconds,"
            " paused_seconds, is_paused, is_active, play_method, client,"
            " remote_address, bitrate)"
            " VALUES (?, ?, ?, ?, ?, 'Movie', ?, ?, 5400, 0, 0, 0,"
            " 'DirectPlay', 'Jellyfin Web', ?, 8000000)",
            (f"s{k}", kdo, "Jana" if kdo == "u1" else "Petr", item,
             {"i1": "Duna", "i2": "Amelie"}[item], pred(kdy), pred(kdy), ADRESA))
    conn.commit()

klient = TestClient(app)
TOKEN = api.vytvor("Grafana")["token"]
H = {"Authorization": f"Bearer {TOKEN}"}

print("--- rozcestník nelže: každá adresa odpovídá a každá routa je v něm ---")
rozcestnik = klient.get("/api/v1/", headers=H).json()["endpoints"]
for cesta in rozcestnik:
    skutecna = cesta.replace("{id}", "i1")
    bez = klient.get(skutecna)
    s = klient.get(skutecna, headers=H)
    check(bez.status_code == 401 and s.status_code == 200,
          f"{cesta}: bez tokenu {bez.status_code}, s tokenem {s.status_code}")
    check(klient.post(skutecna, headers=H).status_code == 405,
          f"{cesta}: POST je 405")

routy = {r.path for r in api.router.routes if r.path.rstrip("/")}
zdokumentovane = {c.replace("{id}", "{item_id}") for c in rozcestnik}
chybi = sorted(r for r in routy if r not in zdokumentovane
               and r.rstrip("/") != "/api/v1")
check(not chybi, f"každá routa pod /api/ je v rozcestníku (chybí: {chybi})")

print()
print("--- nejméně hrané: od nuly nahoru, v nule největší první ---")
nejmene = klient.get("/api/v1/least-played?days=365", headers=H).json()
jmena = [p["name"] for p in nejmene["items"]]
hry = [p["plays"] for p in nejmene["items"]]
check(hry == sorted(hry), f"seřazeno podle spuštění ({hry})")
check(jmena[:2] == ["Novinka", "Nikdo"],
      f"v nule je větší soubor první ({jmena[:2]})")
check(jmena[-1] == "Duna", f"nejhranější je poslední ({jmena[-1]})")
duna = nejmene["items"][-1]
check(duna["plays"] == 3 and duna["last_played"], "s počtem i posledním přehráním")
check(all("size_human" in p and "library" in p for p in nejmene["items"]),
      "každý řádek má velikost i knihovnu")
filtr = klient.get("/api/v1/least-played?min_size_mb=10000", headers=H).json()
check([p["name"] for p in filtr["items"]] == ["Novinka", "Nikdo", "Duna"],
      f"min_size_mb odfiltruje malé ({[p['name'] for p in filtr['items']]})")

print()
print("--- nesledované: co se nehrálo a kolik to zabírá ---")
mrtve = klient.get("/api/v1/unwatched?days=365", headers=H).json()
check([p["name"] for p in mrtve["items"]] == ["Nikdo"],
      f"nesledovaný je jen Nikdo - Novinka je čerstvá ({[p['name'] for p in mrtve['items']]})")
check(mrtve["count"] == 1 and mrtve["size_bytes"] == 12 * GB,
      f"a čísla sedí ({mrtve['count']}, {mrtve['size_human']})")
check(0 < mrtve["share_percent"] < 100, f"podíl knihovny ({mrtve['share_percent']} %)")

print()
print("--- žebříček, diváci, historie ---")
top = klient.get("/api/v1/top-items?days=30&limit=5", headers=H).json()
check(top["items"] and top["items"][0]["title"] == "Duna",
      f"nejsledovanější je Duna ({[t['title'] for t in top['items']]})")
check(top["items"][0]["plays"] == 3, "se třemi spuštěními")
divaci = klient.get("/api/v1/users?days=30", headers=H).json()["users"]
check([d["name"] for d in divaci] == ["Jana", "Petr"],
      f"diváci od nejaktivnějšího ({[d['name'] for d in divaci]})")
check(divaci[0]["plays"] == 2 and divaci[0]["watched_hours"] == 3.0,
      "s počty a hodinami")
historie = klient.get("/api/v1/history?days=30&limit=10", headers=H).json()
check(historie["count"] == 4, f"historie má čtyři záznamy ({historie['count']})")
check(historie["playbacks"][0]["title"] == "Duna"
      and historie["playbacks"][0]["user"] == "Jana", "nejnovější první")
jen_petr = klient.get("/api/v1/history?days=30&user=u2", headers=H).json()
check(all(p["user"] == "Petr" for p in jen_petr["playbacks"])
      and jen_petr["count"] == 2, "filtr na diváka")

print()
print("--- adresy nikam ---")
for cesta in ("/api/v1/history?days=30", "/api/v1/bandwidth?days=30",
              "/api/v1/users", "/api/v1/item/i1", "/api/v1/now-playing"):
    check(ADRESA not in klient.get(cesta, headers=H).text,
          f"{cesta.split('?')[0]} neprozradí IP adresu")
provoz = klient.get("/api/v1/bandwidth?days=30", headers=H).json()
check(provoz["total_bytes"] > 0 and provoz["origin"]["internet"]["plays"] == 4,
      f"provoz je sečtený podle původu ({provoz['origin']['internet']['plays']} z internetu)")

print()
print("--- jeden titul ---")
titul = klient.get("/api/v1/item/i1", headers=H).json()
check(titul["name"] == "Duna" and titul["plays"] == 3 and titul["viewers"] == 2,
      f"detail: {titul['name']}, {titul['plays']} spuštění, {titul['viewers']} diváci")
check(titul["versions"] == [], "verze prázdné, když je soubor jeden")
for spatne in ("neexistuje", "i1;DROP TABLE items", "../../etc/passwd",
               "x" * 200, "%00"):
    odpoved = klient.get(f"/api/v1/item/{spatne}", headers=H)
    check(odpoved.status_code == 404, f"id {spatne[:20]!r} -> 404 ({odpoved.status_code})")
check(db.query_value("SELECT COUNT(*) FROM items") == 4, "tabulka items přežila")

print()
print("--- nesmysl v parametrech nic neshodí ---")
for cesta in ("/api/v1/least-played?days=abc&limit=-5&min_size_mb=x",
              "/api/v1/least-played?limit=999999999",
              "/api/v1/top-items?kind=DROP&limit=abc",
              "/api/v1/history?days=1e400&limit=&user=" + "a" * 500,
              "/api/v1/unwatched?days=0&limit=0",
              "/api/v1/insights?days=-3&limit=1;--",
              "/api/v1/recently-added?limit=1%20OR%201=1",
              "/api/v1/play-methods?days=NaN"):
    odpoved = klient.get(cesta, headers=H)
    check(odpoved.status_code == 200, f"{cesta[:60]} -> {odpoved.status_code}")
velky = klient.get("/api/v1/least-played?limit=999999999", headers=H).json()
check(len(velky["items"]) <= 500, "strop na počet řádků drží")
druh = klient.get("/api/v1/top-items?kind=DROP", headers=H).json()
check(druh["kind"] == "both", f"neznámý druh spadne na both ({druh['kind']})")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
