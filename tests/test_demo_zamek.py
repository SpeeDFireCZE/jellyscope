# -*- coding: utf-8 -*-
r"""Ukázkový režim: všechno je vidět, nic se nezmění.

Ukázka běží na veřejné adrese a přihlašovací údaje jsou rovnou
v přihlašovacím okně - dovnitř se dostane kdokoliv. Bez pojistky by první
návštěvník přepsal adresu Jellyfinu, spustil import nebo restart a pro
všechny ostatní by ukázka skončila.

Hlídá se to jednou middlewarou, ne v každé routě zvlášť: routa, na kterou
by se zapomnělo, je přesně ta, kterou někdo najde.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_demo_zamek.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "demo.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYSCOPE_DEMO"] = "1"

from jellyscope import accounts, config, db  # noqa: E402

config.load_config(reload=True)

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


db.init_db()
accounts.create("demo", "demodemo", is_admin=True)

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import web  # noqa: E402

client = TestClient(web.app)
client.post("/login", data={"username": "demo", "password": "demodemo"},
            follow_redirects=False)

print("--- co se v ukázce nesmí spustit ---")
# Za každou cestou je to, co by v ostrém provozu udělala.
NEBEZPECNE = {
    "/settings/save": {"jellyfin_url": "http://cizi:8096"},
    "/settings/connection": {"url": "http://cizi:8096", "api_key": "x"},
    "/settings/database": {"kind": "sqlite"},
    "/settings/sync": {},
    "/settings/scan": {},
    "/settings/restart": {},
    "/settings/update": {},
    "/settings/tasks": {},
    "/settings/tasks/run": {"key": "sync"},
    "/settings/import/playback-reporting": {},
    "/settings/history/tidy": {},
    "/settings/accounts/create": {"username": "utocnik", "password": "dlouheheslo"},
    "/settings/backup/download": {},
}
# Samotné 303 nestačí: routa po vykonání práce taky přesměrovává.
# Ptáme se proto na hlášku, která se po přesměrování ukáže - ta vzniká
# jedině tehdy, když akce NEPROBĚHLA.
HLASKA = "ukázka"
for cesta, data in NEBEZPECNE.items():
    odpoved = client.post(cesta, data=data, follow_redirects=False)
    stranka = client.get("/").text if odpoved.status_code == 303 else ""
    check(odpoved.status_code == 303 and HLASKA in stranka,
          f"{cesta} → hláška místo akce ({odpoved.status_code})")

check(db.get_setting("jellyfin_url", "") != "http://cizi:8096",
      "adresa Jellyfinu zůstala nedotčená")
check(accounts.get_by_name("utocnik") is None, "a žádný účet nepřibyl")

print()
print("--- co v ukázce fungovat MÁ ---")
# Jinak by si nikdo nemohl ani přepnout jazyk nebo se odhlásit.
odpoved = client.post("/settings/language", data={"ui_language": "en"},
                      follow_redirects=False)
check(odpoved.status_code == 303 and db.get_setting("ui_language") == "en",
      "jazyk rozhraní jde přepnout")
odpoved = client.post("/settings/interface",
                      data={"ui_max_streams": "5", "ui_max_viewers": "5",
                            "ui_map_zoom": "wheel"}, follow_redirects=False)
check(db.get_setting("ui_max_streams") == "5", "a nastavení rozhraní taky")

print()
print("--- prohlížení funguje celé ---")
for cesta in ("/", "/insights", "/languages", "/network", "/library", "/users",
              "/history", "/settings?section=jellyfin"):
    check(client.get(cesta).status_code == 200, f"{cesta} se otevře")

print()
print("--- mimo ukázku se nic neblokuje ---")
os.environ.pop("JELLYSCOPE_DEMO")
config.load_config(reload=True)
odpoved = client.post("/settings/language", data={"ui_language": "cs"},
                      follow_redirects=False)
check(odpoved.status_code == 303, "běžný provoz zůstává, jak byl")
check(web._demo_blokuje.__doc__ is not None, "a funkce je popsaná")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
