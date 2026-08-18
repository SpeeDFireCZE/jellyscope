# -*- coding: utf-8 -*-
"""Rychlá synchronizace nově přidaných + import ze zálohy pluginu.

Dvě věci, které spolu nesouvisí, ale obě vznikly ze stejného důvodu —
plná cesta někdy nestačí:

  * **Rychlá synchronizace** projde jen to, co v Jellyfinu přibylo za
    poslední chvíli. Okno se odvíjí od intervalu úlohy a má rezervu:
    při běhu jednou za hodinu se hledá hodinu a půl zpátky, aby nic
    nepropadlo, když se běh o pár minut opozdí.

  * **Import ze zálohy** obchází rozbité API pluginu Playback Reporting.
    Když je plugin přeložený proti jinému Jellyfinu, spadne dřív, než se
    dostane ke svým datům — zálohu si ale umí vyrobit sám a uloží ji jako
    obyčejný soubor TSV.

U obou je jedna past, kterou test hlídá především: rychlá synchronizace
**nesmí** volat `_mark_missing()`. Ta označí za zmizelé všechno, co běh
neviděl — a tenhle běh vidí jen hrstku titulů, takže by zbytek knihovny
skončil v archivu.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
# Vlastní domeček: bez něj by DATABASE_PATH přebil uložený výběr
# databáze v data/database.json a test by běžel proti ostré databázi.
os.environ["JELLYSCOPE_HOME"] = str(_tmp)
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "recent.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic"

from jellyscope import db, importers, scanner  # noqa: E402
from jellyscope.jellyfin import JellyfinClient  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


def pred(minut: int) -> str:
    """Čas v tom tvaru, v jakém ho posílá Jellyfin."""
    return (datetime.now(timezone.utc) - timedelta(minutes=minut)).strftime(
        "%Y-%m-%dT%H:%M:%S.0000000Z")


db.init_db()

# Knihovna: tři čerstvé tituly a pět set starých.
NOVE = [{"Id": f"novy-{i}", "Name": f"Nový {i}", "Type": "Movie",
         "DateCreated": pred(i * 10), "RunTimeTicks": 6_000_000_000}
        for i in range(3)]
STARE = [{"Id": f"stary-{i}", "Name": f"Starý {i}", "Type": "Movie",
          "DateCreated": pred(5000 + i), "RunTimeTicks": 6_000_000_000}
         for i in range(500)]
KNIHOVNA = NOVE + STARE

pocitadlo = {"stranek": 0}


class FalesnyKlient:
    """Jellyfin, který umí stránkovat seřazený seznam."""

    def __init__(self, *a: Any, **k: Any) -> None:
        pass

    async def __aenter__(self) -> "FalesnyKlient":
        return self

    async def __aexit__(self, *a: Any) -> bool:
        return False

    async def items_page(self, start: int, limit: int, *a: Any, **k: Any) -> dict[str, Any]:
        pocitadlo["stranek"] += 1
        return {"Items": KNIHOVNA[start:start + limit],
                "TotalRecordCount": len(KNIHOVNA)}

    async def _first_admin_id(self) -> str | None:
        return None

    async def virtual_folders(self) -> list[dict[str, Any]]:
        # Rychlá synchronizace prochází knihovny jednu po druhé - jen tak
        # ví, kam nově přidaný titul patří.
        return [{"ItemId": "lib", "Name": "Filmy", "CollectionType": "movies"}]

    # Testujeme skutečnou logiku okna, ne její kopii.
    recent_items = JellyfinClient.recent_items


scanner.JellyfinClient = FalesnyKlient          # type: ignore[assignment]

# Starý titul, který v Jellyfinu pořád je - nesmí zmizet do archivu.
with db.connect() as conn:
    conn.execute(
        """INSERT INTO items (id, name, type, date_created, is_missing, synced_at)
           VALUES ('stary-0', 'Starý 0', 'Movie', '2020-01-01 00:00:00', 0, ?)""",
        (db.utcnow(),),
    )


print("--- hranici určuje poslední známý titul, ne hodiny ---")
# Původně se počítalo časové okno z intervalu úlohy. Jenže pak záleželo
# na tom, jestli úloha běžela podle plánu: když aplikace stála půl dne,
# okno bylo kratší než výpadek a tituly z té doby propadly.
# V databázi je zatím jen ten starý titul z roku 2020, takže hranice
# vyjde těsně před něj - a projde se všechno novější, tedy celá knihovna.
vysledek = asyncio.run(scanner.sync_recent())
check(vysledek["status"] == "ok", f"první běh proběhl: {vysledek}")
check(vysledek["since"].startswith("2019-12-31"),
      f"hranice je pět minut před posledním známým titulem ({vysledek['since']})")
check(vysledek["checked"] == len(KNIHOVNA),
      f"projde se všechno novější ({vysledek['checked']})")
# `stary-0` v databázi už byl (seděli jsme ho výš), takže nový není -
# a jako nový se počítat nesmí.
check(vysledek["items"] == len(KNIHOVNA) - 1,
      f"jako nové se počítají jen ty, které jsme ještě neměli ({vysledek['items']})")


print()
print("--- druhý běh hned po prvním nesmí hlásit žádnou novinku ---")
# Tohle byl nahlášený bug: hranice se schválně posouvá o pět minut zpátky
# (aby se nepřeskočil titul přidaný ve stejnou vteřinu), takže se nejnovější
# už známý titul stáhne znovu. Dřív se započítal jako nový - výsledek proto
# nikdy neukázal nulu a člověk marně hledal, co přibylo.
vysledek = asyncio.run(scanner.sync_recent())
check(vysledek["items"] == 0,
      f"nic nepřibylo, tak se nic nehlásí ({vysledek['items']})")
check(vysledek["checked"] > 0,
      f"zkontrolovat se jich přitom pár muselo ({vysledek['checked']})")

# Na úplně prázdné databázi není od čeho se odrazit - pak se vezmou
# nejnovější položky, kolik se jich vejde do stropu.
with db.connect() as conn:
    conn.execute("DELETE FROM items")
check(scanner._posledni_pridano() is None,
      "prázdná knihovna nemá hranici")
vysledek = asyncio.run(scanner.sync_recent())
check(vysledek["since"] is None and vysledek["items"] == len(KNIHOVNA),
      f"a vezme se všechno ({vysledek['items']})")

# Starý titul vrátíme zpátky - potřebujeme ho níž.
with db.connect() as conn:
    conn.execute(
        """INSERT INTO items (id, name, type, date_created, is_missing, synced_at)
           VALUES ('stary-0', 'Starý 0', 'Movie', '2020-01-01 00:00:00', 0, ?)
           ON CONFLICT(id) DO UPDATE SET is_missing = 0""",
        (db.utcnow(),))

pocitadlo["stranek"] = 0
vysledek = asyncio.run(scanner.sync_recent())
check(vysledek["since"] is not None, f"podruhé už hranice je ({vysledek['since']})")
check(pocitadlo["stranek"] <= 2,
      f"a stačí pár volání místo procházení knihovny ({pocitadlo['stranek']})")


print()
print("--- nový titul se najde, i když úloha dlouho neběžela ---")
KNIHOVNA.insert(0, {"Id": "uplne-novy", "Name": "Úplně nový", "Type": "Movie",
                    "DateCreated": pred(0), "RunTimeTicks": 6_000_000_000})
vysledek = asyncio.run(scanner.sync_recent())
check(db.query_one("SELECT name FROM items WHERE id = 'uplne-novy'") is not None,
      "nový titul se uložil")


print()
print("--- a hlavně: nic nezmizelo do archivu ---")
zmizele = db.query_value("SELECT COUNT(*) FROM items WHERE is_missing = 1")
check(zmizele == 0, f"žádný titul se neoznačil jako zmizelý ({zmizele})")
check(db.query_one("SELECT is_missing FROM items WHERE id = 'stary-0'")["is_missing"] == 0,
      "starý titul, který jsme teď neviděli, zůstal v knihovně")

# Kdyby někdo _mark_missing() do rychlé synchronizace přidal, tenhle test
# by prošel dál - proto ještě kontrola zdrojáku.
zdroj = (PROJECT / "jellyscope" / "scanner.py").read_text(encoding="utf-8")
telo = zdroj[zdroj.index("async def sync_recent("):zdroj.index("async def sync_library(")]
# Hledáme skutečné volání, ne zmínku v komentáři - ta tam být má.
prikazy = [r for r in telo.splitlines()
           if r.strip() and not r.strip().startswith(("#", '"', "*", "-"))]
check(not any("_mark_missing(" in r for r in prikazy),
      "sync_recent() _mark_missing() nevolá (jinak by knihovna zmizela)")


print()
print("--- titul zařazený se zpožděním se opraví sám ---")
# Tohle se stalo v praxi: Jellyfin soubor přidá dřív, než ho zařadí do
# knihovny. Uloží se pod názvem souboru a bez seriálu. Dřív to zůstalo
# navždy - časové okno mezitím ujelo a další běh už se na ten titul
# nepodíval. Teď je hranice odvozená od posledního známého titulu, takže
# se ten nejnovější kontroluje znovu, dokud nepřijde ještě novější.
KNIHOVNA.insert(0, {"Id": "nezarazeny", "Name": "nazev.souboru.1080p.mkv",
                    "Type": "Video", "DateCreated": pred(0),
                    "RunTimeTicks": 6_000_000_000})
asyncio.run(scanner.sync_recent())
r = db.query_one("SELECT name, type, series_name, library_id FROM items"
                 " WHERE id = 'nezarazeny'")
check(r["name"].endswith(".mkv"), "nejdřív se uloží pod názvem souboru")
check(r["library_id"] == "lib",
      f"ale knihovnu známe hned - prochází se po nich ({r['library_id']})")

KNIHOVNA[0] = {"Id": "nezarazeny", "Name": "1. díl", "Type": "Episode",
               "SeriesName": "Kancelář", "SeriesId": "ser-1",
               "ParentIndexNumber": 1, "IndexNumber": 1,
               "DateCreated": pred(0), "RunTimeTicks": 6_000_000_000}
asyncio.run(scanner.sync_recent())
r = db.query_one("SELECT name, type, series_name FROM items WHERE id = 'nezarazeny'")
check(r["name"] == "1. díl", f"po zařazení se název opraví ({r['name']})")
check(r["type"] == "Episode" and r["series_name"] == "Kancelář",
      f"i typ a seriál ({r['type']}, {r['series_name']})")


print()
print("--- opakovaný běh nic nezdvojí ---")
pred_poctem = db.query_value("SELECT COUNT(*) FROM items")
asyncio.run(scanner.sync_recent())
check(db.query_value("SELECT COUNT(*) FROM items") == pred_poctem,
      "počet titulů se nezměnil (zapisuje se přes ON CONFLICT)")


print("--- nový titul se při zdroji ffprobe rovnou změří ---")
# Při zdroji technických dat "ffprobe" se z Jellyfinu údaje schválně
# neberou. Nově přidaný titul proto zůstal úplně prázdný - bez kontejneru,
# rozlišení i velikosti - a čekal až na denní úlohu, tedy klidně den.
#
# Měří se **jen ty nově přidané**, ne celá knihovna: jde o pár souborů.
from jellyscope import probe  # noqa: E402

db.set_setting("tech_source", "ffprobe")
zmerene: list[str] = []


async def _falesny_probe(cesta: str, nastroj: str) -> dict[str, Any]:
    zmerene.append(cesta)
    return {"container": "mkv", "video_codec": "hevc", "width": 3840,
            "height": 2160, "bitrate": 25_000_000, "size_bytes": 40_000_000_000}


probe.probe_file = _falesny_probe                        # type: ignore[assignment]
probe.find_ffprobe = lambda cesta="": "/usr/bin/ffprobe"  # type: ignore[assignment]

# Ať mají všechny dosavadní tituly cestu - jinak by se do analýzy
# nedostaly a test by neukázal, že se vynechávají záměrně.
with db.connect() as conn:
    conn.execute("UPDATE items SET path = '/media/' || id || '.mkv'")
pred_analyzou = db.query_value("SELECT COUNT(*) FROM items")

KNIHOVNA.insert(0, {"Id": "novy-ffprobe", "Name": "Zbrusu nový", "Type": "Movie",
                    "Path": "/media/novy.mkv", "DateCreated": pred(0),
                    "RunTimeTicks": 6_000_000_000})
zmerene.clear()
vysledek = asyncio.run(scanner.sync_recent())

check(vysledek["items"] == 1, f"přibyl jeden titul ({vysledek['items']})")
check(vysledek.get("tech", {}).get("ok") == 1,
      f"a rovnou se změřil ({vysledek.get('tech')})")
check(zmerene == ["/media/novy.mkv"],
      f"měřil se JEN ten nový, ne celá knihovna ({len(zmerene)} souborů)")

novy_radek = db.query_one(
    "SELECT container, width, height, bitrate, tech_source FROM items"
    "  WHERE id = 'novy-ffprobe'")
check(novy_radek["container"] == "mkv" and novy_radek["width"] == 3840,
      f"technická data jsou v databázi ({dict(novy_radek)})")
check(novy_radek["tech_source"] == "ffprobe", "a je u nich, odkud pocházejí")

# Když nic nepřibude, neměří se vůbec nic.
zmerene.clear()
vysledek = asyncio.run(scanner.sync_recent())
check(vysledek["items"] == 0 and not zmerene,
      f"bez nových titulů se ffprobe nespouští ({len(zmerene)})")
check(db.query_value("SELECT COUNT(*) FROM items") == pred_analyzou + 1,
      "a počet titulů sedí")

db.set_setting("tech_source", "jellyfin")


print()
print("--- import ze zálohy pluginu (soubor TSV) ---")
# Plugin si zálohu vyrobí sám: Ovládací panel → Playback Reporting →
# Backup → Save backup. Je to obyčejný text, hodnoty oddělené tabulátorem.
#
# Dřív se sem nahrával rovnou `playback_reporting.db`. K tomu se ale člověk
# musí dostat přes SSH a ještě vědět, kde na serveru leží - kdežto zálohu
# si plugin vyrobí jedním kliknutím.
zaloha = "\n".join("\t".join(str(x) for x in radek) for radek in [
    ("2026-07-01 20:00:00", "u1", "film-1", "Movie", "Matrix",
     "DirectPlay", "Web", "Chrome", 3600),
    ("2026-07-02 21:00:00", "u1", "film-2", "Movie", "Duna",
     "DirectPlay", "Web", "Chrome", 1800),
    ("2026-07-03 10:00:00", "u1", "film-3", "Movie", "Krátký",
     "DirectPlay", "Web", "Chrome", 10),        # pod hranicí, přeskočí se
]).encode("utf-8")

vysledek = asyncio.run(
    importers.import_playback_reporting_tsv(zaloha, min_seconds=60))
check(vysledek["status"] == "ok", f"import proběhl: {vysledek.get('message', '')}")
check(vysledek["imported"] == 2, f"převzaly se dva záznamy ({vysledek['imported']})")
check(vysledek["too_short"] == 1, "krátký se přeskočil")

# Klíč je stejný jako u importu přes API, takže se to nezdvojí ani potom.
klice = [r["session_key"] for r in db.query_all(
    "SELECT session_key FROM playback ORDER BY session_key")]
check(all(k.startswith("import:pbr:") for k in klice),
      f"klíče mají tvar jako z API: {klice}")

vysledek = asyncio.run(
    importers.import_playback_reporting_tsv(zaloha, min_seconds=60))
check(vysledek["imported"] == 0, "opakované nahrání nic nepřidá")
check(db.query_value("SELECT COUNT(*) FROM playback") == 2, "pořád dva záznamy")

# Některé verze pluginu píšou i hlavičku - poznat to musíme samy.
s_hlavickou = (b"DateCreated\tUserId\tItemId\tItemType\tItemName\t"
               b"PlaybackMethod\tClientName\tDeviceName\tPlayDuration\n" + zaloha)
vysledek = asyncio.run(
    importers.import_playback_reporting_tsv(s_hlavickou, min_seconds=60))
check(vysledek["imported"] == 0 and vysledek["found"] == 3,
      f"soubor s hlavičkou se přečte stejně ({vysledek['found']} nalezeno)")


print()
print("--- co když soubor není, co má být ---")
vysledek = asyncio.run(importers.import_playback_reporting_tsv(b"tohle neni zaloha"))
check(vysledek["status"] == "error", "nesmyslný soubor se odmítne")
check("Backup" in vysledek["message"],
      f"a poradí, kde zálohu vyrobit: {vysledek['message'][:110]}")

vysledek = asyncio.run(importers.import_playback_reporting_tsv(b""))
check(vysledek["status"] == "error", "prázdný soubor se odmítne")


print()
print("--- knihovna, ve které nic nepřibylo ---")
# Funkce vrací seznam nových id. Prázdná odpověď z ní dřív vracela nulu
# a volající si ji přidával do seznamu (`nova_id.extend(...)`), což skončí
# na "'int' object is not iterable". Padalo to jen tehdy, když některá
# knihovna neměla ani jednu novinku - tedy skoro při každém běhu.
prazdna = asyncio.run(scanner._uloz_nove_polozky([], "lib", False))
check(prazdna == [], f"vrací se prázdný seznam, ne nula ({prazdna!r})")
nova: list[str] = []
nova.extend(prazdna)
check(nova == [], "a volající ho může rovnou přidat k ostatním")


print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
