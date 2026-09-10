# -*- coding: utf-8 -*-
r"""Ruční zapomenutí diváka smaže hned, soubor databáze přepíše až v noci.

`db.preskladat()` (VACUUM) přepisuje celý soubor a SQLite na tu dobu
drží zámek - na velké databázi minutu, během které aplikace stojí. Po
nočním odklízení to nikomu nevadí; po kliknutí na „Ano, zapomenout" by
to zaseklo všechno. Proto se kliknutím jen maže a přepis si aplikace
poznamená na čas úlohy Odklízení historie.

Co se tu ověřuje:

* `hned=False` smaže řádky, ale soubor nechá být - a **zapíše dluh**,
* `hned=True` (noční cesta) se chová jako dřív: stopa zmizí hned,
* termín je první čas Odklízení **po** žádosti - kdo klikne pět minut
  po něm, čeká do zítřka; kdo klikne před ním, dočká se dnes,
* plánovač dluh splatí, až termín nastane - ne dřív,
* noční odklízení, které samo přepisuje, dluh smaže také,
* po neúspěšném přepisu dluh zůstane (zkusí se zase další noc),
* stránka a hláška o čekajícím přepisu říkají.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_odlozene_preskladani.py
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
DB = Path(_tmp) / "odlozene.db"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(DB)
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic-dostatecne-dlouhy"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

from jellyscope import db, odklizeni, tasks  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


db.init_db()


def naplnit(user_id: str, znamka: str, kolik: int) -> None:
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO users (id, name, is_administrator, is_disabled,"
            " synced_at) VALUES (?,?,0,0,?)", (user_id, znamka, db.utcnow()))
        for poradi in range(kolik):
            conn.execute(
                "INSERT INTO playback (session_key, user_id, user_name,"
                " item_id, started_at, last_seen_at, watched_seconds,"
                " is_active) VALUES (?,?,?,?,?,?,?,0)",
                (f"{user_id}-{poradi}", user_id, znamka, f"i-{poradi}",
                 "2020-01-01 10:00:00", "2020-01-01 10:20:00", 1200))
        conn.commit()


def stopy(znamka: str) -> int:
    """Kolikrát je jméno v souboru databáze (i v deníku vedle ní)."""
    with db.connect() as conn:
        conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
    pocet = 0
    for pripona in ("", "-wal"):
        cesta = Path(str(DB) + pripona)
        if cesta.exists():
            pocet += cesta.read_bytes().count(znamka.encode())
    return pocet


ZNAMKA = "MirekNeobvykleJmeno"
naplnit("u-mirek", ZNAMKA, 200)

print("--- ruční zapomenutí jen maže, přepis zapíše jako dluh ---")
check(odklizeni.preskladani_ceka() == "", "na začátku nic nečeká")
vysledek = odklizeni.zapomen_uzivatele("u-mirek", hned=False)
check(vysledek["smazano"] == 200, f"smazalo se 200 řádků ({vysledek['smazano']})")
check(db.query_value("SELECT COUNT(*) FROM playback WHERE user_id = ?",
                     ("u-mirek",)) == 0, "z databáze jsou pryč hned")
zbylo = stopy(ZNAMKA)
check(zbylo > 100, f"ale v souboru stopa zatím zůstala ({zbylo}x) - přepis nebyl")
check(odklizeni.preskladani_ceka() != "", "a dluh je zapsaný")

print()
print("--- termín: první čas Odklízení po žádosti ---")
db.set_setting("task_purge_time", "04:45")
db.forget_settings()


def termin_pro(zadost_mistni: datetime) -> datetime | None:
    """Podstrčí čas žádosti (zapisuje se v UTC) a vrátí spočítaný termín."""
    utc = (zadost_mistni.astimezone() if zadost_mistni.tzinfo else
           zadost_mistni.replace(tzinfo=datetime.now().astimezone().tzinfo))
    db.set_setting(odklizeni.ODLOZENE_KLIC,
                   utc.astimezone(__import__("datetime").timezone.utc)
                   .strftime(db.TIME_FORMAT))
    db.forget_settings()
    return tasks.kdy_odlozene_preskladani(ted=zadost_mistni)


dnes = datetime(2026, 9, 10, 0, 0)
cil = termin_pro(dnes.replace(hour=15, minute=0))
check(cil == dnes.replace(day=11, hour=4, minute=45),
      f"žádost v 15:00 -> zítra 04:45 ({cil})")
cil = termin_pro(dnes.replace(hour=4, minute=50))
check(cil == dnes.replace(day=11, hour=4, minute=45),
      f"žádost v 04:50 (pět minut po) -> zítra, ne za pět minut ({cil})")
cil = termin_pro(dnes.replace(hour=2, minute=0))
check(cil == dnes.replace(hour=4, minute=45),
      f"žádost ve 2:00 -> ještě dnes 04:45 ({cil})")

db.set_setting(odklizeni.ODLOZENE_KLIC, "")
db.forget_settings()
check(tasks.kdy_odlozene_preskladani() is None, "bez dluhu žádný termín")

print()
print("--- plánovač splácí až v termínu ---")
# Cerstva zadost: termin je az v noci, takze se ted nesmi nic stat.
odklizeni.odloz_preskladani()
asyncio.run(tasks._dodelej_odlozene_preskladani())
check(odklizeni.preskladani_ceka() != "", "před termínem dluh zůstává")
check(stopy(ZNAMKA) > 100, "a soubor se nepřepsal")

# Zadost "z predevcirem": termin davno minul.
stara = (datetime.now().astimezone().astimezone(
    __import__("datetime").timezone.utc) - timedelta(days=2))
db.set_setting(odklizeni.ODLOZENE_KLIC, stara.strftime(db.TIME_FORMAT))
db.forget_settings()
asyncio.run(tasks._dodelej_odlozene_preskladani())
check(odklizeni.preskladani_ceka() == "", "po termínu je dluh splacený")
po = stopy(ZNAMKA)
check(po <= 1, f"a stopa v souboru je pryč ({po}, jedna je účet v users)")

print()
print("--- noční odklízení přepisuje samo, dluh tím mizí ---")
naplnit("u-jana", "JanaNeobvykleJmeno", 50)
odklizeni.zapomen_uzivatele("u-jana", hned=False)
check(odklizeni.preskladani_ceka() != "", "po zapomenutí dluh je")
naplnit("u-karel", "KarelNeobvykleJmeno", 30)          # rok 2020 = stare
odklid = odklizeni.smaz_stare(odklizeni.MIN_DNU)
check(odklid["smazano"] == 30, f"odklízení smazalo 30 starých ({odklid['smazano']})")
check(odklizeni.preskladani_ceka() == "", "a dluh smazalo s sebou")
check(stopy("JanaNeobvykleJmeno") <= 1, "po Janě v souboru nic nezbylo")

print()
print("--- hned=True se chová jako dřív ---")
naplnit("u-petr", "PetrNeobvykleJmeno", 40)
odklizeni.zapomen_uzivatele("u-petr")
check(stopy("PetrNeobvykleJmeno") <= 1, "stopa zmizela hned")
check(odklizeni.preskladani_ceka() == "", "a žádný dluh nevznikl")

print()
print("--- neúspěšný přepis dluh nechá ---")
puvodni = db.preskladat
db.preskladat = lambda: False
try:
    odklizeni.odloz_preskladani()
    check(odklizeni.dokonci_odlozene_preskladani() is False, "hlásí neúspěch")
    check(odklizeni.preskladani_ceka() != "", "a dluh zůstal na další noc")
finally:
    db.preskladat = puvodni
odklizeni.dokonci_odlozene_preskladani()
check(odklizeni.preskladani_ceka() == "", "napodruhé splaceno")

print()
print("--- routa a stránka ---")
from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, web  # noqa: E402

accounts.create("spravce", "dlouheheslo", is_admin=True)
klient = TestClient(web.app)
klient.post("/login", data={"username": "spravce", "password": "dlouheheslo"})
naplnit("u-eva", "EvaNeobvykleJmeno", 20)
odpoved = klient.post("/settings/historie/zapomen", data={"user_id": "u-eva"},
                      follow_redirects=True)
text = re.sub(r"<[^>]+>", " ", odpoved.text)
text = re.sub(r"\s+", " ", text)
check("smazána (20 přehrávání)" in text, "hláška říká, kolik se smazalo")
check("Místo v souboru databáze se uvolní" in text,
      "a že místo v souboru se uvolní až později")
check("soubor databáze se přepíše" in text,
      "karta Odklízení ukazuje čekající přepis")
check(stopy("EvaNeobvykleJmeno") > 10, "kliknutí soubor nepřepsalo")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
