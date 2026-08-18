# -*- coding: utf-8 -*-
r"""Do knihovny patří jen to, co má soubor - film a díl.

Chyba, kterou to hlídá: dohledání osiřelých záznamů v Jellyfinu
(`importers.zaloz_z_jellyfinu`) zakládalo položku z čehokoliv, co Jellyfin
vrátil. Když záznam historie visel na id **seriálu** - a to se u převzaté
historie stává - vznikla položka druhu "Series".

Jenže synchronizace se Jellyfinu ptá jen na `Movie,Episode`. Takovou
položku tedy nikdy neuviděla a `_mark_missing()` ji při každém běhu
označila za zmizelou. V knihovně pak stál "archivovaný" seriál, který
v Jellyfinu normálně je - a vedle něj ten správný, poskládaný z dílů.
Uživatel to vidí jako "seriály samy padají do archivu a znovu se detekují".

Filmů se to netýkalo: id filmu v historii vrátí položku druhu "Movie",
kterou synchronizace zná.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_fantom_serialu.py
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "fantom.db")
os.environ["SECRET_KEY"] = "testovaci-klic"

from jellyscope import db, importers, jellyfin, scanner  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


db.init_db()

SERIAL_ID = "6a6152eddb1c02ccb5c3887497e3e64d"
DIL_ID = "aa11bb22cc33dd44ee55ff6677889900"


def zaznam(item_id: str, jmeno: str, serial: str | None = None) -> None:
    """Jeden záznam historie, který na nic v knihovně nevede."""
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO playback (session_key, user_id, user_name, item_id,"
            " item_name, series_name, item_type, started_at, last_seen_at,"
            " ended_at, watched_seconds) VALUES (?, 'u1', 'Jana', ?, ?, ?,"
            " 'Episode', datetime('now','-16 days'), datetime('now','-16 days'),"
            " datetime('now','-16 days'), 1800)",
            (f"s-{item_id}", item_id, jmeno, serial),
        )


print("--- co se ptáme Jellyfinu a co si vedeme v knihovně, musí sedět ---")
# Kdyby se tyhle dva seznamy rozešly, chyba se vrátí v jiné podobě:
# položka druhu, na který se neptáme, spadne do archivu při každém běhu.
import inspect  # noqa: E402

podpis = inspect.signature(jellyfin.JellyfinClient.items_page)
ptame_se = str(podpis.parameters["item_types"].default)
check(set(ptame_se.split(",")) == set(scanner.SPRAVOVANE_TYPY),
      f"IncludeItemTypes ({ptame_se}) = SPRAVOVANE_TYPY {scanner.SPRAVOVANE_TYPY}")


print()
print("--- záznam visící na id SERIÁLU ---")
zaznam(SERIAL_ID, "Ve službě: Za mřížemi")

serial = {
    "Id": SERIAL_ID,
    "Name": "Ve službě: Za mřížemi",
    "Type": "Series",
    "Path": "/media/serialy/Ve sluzbe za mrizemi",
    "ProviderIds": {"Tmdb": "123456"},
}
vysledek = importers.zaloz_z_jellyfinu([serial])

check(db.query_one("SELECT id FROM items WHERE id = ?", (SERIAL_ID,)) is None,
      "seriál se do knihovny NEZALOŽÍ")
check(vysledek["zalozeno"] == 0, f"a nehlásí se jako založený ({vysledek})")

# Co z toho ale jde vytěžit: jméno seriálu. Tím se díl v přehledech
# přestane tvářit jako samostatný film.
radek = db.query_one("SELECT series_name FROM playback WHERE item_id = ?",
                     (SERIAL_ID,))
check(radek["series_name"] == "Ve službě: Za mřížemi",
      f"do záznamu se doplní jméno seriálu ({radek['series_name']!r})")
check(vysledek["doplneno"] == 1, "a hlásí se to zvlášť")


print()
print("--- záznam visící na id DÍLU (tohle fungovat musí dál) ---")
zaznam(DIL_ID, "7. epizoda")

dil = {
    "Id": DIL_ID,
    "Name": "7. epizoda",
    "Type": "Episode",
    "SeriesName": "Ve službě: Za mřížemi",
    "ParentIndexNumber": 1,
    "IndexNumber": 7,
    "Path": "/media/serialy/Ve sluzbe za mrizemi/S01E07.mkv",
    "ProviderIds": {"Tmdb": "123456"},
}
vysledek = importers.zaloz_z_jellyfinu([dil])
ulozeny = db.query_one("SELECT id, type FROM items WHERE id = ?", (DIL_ID,))
check(ulozeny is not None and ulozeny["type"] == "Episode",
      "díl se do knihovny založí, jak se má")
check(vysledek["zalozeno"] == 1, f"a hlásí se jako založený ({vysledek})")


print()
print("--- synchronizace už nemá co archivovat ---")
# Razítko z budoucnosti = "tenhle běh nic z toho neviděl". Přesně to dělá
# plná synchronizace na svém konci.
scanner._mark_missing("2099-01-01 00:00:00")
archivovanych = db.query_value(
    "SELECT COUNT(*) FROM items WHERE is_missing = 1", default=0)
# Díl archivovaný bude - ten synchronizace opravdu neviděla, protože
# tenhle test žádnou nespouští. Jde o to, že seriál v tabulce není.
check(db.query_one("SELECT id FROM items WHERE id = ?", (SERIAL_ID,)) is None,
      f"v knihovně není co archivovat navíc (archivovaných: {archivovanych})")


print()
print("--- úklid položek, které vznikly dřív, než se to opravilo ---")
with db.connect() as conn:
    conn.execute(
        "INSERT INTO items (id, name, type, is_missing, synced_at)"
        " VALUES ('stary-serial', 'Starý fantom', 'Series', 1, '2026-01-01 00:00:00')")
    conn.execute(
        "INSERT INTO items (id, name, type, is_missing, synced_at)"
        " VALUES ('stara-rada', 'Řada 1', 'Season', 1, '2026-01-01 00:00:00')")
    conn.execute(
        "INSERT INTO item_streams (item_id, stream_index, type)"
        " VALUES ('stary-serial', 0, 'Video')")

smazano = scanner.uklid_fantomu()
check(smazano == 2, f"úklid najde a smaže obojí ({smazano})")
check(db.query_one("SELECT id FROM items WHERE id = 'stary-serial'") is None,
      "seriál je z knihovny pryč")
check(db.query_one("SELECT id FROM items WHERE id = 'stara-rada'") is None,
      "řada taky")
check(db.query_value("SELECT COUNT(*) FROM item_streams"
                     " WHERE item_id = 'stary-serial'", default=0) == 0,
      "a nezůstanou po něm ani stopy v item_streams")
check(db.query_one("SELECT id FROM items WHERE id = ?", (DIL_ID,)) is not None,
      "poctivého dílu se úklid nedotkne")
check(scanner.uklid_fantomu() == 0, "podruhé už nemá co dělat")

# Historie zůstává. Je to platný záznam o tom, co kdo díval - jen k němu
# nic v knihovně nevede, což byla pravda i předtím.
check(db.query_value("SELECT COUNT(*) FROM playback WHERE item_id = ?",
                     (SERIAL_ID,), default=0) == 1,
      "záznam historie zůstal, jen je zase osiřelý")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
