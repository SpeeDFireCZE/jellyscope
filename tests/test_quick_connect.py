# -*- coding: utf-8 -*-
r"""Přihlášení kódem (Quick Connect): heslo tudy neprojde vůbec.

Přihlášení jellyfinovým heslem znamená, že cizí heslo prochází naší
aplikací. Quick Connect to obrací: Jellyscope si řekne o kód, člověk ho
potvrdí ve svém **už přihlášeném Jellyfinu**, a teprve pak dostaneme
identitu.

Celé to stojí na jednom rozdílu, který test hlídá: do prohlížeče jde
**kód**, na serveru zůstává **tajemství**. Kód sám o sobě nikoho nikam
nepustí - bez potvrzení v Jellyfinu je to šest číslic. Tajemství pustí,
a proto se nikdy nesmí objevit na stránce.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_quick_connect.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "quick.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic-dostatecne-dlouhy"
os.environ["JELLYFIN_URL"] = "http://jellyfin.test"
os.environ["JELLYFIN_API_KEY"] = "test-key"

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, db, jellyfin, pristup, web  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


TAJEMSTVI = "TajemstviQXZ0123456789"
KOD = "909919"

db.init_db()
db.set_setting("jellyfin_url", "http://jellyfin.test")
db.set_setting("jellyfin_api_key", "ApiKlicQXZ")
db.forget_settings()
# Bez jedineho uctu posila /login na prvotni nastaveni - prihlasovaci
# stranka by se vubec nevykreslila.
accounts.create("spravce", "dlouheheslo", is_admin=True)

# Stav predstiraneho serveru: co odpovida a co uz od nas dostal.
stav = {"zapnuto": True, "potvrzeno": False}
ZADOSTI: list[httpx.Request] = []
ZRUSENE: list[str] = []


def server(zadost: httpx.Request) -> httpx.Response:
    ZADOSTI.append(zadost)
    cesta = zadost.url.path
    if cesta == "/QuickConnect/Enabled":
        return httpx.Response(200, text="true" if stav["zapnuto"] else "false")
    if cesta == "/QuickConnect/Initiate":
        return httpx.Response(200, json={"Authenticated": False,
                                         "Secret": TAJEMSTVI, "Code": KOD})
    if cesta == "/QuickConnect/Connect":
        return httpx.Response(200, json={"Authenticated": stav["potvrzeno"]})
    if cesta == "/Users/AuthenticateWithQuickConnect":
        return httpx.Response(200, json={
            "AccessToken": "TokenQXZ",
            "User": {"Id": "u-mirek", "Name": "Mirek", "Policy": {}}})
    if cesta == "/Sessions/Logout":
        ZRUSENE.append(zadost.headers.get("Authorization", ""))
        return httpx.Response(204)
    return httpx.Response(200, json={"Items": [], "TotalRecordCount": 0})


async def podvrzeny_klient(self):
    self._client = httpx.AsyncClient(transport=httpx.MockTransport(server),
                                     base_url="http://jellyfin.test")
    return self


jellyfin.JellyfinClient.__aenter__ = podvrzeny_klient

print("--- dokud správce jellyfinový login nezapne, není tudy cesta ---")
klient = TestClient(web.app)
check("/login/quick" not in klient.get("/login").text,
      "tlačítko se na přihlašovací stránce neukazuje")
odpoved = klient.post("/login/quick")
check(odpoved.status_code == 400, f"a routa odmítá ({odpoved.status_code})")
check(not ZADOSTI, "Jellyfinu se přitom nikdo na nic neptal")

db.set_setting(pristup.LOGIN_KLIC, "1")
db.forget_settings()

print()
print("--- zapnuto: na stránce je kód, tajemství zůstalo na serveru ---")
stranka = klient.get("/login").text
check("/login/quick" in stranka, "tlačítko se objevilo")

odpoved = klient.post("/login/quick")
check(odpoved.status_code == 200, f"okno s kódem se otevřelo "
                                  f"({odpoved.status_code})")
check(KOD in odpoved.text, "a kód je v něm vidět")
check(TAJEMSTVI not in odpoved.text,
      "tajemství na stránce NENÍ - s ním by se přihlásil kdokoli")
check(TAJEMSTVI not in str(odpoved.cookies), "ani v cookie")

print()
print("--- čekání na potvrzení ---")
stav_odpoved = klient.get("/login/quick/stav")
check(stav_odpoved.json() == {"hotovo": False},
      f"nepotvrzeno = nic víc než „ještě ne\" ({stav_odpoved.json()})")
check(not db.query_one(
    "SELECT id FROM accounts WHERE jellyfin_user_id = 'u-mirek'"),
    "účet zatím nevznikl")
check(klient.get("/", follow_redirects=False).status_code == 303,
      "a dovnitř se zatím nikdo nedostal")

stav["potvrzeno"] = True
stav_odpoved = klient.get("/login/quick/stav")
check(stav_odpoved.json() == {"hotovo": True},
      f"po potvrzení se přihlásil ({stav_odpoved.json()})")

ucet = db.query_one(
    "SELECT * FROM accounts WHERE jellyfin_user_id = 'u-mirek'")
check(ucet is not None, "účet se založil z toho, co řekl Jellyfin")
check(not (dict(ucet or {}).get("password_hash") or ""),
      "a žádné heslo u něj není - přes Quick Connect žádné nepadlo")
check(not dict(ucet or {}).get("is_admin"),
      "kdo není správce v Jellyfinu, není správce ani tady")

print()
print("--- co odešlo a co po nás zůstalo ---")
check(not [z for z in ZADOSTI if z.url.path == "/Users/AuthenticateByName"],
      "server se nikdy nedozvěděl žádné heslo")
zahajeni = [z for z in ZADOSTI if z.url.path == "/QuickConnect/Initiate"]
check(len(zahajeni) == 1, f"kód se vyžádal jednou ({len(zahajeni)})")
check("Authorization" in zahajeni[0].headers,
      "náš klíč jde v hlavičce, ne v adrese")
check("ApiKlicQXZ" not in str(zahajeni[0].url), "v adrese opravdu není")
check(all(z.url.host == "jellyfin.test" for z in ZADOSTI),
      "a všechno jen na server z nastavení")
check(ZRUSENE and "TokenQXZ" in ZRUSENE[0],
      f"token z přihlášení se hned zrušil ({ZRUSENE})")

# `DeviceId` musi byt pri zahajeni i dokonceni stejny - Jellyfin podle
# nej paruje zadost s potvrzenim. Kdyby se lisil, kod by slo potvrdit
# a prihlaseni by presto nikdy nedobehlo.
dokonceni = [z for z in ZADOSTI
             if z.url.path == "/Users/AuthenticateWithQuickConnect"]
check(len(dokonceni) == 1, "a dokončilo se jednou")
check(zahajeni[0].headers.get("Authorization")
      == dokonceni[0].headers.get("Authorization"),
      "zahájení i dokončení se hlásí stejným zařízením")

print()
print("--- divák vidí svoje, do nastavení nesmí ---")
check(klient.get("/settings", follow_redirects=False).status_code == 403,
      "Nastavení je pro správce")
check(klient.get("/", follow_redirects=False).status_code == 303,
      "a rozcestník ho posílá na jeho vlastní stránku")

print()
print("--- když Quick Connect nemá zapnutý server ---")
stav["zapnuto"] = False
novy = TestClient(web.app)
odpoved = novy.post("/login/quick")
check(odpoved.status_code == 400, f"neotevře se okno s kódem "
                                  f"({odpoved.status_code})")
check("Quick Connect" in odpoved.text,
      "a stránka řekne, že se zapíná v Jellyfinu")
stav["zapnuto"] = True

print()
print("--- vypršelý nebo cizí kód nikoho nepustí ---")
cizi = TestClient(web.app)
check(cizi.get("/login/quick/stav").json() == {"hotovo": False, "konec": True},
      "bez zahájení není co dokončovat")

vypnuty = TestClient(web.app)
vypnuty.post("/login/quick")
db.set_setting(pristup.LOGIN_KLIC, "0")
db.forget_settings()
check(vypnuty.get("/login/quick/stav").json()
      == {"hotovo": False, "konec": True},
      "a rozdělaný kód skončí ve chvíli, kdy správce login vypne")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
