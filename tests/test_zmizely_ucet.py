# -*- coding: utf-8 -*-
r"""Účet z Jellyfinu zmizí, když zmizí člověk v Jellyfinu.

Účet diváka vzniká sám při prvním přihlášení jellyfinovým heslem. Když
správce toho člověka v Jellyfinu odebere, zůstával v Nastavení → Účty
řádek, který nikam nevede - a kdyby se ten člověk vrátil pod stejným id,
zdědil by ho i s právy. Teď ho synchronizace odebere.

Odebírá se **jen účet**, tedy jen přihlášení. Řádek v `users`, historie
a statistiky zůstávají: kdo co sledoval, se odebráním účtu nemění.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_zmizely_ucet.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "ucty.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic-dostatecne-dlouhy"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, db, scanner, web  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


class FalesnyKlient:
    """Jen `users()` - víc synchronizace uživatelů nepotřebuje."""

    def __init__(self, uzivatele):
        self.uzivatele = uzivatele

    async def users(self):
        return self.uzivatele


def sync(uzivatele) -> int:
    return asyncio.run(scanner._sync_users(FalesnyKlient(uzivatele)))


def ucet(jmeno: str):
    return db.query_one("SELECT * FROM accounts WHERE username = ?", (jmeno,))


db.init_db()
accounts.create("spravce", "dlouheheslo", is_admin=True)
accounts.create("ctenar", "dlouheheslo", is_admin=False)

MIREK = {"Id": "aaaaaaaabbbbccccddddeeeeeeeeeeee", "Name": "Mirek",
         "Policy": {"IsAdministrator": False}}
JANA = {"Id": "11111111222233334444555555555555", "Name": "Jana",
        "Policy": {"IsAdministrator": False}}

# Dva divaci se prihlasili - kazdy ma ucet.
accounts.z_jellyfinu({"id": MIREK["Id"], "jmeno": "Mirek", "spravce": False})
accounts.z_jellyfinu({"id": JANA["Id"], "jmeno": "Jana", "spravce": False})
with db.connect() as conn:
    conn.execute("INSERT INTO items (id, name, type, is_missing)"
                 " VALUES ('film-1','Film','Movie',0)")
    conn.execute(
        "INSERT INTO playback (session_key, user_id, user_name, item_id,"
        " item_name, item_type, started_at, last_seen_at, watched_seconds,"
        " is_active) VALUES ('k1', ?, 'Mirek', 'film-1', 'Film', 'Movie',"
        " '2026-09-01 20:00:00', '2026-09-01 21:30:00', 5400, 0)",
        (MIREK["Id"],))
    conn.commit()

print("--- dokud je v Jellyfinu, účet zůstává ---")
sync([MIREK, JANA])
check(ucet("Mirek") is not None and ucet("Jana") is not None,
      "oba účty tu jsou")
check(ucet("spravce") is not None and ucet("ctenar") is not None,
      "místní účty se toho netýkají")

print()
print("--- Mirek se přihlásí, pak ho správce v Jellyfinu odebere ---")
sync([JANA])
check(ucet("Mirek") is None, "Mirkův účet je pryč")
check(ucet("Jana") is not None, "Janin zůstal")
check(ucet("ctenar") is not None and ucet("spravce") is not None,
      "místní účty zůstaly")
check(db.query_one("SELECT name FROM users WHERE id = ?", (MIREK["Id"],))
      is not None, "řádek diváka v `users` zůstal - historie má komu patřit")
kolik = db.query_one("SELECT COUNT(*) AS n FROM playback WHERE user_id = ?",
                     (MIREK["Id"],))
check(int(dict(kolik or {}).get("n") or 0) == 1,
      "a jeho historie taky - statistiky se nemění")

print()
print("--- pojistky ---")
sync([])
check(ucet("Jana") is not None,
      "prázdná odpověď z Jellyfinu nesmaže nikoho (to je chyba spojení, ne prázdný server)")

# Totez id, jednou s pomlckami - to neni jiny clovek.
s_pomlckami = "11111111-2222-3333-4444-555555555555"
sync([{"Id": s_pomlckami, "Name": "Jana", "Policy": {}}])
check(ucet("Jana") is not None, "id s pomlčkami je týž člověk - účet zůstal")

# Posledni spravce zustava, i kdyz je z Jellyfinu.
with db.connect() as conn:
    conn.execute("UPDATE accounts SET is_admin = 0 WHERE username = 'spravce'")
    conn.execute("UPDATE accounts SET is_admin = 1 WHERE username = 'Jana'")
    conn.commit()
sync([MIREK])
check(ucet("Jana") is not None, "poslední správce zůstává, i když ho Jellyfin nezná")
with db.connect() as conn:
    conn.execute("UPDATE accounts SET is_admin = 1 WHERE username = 'spravce'")
    conn.commit()
sync([MIREK])
check(ucet("Jana") is None, "jakmile správce není poslední, odebere se i on")

print()
print("--- přihlášený divák po smazání účtu vypadne ---")
import httpx  # noqa: E402

from jellyscope import jellyfin, pristup  # noqa: E402

db.set_setting(pristup.LOGIN_KLIC, "1")
db.forget_settings()
klient = TestClient(web.app)


def server(zadost: httpx.Request) -> httpx.Response:
    if zadost.url.path == "/Users/AuthenticateByName":
        return httpx.Response(200, json={
            "AccessToken": "t", "User": {"Id": MIREK["Id"], "Name": "Mirek",
                                         "Policy": {}}})
    return httpx.Response(204)


async def podvrzeny(self):
    self._client = httpx.AsyncClient(transport=httpx.MockTransport(server),
                                     base_url="http://jellyfin.test")
    return self


jellyfin.JellyfinClient.__aenter__ = podvrzeny
odpoved = klient.post("/login", data={"username": "Mirek", "password": "x",
                                      "zpusob": "jellyfin"},
                      follow_redirects=False)
check(odpoved.status_code == 303, f"Mirek se přihlásil ({odpoved.status_code})")
check(klient.get("/history", follow_redirects=False).status_code == 200,
      "a vidí svou historii")
sync([JANA])
odpoved = klient.get("/history", follow_redirects=False)
check(odpoved.status_code == 303 and "/login" in odpoved.headers.get("location", ""),
      f"po odebrání v Jellyfinu ho další klik pošle na přihlášení ({odpoved.status_code})")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
