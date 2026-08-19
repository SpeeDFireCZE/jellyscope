# -*- coding: utf-8 -*-
r"""Jellystat u epizody posílá dvě různá id - vzít se musí to správné.

Kořen problému, který se dlouho projevoval jinde: v záloze Jellystatu má
záznam o epizodě

    NowPlayingItemId  = id SERIÁLU     ("Tým SEAL")
    EpisodeId         = id DÍLU        (to, co chceme)
    NowPlayingItemName = jméno SERIÁLU
    EpisodeName        = jméno dílu

Import bral to první. Celá historie seriálu tak skončila na jediném
identifikátoru, ke kterému v knihovně nic nevede, a jmenovala se jménem
seriálu - takže z ní **nešlo dopočítat, o který díl šlo**. Uživatel to
viděl jako „dohledání osiřelých nic nedělá": ono opravdu nebylo z čeho.

Druhá polovina testu hlídá opravu už naimportovaných dat: když se tatáž
záloha nahraje znovu, staré záznamy se mají **opravit na místě**, ne
zdvojit. Klíč `import:jst:<id řádku>:<id položky>` se totiž opravou mění,
takže bez toho by vznikla druhá kopie každého přehrávání.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_jellystat_epizody.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "jst.db")
os.environ["SECRET_KEY"] = "testovaci-klic"

from jellyscope import db, importers  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


db.init_db()

SERIAL_ID = "b31ca2733b47b9f87736375722ef85a0"
DIL_ID = "aa11bb22cc33dd44ee55ff6677889900"

# Díl v knihovně - ať je vidět, že se záznam na něco naváže.
with db.connect() as conn:
    conn.execute(
        "INSERT INTO items (id, name, type, series_id, series_name,"
        " parent_index_number, index_number, is_missing, synced_at)"
        " VALUES (?, 'Nasazení', 'Episode', ?, 'Tým SEAL', 2, 5, 0, ?)",
        (DIL_ID, SERIAL_ID, db.utcnow()),
    )


def zaloha(id_radku: str, s_dilem: bool) -> bytes:
    """Jeden řádek zálohy Jellystatu - s id dílu, nebo bez něj."""
    zaznam = {
        "Id": id_radku,
        "UserId": "u1",
        "UserName": "Jana",
        "NowPlayingItemId": SERIAL_ID,
        "NowPlayingItemName": "Tým SEAL",
        "SeriesName": "Tým SEAL",
        "ActivityDateInserted": "2026-08-01 20:00:00",
        "PlaybackDuration": "1800",
        "Client": "Jellyfin Web",
        "DeviceName": "Chrome",
    }
    if s_dilem:
        zaznam["EpisodeId"] = DIL_ID
        zaznam["EpisodeName"] = "Nasazení"
    return json.dumps([zaznam]).encode("utf-8")


print("--- záznam s EpisodeId se naváže na díl, ne na seriál ---")
vysledek = asyncio.run(importers.import_jellystat_json(zaloha("r1", True), min_seconds=60))
check(vysledek["status"] == "ok" and vysledek["imported"] == 1,
      f"naimportováno ({vysledek['status']}, {vysledek.get('imported')})")

radek = db.query_one("SELECT item_id, item_name, series_name, item_type"
                     " FROM playback WHERE user_id = 'u1'")
check(radek["item_id"] == DIL_ID,
      f"visí na id DÍLU, ne seriálu ({radek['item_id']})")
check(radek["item_name"] == "Nasazení",
      f"a jmenuje se jménem dílu ({radek['item_name']})")
check(radek["series_name"] == "Tým SEAL",
      f"jméno seriálu se neztratilo ({radek['series_name']})")
# Tohle je ta pointa: takový záznam už není osiřelý.
check(importers.orphan_playback_count() == 0, "a není osiřelý")


print()
print("--- starý import (bez EpisodeId) skončí u seriálu ---")
with db.connect() as conn:
    conn.execute("DELETE FROM playback")

asyncio.run(importers.import_jellystat_json(zaloha("r2", False), min_seconds=60))
radek = db.query_one("SELECT item_id, session_key FROM playback WHERE user_id = 'u1'")
check(radek["item_id"] == SERIAL_ID,
      f"bez EpisodeId zbývá jen id seriálu ({radek['item_id']})")
check(importers.orphan_playback_count() == 1, "takový záznam je osiřelý")
stary_klic = radek["session_key"]


print()
print("--- opakovaný import tutéž zálohu OPRAVÍ, nezdvojí ---")
vysledek = asyncio.run(importers.import_jellystat_json(zaloha("r2", True), min_seconds=60))
check(vysledek.get("repaired") == 1, f"hlásí opravu ({vysledek.get('repaired')})")
check(vysledek["imported"] == 0, "a nic nového nevkládá")
check(db.query_value("SELECT COUNT(*) FROM playback") == 1,
      f"v historii je pořád jeden záznam "
      f"({db.query_value('SELECT COUNT(*) FROM playback')})")

radek = db.query_one("SELECT item_id, item_name, session_key FROM playback")
check(radek["item_id"] == DIL_ID, f"a ukazuje na díl ({radek['item_id']})")
check(radek["session_key"] != stary_klic, "klíč se opravou změnil")
check(importers.orphan_playback_count() == 0, "osiřelý už není")

# Potřetí už nemá co dělat.
vysledek = asyncio.run(importers.import_jellystat_json(zaloha("r2", True), min_seconds=60))
check(vysledek.get("repaired") == 0 and vysledek["imported"] == 0,
      f"potřetí nedělá nic ({vysledek.get('repaired')}, {vysledek['imported']})")
check(db.query_value("SELECT COUNT(*) FROM playback") == 1, "a nezdvojí to")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
