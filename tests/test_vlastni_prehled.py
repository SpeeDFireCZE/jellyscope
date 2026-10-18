# -*- coding: utf-8 -*-
r"""Vlastní přehled - stránka poskládaná z existujících sekcí.

Zapíná se v Nastavení -> Rozhraní a platí pro celý server, stejně jako
ostatní nastavení. Sestavuje ho správce; ostatní ho vidí.

Na čem to stojí:

* **Sekce je dvojice** - kus šablony a funkce na data, zapsaná v registru
  (jellyscope/sekce.py). Šablonky jsou tytéž, které používají původní
  stránky, takže se obě místa nemůžou rozejít.
* **Počítá se jen to, co je poskládané.** Přehled spočítá deset sekcí
  každému; tady se spustí jen data vybraných.
* **Vypnutý je jako by nebyl** - záložka není v menu a adresa vede zpátky
  na Přehled.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_vlastni_prehled.py
"""
from __future__ import annotations

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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "prehled.db")
os.environ["SECRET_KEY"] = "testovaci-klic"

from jellyscope import accounts, db, demodata, sekce  # noqa: E402

failures = 0


def check(podminka: bool, popis: str, detail: str = "") -> None:
    global failures
    print(f"{'OK    ' if podminka else 'CHYBA '} {popis} {detail}")
    if not podminka:
        failures += 1


def prepni(zapnuto: bool) -> None:
    db.set_setting(sekce.ZAPNUTO, "1" if zapnuto else "0")
    db.forget_settings()


db.init_db()
demodata.seed()
accounts.create("spravce", "dlouheheslo", is_admin=True)
accounts.create("divak", "dlouheheslo", is_admin=False)

print("--- registr sekcí ---")
check(len(sekce.SEZNAM) >= 10, f"sekcí je {len(sekce.SEZNAM)}")
check(len({s.klic for s in sekce.SEZNAM}) == len(sekce.SEZNAM),
      "klíče se neopakují")
for s in sekce.SEZNAM:
    check((PROJECT / "jellyscope" / "templates" / s.sablona).exists(),
          f"šablonka {s.sablona} existuje")

# Název a popis karty se překládají až za běhu (`_(sekce.nazev)`), takže je
# kontrola slovníku ze šablon nevidí - hlídá se to tady.
import json  # noqa: E402

for jazyk in ("en", "es"):
    slovnik = json.loads((PROJECT / "jellyscope" / "translations" / f"{jazyk}.json")
                         .read_text(encoding="utf-8"))
    chybi = [t for s in sekce.SEZNAM for t in (s.nazev, s.popis) if t not in slovnik]
    check(not chybi, f"názvy a popisy karet mají překlad ({jazyk})", f"(chybí: {chybi})")

# Sekce si v jednom kontextu nesmi prepsat hodnoty. Stejny klic je
# v poradku, dokud znamena totez - "Celkem odsledovano" a dlazdice pod
# nim kresli z tehoz souhrnu. Chyba by byla, kdyby pod jednim jmenem
# posilaly ruzne veci: jedna sekce by pak vykreslila cizi cisla.
podle_klice: dict[str, list[tuple[str, object]]] = {}
for s in sekce.SEZNAM:
    for klic, hodnota in s.data(30).items():
        podle_klice.setdefault(klic, []).append((s.klic, hodnota))

nesedi = [klic for klic, zdroje in podle_klice.items()
          if len(zdroje) > 1 and any(h != zdroje[0][1] for _, h in zdroje[1:])]
check(not nesedi, "sdílené klíče znamenají u všech sekcí totéž",
      f"(rozcházejí se: {nesedi})")

print()
print("--- dotazy projdou i na PostgreSQL ---")
# `sloupec IS ?` SQLite bere, PostgreSQL ne - z `IS %s` je pro nej
# syntakticka chyba. A protoze se rozvrzeni cte pri KAZDEM pozadavku
# (kvuli zalozce v menu), neshodilo to jednu stranku, ale celou
# aplikaci. Chytame proto skutecne dotazy, ne jen zdrojak.
from jellyscope import dialect  # noqa: E402

