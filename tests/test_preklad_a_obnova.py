# -*- coding: utf-8 -*-
"""Načtení metadat jedné položky a mezery v anglickém překladu.

Dvě věci pohromadě, protože obě jsou o tom, co uživatel opravdu vidí:

1. **Obnovení jedné položky.** Když v Jellyfinu opravíš metadata jednoho
   filmu, nemá být potřeba kvůli tomu pouštět synchronizaci celé
   knihovny. Z Jellyfinu se přitom smí jenom číst.

2. **Překlad.** Věty skládané v Pythonu (popisky grafů, hlášky úloh,
   „před 5 min") se do slovníku nedostanou samy — a když se složí i
   s proměnnou částí, ve slovníku se nikdy nenajdou. Přesně to se stalo
   u „Dabing, nebo originál?", kde zůstala polovina věty česky.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = str(_tmp)
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "preklad.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic"

from jellyscope import (accounts, applog, db, formatting, i18n,  # noqa: E402
                        langstats, scanner)

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


db.init_db()
accounts.create("spravce", "dlouheheslo", is_admin=True)
accounts.create("ctenar", "ctenarheslo", is_admin=False)

with db.connect() as conn:
    conn.execute("INSERT INTO libraries (id, name, synced_at) VALUES ('lib','Filmy',?)",
                 (db.utcnow(),))
    conn.execute(
        """INSERT INTO items (id, name, type, library_id, production_year,
                              audio_languages, is_missing, synced_at)
           VALUES ('film-1', 'Starý název', 'Movie', 'lib', 1999, 'en', 0, ?)""",
        (db.utcnow(),))


print("--- načtení metadat jedné položky ---")
dotazy: list[list[str]] = []


class FalesnyKlient:
    """Jellyfin, který zná jednu položku - a hlásí, na co se ho ptali."""

    odpoved: list[dict[str, Any]] = [{
        "Id": "film-1", "Name": "Opravený název", "Type": "Movie",
        "ProductionYear": 2001, "RunTimeTicks": 60_000_000_000,
        "MediaSources": [{
            "Container": "mkv", "Size": 5_000_000_000, "Path": "/m/f.mkv",
            "MediaStreams": [
                {"Type": "Video", "Index": 0, "Codec": "hevc",
                 "Width": 1920, "Height": 1080},
                {"Type": "Audio", "Index": 1, "Codec": "eac3", "Language": "cze"},
                {"Type": "Audio", "Index": 2, "Codec": "ac3", "Language": "eng"},
            ],
        }],
    }]

    def __init__(self, *a: Any, **k: Any) -> None:
        pass

    async def __aenter__(self) -> "FalesnyKlient":
        return self

    async def __aexit__(self, *a: Any) -> bool:
        return False

    async def items_by_ids(self, ids: list[str]) -> list[dict[str, Any]]:
        dotazy.append(list(ids))
        return [i for i in self.odpoved if i["Id"] in ids]


scanner.JellyfinClient = FalesnyKlient  # type: ignore[assignment]

vysledek = asyncio.run(scanner.refresh_item("film-1"))
check(vysledek["status"] == "ok", f"obnovení proběhlo ({vysledek})")
check(dotazy == [["film-1"]], f"ptali jsme se jen na tu jednu položku ({dotazy})")

radek = db.query_one("SELECT * FROM items WHERE id = 'film-1'")
check(radek["name"] == "Opravený název", f"název se přepsal ({radek['name']})")
check(radek["production_year"] == 2001, f"rok taky ({radek['production_year']})")
check(radek["library_id"] == "lib",
      "knihovna zůstala - Jellyfin ji v odpovědi neposílá a prázdná by "
      "položku vyřadila z přehledů")
check("cs" in (radek["audio_languages"] or ""),
      f"jazyk stopy se doplnil ({radek['audio_languages']})")

stopy = db.query_all("SELECT * FROM item_streams WHERE item_id = 'film-1'")
check(len(stopy) == 3, f"stopy se přepsaly ({len(stopy)})")

# Co Jellyfin nezná, se nesmí tiše smazat - historie na to odkazuje.
neznama = asyncio.run(scanner.refresh_item("neexistuje"))
check(neznama["status"] == "error", "neznámé id vrátí chybu")
FalesnyKlient.odpoved = []
zmizela = asyncio.run(scanner.refresh_item("film-1"))
check(zmizela["status"] == "error", "položka, kterou Jellyfin nezná, taky")
check(db.query_one("SELECT * FROM items WHERE id = 'film-1'") is not None,
      "a v databázi zůstala")


print()
print("--- tlačítko je jen pro správce ---")
from fastapi.testclient import TestClient  # noqa: E402

import jellyscope.web as web  # noqa: E402

web.scanner.JellyfinClient = FalesnyKlient  # type: ignore[assignment]

spravce = TestClient(web.app)
spravce.post("/login", data={"username": "spravce", "password": "dlouheheslo"})
ctenar = TestClient(web.app)
ctenar.post("/login", data={"username": "ctenar", "password": "ctenarheslo"})

stranka = spravce.get("/item/film-1").text
check('action="/item/film-1/refresh"' in stranka, "správce tlačítko vidí")
check('action="/item/film-1/refresh"' not in ctenar.get("/item/film-1").text,
      "čtenář ne")
check(ctenar.post("/item/film-1/refresh", follow_redirects=False).status_code == 403,
      "a routu nedostane")


print()
print("--- anglický překlad tam, kde se věty skládají v Pythonu ---")
db.set_setting("ui_language", "en")
try:
    # "Dabing, nebo originál?" - popisek nese název jazyka, takže se
    # celá věta ve slovníku nikdy nenajde. Musí se složit z kousků.
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO items (id, name, type, library_id, audio_languages,
                                  is_missing, synced_at)
               VALUES ('film-2','Duna','Movie','lib','cs,en',0,?)""",
            (db.utcnow(),))
        conn.execute(
            """INSERT INTO playback (session_key, user_id, item_id, item_name,
                                     item_type, audio_language, started_at,
                                     last_seen_at, watched_seconds, is_active)
               VALUES ('s1','u1','film-2','Duna','Movie','cs',?,?,3600,0)""",
            (db.utcnow(), db.utcnow()))
    db.set_setting("preferred_language", "cs")
    popisky = [r["label"] for r in langstats.dubbed_vs_original(90)]
    check(popisky and all("i když" not in p and "jiná možnost" not in p
                          and "Jiný jazyk" not in p for p in popisky),
          f"popisky dabingu jsou anglicky ({popisky})")

    check(formatting.relative_human(db.utcnow()) == "just now",
          f"relativní čas je anglicky ({formatting.relative_human(db.utcnow())})")

    from jellyscope import tasks  # noqa: E402
    for uloha in tasks.TASKS.values():
        check(i18n.translate(uloha.name) != uloha.name,
              f"název úlohy má překlad: {uloha.name}")
        check(i18n.translate(uloha.description) != uloha.description,
              f"popis úlohy má překlad: {uloha.name}")

    ulohy = spravce.get("/settings?section=tasks").text
    for cesky in ["Synchronizace knihovny", "Stáhne z Jellyfinu",
                  "Záloha databáze", "Nově přidané tituly"]:
        check(cesky not in ulohy, f"v anglických Úlohách není {cesky!r}")

    importy = spravce.get("/settings?section=import").text
    check("Vybrat soubor" not in importy and "Choose file" in importy,
          "výběr souboru je vlastní tlačítko, ne nativní prvek prohlížeče")
