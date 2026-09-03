# -*- coding: utf-8 -*-
"""Stránka Srovnání: dvě libovolná období vedle sebe.

Nezaměňovat s `test_srovnani_obdobi.py` - ten hlídá „oproti
předchozímu období" na Přehledu, kde si druhé okno člověk nevybírá.

„Oproti minulému období" umí Přehled dávno, jenže minulé období si člověk
nevybírá — je to okno těsně před tím zvoleným. Na otázku „byl srpen lepší
než prosinec?" to neodpoví.

Čtyři místa, kde se dá snadno zamlčet chyba, a proto se měří:

* **Různě dlouhá období.** V součtu vyhraje delší období skoro vždycky,
  takže vedle součtu musí stát denní průměr — a stránka musí říct, že se
  délky liší.
* **Překryv.** Když mají období společný kus, je část dat v obou
  sloupcích a rozdíl mezi nimi neměří změnu.
* **Procenta.** Rozdíl dvou procentuálních údajů je v procentních bodech.
  „Z 12 % na 18 %" je +6 bodů; +50 % je formálně správně a matoucí.
* **Doručení z importu.** Playback Reporting píše „Transcode (v:h264
  a:direct)". Při shodě na přesný název by přepočet z importu v srovnání
  chyběl — sečíst se to musí podle role.

Spuštění:
    .\\.venv\\Scripts\\python.exe tests\\test_srovnani_obdobi.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "dve_obdobi.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYSCOPE_DEMO"] = "0"

from jellyscope import db, stats  # noqa: E402

failures = 0


def check(podminka: bool, popis: str, detail: str = "") -> None:
    global failures
    print(f"{'OK    ' if podminka else 'CHYBA '} {popis} {detail}")
    if not podminka:
        failures += 1


TED = datetime.now(timezone.utc).replace(tzinfo=None)
poradi = 0


def prehrani(pred_dny: float, sekund: int, uzivatel: str = "Petr",
             titul: str = "Duna", metoda: str = "DirectPlay") -> None:
    global poradi
    poradi += 1
    kdy = TED - timedelta(days=pred_dny)
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO playback (session_key, user_id, user_name, item_id,
                                     item_name, item_type, started_at,
                                     last_seen_at, watched_seconds,
                                     play_method, is_active)
               VALUES (?,?,?,?,?, 'Movie', ?, ?, ?, ?, 0)""",
            (f"s{poradi}", "u-" + uzivatel, uzivatel, "film-" + titul, titul,
             kdy.strftime(db.TIME_FORMAT), kdy.strftime(db.TIME_FORMAT),
             sekund, metoda))
        conn.commit()


db.init_db()


def obdobi(od_pred: float, do_pred: float) -> stats.Obdobi:
    """Okno mezi „před X dny" a „před Y dny", v UTC - meze bez zaokrouhlení."""
    od = (TED - timedelta(days=od_pred)).strftime(db.TIME_FORMAT)
    do = (TED - timedelta(days=do_pred)).strftime(db.TIME_FORMAT)
    return stats.Obdobi(od=od, do=do, dny=max(1, round(od_pred - do_pred)),
                        relativni=False, od_mistni=od, do_mistni=do)


# Dve nesouvisejici okna: A = pred 1-10 dny, B = pred 21-30 dny.
A = obdobi(10, 1)
B = obdobi(30, 21)

# A: 10 hodin celkem. B: 5 hodin. Cisla jsou zamerne kulata, aby se
# na nich poznalo i drobne posunuti.
for den in (2, 4, 6, 8, 9):
    prehrani(den, 2 * 3600)                      # 5 x 2 h = 10 h
for den in (22, 24, 26):
    prehrani(den, int(5 / 3 * 3600), uzivatel="Jana", titul="Alien")  # 5 h

print("--- co se změřilo, to se srovná ---")
v = stats.srovnani(A, B)
sledovani = {r["popisek"]: r for r in v["skupiny"][0]["radky"]}

odsledovano = sledovani["Odsledováno"]
check(round(odsledovano["a"], 2) == 10.0, f"období A má 10 h ({odsledovano['a']:.2f})")
check(round(odsledovano["b"], 2) == 5.0, f"období B má 5 h ({odsledovano['b']:.2f})")
check(round(odsledovano["rozdil"], 2) == 5.0,
      f"rozdíl je +5 h ({odsledovano['rozdil']:.2f})")
check(round(odsledovano["relativni"]) == 100,
      f"a relativně dvojnásobek ({odsledovano['relativni']:.0f} %)")

check(sledovani["Spuštění"]["a"] == 5 and sledovani["Spuštění"]["b"] == 3,
      "spuštění sedí (5 vs 3)")