zachycene: list[str] = []
_puvodni_all = db.query_all
_puvodni_connect = db.connect


def _zachyt_all(sql, params=()):
    zachycene.append(sql)
    return _puvodni_all(sql, params)


class _Odposlech:
    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=()):
        zachycene.append(sql)
        return self._conn.execute(sql, params)

    def executemany(self, sql, params):
        zachycene.append(sql)
        return self._conn.executemany(sql, params)

    def __getattr__(self, jmeno):
        return getattr(self._conn, jmeno)


import contextlib  # noqa: E402


@contextlib.contextmanager
def _zachyt_connect(*args, **kwargs):
    with _puvodni_connect(*args, **kwargs) as conn:
        yield _Odposlech(conn)


db.query_all = _zachyt_all
db.connect = _zachyt_connect
try:
    sekce.nacti_rozvrzeni()
    sekce.nacti_rozvrzeni(account_id=1)
    sekce.uloz_rozvrzeni(["kodeky"])
    sekce.uloz_rozvrzeni(["kodeky"], account_id=1)
finally:
    db.query_all = _puvodni_all
    db.connect = _puvodni_connect

nase = [s for s in zachycene if "dashboard_layout" in s]
check(len(nase) >= 4, f"zachyceno {len(nase)} dotazů nad rozvržením")
prelozene = [dialect.translate(s, dialect.POSTGRES) for s in nase]
# Hledá se bez ohledu na velikost písmen - `%s` se `.upper()` změní na
# `%S` a porovnání s "IS %s" by pak nenašlo nic nikdy.
import re as _re  # noqa: E402

VADNE = _re.compile(r"\bIS\s+%s", _re.I)
spatne = [s for s in prelozene if VADNE.search(s)]
check(not spatne, "žádný nekončí jako `IS %s`", f"({spatne})")
check(any(_re.search(r"\bIS\s+NULL", s, _re.I) for s in prelozene),
      "společné rozvržení se ptá na IS NULL")

# Kontrola sama sebe: kdyby hledání přestalo hledat, mlčelo by.
check(bool(VADNE.search(dialect.translate(
    "SELECT 1 FROM t WHERE a IS ?", dialect.POSTGRES))),
    "a starý zápis `IS ?` by opravdu propadl")

sekce.uloz_rozvrzeni([])

print()
print("--- ukládání rozvržení ---")
check(sekce.nacti_rozvrzeni() == [], "nové rozvržení je prázdné")
ulozene = sekce.uloz_rozvrzeni(["nejaktivnejsi_uzivatele", "prave_se_hraje"])
check(ulozene == ["nejaktivnejsi_uzivatele", "prave_se_hraje"],
      "uloží se v zadaném pořadí")
check([s.klic for s in sekce.nacti_rozvrzeni()] == ulozene, "a tak se i načte")

# Do databaze patri jen to, co jde vykreslit. Jinak by stacilo sekci
# z registru odebrat a stranka by spadla na necem, co si tam nekdo dal
# pred pul rokem.
check(sekce.uloz_rozvrzeni(["prave_se_hraje", "neexistuje", "prave_se_hraje"])
      == ["prave_se_hraje"], "neznámé klíče i duplicity se zahodí")

# Nabidka v okne uz neni "zbytek registru": ukazuji se vsechny sekce
# a pouzite se zasednou. Kdyby se vynechavaly, seznam by pri pridavani
# a odebirani poskakoval a clovek by ztracel misto, kde byl. Overuje se
# to na strance nize.

print()
print("--- počítá se jen to, co je poskládané ---")
volani: list[str] = []
puvodni = sekce.SEZNAM


class _Spion:
    """Sekce, která si zapíše, že se na její data někdo zeptal."""

    def __init__(self, klic: str) -> None:
        self.klic = klic

    def __call__(self, obdobi: object) -> dict[str, object]:
        volani.append(self.klic)
        return {f"data_{self.klic}": 1}


spionske = tuple(
    sekce.Sekce(s.klic, s.nazev, s.popis, s.sablona, _Spion(s.klic),
                s.obdobi, s.sirka, s.obal_karta, oblast=s.oblast)
    for s in puvodni)