finally:
    db.set_setting("ui_language", "cs")


print()
print("--- jazyk logu ---")
import logging  # noqa: E402

filtr = applog._PrekladHlasek()


def prelozeno(zprava: str) -> str:
    zaznam = logging.LogRecord("t", logging.INFO, __file__, 1, zprava, (), None)
    filtr.filter(zaznam)
    return str(zaznam.msg)


applog.nastav_jazyk("cs")
check(prelozeno("databaze pripravena") == "databaze pripravena",
      "česky se nepřekládá")

applog.nastav_jazyk("en")
check(prelozeno("databaze pripravena") == "database ready",
      f"anglicky ano ({prelozeno('databaze pripravena')})")
check(prelozeno("tohle ve slovniku neni") == "tohle ve slovniku neni",
      "chybějící překlad hlášku nezahodí")


def prelozeno_s_hodnotami(zprava: str, args: tuple[Any, ...]) -> str:
    zaznam = logging.LogRecord("t", logging.INFO, __file__, 1, zprava, args, None)
    filtr.filter(zaznam)
    return zaznam.getMessage()


# Do hlášky se doplňují i české názvy z rozhraní - anglický log by jinak
# psal "scheduled task: Nově přidané tituly".
from jellyscope import tasks as _tasks  # noqa: E402

