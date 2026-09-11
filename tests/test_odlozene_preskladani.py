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
* noční odklízení, které samo přepisuje, dluh smaže - ale jen když
  v koši nic nečeká,
* po neúspěšném přepisu dluh zůstane (zkusí se zase další noc),
* hláška i okno Obnovit říkají, do kdy jde zásah vzít zpět.

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

# "Prepis nebyl" se pozna z toho, ze se db.preskladat() nezavolalo - ne
# z bajtu v souboru. Linuxove distribuce prekladaji SQLite se
# SECURE_DELETE, ktere smazane radky vynuluje uz pri DELETE, takze by
# tam stopa chybela i bez prepisu a test by na Linuxu lhal.
PREPISU = {"pocet": 0}
_puvodni_preskladat = db.preskladat


def _pocitane_preskladat() -> bool:
    PREPISU["pocet"] += 1
    return _puvodni_preskladat()


db.preskladat = _pocitane_preskladat


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
vysledek = odklizeni.zapomen_uzivatele("u-mirek", do_kose=True)
check(vysledek["smazano"] == 200, f"smazalo se 200 řádků ({vysledek['smazano']})")
check(db.query_value("SELECT COUNT(*) FROM playback WHERE user_id = ?",
                     ("u-mirek",)) == 0, "z databáze jsou pryč hned")
check(PREPISU["pocet"] == 0, "ale soubor se nepřepsal (preskladat se nevolalo)")
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
check(PREPISU["pocet"] == 0, "a soubor se nepřepsal")

# Zadost "z predevcirem": termin davno minul.
stara = (datetime.now().astimezone().astimezone(
    __import__("datetime").timezone.utc) - timedelta(days=2))
db.set_setting(odklizeni.ODLOZENE_KLIC, stara.strftime(db.TIME_FORMAT))
db.forget_settings()
asyncio.run(tasks._dodelej_odlozene_preskladani())
check(odklizeni.preskladani_ceka() == "", "po termínu je dluh splacený")
check(PREPISU["pocet"] == 1, "přepis proběhl právě jednou")
po = stopy(ZNAMKA)
# Dve stopy smi zustat a obe jsou zamerne: ucet v `users` (ten je
# v Jellyfinu, odsud se nemaze) a radek v `zapomenuti`, podle ktereho
# stranka nabizi obnovu ze zalohy. Zmizi s ni.
check(po <= 2, f"a stopa po přehráváních je pryč ({po}: účet a záznam o zapomenutí)")

print()
print("--- noční odklízení přepisuje samo, dluh tím mizí ---")
naplnit("u-jana", "JanaNeobvykleJmeno", 50)
odklizeni.zapomen_uzivatele("u-jana", do_kose=True)
check(odklizeni.preskladani_ceka() != "", "po zapomenutí dluh je")
naplnit("u-karel", "KarelNeobvykleJmeno", 30)          # rok 2020 = stare
odklid = odklizeni.smaz_stare(odklizeni.MIN_DNU)
check(odklid["smazano"] == 30, f"odklízení smazalo 30 starých ({odklid['smazano']})")
check(odklizeni.preskladani_ceka() != "", "dluh zůstal (v koši je Jana)")
# Jana je v kosi, takze odklizeni jeji radky smazat nesmi - a objednavku
# na prepis nesmi zrusit, jinak by kos neměl kdy zmizet.
check(odklizeni.kos_ma_radky(), "Jana zůstává v koši")
check(odklizeni.preskladani_ceka() != "",
      "a objednávka na přepis zůstala - koš se musí mít kdy vysypat")

print()
print("--- mimo koš (do_kose=False) se maže rovnou ---")
odklizeni.vysyp_kos()                      # at uklidime po Jane
odklizeni.dokonci_odlozene_preskladani()
naplnit("u-petr", "PetrNeobvykleJmeno", 40)
odklizeni.zapomen_uzivatele("u-petr")
check(stopy("PetrNeobvykleJmeno") <= 1, "stopa zmizela hned")
check(odklizeni.preskladani_ceka() == "", "a žádný dluh nevznikl")

print()
print("--- neúspěšný přepis dluh nechá ---")
db.preskladat = lambda: False
try:
    odklizeni.odloz_preskladani()
    check(odklizeni.dokonci_odlozene_preskladani() is False, "hlásí neúspěch")
    check(odklizeni.preskladani_ceka() != "", "a dluh zůstal na další noc")
finally:
    db.preskladat = _pocitane_preskladat
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
pred_klikem = PREPISU["pocet"]
odpoved = klient.post("/settings/historie/zapomen", data={"user_id": "u-eva"},
                      follow_redirects=True)
text = re.sub(r"<[^>]+>", " ", odpoved.text)
text = re.sub(r"\s+", " ", text)
check("(20 přehrávání)" in text, "hláška říká, kolik odešlo")
check("jde vrátit zpět" in text,
      "a že to jde do noci vzít zpět")
check("Nejbližší úklid" in text,
      "okno Obnovit říká, do kdy to jde vzít zpět")
check(PREPISU["pocet"] == pred_klikem, "kliknutí soubor nepřepsalo")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
