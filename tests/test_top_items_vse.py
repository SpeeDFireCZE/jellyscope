# -*- coding: utf-8 -*-
r"""Celý žebříček nejsledovanějších titulů - okno nad grafem.

Graf na Přehledu ukazuje deset titulů. Odkaz pod ním otevře okno se
všemi; bez JavaScriptu vede na stránku se stejnou tabulkou. Jedna routa
obslouží obojí a pozná se to podle hlavičky, kterou posílá `fetch`.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_top_items_vse.py
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
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "top.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic-dostatecne-dlouhy"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, db, web  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


db.init_db()
accounts.create("spravce", "dlouheheslo", is_admin=True)
accounts.create("ctenar", "dlouheheslo", is_admin=False)

# Ctrnact filmu, kazdy jinak dlouho sledovany - vic nez deset, aby bylo
# co schovat za graf.
kdy = (datetime.now(timezone.utc) - timedelta(days=1)).strftime(db.TIME_FORMAT)
with db.connect() as conn:
    for i in range(1, 15):
        conn.execute("INSERT INTO items (id, name, type, is_missing)"
                     " VALUES (?, ?, 'Movie', 0)", (f"f{i}", f"FilmQX{i:02d}"))
        conn.execute(
            "INSERT INTO playback (session_key, user_id, user_name, item_id,"
            " item_name, item_type, started_at, last_seen_at, watched_seconds,"
            " is_active) VALUES (?, 'u1', 'Jana', ?, ?, 'Movie', ?, ?, ?, 0)",
            (f"s{i}", f"f{i}", f"FilmQX{i:02d}", kdy, kdy, 600 * i))
    conn.execute("INSERT INTO items (id, name, type, series_name, series_id, is_missing)"
                 " VALUES ('e1', 'Díl 1', 'Episode', 'SerialQX', 'ser1', 0)")
    conn.execute("INSERT INTO items (id, name, type, is_missing)"
                 " VALUES ('ser1', 'SerialQX', 'Series', 0)")
    conn.execute(
        "INSERT INTO playback (session_key, user_id, user_name, item_id,"
        " item_name, item_type, series_name, started_at, last_seen_at,"
        " watched_seconds, is_active) VALUES ('se', 'u1', 'Jana', 'e1', 'Díl 1',"
        " 'Episode', 'SerialQX', ?, ?, 99999, 0)", (kdy, kdy))
    conn.commit()

k = TestClient(web.app)
k.post("/login", data={"username": "spravce", "password": "dlouheheslo",
                       "zpusob": "mistni"})

print("--- na Přehledu je odkaz na celý žebříček ---")
prehled = k.get("/?top_kind=both").text
check('data-okno-nacist="okno-top-vse"' in prehled, "odkaz nese id okna")
check('href="/top-items?kind=both"' in prehled, "a adresu s druhem")
check('id="okno-top-vse"' in prehled, "okno je na stránce")
check(prehled.count("FilmQX") <= 10 * 2, "graf sám ukazuje nanejvýš deset")

print()
print("--- výřez pro okno ---")
vyrez = k.get("/top-items?kind=both", headers={"X-Requested-With": "fetch"}).text
check("<nav" not in vyrez, "je to výřez, ne celá stránka")
check(vyrez.count("<tr>") == 1 + 15, f"všech patnáct titulů ({vyrez.count('<tr>') - 1})")
check(vyrez.index("SerialQX") < vyrez.index("FilmQX14"),
      "seřazeno od nejsledovanějšího (seriál je první)")
check("seriál" in vyrez, "seriál je označený")
check('href="/item/f14"' in vyrez, "film jde prokliknout")

print()
print("--- bez JavaScriptu celá stránka ---")
stranka = k.get("/top-items?kind=both").text
check("<nav" in stranka and "FilmQX01" in stranka, "stránka s menu a tabulkou")

print()
print("--- filtr druhu platí ---")
jen_filmy = k.get("/top-items?kind=movies", headers={"X-Requested-With": "fetch"}).text
check("SerialQX" not in jen_filmy and "FilmQX01" in jen_filmy, "jen filmy")
jen_serialy = k.get("/top-items?kind=series", headers={"X-Requested-With": "fetch"}).text
check("SerialQX" in jen_serialy and "FilmQX01" not in jen_serialy, "jen seriály")

print()
print("--- práva: čtenář bez žebříčků nesmí ---")
db.set_setting("ctenar_vidi_zebricky", "0")
db.forget_settings()
c = TestClient(web.app)
c.post("/login", data={"username": "ctenar", "password": "dlouheheslo",
                       "zpusob": "mistni"})
check(c.get("/top-items", follow_redirects=False).status_code == 403,
      "žebříček je zavřený jako Zjištění")
db.set_setting("ctenar_vidi_zebricky", "1")
db.forget_settings()
check(c.get("/top-items", follow_redirects=False).status_code == 200,
      "a se zapnutými žebříčky otevřený")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