check(sledovani["Diváci"]["a"] == 1 and sledovani["Diváci"]["b"] == 1,
      "diváci sedí")

# Obe okna jsou devet dnu, takze denni prumer je soucet deleny deveti.
denne = sledovani["Denní průměr"]
check(abs(denne["a"] - 10 / 9) < 0.01, f"denní průměr A ({denne['a']:.3f} h)")
check(abs(denne["b"] - 5 / 9) < 0.01, f"denní průměr B ({denne['b']:.3f} h)")

print()
print("--- různě dlouhá období se poznají ---")
check(v["stejne_dlouha"], "dvě devítidenní okna jsou stejně dlouhá")

dlouhe = obdobi(40, 1)      # 39 dnu
nerovne = stats.srovnani(dlouhe, B)
check(not nerovne["stejne_dlouha"], "39 dnů vs 9 dnů se za stejně dlouhá nevydává")
check(round(nerovne["a"]["dnu"]) == 39 and round(nerovne["b"]["dnu"]) == 9,
      f"a délky se hlásí ({nerovne['a']['dnu']:.0f} vs {nerovne['b']['dnu']:.0f} dnů)")

# Denni prumer je jedine cislo, ktere ma u ruzne dlouhych oken smysl:
# soucet A je vetsi, prumer mensi.
sled_nerovne = {r["popisek"]: r for r in nerovne["skupiny"][0]["radky"]}
check(sled_nerovne["Odsledováno"]["rozdil"] > 0, "v součtu vede delší období")
check(sled_nerovne["Denní průměr"]["rozdil"] < 0,
      "ale v denním průměru kratší - proto tam ten řádek je")

print()
print("--- překryv se neschová ---")
check(not v["prekryv"], "nesousedící okna se nepřekrývají")
check(stats.srovnani(A, obdobi(12, 5))["prekryv"], "překryv se pozná")
check(stats.srovnani(A, A)["prekryv"], "a tytéž okno se sebou samým taky")

print()
print("--- procenta se počítají v bodech, ne v procentech z procenta ---")
# Do B pridame prepocet, aby podil transcode nebyl v obou oknech nula.
prehrani(23, 3600, uzivatel="Jana", titul="Alien",
         metoda="Transcode (v:h264 a:direct)")
v2 = stats.srovnani(A, B)
doruceni = {r["popisek"]: r for r in v2["skupiny"][1]["radky"]}

podil = doruceni["Podíl transcode"]
check(podil["relativni"] is None, "u procentuálního údaje se relativní změna nedodává")
check(abs(podil["rozdil"] - (podil["a"] - podil["b"])) < 0.001,
      f"rozdíl je prostý rozdíl v bodech ({podil['rozdil']:.1f})")
check(podil["b"] > 0, f"a v B se transcode opravdu započítal ({podil['b']:.1f} %)")

# Past: importovana historie ma "Transcode (v:h264 a:direct)". Kdyby se
# scitalo podle presneho nazvu, byl by tenhle radek nula.
check(doruceni["Transcode"]["b"] > 0,
      f"přepočet z importu se počítá jako transcode ({doruceni['Transcode']['b']:.2f} h)")
check(doruceni["Přímé přehrávání"]["a"] > 0, "a přímé přehrávání zvlášť")

print()
print("--- jazyky ---")
# V A se divalo cesky, v B anglicky. Prave tohle ma srovnani ukazat.
prehrani(3, 4 * 3600, uzivatel="Petr", titul="Duna")
prehrani(25, 4 * 3600, uzivatel="Jana", titul="Alien")
with db.connect() as conn:
    conn.execute("UPDATE playback SET audio_language = 'cs'"
                 " WHERE started_at >= ? AND started_at < ?", (A.od, A.do))
    conn.execute("UPDATE playback SET audio_language = 'en'"
                 " WHERE started_at >= ? AND started_at < ?", (B.od, B.do))
    conn.commit()

v3 = stats.srovnani(A, B)
jazyky = next((s for s in v3["skupiny"]
               if s.get("s_podilem")), None)
check(jazyky is not None, "skupina jazyků je v srovnání")
podle_jazyka = {r["popisek"]: r for r in (jazyky or {"radky": []})["radky"]}
check(len(podle_jazyka) >= 2,
      f"a jsou v ní oba jazyky ({list(podle_jazyka)})")

from jellyscope import languages  # noqa: E402

cestina = podle_jazyka.get(languages.display("cs"))
anglictina = podle_jazyka.get(languages.display("en"))
check(cestina is not None and anglictina is not None,
      f"čeština i angličtina se našly ({list(podle_jazyka)})")
