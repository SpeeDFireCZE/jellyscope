# -*- coding: utf-8 -*-
r"""Odkud se bere volné místo na disku.

Původně se ptalo `shutil.disk_usage` na disk, kde běží **Jellyscope**.
Jenže data bývají jinde – na NASu, na jiném stroji, v jiném kontejneru –
a pak se měřil úplně cizí disk a odhad „místo dojde za X dnů" mluvil
o něčem jiném.

Tři zdroje, v tomhle pořadí:

1. **Ručně zadaná kapacita.** U knihovny v cloudu ji nepozná ani Jellyfin;
   správce ji ví. Jeho číslo přebíjí všechno.
2. **Jellyfin.** Sedí u těch souborů, takže se ptáme jeho.
3. **Disk pod aplikací.** Původní způsob; platí jen když data leží tady.

Spuštění:
    .\\.venv\\Scripts\\python.exe tests\\test_volne_misto.py
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "misto.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYSCOPE_DEMO"] = "0"

from jellyscope import db, scanner  # noqa: E402

failures = 0
GB = 1024 ** 3


def check(podminka: bool, popis: str, detail: str = "") -> None:
    global failures
    print(f"{'OK    ' if podminka else 'CHYBA '} {popis} {detail}")
    if not podminka:
        failures += 1


db.init_db()

print("--- čtení odpovědi Jellyfinu ---")
# Slozka knihovny se najde podle cesty.
odpoved = {"Folders": [
    {"Path": "/data/serialy", "FreeSpace": 100 * GB, "TotalSpace": 500 * GB},
    {"Path": "/data/filmy", "FreeSpace": 700 * GB, "TotalSpace": 4000 * GB},
]}
misto = scanner.misto_z_jellyfinu(odpoved, "/data/filmy/Duna/Duna.mkv")
check(misto.get("volne") == 700 * GB,
      f"vybere se složka, ve které knihovna leží ({misto})")
check(misto.get("celkem") == 4000 * GB, "i s celkovou velikostí")

# Kdyz zadna nesedi, plati ta nejtesnejsi - misto dojde na ni.
misto = scanner.misto_z_jellyfinu(odpoved, "/uplne/jinde/film.mkv")
check(misto.get("volne") == 100 * GB,
      f"jinak ta s nejmenším volným místem ({misto})")

# Windows a Linux zapisuji cesty jinak; velikost pismen taky nema
# rozhodovat o tom, jestli se disk najde.
misto = scanner.misto_z_jellyfinu(
    {"Folders": [{"Path": "D:\\Media\\Filmy", "FreeSpace": 42 * GB}]},
    "d:/media/filmy/a.mkv")
check(misto.get("volne") == 42 * GB, f"lomítka ani velikost písmen nevadí ({misto})")

# Co neni cislo, se nebere. Hadat hodnotu, ze ktere se pocita "dojde za
# X dnu", by bylo horsi nez priznat, ze ji nezname.
for nesmysl in ({}, None, {"Folders": []}, {"Folders": [{"Path": "/x"}]},
                {"Folders": [{"Path": "/x", "FreeSpace": "hodně"}]},
                {"Folders": [{"Path": "/x", "FreeSpace": 0}]}):
    check(scanner.misto_z_jellyfinu(nesmysl, "/x") == {},
          f"z {str(nesmysl)[:34]} se nic nevymýšlí")

print()
print("--- pořadí zdrojů ---")


def polozka(cislo: int, velikost: int, cesta: str = "/data/filmy/f.mkv") -> None:
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO items (id, name, type, path, size_bytes, is_missing)"
            " VALUES (?,?, 'Movie', ?, ?, 0)",
            (f"i{cislo}", f"Film {cislo}", cesta, velikost))
        conn.commit()


for cislo in range(3):
    polozka(cislo, 100 * GB)          # knihovna má 300 GB

# 1) Bez čehokoliv se sáhne na disk pod aplikací.
volne = scanner._volne_misto_knihovny()
check(db.get_setting(scanner.ZDROJ_KLIC, "") in ("disk", ""),
      f"bez jiného zdroje se čte disk pod aplikací ({db.get_setting(scanner.ZDROJ_KLIC, '')})")

# 2) Údaj z Jellyfinu má přednost před ním.
db.set_setting(scanner.JF_VOLNE_KLIC, str(900 * GB))
db.forget_settings()
check(scanner._volne_misto_knihovny() == 900 * GB,
      "údaj z Jellyfinu přebije disk pod aplikací")
check(db.get_setting(scanner.ZDROJ_KLIC, "") == "jellyfin", "a je to vidět na zdroji")

# 3) Ručně zadaná kapacita přebije i Jellyfin.
db.set_setting(scanner.KAPACITA_KLIC, str(1000 * GB))
db.forget_settings()
volne = scanner._volne_misto_knihovny()
check(volne == 700 * GB,
      f"kapacita minus knihovna = 1000 - 300 GB ({volne / GB:.0f} GB)")
check(db.get_setting(scanner.ZDROJ_KLIC, "") == "rucne", "a zdroj to říká")

# Knihovna větší než zadaná kapacita nesmí dát záporné místo.
db.set_setting(scanner.KAPACITA_KLIC, str(100 * GB))
db.forget_settings()
check(scanner._volne_misto_knihovny() == 0,
      "knihovna větší než kapacita znamená nula, ne záporné číslo")

# Nula znamená "zjisti si to sám", ne "nula bajtů volných".
db.set_setting(scanner.KAPACITA_KLIC, "0")
db.forget_settings()
check(scanner._volne_misto_knihovny() == 900 * GB,
      "nula vypne ruční hodnotu a platí zase Jellyfin")

print()
print("--- co se uloží po synchronizaci ---")
db.set_setting(scanner.JF_VOLNE_KLIC, "")
db.set_setting(scanner.JF_CELKEM_KLIC, "")
db.forget_settings()
scanner.zapamatuj_misto_z_jellyfinu(
    {"Folders": [{"Path": "/data/filmy", "FreeSpace": 55 * GB,
                  "TotalSpace": 900 * GB}]})
db.forget_settings()
check(db.get_setting(scanner.JF_VOLNE_KLIC, "") == str(55 * GB),
      "volné místo se uloží pro pozdější zápis snímku")
check(db.get_setting(scanner.JF_CELKEM_KLIC, "") == str(900 * GB),
      "a celková velikost taky")

# Cesta knihovny se bere z NEJVETSI polozky - tam se meri disk, na kterem
# knihovna doopravdy lezi.
polozka(99, 500 * GB, "/jiny/disk/velky.mkv")
scanner.zapamatuj_misto_z_jellyfinu(
    {"Folders": [{"Path": "/data/filmy", "FreeSpace": 10 * GB},
                 {"Path": "/jiny/disk", "FreeSpace": 20 * GB}]})
db.forget_settings()
check(db.get_setting(scanner.JF_VOLNE_KLIC, "") == str(20 * GB),
      "hledá se disk největšího souboru, ne první složka v odpovědi")

print()
print("--- stránka ---")
from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts  # noqa: E402
from jellyscope.web import app  # noqa: E402

accounts.create("spravce", "dlouheheslo", is_admin=True)
with TestClient(app) as client:
    client.post("/login", data={"username": "spravce", "password": "dlouheheslo"},
                follow_redirects=False)
    stranka = client.get("/settings?section=data")
    check(stranka.status_code == 200, "Sběr dat se načte")
    check('name="library_capacity_gb"' in stranka.text, "a má pole pro kapacitu")

    client.post("/settings", follow_redirects=False, data={
        "tech_source": "jellyfin", "poll_interval": "10",
        "ffprobe_concurrency": "3", "path_mappings": "[]",
        "library_capacity_gb": "4000"})
    db.forget_settings()
    check(db.get_int_setting(scanner.KAPACITA_KLIC, 0, 10 ** 18, 0) == 4000 * GB,
          "zadané GB se uloží jako bajty")
    html = client.get("/settings?section=data").text
    check('value="4000"' in html, "a do formuláře se vrátí zase v GB")

    # Nesmysl se srovná, ne uloží.
    client.post("/settings", follow_redirects=False, data={
        "tech_source": "jellyfin", "poll_interval": "10",
        "ffprobe_concurrency": "3", "path_mappings": "[]",
        "library_capacity_gb": "-9"})
    db.forget_settings()
    check(db.get_int_setting(scanner.KAPACITA_KLIC, 0, 10 ** 18, 0) == 0,
          "záporná kapacita se srovná na nulu")

print()
print("--- starší Jellyfin endpoint nemá ---")
import asyncio  # noqa: E402

from jellyscope.jellyfin import JellyfinClient, JellyfinError  # noqa: E402


async def _zkus() -> dict:
    klient = JellyfinClient("http://127.0.0.1:1", "k")

    async def _spadni(cesta, params=None):
        raise JellyfinError("404")

    klient._get = _spadni                       # type: ignore[assignment]
    try:
        return await klient.storage()
    finally:
        await klient.close()


check(asyncio.run(_zkus()) == {},
      "chybějící endpoint vrátí prázdno, ne výjimku - synchronizace běží dál")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
