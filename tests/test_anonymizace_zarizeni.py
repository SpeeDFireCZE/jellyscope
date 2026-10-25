# -*- coding: utf-8 -*-
r"""Anonymizace názvů zařízení.

Zařízení se v Jellyfinu často jmenují po lidech („Petrův Shield",
„Janin iPhone"). Se zapnutým skrýváním jmen a adres proto musí zmizet
i názvy cizích zařízení - jinak by pseudonym „Divák 3" prozradil
sousední sloupec. Přehrávač (Jellyfin Web, Android TV…) zůstává.
Hlídá se tu:

* cizí zařízení = stálá přezdívka „Zařízení N", vlastní zůstává;
* stránky (historie) i API (historie, filtr `device[...]`);
* bez skrývání jmen se nic nemění.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_anonymizace_zarizeni.py
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "zarizeni.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYSCOPE_DEMO"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, api, db, pristup  # noqa: E402
from jellyscope.web import app  # noqa: E402

failures = 0


def check(podminka: bool, popis: str) -> None:
    global failures
    print(f"{'OK    ' if podminka else 'CHYBA '} {popis}")
    if not podminka:
        failures += 1


db.init_db()
kdy = (datetime.now(timezone.utc) - timedelta(days=1)).strftime(db.TIME_FORMAT)
with db.connect() as conn:
    conn.execute("INSERT INTO libraries (id, name, collection_type) VALUES ('lib1', 'Filmy', 'movies')")
    conn.execute("INSERT INTO users (id, name, is_administrator, is_disabled)"
                 " VALUES ('u1', 'Jana', 0, 0), ('u2', 'Petr', 0, 0)")
    conn.execute("INSERT INTO items (id, name, type, library_id, tech_source, is_missing)"
                 " VALUES ('i1', 'Duna', 'Movie', 'lib1', 'jellyfin', 0)")
    for k, (kdo, jmeno, zarizeni, prehravac) in enumerate((
            ("u1", "Jana", "Janin iPhone", "Jellyfin iOS"),
            ("u2", "Petr", "Petruv Shield", "Android TV"))):
        conn.execute(
            "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name,"
            " item_type, started_at, last_seen_at, watched_seconds, paused_seconds,"
            " is_paused, is_active, play_method, client, device_name, remote_address)"
            " VALUES (?, ?, ?, 'i1', 'Duna', 'Movie', ?, ?, 3600, 0, 0, 0, 'DirectPlay',"
            " ?, ?, '203.0.113.7')", (f"s{k}", kdo, jmeno, kdy, kdy, prehravac, zarizeni))
    conn.commit()

print("--- pravidlo ---")
radky = pristup.anonymizuj([
    {"user_id": "u1", "user_name": "Jana", "device_name": "Janin iPhone", "client": "Jellyfin iOS"},
    {"user_id": "u2", "user_name": "Petr", "device_name": "Petruv Shield", "client": "Android TV"},
], "u1")
check(radky[0]["device_name"] == "Janin iPhone", "vlastní zařízení zůstává")
check(radky[1]["device_name"].startswith("Zařízení ") and radky[1]["client"] == "Android TV",
      f"cizí zařízení má přezdívku, přehravač zůstává ({radky[1]['device_name']})")
znovu = pristup.anonymizuj([{"user_id": "u2", "device_name": "Petruv Shield"}], "u1")
check(znovu[0]["device_name"] == radky[1]["device_name"], "přezdívka je stálá")
souhrn = pristup.anonymizuj([{"label": "Petruv Shield", "device_name": "Petruv Shield",
                               "user_id": "u2", "hours": 1.0}], "u1")
check(souhrn[0]["label"] == souhrn[0]["device_name"] != "Petruv Shield"
      and souhrn[0]["label"].startswith("Zařízení "),
      f"souhrn podle zařízení (detail diváka): popisek je přezdívka zařízení, ne „Divák“ ({souhrn[0]['label']})")

# Cisla zarizeni NE podle abecedy: kdo zna jmena zarizeni (rodina), by je
# podle poradi priradil zpatky. A nove zarizeni nesmi posunout cisla
# tech starsich - pocita se podle prvniho vyskytu.
pred_novym = pristup.anonymizuj([{"user_id": "u2", "device_name": "Petruv Shield"},
                                 {"user_id": "u2", "device_name": "Janin iPhone"}], "u9")
with db.connect() as conn:
    conn.execute(
        "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name,"
        " item_type, started_at, last_seen_at, watched_seconds, paused_seconds,"
        " is_paused, is_active, play_method, client, device_name)"
        " VALUES ('s9', 'u2', 'Petr', 'i1', 'Duna', 'Movie', ?, ?, 60, 0, 0, 0,"
        " 'DirectPlay', 'Web', 'Aaa novy')", (kdy, kdy))
    conn.commit()
pristup.zapomen_zarizeni()
po_novem = pristup.anonymizuj([{"user_id": "u2", "device_name": "Petruv Shield"},
                               {"user_id": "u2", "device_name": "Janin iPhone"},
                               {"user_id": "u2", "device_name": "Aaa novy"}], "u9")
check([r["device_name"] for r in po_novem[:2]] == [r["device_name"] for r in pred_novym]
      and po_novem[2]["device_name"].endswith(" 3"),
      f"nové zařízení dostane další číslo, starší se neposunou ({[r['device_name'] for r in po_novem]})")

# Historie API se anonymizuje po dávkách - pořadí zařízení se nesmí
# načítat znovu pro každou (prohledává celou tabulku přehrání).
volani = []
puvodni = pristup._poradi_zarizeni
pristup._poradi_zarizeni = lambda: volani.append(1) or puvodni()
pristup.zapomen_zarizeni()
for _ in range(5):
    pristup.anonymizuj([{"user_id": "u2", "device_name": "Petruv Shield"}], "u9")
pristup._poradi_zarizeni = puvodni
check(len(volani) == 1, f"pořadí zařízení se načte jednou, ne pro každou dávku ({len(volani)}×)")

print("--- stránky ---")
accounts.create("spravce", "dlouheheslo", is_admin=True)
accounts.create("ctenar", "ctenarheslo")
db.set_setting("ctenar_anonymizace", "1")
db.forget_settings()


def prihlas(jmeno: str, heslo: str) -> TestClient:
    k = TestClient(app)
    k.post("/login", data={"username": jmeno, "password": heslo, "zpusob": "mistni"},
           follow_redirects=False)
    return k


ctenar = prihlas("ctenar", "ctenarheslo")
historie = ctenar.get("/history").text
check("Petruv Shield" not in historie and "Janin iPhone" not in historie
      and "Zařízení " in historie, "čtenář se skrytými jmény nevidí názvy zařízení")
check("Android TV" in historie, "přehrávač vidí dál")
detail = ctenar.get("/users/" + pristup.anonymizuj([{"user_id": "u2"}], "")[0]["user_id"])
check("Petruv Shield" not in detail.text, "ani v detailu diváka")
# Navod k API na Muj ucet popisuje pole (device: text) - to je dokumentace,
# ne data o divacich, anonymizace na nej nesmi sahnout.
db.set_setting("ctenar_smi_klic", "1")
db.forget_settings()
ucet = ctenar.get("/account").text
check('<code>device</code>&nbsp;<span class="api-typ">text</span>' in ucet
      and '<code>user_id</code>&nbsp;<span class="api-typ">text</span>' in ucet,
      "návod k API na Můj účet ukazuje typy polí i se skrytými jmény")

# Interni JSON /api/now-playing (mimo API s klici): taky jen anonymizovane.
with db.connect() as conn:
    conn.execute(
        "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name,"
        " item_type, started_at, last_seen_at, watched_seconds, paused_seconds,"
        " is_paused, is_active, play_method, client, device_name, remote_address)"
        " VALUES ('zive', 'u2', 'Petr', 'i1', 'Duna', 'Movie', ?, ?, 60, 0, 0, 1,"
        " 'DirectPlay', 'Android TV', 'Petruv Shield', '203.0.113.7')",
        (datetime.now(timezone.utc).strftime(db.TIME_FORMAT),) * 2)
    conn.commit()
zive = ctenar.get("/api/now-playing").text
check("Petruv Shield" not in zive and "203.0.113.7" not in zive and '"Petr"' not in zive,
      "interní /api/now-playing pro čtenáře se skrytými jmény: žádné skutečné jméno, zařízení ani IP")
spravce = prihlas("spravce", "dlouheheslo")
check("Petruv Shield" in spravce.get("/history").text, "správce bez skrývání vidí skutečné názvy")

print("--- API ---")
klic = api.vytvor("Widget", None, api.zabal_prava_klice([], True, True))["token"]
H = {"Authorization": f"Bearer {klic}"}
anonym = TestClient(app)
zaznamy = anonym.get("/api/v1/history", headers=H).json().get("playbacks", [])
zarizeni = {z["device"] for z in zaznamy}
check(zaznamy and not zarizeni & {"Petruv Shield", "Janin iPhone"}
      and all(z.startswith("Zařízení ") for z in zarizeni),
      f"API historie: přezdívky zařízení ({zarizeni})")
check(anonym.get("/api/v1/history?device[contains]=Petr", headers=H).json().get("playbacks") == [],
      "filtr skutečný název zařízení nenajde")
jedno = sorted(zarizeni)[0] if zarizeni else "x"
check(len(anonym.get(f"/api/v1/history?device[equals]={jedno}", headers=H).json().get("playbacks", [])) == 1,
      "filtr podle přezdívky funguje")
otevreny = api.vytvor("Grafana")["token"]
check({z["device"] for z in anonym.get("/api/v1/history", headers={
      "Authorization": f"Bearer {otevreny}"}).json()["playbacks"]} == {"Petruv Shield", "Janin iPhone",
                                                                     "Aaa novy"},
      "klíč bez skrývání jmen vidí skutečné názvy")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