sekce.data_pro([spionske[0], spionske[2]], 30)
check(volani == [spionske[0].klic, spionske[2].klic],
      f"zeptalo se jen dvou sekcí ({volani})")

print()
print("--- stránka ---")
from fastapi.testclient import TestClient  # noqa: E402

from jellyscope.web import app  # noqa: E402

with TestClient(app) as client:
    client.post("/login", data={"username": "spravce", "password": "dlouheheslo"},
                follow_redirects=False)

    prepni(False)
    odpoved = client.get("/dashboard", follow_redirects=False)
    check(odpoved.status_code == 303 and odpoved.headers.get("location") == "/",
          "vypnutý přehled vede zpátky na Přehled")
    check('href="/dashboard"' not in client.get("/").text, "a v menu není")

    prepni(True)
    sekce.uloz_rozvrzeni([])
    stranka = client.get("/dashboard").text
    check("ještě v něm nic není" in stranka, "zapnutý a prázdný to řekne")
    # Prazdny prehled se ostatnim neukazuje, ale spravce ho vidi - jinak
    # by ho zapnul a nemel kudy dovnitr, aby si ho sestavil.
    check('href="/dashboard"' in client.get("/").text,
          "správce prázdný přehled v menu má - jinak se k němu nedostane")

    sekce.uloz_rozvrzeni(["prave_se_hraje", "nejaktivnejsi_uzivatele"])
    stranka = client.get("/dashboard").text
    check(stranka.count('class="dash-sekce') == 2, "dvě sekce se vykreslí")
    check('href="/dashboard"' in client.get("/").text, "a záložka je v menu")
    check("Upravit přehled" in stranka, "správce má tlačítko úprav")

    # Okno nabizi CELY registr a pouzite sekce zasedne. Kdyby se
    # vynechavaly, seznam by pri pridavani a odebirani poskakoval.
    check(stranka.count("data-pridat=") == len(sekce.SEZNAM),
          f"v nabídce jsou všechny sekce ({stranka.count('data-pridat=')})")
    check(stranka.count("disabled") >= 2,
          "a ty, které v přehledu jsou, jsou zašedlé")

    # Filtr obdobi se ukazuje jen tehdy, kdyz ho aspon jedna sekce
    # pouziva - jinak by nahore stal prepinac, ktery nic nedela.
    sekce.uloz_rozvrzeni(["prave_se_hraje", "kodeky"])
    check("okno-obdobi" not in client.get("/dashboard").text,
          "u sekcí bez období se filtr neukazuje")
    sekce.uloz_rozvrzeni(["prave_se_hraje", "nejaktivnejsi_uzivatele"])
    check("okno-obdobi" in client.get("/dashboard").text,
          "a s obdobím ano")

    # Ulozeni ze stranky
    odpoved = client.post("/dashboard/layout",
                          data={"poradi": "kodeky,rozliseni"},
                          follow_redirects=False)
    check(odpoved.status_code == 303, "uložení přesměruje zpátky")
    check([s.klic for s in sekce.nacti_rozvrzeni()] == ["kodeky", "rozliseni"],
          "a pořadí se opravdu uloží")

    print()
    print("--- každá sekce se vykreslí i sama v přehledu ---")
    # Sablonka vlozena do stranky vidi i to, co si ta stranka
    # naimportovala - treba makro `tile`. Kdyz sekce sedi v prehledu
    # sama, chybejici import ji shodi ("'tile' is undefined") - a presne
    # to se stalo u "Odkud se divaji". Proto se zkousi kazda zvlast.
    for s in sekce.SEZNAM:
        sekce.uloz_rozvrzeni([s.klic])
        odpoved = client.get("/dashboard")
        check(odpoved.status_code == 200, f"sekce {s.klic} sama v přehledu",
              f"(HTTP {odpoved.status_code})")

    # A jeste vsechny najednou v jedne strance - kdyby si dve sekce
    # navzajem prepsaly promennou, tady to praskne.
    sekce.uloz_rozvrzeni([s.klic for s in sekce.SEZNAM])
    vse = client.get("/dashboard")
    check(vse.status_code == 200, "a všechny v jednom přehledu")
    kolik = vse.text.count('class="dash-sekce')
    check(kolik == len(sekce.SEZNAM),
          f"vykreslí se všechny ({kolik} z {len(sekce.SEZNAM)})")

    print()
    print("--- značky šířek jsou na jednom místě ---")
    # Znacky (⅓ ½ 1) potrebuje sablona i JavaScript. Kdyby je mel kazdy
    # svoje, jedna z kopii by se casem rozesla - proto jdou z registru
    # a do prohlizece se vezou v atributu.
    sekce.uloz_rozvrzeni("kodeky:tretina,rozliseni:pul,odkud:cela")
    stranka = client.get("/dashboard").text
    # V popisku klíče šířek, ne značky: „⅓" konzole v cp1250 neumí vypsat
    # a test by spadl na print místo na tom, co hlídá.
    check(all(z in stranka for z in sekce.SIRKY.values()),
          f"všechny značky se vykreslí ({list(sekce.SIRKY)})")
    check("data-sirky=" in stranka, "a JavaScript je dostane ze stránky")
    # Atribut musi byt v apostrofech: `tojson` escapuje `'`, ale ne `"`,
    # takze v uvozovkach by JSON znacku ukoncil uprostred.
    check("data-sirky='" in stranka, "atribut je v apostrofech, ať JSON značku neukončí")

    print()
    print("--- šířka panelu ---")
    # Sirka se uklada k umisteni, ne k sekci: tataz statistika muze byt
    # u jednoho serveru pres celou sirku a u druheho ve tretine.
    client.post("/dashboard/layout",
                data={"poradi": "kodeky:tretina,rozliseni:cela"},
                follow_redirects=False)
    ulozene = sekce.nacti_rozvrzeni()
    check([s.sirka for s in ulozene] == ["tretina", "cela"],
          f"šířky se uloží ({[s.sirka for s in ulozene]})")
    stranka = client.get("/dashboard").text
    check("dash-tretina" in stranka and "dash-cela" in stranka,
          "a promítnou se do stránky")

    # Neznama sirka (jina verze, podvrzeny formular) se ignoruje a plati
    # ta z registru - stranka se kvuli tomu rozbit nesmi.
    client.post("/dashboard/layout", data={"poradi": "kodeky:obrovska"},
                follow_redirects=False)
    check(sekce.nacti_rozvrzeni()[0].sirka == sekce.PODLE_KLICE["kodeky"].sirka,
          "neznámá šířka spadne zpátky na tu z registru")

