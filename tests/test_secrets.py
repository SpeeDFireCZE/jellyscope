# -*- coding: utf-8 -*-
"""API klíč k Jellyfinu se nesmí dostat do prohlížeče.

Kdo má ten klíč, může s cizím Jellyfinem dělat všechno, co jeho vlastník.
Jellyscope si ho proto drží jen na serveru: obrázky se stahují přes něj,
šablony ho nikdy nevypisují a do stránky se nesmí dostat ani nedopatřením.

Test to nekontroluje čtením kódu, ale **projde přihlášeně celou aplikaci
a hledá ten klíč ve výstupu** — v HTML, v hlavičkách i v cookies.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
# Vlastní domeček: bez něj by DATABASE_PATH přebil uložený výběr
# databáze v data/database.json a test by běžel proti ostré databázi.
os.environ["JELLYSCOPE_HOME"] = str(_tmp)
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "secrets.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic"

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, db  # noqa: E402
from jellyscope.web import app  # noqa: E402

failures = 0

# Klíč schválně nezvyklý, ať se nedá splést s ničím jiným na stránce.
KLIC = "JELLYFIN-KLIC-NESMI-UNIKNOUT-9f3a7c2e"
ADRESA = "http://jellyfin.doma.local:8096"


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


db.init_db()
accounts.create("spravce", "dlouheheslo", is_admin=True)
db.set_setting("jellyfin_url", ADRESA)
db.set_setting("jellyfin_api_key", KLIC)

# Aspoň jedna položka, aby se na Přehledu vůbec nějaký plakát vykreslil -
# jinak by kontrola "obrázky jdou přes nás" testovala prázdnou stránku.
with db.connect() as conn:
    conn.execute("INSERT INTO libraries (id, name) VALUES ('lib', 'Filmy')")
    conn.execute(
        """INSERT INTO items (id, name, type, library_id, date_created,
                              is_missing, synced_at)
           VALUES ('film-1', 'Zkušební film', 'Movie', 'lib', ?, 0, ?)""",
        (db.utcnow(), db.utcnow()),
    )

client = TestClient(app)
client.post("/login", data={"username": "spravce", "password": "dlouheheslo"})


print("--- klíč v uloženém nastavení opravdu je ---")
check(db.get_setting("jellyfin_api_key") == KLIC, "klíč je v databázi")
check(KLIC not in db.get_public_settings().values(),
      "get_public_settings() ho neobsahuje")
check(KLIC in db.get_settings().values(),
      "get_settings() ho obsahuje (server ho potřebuje)")


print()
print("--- žádná stránka ho nevypíše ---")
STRANKY = [
    "/", "/?kind=movies", "/partials/daily?kind=both",
    "/insights", "/languages", "/library", "/users", "/history",
    "/settings", "/settings?section=jellyfin", "/settings?section=data",
    "/settings?section=tasks", "/settings?section=import",
    "/settings?section=database", "/settings?section=accounts",
    "/settings?section=general",
]
for cesta in STRANKY:
    response = client.get(cesta)
    v_tele = KLIC in response.text
    v_hlavickach = any(KLIC in str(h) for h in response.headers.values())
    check(not v_tele and not v_hlavickach,
          f"{response.status_code}  {cesta}")

print()
check(all(KLIC not in (c or "") for c in client.cookies.values()),
      "klíč není ani v cookies")


print()
print("--- formulář ukazuje jen tečky, ne hodnotu ---")
stranka = client.get("/settings?section=jellyfin").text
check('type="password"' in stranka, "pole pro klíč je typu password")
check('value="' + KLIC not in stranka, "pole nemá klíč ve value")
check("••••" in stranka, "místo klíče jsou jen tečky")
check(ADRESA in stranka, "adresa serveru se ukazuje (není tajná, musí jít měnit)")


print()
print("--- obrázky jdou přes nás, ne přímo z Jellyfinu ---")
# Kdyby stránka odkazovala rovnou na Jellyfin, prohlížeč by musel znát
# jeho adresu i klíč. Proto se obrázky stahují přes /image/.
prehled = client.get("/").text
check("/image/" in prehled, "plakáty se načítají přes /image/")
check(ADRESA not in prehled, "adresa Jellyfinu se do přehledu nedostane")
check("api_key=" not in prehled and "ApiKey" not in prehled,
      "v žádném odkazu není klíč jako parametr")


print()
print("--- klíč se nedá vytáhnout ani jako nepřihlášený ---")
host = TestClient(app)   # bez přihlášení
for cesta in ("/", "/settings", "/settings?section=jellyfin"):
    odpoved = host.get(cesta, follow_redirects=False)
    check(odpoved.status_code in (302, 303, 307) and KLIC not in odpoved.text,
          f"nepřihlášený dostane přesměrování na /login ({cesta})")


print()
print("--- čtenář (ne správce) se k nastavení Jellyfinu nedostane ---")
accounts.create("ctenar", "dlouheheslo2", is_admin=False)
ctenar = TestClient(app)
ctenar.post("/login", data={"username": "ctenar", "password": "dlouheheslo2"})
odpoved = ctenar.get("/settings?section=jellyfin")
check(KLIC not in odpoved.text, "čtenář klíč nevidí")
odpoved = ctenar.post("/settings/connection",
                      data={"jellyfin_url": "http://x", "action": "test"},
                      follow_redirects=False)
check(odpoved.status_code == 403, "čtenář nemůže ani zkoušet spojení")


print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
