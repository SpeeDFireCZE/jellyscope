# -*- coding: utf-8 -*-
r"""Mapa na stránce Síť: místa, skuteční lidé a shluky podle přiblížení.

GeoLite2 dává městům souřadnice po čtvrtích - Praha jich má jedenáct.
Dřív se zaokrouhlovaly na desetinu stupně, takže se jedno město rozpadlo
na několik teček se stejným popiskem, a počet lidí byl maximum přes
adresy místo počtu různých diváků. Hlídá se tu:

* jedno místo = jedny souřadnice z GeoIP, lidé = různí diváci;
* shluky pro každou úroveň přiblížení a jejich popisky;
* vrstvy v SVG.

GeoIP se podvrhuje slovníkem adresa -> místo, databáze GeoLite2 není
potřeba.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_mapa_shluky.py
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "mapa.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYSCOPE_DEMO"] = "0"

from jellyscope import db, formatting, geoip, stats  # noqa: E402

failures = 0


def check(podminka: bool, popis: str) -> None:
    global failures
    print(f"{'OK    ' if podminka else 'CHYBA '} {popis}")
    if not podminka:
        failures += 1


def misto(lat: float, lon: float, mesto: str) -> dict:
    return {"lat": lat, "lon": lon, "mesto": mesto, "zeme": "Czechia", "kod": "CZ"}


# Tri prazske ctvrti, Kladno a adresa, u ktere GeoIP zna jen zemi.
MISTA = {
    "203.0.113.1": misto(50.0883, 14.4124, "Prague"),
    "203.0.113.2": misto(50.0471, 14.4523, "Prague"),
    "203.0.113.3": misto(50.1242, 14.398, "Prague"),
    "203.0.113.4": misto(50.1473, 14.1028, "Kladno"),
    "203.0.113.5": misto(49.75, 15.5, ""),
}
geoip.je_k_dispozici = lambda: True
geoip.najdi = lambda adresa: MISTA.get(adresa)

db.init_db()
pred = lambda dnu: (datetime.now(timezone.utc) - timedelta(days=dnu)).strftime(db.TIME_FORMAT)  # noqa: E731
PREHRANI = [
    # divak, adresa, sekund
    ("u1", "203.0.113.1", 600),
    ("u1", "203.0.113.2", 900),
    ("u2", "203.0.113.2", 900),
    ("u3", "203.0.113.3", 300),
    ("u4", "203.0.113.4", 200),
    ("u5", "203.0.113.5", 100),
    ("u6", "192.168.1.5", 500),   # domaci sit - nic neoznacuje
    ("u7", "", 500),              # import - adresu nenese
]
with db.connect() as conn:
    for k, (kdo, adresa, sekund) in enumerate(PREHRANI):
        conn.execute(
            "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name,"
            " item_type, started_at, last_seen_at, watched_seconds, paused_seconds,"
            " is_paused, is_active, play_method, client, remote_address, bitrate)"
            " VALUES (?, ?, ?, 'i1', 'Duna', 'Movie', ?, ?, ?, 0, 0, 0, 'DirectPlay',"
            " 'Web', ?, 8000000)",
            (f"s{k}", kdo, kdo.upper(), pred(1), pred(1), sekund, adresa))
    conn.commit()

print("--- místa ---")
body = stats.hotspoty(30)
podle_adresy = {(b["lat"], b["lon"]): b for b in body}
check(len(body) == 5, f"pět míst: tři pražské čtvrti, Kladno, jen země ({len(body)})")
a2 = podle_adresy.get((50.0471, 14.4523), {})
check(a2.get("lidi") == 2 and a2.get("divaci") == frozenset({"u1", "u2"}),
      f"lidé = různí diváci ({a2.get('lidi')}, {a2.get('divaci')})")
check(podle_adresy.get((50.0883, 14.4124), {}).get("popis") == "Prague, Czechia",
      "popis místa: město, země")
check(podle_adresy.get((49.75, 15.5), {}).get("popis") == "Czechia",
      "bez města jen země")
check([b["sekund"] for b in body] == [1800, 600, 300, 200, 100],
      "seřazeno podle odsledovaného času")
check(all(b["lidi"] == len(b["divaci"]) for b in body), "lidi sedí s množinou diváků")

zeme = {z["label"]: z for z in stats.zeme_divaku(30)}
check(zeme.get("Czechia", {}).get("mist") == 3,
      f"tabulka zemí počítá města: Prague, Kladno, Czechia ({zeme.get('Czechia', {}).get('mist')})")

volani_mist = []
puvodni_mista = stats.mista


def pocitana_mista(dni):
    volani_mist.append(dni)
    return puvodni_mista(dni)


stats.mista = pocitana_mista
from jellyscope import sekce  # noqa: E402

data_karty = sekce.PODLE_KLICE["mapa"].data(30)
stats.mista = puvodni_mista
check(len(volani_mist) == 1 and len(data_karty["hotspoty"]) == 5
      and data_karty["zeme"][0]["mist"] == 3,
      f"karta Mapa spočítá místa jednou, ne zvlášť pro tabulku zemí ({len(volani_mist)}×)")

print("--- tvary čísel ---")
for n, cekam in ((1, "1 člověk"), (3, "3 lidé"), (5, "5 lidí"), (0, "0 lidí"), (22, "22 lidí")):
    vysledek = formatting.tvar(n, "{n} člověk", "{n} lidé", "{n} lidí")
    check(vysledek == cekam, f"tvar({n}) = {cekam!r} ({vysledek!r})")

print("--- Mercator ---")
from jellyscope import mapa, worldmap  # noqa: E402

check(worldmap.na_mapu(0, 0) == (180.0, 180.0), "rovník a nultý poledník uprostřed")
check(abs(worldmap.na_mapu(85.05112878, 0)[1]) < 1e-6, "85,05° s. š. je horní okraj")
jih = worldmap.na_mapu(-90, 0)
check(jih == worldmap.na_mapu(-85.05112878, 0) and all(map(lambda v: v == v and abs(v) < 1e9, jih)),
      f"jižní pól se omezí, žádné nekonečno ({jih})")
x, y = worldmap.na_mapu(50.0883, 14.4124)
check(abs(x - 194.41) < 0.01 and abs(y - 121.95) < 0.01, f"Praha ({x:.2f}, {y:.2f})")
check(worldmap.VYCHOZI == (0.0, 40.41, 360.0, 215.04) and worldmap.SIRKA == worldmap.VYSKA == 360,
      "výchozí pohled 80° s. š. - 60° j. š., svět 360 × 360")

print("--- shluky ---")
urovne = mapa.shluky(body)
prvni = urovne[0]["shluky"] if urovne else []
check(len(urovne) >= 2 and urovne[0]["uroven"] == 1 and len(prvni) == 1,
      f"na 1× je všechno jeden shluk ({[len(u['shluky']) for u in urovne]})")
check(bool(prvni) and prvni[0]["lidi"] == 5 and prvni[0]["mist"] == 5,
      "shluk: pět různých lidí, pět míst")
check(bool(prvni) and prvni[0]["popis"] == "Prague, Czechia a 2 další místa",
      f"popis shluku ({prvni[0]['popis'] if prvni else None})")
posledni = urovne[-1] if urovne else {"uroven": 0, "shluky": []}
check(len(posledni["shluky"]) == 5 and all(s["mist"] == 1 for s in posledni["shluky"])
      and posledni["uroven"] <= mapa.MAX_UROVEN,
      f"poslední úroveň ({posledni['uroven']}×): každé místo zvlášť")
otisk = lambda u: sorted((round(s["x"], 6), round(s["y"], 6), s["mist"]) for s in u["shluky"])  # noqa: E731
check(all(otisk(a) != otisk(b) for a, b in zip(urovne, urovne[1:])),
      "žádné dvě po sobě jdoucí úrovně nejsou stejné")
check([u["uroven"] for u in urovne] == sorted({u["uroven"] for u in urovne}),
      "úrovně vzestupně, bez opakování")
check(mapa.shluky(body) == urovne, "stejná data = stejné shluky")
blizko = [dict(lat=50.0, lon=14.0, sekund=10, plays=1, bajtu=0, divaci=frozenset({"a"}),
               popis="X"),
          dict(lat=50.0, lon=14.001, sekund=5, plays=1, bajtu=0, divaci=frozenset({"b"}),
               popis="X")]
urovne_blizko = mapa.shluky(blizko)
konec = urovne_blizko[-1]
check(len(konec["shluky"]) == 1 and konec["shluky"][0]["mist"] == 2
      and konec["shluky"][0]["popis"] == "X",
      "místa 0,001 od sebe zůstanou i na 1024× jedním shlukem")
check(mapa.max_priblizeni(urovne_blizko, len(blizko)) == mapa.MAX_UROVEN,
      "a mapa se dá přiblížit až na 1024×")
check(mapa.max_priblizeni(urovne, len(body)) == max(20, posledni["uroven"]),
      "jinak do poslední úrovně, aspoň 20×")
check(mapa.max_priblizeni(mapa.shluky(body[:1]), 1) == 20, "jedno místo: 20×")
check(mapa.shluky([]) == [], "bez míst žádné úrovně")

print("--- SVG ---")
import re  # noqa: E402

from jellyscope import charts  # noqa: E402

svg = charts.mapa_sveta(body)
# Tecka = jeden shluk; shluk, ktery se pres nekolik urovni nemeni, je ve
# strance jen jednou (data-od / data-do), ne v kazde urovni znovu.
tecky = re.findall(r'<circle class="mapa-bod"[^>]*>', svg)
od_do = [(int(re.search(r'data-od="(\d+)"', c).group(1)), int(re.search(r'data-do="(\d+)"', c).group(1)))
         for c in tecky]
check(all("clenove" in s for u in urovne for s in u["shluky"]), "shluk ví, ze kterých míst je")
check(len(tecky) < sum(len(u["shluky"]) for u in urovne),
      f"stejný shluk není v každé úrovni znovu ({len(tecky)} teček)")
check(all(sum(1 for od, do in od_do if od <= u["uroven"] <= do) == len(u["shluky"]) for u in urovne),
      "na každé úrovni je vidět přesně tolik teček, kolik je shluků")
check(f'data-urovne="{" ".join(str(u["uroven"]) for u in urovne)}"' in svg, "seznam úrovní pro skript")
check(all(('display="none"' in c) == (od != 1) for c, (od, _) in zip(tecky, od_do)),
      "bez skriptu jsou vidět jen tečky úrovně 1×")
check(f'data-max="{mapa.max_priblizeni(urovne, len(body))}"' in svg, "data-max podle dat")
check('data-svet="0 0 360 360"' in svg and 'data-vychozi="0 40.41 360 215.04"' in svg
      and 'viewBox="0 40.41 360 215.04"' in svg, "svět, výchozí pohled a viewBox")
check("Prague, Czechia a 2 další místa: " in svg and "5 lidí" in svg,
      "tooltip shluku: popis a tvar čísla")
for trida in ("mapa-podklad-zakladni", "mapa-podrobna", "mapa-dlazdice", "mapa-hranice"):
    check(f'class="{trida}' in svg, f"vrstva podkladu .{trida}")
check(svg.index("mapa-podklad-zakladni") < svg.index("mapa-podrobna")
      < svg.index("mapa-dlazdice") < svg.index("mapa-hranice") < svg.index('class="mapa-bod"'),
      "pořadí zespodu: zabudovaný obrys, podrobná, OSM, hranice, tečky")
check('data-podklad="kresleny"' in svg and 'data-podrobna="0"' in svg, "výchozí podklad")
check('data-site="32 128"' in svg, "velikost sítě dlaždic posílá server")
# Velikost tecky se pocita v ramci urovne, ktera je zrovna videt - tecka
# prenesena z nizsi urovne nesmi mit meritko te nizsi (vetsi cas = mensi
# tecka). Skript proto dostane cas tecky a maximum kazde urovne.
check(tecky and all(re.search(r'data-s="\d+"', c) for c in tecky), "každá tečka nese odsledovaný čas")
maxima = re.search(r'data-maxima="([^"]*)"', svg)
check(bool(maxima) and [int(v) for v in maxima.group(1).split()]
      == [max(s["sekund"] for s in u["shluky"]) for u in urovne], "maximum času pro každou úroveň")
check('class="mapa-hranice"' not in charts.mapa_sveta(body, podklad="osm"),
      "při OpenStreetMap se hrubé hranice 1:110M přes skutečnou mapu nekreslí")
jedno = charts.mapa_sveta(body[:1])
check(jedno.count('class="mapa-bod"') == 1 and 'data-max="20"' in jedno,
      "jedno místo: jedna tečka, přiblížení 20×")
# S podrobnou mapou nebo OSM je pod teckami co videt i tam, kde se uz
# zadna tecka nerozpada - priblizit jde vzdycky az na 1024x.
check(f'data-max="{mapa.MAX_UROVEN}"' in charts.mapa_sveta(body[:1], podrobna=True)
      and f'data-max="{mapa.MAX_UROVEN}"' in charts.mapa_sveta(body[:1], podklad="osm"),
      "s podrobnou mapou nebo OSM jde přiblížit až na 1024× i u vzdálených míst")
import random  # noqa: E402

nahoda = random.Random(5)
evropa = [dict(lat=nahoda.uniform(36, 60), lon=nahoda.uniform(-10, 30), sekund=nahoda.randint(1, 9999),
               plays=1, bajtu=0, divaci=frozenset({f"u{i % 30}"}), popis=f"Mesto {i}, Zeme")
          for i in range(500)]
velka = charts.mapa_sveta(evropa)
check(len(velka) < 300_000, f"500 míst: SVG do 300 kB ({len(velka) // 1000} kB)")
check("Zatím žádná data" in charts.mapa_sveta([]), "bez míst text místo mapy")
zly = [{**body[0], "popis": "<script>alert(1)</script>"}]
check("<script>alert" not in charts.mapa_sveta(zly), "popis místa je escapovaný")

mapa_js = (PROJECT / "jellyscope" / "static" / "mapa.js").read_text(encoding="utf-8") \
    if (PROJECT / "jellyscope" / "static" / "mapa.js").exists() else ""
zaklad = (PROJECT / "jellyscope" / "templates" / "base.html").read_text(encoding="utf-8")
check('src="/static/mapa.js' in zaklad and 'querySelector(".mapa")' not in zaklad,
      "skript mapy je v static/mapa.js, ne v base.html")
check("dataset.s" in mapa_js and "dataset.maxima" in mapa_js,
      "mapa.js počítá poloměr tečky v rámci viditelné úrovně")
check("dataset.od" in mapa_js and "dataset.max" in mapa_js and "dataset.svet" in mapa_js,
      "mapa.js přepíná vrstvy a hlídá meze z dat")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