print()
print("--- kdo smí sestavovat ---")
sekce.uloz_rozvrzeni(["kodeky", "rozliseni"])
with TestClient(app) as divak:
    divak.post("/login", data={"username": "divak", "password": "dlouheheslo"},
               follow_redirects=False)
    stranka = divak.get("/dashboard").text
    check(stranka.count('class="dash-sekce') == 2, "neadmin přehled vidí")
    check("Upravit přehled" not in stranka, "ale tlačítko úprav nemá")
    check(divak.post("/dashboard/layout", data={"poradi": "prave_se_hraje"},
                     follow_redirects=False).status_code in (302, 303, 401, 403),
          "a uložit ho nemůže")
    check([s.klic for s in sekce.nacti_rozvrzeni()] == ["kodeky", "rozliseni"],
          "rozvržení zůstalo, jak bylo")

print()
print("--- zapnutí vede rovnou k sestavení ---")
prepni(False)
sekce.uloz_rozvrzeni([])
with TestClient(app) as client:
    client.post("/login", data={"username": "spravce", "password": "dlouheheslo"},
                follow_redirects=False)
    odpoved = client.post("/settings/interface",
                          data={"ui_max_streams": "10", "ui_max_viewers": "10",
                                "ui_map_zoom": "click", "ui_skin": "novy",
                                "ui_cas_presne": "0", "ui_dashboard": "1",
                                "dashboard_nazev": "Rodina"},
                          follow_redirects=False)
    check(odpoved.headers.get("location") == "/dashboard",
          "po zapnutí to hodí rovnou do přehledu")

    # Uz sestaveny prehled uz clovek videt nepotrebuje - zustane
    # v nastavení, kde zrovna je.
    sekce.uloz_rozvrzeni(["kodeky"])
    prepni(False)
    odpoved = client.post("/settings/interface",
                          data={"ui_max_streams": "10", "ui_max_viewers": "10",
                                "ui_map_zoom": "click", "ui_skin": "novy",
                                "ui_cas_presne": "0", "ui_dashboard": "1",
                                "dashboard_nazev": "Rodina"},
                          follow_redirects=False)
    check(odpoved.headers.get("location", "").startswith("/settings"),
          "u sestaveného přehledu zůstane v nastavení")

