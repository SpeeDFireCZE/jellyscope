# -*- coding: utf-8 -*-
r"""Ruční přiřazení archivované položky k jinému titulu.

Jellyfin titul určil špatně (třeba jako „Behind the scenes"), po opravě
ho založil znovu - a nesedí název, tmdb ani id. Automatika nemá podle
čeho párovat, člověk ví. Přiřazení:

* přesune historii (i z koše zapomenutých diváků) na vybraný živý titul
  a přepíše u ní název, druh, seriál a knihovnu,
* smaže archivovanou položku i se stopami a verzemi,
* udělá předtím zálohu databáze.

Jde to z detailu položky (jedna) i z archivu knihovny (hromadně - co nemá
vybraný cíl, zůstává). Mazání z archivu se potvrzuje vlastním oknem a taky
se před ním zálohuje. Z archivu knihovny jde mazat i hromadně - zaškrtnuté
položky, nikdy titul, který v knihovně je, ani archiv jiné knihovny.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_prirazeni_archivu.py
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
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "archiv.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic-dostatecne-dlouhy"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_DEMO"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, db, stats, web  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


db.init_db()
accounts.create("spravce", "dlouheheslo", is_admin=True)
accounts.create("ctenar", "dlouheheslo", is_admin=False)
kdy = (datetime.now(timezone.utc) - timedelta(days=3)).strftime(db.TIME_FORMAT)
# Ze slozky, kterou aplikace opravdu pouziva - ne z promenne testu.
ZALOHY = Path(os.environ["JELLYSCOPE_HOME"]) / "data" / "zalohy"


def polozka(item_id, name, typ="Movie", archiv=0, series=None, rada=None, dil=None,
            path=None, lib="filmy"):
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO items (id, name, type, library_id, series_id, series_name,"
            " parent_index_number, index_number, production_year, path, is_missing)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 2021, ?, ?)",
            (item_id, name, typ, lib, series and "s-" + series, series, rada, dil,
             path or f"/media/{item_id}.mkv", archiv))
        conn.commit()


def prehrani(item_id, name, kolik, typ="Movie", tabulka="playback"):
    with db.connect() as conn:
        for i in range(kolik):
            conn.execute(
                f"INSERT INTO {tabulka} (session_key, user_id, user_name, item_id, item_name,"
                " item_type, library_id, started_at, last_seen_at, watched_seconds, is_active)"
                " VALUES (?, 'u1', 'Jana', ?, ?, ?, 'filmy', ?, ?, 1800, 0)",
                (f"{item_id}-{tabulka}-{i}", item_id, name, typ, kdy, kdy))
        conn.commit()


with db.connect() as conn:
    conn.execute("INSERT INTO libraries (id, name, collection_type) VALUES"
                 " ('filmy', 'Filmy', 'movies'), ('serialy', 'Seriály', 'tvshows')")
    conn.commit()

polozka("duna", "Duna", path="/media/filmy/Duna (2021)/Duna.mkv")
polozka("bts", "Behind the scenes", typ="Video", archiv=1,
        path="/media/filmy/Duna (2021)/Duna.mkv")
prehrani("bts", "Behind the scenes", 3, typ="Video")
with db.connect() as conn:
    conn.execute("INSERT INTO item_streams (item_id, stream_index, type)"
                 " VALUES ('bts', 0, 'Video')")
    conn.execute("INSERT INTO item_versions (item_id, source_id, poradi, nazev)"
                 " VALUES ('bts', 'zdroj', 0, '1080p')")
    db._zajisti_kos(conn)
    conn.commit()
prehrani("bts", "Behind the scenes", 1, typ="Video", tabulka=db.KOS)

polozka("amelie", "Amélie")
polozka("stary-dil", "Pilot (2)", typ="Episode", archiv=1, series="Mikeš", rada=1, dil=1,
        lib="serialy")
prehrani("stary-dil", "Pilot (2)", 2, typ="Episode")
polozka("novy-dil", "Pilot", typ="Episode", series="Mikeš", rada=1, dil=1, lib="serialy")
polozka("zbytek", "Upoutávka", typ="Video", archiv=1)
prehrani("zbytek", "Upoutávka", 1, typ="Video")
polozka("jiny-archiv", "Ztracený film", archiv=1)

spravce = TestClient(web.app)
spravce.post("/login", data={"username": "spravce", "password": "dlouheheslo",
                             "zpusob": "mistni"})
ctenar = TestClient(web.app)
ctenar.post("/login", data={"username": "ctenar", "password": "dlouheheslo",
                            "zpusob": "mistni"})

print("--- hledání cíle ---")
nalez = spravce.get("/archiv/hledat?q=Du").json()["polozky"]
check([p["id"] for p in nalez] == ["duna"], f"jen živá Duna, archiv ne ({[p['id'] for p in nalez]})")
check(nalez[0]["soubor"] == "Duna.mkv" and "/media" not in str(nalez),
      "u výsledku jméno souboru, ne celá cesta na disku")
check(spravce.get("/archiv/hledat?q=Duna.mkv").json()["polozky"][0]["id"] == "duna",
      "najde se i podle jména souboru")
check(spravce.get("/archiv/hledat?q=D").json()["polozky"] == [], "jedno písmeno nic nehledá")
check(nalez[0]["nahled"].startswith("/image/duna?kind=Primary&w=80"),
      f"u výsledku je adresa plakátku ({nalez[0]['nahled']})")
dil = spravce.get("/archiv/hledat?q=Mike").json()["polozky"][0]
check(dil["nahled"].startswith("/image/s-Mikeš") or dil["nahled"].startswith("/image/s-Mike"),
      f"u dílu plakát seriálu, ne snímek z dílu ({dil['nahled']})")
check(spravce.get("/archiv/hledat?q=Mike").json()["polozky"][0]["popis"]
      == "Mikeš S01E01 – Pilot", "díl je popsaný seriálem a číslem")
check(ctenar.get("/archiv/hledat?q=Du", follow_redirects=False).status_code == 403,
      "čtenář hledat nesmí")

print()
print("--- přiřazení z detailu ---")
detail = spravce.get("/item/bts").text
check('id="okno-priradit"' in detail and 'id="okno-smazat"' in detail,
      "archivovaná položka má okno přiřazení i potvrzení smazání")
check("confirm(" not in detail, "prohlížečový confirm() už tam není")
okno = detail[detail.index('id="okno-priradit"'):detail.index("</dialog>", detail.index('id="okno-priradit"'))]
# Obrazek polozky z archivu uz v Jellyfinu neni - zbylo by prazdne policko.
check("/image/bts" not in okno and 'class="plakatek"><img' not in okno,
      "archivovaná položka v okně plakátek nemá")
check("data-vyber-cile" in okno, "plakát má jen vybraný cíl (výběr je v okně)")
check('id="okno-priradit"' not in ctenar.get("/item/bts").text, "čtenář je nevidí")
check('id="okno-priradit"' not in spravce.get("/item/duna").text,
      "živá položka okno přiřazení nemá")

check(ctenar.post("/item/bts/priradit", data={"cil": "duna"},
                  follow_redirects=False).status_code == 403, "čtenář přiřazovat nesmí")
for cil, popis in (("jiny-archiv", "do archivu"), ("bts", "sám na sebe"),
                   ("neexistuje", "neexistující"), ("", "prázdný cíl")):
    spravce.post("/item/bts/priradit", data={"cil": cil}, follow_redirects=False)
    check(stats.item("bts") is not None, f"cíl {popis} se odmítne a nic se nezmění")
spravce.post("/item/duna/priradit", data={"cil": "amelie"}, follow_redirects=False)
check(stats.item("duna") is not None, "živou položku přiřadit nejde (smazala by se z knihovny)")
check(not list(ZALOHY.glob("jellyscope-*")) if ZALOHY.exists() else True,
      "u odmítnutých se ani nezálohovalo")

odpoved = spravce.post("/item/bts/priradit", data={"cil": "duna"}, follow_redirects=False)
check(odpoved.status_code == 303 and odpoved.headers["location"] == "/item/duna",
      f"po přiřazení se jde na nový titul ({odpoved.headers.get('location')})")
check(stats.item("bts") is None, "archivovaná položka je smazaná")
radky = db.query_all("SELECT item_name, item_type FROM playback WHERE item_id = 'duna'")
check(len(radky) == 3, f"všechna tři přehrávání přešla na Dunu ({len(radky)})")
check(all(r["item_name"] == "Duna" and r["item_type"] == "Movie" for r in radky),
      "a nesou opravený název i druh")
check(db.query_value(f"SELECT COUNT(*) FROM {db.KOS} WHERE item_id = 'duna'") == 1,
      "i řádek v koši zapomenutých diváků (obnova ho vrátí ke správnému titulu)")
check(db.query_value("SELECT COUNT(*) FROM item_streams WHERE item_id = 'bts'") == 0
      and db.query_value("SELECT COUNT(*) FROM item_versions WHERE item_id = 'bts'") == 0,
      "stopy a verze špatně určené položky jsou pryč")
check(len(list(ZALOHY.glob("jellyscope-*"))) == 1, "a předtím se udělala záloha")
check("Duna" in spravce.get("/history").text
      and "Behind the scenes" not in spravce.get("/history").text,
      "historie ukazuje opravený titul")

print()
print("--- hromadně z archivu ---")
archiv = spravce.get("/library/serialy?tab=media&archived=1").text
check('id="okno-prirazeni"' in archiv and "Mikeš S01E01 – Pilot (2)" in archiv,
      "archiv knihovny má okno s archivovanými díly jednotlivě")
check('class="plakatek"><img' not in archiv and "data-nahled" not in archiv,
      "řádky archivu plakátek nemají")
check('id="okno-prirazeni"' not in ctenar.get("/library/serialy?tab=media&archived=1").text,
      "čtenář ho nevidí")
check('id="okno-prirazeni"' not in spravce.get("/library/serialy?tab=media").text,
      "v živé knihovně okno není")
okno = archiv.split('id="okno-prirazeni"', 1)[1].split("</dialog>", 1)[0]
check('data-krok="potvrzeni" hidden' in okno and "data-rekap-seznam" in okno,
      "okno má krok s rekapitulací, na začátku skrytý")
formular = okno.split('class="okno-kroky"', 1)[1]
check(formular.count('type="submit"') == 1
      and 'type="submit" name="potvrzeno" value="1" data-rekap-potvrdit' in formular,
      "odesílá jen potvrzení rekapitulace (první tlačítko jen přepne krok)")

# Bez potvrzeni rekapitulace se nic nezmeni - ani se nezalohuje.
zaloh_pred = len(list(ZALOHY.glob("jellyscope-*")))
odpoved = spravce.post("/library/filmy/archiv/priradit", data={"cil_stary-dil": "novy-dil"},
                       follow_redirects=False)
check(odpoved.status_code == 303 and stats.item("stary-dil") is not None
      and db.query_value("SELECT COUNT(*) FROM playback WHERE item_id = 'stary-dil'") == 2,
      "bez potvrzení rekapitulace zůstane archiv, jak byl")
check(len(list(ZALOHY.glob("jellyscope-*"))) == zaloh_pred, "a nezálohuje se")
check(spravce.post("/library/filmy/archiv/priradit",
                   data={"cil_stary-dil": "novy-dil", "potvrzeno": "ano"},
                   follow_redirects=False).status_code == 303
      and stats.item("stary-dil") is not None, "potvrzeno musí být přesně 1")

odpoved = spravce.post("/library/filmy/archiv/priradit", data={
    "potvrzeno": "1",
    "cil_stary-dil": "novy-dil",
    "cil_zbytek": "",                       # bez cile - nic se nestane
    "cil_jiny-archiv": "neexistuje",        # chybny cil - nesmi zastavit ostatni
}, follow_redirects=False)
check(odpoved.status_code == 303, f"uloženo ({odpoved.status_code})")
check(stats.item("stary-dil") is None
      and db.query_value("SELECT COUNT(*) FROM playback WHERE item_id = 'novy-dil'") == 2,
      "díl s cílem: historie přešla, z archivu je pryč")
check(stats.item("zbytek") is not None
      and db.query_value("SELECT COUNT(*) FROM playback WHERE item_id = 'zbytek'") == 1,
      "položka bez cíle zůstala, jak byla")
check(stats.item("jiny-archiv") is not None, "položka s chybným cílem taky")
check(len(list(ZALOHY.glob("jellyscope-*"))) == zaloh_pred + 1, "jedna záloha na celou dávku")
check(ctenar.post("/library/filmy/archiv/priradit",
                  data={"potvrzeno": "1", "cil_zbytek": "amelie"},
                  follow_redirects=False).status_code == 403, "čtenář hromadně nesmí")
zaloh_pred = len(list(ZALOHY.glob("jellyscope-*")))
spravce.post("/library/filmy/archiv/priradit", data={"potvrzeno": "1", "cil_zbytek": ""})
check(len(list(ZALOHY.glob("jellyscope-*"))) == zaloh_pred, "bez jediného cíle se ani nezálohuje")

print()
print("--- mazání z archivu ---")
zaloh_pred = len(list(ZALOHY.glob("jellyscope-*")))
check(ctenar.post("/item/zbytek/delete", follow_redirects=False).status_code == 403,
      "čtenář mazat nesmí")
spravce.post("/item/zbytek/delete", follow_redirects=False)
check(stats.item("zbytek") is None
      and db.query_value("SELECT COUNT(*) FROM playback WHERE item_id = 'zbytek'") == 0,
      "titul i historie jsou smazané")
check(len(list(ZALOHY.glob("jellyscope-*"))) == zaloh_pred + 1, "a předtím se zálohovalo")

check(stats.item("amelie") is not None
      and spravce.post("/item/amelie/delete", follow_redirects=False).status_code == 303
      and stats.item("amelie") is not None, "titul, který v knihovně je, smazat nejde")
check(len(list(ZALOHY.glob("jellyscope-*"))) == zaloh_pred + 1, "a kvůli tomu se nezálohuje")

print()
print("--- hromadné mazání z archivu ---")
polozka("smaz-1", "Starý film", archiv=1)
prehrani("smaz-1", "Starý film", 2)
prehrani("smaz-1", "Starý film", 1, tabulka=db.KOS)
polozka("smaz-2", "Druhý starý", archiv=1)
prehrani("smaz-2", "Druhý starý", 1)
polozka("cizi-archiv", "Díl odjinud", typ="Episode", archiv=1, series="Jiný", rada=1, dil=1,
        lib="serialy")
prehrani("cizi-archiv", "Díl odjinud", 1, typ="Episode")

archiv = spravce.get("/library/filmy?tab=media&archived=1").text
check('id="okno-mazani"' in archiv and "data-vybrat-vse" in archiv,
      "archiv má okno mazání s „Vybrat vše\"")
okno = archiv.split('id="okno-mazani"', 1)[1].split("</dialog>", 1)[0]
radek = okno.split('value="smaz-1"', 1)[1].split("</li>", 1)[0]
check("Starý film" in radek and "2 přehrávání" in radek,
      "řádek má zaškrtávátko, název a počet přehrávání")
check('value="amelie"' not in okno and 'value="cizi-archiv"' not in okno,
      "nabízí jen archiv téhle knihovny")
check(okno.split('class="okno-kroky"', 1)[1].count('type="submit"') == 1
      and 'type="submit" name="potvrzeno" value="1"' in okno,
      "maže jen tlačítko potvrzení")
check('id="okno-mazani"' not in ctenar.get("/library/filmy?tab=media&archived=1").text,
      "čtenář okno nevidí")
check('id="okno-mazani"' not in spravce.get("/library/filmy?tab=media").text,
      "v živé knihovně okno není")

zaloh_pred = len(list(ZALOHY.glob("jellyscope-*")))
spravce.post("/library/filmy/archiv/smazat", data={"smazat": ["smaz-1", "smaz-2"]})
check(stats.item("smaz-1") is not None and stats.item("smaz-2") is not None,
      "bez potvrzení se nic nesmaže")
spravce.post("/library/filmy/archiv/smazat",
             data={"potvrzeno": "1", "smazat": ["amelie", "cizi-archiv", "neexistuje"]})
check(stats.item("amelie") is not None and stats.item("cizi-archiv") is not None,
      "živý titul ani archiv jiné knihovny nesmaže")
check(len(list(ZALOHY.glob("jellyscope-*"))) == zaloh_pred,
      "a když není co mazat, nezálohuje se")
check(ctenar.post("/library/filmy/archiv/smazat",
                  data={"potvrzeno": "1", "smazat": ["smaz-1"]},
                  follow_redirects=False).status_code == 403
      and stats.item("smaz-1") is not None, "čtenář mazat nesmí")
# „Vybrat vše" u plného okna pošle tisíc polí; Starlette jich bez
# zvednutí limitu pustí jen 1000 a odpoví chybou 400.
odpoved = spravce.post("/library/filmy/archiv/smazat", follow_redirects=False, data={
    "potvrzeno": "1", "smazat": [f"x{i}" for i in range(web.STROP_MAZANI_ARCHIVU)]})
check(odpoved.status_code == 303, f"projde i plný seznam ({odpoved.status_code})")

odpoved = spravce.post("/library/filmy/archiv/smazat", follow_redirects=False, data={
    "potvrzeno": "1",
    "smazat": ["smaz-1", "smaz-2", "smaz-1", "amelie", "cizi-archiv", "neexistuje"]})
check(odpoved.status_code == 303, f"smazáno ({odpoved.status_code})")
check(stats.item("smaz-1") is None and stats.item("smaz-2") is None,
      "zaškrtnuté položky z archivu jsou pryč")
check(db.query_value("SELECT COUNT(*) FROM playback WHERE item_id IN ('smaz-1', 'smaz-2')") == 0,
      "i s historií")
check(db.query_value(f"SELECT COUNT(*) FROM {db.KOS} WHERE item_id = 'smaz-1'") == 0,
      "i v koši zapomenutých diváků")
check(stats.item("amelie") is not None and stats.item("cizi-archiv") is not None
      and db.query_value("SELECT COUNT(*) FROM playback WHERE item_id = 'cizi-archiv'") == 1,
      "živý titul a archiv jiné knihovny zůstaly i s historií")
check(stats.item("jiny-archiv") is not None, "nezaškrtnutá položka zůstala")
check(len(list(ZALOHY.glob("jellyscope-*"))) == zaloh_pred + 1, "jedna záloha na celou dávku")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
