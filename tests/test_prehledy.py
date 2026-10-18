# -*- coding: utf-8 -*-
r"""Přehledy: pojmenovaný přehled pro všechny, Můj přehled, práva karet.

Návrh: docs/superpowers/specs/2026-10-16-prehledy-design.md.

* **Přehled pro všechny** (dřív „Vlastní přehled") má název, který zadá
  správce. Ten je v menu i v nadpisu; bez něj přehled zapnout nejde.
  Stávající zapnutý přehled bez názvu dostane „Vlastní přehled".

Spuštění:
    .\.venv\Scripts\python.exe tests\test_prehledy.py
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
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "prehledy.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic-dostatecne-dlouhy"
os.environ["JELLYSCOPE_DEMO"] = "0"

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, db, jellyfin, pristup, sekce, web  # noqa: E402

failures = 0


def check(podminka: bool, popis: str) -> None:
    global failures
    print(f"{'OK    ' if podminka else 'CHYBA '} {popis}")
    if not podminka:
        failures += 1


def prekladu(veta: str) -> list[str]:
    """Věta ve všech jazycích rozhraní (čeština je věta sama)."""
    vysledek = [veta]
    for jazyk in ("en", "es"):
        cesta = PROJECT / "jellyscope" / "translations" / f"{jazyk}.json"
        preklad = json.loads(cesta.read_text(encoding="utf-8")).get(veta)
        if preklad:
            vysledek.append(preklad)
    return vysledek


db.init_db()
accounts.create("spravce", "dlouheheslo", is_admin=True)
accounts.create("ctenar", "ctenarheslo")
db.forget_settings()


def prihlas(jmeno: str, heslo: str) -> TestClient:
    k = TestClient(web.app)
    k.post("/login", data={"username": jmeno, "password": heslo, "zpusob": "mistni"},
           follow_redirects=False)
    return k


spravce = prihlas("spravce", "dlouheheslo")
ctenar = prihlas("ctenar", "ctenarheslo")


def nastav_rozhrani(klient: TestClient, **pole) -> int:
    return klient.post("/settings/interface", data={k: str(v) for k, v in pole.items()},
                       follow_redirects=False).status_code


def odkaz_v_menu(html: str, adresa: str) -> str:
    """Text odkazu v menu (bez značek), nebo prázdno."""
    shoda = re.search(r'<a[^>]*href="' + re.escape(adresa) + r'"[^>]*>(.*?)</a>', html, re.S)
    return re.sub(r"<[^>]+>", "", shoda.group(1)).strip() if shoda else ""


def nazev_nejde(text: str) -> bool:
    try:
        sekce.over_nazev(text)
    except ValueError:
        return True
    return False


print("--- název: kontrola ---")
check(sekce.over_nazev("  Rodinný   přehled ") == "Rodinný přehled",
      "ořízne okraje a slije mezery")
check(nazev_nejde("") and nazev_nejde("   "), "prázdný název nejde")
check(sekce.over_nazev("x" * 40) == "x" * 40 and nazev_nejde("x" * 41),
      "nejvýš 40 znaků")
check(sekce.over_nazev("Ž" * 40) == "Ž" * 40, "počítají se znaky, ne bajty")
zakazane = prekladu("Přehled") + prekladu("Můj přehled") + ["přehled", "MŮJ PŘEHLED"]
check(len(zakazane) >= 7, f"zakázané názvy ve všech jazycích ({zakazane})")
check(all(nazev_nejde(z) for z in zakazane),
      "název se nesmí plést s Přehledem ani s Mým přehledem v žádném jazyce")

print("--- název: Nastavení -> Rozhraní ---")
nastav_rozhrani(spravce, ui_dashboard=1, dashboard_nazev="")
db.forget_settings()
check(not sekce.je_zapnuty(), "bez názvu se přehled nezapne")
check("potřebuje název" in spravce.get("/settings?section=interface").text,
      "a hláška řekne proč")
check(nastav_rozhrani(spravce, ui_dashboard=1, dashboard_nazev="Rodina") == 303,
      "s názvem projde")
db.forget_settings()
check(sekce.je_zapnuty() and sekce.nazev() == "Rodina", "zapnutý a s názvem Rodina")
check('value="Rodina"' in spravce.get("/settings?section=interface").text,
      "název je vyplněný ve formuláři")

nastav_rozhrani(spravce, ui_dashboard=1, dashboard_nazev="Přehled")
db.forget_settings()
check(sekce.je_zapnuty() and sekce.nazev() == "Rodina",
      "neplatný název u zapnutého přehledu: zůstane starý")

# Vypnutý přehled si starý název pamatuje. Zapnout ho s neplatným ale nesmí:
# hláška říká „nejde" a přehled nemá potichu naskočit pod starým jménem.
nastav_rozhrani(spravce, ui_dashboard=0, dashboard_nazev="Rodina")
for neplatny in ("Přehled", ""):
    nastav_rozhrani(spravce, ui_dashboard=1, dashboard_nazev=neplatny)
    db.forget_settings()
    check(not sekce.je_zapnuty() and sekce.nazev() == "Rodina",
          f"zapnutí s neplatným názvem ({neplatny!r}): zůstane vypnutý")
nastav_rozhrani(spravce, ui_dashboard=1, dashboard_nazev="Rodina")
db.forget_settings()

historie = spravce.get("/history").text
check(odkaz_v_menu(historie, "/dashboard") == "Rodina", "v menu je název, ne „Vlastní přehled“")
stranka = spravce.get("/dashboard").text
check("<h1>Rodina</h1>" in stranka, "nadpis stránky je název")
check(re.search(r"<title>[^<]*Rodina", stranka) is not None, "titulek okna taky")
ucty = spravce.get("/settings?section=accounts").text
check("Vlastní přehled („Rodina“)" in ucty, "strom práv ukáže, kterou stránku povoluje")

nastav_rozhrani(spravce, ui_dashboard=1, dashboard_nazev="<script>x</script>")
db.forget_settings()
historie = spravce.get("/history").text
stranka = spravce.get("/dashboard").text
check(sekce.nazev() == "<script>x</script>", "název se uloží, jak je")
check("<script>x</script>" not in historie and "&lt;script&gt;x&lt;/script&gt;" in historie
      and "<h1>&lt;script&gt;x&lt;/script&gt;</h1>" in stranka,
      "v menu i nadpisu je escapovaný")
nastav_rozhrani(spravce, ui_dashboard=1, dashboard_nazev="Rodina")

print("--- název: stávající instalace ---")
db.set_setting(sekce.NAZEV_KLIC, "")
db.set_setting(sekce.ZAPNUTO, "1")
db.forget_settings()
check(sekce.doplnit_nazev() is True and sekce.nazev() == "Vlastní přehled",
      "zapnutý přehled bez názvu dostane „Vlastní přehled“")
check(sekce.doplnit_nazev() is False and sekce.nazev() == "Vlastní přehled",
      "druhý start už nic nemění")
db.set_setting(sekce.NAZEV_KLIC, "Kino")
db.forget_settings()
check(sekce.doplnit_nazev() is False and sekce.nazev() == "Kino", "přejmenovaný se nepřepíše")
db.set_setting(sekce.NAZEV_KLIC, "")
db.set_setting(sekce.ZAPNUTO, "0")
db.forget_settings()
check(sekce.doplnit_nazev() is False and sekce.nazev() == "",
      "vypnutý přehled zůstane bez názvu")


print("--- práva karet ---")
# Oblast karty = oblast stránky, ze které karta pochází (tabulka ve spec).
OCEKAVANE_OBLASTI = {
    "prehled": {"souhrn", "dlazdice", "sledovanost_po_dnech", "nejsledovanejsi",
                "nejaktivnejsi_uzivatele", "doruceni", "prehravace", "kdy_se_sleduje"},
    "prave_hraje": {"prave_se_hraje"},
    "knihovna": {"nedavno_pridane", "knihovna_celkem", "rust_knihovny", "kodeky",
                 "rozliseni", "dynamicky_rozsah"},
    "sit": {"zivy_tok", "spicky_po_dnech", "kdo_streamoval", "podle_prehravace", "odkud"},
    "jazyky": {"pomer_jazyku", "dabing", "titulky", "jazyky_knihovny"},
}
neplatne = [s.klic for s in sekce.SEZNAM if getattr(s, "oblast", None) not in pristup.PODLE_KLICE]
check(not neplatne, f"každá karta má platnou oblast práv ({neplatne})")
rozdily = [(klic, oblast, getattr(sekce.PODLE_KLICE.get(klic), "oblast", None))
           for oblast, klice in OCEKAVANE_OBLASTI.items() for klic in klice
           if getattr(sekce.PODLE_KLICE.get(klic), "oblast", None) != oblast]
check(not rozdily, f"oblasti 24 stávajících karet podle spec ({rozdily})")

KLIC_SITE = pristup.klic_oblasti(pristup.CTENAR, pristup.PODLE_KLICE["sit"])
SOUHRN, ODKUD = sekce.PODLE_KLICE["souhrn"], sekce.PODLE_KLICE["odkud"]


def ucet(jmeno: str) -> dict:
    db.forget_settings()
    return accounts.get_by_name(jmeno) or {}


db.set_setting(KLIC_SITE, "0")
check(sekce.smi(ODKUD, ucet("spravce")) is True, "správce smí každou kartu")
check(sekce.smi(ODKUD, ucet("ctenar")) is False and sekce.smi(SOUHRN, ucet("ctenar")) is True,
      "čtenář bez oblasti Síť nesmí kartu ze Sítě, kartu z Přehledu ano")
check(sekce.povolene([SOUHRN, ODKUD], ucet("ctenar")) == [SOUHRN],
      "povolene() nechá jen karty, které účet smí")

with db.connect() as conn:
    conn.execute("INSERT INTO users (id, name, is_administrator, is_disabled, synced_at)"
                 " VALUES ('u-karel', 'Karel', 0, 0, ?)", (db.utcnow(),))
    conn.execute("INSERT INTO playback (session_key, user_id, user_name, item_id, item_name,"
                 " started_at, last_seen_at, watched_seconds, remote_address, is_active)"
                 " VALUES ('s1', 'u-karel', 'Karel', 'i-1', 'Titul', ?, ?, 1800, '10.0.0.5', 0)",
                 (db.utcnow(), db.utcnow()))
    conn.commit()
db.set_setting(sekce.ZAPNUTO, "1")
db.set_setting(sekce.NAZEV_KLIC, "Rodina")
sekce.uloz_rozvrzeni(["souhrn", "odkud"])


def karty_na_strance(html: str) -> list[str]:
    return re.findall(r'class="dash-sekce[^"]*" data-sekce="(\w+)"', html)


# Data zakázané karty se ani nespočítají.
volani_odkud: list[int] = []
puvodni_odkud = sekce.stats.odkud_se_divaji


def sledovane_odkud(*args, **kwargs):
    volani_odkud.append(1)
    return puvodni_odkud(*args, **kwargs)


sekce.stats.odkud_se_divaji = sledovane_odkud
stranka = ctenar.get("/dashboard").text
sekce.stats.odkud_se_divaji = puvodni_odkud
check(karty_na_strance(stranka) == ["souhrn"],
      f"čtenář bez Sítě: na přehledu pro všechny jen povolené karty ({karty_na_strance(stranka)})")
check("10.0.0.5" not in stranka and "Odkud se dívají" not in stranka,
      "ani kus dat zakázané karty (žádná IP adresa)")
check(not volani_odkud, "data zakázané karty se nepočítají")
check("data-pridat=" not in stranka, "čtenář okno s kartami nemá (jako dnes)")

stranka_spravce = spravce.get("/dashboard").text
check(karty_na_strance(stranka_spravce) == ["souhrn", "odkud"]
      and all(f'data-pridat="{s.klic}"' in stranka_spravce for s in sekce.SEZNAM),
      "správce vidí obě karty a okno nabízí všechny")

db.set_setting(KLIC_SITE, "1")
stranka = ctenar.get("/dashboard").text
check(karty_na_strance(stranka) == ["souhrn", "odkud"] and "10.0.0.5" in stranka,
      "po vrácení oblasti Síť je karta i s adresou zpět")

# Kdo v přehledu nesmí ani jednu kartu, nemá ho v menu - prázdná stránka
# s „ještě v něm nic není" by lhala. Správce ho v menu má vždycky.
sekce.uloz_rozvrzeni(["odkud"])
db.set_setting(KLIC_SITE, "0")
check(odkaz_v_menu(ctenar.get("/history").text, "/dashboard") == "",
      "čtenář, který v přehledu nesmí nic, ho v menu nemá")
odpoved = ctenar.get("/dashboard", follow_redirects=False)
check(odpoved.status_code == 303 and odpoved.headers.get("location") == "/",
      "a přímá adresa ho vrátí na Přehled, ne na „ještě v něm nic není“")
check(odkaz_v_menu(spravce.get("/history").text, "/dashboard") == "Rodina",
      "správce ano")


# Po přihlášení se jde na přehled, jen když v něm účet něco uvidí. Jinak
# by přistál na prázdné stránce, kterou v menu ani nemá.
def kam_po_prihlaseni(jmeno: str, heslo: str) -> str:
    odpoved = TestClient(web.app).post(
        "/login", data={"username": jmeno, "password": heslo, "zpusob": "mistni"},
        follow_redirects=False)
    return odpoved.headers.get("location", "")


check(kam_po_prihlaseni("ctenar", "ctenarheslo") == "/",
      "čtenář, který v přehledu nesmí nic, po přihlášení přistane na Přehledu")
check(kam_po_prihlaseni("spravce", "dlouheheslo") == "/dashboard",
      "správce po přihlášení na přehled pro všechny")
db.set_setting(KLIC_SITE, "1")
check(odkaz_v_menu(ctenar.get("/history").text, "/dashboard") == "Rodina",
      "s oblastí Síť ho čtenář v menu zase má")
check(kam_po_prihlaseni("ctenar", "ctenarheslo") == "/dashboard",
      "s oblastí Síť po přihlášení zase na přehled")


# Přistát se má jen tam, kam účet smí - stránka „sem nemáš přístup" hned
# po zadání hesla je nejhorší možné uvítání.
def pristani(jmeno: str, heslo: str) -> tuple[str, int]:
    """Kde člověk po přihlášení skončí (i po přesměrováních) a s jakým kódem."""
    odpoved = TestClient(web.app).post(
        "/login", data={"username": jmeno, "password": heslo, "zpusob": "mistni"})
    return odpoved.url.path, odpoved.status_code


KLIC_DASHBOARDU = pristup.klic_oblasti(pristup.CTENAR, pristup.PODLE_KLICE["dashboard"])
KLIC_PREHLEDU = pristup.klic_oblasti(pristup.CTENAR, pristup.PODLE_KLICE["prehled"])
db.set_setting(KLIC_DASHBOARDU, "0")
check(pristani("ctenar", "ctenarheslo") == ("/", 200),
      "čtenář bez oblasti přehledu pro všechny přistane na Přehledu, ne na 403")
db.set_setting(KLIC_PREHLEDU, "0")
kam, stav = pristani("ctenar", "ctenarheslo")
check(kam not in ("/", "/dashboard") and stav == 200,
      f"bez Přehledu na první stránce, kterou smí ({kam} {stav})")
db.set_setting(KLIC_DASHBOARDU, "1")
db.set_setting(KLIC_PREHLEDU, "1")
check(pristani("ctenar", "ctenarheslo") == ("/dashboard", 200),
      "s oběma oblastmi zase na přehled pro všechny")
sekce.uloz_rozvrzeni(["souhrn", "odkud"])


print("--- Můj přehled ---")
ID_SPRAVCE, ID_CTENARE = ucet("spravce")["id"], ucet("ctenar")["id"]


def karta_mujprehled(html: str) -> str:
    """Text tlačítka na kartě Můj přehled na stránce Můj účet, nebo prázdno."""
    karta = re.search(r'id="muj-prehled".*?</div>\s*</div>', html, re.S)
    tlacitko = re.search(r'<a[^>]*href="/account/prehled"[^>]*>(.*?)</a>', karta.group(0), re.S) \
        if karta else None
    return re.sub(r"<[^>]+>", "", tlacitko.group(1)).strip() if tlacitko else ""


stranka = ctenar.get("/account/prehled")
check(stranka.status_code == 200 and "Můj přehled je zatím prázdný." in stranka.text
      and 'data-okno="okno-rozvrzeni"' in stranka.text,
      "prázdný Můj přehled: výzva a tlačítko pro přidání karet")
# Na počítači je plocha mřížka o šesti sloupcích. Výzva bez vlastního
# pravidla zabrala jeden z nich - úzká dlaždice s textem po slovech.
pravidlo = re.search(r"\.dash-vyzva\s*\{([^}]*)\}",
                     (PROJECT / "jellyscope" / "static" / "style.css").read_text(encoding="utf-8"))
check(bool(pravidlo) and "grid-column: 1 / -1" in pravidlo.group(1),
      "výzva prázdného přehledu je přes celou šířku mřížky")
check(sekce.nacti_rozvrzeni(ID_CTENARE) == [],
      "prázdný zůstane prázdný - nepůjčí si rozvržení přehledu pro všechny")
check(odkaz_v_menu(ctenar.get("/history").text, "/account/prehled") == "",
      "prázdný Můj přehled v menu není")
check(karta_mujprehled(ctenar.get("/account").text) == "Poskládat",
      "na Mém účtu karta s tlačítkem Poskládat")

ulozeni = ctenar.post("/account/prehled", data={"poradi": "souhrn:cela,kodeky:pul"},
                      follow_redirects=False)
check(ulozeni.status_code == 303, "čtenář si Můj přehled uloží")
check(karty_na_strance(ctenar.get("/account/prehled").text) == ["souhrn", "kodeky"],
      "a vidí v něm své karty")
check(odkaz_v_menu(ctenar.get("/history").text, "/account/prehled") == "Můj přehled",
      "s kartami je v menu")
check(karta_mujprehled(ctenar.get("/account").text) == "Otevřít", "na Mém účtu tlačítko Otevřít")
check(karty_na_strance(ctenar.get("/dashboard").text) == ["souhrn", "odkud"],
      "přehled pro všechny se tím nezměnil")

# Review Focus 1: cizí Můj přehled jde jen z relace, nikdy z adresy nebo formuláře.
sekce.uloz_rozvrzeni(["dlazdice"], ID_SPRAVCE)
ctenar.post("/account/prehled", data={"poradi": "kodeky", "account_id": str(ID_SPRAVCE)},
            follow_redirects=False)
check([s.klic for s in sekce.nacti_rozvrzeni(ID_SPRAVCE)] == ["dlazdice"]
      and [s.klic for s in sekce.nacti_rozvrzeni(ID_CTENARE)] == ["kodeky"],
      "account_id ve formuláři se ignoruje: změní se jen vlastní")
check(karty_na_strance(ctenar.get(f"/account/prehled?account_id={ID_SPRAVCE}").text) == ["kodeky"],
      "account_id v adrese se ignoruje: každý vidí svůj")

# Divák z Jellyfinu smí uložit Můj přehled, jiné POSTy dál ne.
def jellyfin_server(zadost: httpx.Request) -> httpx.Response:
    telo = json.loads(zadost.content or b"{}")
    if (telo.get("Username"), telo.get("Pw")) == ("mirek", "tajne"):
        return httpx.Response(200, json={"User": {
            "Id": "u-mirek", "Name": "mirek", "Policy": {"IsAdministrator": False}}})
    return httpx.Response(401, json={})


async def podvrzeny_klient(self):
    self._client = httpx.AsyncClient(transport=httpx.MockTransport(jellyfin_server),
                                     base_url="http://jellyfin.test")
    return self


jellyfin.JellyfinClient.__aenter__ = podvrzeny_klient
db.set_setting(pristup.LOGIN_KLIC, "1")
divak = TestClient(web.app)
divak.post("/login", data={"username": "mirek", "password": "tajne", "zpusob": "jellyfin"},
           follow_redirects=False)
check(divak.post("/account/prehled", data={"poradi": "kodeky"},
                 follow_redirects=False).status_code == 303,
      "divák z Jellyfinu si Můj přehled uloží (303)")
check(divak.post("/dashboard/layout", data={"poradi": "kodeky"},
                 follow_redirects=False).status_code == 403,
      "rozvržení pro všechny dál nesmí (403)")

# Review Focus 2: odebrané právo - hláška místo dat, žádná IP adresa.
ctenar.post("/account/prehled", data={"poradi": "souhrn,odkud"}, follow_redirects=False)
db.set_setting(KLIC_SITE, "0")
stranka = ctenar.get("/account/prehled").text
check("Nemáš oprávnění k oblasti" in stranka and "Síť a adresy" in stranka
      and "Odkud se dívají" in stranka,
      "karta mimo práva: hláška s názvem karty i oblasti")
check("10.0.0.5" not in stranka, "a ani kus jejích dat")
check(karty_na_strance(stranka) == ["souhrn", "odkud"], "karta zůstává na svém místě")
check('data-pridat="odkud"' not in stranka
      and re.search(r'class="dash-dlazdice[^"]*"\s+data-sekce="odkud"', stranka) is not None,
      "okno ji nenabízí, ale v rozvržení ji má, takže jde odebrat")
db.set_setting(KLIC_SITE, "1")
stranka = ctenar.get("/account/prehled").text
check("10.0.0.5" in stranka and "Nemáš oprávnění k oblasti" not in stranka,
      "po vrácení oblasti Síť je karta zpět s daty")

# Podvrzenym formularem si ucet kartu bez prava neprida; tu, kterou uz
# v prehledu ma, ale prenaskladanim neztrati.
sekce.uloz_rozvrzeni(["souhrn"], ID_CTENARE)
db.set_setting(KLIC_SITE, "0")
ctenar.post("/account/prehled", data={"poradi": "souhrn,odkud"}, follow_redirects=False)
check([s.klic for s in sekce.nacti_rozvrzeni(ID_CTENARE)] == ["souhrn"],
      "kartu bez práva si podvrženým formulářem nepřidá")
sekce.uloz_rozvrzeni(["souhrn", "odkud"], ID_CTENARE)
ctenar.post("/account/prehled", data={"poradi": "odkud,souhrn"}, follow_redirects=False)
check([s.klic for s in sekce.nacti_rozvrzeni(ID_CTENARE)] == ["odkud", "souhrn"],
      "kartu, kterou už má, přeskládáním neztratí")
db.set_setting(KLIC_SITE, "1")

docasny = accounts.create("docasny", "docasneheslo")
sekce.uloz_rozvrzeni(["kodeky"], docasny)
accounts.delete(docasny)
zbytek = db.query_one("SELECT COUNT(*) AS n FROM dashboard_layout WHERE account_id = ?", (docasny,))
check(int(zbytek["n"]) == 0, "smazaný účet vezme svůj Můj přehled s sebou")

print()
print("--- stažení GeoIP jen pro správce ---")
# POST /network/geoip smí jen správce. Tlačítko u kohokoliv jiného by
# vedlo na „sem nemáš přístup".
check('action="/network/geoip"' in spravce.get("/network").text,
      "správce na stránce Síť tlačítko pro stažení GeoIP má")
check('action="/network/geoip"' not in ctenar.get("/network").text,
      "čtenář ho nemá")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