print()
print("--- prázdný přehled a neadmin ---")
prepni(True)
sekce.uloz_rozvrzeni([])
with TestClient(app) as divak:
    divak.post("/login", data={"username": "divak", "password": "dlouheheslo"},
               follow_redirects=False)
    check('href="/dashboard"' not in divak.get("/").text,
          "kdo ho nesestaví, tomu prázdná záložka v menu nepřekáží")

print()
print("--- přesměrování po přihlášení ---")
prepni(False)
with TestClient(app) as c:
    r = c.post("/login", data={"username": "spravce", "password": "dlouheheslo"},
               follow_redirects=False)
    check(r.headers.get("location") == "/", "vypnutý -> Přehled")

prepni(True)
sekce.uloz_rozvrzeni([])
with TestClient(app) as c:
    r = c.post("/login", data={"username": "spravce", "password": "dlouheheslo"},
               follow_redirects=False)
    check(r.headers.get("location") == "/",
          "zapnutý, ale prázdný -> taky Přehled (prázdná stránka je horší začátek)")

sekce.uloz_rozvrzeni(["prave_se_hraje"])
with TestClient(app) as c:
    r = c.post("/login", data={"username": "spravce", "password": "dlouheheslo"},
               follow_redirects=False)
    check(r.headers.get("location") == "/dashboard", "zapnutý s obsahem -> tam")

print()
print("--- původní stránky kreslí tytéž sekce ---")
# Kdyby extrakce udelala kopii, tohle by proslo i po rozejiti obou mist -
# proto se ptame na obsah, ktery pochazi ze sdilene sablonky.
with TestClient(app) as client:
    client.post("/login", data={"username": "spravce", "password": "dlouheheslo"},
                follow_redirects=False)
    for adresa, nadpisy in (
            ("/", ["Nejaktivnější uživatelé", "Jak server obsah doručuje",
                   "Přehrávače", "Kdy se sleduje"]),
            ("/network", ["Špička po dnech", "Kdo nejvíc streamoval"]),
            ("/library", ["Kodeky", "Rozlišení", "Dynamický rozsah"])):
        text = client.get(adresa).text
        chybi = [n for n in nadpisy if n not in text]
        check(not chybi, f"{adresa} má pořád své karty", f"chybí {chybi}")

print()
print("--- karty ze Zjištění ---")
# Karta = šablonka, kterou vkládá stránka Zjištění i přehled. Na stránce
# se nic nezmění; přehled ji umí vykreslit samotnou.
ZJISTENI = {
    "zj_nevyuzite": "Místo, které nikdo nevyužívá",
    "zj_transcodovane": "Nejčastěji transcodované soubory",
    "zj_duvody": "Proč se transcoduje",
    "zj_rychlost": "Rychlost převodu",
    "zj_kvalita": "Díváš se na to hodně, ale ve slabé kvalitě",
    "zj_velke": "Zabírá hodně, sledováno skoro vůbec",
    "zj_knihovny": "Využití knihoven",
    "zj_duplicity": "Možné duplicity",
    "zj_odlozene": "Začaté a odložené",
}
check(all(sekce.PODLE_KLICE.get(k) is not None and sekce.PODLE_KLICE[k].oblast == "zebricky"
          for k in ZJISTENI),
      "všech 9 karet Zjištění je v registru s oblastí Zjištění a žebříčky")