if cestina and anglictina:
    check(cestina["a"] > 0 and cestina["b"] == 0,
          f"česky se dívalo jen v A ({cestina['a']:.1f} vs {cestina['b']:.1f} h)")
    check(anglictina["b"] > 0 and anglictina["a"] == 0,
          f"anglicky jen v B ({anglictina['a']:.1f} vs {anglictina['b']:.1f} h)")
    # Past: pri PRUNIKU obou obdobi by z tabulky vypadl kazdy jazyk, ktery
    # je jen v jednom - tedy prave ta odpoved, kvuli ktere se clovek diva.
    check(cestina["podil_a"] > 0 and cestina["podil_b"] == 0,
          "a podíl se hlásí u obou sloupců")

print()
print("--- knihovna ---")
# Bez snimku se tabulka nekresli: "nepribylo nic" a "nevime" jsou dve
# ruzne veci a zamenit je znamena lhat.
check(not v3["knihovna_zname"], "bez denních snímků se růst knihovny netvrdí")


def snimek(pred_dny: float, polozek: int, velikost: int) -> None:
    den = (TED - timedelta(days=pred_dny)).date().isoformat()
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO library_snapshot (den, polozek, filmu, epizod,"
            " velikost, uhd, hdr, bez_technik, volne_misto, zapsano_v)"
            " VALUES (?,?,?,0,?,0,0,0,NULL,?)"
            " ON CONFLICT (den) DO UPDATE SET polozek = excluded.polozek,"
            " velikost = excluded.velikost",
            (den, polozek, polozek, velikost, db.utcnow()))
        conn.commit()


# A: pribylo 20 polozek a 100 GB. B: 5 polozek a 10 GB.
snimek(9, 100, 1_000 * 1024 ** 3)
snimek(2, 120, 1_100 * 1024 ** 3)
snimek(29, 50, 500 * 1024 ** 3)
snimek(22, 55, 510 * 1024 ** 3)

v4 = stats.srovnani(A, B)
check(v4["knihovna_zname"], "se snímky už se srovnat dá")
knihovna = next((s for s in v4["skupiny"] if s["nadpis"] in ("Knihovna", "Library")), None)
check(knihovna is not None, "a skupina Knihovna je v tabulce")
podle_radku = {r["popisek"]: r for r in (knihovna or {"radky": []})["radky"]}
polozky = next(iter(podle_radku.values()), None)
check(polozky is not None and polozky["a"] == 20 and polozky["b"] == 5,
      f"přírůstek položek sedí ({polozky['a']} vs {polozky['b']})")
data = list(podle_radku.values())[1]
check(round(data["a"] / 1024 ** 3) == 100 and round(data["b"] / 1024 ** 3) == 10,
      f"přírůstek dat taky ({data['a'] / 1024 ** 3:.0f} vs {data['b'] / 1024 ** 3:.0f} GB)")

print()
print("--- prázdno se nevydává za srovnání ---")
prazdno = stats.srovnani(obdobi(400, 380), obdobi(370, 360))
check(not prazdno["ma_data"], "dvě prázdná období nemají co srovnávat")
check(v["ma_data"], "a období s daty ano")

print()
print("--- stránka ---")
from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts  # noqa: E402
from jellyscope.web import app  # noqa: E402

accounts.create("spravce", "dlouheheslo", is_admin=True)
with TestClient(app) as client:
    client.post("/login", data={"username": "spravce", "password": "dlouheheslo"},
                follow_redirects=False)
    stranka = client.get("/srovnani")
    check(stranka.status_code == 200, "Srovnání se načte")
    html = stranka.text
    check('name="a_od"' in html and 'name="b_od"' in html,
          "obě období mají v adrese vlastní předponu")
    check('id="okno-obdobi-a"' in html and 'id="okno-obdobi-b"' in html,
          "a každé své okno s vlastním rozmezím")

    # Prepnuti jednoho obdobi nesmi shodit druhe: odkaz to druhe nese s sebou.
    vlastni = client.get("/srovnani?a_od=2026-08-01&a_do=2026-08-31&b_days=7").text
    check("a_od=2026-08-01" in vlastni,
          "odkazy druhého přepínače nesou vlastní rozmezí toho prvního")

    # Zaporny rozdil se musi napsat se znamenkem. Filtry `hours` i `bytes`
    # berou zaporne cislo jako nulu, takze naivni vypis by tvrdil "0 min".
    klesa = client.get("/srovnani?a_days=365&b_days=7").text
    check("&minus;" in klesa or "−" in klesa,
          "záporný rozdíl se vypíše se znaménkem, ne jako nula")

    # Odkaz v menu.
    check('href="/srovnani"' in html, "a v menu je na co kliknout")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
