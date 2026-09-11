# -*- coding: utf-8 -*-
r"""Titul, který leží na disku víckrát (4K vedle 1080p).

Dva soubory téhož filmu ve složce Jellyfin nespojí do dvou položek -
udělá **jednu položku s víc `MediaSources`**. Dřív se z nich vzala jen
ta první a druhá se ztratila: v knihovně stálo 1080p, i když vedle leželo
4K, a v detailu nebylo poznat, že soubory jsou dva.

Verze se teď ukládají všechny a v detailu titulu se dají přepnout. Údaje
samotné položky (a tedy i statistiky) zůstávají z té, kterou Jellyfin
považuje za hlavní - jinak by se velikost knihovny počítala dvakrát.

Ověřeno i naostro proti Jellyfinu 10.11.11: dva soubory ve složce daly
jednu položku se dvěma verzemi a přepínač v detailu mění velikost,
rozlišení i cestu.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_verze_souboru.py
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
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "verze.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic-dostatecne-dlouhy"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, db, jellyfin, scanner, stats, web  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


db.init_db()

# Tak to posila Jellyfin: jedna polozka, dva soubory.
POLOZKA = {
    "Id": "film-1",
    "Name": "Zkouška",
    "Type": "Movie",
    "MediaSources": [
        {"Id": "zdroj-4k", "Name": "2160p", "Path": "/media/film-4k.mkv",
         "Container": "mkv", "Size": 40_000_000_000, "Bitrate": 60_000_000,
         "RunTimeTicks": 72_000_000_000,
         "MediaStreams": [
             {"Type": "Video", "Codec": "hevc", "Width": 3840, "Height": 2160,
              "VideoRange": "HDR"},
             {"Type": "Audio", "Codec": "truehd", "Channels": 8,
              "Language": "eng"},
             {"Type": "Subtitle", "Language": "cze"}]},
        {"Id": "zdroj-1080", "Name": "1080p", "Path": "/media/film-1080.mkv",
         "Container": "mp4", "Size": 8_000_000_000, "Bitrate": 10_000_000,
         "RunTimeTicks": 72_000_000_000,
         "MediaStreams": [
             {"Type": "Video", "Codec": "h264", "Width": 1920, "Height": 1080},
             {"Type": "Audio", "Codec": "aac", "Channels": 2,
              "Language": "cze"}]},
    ],
}

print("--- z odpovědi Jellyfinu se vytáhnou obě verze ---")
verze = jellyfin.extract_verze(POLOZKA)
check(len(verze) == 2, f"dvě verze ({len(verze)})")
check([v["nazev"] for v in verze] == ["2160p", "1080p"],
      f"v pořadí, v jakém je posílá Jellyfin ({[v['nazev'] for v in verze]})")
check(verze[0]["width"] == 3840 and verze[1]["width"] == 1920,
      "každá má svoje rozlišení")
check(verze[0]["video_codec"] == "hevc" and verze[1]["video_codec"] == "h264",
      "i svůj kodek")
check(verze[0]["size_bytes"] != verze[1]["size_bytes"], "i svou velikost")

hlavni = jellyfin.extract_tech_from_item(POLOZKA)
check(hlavni["width"] == 3840,
      "položka si drží tu, kterou Jellyfin považuje za hlavní")
check(hlavni["size_bytes"] == verze[0]["size_bytes"],
      "takže se velikost knihovny nepočítá dvakrát")

print()
print("--- uloží se a dají se přečíst ---")
with db.connect() as conn:
    conn.execute(
        "INSERT INTO items (id, name, type, width, height, size_bytes,"
        " container, path, is_missing) VALUES"
        " ('film-1','Zkouška','Movie',3840,2160,40000000000,'mkv',"
        " '/media/film-4k.mkv',0)")
    conn.commit()
check(scanner.zapis_verze([("film-1", verze)]) == 2, "zapsaly se dvě")

ulozene = [dict(v) for v in stats.verze_polozky("film-1")]
check(len(ulozene) == 2, f"a dvě se přečtou ({len(ulozene)})")
check(ulozene[0]["nazev"] == "2160p", "pořadí drží")
check(ulozene[1]["audio_languages"], "u každé i jazyky zvuku")

# Kdyz jedna verze z disku zmizi, nema po ni v detailu zustat radek.
check(scanner.zapis_verze([("film-1", verze[:1])]) == 1, "přepis na jednu")
check(len(stats.verze_polozky("film-1")) == 1, "druhá se smazala")
scanner.zapis_verze([("film-1", verze)])

print()
print("--- v detailu se dají přepnout ---")
accounts.create("spravce", "dlouheheslo", is_admin=True)
klient = TestClient(web.app)
klient.post("/login", data={"username": "spravce", "password": "dlouheheslo",
                            "zpusob": "mistni"})


def velikost(html: str) -> str:
    shoda = re.search(r"Velikost</th><td>([^<]+)", html)
    return shoda.group(1).strip() if shoda else "?"


def cesta(html: str) -> str:
    shoda = re.search(r"word-break:break-all\">([^<]+)", html)
    return shoda.group(1).strip() if shoda else "?"


vychozi = klient.get("/item/film-1").text
check("?verze=zdroj-1080" in vychozi and "?verze=zdroj-4k" in vychozi,
      "přepínač nabízí obě")
check("2160p" in vychozi and "1080p" in vychozi, "a jmenuje je")

prepnuto = klient.get("/item/film-1?verze=zdroj-1080").text
check(velikost(vychozi) != velikost(prepnuto),
      f"velikost se přepnutím změní ({velikost(vychozi)} -> {velikost(prepnuto)})")
check("film-1080.mkv" in cesta(prepnuto),
      f"a cesta ukazuje na ten druhý soubor ({cesta(prepnuto)})")
check("film-4k.mkv" in cesta(vychozi), "bez přepnutí je to hlavní soubor")

print()
print("--- jeden soubor: nepřepíná se nic ---")
with db.connect() as conn:
    conn.execute("INSERT INTO items (id, name, type, is_missing)"
                 " VALUES ('film-2','Sám','Movie',0)")
    conn.commit()
scanner.zapis_verze([("film-2", verze[:1])])
sam = klient.get("/item/film-2").text
check("?verze=" not in sam, "u jediné verze se přepínač nekreslí")

print()
print("--- úlohy verze nepromažou ---")
# Slucovani, uklid a narovnani dat sahaji na polozky. Verze na ne
# odkazuji cizim klicem, takze se musi resit spolu s nimi - jinak na
# PostgreSQL spadne cizi klic a na SQLite zustanou viset na id, ktere uz
# neexistuje.
zdroj = (PROJECT / "jellyscope" / "scanner.py").read_text(encoding="utf-8")
for jmeno, usek in (
        ("slučování podle tmdb", "_merge_by_tmdb"),
        ("přenos historie", "prenes_historii"),
        ("slučování archivu", "slouc_archiv_do_zivych"),
        ("úklid cizích typů", "uklid_fantomu")):
    zacatek = zdroj.index(f"def {usek}")
    # Telo az po dalsi funkci - nektera je dlouha pres sto radku a pevny
    # kus znaku by merilo jen jeji zacatek.
    dalsi = zdroj.find(chr(10) + "def ", zacatek + 1)
    telo = zdroj[zacatek:dalsi if dalsi > 0 else len(zdroj)]
    check("item_versions" in telo, f"{jmeno} myslí i na verze")

# A hlavne: prazdna odpoved nesmi verze smazat. Presne to by se stalo,
# kdyby nejaky dotaz nepozadal o `MediaSources`.
check(scanner.zapis_verze([("film-1", [])]) == 0,
      "prázdný seznam nic nezapíše")
check(len(stats.verze_polozky("film-1")) == 2,
      "a hlavně nic nesmaže - verze zůstaly obě")

# Cizi klic funguje: po smazani polozky nezustanou verze viset.
with db.connect() as conn:
    conn.execute("DELETE FROM item_versions WHERE item_id = 'film-2'")
    conn.execute("DELETE FROM items WHERE id = 'film-2'")
    conn.commit()
check(len(stats.verze_polozky("film-2")) == 0, "po smazání položky nic nezbylo")

print()
print("--- záloha o verzích ví ---")
from jellyscope import tasks  # noqa: E402

check("item_versions" in tasks.ZALOHOVANE_TABULKY, "tabulka se zálohuje")
poradi = list(tasks.ZALOHOVANE_TABULKY)
check(poradi.index("items") < poradi.index("item_versions"),
      "a po položkách - odkazuje se na ně cizím klíčem")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