with TestClient(app) as client:
    client.post("/login", data={"username": "spravce", "password": "dlouheheslo"},
                follow_redirects=False)
    text = client.get("/insights").text
    chybi = [n for n in ZJISTENI.values() if n not in text]
    check(not chybi, "/insights má pořád všech 9 karet", f"chybí {chybi}")
    check('id="okno-tabulka"' in text, "a okno pro celou tabulku")
    prepni(True)
    db.set_setting(sekce.NAZEV_KLIC, "Rodina")
    for klic, nadpis in ZJISTENI.items():
        if klic not in sekce.PODLE_KLICE:
            continue
        sekce.uloz_rozvrzeni([klic])
        odpoved = client.get("/dashboard")
        check(odpoved.status_code == 200 and nadpis in odpoved.text,
              f"karta {klic} se vykreslí sama v přehledu")
    sekce.uloz_rozvrzeni(["zj_transcodovane"])
    check('id="okno-tabulka"' in client.get("/dashboard").text,
          "přehled s tabulkou Zjištění má okno pro celou tabulku")
    sekce.uloz_rozvrzeni(["kodeky"])
    check('id="okno-tabulka"' not in client.get("/dashboard").text,
          "bez takové karty okno nepotřebuje")

print()
print("--- karty Jazyky, Uživatelé, Knihovna, Síť ---")
# Čím se karta v přehledu pozná (nadpis nebo text, který jinde není).
DALSI = {
    "kdo_jazyk": "Kdo v jakém jazyce sleduje",
    "kombinace": "Kombinace stop",
    "chybi_stopa": "Sledujete to, ale stopa chybí",
    "uzivatele_graf": "Sledovanost podle uživatele",
    "uzivatele_tabulka": "<h2>Uživatelé</h2>",
    "knihovny": "lib-grid",
    "sit_cisla": "Průměrný tok jednoho streamu",
    "mapa": "<h2>Mapa</h2>",
}
OBLAST_DALSICH = {"kdo_jazyk": "jazyky", "kombinace": "jazyky", "chybi_stopa": "jazyky",
                  "uzivatele_graf": "uzivatele", "uzivatele_tabulka": "uzivatele",
                  "knihovny": "knihovna", "sit_cisla": "sit", "mapa": "sit"}
check(all(getattr(sekce.PODLE_KLICE.get(k), "oblast", None) == o for k, o in OBLAST_DALSICH.items()),
      "8 dalších karet je v registru se správnou oblastí")
check(len(sekce.SEZNAM) == 41, f"stavitel má 41 karet ({len(sekce.SEZNAM)})")
with TestClient(app) as client:
    client.post("/login", data={"username": "spravce", "password": "dlouheheslo"},
                follow_redirects=False)
    for adresa, nadpisy in (
            ("/languages", ["Kdo v jakém jazyce sleduje", "Kombinace stop"]),
            ("/users", ["Sledovanost podle uživatele", "<h2>Přehled</h2>"]),
            ("/library", ["lib-grid"]),
            ("/network", ["Průměrný tok jednoho streamu", "<h2>Mapa</h2>"])):
        text = client.get(adresa).text
        chybi = [n for n in nadpisy if n not in text]
        check(not chybi, f"{adresa} má pořád své karty", f"chybí {chybi}")
    prepni(True)
    db.set_setting(sekce.NAZEV_KLIC, "Rodina")
    for klic, znak in DALSI.items():
        if klic not in sekce.PODLE_KLICE:
            continue
        sekce.uloz_rozvrzeni([klic])
        odpoved = client.get("/dashboard")
        check(odpoved.status_code == 200 and znak in odpoved.text,
              f"karta {klic} se vykreslí sama v přehledu")
    # Stáhnout GeoIP je akce správce na stránce Síť, ne tlačítko v přehledu.
    sekce.uloz_rozvrzeni(["mapa"])
    check('action="/network/geoip"' not in client.get("/dashboard").text,
          "mapa v přehledu nenabízí stažení GeoIP (je na stránce Síť)")

    # Review Focus 4: všech 41 karet naráz.
    sekce.uloz_rozvrzeni([s.klic for s in sekce.SEZNAM])
    odpoved = client.get("/dashboard")
    vykreslene = re.findall(r'class="dash-sekce[^"]*" data-sekce="(\w+)"', odpoved.text)
    check(odpoved.status_code == 200 and len(vykreslene) == len(sekce.SEZNAM),
          f"přehled se všemi kartami naráz se vykreslí ({len(vykreslene)} z {len(sekce.SEZNAM)})")

