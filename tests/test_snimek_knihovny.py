# -*- coding: utf-8 -*-
r"""Denní snímek knihovny a růst v čase.

Jellyscope si jinak pamatuje jen SOUČASNÝ stav knihovny; historii má
výhradně přehrávání. Jeden řádek na den (velikost, počet položek, kolik je
4K, kolik bez metadat) otevírá otázky, na které dosud nebylo z čeho
odpovědět: jak knihovna roste, co přibylo za období a jestli se transcode
po zásahu zlepšil.

Na čem to stojí:

* **Zapisuje se po úspěšné synchronizaci**, ne z vlastní úlohy - po
  zastavené nebo spadlé by snímek zachytil půlku knihovny a v grafu by
  z toho byl propad, který se nestal.
* **Jeden řádek na den**, tentýž den se přepisuje: platí poslední známý
  stav dne.
* **Odhad umí dvě podoby** - "místo dojde za X dnů" tam, kde na soubory
  vidíme, jinak "za rok poroste na X". Slibovat první tam, kde platí jen
  druhé, by bylo horší než mlčet.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_snimek_knihovny.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "snimek.db")
os.environ["SECRET_KEY"] = "testovaci-klic"

from jellyscope import db, demodata, scanner, sekce, stats  # noqa: E402

failures = 0


def check(podminka: bool, popis: str, detail: str = "") -> None:
    global failures
    print(f"{'OK    ' if podminka else 'CHYBA '} {popis} {detail}")
    if not podminka:
        failures += 1


def snimek(den: str, velikost: int, polozek: int = 100,
           volne: int | None = None) -> None:
    """Podstrčí snímek za daný den - pro počítání růstu."""
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO library_snapshot (den, polozek, filmu, epizod,"
            " velikost, uhd, hdr, bez_technik, volne_misto, zapsano_v)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT (den) DO UPDATE SET velikost = excluded.velikost,"
            " polozek = excluded.polozek, volne_misto = excluded.volne_misto",
            (den, polozek, polozek, 0, velikost, 0, 0, 0, volne, db.utcnow()))
        conn.commit()


db.init_db()
demodata.seed()

print("--- zápis snímku ---")
zapsany = scanner.zapis_snimek()
check(zapsany is not None, "snímek se zapíše")
check(zapsany["polozek"] > 0 and zapsany["velikost"] > 0,
      f"a nese skutečná čísla ({zapsany['polozek']} položek)")

# Cisla musi sedet s tim, co je v knihovne - jinak by graf rustu kreslil
# neco jineho nez zbytek aplikace.
skutecne = db.query_one(
    "SELECT COUNT(*) AS polozek, COALESCE(SUM(COALESCE(size_bytes, 0)), 0) AS velikost"
    " FROM items WHERE is_missing = 0")
check(zapsany["polozek"] == skutecne["polozek"], "počet položek sedí s knihovnou")
check(zapsany["velikost"] == skutecne["velikost"], "velikost taky")

pokryti = stats.tech_coverage()
check(zapsany["bez_technik"] == pokryti["missing"],
      "a počet položek bez technických dat sedí se Zjištěními")

# Tyz den se prepisuje, ne zdvojuje: plati posledni znamy stav dne.
scanner.zapis_snimek()
scanner.zapis_snimek()
check(db.query_value("SELECT COUNT(*) FROM library_snapshot") == 1,
      "opakovaný zápis téhož dne řádek přepíše, nepřidá")

print()
print("--- minulost se dopočítá z data vzniku ---")
# Snimky zacinaji az u teto verze, ale minulost nese `date_created`
# a u polozek v archivu `synced_at`. Krivka tak sahá i pred prvni snimek.
dnes = date.today()
with db.connect() as conn:
    conn.execute("DELETE FROM library_snapshot")
    conn.commit()

pred_rokem = (dnes - timedelta(days=365)).isoformat()
pred_pul_rokem = (dnes - timedelta(days=180)).isoformat()
with db.connect() as conn:
    conn.execute("UPDATE items SET date_created = ?", (pred_rokem + " 12:00:00",))
    conn.execute("UPDATE items SET date_created = ? WHERE id IN"
                 " (SELECT id FROM items LIMIT 5)", (pred_pul_rokem + " 12:00:00",))
    conn.commit()

krivka = stats.snimky(400)
check(len(krivka) > 300, f"křivka sahá zpět přes rok ({len(krivka)} dnů)")
check(all(radek["dopocteno"] for radek in krivka),
      "a bez jediného snímku je celá dopočtená")
# Pet polozek pribylo az pred pul rokem - do te doby jich musi byt min.
starsi = next(r for r in krivka if r["den"] == (dnes - timedelta(days=200)).isoformat())
novejsi = next(r for r in krivka if r["den"] == (dnes - timedelta(days=100)).isoformat())
check(novejsi["polozek"] == starsi["polozek"] + 5,
      f"a přírůstek sedí ({starsi['polozek']} → {novejsi['polozek']})")

# NEJDULEZITEJSI INVARIANT: posledni bod krivky se musi rovnat tomu, co
# hlasi "Velikost celkem" na teze strance. Kdyz se lisi, graf si
# protireci s cislem hned vedle - a presne to se stalo: polozky
# v archivu, u kterych chybi `synced_at`, z krivky nikdy neodesly
# a drzely ji navzdy vys.
with db.connect() as conn:
    conn.execute("UPDATE items SET is_missing = 1, synced_at = NULL"
                 " WHERE id IN (SELECT id FROM items LIMIT 2)")
    conn.execute("UPDATE items SET is_missing = 1, synced_at = ''"
                 " WHERE id IN (SELECT id FROM items WHERE is_missing = 0 LIMIT 2)")
    conn.commit()

skutecnost = int(db.query_value(
    "SELECT COALESCE(SUM(COALESCE(size_bytes, 0)), 0) FROM items"
    " WHERE is_missing = 0", default=0) or 0)
konec_krivky = stats.snimky(400)[-1]
check(int(konec_krivky["velikost"]) == skutecnost,
      f"konec křivky sedí s velikostí knihovny"
      f" ({int(konec_krivky['velikost'])} vs {skutecnost})")
check(int(konec_krivky["polozek"]) == db.query_value(
          "SELECT COUNT(*) FROM items WHERE is_missing = 0", default=0),
      "a počet položek taky")

# Vratime zpatky, at dalsi kontroly meri, co maji.
with db.connect() as conn:
    conn.execute("UPDATE items SET is_missing = 0, synced_at = ?", (db.utcnow(),))
    conn.commit()

# Polozka v archivu z knihovny ubyde tim dnem, kdy ji aplikace naposledy
# videla - jinak by krivka jen rostla a mazani by z ni zmizelo.
with db.connect() as conn:
    conn.execute("UPDATE items SET is_missing = 1, synced_at = ?"
                 " WHERE id IN (SELECT id FROM items LIMIT 3)",
                 ((dnes - timedelta(days=50)).isoformat() + " 12:00:00",))
    conn.commit()
s_ubytkem = stats.snimky(400)
pred = next(r for r in s_ubytkem if r["den"] == (dnes - timedelta(days=60)).isoformat())
po = next(r for r in s_ubytkem if r["den"] == (dnes - timedelta(days=40)).isoformat())
check(po["polozek"] == pred["polozek"] - 3,
      f"úbytek z archivu se pozná ({pred['polozek']} → {po['polozek']})")

# Měření má vždycky přednost před dopočtem.
snimek((dnes - timedelta(days=10)).isoformat(), 9_999 * 1024 ** 3, polozek=777)
smiseno = stats.snimky(400)
posledni_dopoctene = [r for r in smiseno if r["dopocteno"]]
zmerene = [r for r in smiseno if not r["dopocteno"]]
check(len(zmerene) == 1 and zmerene[0]["polozek"] == 777,
      "snímek se do křivky dostane jako měření")
check(posledni_dopoctene and posledni_dopoctene[-1]["den"]
      < zmerene[0]["den"], "a dopočet končí tam, kde měření začíná")
check(all(r["den"] < zmerene[0]["den"] for r in posledni_dopoctene),
      "dopočet se přes změřenou dobu nepřekrývá")

# Dopocet se posadi na prvni zmerenou hodnotu. Tvar minulosti z dat
# vycteme, ale jeji vyska je systematicky posunuta (dnesni velikosti),
# a bez posazeni je na spoji schod - tedy tvrzeni o konkretnim dni,
# ktere je nepravdive: knihovna pres noc o polovinu neprisla.
spoj_dopocet = posledni_dopoctene[-1]["velikost"]
spoj_mereni = zmerene[0]["velikost"]
check(abs(spoj_dopocet - spoj_mereni) < spoj_mereni * 0.05,
      f"dopočet je posazený na měření ({spoj_dopocet / 1024 ** 3:.0f}"
      f" vs {spoj_mereni / 1024 ** 3:.0f} GB)")
check(all(r["velikost"] >= 0 for r in posledni_dopoctene),
      "a žádný den nevyjde záporně")
# Past: posun je JEDNO cislo pro celou dopoctenou cast, takze se tvar
# nesmi zmenit - prirustky mezi dny zustavaji, jake byly.
bez_posazeni = stats._dopoctena_krivka(posledni_dopoctene[0]["den"],
                                       posledni_dopoctene[-1]["den"])
puvodni_rozdil = (bez_posazeni[-1]["velikost"] - bez_posazeni[0]["velikost"])
posazeny_rozdil = (posledni_dopoctene[-1]["velikost"]
                   - posledni_dopoctene[0]["velikost"])
check(puvodni_rozdil == posazeny_rozdil,
      f"posazení nemění tvar křivky ({puvodni_rozdil} vs {posazeny_rozdil})")

# A do grafu jde krivka jako DVE serie, aby slo poznat mereni od dopoctu.
data = sekce.data_rustu(400)
graf_dopoctene = [r for r in data["snimky"] if r["gb_dopocteno"] is not None]
graf_zmerene = [r for r in data["snimky"] if r["gb_zmereno"] is not None]
check(bool(graf_dopoctene) and bool(graf_zmerene),
      f"graf má obě série ({len(graf_dopoctene)} dopočtených,"
      f" {len(graf_zmerene)} změřených bodů)")
check(graf_dopoctene[-1]["den"] == graf_zmerene[0]["den"],
      "a navazují na sebe ve společném dni, takže mezi nimi není díra")
check(data["rust"]["dopocteno"] and data["rust"]["dopocteno_do"],
      f"souhrn hlásí, že část je dopočtená (do {data['rust']['dopocteno_do']})")

print()
print("--- růst v čase ---")
# Dal uz jde o samotny vypocet rustu ze snimku, takze se dopocet vypne:
# `date_created` je jedina vec, ze ktere umi minulost postavit.
with db.connect() as conn:
    conn.execute("UPDATE items SET date_created = NULL, is_missing = 0")
    conn.execute("DELETE FROM library_snapshot")
    conn.commit()
for odstup, velikost in ((30, 1_000 * 1024 ** 3), (0, 1_300 * 1024 ** 3)):
    snimek((dnes - timedelta(days=odstup)).isoformat(), velikost,
           polozek=100 + (30 - odstup))

rust = stats.rust_knihovny(90)
check(rust["dost_dat"], "dva snímky stačí na křivku")
check(rust["prirustek"] == 300 * 1024 ** 3,
      f"přírůstek sedí ({rust['prirustek'] / 1024 ** 3:.0f} GB)")
check(round(rust["denne"] / 1024 ** 3) == 10, "denní přírůstek taky (10 GB)")
check(rust["polozek"] == 30, "a přírůstek položek")

# Bez volneho mista se slibuje jen to, co vime.
check(rust["dnu_do_konce"] is None, "bez známého volného místa se odhad nedělá")
check(rust["za_rok"] and rust["za_rok"] > rust["velikost"],
      "místo toho se řekne, kam to spěje za rok")

print()
print("--- odhad, když na volné místo vidíme ---")
snimek(dnes.isoformat(), 1_300 * 1024 ** 3, polozek=130,
       volne=500 * 1024 ** 3)
rust = stats.rust_knihovny(90)
check(rust["dnu_do_konce"] == 50,
      f"500 GB volna při 10 GB denně je 50 dnů ({rust['dnu_do_konce']})")

print()
print("--- co se nedá spočítat, se netvrdí ---")
with db.connect() as conn:
    conn.execute("DELETE FROM library_snapshot")
    conn.commit()
prazdno = stats.rust_knihovny(90)
check(not prazdno["dost_dat"] and prazdno["dnu"] == 0,
      "bez snímků se nic netvrdí")

snimek(dnes.isoformat(), 1_000 * 1024 ** 3)
jeden = stats.rust_knihovny(90)
check(not jeden["dost_dat"], "jeden bod není růst")

# Klesajici knihovna: nema smysl hlasit, za jak dlouho dojde misto -
# a delit nulou uz vubec ne.
snimek((dnes - timedelta(days=10)).isoformat(), 2_000 * 1024 ** 3, volne=10 ** 12)
klesa = stats.rust_knihovny(90)
check(klesa["dost_dat"] and klesa["prirustek"] < 0, "úbytek se pozná")
check(klesa["dnu_do_konce"] is None and klesa["za_rok"] is None,
      "u klesající knihovny se odhad nedělá")

snimek((dnes - timedelta(days=5)).isoformat(), 1_000 * 1024 ** 3, volne=10 ** 12)
stejne = stats.rust_knihovny(90)
check(stejne["dnu_do_konce"] is None, "ani u stojící - dělit nulou nejde")

print()
print("--- v grafu je dopočet odlišený od měření ---")
from jellyscope import charts  # noqa: E402

BODY = [{"den": f"2026-01-0{i}", "a": float(i)} for i in range(1, 6)]
SERIE = [{"key": "a", "label": "A"}]


def cest(svg: str) -> int:
    """Kolik čar (ne výplní) graf nakreslil."""
    return svg.count('fill="none" stroke=')


cela = charts.area_chart_multi(BODY, "den", SERIE)
check(cest(cela) == 1, f"souvislá série je jedna čára ({cest(cela)})")

# None = "tady série neplatí". Musí z toho být přerušení, ne propad k nule.
s_mezerou = charts.area_chart_multi(
    [dict(b, a=None) if b["den"] == "2026-01-03" else b for b in BODY],
    "den", SERIE)
check(cest(s_mezerou) == 2, f"série s dírou se rozpadne na dvě čáry ({cest(s_mezerou)})")
# A hlavně: CHYBĚJÍCÍ KLÍČ musí dál znamenat nulu. Na tom stojí všechny
# ostatní grafy v aplikaci - kdyby se z něj stala díra, rozpadly by se.
bez_klice = charts.area_chart_multi(
    [{"den": b["den"]} if b["den"] == "2026-01-03" else b for b in BODY],
    "den", SERIE)
check(cest(bez_klice) == 1,
      f"chybějící klíč zůstává nulou, ne dírou ({cest(bez_klice)})")

print()
print("--- sekce ---")
data = sekce.data_rustu(90)
check("snimky" in data and "rust" in data, "sekce dodá snímky i souhrn")
check(all("gb" in radek for radek in data["snimky"]),
      "graf dostane gigabajty, ne bajty")
check("rust_knihovny" in sekce.PODLE_KLICE, "a je v registru sekcí")

print()
print("--- stránka ---")
from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts  # noqa: E402
from jellyscope.web import app  # noqa: E402

accounts.create("spravce", "dlouheheslo", is_admin=True)
with TestClient(app) as client:
    client.post("/login", data={"username": "spravce", "password": "dlouheheslo"},
                follow_redirects=False)
    stranka = client.get("/library")
    check(stranka.status_code == 200, "Knihovna se načte")
    check("Růst knihovny" in stranka.text or "Library growth" in stranka.text,
          "a růst je na ní")

    # Sekce musi obstat i sama v prehledu - stejna past jako u "Odkud se
    # divaji", kde chybejici import makra shodil celou stranku.
    db.set_setting(sekce.ZAPNUTO, "1")
    db.forget_settings()
    sekce.uloz_rozvrzeni(["rust_knihovny"])
    check(client.get("/dashboard").status_code == 200,
          "a sama ve vlastním přehledu taky")

    print()
    print("--- období na Knihovně je tytéž jako všude jinde ---")
    # Rok snimku, at je na cem rozsah poznat: se tremi radky vyjde
    # tricet dnu stejne jako rok a test by nemeril nic.
    for odstup in range(0, 365, 3):
        snimek((dnes - timedelta(days=odstup)).isoformat(),
               (1_000 - odstup) * 1024 ** 3, polozek=200 - odstup // 3)

    # Zadny vlastni prepinac na karte: jedna volba pro celou aplikaci,
    # vcetne vlastniho rozmezi. Kdyby mela Knihovna svuj, mel by clovek
    # dve ruzna obdobi a musel by hlidat, ktere zrovna plati.
    stranka = client.get("/library?days=365").text
    check("okno-obdobi" in stranka, "filtr období je na stránce")
    check("rust_dny" not in stranka, "a vlastní přepínač na kartě není")

    # Volba se sdili se zbytkem aplikace.
    client.get("/?days=30")
    check('href="/library?days=30"' in client.get("/library").text
          or 'chip active' in client.get("/library").text,
          "volba z jiné stránky se propíše i sem")

    def bodu(html: str) -> int:
        return html.count('class="chart-hit"')

    kratke = bodu(client.get("/library?days=30").text)
    dlouhe = bodu(client.get("/library?days=365").text)
    check(dlouhe > kratke, f"delší období nakreslí víc bodů ({kratke} -> {dlouhe})")

    # Vlastni rozmezi - to na karte drive nesslo vubec.
    od = (dnes - timedelta(days=60)).isoformat()
    do = (dnes - timedelta(days=30)).isoformat()
    vlastni = client.get(f"/library?od={od}&do={do}")
    check(vlastni.status_code == 200, "vlastní rozmezí projde")
    check(0 < bodu(vlastni.text) < dlouhe,
          f"a nakreslí jen svůj výsek ({bodu(vlastni.text)} bodů)")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