check(prelozeno_s_hodnotami("naplanovana uloha: %s",
                            (_tasks.TASKS["recent"].name,))
      == "scheduled task: Recently added titles",
      "název úlohy v hlášce se přeloží taky")
check(prelozeno_s_hodnotami("zaloha smazana: %s", ("jellyscope-20260818.db",))
      == "backup deleted: jellyscope-20260818.db",
      "ale jméno souboru zůstane, jak je")

# U hlášky, kterou slovník nezná (třeba z uvicornu), se hodnoty nechávají
# být - je v nich cokoliv a překládat data by byla chyba.
check(prelozeno_s_hodnotami("cizi hlaska %s", ("Nově přidané tituly",))
      == "cizi hlaska Nově přidané tituly",
      "v cizí hlášce se hodnoty nepřekládají")

applog.nastav_jazyk("cs")

# Slovník musí sedět na hlášky ve zdrojácích - jinak by se tiše přestalo
# překládat, jakmile někdo hlášku přepíše.
import ast  # noqa: E402

ve_zdroji = set()
for soubor in sorted((PROJECT / "jellyscope").glob("*.py")):
    for uzel in ast.walk(ast.parse(soubor.read_text(encoding="utf-8"))):
        if (isinstance(uzel, ast.Call) and isinstance(uzel.func, ast.Attribute)
                and uzel.func.attr in ("debug", "info", "warning", "error",
                                       "exception", "critical")
                and uzel.args and isinstance(uzel.args[0], ast.Constant)
                and isinstance(uzel.args[0].value, str)):
            ve_zdroji.add(uzel.args[0].value)

chybi = ve_zdroji - set(i18n.LOG_EN)
navic = set(i18n.LOG_EN) - ve_zdroji
check(not chybi, f"každá hláška má překlad (chybí {len(chybi)}: {sorted(chybi)[:3]})")
check(not navic, f"a žádný překlad nezůstal po smazané hlášce ({sorted(navic)[:3]})")

# Zástupné znaky musí sedět, jinak by se log rozsypal až za běhu.
import re  # noqa: E402

vzor = re.compile(r"%\(?\w*\)?[sdrf]")
rozdilne = [k for k, v in i18n.LOG_EN.items() if vzor.findall(k) != vzor.findall(v)]
check(not rozdilne, f"zástupné znaky (%s, %d) sedí ({rozdilne[:2]})")

# A ted to same doopravdy: kazda hlaska se ZAPISE do skutecneho souboru
# s anglickym prekladem a se spravnym poctem hodnot. Shoda slovniku sama
# o sobe nestaci - preklad s jinym typem zastupce (%s misto %d) projde
# kontrolou vyse a spadne az za behu, u konkretni hlasky, kterou nikdo
# necekal.
import logging  # noqa: E402

cesta_logu = applog.setup()
applog.nastav_jazyk("en")
pred = len(Path(cesta_logu).read_text(encoding="utf-8").splitlines())
zkusebni = logging.getLogger("jellyscope.zkouska_prekladu")
spadle = []
for hlaska in sorted(ve_zdroji):
    znaky = vzor.findall(hlaska)
    if any(z.startswith("%(") for z in znaky):
        # Pojmenovaní zástupci - aplikace posílá slovník, ne n-tici.
        hodnoty = ({j: "x" for j in re.findall(r"%\((\w+)\)", hlaska)},)
    else:
        hodnoty = tuple(7 if z[-1] in "df" else "x" for z in znaky)
    try:
        zkusebni.warning(hlaska, *hodnoty)
    except Exception as chyba:      # noqa: BLE001
        spadle.append((hlaska[:44], str(chyba)[:44]))
for rukojet in logging.getLogger().handlers:
    rukojet.flush()

check(not spadle, f"každá hláška se anglicky opravdu zapíše ({spadle[:2]})")

# V anglickem logu nesmi zustat ceska hlaska - to by znamenalo, ze
# preklad sice ve slovniku je, ale klic neodpovida tomu, co se loguje.
napsane = Path(cesta_logu).read_text(encoding="utf-8").splitlines()[pred:]
ceske = [r.split("] ", 1)[-1] for r in napsane
         if re.search(r"[ěščřžýáíéůúňť]", r.split(": ", 2)[-1])]
check(not ceske, f"a v anglickém logu nezůstane česká věta ({ceske[:1]})")
applog.nastav_jazyk("cs")


print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
