# -*- coding: utf-8 -*-
r"""Statistika rychlosti převodu na stránce Zjištění (a v API).

Průměr, nejpomalejší a nejrychlejší převod, kolikrát převod nestíhal
video, a totéž rozpadlé podle toho, čím se převádí (procesor, QSV...).

* Jednotkou je jedno přehrávání a jeho průměr (součet / počet vzorků).
* Celkový průměr je vážený časem převodu, ne prostý průměr přehrávání.
* Přehrávání bez měření (starší než 1.7.3, import) se nepočítá vůbec.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_rychlost_prevodu.py
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "rychlost.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic-dostatecne-dlouhy"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_DEMO"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, api, db, insights, web  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


db.init_db()
accounts.create("spravce", "dlouheheslo", is_admin=True)
kdy = (datetime.now(timezone.utc) - timedelta(days=2)).strftime(db.TIME_FORMAT)


def prevod(klic: str, nazev: str, hw: str | None, fps: int, vzorku: int,
           video: int | None = 24) -> None:
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name,"
            " item_type, started_at, last_seen_at, watched_seconds, is_active,"
            " play_method, transcode_hw, transcode_fps_soucet, transcode_fps_vzorku,"
            " video_fps) VALUES (?, 'u1', 'Jana', ?, ?, 'Movie', ?, ?, 600, 0,"
            " 'Transcode', ?, ?, ?, ?)",
            (klic, klic, nazev, kdy, kdy, hw, fps * vzorku, vzorku, video))
        conn.commit()


# Procesor: 40 fps dlouho (100 vzorku), 18 fps kratce (10) - ten nestihal.
prevod("a", "Duna", None, 40, 100)
prevod("b", "Amélie", None, 18, 10)
# QSV: 150 a 210 fps.
prevod("c", "Matrix", "qsv", 150, 50)
prevod("d", "Kolja", "qsv", 210, 40)
# Stare prehravani bez mereni - nesmi nic ovlivnit.
prevod("e", "Starý film", None, 0, 0)

print("--- čísla ---")
r = insights.rychlost_prevodu(30)
check(r["prehravani"] == 4, f"čtyři přehrávání s měřením, staré se nepočítá ({r['prehravani']})")
# Vazeny: (40*100 + 18*10 + 150*50 + 210*40) / 200 = 20080 / 200 = 100,4
check(r["prumer"] == 100, f"průměr je vážený časem převodu ({r['prumer']}, prostý by byl 105)")
check(r["nejmin"]["fps"] == 18 and r["nejmin"]["label"] == "Amélie",
      f"nejpomalejší: {r['nejmin']['label']} {r['nejmin']['fps']} fps")
check(r["nejvic"]["fps"] == 210 and r["nejvic"]["label"] == "Kolja",
      f"nejrychlejší: {r['nejvic']['label']} {r['nejvic']['fps']} fps")
check(r["nestiha"] == 1 and round(r["nestiha_podil"]) == 25,
      f"nestíhalo jedno ze čtyř ({r['nestiha']}, {r['nestiha_podil']:.0f} %)")

hw = {s["hw"]: s for s in r["podle_hw"]}
check(set(hw) == {"", "qsv"}, f"dvě skupiny: procesor a QSV ({sorted(hw)})")
check(hw[""]["prumer"] == 38 and hw[""]["nejmin"]["fps"] == 18 and hw[""]["nejvic"]["fps"] == 40,
      f"procesor: průměr {hw['']['prumer']}, 18–40")
check(hw["qsv"]["prumer"] == 177 and hw["qsv"]["nestiha"] == 0,
      f"QSV: průměr {hw['qsv']['prumer']}, nikdy nenestíhalo")

print()
print("--- stránka Zjištění ---")
k = TestClient(web.app)
k.post("/login", data={"username": "spravce", "password": "dlouheheslo", "zpusob": "mistni"})
html = k.get("/insights?days=30").text
zacatek = html.index('id="rychlost-prevodu"')
karta = html[zacatek:html.index("</table>", zacatek)]
for co in ("100 fps", "18 fps", "Amélie", "210 fps", "Kolja", "QSV", "procesor"):
    check(co in karta, f"karta ukazuje {co}")

print()
print("--- API ---")
token = api.vytvor("test")["token"]
data = k.get("/api/v1/insights?days=30",
             headers={"Authorization": f"Bearer {token}"}).json()["transcode_speed"]
check(data["average_fps"] == 100 and data["slowest_fps"] == 18 and data["fastest_fps"] == 210,
      f"API vrací totéž ({data['average_fps']}, {data['slowest_fps']}, {data['fastest_fps']})")
check({s["hardware"] for s in data["by_hardware"]} == {"cpu", "qsv"}, "i rozpad podle hardwaru")

print()
print("--- bez dat ---")
with db.connect() as conn:
    conn.execute("DELETE FROM playback")
    conn.commit()
check(insights.rychlost_prevodu(30)["prehravani"] == 0, "prázdno je prázdno, ne chyba")
check("Zatím žádný převod" in k.get("/insights?days=30").text, "a stránka to řekne")
check(k.get("/api/v1/insights?days=30", headers={"Authorization": f"Bearer {token}"}
            ).json()["transcode_speed"] == {"playbacks": 0, "by_hardware": []},
      "API taky")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
