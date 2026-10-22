# -*- coding: utf-8 -*-
r"""API klíče po uživatelích: povolení, vlastník, stav, záznam použití, práva.

Dřív vyráběl klíče jen správce a každý klíč viděl všechna data serveru.
Teď klíč patří účtu a má přesně práva svého vlastníka; čtenář nebo divák
z Jellyfinu si ho smí vyrobit jen s povolením správce. Návrh:
docs/superpowers/specs/2026-10-15-api-klice-design.md.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_api_klice.py
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "api_klice.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic-dostatecne-dlouhy"
os.environ["JELLYFIN_URL"] = "http://jellyfin.test"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_DEMO"] = "0"

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from starlette.requests import Request  # noqa: E402

from jellyscope import accounts, api, applog, db, jellyfin, pristup, web  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


# Podvrzeny Jellyfin: zna jedineho divaka, aby slo prihlasit ucet z Jellyfinu.
def server(zadost: httpx.Request) -> httpx.Response:
    telo = json.loads(zadost.content or b"{}")
    if (telo.get("Username"), telo.get("Pw")) == ("mirek", "tajne"):
        return httpx.Response(200, json={"User": {
            "Id": "u-mirek", "Name": "mirek", "Policy": {"IsAdministrator": False}}})
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
db.forget_settings()


def prihlas(jmeno: str, heslo: str, zpusob: str = "mistni") -> TestClient:
    k = TestClient(web.app)
    k.post("/login", data={"username": jmeno, "password": heslo, "zpusob": zpusob},
           follow_redirects=False)
    return k


spravce_k = prihlas("spravce", "dlouheheslo")
ctenar_k = prihlas("ctenar", "ctenarheslo")
divak_k = prihlas("mirek", "tajne", "jellyfin")


def ucet(jmeno: str) -> dict:
    """Čerstvý řádek účtu (po každé změně se čte znovu)."""
    db.forget_settings()
    return accounts.get_by_name(jmeno) or accounts.podle_jellyfin_id("u-mirek") or {}


def divak() -> dict:
    db.forget_settings()
    return accounts.podle_jellyfin_id("u-mirek") or {}


print("--- povolení vlastního klíče ---")
check(pristup.smi_klic(ucet("spravce")) is True, "správce smí vždycky")
check(pristup.smi_klic(ucet("ctenar")) is False, "čtenář ve výchozím stavu ne")
check(pristup.smi_klic(divak()) is False, "divák z Jellyfinu ve výchozím stavu ne")
check(pristup.smi_klic(None) is False, "nikdo (bez účtu) ne")

db.set_setting("ctenar_smi_klic", "1")
check(pristup.smi_klic(ucet("ctenar")) is True, "zapnutá skupina čtenářů: čtenář smí")
check(pristup.smi_klic(divak()) is False, "a divák pořád ne - skupiny jsou zvlášť")
check(pristup.smi_klic_skupina(pristup.CTENAR) is True
      and pristup.smi_klic_skupina(pristup.DIVAK) is False, "smi_klic_skupina čte skupinu")

accounts.uloz_smi_klic(ucet("ctenar")["id"], "0")
check(pristup.smi_klic(ucet("ctenar")) is False, "výjimka „ne“ přebije zapnutou skupinu")
accounts.uloz_smi_klic(divak()["id"], "1")
check(pristup.smi_klic(divak()) is True, "výjimka „ano“ přebije vypnutou skupinu")
accounts.uloz_smi_klic(ucet("ctenar")["id"], None)
check(pristup.smi_klic(ucet("ctenar")) is True, "bez výjimky zase podle skupiny")

# Nesmysl ve sloupci (rucni zasah do databaze) = podle skupiny, ne zavrene
# ani otevrene dvere.
with db.connect() as conn:
    conn.execute("UPDATE accounts SET smi_klic = 'x' WHERE id = ?", (divak()["id"],))
check(pristup.smi_klic(divak()) is False, "hodnota mimo 1/0 = podle skupiny (divák: ne)")
accounts.uloz_smi_klic(divak()["id"], "neco")
check(divak().get("smi_klic") is None, "uloz_smi_klic uloží jen 1, 0, nebo nic")
db.set_setting("ctenar_smi_klic", "0")


print("--- stav a vlastník klíče ---")
with db.connect() as conn:
    sloupce_klicu = conn.table_columns("api_tokens")
check({"account_id", "stav"} <= set(sloupce_klicu), "api_tokens má vlastníka a stav")

anon = TestClient(web.app)


def zavolej(token: str, cesta: str = "/api/v1/summary"):
    return anon.get(cesta, headers={"Authorization": f"Bearer {token}"})


def pozadavek(token: str) -> Request:
    """Holý požadavek s hlavičkou - na přímé volání over_token."""
    return Request({"type": "http", "method": "GET", "path": "/api/v1/summary",
                    "query_string": b"",
                    "headers": [(b"authorization", f"Bearer {token}".encode())]})


def posledni_pouziti() -> dict:
    """Poslední řádek logu API (applog.read_api) pod jmény, která test zná."""
    radky = applog.read_api(applog.MAX_LINES)["radky"]
    if not radky:
        return {}
    radek = radky[0]
    return {"cesta": radek["cesta"], "ip": radek["ip"], "vysledek": radek["vysledek"],
            "token_id": radek["klic_id"], "vlastnik": radek["vlastnik"]}


def pocet_pouziti() -> int:
    return len(applog.read_api(applog.MAX_LINES)["radky"])


# Review Focus 5: dnesni klice nemaji vlastnika a po aktualizaci musi
# fungovat dal - jinak se rozbije Grafana i Homepage.
serveru = api.vytvor("Grafana")
check({"token", "jmeno", "id"} <= set(serveru), "vytvor vrací token, jméno i id")
check(zavolej(serveru["token"]).status_code == 200, "klíč serveru (bez vlastníka) projde")
overeni = api.over_token(pozadavek(serveru["token"]))
check(overeni["account"] is None and overeni["role"] == pristup.SPRAVCE,
      "klíč serveru má plná práva (role správce, bez účtu)")

check(api.vypni(serveru["id"]) == "Grafana", "vypni vrací jméno klíče")
odpoved = zavolej(serveru["token"])
check(odpoved.status_code == 403 and odpoved.json().get("error") == "key_disabled",
      "vypnutý klíč: 403 key_disabled")
check(api.zapni(serveru["id"]) == "Grafana", "zapni vrací jméno klíče")
check(zavolej(serveru["token"]).status_code == 200, "zapnutý klíč zase projde")
odpoved = zavolej(serveru["token"], "/api/v1/item/neexistuje")
check(odpoved.status_code == 404 and "json" in odpoved.headers.get("content-type", ""),
      "chyba pod /api/ je JSON, ne HTML stránka")
check(api.vypni(999999) == "" and api.zapni(999999) == "", "neexistující klíč: prázdné jméno")

ctenar_id = ucet("ctenar")["id"]
klic_ctenare = api.vytvor("Homepage", ctenar_id)
odpoved = zavolej(klic_ctenare["token"])
check(odpoved.status_code == 403 and odpoved.json().get("error") == "key_not_allowed",
      "čtenář bez povolení: 403 key_not_allowed")
db.set_setting("ctenar_smi_klic", "1")
check(zavolej(klic_ctenare["token"]).status_code == 200, "povolení zapnuté: klíč projde")
overeni = api.over_token(pozadavek(klic_ctenare["token"]))
check((overeni["account"] or {}).get("id") == ctenar_id and overeni["role"] == pristup.CTENAR,
      "over_token vrací účet vlastníka a jeho roli")
db.set_setting("ctenar_smi_klic", "0")
check(zavolej(klic_ctenare["token"]).status_code == 403, "povolení vrácené: zase 403")
check(any(r["id"] == klic_ctenare["id"] for r in api.seznam(ctenar_id)),
      "klíč se odebráním povolení nesmaže")

# Review Focus 2: spravce, ktery o roli prijde, dostane prava ctenare.
druhy_spravce = accounts.create("spravce2", "dlouheheslo2", is_admin=True)
klic_spravce = api.vytvor("Admin", druhy_spravce)
check(zavolej(klic_spravce["token"]).status_code == 200, "klíč správce projde i bez povolení")
accounts.set_admin(druhy_spravce, False)
check(zavolej(klic_spravce["token"]).status_code == 403,
      "správce bez role: klíč se řídí povolením čtenářů (403)")

vsechny = api.seznam()
check({"id", "name", "ukazka", "created_at", "last_used_at", "stav", "account_id",
       "vlastnik"} <= set(vsechny[0]), "seznam má vlastníka i stav")
podle_id = {r["id"]: r for r in vsechny}
check(podle_id[serveru["id"]]["vlastnik"] is None
      and podle_id[klic_ctenare["id"]]["vlastnik"] == "ctenar",
      "seznam bez parametru: všechny klíče a jejich vlastníci")
check([r["id"] for r in api.seznam(ctenar_id)] == [klic_ctenare["id"]],
      "seznam(account_id): jen klíče toho účtu")
check(podle_id[klic_spravce["id"]]["stav"] == "aktivni", "nový klíč je aktivní")


print("--- záznam použití ---")
zavolej(serveru["token"], "/api/v1/summary?days=7")
radek = posledni_pouziti()
check(radek.get("cesta") == "/api/v1/summary?days=7", "zapíše se adresa i s parametry")
check(radek.get("ip") == "testclient", "zapíše se IP adresa klienta")
check(radek.get("vysledek") == 200 and radek.get("token_id") == serveru["id"],
      "zapíše se výsledek a čí klíč to byl")
api.vypni(serveru["id"])
zavolej(serveru["token"])
check(posledni_pouziti().get("vysledek") == 403, "zamítnutý požadavek se zapíše taky (403)")
api.zapni(serveru["id"])

pred = pocet_pouziti()
zavolej("js_tohle-neni-zadny-klic")
anon.get("/api/v1/summary")
check(pocet_pouziti() == pred, "neplatný nebo chybějící klíč se nezapisuje")

# Review Focus 4: dlouha adresa se do logu nezapise cela.
zavolej(serveru["token"], "/api/v1/summary?x=" + "a" * 400)
check(len(posledni_pouziti().get("cesta", "")) == 300, "dlouhá adresa se ořízne na 300 znaků")

puvodni_zapis = api.zapis_pouziti


def rozbity_zapis(*args, **kwargs):
    raise RuntimeError("disk je plný")


api.zapis_pouziti = rozbity_zapis
logging.disable(logging.CRITICAL)
check(zavolej(serveru["token"]).status_code == 200, "chyba zápisu použití odpověď neshodí")
logging.disable(logging.NOTSET)
api.zapis_pouziti = puvodni_zapis

# Review Focus 1: smazany ucet vezme klic s sebou, zaznamy zustanou.
db.set_setting("ctenar_smi_klic", "1")
docasny = accounts.create("docasny", "docasneheslo")
klic_docasny = api.vytvor("Docasny", docasny)
check(zavolej(klic_docasny["token"]).status_code == 200, "klíč dočasného účtu projde")
jeho = len(applog.read_api(applog.MAX_LINES, klic=klic_docasny["id"])["radky"])
accounts.delete(docasny)
check(zavolej(klic_docasny["token"]).status_code == 401, "smazaný účet: klíč neexistuje (401)")
check(jeho > 0 and len(applog.read_api(applog.MAX_LINES, klic=klic_docasny["id"])["radky"]) == jeho,
      "záznamy v logu API zůstanou i po smazání účtu")
db.set_setting("ctenar_smi_klic", "0")


print("--- práva vlastníka ---")
get_cesty = sorted(r.path for r in api.router.routes if "GET" in getattr(r, "methods", ()))
check(set(get_cesty) == set(api.OBLASTI_ADRES),
      "každá adresa API má oblast (a v OBLASTI_ADRES nic navíc): "
      f"{sorted(set(get_cesty) ^ set(api.OBLASTI_ADRES))}")

CIZI_JMENO, CIZI_ID, CIZI_IP = "KarelCizinec", "u-karel", "10.0.0.5"
with db.connect() as conn:
    for uid, jmeno in (("u-mirek", "mirek"), (CIZI_ID, CIZI_JMENO)):
        conn.execute(
            "INSERT INTO users (id, name, is_administrator, is_disabled,"
            " synced_at) VALUES (?,?,0,0,?)", (uid, jmeno, db.utcnow()))
        for poradi in range(3):
            conn.execute(
                "INSERT INTO playback (session_key, user_id, user_name,"
                " item_id, item_name, started_at, last_seen_at,"
                " watched_seconds, remote_address, is_active)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (f"{uid}-{poradi}", uid, jmeno, "i-0", "Titul 0", db.utcnow(),
                 db.utcnow(), 1200, CIZI_IP if uid == CIZI_ID else "10.0.0.9",
                 1 if (uid == CIZI_ID and poradi == 0) else 0))
    conn.execute("INSERT INTO items (id, name, type) VALUES ('i-0','Titul 0','Movie')")
    conn.commit()

db.set_setting("ctenar_smi_klic", "1")
db.set_setting("divak_smi_klic", "1")
db.set_setting(pristup.klic_oblasti(pristup.CTENAR, pristup.PODLE_KLICE["sit"]), "0")
odpoved = zavolej(klic_ctenare["token"], "/api/v1/bandwidth")
check(odpoved.status_code == 403, f"čtenář bez oblasti Síť: /bandwidth 403 ({odpoved.status_code})")
check(posledni_pouziti().get("vysledek") == 403
      and posledni_pouziti().get("cesta") == "/api/v1/bandwidth",
      "adresa mimo oblast se zapíše jako 403")
adresy = zavolej(klic_ctenare["token"], "/api/v1").json().get("endpoints", {})
check("/api/v1/bandwidth" not in adresy and "/api/v1/summary" in adresy,
      "rozcestník ukáže jen adresy, které klíč smí")
check(zavolej(klic_ctenare["token"], "/api/v1/summary").status_code == 200,
      "oblast, kterou čtenář má, projde")
db.set_setting(pristup.klic_oblasti(pristup.CTENAR, pristup.PODLE_KLICE["sit"]), "1")
check(zavolej(klic_ctenare["token"], "/api/v1/bandwidth").status_code == 200,
      "oblast Síť zapnutá: /bandwidth 200")
check("/api/v1/bandwidth" in zavolej(klic_ctenare["token"], "/api/v1").json()["endpoints"],
      "a rozcestník ji zase ukáže")

# Unikovy test: divak vidi vsechno, co mu sprava dovoli - a presto
# nikde nesmi byt cizi jmeno, cizi id ani cizi adresa.
for oblast in pristup.OBLASTI:
    db.set_setting(pristup.klic_oblasti(pristup.DIVAK, oblast), "1")
klic_divaka = api.vytvor("Mobil", divak()["id"])
for cesta in get_cesty:
    adresa = cesta.replace("{item_id}", "i-0")
    odpoved = zavolej(klic_divaka["token"], adresa)
    unik = [s for s in (CIZI_JMENO, CIZI_ID, CIZI_IP) if s in odpoved.text]
    check(odpoved.status_code == 200 and not unik,
          f"divák {adresa}: {odpoved.status_code}, bez cizích údajů {unik or ''}")

# Dřív se cizí `user` potichu přepsal na vlastního diváka. Teď je to 403:
# klíč nedostane jiná data, než o která si řekl, a nikdo si nemyslí, že
# vidí cizí historii.
odpoved = zavolej(klic_divaka["token"], f"/api/v1/history?user={CIZI_ID}")
check(odpoved.status_code == 403 and odpoved.json().get("error") == "own_history_only",
      "divák s ?user=cizí id: 403 own_history_only")
historie = zavolej(klic_divaka["token"], "/api/v1/history").json()
check(historie["count"] > 0 and all(p["user_id"] == "u-mirek" for p in historie["playbacks"]),
      "divák bez ?user dostane jen svou historii")
hraje = zavolej(klic_divaka["token"], "/api/v1/now-playing").json()
check(hraje["count"] == 1 and hraje["sessions"][0]["user"] != CIZI_JMENO,
      "právě hraje: cizí relace je vidět, ale bez jména")

for jmeno, klic in (("klíč serveru", serveru), ("klíč správce", klic_spravce)):
    if klic is klic_spravce:
        accounts.set_admin(druhy_spravce, True)
    text = zavolej(klic["token"], "/api/v1/users").text \
        + zavolej(klic["token"], "/api/v1/history").text
    check(CIZI_JMENO in text and CIZI_ID in text, f"{jmeno} vidí jména (anonymizace se ho netýká)")
db.set_setting("ctenar_smi_klic", "0")
db.set_setting("divak_smi_klic", "0")


print("--- Nastavení -> API ---")


def stav_klice(token_id: int) -> str:
    radek = db.query_one("SELECT stav FROM api_tokens WHERE id = ?", (token_id,))
    return radek["stav"] if radek else ""


def posli(klient: TestClient, adresa: str, **data) -> int:
    return klient.post(adresa, data={k: str(v) for k, v in data.items()},
                       follow_redirects=False).status_code


stranka = spravce_k.get("/settings?section=api").text
check("Homepage" in stranka and "ctenar" in stranka, "správce vidí klíč čtenáře i s vlastníkem")
check("klíč serveru" in stranka, "klíč bez vlastníka je popsaný jako klíč serveru")

check(posli(spravce_k, "/settings/api/token/vypnout", token_id=klic_ctenare["id"]) == 303
      and stav_klice(klic_ctenare["id"]) == "vypnuty", "správce klíč vypne")
check("vypnutý" in spravce_k.get("/settings?section=api").text, "stav je vidět v tabulce")
check(posli(spravce_k, "/settings/api/token/zapnout", token_id=klic_ctenare["id"]) == 303
      and stav_klice(klic_ctenare["id"]) == "aktivni", "správce klíč zase zapne")
na_smazani = api.vytvor("Na smazání", ctenar_id)
check(posli(spravce_k, "/settings/api/token/zrusit", token_id=na_smazani["id"]) == 303
      and not stav_klice(na_smazani["id"]), "správce zneplatní i cizí klíč")
check(posli(spravce_k, "/settings/api/token/vypnout", token_id="nesmysl") == 303,
      "nesmyslné id nic neshodí")

zavolej(serveru["token"], "/api/v1/summary?znacka=serveru")
zavolej(klic_ctenare["token"], "/api/v1/summary?znacka=ctenare")
stranka = spravce_k.get("/settings?section=log&log_druh=api").text
check("/api/v1/summary?znacka=serveru" in stranka and "testclient" in stranka,
      "log API v Nastavení → Log ukazuje adresu i IP")
podle_klice = spravce_k.get(f"/settings?section=log&log_druh=api&log_klic={klic_ctenare['id']}").text
check("znacka=ctenare" in podle_klice and "znacka=serveru" not in podle_klice, "filtr klíče ukáže jen ten klíč")
podle_uctu = spravce_k.get("/settings?section=log&log_druh=api&log_ucet=ctenar").text
check("znacka=ctenare" in podle_uctu and "znacka=serveru" not in podle_uctu, "filtr uživatele ukáže jen jeho klíče")
check(spravce_k.get("/settings?section=api&klic=abc&ucet=").status_code == 200,
      "nesmyslný filtr stránku neshodí")

nove = spravce_k.post("/settings/api/token", data={"name": "Moje Grafana"},
                      follow_redirects=False)
moje = [r for r in api.seznam(ucet("spravce")["id"]) if r["name"] == "Moje Grafana"]
check(nove.status_code == 303 and len(moje) == 1, "klíč vyrobený správcem patří jemu")

for adresa in ("/settings/api/token/vypnout", "/settings/api/token/zapnout"):
    check(posli(ctenar_k, adresa, token_id=serveru["id"]) == 403,
          f"čtenář na {adresa}: 403")
check(stav_klice(serveru["id"]) == "aktivni", "a stav klíče se nezměnil")


print("--- Nastavení -> Účty ---")
# Zaskrtavatko, ktere neni zaskrtnute, prohlizec vubec neposle.
check(posli(spravce_k, "/settings/accounts/klice", ctenar_smi_klic="on") == 303
      and db.get_setting("ctenar_smi_klic") == "1"
      and db.get_setting("divak_smi_klic") == "0", "skupiny: čtenáři ano, diváci ne")
posli(spravce_k, "/settings/accounts/klice", divak_smi_klic="on")
check(db.get_setting("ctenar_smi_klic") == "0" and db.get_setting("divak_smi_klic") == "1",
      "skupiny: obráceně")
stranka = spravce_k.get("/settings?section=accounts").text
check('name="divak_smi_klic" checked' in stranka
      and 'name="ctenar_smi_klic" checked' not in stranka, "karta ukazuje uložený stav")
check('name="smi_klic"' in stranka, "okno Práva má volbu vlastního klíče")

posli(spravce_k, "/settings/accounts/prava", account_id=ctenar_id, rezim="skupina",
      smi_klic="ne")
check(ucet("ctenar").get("smi_klic") == "0" and ucet("ctenar").get("pristup") is None,
      "režim podle skupiny: výjimka klíče se uloží, strom zůstane skupinový")
posli(spravce_k, "/settings/accounts/prava", account_id=ctenar_id, rezim="vlastni",
      vidi_prehled="on", smi_klic="ano")
vlastni = pristup.vlastni_prava(ucet("ctenar")) or {}
check(ucet("ctenar").get("smi_klic") == "1" and set(vlastni.get("oblasti", ())) == {"prehled"},
      "režim vlastní: výjimka klíče se uloží vedle stromu")
posli(spravce_k, "/settings/accounts/prava", account_id=ctenar_id, rezim="vlastni",
      vidi_prehled="on", smi_klic="skupina")
vlastni = pristup.vlastni_prava(ucet("ctenar")) or {}
check(ucet("ctenar").get("smi_klic") is None and set(vlastni.get("oblasti", ())) == {"prehled"},
      "„podle skupiny“ = žádná výjimka, strom beze změny")
posli(spravce_k, "/settings/accounts/prava", account_id=ctenar_id, rezim="skupina")
check(ucet("ctenar").get("smi_klic") is None and ucet("ctenar").get("pristup") is None,
      "formulář bez volby výjimku nechá, jak byla")

check(posli(ctenar_k, "/settings/accounts/klice", ctenar_smi_klic="on") == 403
      and db.get_setting("ctenar_smi_klic") == "0", "čtenář povolení měnit nesmí (403)")
db.set_setting("divak_smi_klic", "0")


print("--- Můj účet ---")
novacek_id = accounts.create("novacek", "novacekheslo")
novacek_k = prihlas("novacek", "novacekheslo")
check('id="klice"' not in novacek_k.get("/account").text,
      "bez povolení a bez klíčů karta API klíče není")
check(posli(novacek_k, "/account/klic", name="Pokus") == 303 and not api.seznam(novacek_id),
      "bez povolení POST nic nezaloží")

accounts.uloz_smi_klic(novacek_id, "1")
check('id="klice"' in novacek_k.get("/account").text, "s povolením je karta vidět")
check(posli(novacek_k, "/account/klic", name="Muj skript") == 303
      and [r["name"] for r in api.seznam(novacek_id)] == ["Muj skript"],
      "vyrobený klíč patří účtu z relace")
stranka = novacek_k.get("/account").text
nalez = re.search(r"(js_[A-Za-z0-9_\-]{20,})", stranka)
novy = nalez.group(1) if nalez else ""
check(bool(novy) and zavolej(novy, "/api/v1/").status_code == 200,
      "nový klíč se ukáže a funguje na /api/v1/")
check(novy and novy not in novacek_k.get("/account").text, "a podruhé už se neukáže")
check(novy and novy not in spravce_k.get("/settings?section=api").text,
      "ani v Nastavení - relace se nemíchají")

check(posli(novacek_k, "/account/klic/zrusit", token_id=klic_ctenare["id"]) == 303
      and stav_klice(klic_ctenare["id"]) == "aktivni", "cizí klíč zneplatnit nejde")

sit = pristup.PODLE_KLICE["sit"]
db.set_setting(pristup.klic_oblasti(pristup.CTENAR, sit), "0")
stranka = novacek_k.get("/account").text
check("GET /api/v1/bandwidth" not in stranka and "GET /api/v1/summary" in stranka,
      "seznam adres na kartě jen podle práv účtu")
db.set_setting(pristup.klic_oblasti(pristup.CTENAR, sit), "1")

accounts.uloz_smi_klic(novacek_id, "0")
stranka = novacek_k.get("/account").text
check('id="klice"' in stranka and 'action="/account/klic"' not in stranka,
      "bez povolení, ale s klíčem: karta je, formulář na nový ne")
posli(novacek_k, "/account/klic", name="Druhy")
check(len(api.seznam(novacek_id)) == 1, "vlastník bez povolení nový nevyrobí")
muj = api.seznam(novacek_id)[0]["id"]
check(posli(novacek_k, "/account/klic/zrusit", token_id=muj) == 303
      and not api.seznam(novacek_id), "ale starý zneplatní")

db.set_setting("divak_smi_klic", "1")
check(posli(divak_k, "/account/klic", name="Telefon") == 303, "divák z Jellyfinu klíč vyrobí (303)")
telefon = [r["id"] for r in api.seznam(divak()["id"]) if r["name"] == "Telefon"]
check(len(telefon) == 1, "a klíč patří jemu")
check(telefon and posli(divak_k, "/account/klic/zrusit", token_id=telefon[0]) == 303
      and not stav_klice(telefon[0]), "divák svůj klíč zneplatní (303)")
check(posli(divak_k, "/settings/api/token", name="Cizi") == 403, "jiný POST má divák dál 403")
db.set_setting("divak_smi_klic", "0")


print("--- po revizi: Jellyfin zavřený pro diváky ---")
db.set_setting("divak_smi_klic", "1")
klic_mirka = api.vytvor("Mirkuv skript", divak()["id"])
check(zavolej(klic_mirka["token"], "/api/v1/").status_code == 200, "klíč diváka projde")
db.set_setting(pristup.LOGIN_KLIC, "0")
odpoved = zavolej(klic_mirka["token"], "/api/v1/")
check(odpoved.status_code == 403, f"přihlášení přes Jellyfin vypnuté: klíč diváka 403 ({odpoved.status_code})")
check(posledni_pouziti().get("vysledek") == 403, "a zamítnutí se zapíše")
check(zavolej(klic_ctenare["token"], "/api/v1/").status_code in (200, 403)
      and zavolej(serveru["token"], "/api/v1/").status_code == 200,
      "klíče místních účtů se to netýká")
db.set_setting(pristup.LOGIN_KLIC, "1")
with db.connect() as conn:
    conn.execute("UPDATE users SET is_disabled = 1 WHERE id = 'u-mirek'")
    conn.commit()
check(zavolej(klic_mirka["token"], "/api/v1/").status_code == 403,
      "divák zakázaný v Jellyfinu: klíč 403")
with db.connect() as conn:
    conn.execute("UPDATE users SET is_disabled = 0 WHERE id = 'u-mirek'")
    conn.commit()
check(zavolej(klic_mirka["token"], "/api/v1/").status_code == 200, "povolený zase projde")
api.zrus(klic_mirka["id"])
db.set_setting("divak_smi_klic", "0")


print("--- po revizi: zápis použití až po odpovědi ---")
udalosti: list[str] = []


def sledovany_zapis(*args, **kwargs):
    udalosti.append("zapis")


async def asgi_pozadavek(cesta: str, token: str) -> None:
    rozsah = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
              "method": "GET", "scheme": "http", "path": cesta, "raw_path": cesta.encode(),
              "query_string": b"", "root_path": "", "client": ("testclient", 50000),
              "server": ("testserver", 80),
              "headers": [(b"host", b"testserver"),
                          (b"authorization", f"Bearer {token}".encode())]}

    async def prijmi():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def posli_asgi(zprava):
        if zprava["type"] == "http.response.body" and not zprava.get("more_body"):
            udalosti.append("odeslano")

    await web.app(rozsah, prijmi, posli_asgi)


api.zapis_pouziti = sledovany_zapis
asyncio.run(asgi_pozadavek("/api/v1/summary", serveru["token"]))
api.zapis_pouziti = puvodni_zapis
check(udalosti == ["odeslano", "zapis"],
      f"klient má odpověď dřív, než se zapisuje ({udalosti})")

puvodni_prehled = api.stats.overview


def rozbity_prehled(*args, **kwargs):
    raise RuntimeError("rozbitý dotaz")


api.stats.overview = rozbity_prehled
logging.disable(logging.CRITICAL)
odpoved = TestClient(web.app, raise_server_exceptions=False).get(
    "/api/v1/summary?days=29", headers={"Authorization": f"Bearer {serveru['token']}"})
logging.disable(logging.NOTSET)
api.stats.overview = puvodni_prehled
check(odpoved.status_code == 500 and posledni_pouziti().get("cesta") == "/api/v1/summary?days=29"
      and posledni_pouziti().get("vysledek") == 500, "neodchycená chyba (500) se zapíše taky")


class Zachytavac(logging.Handler):
    def __init__(self):
        super().__init__()
        self.zaznamy: list[logging.LogRecord] = []

    def emit(self, record):
        self.zaznamy.append(record)


zachytavac = Zachytavac()
web_log = logging.getLogger("jellyscope.web")
web_log.addHandler(zachytavac)
web_log.propagate = False
api.zapis_pouziti = rozbity_zapis
web._posledni_chyba_zapisu = 0.0
zavolej(serveru["token"])
zavolej(serveru["token"])
api.zapis_pouziti = puvodni_zapis
web_log.removeHandler(zachytavac)
web_log.propagate = True
chyb_zapisu = [r for r in zachytavac.zaznamy if "Zapis pouziti" in r.getMessage()]
check(len(chyb_zapisu) == 1, f"opakovaná chyba zápisu se zaloguje jednou, ne pokaždé ({len(chyb_zapisu)})")


print("--- po revizi: nulový znak ---")
zavolej(serveru["token"], "/api/v1/item/abc%00def")
check("\x00" not in posledni_pouziti().get("cesta", "x\x00"),
      "nulový znak z adresy se do záznamu nedostane")


print("--- po revizi: Můj účet ---")
accounts.uloz_smi_klic(novacek_id, "1")
db.set_setting(pristup.klic_oblasti(pristup.CTENAR, pristup.PODLE_KLICE["prehled"]), "0")
stranka = novacek_k.get("/account").text
priklad = re.search(r"api/v1/([a-z\-/]*)</code></pre>", stranka)
povolene = re.findall(r"<code>GET /api/v1/([a-z/-]*)", stranka)
check(priklad is not None and priklad.group(1) in povolene,
      f"příklad curl míří na adresu, kterou klíč smí ({priklad and priklad.group(1)})")
db.set_setting(pristup.klic_oblasti(pristup.CTENAR, pristup.PODLE_KLICE["prehled"]), "1")
posli(novacek_k, "/account/klic", name="Se sloupcem")
check("Vyrobeno" in novacek_k.get("/account").text, "tabulka klíčů má sloupec Vyrobeno")
for r in api.seznam(novacek_id):
    api.zrus(r["id"])
accounts.uloz_smi_klic(novacek_id, None)


print("--- nový klíč v řádku tabulky, Kopírovat jen do obnovení ---")
CELY_KLIC = r"js_[A-Za-z0-9_\-]{20,}"


def radek_klice(html: str, jmeno: str) -> str:
    """Řádek tabulky (<tr>…</tr>), ve kterém je klíč s tímhle jménem."""
    for radek in re.findall(r"<tr>.*?</tr>", html, re.S):
        if jmeno in radek:
            return radek
    return ""


for kdo, klient, vyrob, stranka_url in (
        ("správce", spravce_k, "/settings/api/token", "/settings?section=api"),
        ("uživatel", novacek_k, "/account/klic", "/account")):
    if klient is novacek_k:
        accounts.uloz_smi_klic(novacek_id, "1")
    posli(klient, vyrob, name=f"Radek {kdo}")
    html = klient.get(stranka_url).text
    radek = radek_klice(html, f"Radek {kdo}")
    cely = re.findall(CELY_KLIC, html)
    check(len(cely) == 1 and cely[0] in radek,
          f"{kdo}: celý klíč je jednou, v řádku svého klíče")
    check('data-kopirovat="' in radek, f"{kdo}: v řádku je tlačítko Kopírovat")
    check("Klíč vznikl a vidíš ho naposledy." not in html,
          f"{kdo}: rámeček nad formulářem zmizel")
    check(cely and zavolej(cely[0], "/api/v1/").status_code == 200, f"{kdo}: ukázaný klíč funguje")
    znovu = klient.get(stranka_url).text
    check(not re.findall(CELY_KLIC, znovu) and 'data-kopirovat="' not in znovu,
          f"{kdo}: po obnovení stránky klíč i Kopírovat zmizí")
    check("*" * 12 in radek_klice(znovu, f"Radek {kdo}"), f"{kdo}: zbyde začátek a hvězdičky")
for r in api.seznam(novacek_id):
    api.zrus(r["id"])
for r in api.seznam(ucet("spravce")["id"]):
    if r["name"] == "Radek správce":
        api.zrus(r["id"])

# Stranka s celym klicem se nesmi vratit z mezipameti prohlizece (tlacitko
# Zpet, i po odhlaseni na sdilenem pocitaci).
for klient, vyrob, stranka_url in ((spravce_k, "/settings/api/token", "/settings?section=api"),
                                   (novacek_k, "/account/klic", "/account")):
    if klient is novacek_k:
        accounts.uloz_smi_klic(novacek_id, "1")
    posli(klient, vyrob, name="Bez mezipameti")
    odpoved = klient.get(stranka_url)
    check(re.findall(CELY_KLIC, odpoved.text)
          and "no-store" in odpoved.headers.get("cache-control", ""),
          f"{stranka_url}: stránka s celým klíčem má Cache-Control: no-store")
for r in api.seznam():
    if r["name"] == "Bez mezipameti":
        api.zrus(r["id"])

# Klic vyrobeny v Nastaveni patri spravci, ale na Muj ucet se ukazat nema.
posli(spravce_k, "/settings/api/token", name="Jen v Nastaveni")
check(not re.findall(CELY_KLIC, spravce_k.get("/account").text),
      "klíč vyrobený v Nastavení se na Můj účet neukáže")
check(len(re.findall(CELY_KLIC, spravce_k.get("/settings?section=api").text)) == 1,
      "v Nastavení ano")
for r in api.seznam():
    if r["name"] == "Jen v Nastaveni":
        api.zrus(r["id"])


print("--- návod jako u správce ---")


def okno_navodu(html: str) -> str:
    zacatek = html.find('id="okno-api-dokumentace"')
    return html[zacatek:html.find("</dialog>", zacatek)] if zacatek >= 0 else ""


klic_site = pristup.klic_oblasti(pristup.CTENAR, pristup.PODLE_KLICE["sit"])
puvodni_sit = db.get_setting(klic_site, "")
db.set_setting(klic_site, "0")
stranka = novacek_k.get("/account").text
navod = okno_navodu(stranka)
check(bool(navod) and 'data-okno="okno-api-dokumentace"' in stranka,
      "Můj účet má tlačítko a okno Jak se to používá")
check("GET /api/v1/libraries" in navod and "GET /api/v1/bandwidth" not in navod,
      "v okně uživatele jen adresy, které jeho klíč smí")
check("Authorization: Bearer" in navod, "okno vysvětluje hlavičku s klíčem")
check("Správce vidí" in navod, "a říká, že správce vidí použití klíče")
spravce_navod = okno_navodu(spravce_k.get("/settings?section=api").text)
check("GET /api/v1/bandwidth" in spravce_navod and "GET /api/v1/summary" in spravce_navod,
      "správce v okně vidí všechny adresy")
# Seznam v okne je psany rucne (kvuli popiskum) - nesmi se rozejit s tim,
# co API doopravdy ma. Kazda adresa z OBLASTI_ADRES v nem musi byt.
chybi = [a for a in api.povolene_adresy(ucet("spravce"))
         if "GET " + a.replace("{id}", "&lt;id&gt;") not in spravce_navod]
check(not chybi, f"okno návodu zná každou adresu API ({chybi})")
# Filtrování a řazení: zápis, příklady a pole u adres. Pole se berou
# z api.SEZNAMY - stejného popisu, jakým se dotaz kontroluje - takže
# nápověda nemůže slíbit pole, které neexistuje.
check("pole[operátor]=hodnota" in navod and "path[starts_with]=" in navod
      and "sort=plays,added_at" in navod and "limit=5" in navod,
      "okno uživatele vysvětluje filtry, řazení a limit s příkladem")
check("never_finished." in spravce_navod and "starts_with" in spravce_navod
      and "not_contains" in spravce_navod, "příklad s předponou seznamu a tabulka operátorů")
check('data-pole-adresy="/api/v1/least-played"' in navod and "<code>path</code>" in navod,
      "u adresy je rozbalovací seznam polí")
check('data-pole-adresy="/api/v1/bandwidth"' not in navod,
      "pole adres, na které klíč nesmí, nejsou vidět")
check(all(f'data-pole-adresy="{a}"' in spravce_navod for a in api.SEZNAMY),
      "správce vidí pole u všech adres se seznamem")
check("unknown_field" in navod and "400" in navod, "okno vysvětluje chybu 400 s kódem")
check("user=&amp;" not in spravce_navod and "user=&" not in spravce_navod,
      "příklad historie bez prázdného user=")
navod_spravce_ucet = okno_navodu(spravce_k.get("/account").text)
check("GET /api/v1/bandwidth" in navod_spravce_ucet and "Správce vidí" not in navod_spravce_ucet
      and "tvůj klíč smí" not in navod_spravce_ucet,
      "správce na Můj účet: všechny adresy, bez vět pro čtenáře")
if puvodni_sit:
    db.set_setting(klic_site, puvodni_sit)
else:
    with db.connect() as conn:
        conn.execute("DELETE FROM settings WHERE key = ?", (klic_site,))
        conn.commit()
    db.forget_settings()
accounts.uloz_smi_klic(novacek_id, None)


print("--- API podle oprávnění účtu, ne podle výchozích hodnot ---")
# Divak z Jellyfinu: skupinu nastavuje Nastaveni -> Jellyfin (/settings/divaci).
# Nastaveni skupiny divaku se po sekci vrati, jak bylo - i to, co v tabulce
# vubec nebylo (pak plati vychozi hodnota).
KLICE_SKUPINY = ([pristup.LOGIN_KLIC, "divak_anonymizace", "divak_smi_klic"]
                 + [pristup.klic_oblasti(pristup.DIVAK, o) for o in pristup.OBLASTI])
zaloha_skupiny = {k: (db.query_one("SELECT value FROM settings WHERE key = ?", (k,)) or {}).get("value")
                  for k in KLICE_SKUPINY}
db.set_setting("divak_smi_klic", "1")
mirek_id = divak()["id"]
klic_opravneni = api.vytvor("Opravneni", mirek_id)


def kod(cesta: str) -> int:
    return zavolej(klic_opravneni["token"], cesta).status_code


posli(spravce_k, "/settings/divaci", jellyfin_login="on", vidi_prehled="on", vidi_sit="on",
      anonymizace="on")
check(pristup.anonymizace_zapnuta(pristup.DIVAK), "skupina diváků jména skrývá")
check(kod("/api/v1/summary") == 200 and kod("/api/v1/bandwidth") == 200
      and kod("/api/v1/libraries") == 403,
      "skupina diváků uložená v Nastavení → Jellyfin: klíč smí přesně její oblasti")
accounts.uloz_prava(mirek_id, pristup.zabal_prava(["knihovna"], True))
check(kod("/api/v1/libraries") == 200, "vlastní práva účtu přidají oblast, kterou skupina nemá")
check(kod("/api/v1/summary") == 403 and kod("/api/v1/bandwidth") == 403,
      "a uberou oblasti, které skupina má")
accounts.uloz_prava(mirek_id, pristup.zabal_prava(["uzivatele"], False))
check(CIZI_JMENO in zavolej(klic_opravneni["token"], "/api/v1/users").text,
      "vlastní anonymizace vypnutá: klíč vidí jména, i když skupina je skrývá")
accounts.uloz_prava(mirek_id, pristup.zabal_prava(["uzivatele"], True))
check(CIZI_JMENO not in zavolej(klic_opravneni["token"], "/api/v1/users").text,
      "vlastní anonymizace zapnutá: jména schovaná")
accounts.uloz_smi_klic(mirek_id, "0")
check(kod("/api/v1/") == 403, "výjimka „nesmí klíče“ u účtu přebije povolenou skupinu")
accounts.uloz_smi_klic(mirek_id, None)
accounts.uloz_prava(mirek_id, None)
api.zrus(klic_opravneni["id"])
with db.connect() as conn:
    for klic_nastaveni, hodnota in zaloha_skupiny.items():
        if hodnota is None:
            conn.execute("DELETE FROM settings WHERE key = ?", (klic_nastaveni,))
        else:
            conn.execute("UPDATE settings SET value = ? WHERE key = ?", (hodnota, klic_nastaveni))
    conn.commit()
db.forget_settings()

# Okno Prava u divaka odkazuje na jeho skupinu v sekci Jellyfin - mimo
# <label> prepinace, jinak je odkaz soucasti nazvu volby a klik okno
# zavre bez ulozeni. U ctenare odkaz neni, jeho skupina je primo tady.
ucty = spravce_k.get("/settings?section=accounts").text


def okno_prav(html: str, account_id: int) -> str:
    zacatek = html.find(f'id="okno-prava-{account_id}"')
    return html[zacatek:html.find("</dialog>", zacatek)] if zacatek >= 0 else ""


okno_mirka = okno_prav(ucty, mirek_id)
check('href="/settings?section=jellyfin#divaci"' in okno_mirka
      and not re.search(r"<label[^>]*>(?:(?!</label>).)*section=jellyfin#divaci", okno_mirka, re.S),
      "okno Práva u diváka odkazuje na skupinu v sekci Jellyfin (mimo popisek volby)")
check("section=jellyfin#divaci" not in okno_prav(ucty, ctenar_id),
      "okno Práva u čtenáře ten odkaz nemá")


print("--- texty o povolení ---")
stranka = spravce_k.get("/settings?section=accounts").text
check("Smí si vyrábět klíče" in stranka and "Smí si ho vyrobit" not in stranka,
      "volba v okně Práva mluví o klíčích, ne o jednom")
check("Vlastní API klíče" in stranka, "nadpis i popisek jsou v množném čísle")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
