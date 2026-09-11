# -*- coding: utf-8 -*-
r"""Divák přihlášený přes Jellyfin vidí sebe - a nikoho jiného.

Přihlášení jménem a heslem z Jellyfinu znamená, že do Jellyscope chodí
i lidé, kterým do cizí historie nic není. Rozdělení dat proto nesmí
stát na tom, že se na žádnou stránku nezapomnělo: **co není výslovně
povolené, je zakázané** (viz `web.DIVAK_SMI`).

Tenhle test je ta pojistka. Projde všechny routy aplikace jako divák
a u každé odpovědi hledá stopy po druhém divákovi - jeho jméno i jeho
id. Když někdo přidá stránku a zapomene na rozsah, spadne to tady,
a ne u někoho na serveru.

Ověřuje se:

* přihlášení proti Jellyfinu (divák, správce, vypnutý účet, špatné heslo),
* práva se přebírají z Jellyfinu při každém přihlášení,
* místní účty tím nejsou dotčené,
* divák se dostane jen na povolené adresy a jen přes GET,
* v žádné odpovědi není cizí jméno ani cizí id,
* správce vidí všechno dál.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_rozsah_divaka.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "rozsah.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic-dostatecne-dlouhy"
os.environ["JELLYFIN_URL"] = "http://jellyfin.test"
os.environ["JELLYFIN_API_KEY"] = "test-key"

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, db, jellyfin, web  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


# --- podvrzeny Jellyfin -------------------------------------------------
JE_SPRAVCE = {"sef": True}
UCTY = {
    ("mirek", "tajne"): {"Id": "u-mirek", "Name": "Mirek",
                         "Policy": {"IsAdministrator": False}},
    ("sef", "tajne"): {"Id": "u-sef", "Name": "Sef", "Policy": {}},
    ("vypnuty", "tajne"): {"Id": "u-vyp", "Name": "Vypnuty",
                           "Policy": {"IsAdministrator": False,
                                      "IsDisabled": True}},
}


def server(zadost: httpx.Request) -> httpx.Response:
    telo = json.loads(zadost.content or b"{}")
    udaje = UCTY.get((telo.get("Username", ""), telo.get("Pw", "")))
    if udaje is None:
        return httpx.Response(401, json={})
    if udaje["Id"] == "u-sef":
        udaje = {**udaje,
                 "Policy": {"IsAdministrator": JE_SPRAVCE["sef"]}}
    return httpx.Response(200, json={"User": udaje})


async def podvrzeny_klient(self):
    self._client = httpx.AsyncClient(transport=httpx.MockTransport(server),
                                     base_url="http://jellyfin.test")
    return self


jellyfin.JellyfinClient.__aenter__ = podvrzeny_klient

db.init_db()
accounts.create("spravce", "dlouheheslo", is_admin=True)

# Prihlasovani jellyfinovym heslem je ve vychozim stavu vypnute -
# otevrit statistiky cele domacnosti je rozhodnuti, ne vedlejsi ucinek
# aktualizace. Test si ho zapne, protoze o nem je.
from jellyscope import pristup  # noqa: E402

db.set_setting(pristup.LOGIN_KLIC, "1")
db.forget_settings()

CIZI_JMENO = "KarelCizinec"
CIZI_ID = "u-karel"
with db.connect() as conn:
    for uid, jmeno in (("u-mirek", "Mirek"), (CIZI_ID, CIZI_JMENO)):
        conn.execute(
            "INSERT INTO users (id, name, is_administrator, is_disabled,"
            " synced_at) VALUES (?,?,0,0,?)", (uid, jmeno, db.utcnow()))
        for poradi in range(4):
            conn.execute(
                "INSERT INTO playback (session_key, user_id, user_name,"
                " item_id, item_name, started_at, last_seen_at,"
                " watched_seconds, remote_address, is_active)"
                " VALUES (?,?,?,?,?,?,?,?,?,0)",
                (f"{uid}-{poradi}", uid, jmeno, f"i-{poradi}",
                 f"Titul {poradi}", "2026-09-01 10:00:00",
                 "2026-09-01 10:20:00", 1200, "10.0.0.5"))
    conn.execute("INSERT INTO items (id, name, type) VALUES ('i-0','Titul 0','Movie')")
    conn.commit()


def klient() -> TestClient:
    return TestClient(web.app)


def prihlas(jmeno: str, heslo: str, zpusob: str = "") -> tuple[TestClient, int]:
    """Přihlášení tím tlačítkem, které by člověk zmáčkl.

    `zpusob` se odvodí od toho, jestli takový účet zná Jellyfin -
    přihlašovací stránka má dvě tlačítka a každé klepe na jiné dveře.
    """
    if not zpusob:
        zpusob = "jellyfin" if any(jm == jmeno for jm, _ in UCTY) else "mistni"
    k = klient()
    odpoved = k.post("/login",
                     data={"username": jmeno, "password": heslo,
                           "zpusob": zpusob},
                     follow_redirects=False)
    return k, odpoved.status_code


print("--- přihlášení přes Jellyfin ---")
k_divak, stav = prihlas("mirek", "tajne")
check(stav == 303, f"divák se přihlásí ({stav})")
ucet = dict(db.query_one(
    "SELECT username, is_admin, jellyfin_user_id, password_hash"
    " FROM accounts WHERE jellyfin_user_id = 'u-mirek'") or {})
check(ucet.get("jellyfin_user_id") == "u-mirek", "účet je svázaný s divákem")
check(not ucet.get("is_admin"), "a není správce")
check(not ucet.get("password_hash"),
      "heslo se sem neukládá - dovnitř jedině přes Jellyfin")

_, stav = prihlas("vypnuty", "tajne")
check(stav == 401, f"vypnutý účet dovnitř nesmí ({stav})")
_, stav = prihlas("mirek", "spatne")
check(stav == 401, f"špatné heslo neprojde ({stav})")
_, stav = prihlas("spravce", "dlouheheslo")
check(stav == 303, "místní účet funguje dál")
_, stav = prihlas("spravce", "tajne", zpusob="mistni")
check(stav == 401, "a jellyfinové heslo do něj nepustí")

print()
print("--- každé tlačítko klepe na jiné dveře ---")
_, stav = prihlas("mirek", "tajne", zpusob="mistni")
check(stav == 401,
      "jellyfinové heslo se s místními otisky neporovnává")
_, stav = prihlas("spravce", "dlouheheslo", zpusob="jellyfin")
check(stav == 401,
      "a heslo místního účtu se Jellyfinu neposílá")
stranka = klient().get("/login").text
check('value="jellyfin"' in stranka,
      "se zapnutým přihlašováním je tlačítko na stránce")

print()
print("--- práva se berou z Jellyfinu při každém přihlášení ---")
prihlas("sef", "tajne")
check(bool(dict(db.query_one(
    "SELECT is_admin FROM accounts WHERE jellyfin_user_id = 'u-sef'"))["is_admin"]),
    "správce Jellyfinu je správcem i tady")
JE_SPRAVCE["sef"] = False
prihlas("sef", "tajne")
check(not dict(db.query_one(
    "SELECT is_admin FROM accounts WHERE jellyfin_user_id = 'u-sef'"))["is_admin"],
    "odebraná práva platí hned při příštím přihlášení")

print()
print("--- kam divák smí a kam ne ---")
k_divak, _ = prihlas("mirek", "tajne")
POVOLENE = ("/users/u-mirek", "/history", "/insights", "/library",
            "/item/i-0", "/partials/top-items", "/partials/recently-added")
ZAKAZANE = ("/users", "/users/" + CIZI_ID, "/network", "/settings",
            "/dashboard", "/languages", "/srovnani", "/partials/now-playing",
            "/partials/network-live", "/api/now-playing")

for cesta in POVOLENE:
    stav = k_divak.get(cesta, follow_redirects=False).status_code
    check(stav == 200, f"smí {cesta} ({stav})")
for cesta in ZAKAZANE:
    stav = k_divak.get(cesta, follow_redirects=False).status_code
    check(stav == 403, f"nesmí {cesta} ({stav})")

odpoved = k_divak.get("/", follow_redirects=False)
check(odpoved.headers.get("location") == "/users/u-mirek",
      f"Přehled ho pošle na jeho stránku ({odpoved.headers.get('location')})")
check(k_divak.post("/item/i-0/delete",
                   follow_redirects=False).status_code == 403,
      "a nic nemaže - divák jen čte")

print()
print("--- v odpovědích není po cizím divákovi ani stopa ---")
# Tohle je ta pojistka: kdyz nekdo prida stranku a zapomene na rozsah,
# spadne to tady. Prochazi se VSECHNY routy aplikace, ne vybrane.
cesty = []
for routa in web.app.routes:
    cesta = getattr(routa, "path", "")
    if not cesta or "GET" not in (getattr(routa, "methods", None) or set()):
        continue
    if "{" in cesta:
        cesta = (cesta.replace("{user_id}", "u-mirek")
                      .replace("{item_id}", "i-0")
                      .replace("{series_id}", "i-0")
                      .replace("{library_id}", "lib"))
    if "{" in cesta:
        continue
    cesty.append(cesta)

# Nejdriv se zapne VSECHNO, co jde divakovi povolit, a k tomu
# anonymizace. Je to nejhorsi pripad: spravce otevrel kazdou stranku,
# ale jmena ani adresy videt nemaji byt.
for oblast in pristup.OBLASTI:
    db.set_setting(pristup.klic_oblasti(pristup.DIVAK, oblast), "1")
db.set_setting(f"{pristup.DIVAK}_anonymizace", "1")
db.forget_settings()

prosakuje = []
for cesta in sorted(set(cesty)):
    odpoved = k_divak.get(cesta, follow_redirects=False)
    text = odpoved.text if "text" in odpoved.headers.get("content-type", "") \
        or "json" in odpoved.headers.get("content-type", "") else ""
    if CIZI_JMENO in text or CIZI_ID in text:
        prosakuje.append(f"{cesta} ({odpoved.status_code})")

check(not prosakuje,
      f"prošlo {len(set(cesty))} adres, cizí divák uniká na: {prosakuje}")

# A totez s adresou: IP cizich lidi nema divak videt ani na Siti.
prosakuje_ip = []
for cesta in sorted(set(cesty)):
    odpoved = k_divak.get(cesta, follow_redirects=False)
    if "10.0.0.5" in odpoved.text:
        prosakuje_ip.append(f"{cesta} ({odpoved.status_code})")
check(not prosakuje_ip, f"a cizí IP adresa uniká na: {prosakuje_ip}")

# Zpatky na vychozi stav, at dalsi kontroly meri to, co maji.
for oblast in pristup.OBLASTI:
    db.set_setting(pristup.klic_oblasti(pristup.DIVAK, oblast),
                   "1" if oblast.vychozi else "0")
db.forget_settings()

print()
print("--- historie je jen jeho, ať si do adresy napíše cokoliv ---")
text = k_divak.get(f"/history?user_id={CIZI_ID}").text
check(CIZI_JMENO not in text and CIZI_ID not in text,
      "vnucený filtr přebije adresu")
check("Mirek" in text, "a svoje záznamy vidí")

print()
print("--- co divák uvidí, si nastavuje správce ---")


def nastav(**volby):
    for klic, hodnota in volby.items():
        db.set_setting(klic, "1" if hodnota else "0")
    db.forget_settings()


nastav(**{pristup.klic_oblasti(pristup.DIVAK, pristup.PODLE_KLICE["uzivatele"]): True})
check(k_divak.get("/users").status_code == 200,
      "zapnutý seznam diváků se otevře")
nastav(**{pristup.klic_oblasti(pristup.DIVAK, pristup.PODLE_KLICE["uzivatele"]): False})
check(k_divak.get("/users").status_code == 403, "vypnutý zase ne")

nastav(**{pristup.klic_oblasti(pristup.DIVAK, pristup.PODLE_KLICE["sit"]): True})
check(k_divak.get("/network").status_code == 200, "totéž platí pro Síť")
nastav(**{pristup.klic_oblasti(pristup.DIVAK, pristup.PODLE_KLICE["sit"]): False})

# Pod-oblast sama nestaci - "kdo to sledoval" bez knihovny nema kde byt.
nastav(**{pristup.klic_oblasti(pristup.DIVAK, pristup.PODLE_KLICE["knihovna"]): False,
          pristup.klic_oblasti(pristup.DIVAK, pristup.PODLE_KLICE["knihovna_kdo"]): True})
check(not pristup.vidi(pristup.DIVAK, "knihovna_kdo"),
      "pod-oblast se bez té nad sebou nezapne")
check(k_divak.get("/library").status_code == 403, "a knihovna je zavřená")
nastav(**{pristup.klic_oblasti(pristup.DIVAK, pristup.PODLE_KLICE["knihovna"]): True})
check(pristup.vidi(pristup.DIVAK, "knihovna_kdo"), "se zapnutou knihovnou už platí")

print()
print("--- anonymizace: čísla místo jmen, pomlčka místo adresy ---")
nastav(**{pristup.klic_oblasti(pristup.DIVAK, pristup.PODLE_KLICE["uzivatele"]): True,
          pristup.klic_oblasti(pristup.DIVAK, pristup.PODLE_KLICE["sit"]): True,
          f"{pristup.DIVAK}_anonymizace": True})
text = k_divak.get("/users").text
check(CIZI_JMENO not in text, "cizí jméno na seznamu diváků není")
check("Divák" in text, "je tam přezdívka")
check("Mirek" in text, "a sebe divák vidí pod svým jménem")
check("10.0.0.5" not in k_divak.get("/network").text,
      "cizí IP adresa se neukáže")
check(pristup.prezdivka(CIZI_ID) == pristup.prezdivka(CIZI_ID),
      "přezdívka je stálá")

nastav(**{f"{pristup.DIVAK}_anonymizace": False})
check(CIZI_JMENO in k_divak.get("/users").text,
      "bez anonymizace se jména ukážou (když to správce chce)")
nastav(**{f"{pristup.DIVAK}_anonymizace": True,
          pristup.klic_oblasti(pristup.DIVAK, pristup.PODLE_KLICE["uzivatele"]): False,
          pristup.klic_oblasti(pristup.DIVAK, pristup.PODLE_KLICE["sit"]): False})

print()
print("--- vypnuté přihlašování nepustí dovnitř nikoho z Jellyfinu ---")
nastav(**{pristup.LOGIN_KLIC: False})
_, stav = prihlas("mirek", "tajne")
check(stav == 401, f"divák se nepřihlásí ({stav})")
check('value="jellyfin"' not in klient().get("/login").text,
      "a tlačítko na přihlašovací stránce vůbec není")
_, stav = prihlas("spravce", "dlouheheslo")
check(stav == 303, "místní účet dovnitř pořád může")
nastav(**{pristup.LOGIN_KLIC: True})

print()
print("--- nastavení se dá uložit ze stránky ---")
k_spravce_ulozeni, _ = prihlas("spravce", "dlouheheslo")
odpoved = k_spravce_ulozeni.post(
    "/settings/divaci",
    data={"jellyfin_login": "on", "vidi_zebricky": "on", "anonymizace": "on"},
    follow_redirects=False)
check(odpoved.headers.get("location", "").endswith("#divaci"),
      "uložení se vrátí ke kartě")
check(pristup.login_zapnuty() and pristup.vidi(pristup.DIVAK, "zebricky"),
      "zaškrtnuté se uložilo")
check(not pristup.vidi(pristup.DIVAK, "uzivatele"),
      "a co zaškrtnuté není, se vypnulo")
stranka = k_spravce_ulozeni.get("/settings?section=jellyfin").text
check('name="vidi_zebricky"' in stranka and 'name="vidi_sit"' in stranka,
      "strom je na stránce celý")
check(stranka.count('form="divaci-form"') >= len(pristup.OBLASTI) + 2,
      "a všechna zaškrtávátka patří k jeho formuláři")
for oblast in pristup.OBLASTI:
    db.set_setting(pristup.klic_oblasti(pristup.DIVAK, oblast),
                   "1" if oblast.vychozi else "0")
db.set_setting(f"{pristup.DIVAK}_anonymizace", "1")
db.forget_settings()

print()
print("--- místní čtenářský účet má svůj vlastní strom ---")
# Ctenarsky ucet zaklada spravce rucne a odjakziva vidi statistiky cele.
# Po aktualizaci se mu proto nesmi nic zmenit - ubrat se da, pribyt ne.
accounts.create("ctenar", "dlouheheslo", is_admin=False)
k_ctenar, stav = prihlas("ctenar", "dlouheheslo")
check(stav == 303, f"čtenář se přihlásí ({stav})")
for cesta in ("/", "/users", "/network", "/languages", "/srovnani",
              f"/users/{CIZI_ID}"):
    kod = k_ctenar.get(cesta, follow_redirects=False).status_code
    check(kod == 200, f"ve výchozím stavu vidí {cesta} ({kod})")
check(k_ctenar.get("/settings", follow_redirects=False).status_code == 403,
      "do Nastavení nesmí - to je věc správce")
check(k_ctenar.post("/settings/connection", data={},
                    follow_redirects=False).status_code == 403,
      "a tím spíš v něm nic nezmění")
check(CIZI_JMENO in k_ctenar.get("/users").text,
      "a jména vidí, jak je zvyklý")

nastav(**{pristup.klic_oblasti(pristup.CTENAR,
                               pristup.PODLE_KLICE["sit"]): False})
check(k_ctenar.get("/network", follow_redirects=False).status_code == 403,
      "odškrtnutá Síť se mu zavře")
check(k_ctenar.get("/users", follow_redirects=False).status_code == 200,
      "zbytek mu zůstane")

nastav(**{f"{pristup.CTENAR}_anonymizace": True})
text = k_ctenar.get("/users").text
check(CIZI_JMENO not in text and "Divák" in text,
      "se zapnutou anonymizací jsou i tady čísla místo jmen")
check("10.0.0.5" not in k_ctenar.get("/history").text,
      "a adresa se neukáže ani v historii")
nastav(**{f"{pristup.CTENAR}_anonymizace": False,
          pristup.klic_oblasti(pristup.CTENAR,
                               pristup.PODLE_KLICE["sit"]): True})

check(pristup.role({"is_admin": 1}) == pristup.SPRAVCE, "role: správce")
check(pristup.role({"jellyfin_user_id": "u-1"}) == pristup.DIVAK, "role: divák")
check(pristup.role({"jellyfin_user_id": None}) == pristup.CTENAR, "role: čtenář")

print()
print("--- a jeho strom je v Nastavení → Účty ---")
k_spravce_ctenari, _ = prihlas("spravce", "dlouheheslo")
stranka = k_spravce_ctenari.get("/settings?section=accounts").text
check('id="ctenari"' in stranka and 'action="/settings/ctenari"' in stranka,
      "karta tam je")
check(stranka.count('form="ctenari-form"') >= len(pristup.OBLASTI) + 1,
      "se všemi zaškrtávátky")
odpoved = k_spravce_ctenari.post("/settings/ctenari",
                                 data={"vidi_knihovna": "on"},
                                 follow_redirects=False)
check(odpoved.headers.get("location", "").endswith("#ctenari"),
      "uložení se vrátí ke kartě")
check(pristup.vidi(pristup.CTENAR, "knihovna")
      and not pristup.vidi(pristup.CTENAR, "sit"),
      "a uložilo se přesně to, co bylo zaškrtnuté")
for oblast in pristup.OBLASTI:
    db.set_setting(pristup.klic_oblasti(pristup.CTENAR, oblast), "1")
db.forget_settings()

print()
print("--- správce vidí dál všechno ---")
k_spravce, _ = prihlas("spravce", "dlouheheslo")
for cesta in ("/", "/users", f"/users/{CIZI_ID}", "/network", "/settings",
              "/languages", "/srovnani"):
    stav = k_spravce.get(cesta, follow_redirects=False).status_code
    check(stav == 200, f"správce smí {cesta} ({stav})")
check(CIZI_JMENO in k_spravce.get("/users").text,
      "a cizí diváky v seznamu má")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
