# -*- coding: utf-8 -*-
r"""Přihlašovací údaje k Jellyfinu nesmí nikam propadnout.

Přihlašování jellyfinovým heslem znamená, že aplikací prochází **cizí
heslo** a že u sebe drží API klíč k celému serveru. Tenhle test hlídá
cesty, kterými by mohly uniknout - protože uniknout můžou tiše a projeví
se to až tehdy, když je pozdě.

Co se ověřuje:

* heslo z přihlašovacího okna se nikam neukládá - ani do účtu, ani do
  nastavení, ani do logu,
* API klíč a heslo jdou **v hlavičce**, nikdy v adrese (adresy končí
  v logu proxy i v historii prohlížeče),
* token, který ověření hesla v Jellyfinu vyrobí, se hned ruší,
* API klíč se nikdy nevypíše na stránku ani do odpovědi API,
* v logu aplikace není klíč ani heslo,
* divák ani čtenář se ke klíči nedostanou (Nastavení je pro správce).

Spuštění:
    .\.venv\Scripts\python.exe tests\test_jellyfin_udaje.py
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "udaje.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic-dostatecne-dlouhy"
os.environ["JELLYFIN_URL"] = "http://jellyfin.test"
os.environ["JELLYFIN_API_KEY"] = "test-key"

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, applog, db, jellyfin, pristup, web  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


# Schvalne neobvykle retezce - v souboru ani na strance se nemuzou
# objevit nahodou.
KLIC = "ApiKlicQXZ0123456789"
HESLO = "TajneHesloQXZ!42"

db.init_db()
db.set_setting("jellyfin_url", "http://jellyfin.test")
db.set_setting("jellyfin_api_key", KLIC)
db.set_setting(pristup.LOGIN_KLIC, "1")
db.forget_settings()
accounts.create("spravce", "dlouheheslo", is_admin=True)

ZADOSTI: list[httpx.Request] = []
ZRUSENE: list[str] = []


def server(zadost: httpx.Request) -> httpx.Response:
    ZADOSTI.append(zadost)
    if zadost.url.path == "/Users/AuthenticateByName":
        telo = json.loads(zadost.content or b"{}")
        if telo.get("Username") == "mirek" and telo.get("Pw") == HESLO:
            return httpx.Response(200, json={
                "AccessToken": "TokenQXZ",
                "User": {"Id": "u-mirek", "Name": "Mirek", "Policy": {}}})
        return httpx.Response(401, json={})
    if zadost.url.path == "/Sessions/Logout":
        ZRUSENE.append(zadost.headers.get("Authorization", ""))
        return httpx.Response(204)
    return httpx.Response(200, json={"Items": [], "TotalRecordCount": 0})


async def podvrzeny_klient(self):
    self._client = httpx.AsyncClient(transport=httpx.MockTransport(server),
                                     base_url="http://jellyfin.test")
    return self


jellyfin.JellyfinClient.__aenter__ = podvrzeny_klient

klient = TestClient(web.app)
odpoved = klient.post("/login", data={"username": "mirek", "password": HESLO,
                                      "zpusob": "jellyfin"},
                      follow_redirects=False)

print("--- cizí heslo projde a nikde nezůstane ---")
check(odpoved.status_code == 303, f"divák se přihlásil ({odpoved.status_code})")

ucet = dict(db.query_one(
    "SELECT * FROM accounts WHERE jellyfin_user_id = 'u-mirek'") or {})
check(HESLO not in json.dumps(ucet, default=str),
      "heslo není v účtu (ani jako otisk něčeho)")
check(not ucet.get("password_hash"), "a pole pro heslo zůstalo prázdné")

nastaveni = db.query_all("SELECT key, value FROM settings")
check(not [r for r in nastaveni if HESLO in str(dict(r)["value"])],
      "heslo není v nastavení")

soubor = Path(str(db.database_config().path))
if not soubor.is_absolute():
    soubor = PROJECT / soubor
obsah = soubor.read_bytes() if soubor.exists() else b""
check(HESLO.encode() not in obsah, "heslo není ani v souboru databáze")

print()
print("--- co odešlo do Jellyfinu ---")
prihlasovaci = [z for z in ZADOSTI
                if z.url.path == "/Users/AuthenticateByName"]
check(len(prihlasovaci) == 1, f"jeden dotaz na ověření ({len(prihlasovaci)})")
zadost = prihlasovaci[0]
check(HESLO not in str(zadost.url),
      "heslo není v adrese - adresy se logují v proxy i v prohlížeči")
check(KLIC not in str(zadost.url), "a API klíč taky ne")
check("Authorization" in zadost.headers, "údaje jdou v hlavičce")
check(zadost.url.host == "jellyfin.test",
      f"a nikam jinam než na server z nastavení ({zadost.url.host})")

check(ZRUSENE and "TokenQXZ" in ZRUSENE[0],
      f"token z ověření se hned zrušil ({ZRUSENE})")
check(db.get_setting("jellyfin_api_key", "") == KLIC,
      "náš API klíč zůstal beze změny")

print()
print("--- klíč se nikdy neukáže na stránce ---")
spravce = TestClient(web.app)
spravce.post("/login", data={"username": "spravce", "password": "dlouheheslo",
                            "zpusob": "mistni"})
for cesta in ("/settings?section=jellyfin", "/settings?section=data",
              "/health", "/"):
    text = spravce.get(cesta).text
    check(KLIC not in text, f"{cesta} klíč nevypisuje")

print()
print("--- v logu není klíč ani heslo ---")
log = logging.getLogger("jellyscope.zkouska")
log.info("nastaveni jellyfin_api_key zmeneno")
radky = "\n".join(str(r) for r in applog.posledni_radky(200)) \
    if hasattr(applog, "posledni_radky") else ""
soubor_logu = Path(_tmp) / "data" / "logs"
if soubor_logu.exists():
    for cesta in soubor_logu.glob("*.log"):
        radky += cesta.read_text(encoding="utf-8", errors="replace")
check(KLIC not in radky, "klíč v logu není")
check(HESLO not in radky, "heslo v logu není")

print()
print("--- nešifrované spojení je vidět v Nastavení ---")
# Zasifrovat heslo za Jellyfin nejde: sifrovani dela https, ne aplikace
# nad nim. Co se udelat da, je rict to nahlas - a jen tam, kde to plati.
from jellyscope import jellyfin as _jf  # noqa: E402

check(_jf.po_siti_nesifrovane("http://192.168.1.10:8096"),
      "http na jiný stroj = varovat")
check(not _jf.po_siti_nesifrovane("https://jellyfin.doma.cz"),
      "https je v pořádku")
check(not _jf.po_siti_nesifrovane("http://localhost:8096"),
      "localhost nic po síti neposílá")
check(not _jf.po_siti_nesifrovane(""), "prázdná adresa nevaruje")

db.set_setting("jellyfin_url", "http://192.168.1.10:8096")
db.forget_settings()
stranka = spravce.get("/settings?section=jellyfin").text
check("není šifrované" in stranka or "not encrypted" in stranka,
      "a správce to v kartě vidí")
db.set_setting("jellyfin_url", "https://jellyfin.doma.cz")
db.forget_settings()
check("není šifrované" not in spravce.get("/settings?section=jellyfin").text,
      "na https se nevaruje")
db.set_setting("jellyfin_url", "http://jellyfin.test")
db.forget_settings()

print()
print("--- ke klíči se dostane jen správce ---")
divak = TestClient(web.app)
divak.post("/login", data={"username": "mirek", "password": HESLO,
                           "zpusob": "jellyfin"})
for cesta in ("/settings?section=jellyfin", "/settings"):
    odpoved = divak.get(cesta, follow_redirects=False)
    check(odpoved.status_code == 403,
          f"divák do {cesta} nesmí ({odpoved.status_code})")
    check(KLIC not in odpoved.text, "a klíč v odpovědi není")

accounts.create("ctenar", "dlouheheslo", is_admin=False)
ctenar = TestClient(web.app)
ctenar.post("/login", data={"username": "ctenar", "password": "dlouheheslo",
                            "zpusob": "mistni"})
odpoved = ctenar.get("/settings?section=jellyfin", follow_redirects=False)
check(odpoved.status_code == 403,
      f"čtenář do Nastavení nesmí ({odpoved.status_code})")
check(KLIC not in odpoved.text, "a klíč v odpovědi není")

print()
print("--- vypnuté přihlašování se Jellyfinu ani neptá ---")
db.set_setting(pristup.LOGIN_KLIC, "0")
db.forget_settings()
pocet = len(ZADOSTI)
TestClient(web.app).post("/login", data={"username": "mirek",
                                         "password": HESLO,
                                         "zpusob": "jellyfin"})
check(len(ZADOSTI) == pocet,
      "žádný dotaz nikam neodešel - ani heslo, ani klíč")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
