# -*- coding: utf-8 -*-
r"""Místa, která CodeQL označil jako díru - a co se na nich doopravdy děje.

Statická analýza hlásí, že do cesty k souboru a do adresy přesměrování
teče hodnota od uživatele. Je to pravda a je to i v pořádku: obojí projde
kontrolou, kterou CodeQL nevidí, protože vede přes jinou funkci.

Nález se tím ale nestává neškodným navždy. Kdyby ta kontrola kdykoliv
odešla, dostane se ze statické analýzy pravda. Tenhle test proto neověřuje
kód očima, ale **útokem**: zkusí přesně to, čeho se hlášení bojí.

Co se hlídá a proč to CodeQL nepozná:

* `py/path-injection` u stahování zálohy - jméno souboru se ověřuje
  v `tasks.backup_file()`, tedy o funkci dál;
* `py/url-redirection` u spuštění analýzy - id knihovny se použije jen
  tehdy, když taková knihovna v databázi existuje (`stats.library()`);
* `py/clear-text-logging` u změny nastavení - hodnota se zapíše jen
  u nastavení, o kterém víme, že je neškodné.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_nalezy_codeql.py
"""
from __future__ import annotations

import io
import logging
import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "nalezy.db")
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
with db.connect() as conn:
    conn.execute("INSERT INTO libraries (id,name,collection_type,paths,"
                 "synced_at) VALUES ('lib-1','Filmy','movies','[]',?)",
                 (db.utcnow(),))
    conn.commit()

klient = TestClient(web.app, follow_redirects=False)
klient.post("/login", data={"username": "spravce",
                            "password": "dlouheheslo"})

print("--- stažení zálohy pustí jen zálohu ---")
zalohy = Path(_tmp) / "zalohy"
zalohy.mkdir(exist_ok=True)
(zalohy / "jellyscope-2026-09-09.db").write_text("poctiva zaloha",
                                                 encoding="utf-8")
# O patro vys neco, co se ven dostat nesmi. Dva soubory schvalne:
#
#   `tajne.env`          - beznou cestou; odmitne ho uz podminka na tvar
#                          jmena, takze o kontrole lomitek nic nerekne.
#   `jellyscope-tajne.db`- jmenuje se **presne tak, jak se zaloha jmenovat
#                          ma**. Tenhle projde vsim ostatnim a zastavi ho
#                          jedine kontrola, ze jmeno neni cesta. Bez nej by
#                          test tu kontrolu vubec nezkousel - overeno tak,
#                          ze se vypnula a test si toho nevsiml.
(Path(_tmp) / "tajne.env").write_text("SECRET_KEY=tohle-se-ven-nesmi",
                                      encoding="utf-8")
(Path(_tmp) / "jellyscope-tajne.db").write_text(
    "tohle-se-ven-nesmi-dostat-taky", encoding="utf-8")
db.set_setting("backup_path", str(zalohy))
db.forget_settings()

odpoved = klient.get("/settings/backup/download",
                     params={"name": "jellyscope-2026-09-09.db"})
check(odpoved.status_code == 200 and "poctiva" in odpoved.text,
      "poctivá záloha se stáhne")

MIMO_SLOZKU = [
    # Tyhle zastavi uz podminka na tvar jmena.
    "../tajne.env", "..\\tajne.env", "../../tajne.env",
    "jellyscope-../tajne.env", "/etc/passwd", "C:\\Windows\\win.ini",
    "tajne.env", "....//tajne.env", str(Path(_tmp) / "tajne.env"),
    # A tyhle uz projdou vsim krome kontroly, ze jmeno neni cesta.
    # Musi **zacinat** na "jellyscope-" a koncit na ".db", jinak je
    # odmitne uz podminka na tvar jmena a o kontrole lomitek nic nerekne -
    # presne na tom se driv tenhle test naslepo choval, ze prochazel.
    "jellyscope-x/../../jellyscope-tajne.db",
    "jellyscope-x\\..\\..\\jellyscope-tajne.db",
    "jellyscope-2026-09-09.db/../../jellyscope-tajne.db",
    "../jellyscope-tajne.db",
    str(Path(_tmp) / "jellyscope-tajne.db"),
]
prosly = [u for u in MIMO_SLOZKU
          if "nesmi" in klient.get("/settings/backup/download",
                                   params={"name": u}).text]
check(not prosly, f"ven ze složky se nedostane nic ({prosly[:2]})")

print()
print("--- spuštění analýzy vrátí člověka jen k nám ---")
# `library_id` z formulare se do adresy dostane, jen kdyz takova knihovna
# opravdu existuje.
#
# Pozor na to, co tenhle oddil doopravdy dokazuje: bezpecno tu nedela ta
# kontrola existence, ale **pevna predpona** `/library/`. I kdyby se
# kontrola vypnula, vyjde z toho porad cesta na nasem serveru - overeno
# tak, ze se vypnula a nic se nestalo. Kontrola existence je tu proto, aby
# odkaz nekam vedl, ne kvuli bezpecnosti.
#
# Ten oddil tedy hlida vysledek: at se do `library_id` posle cokoliv,
# clovek skonci u nas. To je to, na cem zalezi.
ODVEDENI = ["//utocnik.cz", "https://utocnik.cz", "/\\utocnik.cz",
            "\\\\utocnik.cz", "javascript:alert(1)",
            "lib-1/../../https://utocnik.cz", "neexistujici"]
odvedlo = []
for utok in ODVEDENI:
    kam = klient.post("/settings/scan",
                      data={"mode": "missing", "library_id": utok}
                      ).headers.get("location", "")
    if kam.startswith(("http://", "https://", "//", "\\\\", "/\\")):
        odvedlo.append((utok, kam))
check(not odvedlo, f"cizí adresa neprojde ({odvedlo[:2]})")

kam = klient.post("/settings/scan",
                  data={"mode": "missing", "library_id": "lib-1"}
                  ).headers.get("location", "")
check(kam.startswith("/library/lib-1"),
      f"a k existující knihovně se člověk vrátí ({kam})")

print()
print("--- hodnota nastavení jde do logu jen tam, kde je neškodná ---")
zachyt = io.StringIO()
posluchac = logging.StreamHandler(zachyt)
posluchac.setFormatter(logging.Formatter("%(message)s"))
logger = logging.getLogger("jellyscope.db")
logger.addHandler(posluchac)
uroven = logger.level
logger.setLevel(logging.INFO)
try:
    db.set_setting("jellyfin_api_key", "TAJNY-KLIC-Z-TESTU")
    db.set_setting("notify_smtp_heslo", "tajneheslo-z-testu")
    db.set_setting("notify_smtp_komu", "petr@example.org")
    db.set_setting("poll_interval", "42")
finally:
    logger.removeHandler(posluchac)
    logger.setLevel(uroven)

zapsano = zachyt.getvalue()
for tajemstvi in ("TAJNY-KLIC-Z-TESTU", "tajneheslo-z-testu",
                  "petr@example.org"):
    check(tajemstvi not in zapsano, f"{tajemstvi[:22]!r} v logu není")
check("jellyfin_api_key" in zapsano, "ale že se změnilo, v logu je")
check("poll_interval: 10 -> 42" in zapsano,
      "a neškodné nastavení se vypíše i s hodnotou")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