print()
print("--- karta v přehledu dostane tatáž data jako na své stránce ---")
# Klíč, pod kterým karta čte data, musí v přehledu znamenat totéž co na
# stránce, odkud karta pochází. Jinak se karta vykreslí, jen s cizími
# čísly: „Celkem za všechny knihovny" dostávala pokrytí jazyků místo
# technických dat a ukazovala samé nuly.
from jellyscope import web_zaklad  # noqa: E402

STRANKA_OBLASTI = {"prehled": "/", "prave_hraje": "/", "knihovna": "/library",
                   "sit": "/network", "jazyky": "/languages", "uzivatele": "/users",
                   "zebricky": "/insights"}
# Karta, jejíž stránka není stránka její oblasti (oblast podle spec).
STRANKA_KARTY = {"nedavno_pridane": "/"}
# Živý tok se mění s každým voláním - nedá se porovnat ani sám se sebou.
MENI_SE_V_CASE = {"zive"}

kontexty: dict[str, dict] = {}
_puvodni_odpoved = web_zaklad.templates.TemplateResponse


def _zachyt_kontext(request, nazev, *args, **kwargs):
    kontext = args[0] if args else kwargs.get("context", {})
    kontexty.setdefault(request.url.path, dict(kontext))
    return _puvodni_odpoved(request, nazev, *args, **kwargs)


web_zaklad.templates.TemplateResponse = _zachyt_kontext
try:
    with TestClient(app) as client:
        client.post("/login", data={"username": "spravce", "password": "dlouheheslo"},
                    follow_redirects=False)
        for adresa in set(STRANKA_OBLASTI.values()):
            client.get(adresa)
finally:
    web_zaklad.templates.TemplateResponse = _puvodni_odpoved

lisi, chybi = [], []
for karta in sekce.SEZNAM:
    stranka = STRANKA_KARTY.get(karta.klic) or STRANKA_OBLASTI[karta.oblast]
    kontext = kontexty.get(stranka, {})
    for klic, hodnota in karta.data(kontext.get("days")).items():
        if klic in MENI_SE_V_CASE:
            continue
        if klic not in kontext:
            chybi.append(f"{karta.klic}.{klic} ({stranka})")
        elif hodnota != kontext[klic]:
            lisi.append(f"{karta.klic}.{klic} ({stranka})")
check(len(kontexty) == len(set(STRANKA_OBLASTI.values())),
      f"zachycený kontext všech stránek ({sorted(kontexty)})")
check(not chybi, "každý klíč karty má i její stránka", f"chybí {chybi}")
check(not lisi, "karta dostává v přehledu tatáž data jako na své stránce", f"liší se {lisi}")

print()
print("--- co potřebuje víc karet, se spočítá jednou ---")
# Graf i tabulka uživatelů berou tutéž tabulku, čtyři jazykové karty tytéž
# barvy jazyků. Přehled s nimi všemi je nemá počítat znovu pro každou kartu.
from jellyscope import langstats, stats  # noqa: E402

volani: dict[str, int] = {"user_table": 0, "colour_map": 0}
puvodni = {"user_table": stats.user_table, "colour_map": langstats.colour_map}


def _pocitej(jmeno):
    def obal(*args, **kwargs):
        volani[jmeno] += 1
        return puvodni[jmeno](*args, **kwargs)
    return obal


stats.user_table = _pocitej("user_table")
langstats.colour_map = _pocitej("colour_map")
try:
    data = sekce.data_pro([sekce.PODLE_KLICE[k] for k in (
        "uzivatele_graf", "uzivatele_tabulka", "pomer_jazyku", "kdo_jazyk",
        "jazyky_knihovny")], 30)
finally:
    stats.user_table = puvodni["user_table"]
    langstats.colour_map = puvodni["colour_map"]
check(volani == {"user_table": 1, "colour_map": 1} and "rows" in data and "by_user" in data,
      f"tabulka uživatelů i barvy jazyků jen jednou ({volani})")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
