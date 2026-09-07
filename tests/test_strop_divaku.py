# -*- coding: utf-8 -*-
r"""Kolik diváků ukáže stránka Jazyky, než zbytek schová do okna.

Stropy jsou dva a je to schválně: na širokou obrazovku se pruhů vejde
víc než na telefon, kde je pruh přes celou šířku a hned pod diváky je
ještě legenda a tabulka. Server přitom neví, na čem se člověk dívá -
pošle obojí a rozhodne CSS (`jen-desktop` / `jen-mobil`).

Past, kterou to hlídá: mezi oběma stropy stránka ukazuje VŠECHNY pruhy
(žádný souhrnný knoflík místo nich), ale ty nad mobilním stropem musí
být zabalené tak, aby je úzká obrazovka schovala - a okno na stránce
musí být i tehdy, když se schovává jen na mobilu. Bez něj by tlačítko
„zobrazit všech" nemělo co otevřít.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_strop_divaku.py
"""
from __future__ import annotations

import os
import re
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "stropy.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYSCOPE_DEMO"] = "0"

from jellyscope import db  # noqa: E402

failures = 0


def check(podminka: bool, popis: str, detail: str = "") -> None:
    global failures
    print(f"{'OK    ' if podminka else 'CHYBA '} {popis} {detail}")
    if not podminka:
        failures += 1


db.init_db()

# Osm divaku, kazdy s hodinou v jinem jazyce. Osm proto, ze lezi mezi
# vychozimi stropy (mobil 5, web 10) - presne v pasmu, kde se stranka
# ma chovat na kazde sirce jinak.
DIVAKU = 8
zacatek = datetime.now(timezone.utc) - timedelta(days=1)
with db.connect() as conn:
    for cislo in range(1, DIVAKU + 1):
        conn.execute(
            "INSERT INTO playback (session_key, user_id, user_name, item_id,"
            " item_name, started_at, last_seen_at, ended_at, watched_seconds,"
            " paused_seconds, is_paused, is_active, audio_language)"
            " VALUES (?,?,?,'film','Duna',?,?,?,3600,0,0,0,?)",
            (f"s{cislo}", f"u{cislo}", f"Divak {cislo}",
             zacatek.strftime(db.TIME_FORMAT),
             (zacatek + timedelta(hours=1)).strftime(db.TIME_FORMAT),
             (zacatek + timedelta(hours=1)).strftime(db.TIME_FORMAT),
             "cs" if cislo % 2 else "en"))
    conn.commit()

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, langstats, web  # noqa: E402
from jellyscope.web import app  # noqa: E402

accounts.create("spravce", "dlouheheslo", is_admin=True)
client = TestClient(app)
client.post("/login", data={"username": "spravce", "password": "dlouheheslo"},
            follow_redirects=False)


print("--- mezi stropy: web ukáže všechny, mobil jen část ---")
lidi = langstats.languages_by_user(30, {})
check(len(lidi) == DIVAKU, f"data mají {len(lidi)} diváků")
check(web._stropy()["strop_lidi"] == 10 and web._stropy()["strop_lidi_mobil"] == 5,
      f"výchozí stropy jsou 10 a 5 ({web._stropy()})")

html = client.get("/languages?days=30").text
karta = html.split('id="okno-jazyky"', 1)[0]
schovanych = len(re.findall(r'<div class="jen-desktop">', karta))
check(schovanych == DIVAKU - 5,
      f"nad mobilním stropem se schová {schovanych} pruhů (čekáno {DIVAKU - 5})")
check('class="btn-row jen-mobil"' in karta,
      "a na mobilu je místo nich tlačítko")
check('id="okno-jazyky"' in html,
      "okno je na stránce, i když se schovává jen na mobilu")
check('<strong>' not in karta or "Diváci jsou skrytí" not in karta,
      "na široké obrazovce se souhrnný knoflík nekreslí")

print()
print("--- pod oběma stropy se neschovává nic ---")
# Nevakuovost: kdyby se `jen-desktop` psalo vzdycky, nasel by ho
# i tenhle pripad - a test vyse by neznamenal nic.
client.post("/settings/interface", follow_redirects=False,
            data={"ui_max_streams": "10", "ui_max_streams_mobile": "3",
                  "ui_max_viewers": "10", "ui_max_viewers_mobile": "10"})
db.forget_settings()
html = client.get("/languages?days=30").text
karta = html.split('id="okno-jazyky"', 1)[0]
check('<div class="jen-desktop">' not in karta, "žádný pruh se neschovává")
check('class="btn-row jen-mobil"' not in karta, "ani tlačítko na mobil")
check('id="okno-jazyky"' not in html, "a okno se vůbec nekreslí")

print()
print("--- pod mobilním stropem, nad webovým: schová se všechno ---")
client.post("/settings/interface", follow_redirects=False,
            data={"ui_max_streams": "10", "ui_max_streams_mobile": "3",
                  "ui_max_viewers": "3", "ui_max_viewers_mobile": "3"})
db.forget_settings()
html = client.get("/languages?days=30").text
karta = html.split('id="okno-jazyky"', 1)[0]
check("Diváci jsou skrytí" in karta, "místo pruhů je souhrnný knoflík")
check('<div class="jen-desktop">' not in karta,
      "a jednotlivé pruhy se už neřeší - schované jsou všechny")
check('id="okno-jazyky"' in html, "okno je připravené")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
