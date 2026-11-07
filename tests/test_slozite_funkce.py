# -*- coding: utf-8 -*-
r"""Chování čtyř nejdelších funkcí tam, kam ostatní testy nedosáhnou.

Přestavba 3f rozložila `_store_sessions` (sběrač), `run_tech_scan`
(analýza souborů) a `import_jellystat_json` na pojmenované kroky - a
`settings_page` taky, tu ale ostatní testy projdou celou. Před rozložením
se změřilo, kterými řádky dnešní sada neprojde; tenhle test je připíchne,
ať se dá poznat, že se po rozložení chovají stejně:

* sběrač: relace, která nic nehraje; jeden snímek = jedna transakce;
* analýza: obsazeno, chybí ffprobe, vadné mapování cest, omezení na
  knihovnu, `limit`, prázdný výčet položek, nic k analýze, doplnění
  jazyků (i když Jellyfin selže), dovětky zprávy úlohy;
* import Jellystatu: chybí tabulka přehrávání; záznam, který není
  slovník, je bez času, má nečitelnou délku nebo je v souboru dvakrát.

Test prošel nad kódem PŘED rozložením a musí projít i po něm.

Spec: docs/superpowers/specs/2026-11-06-slozite-funkce-design.md

Spuštění:
    .venv/bin/python tests/test_slozite_funkce.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))
from _balik import podvrhni  # noqa: E402 - podvrh ve všech podmodulech balíku

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "slozite.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

from jellyscope import collector, db, importers, languages, probe, scanner  # noqa: E402
from jellyscope.jellyfin import JellyfinError  # noqa: E402

failures = 0


def check(podminka: bool, popis: str) -> None:
    global failures
    print(f"{'OK    ' if podminka else 'CHYBA '} {popis}")
    if not podminka:
        failures += 1


db.init_db()


def pocet_uloh(druh: str) -> int:
    return db.query_value("SELECT COUNT(*) FROM scan_log WHERE kind = ?", (druh,)) or 0


print("--- sběrač: relace, která nic nehraje ---")
HRAJE = {"Id": "s1", "UserId": "u1", "UserName": "Jana", "Client": "Web",
         "DeviceName": "Chrome", "DeviceId": "d1", "PlayState": {"PositionTicks": 0},
         "NowPlayingItem": {"Id": "film-1", "Name": "Matrix", "Type": "Movie"}}
NEHRAJE = {"Id": "s2", "UserId": "u2", "UserName": "Karel", "Client": "Web",
           "DeviceName": "Firefox", "DeviceId": "d2", "PlayState": {}}
vysledek = collector._store_sessions([NEHRAJE, HRAJE], max_gap_seconds=60)
check(vysledek == {"active": 1, "started": 1, "ended": 0},
      f"nehrající relace se nezapočítá mezi aktivní ({vysledek})")
vysledek = collector._store_sessions([NEHRAJE], max_gap_seconds=60)
check(vysledek == {"active": 0, "started": 0, "ended": 1},
      f"když hraje jen nehrající, všechno aktivní se ukončí ({vysledek})")

print()
print("--- sběrač: jeden snímek = jedna transakce ---")
# Pomocník, který by si otevřel vlastní spojení, by na SQLite čekal na
# zámek a při chybě nechal půl snímku zapsaného.
puvodni_connect = db.connect
otevreno = {"n": 0}


def pocitany_connect(*a: Any, **k: Any) -> Any:
    otevreno["n"] += 1
    return puvodni_connect(*a, **k)


db.connect = pocitany_connect  # type: ignore[assignment]
try:
    dalsi = dict(HRAJE, Id="s3", UserId="u3", DeviceId="d3",
                 NowPlayingItem={"Id": "film-2", "Name": "Alien", "Type": "Movie"})
    collector._store_sessions([HRAJE, dalsi], max_gap_seconds=60)
    collector._store_sessions([HRAJE, dalsi], max_gap_seconds=60)
finally:
    db.connect = puvodni_connect  # type: ignore[assignment]
check(otevreno["n"] == 2, f"dvě volání se dvěma relacemi = dvě spojení ({otevreno['n']})")


print()
print("--- analýza souborů ---")
with db.connect() as conn:
    conn.execute("DELETE FROM playback")
    for lib in ("lib-a", "lib-b"):
        conn.execute("INSERT INTO libraries (id, name, collection_type, synced_at)"
                     " VALUES (?, ?, 'movies', ?)", (lib, lib, db.utcnow()))
    for i, lib in enumerate(("lib-a", "lib-a", "lib-b")):
        conn.execute("INSERT INTO items (id, name, type, library_id, path, is_missing, synced_at)"
                     " VALUES (?, ?, 'Movie', ?, ?, 0, ?)",
                     (f"pol-{i}", f"Film {i}", lib, f"/media/film-{i}.mkv", db.utcnow()))

cesty: list[str] = []


async def probe_ok(path: str, binarka: str) -> dict[str, Any]:
    cesty.append(path)
    return {"container": "mkv", "video_codec": "h264", "size_bytes": 1, "streams": []}


probe.find_ffprobe = lambda p="": "/usr/bin/ffprobe"  # type: ignore[assignment]
probe.probe_file = probe_ok  # type: ignore[assignment]
# Jellyfin v testu není - doplňky se podvrhnou, ať se nikam nepřipojují.
vrat = [podvrhni(scanner, "kandidati_na_jazyk_z_nazvu", lambda *a, **k: []),
        podvrhni(scanner, "kandidati_na_rozsah_z_jellyfinu", lambda *a, **k: [])]


async def drzi_zamek() -> dict[str, Any]:
    async with scanner.beh._scan_lock:
        return await scanner.run_tech_scan()

check(asyncio.run(drzi_zamek()) == {"status": "busy", "message": "Jiná úloha už běží."},
      "když už úloha běží, analýza odpoví 'busy'")

probe.find_ffprobe = lambda p="": None  # type: ignore[assignment]
vysledek = asyncio.run(scanner.run_tech_scan())
check(vysledek.get("status") == "error"
      and vysledek.get("message", "").startswith("ffprobe se nepodařilo najít"),
      f"bez ffprobe chyba s návodem ({vysledek.get('status')})")
probe.find_ffprobe = lambda p="": "/usr/bin/ffprobe"  # type: ignore[assignment]

for vadne in ("{", '{"a": 1}'):
    db.set_setting("path_mappings", vadne)
    cesty.clear()
    vysledek = asyncio.run(scanner.run_tech_scan(only_missing=False, item_ids=["pol-0"]))
    check(vysledek.get("status") == "ok" and cesty == ["/media/film-0.mkv"],
          f"vadné mapování cest {vadne!r} se přeskočí, cesta zůstane ({cesty})")
db.set_setting("path_mappings", "[]")

cesty.clear()
asyncio.run(scanner.run_tech_scan(only_missing=False, library_id="lib-b"))
check(cesty == ["/media/film-2.mkv"], f"library_id analyzuje jen tu knihovnu ({cesty})")

cesty.clear()
asyncio.run(scanner.run_tech_scan(only_missing=False, limit=1))
check(len(cesty) == 1, f"limit=1 zanalyzuje jednu položku ({len(cesty)})")

pred = pocet_uloh("tech")
vysledek = asyncio.run(scanner.run_tech_scan(item_ids=[]))
check(vysledek == {"status": "ok", "total": 0, "ok": 0, "failed": 0},
      f"prázdný výčet položek: nic ({vysledek})")
check(pocet_uloh("tech") == pred, "a úloha se ani nezaloží")

asyncio.run(scanner.run_tech_scan(only_missing=False))     # teď mají ffprobe všechny
pred = pocet_uloh("tech")
vysledek = asyncio.run(scanner.run_tech_scan(only_missing=True))
check(vysledek == {"status": "ok", "total": 0, "ok": 0, "failed": 0},
      f"nic k analýze: nic ({vysledek})")
zaznam = scanner.last_scan("tech")
check(pocet_uloh("tech") == pred + 1 and zaznam is not None and zaznam["status"] == "done"
      and zaznam["message"] == "Není co analyzovat.",
      "a úloha se zapíše s hláškou 'Není co analyzovat.'")

print()
print("--- analýza: stopy bez jazyka se doptají Jellyfinu najednou ---")


async def probe_bez_jazyka(path: str, binarka: str) -> dict[str, Any]:
    cesty.append(path)
    return {"container": "mkv", "video_codec": "h264", "size_bytes": 1,
            "streams": [{"stream_index": 0, "type": "Video", "codec": "h264", "language": languages.UNKNOWN},
                        {"stream_index": 1, "type": "Audio", "codec": "aac", "language": languages.UNKNOWN}]}


probe.probe_file = probe_bez_jazyka  # type: ignore[assignment]
# Z dřívějška: položka pol-1 (stejná knihovna) má titulky bez jazyka i bez
# značky původu, pol-2 (jiná knihovna) taky - ta se ptát nemá.
with db.connect() as conn:
    for pol in ("pol-1", "pol-2"):
        conn.execute("INSERT INTO item_streams (item_id, stream_index, type, language)"
                     " VALUES (?, 5, 'Subtitle', ?)", (pol, languages.UNKNOWN))
dotazy: list[list[str]] = []


async def doplnit_ok(ids: list[str]) -> int:
    dotazy.append(ids)
    return 4


vrat.append(podvrhni(scanner, "doplnit_jazyky_z_jellyfinu", doplnit_ok))
vysledek = asyncio.run(scanner.run_tech_scan(only_missing=False, item_ids=["pol-0"],
                                             library_id="lib-a"))
check(dotazy == [["pol-0"]], f"jen analyzovaná položka, z dřívějška nic mimo výčet ({dotazy})")
dotazy.clear()
# Analyzuje se jen pol-0 (ostatní už ffprobe mají); pol-1 přijde z dřívějška.
with db.connect() as conn:
    conn.execute("UPDATE items SET tech_source = NULL WHERE id = 'pol-0'")
cesty.clear()
vysledek = asyncio.run(scanner.run_tech_scan(only_missing=True, library_id="lib-a"))
check(cesty == ["/media/film-0.mkv"] and dotazy == [["pol-0", "pol-1"]],
      f"z analýzy i z dřívějška jedním dotazem, seřazeně, jen ta knihovna ({cesty}, {dotazy})")
check(vysledek.get("doplneno") == 4, f"počet doplněných se vrátí ({vysledek.get('doplneno')})")

# Seřazení se pozná až na víc položkách: dvě (ale i pět) můžou být ve
# správném pořadí i náhodou - pořadí množiny závisí na PYTHONHASHSEED.
# U dvanácti se to nestalo ani u jednoho z 200 semínek. Názvy jsou schválně
# v jiném pořadí než id - analýza jde podle názvu.
SERAZENE = [f"pol-c{i:02d}" for i in range(12)]
with db.connect() as conn:
    conn.execute("INSERT INTO libraries (id, name, collection_type, synced_at)"
                 " VALUES ('lib-c', 'lib-c', 'movies', ?)", (db.utcnow(),))
    for nazev, pol in zip("ABCDEFGHIJKL", [SERAZENE[(i * 5) % 12] for i in range(12)]):
        conn.execute("INSERT INTO items (id, name, type, library_id, path, is_missing, synced_at)"
                     " VALUES (?, ?, 'Movie', 'lib-c', ?, 0, ?)",
                     (pol, f"Film {nazev}", f"/media/{pol}.mkv", db.utcnow()))
dotazy.clear()
asyncio.run(scanner.run_tech_scan(only_missing=False, library_id="lib-c"))
check(dotazy == [SERAZENE], f"dvanáct položek jedním dotazem, seřazeně - ne v pořadí analýzy ({dotazy})")


async def doplnit_chyba(ids: list[str]) -> int:
    raise JellyfinError("Jellyfin neodpovídá")


vrat.append(podvrhni(scanner, "doplnit_jazyky_z_jellyfinu", doplnit_chyba))
vysledek = asyncio.run(scanner.run_tech_scan(only_missing=False, library_id="lib-a"))
check(vysledek.get("status") == "ok" and vysledek.get("doplneno") == 0,
      f"nedostupný Jellyfin analýzu nezkazí ({vysledek.get('status')}, {vysledek.get('doplneno')})")

print()
print("--- analýza: dovětky zprávy úlohy ---")
probe.probe_file = probe_ok  # type: ignore[assignment]
vrat += [podvrhni(scanner, "kandidati_na_jazyk_z_nazvu", lambda *a, **k: ["pol-0"]),
         podvrhni(scanner, "doplnit_jazyky_z_nazvu", lambda ids: 2),
         podvrhni(scanner, "kandidati_na_rozsah_z_jellyfinu", lambda *a, **k: ["pol-0"])]


async def rozsah_ok(ids: list[str]) -> int:
    return 3


vrat.append(podvrhni(scanner, "doplnit_rozsah_z_jellyfinu", rozsah_ok))
asyncio.run(scanner.run_tech_scan(only_missing=False, item_ids=["pol-0"]))
zprava = (scanner.last_scan("tech") or {}).get("message") or ""
check(", odhadnut z názvu u 2 titulů" in zprava, f"dovětek o odhadu z názvu ({zprava})")
check(", rozsah z Jellyfinu u 3 titulů" in zprava, "dovětek o rozsahu z Jellyfinu")
for v in reversed(vrat):
    v()


print()
print("--- import Jellystatu: chybí tabulka přehrávání ---")
vysledek = asyncio.run(importers.import_jellystat_json(json.dumps({"neco": []}).encode()))
check(vysledek.get("status") == "error", f"soubor bez tabulky je chyba ({vysledek.get('status')})")
zaznam = scanner.last_scan("import")
check(zaznam is not None and zaznam["status"] == "error"
      and zaznam["message"] == "tabulka přehrávání nenalezena",
      "a úloha to zapíše")

print()
print("--- import Jellystatu: vadné záznamy a duplicita v souboru ---")
PLATNY = {"Id": "r1", "UserId": "u1", "UserName": "Jana", "NowPlayingItemId": "film-x",
          "NowPlayingItemName": "Matrix", "ActivityDateInserted": "2026-08-01 20:00:00",
          "PlaybackDuration": "1800", "Client": "Jellyfin Web", "DeviceName": "Chrome"}
zaloha = [PLATNY, dict(PLATNY), "neni slovnik",
          {"Id": "r2", "UserId": "u1", "NowPlayingItemId": "film-x", "PlaybackDuration": "900"},
          dict(PLATNY, Id="r3", PlaybackDuration="abc")]
vysledek = asyncio.run(importers.import_jellystat_json(json.dumps(zaloha).encode(), min_seconds=60))
check(vysledek.get("found") == 5, f"našlo se pět záznamů ({vysledek.get('found')})")
check(vysledek.get("imported") == 1, f"naimportoval se jeden ({vysledek.get('imported')})")
check(vysledek.get("duplicate") == 1, f"druhý stejný je duplicita ({vysledek.get('duplicate')})")
check(vysledek.get("too_short") == 1,
      f"nečitelná délka = 0 s = příliš krátký ({vysledek.get('too_short')})")
check(vysledek.get("known_elsewhere") == 0 and vysledek.get("repaired") == 0,
      "ne-slovník a záznam bez času se tiše přeskočí")

print()
print("--- import Jellystatu: oprava starého importu, záznam v souboru dvakrát ---")
# Starý import vzal id seriálu místo dílu. Tentýž řádek zálohy je teď
# v souboru dvakrát, s id dílu: první se opraví na místě, druhý už je
# duplicita. Kdyby oprava měnila jen kopii známých klíčů, opravila by se
# dvakrát a druhý by se tvářil jako nový.
with db.connect() as conn:
    conn.execute("INSERT INTO playback (session_key, user_id, user_name, item_id, item_name, started_at,"
                 " last_seen_at, ended_at, watched_seconds, paused_seconds, is_paused, is_active)"
                 " VALUES ('import:jst:r9:serial-z', 'u1', 'Jana', 'serial-z', 'Seriál',"
                 " '2026-07-02 20:00:00', '2026-07-02 20:00:00', '2026-07-02 20:00:00', 2400, 0, 0, 0)")
OPRAVA = dict(PLATNY, Id="r9", NowPlayingItemId="serial-z", NowPlayingItemName="Seriál",
              SeriesName="Seriál", EpisodeId="dil-z", EpisodeName="Pilot",
              ActivityDateInserted="2026-07-02 20:00:00", PlaybackDuration="2400")
vysledek = asyncio.run(importers.import_jellystat_json(json.dumps([OPRAVA, dict(OPRAVA)]).encode(),
                                                       min_seconds=60))
check(vysledek.get("repaired") == 1 and vysledek.get("duplicate") == 1 and vysledek.get("imported") == 0,
      f"jednou opraveno, podruhé duplicita, nic nového ({vysledek.get('repaired')}, "
      f"{vysledek.get('duplicate')}, {vysledek.get('imported')})")
klice = [r["session_key"] for r in db.query_all(
    "SELECT session_key FROM playback WHERE session_key LIKE 'import:jst:r9:%'")]
check(klice == ["import:jst:r9:dil-z"], f"v historii je jeden řádek a ukazuje na díl ({klice})")


print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
