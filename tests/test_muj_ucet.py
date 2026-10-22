# -*- coding: utf-8 -*-
r"""Stránka „Můj účet": vlastní jazyk a vlastní heslo.

Nastavení je jen pro správce, takže čtenář ani divák z Jellyfinu dřív
neměli kde si změnit heslo nebo jazyk. Stránka /account je o jednom účtu
a otevře ji každý přihlášený:

* **jazyk rozhraní** se uloží u účtu a přebije jazyk serveru. Kdo si nic
  nevybral, má jazyk serveru. Přihlašovací stránka (bez účtu) je vždycky
  v jazyce serveru,
* **heslo** si mění jen místní účet a jen se současným heslem. Divák
  z Jellyfinu má heslo v Jellyfinu - formulář nevidí a podvržený
  požadavek nic nezmění,
* účet se bere z relace, nikdy z formuláře: cizí heslo ani cizí jazyk
  odsud změnit nejde.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_muj_ucet.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "muj_ucet.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic-dostatecne-dlouhy"
os.environ["JELLYFIN_URL"] = "http://jellyfin.test"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_DEMO"] = "0"

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, db, jellyfin, pristup, scanner, web, web_zaklad  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


# Profilovy obrazek divaka v podvrzenem Jellyfinu. Chova se jako skutecny
# (overeno na 10.11.11, 12.0 a 12.1): otisk je v /Users jako PrimaryImageTag,
# obrazek vraci GET /UserImage?userId=&tag=, bez obrazku 404.
OBRAZEK = b"\x89PNG\r\n\x1a\nobrazek-mirka"
STAV_OBRAZKU = {"tag": "tag1", "typ": "image/png", "chyba": False}
DOTAZY_OBRAZKU: list[dict] = []


# Podvrzeny Jellyfin: zna jedineho divaka, aby slo prihlasit ucet z Jellyfinu.
def server(zadost: httpx.Request) -> httpx.Response:
    if zadost.method == "GET" and zadost.url.path == "/Users":
        return httpx.Response(200, json=[
            {"Id": "u-mirek", "Name": "mirek", "Policy": {"IsAdministrator": False},
             "PrimaryImageTag": STAV_OBRAZKU["tag"]},
            {"Id": "u-cizi", "Name": "cizi", "Policy": {"IsAdministrator": False}}])
    if zadost.method == "GET" and zadost.url.path == "/UserImage":
        DOTAZY_OBRAZKU.append(dict(zadost.url.params))
        if STAV_OBRAZKU["chyba"]:
            raise httpx.ConnectError("Jellyfin nebezi")
        if zadost.url.params.get("userId") == "u-mirek" and STAV_OBRAZKU["tag"]:
            hlavicky = {"content-type": STAV_OBRAZKU["typ"]} if STAV_OBRAZKU["typ"] else {}
            return httpx.Response(200, content=OBRAZEK, headers=hlavicky)
        return httpx.Response(404)
    # Obrazky polozek (plakaty) - id s "svg" vrati SVG se skriptem.
    if zadost.method == "GET" and zadost.url.path.startswith("/Items/"):
        if "svg" in zadost.url.path:
            return httpx.Response(200, content=b"<svg onload=alert(1)></svg>",
                                  headers={"content-type": "image/svg+xml"})
        return httpx.Response(200, content=b"\xff\xd8plakat", headers={"content-type": "image/jpeg"})
    telo = json.loads(zadost.content or b"{}")
    if (telo.get("Username"), telo.get("Pw")) == ("mirek", "tajne"):
        # Jako skutečný Jellyfin: otisk obrázku přijde i s přihlášením.
        return httpx.Response(200, json={"User": {
            "Id": "u-mirek", "Name": "mirek", "Policy": {"IsAdministrator": False},
            "PrimaryImageTag": STAV_OBRAZKU["tag"] or None}})
    return httpx.Response(401, json={})


async def podvrzeny_klient(self):
    self._client = httpx.AsyncClient(transport=httpx.MockTransport(server),
                                     base_url="http://jellyfin.test")
    return self


jellyfin.JellyfinClient.__aenter__ = podvrzeny_klient

db.init_db()
accounts.create("spravce", "dlouheheslo", is_admin=True)
accounts.create("ctenar", "ctenarheslo")
db.set_setting(pristup.LOGIN_KLIC, "1")
db.set_setting("ui_language", "cs")
db.forget_settings()


def prihlas(jmeno: str, heslo: str, zpusob: str = "mistni") -> TestClient:
    k = TestClient(web.app)
    k.post("/login", data={"username": jmeno, "password": heslo, "zpusob": zpusob},
           follow_redirects=False)
    return k


spravce = prihlas("spravce", "dlouheheslo")
ctenar = prihlas("ctenar", "ctenarheslo")
divak = prihlas("mirek", "tajne", "jellyfin")

print("--- stránku otevře každý přihlášený ---")
for nazev, k in (("správce", spravce), ("čtenář", ctenar), ("divák z Jellyfinu", divak)):
    odpoved = k.get("/account", follow_redirects=False)
    check(odpoved.status_code == 200, f"{nazev}: /account vrací 200 ({odpoved.status_code})")
check('href="/account"' in ctenar.get("/history").text, "jméno v patičce menu vede na Můj účet")

# V paticce menu je Muj ucet jako tlacitko pres celou sirku: jmeno a pod
# nim role. Drive byl jen maly odkaz a odznak role byl vetsi nez jmeno.
for nazev, k, jmeno, role in (("správce", spravce, "spravce", "správce"),
                              ("čtenář", ctenar, "ctenar", "čtenář"),
                              ("divák z Jellyfinu", divak, "mirek", "divák z Jellyfinu")):
    odkaz = re.search(r'(<a class="ucet-odkaz"[^>]*>)(.*?)</a>', k.get("/history").text, re.S)
    check(bool(odkaz) and f"<strong>{jmeno}</strong>" in odkaz.group(2)
          and role in odkaz.group(2) and "Můj účet" in odkaz.group(2),
          f"{nazev}: v patičce je tlačítko Můj účet se jménem a rolí")
    # Dlouhé jméno se zkrátí výpustkou - celé musí jít zjistit aspoň z title.
    check(bool(odkaz) and jmeno in odkaz.group(1), f"{nazev}: celé jméno je i v title")
check(bool(re.search(r'<a class="ucet-odkaz"[^>]*aria-current="page"', ctenar.get("/account").text)),
      "na stránce Můj účet je tlačítko označené jako aktuální")
check(not re.search(r'<a class="ucet-odkaz"[^>]*aria-current', ctenar.get("/history").text),
      "jinde ne")
anonym = TestClient(web.app).get("/account", follow_redirects=False)
check(anonym.status_code in (302, 303, 307) and "/login" in anonym.headers.get("location", ""),
      "nepřihlášeného pošle na přihlášení")

print()
print("--- jazyk účtu přebije jazyk serveru ---")
check("Odhlásit" in ctenar.get("/account").text, "bez volby je čtenář v jazyce serveru (česky)")
odpoved = ctenar.post("/account/language", data={"ui_language": "en"}, follow_redirects=False)
check(odpoved.status_code == 303, f"uložení jazyka přesměruje zpátky ({odpoved.status_code})")
stranka = ctenar.get("/account").text
check("Sign out" in stranka, "čtenář teď vidí angličtinu")
check("Language saved." in stranka, "hláška o uložení je už v novém jazyce")
check("Sign out" in ctenar.get("/history").text, "i na ostatních stránkách")
check("Odhlásit" in spravce.get("/account").text, "správce zůstal v jazyce serveru")
check("Přihlásit" in TestClient(web.app).get("/login").text,
      "přihlašovací stránka je v jazyce serveru")
check(db.get_setting("ui_language") == "cs", "jazyk serveru se nezměnil")

# follow_redirects=False: jinak hlasku "spotrebuje" uz odpoved na POST.
ctenar.post("/account/language", data={"ui_language": "xx-neexistuje"}, follow_redirects=False)
stranka = ctenar.get("/account").text
check("Sign out" in stranka, "neznámý jazyk se neuloží, zůstane poslední volba")
check("That language is not available." in stranka, "a řekne se proč (v jazyce účtu)")
ctenar.post("/account/language", data={"ui_language": ""})
check("Odhlásit" in ctenar.get("/account").text, "prázdná volba = zase podle serveru")

divak.post("/account/language", data={"ui_language": "es"})
check("Cerrar sesión" in divak.get("/account").text, "jazyk si nastaví i divák z Jellyfinu")

# Spravce s vlastnim jazykem v Nastaveni -> Obecne: vyber jazyka SERVERU
# musi ukazovat jazyk serveru, ne jeho vlastni. Jinak by ho jedinym
# ulozenim nastavil vsem.
spravce.post("/account/language", data={"ui_language": "en"})
obecne = spravce.get("/settings?section=general").text
vyber = obecne[obecne.index('id="ui_language"'):]
vyber = vyber[:vyber.index("</select>")]
check('value="cs" selected' in vyber and 'value="en" selected' not in vyber,
      "Nastavení → Obecné ukazuje jazyk serveru, ne vlastní jazyk správce")
spravce.post("/settings/language", data={"ui_language": "cs", "log_language": "cs",
                                         "app_timezone": ""}, follow_redirects=False)
stranka = spravce.get("/settings?section=general").text
check("Save language" in stranka and "Sign out" in stranka,
      "hláška po uložení jazyka serveru je v jazyce, který správce vidí")

# Upozorneni a ulohy na pozadi jsou v jazyce serveru, i kdyz je spusti
# (nebo si nahled otevre) spravce s vlastnim jazykem.
from jellyscope import i18n, notifikace  # noqa: E402

token = i18n.nastav_jazyk_uctu("en")
try:
    vychozi = notifikace.texty_pro_nastaveni()[0]["predmet_vychozi"]
finally:
    i18n.vrat_jazyk_uctu(token)
check(vychozi == notifikace.ZPRAVY[0].predmet,
      f"text upozornění je v jazyce serveru, ne účtu ({vychozi!r})")

import asyncio  # noqa: E402


async def _jazyk_v_uloze() -> str:
    return i18n.current_language()


async def _spust_z_pozadavku() -> str:
    token = i18n.nastav_jazyk_uctu("en")
    try:
        return await asyncio.create_task(i18n.bez_jazyka_uctu(_jazyk_v_uloze()))
    finally:
        i18n.vrat_jazyk_uctu(token)


check(asyncio.run(_spust_z_pozadavku()) == "cs",
      "úloha spuštěná z požadavku běží v jazyce serveru, ne v jazyce účtu")
zdroj = (PROJECT / "jellyscope" / "web_nastaveni.py").read_text(encoding="utf-8")
holé = [r for r in zdroj.splitlines()
        if "asyncio.create_task(" in r and "bez_jazyka_uctu" not in r and "_restart_soon" not in r]
check(not holé, f"každá úloha z Nastavení se spouští bez jazyka účtu ({holé})")
spravce.post("/account/language", data={"ui_language": ""})

print()
print("--- heslo ---")
check('name="current_password"' in ctenar.get("/account").text, "čtenář má formulář na heslo")
ctenar.post("/account/password", data={"current_password": "spatne-heslo",
                                       "password": "novehesloctenare",
                                       "password_again": "novehesloctenare"})
check(accounts.authenticate("ctenar", "ctenarheslo") is not None,
      "se špatným současným heslem se nic nezmění")
ctenar.post("/account/password", data={"current_password": "ctenarheslo",
                                       "password": "novehesloctenare",
                                       "password_again": "novehesloctenare"})
check(accounts.authenticate("ctenar", "novehesloctenare") is not None,
      "se správným současným heslem se heslo změní")

# Ucet se bere z relace: podvrzene id spravce ve formulari nic nezmeni.
id_spravce = accounts.get_by_name("spravce")["id"]
ctenar.post("/account/password", data={"account_id": str(id_spravce),
                                       "current_password": "novehesloctenare",
                                       "password": "unesenyspravce1",
                                       "password_again": "unesenyspravce1"})
check(accounts.authenticate("spravce", "dlouheheslo") is not None,
      "cizí heslo odsud změnit nejde (id ve formuláři se ignoruje)")
check(accounts.authenticate("ctenar", "unesenyspravce1") is not None,
      "změnilo se heslo toho, kdo je přihlášený - účet se bere z relace")
check(ctenar.get("/account", follow_redirects=False).status_code == 200,
      "a ten, kdo heslo změnil, zůstal přihlášený")

stranka_divaka = divak.get("/account").text
check('name="current_password"' not in stranka_divaka, "divák z Jellyfinu formulář na heslo nevidí")
pred = accounts.podle_jellyfin_id("u-mirek")["password_hash"]
odpoved = divak.post("/account/password", data={"current_password": "", "password": "nejakeheslo1",
                                                "password_again": "nejakeheslo1"},
                     follow_redirects=False)
check(accounts.podle_jellyfin_id("u-mirek")["password_hash"] == pred,
      f"a podvržený požadavek jeho účtu heslo nedá ({odpoved.status_code})")
check(odpoved.status_code == 303, "heslo odmítla až sama routa (divák se k ní dostal)")
for cesta in ("/account/neco-dalsiho", "/settings/sync"):
    stav = divak.post(cesta, data={}, follow_redirects=False).status_code
    check(stav == 403, f"divák dál nesmí jiné POSTy: {cesta} ({stav})")

print()
print("--- změna hesla odhlásí ostatní relace ---")
# Kdo zjisti, ze se mu nekdo dostal do uctu, zmeni heslo - a ukradena
# relace musi prestat platit, jinak zmena hesla nic neresi.
druhy = prihlas("ctenar", "unesenyspravce1")
check(druhy.get("/account", follow_redirects=False).status_code == 200, "druhá relace platí")
ctenar.post("/account/password", data={"current_password": "unesenyspravce1",
                                       "password": "uplnenoveheslo1",
                                       "password_again": "uplnenoveheslo1"})
check(ctenar.get("/account", follow_redirects=False).status_code == 200,
      "relace, ve které se heslo změnilo, platí dál")
stav = druhy.get("/account", follow_redirects=False)
check(stav.status_code in (302, 303, 307) and "/login" in stav.headers.get("location", ""),
      f"druhá relace po změně hesla skončila ({stav.status_code})")

print()
print("--- Nastavení → Účty: vlastní heslo jen přes Můj účet ---")
ucty = spravce.get("/settings?section=accounts").text
check("okno-vlastni-heslo" not in ucty and 'href="/account"' in ucty,
      "místo formuláře bez současného hesla vede tlačítko na Můj účet")
spravce.post("/settings/accounts/password", data={"account_id": str(id_spravce),
                                                  "password": "bezsoucasneho1",
                                                  "password_again": "bezsoucasneho1"})
check(accounts.authenticate("spravce", "dlouheheslo") is not None,
      "vlastní heslo už Nastavení nezmění (chtělo by to současné heslo)")

print()
print("--- hádání současného hesla brzdí stejně jako přihlášení ---")
for _ in range(12):
    ctenar.post("/account/password", data={"current_password": "hadam",
                                           "password": "hackerheslo1",
                                           "password_again": "hackerheslo1"})
check(accounts.blokace_zbyva("testclient") != 0, "po sérii špatných hesel je adresa zablokovaná")
ctenar.post("/account/password", data={"current_password": "uplnenoveheslo1",
                                       "password": "hackerheslo1",
                                       "password_again": "hackerheslo1"})
check(accounts.authenticate("ctenar", "uplnenoveheslo1") is not None,
      "a během blokace se heslo nezmění ani se správným současným")
accounts.odblokuj("testclient")

print()
print("--- profilový obrázek z Jellyfinu ---")
divak = prihlas("mirek", "tajne", "jellyfin")


def avatar(klient: TestClient) -> str:
    """Adresa obrázku v tlačítku Můj účet v patičce, nebo prázdno."""
    odkaz = re.search(r'<a class="ucet-odkaz"[^>]*>(.*?)</a>', klient.get("/history").text, re.S)
    obrazek = re.search(r'<img[^>]*src="([^"]+)"', odkaz.group(1)) if odkaz else None
    return obrazek.group(1) if obrazek else ""


check(avatar(divak) == "" and divak.get("/account/obrazek").status_code == 404,
      "před synchronizací Jellyscope o obrázku neví: písmeno, žádný obrázek")


async def synchronizuj_uzivatele() -> None:
    async with jellyfin.JellyfinClient("http://jellyfin.test", "test-key") as klient:
        await scanner._sync_users(klient)


asyncio.run(synchronizuj_uzivatele())
otisky = {r["id"]: r["image_tag"] for r in db.query_all("SELECT id, image_tag FROM users")}
check(otisky.get("u-mirek") == "tag1" and otisky.get("u-cizi") is None,
      "synchronizace uloží otisk obrázku (kdo ho nemá, má prázdno)")
check(avatar(divak) == "/account/obrazek?v=tag1", "divák má v patičce svůj obrázek")
check("/account/obrazek?v=tag1" in divak.get("/account").text, "i na stránce Můj účet")

odpoved = divak.get("/account/obrazek?v=tag1")
check(odpoved.status_code == 200 and odpoved.content == OBRAZEK
      and odpoved.headers.get("content-type", "").startswith("image/png"),
      "/account/obrazek vrátí obrázek z Jellyfinu")
# Prohlizec si ho necha, ale pokazde se zepta serveru (no-cache + ETag):
# server overi prihlaseni a vrati 304 bez dotazu do Jellyfinu. Na sdilenem
# pocitaci tak cizi fotku z mezipameti nikdo jiny nedostane.
cache = odpoved.headers.get("cache-control", "")
check("private" in cache and "no-cache" in cache and odpoved.headers.get("etag"),
      "prohlížeč si ho drží soukromě a před použitím se zeptá serveru (ETag)")
pocet = len(DOTAZY_OBRAZKU)
znovu = divak.get("/account/obrazek?v=tag1", headers={"If-None-Match": odpoved.headers.get("etag", "")})
check(znovu.status_code == 304 and len(DOTAZY_OBRAZKU) == pocet,
      "nezměněný obrázek: 304 a Jellyfin se neptá")
anonym_obrazek = TestClient(web.app).get("/account/obrazek?v=tag1",
                                         headers={"If-None-Match": odpoved.headers.get("etag", "")},
                                         follow_redirects=False)
check(anonym_obrazek.status_code in (302, 303, 307), "nepřihlášenému nic, ani 304")
check(DOTAZY_OBRAZKU and DOTAZY_OBRAZKU[-1] == {"userId": "u-mirek", "tag": "tag1"},
      "Jellyfinu se posílá id a otisk z databáze")
soubory = [p.name for p in Path(_tmp).rglob("*") if p.is_file() and "mirek" in p.name]
check(not soubory, f"na disk serveru se obrázek neukládá ({soubory})")

DOTAZY_OBRAZKU.clear()
divak.get("/account/obrazek?userId=u-cizi&user=u-cizi&id=u-cizi")
check(DOTAZY_OBRAZKU and DOTAZY_OBRAZKU[-1].get("userId") == "u-mirek",
      "id v adrese se ignoruje: každý dostane jen svůj obrázek")

DOTAZY_OBRAZKU.clear()
check(avatar(ctenar) == "" and ctenar.get("/account/obrazek").status_code == 404
      and not DOTAZY_OBRAZKU, "místní účet: písmeno, žádný dotaz do Jellyfinu")
check(spravce.get("/account/obrazek").status_code == 404, "ani místní správce obrázek nemá")

# Co Jellyfin vrati jinak nez jako obrazek, se dal neposle: SVG umi skript,
# a kdyby se otevrelo primo na nasi adrese, bezel by pod Jellyscope.
for typ, popis in (("image/svg+xml", "SVG"), ("text/html", "HTML"), ("", "bez typu")):
    STAV_OBRAZKU["typ"] = typ
    check(divak.get("/account/obrazek?v=tag1").status_code == 404, f"{popis}: 404, ne obsah")
STAV_OBRAZKU["typ"] = "image/avif"
check(divak.get("/account/obrazek?v=tag1").status_code == 200, "AVIF z telefonu projde")
STAV_OBRAZKU["typ"] = "image/png"

STAV_OBRAZKU["chyba"] = True
nedostupny = divak.get("/account/obrazek?v=tag1")
check(nedostupny.status_code == 404 and "max-age" in nedostupny.headers.get("cache-control", ""),
      "Jellyfin neběží: 404 (písmeno) a prohlížeč chvíli nezkouší znovu")
STAV_OBRAZKU["chyba"] = False

puvodni_strop = jellyfin.MAX_BAJTU_OBRAZKU
jellyfin.MAX_BAJTU_OBRAZKU = 10
check(divak.get("/account/obrazek?v=tag1").status_code == 404,
      "obrázek nad strop velikosti se neposílá (Jellyfin ho nezmenšuje)")
jellyfin.MAX_BAJTU_OBRAZKU = puvodni_strop

# Totez plati pro plakaty: SVG se pod nasi adresou neposle.
async def plakat(item_id: str):
    async with jellyfin.JellyfinClient("http://jellyfin.test", "test-key") as klient:
        return await klient.image_bytes(item_id)


check(asyncio.run(plakat("svg1")) is None and asyncio.run(plakat("abc1")) is not None,
      "obrázek položky jako SVG se nestáhne, JPEG ano")

# Jellyfin posila tytez id jednou s pomlckami, jednou bez.
with db.connect() as conn:
    conn.execute("INSERT INTO users (id, name, is_administrator, is_disabled, image_tag)"
                 " VALUES ('aa11bb22cc33', 'pomlcky', 0, 0, 'otisk9')")
    conn.commit()
check(web_zaklad.otisk_obrazku_uctu({"jellyfin_user_id": "AA11BB22-CC33"}) == "otisk9",
      "otisk se najde, i když se id liší pomlčkami a velikostí písmen")

STAV_OBRAZKU["tag"] = "tag2"
asyncio.run(synchronizuj_uzivatele())
check(avatar(divak) == "/account/obrazek?v=tag2",
      "nový obrázek v Jellyfinu = nová adresa, prohlížeč si stáhne nový")

STAV_OBRAZKU["tag"] = ""
asyncio.run(synchronizuj_uzivatele())
check(avatar(divak) == "" and divak.get("/account/obrazek").status_code == 404,
      "obrázek v Jellyfinu smazaný: zpátky písmeno")

print()
print("--- obrázek se obnoví při přihlášení ---")
# Synchronizace běží v noci. Kdo si v Jellyfinu změní obrázek a přihlásí
# se, má ho vidět hned: otisk přijde s přihlášením, obrázek sám ne.
STAV_OBRAZKU["tag"] = "tag1"
asyncio.run(synchronizuj_uzivatele())
STAV_OBRAZKU["tag"] = "tag3"
DOTAZY_OBRAZKU.clear()
znovu = prihlas("mirek", "tajne", "jellyfin")
check(avatar(znovu) == "/account/obrazek?v=tag3",
      "po přihlášení nová adresa obrázku, bez čekání na synchronizaci")
check(not DOTAZY_OBRAZKU, "přihlášení samo obrázek nestahuje, jen porovná otisk")
check(accounts.obnov_obrazek("u-mirek", "tag3") is False
      and accounts.obnov_obrazek("U-MIREK", "tag4") is True,
      "beze změny se nic nepřepisuje; id se porovná bez ohledu na pomlčky a velikost")
STAV_OBRAZKU["tag"] = ""
znovu = prihlas("mirek", "tajne", "jellyfin")
check(avatar(znovu) == "", "obrázek v Jellyfinu smazaný: po přihlášení písmeno")
check(accounts.obnov_obrazek("u-neznamy", "tag9") is False
      and not db.query_one("SELECT id FROM users WHERE id = 'u-neznamy'"),
      "nesynchronizovaného diváka nezakládá (doplní ho synchronizace)")

# Otisk je kosmetika: když zápis selže (zamčená databáze, obnovená
# záloha bez sloupce), přihlášení přes Jellyfin musí projít dál.
import sqlite3  # noqa: E402

puvodni_connect = db.connect


def _zamcena(*args, **kwargs):
    raise sqlite3.OperationalError("database is locked")


db.connect = _zamcena
try:
    vysledek, chyba = accounts.obnov_obrazek("u-divak", "tag-x"), ""
except Exception as e:  # noqa: BLE001
    vysledek, chyba = None, repr(e)
finally:
    db.connect = puvodni_connect
check(vysledek is False, f"selhaný zápis otisku přihlášení neshodí {chyba}")

print()
print("--- průhledný obrázek leží na neutrální ploše ---")
# Obrázek s průhledným pozadím (postavička, logo) prosvítal modrou barvou
# kolečka s písmenem. Pod obrázkem je teď plocha motivu - v tmavém režimu
# tmavě šedá, ve světlém světle šedá -, takže do obrázku nic nepřidává.
styl = (PROJECT / "jellyscope" / "static" / "style.css").read_text(encoding="utf-8")
pod_obrazkem = re.search(r"\.ucet-avatar img\s*\{([^}]*)\}", styl)
pod_obrazkem = pod_obrazkem.group(1) if pod_obrazkem else ""
check("background: var(--surface-2)" in pod_obrazkem,
      "pod obrázkem je neutrální plocha motivu")
check("--accent" not in pod_obrazkem, "a ne barva zvýraznění")
# Kolečko pod obrázkem má barvu písmena; na vyhlazeném okraji by z něj
# prosvítal tenký modrý lem. S obrázkem je proto neutrální i kolečko.
s_obrazkem = re.search(r"\.ucet-avatar:has\(img\)\s*\{([^}]*)\}", styl)
check(s_obrazkem is not None and "var(--surface-2)" in s_obrazkem.group(1),
      "kolečko s obrázkem nemá modrý lem")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
