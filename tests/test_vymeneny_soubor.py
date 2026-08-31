# -*- coding: utf-8 -*-
r"""Vyměněný soubor si nesmí nechat technická data toho starého.

Když v Jellyfinu nahradíš soubor novým (jiné rozlišení, jiný kodek),
vznikne nové ItemId. Jellyscope pozná, že jde o tentýž titul, a sloučí
je - historie zůstane. U toho ale položku jen **přejmenuje** na nové id,
takže jí zůstanou i technická data: kodek, rozlišení, velikost, jazyky
a hlavně `tech_source='ffprobe'`.

A právě to poslední je past. Analýza souborů bere jen položky, které
technická data ještě nemají; tahle je má, takže ji přeskočí - navždy.
Na detailu pak svítí údaje starého souboru (nebo prázdno, když se starý
nikdy nezměřil) a jediné, co pomůže, je ruční „Načíst metadata znovu".

Po sloučení se proto technická data mažou. Je to jiný soubor.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_vymeneny_soubor.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "vymena.db")
os.environ["SECRET_KEY"] = "testovaci-klic"

from jellyscope import db, scanner  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


db.init_db()


def stary_film(item_id: str, tmdb: str) -> None:
    """Film, ktery uz je zmereny - jak by ho nechala analyza souboru."""
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO items (id, name, type, is_missing, synced_at, path, tmdb_id,"
            " container, video_codec, audio_codec, width, height, bitrate, size_bytes,"
            " audio_languages, default_audio_language, tech_source, tech_updated_at)"
            " VALUES (?, 'Duna', 'Movie', 0, '2026-01-01 00:00:00', '/data/duna-720p.mkv',"
            " ?, 'matroska', 'h264', 'ac3', 1280, 720, 4000000, 2000000000,"
            " 'cs,en', 'cs', 'ffprobe', '2026-01-01 00:00:00')",
            (item_id, tmdb))
        conn.execute(
            "INSERT INTO item_streams (item_id, stream_index, type, codec, language)"
            " VALUES (?, 1, 'Audio', 'ac3', 'cs')", (item_id,))
        conn.execute(
            "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name,"
            " started_at, last_seen_at, watched_seconds, paused_seconds)"
            " VALUES (?, 'u1', 'Pepa', ?, 'Duna', '2026-01-02 20:00:00',"
            " '2026-01-02 22:00:00', 7200, 0)", ('s-' + item_id, item_id))


print("--- soubor se vyměnil za nový (jiné ItemId) ---")
stary_film("stare-id", "tmdb-438631")
scanner._merge_by_tmdb([(("tmdb-438631", -1, -1), "nove-id")], chranena={"nove-id"})

radek = db.query_one("SELECT * FROM items WHERE id = 'nove-id'")
check(radek is not None, "položka nese nové id")

prehrani = db.query_value("SELECT COUNT(*) FROM playback WHERE item_id = 'nove-id'")
check(prehrani == 1, f"historie se přenesla ({prehrani})")

print()
print("--- ale technická data zůstat nesmí ---")
check(radek["tech_source"] is None,
      f"zdroj technických dat je zase prázdný ({radek['tech_source']})")
check(radek["video_codec"] is None and radek["height"] is None,
      f"kodek ani rozlišení tam nejsou ({radek['video_codec']}, {radek['height']})")
check(radek["audio_languages"] is None,
      f"ani jazyky ({radek['audio_languages']})")
check(radek["size_bytes"] is None, "ani velikost - nový soubor je jiný")
stopy = db.query_value("SELECT COUNT(*) FROM item_streams WHERE item_id = 'nove-id'")
check(stopy == 0, f"stopy starého souboru jsou pryč ({stopy})")

print()
print("--- a analýza souborů si ji vezme ---")
# Tohle je to, co uzivateli chybelo: dokud mela polozka tech_source,
# `only_missing=True` ji preskakovala i pri nocnim behu.
cekajici = db.query_all(
    "SELECT id FROM items WHERE is_missing = 0 AND path IS NOT NULL AND path != ''"
    " AND (tech_source IS NULL OR tech_source != 'ffprobe')")
check([r["id"] for r in cekajici] == ["nove-id"],
      f"položka čeká na změření ({[r['id'] for r in cekajici]})")

print()
print("--- celá cesta synchronizace ---")
# Rychla synchronizace slucuje a hned pak meri nove polozky
# (`run_tech_scan(only_missing=True, item_ids=...)`). Presne tudy to
# uzivateli propadlo: polozka v seznamu novych byla, ale `only_missing`
# ji preskocila, protoze po slouceni mela `tech_source` stareho souboru.
with db.connect() as conn:
    conn.execute("DELETE FROM items")
    conn.execute("DELETE FROM playback")

stary_film("stare-2", "tmdb-693134")
# Radek skladame stejnou funkci jako synchronizace, ne rucne - jinak
# by test prestal platit pri prvnim pridanem sloupci.
radek_nove = scanner._radek_polozky(
    {"Id": "nove-2", "Name": "Duna", "Type": "Movie", "ProductionYear": 2024,
     "Path": "/data/duna-2160p.mkv", "DateCreated": "2026-02-01T00:00:00Z",
     "ProviderIds": {"Tmdb": "693134"}},
    None, {}, "2026-02-01 00:00:00")

scanner._write_batch(
    [radek_nove], [], [(("tmdb-693134", -1, -1), "nove-2")],
    keep_existing_tech=True, chranena={"nove-2"})

po_synchronizaci = db.query_one(
    "SELECT tech_source, height FROM items WHERE id = 'nove-2'")
check(po_synchronizaci is not None, "položka po synchronizaci existuje")
if po_synchronizaci:
    check(po_synchronizaci["tech_source"] is None,
          f"a čeká na změření ({po_synchronizaci['tech_source']})")
check(db.query_value("SELECT COUNT(*) FROM playback WHERE item_id = 'nove-2'") == 1,
      "historie se přenesla i touhle cestou")

print()
print("--- co se nesloučilo, se nemaže ---")
# Polozka, ktera se jen znovu videla pri synchronizaci, o zmerena data
# prijit nesmi - jinak by se cela knihovna merila kazdou noc znovu.
stary_film("nedotcene-id", "tmdb-27205")
scanner._merge_by_tmdb([], chranena=set())
nedotcene = db.query_one("SELECT tech_source, height FROM items WHERE id = 'nedotcene-id'")
check(nedotcene["tech_source"] == "ffprobe", "nesloučená položka si data nechává")
check(nedotcene["height"] == 720, "včetně rozlišení")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
