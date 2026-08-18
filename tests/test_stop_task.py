# -*- coding: utf-8 -*-
"""Zastavení běžící úlohy nesmí nechat databázi v nepořádku.

Úloha se **nepřerušuje uprostřed práce**. Nastaví se jen příznak a smyčka
si ho všimne, až dodělá rozdělanou položku — teprve pak skončí. Tvrdé
přerušení (`task.cancel()`) by mohlo přijít uprostřed zápisu a nechat po
sobě poloviční dávku.

Nejnebezpečnější místo je `_mark_missing()`: ten po synchronizaci označí
za zmizelé všechno, co se v tomhle běhu nevidělo. Když se ale běh zastaví
v půlce, „nevidělo se" většina knihovny — a bez ochrany by tituly, které
v Jellyfinu normálně jsou, zmizely do archivu. Přesně tomu se tenhle test
věnuje nejvíc.
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
# Vlastní domeček: bez něj by DATABASE_PATH přebil uložený výběr
# databáze v data/database.json a test by běžel proti ostré databázi.
os.environ["JELLYSCOPE_HOME"] = str(_tmp)
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "stop.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic"

from jellyscope import db, scanner  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


# Kolik položek stihne projít, než někdo zmáčkne Zastavit.
STOP_PO = 250
CELKEM = 1000


class FalesnyKlient:
    """Jellyfin, který vrací 1000 položek - a po 250. se „zmáčkne" Zastavit."""

    def __init__(self, *a: Any, **k: Any) -> None:
        pass

    async def __aenter__(self) -> "FalesnyKlient":
        return self

    async def __aexit__(self, *a: Any) -> bool:
        return False

    async def users(self) -> list[dict[str, Any]]:
        return [{"Id": "u1", "Name": "Karel", "Policy": {}}]

    async def virtual_folders(self) -> list[dict[str, Any]]:
        return [{"ItemId": "lib-filmy", "Name": "Filmy", "CollectionType": "movies"}]

    async def item_count(self, item_types: str = "", parent_id: str | None = None) -> int:
        # Tohle se Jellyfinu ptáme předem, aby šel ukázat průběh.
        return CELKEM

    async def iter_items(self, parent_id: str):        # noqa: ANN201
        for i in range(CELKEM):
            if i == STOP_PO:
                # Tohle dělá uživatel kliknutím na Zastavit.
                scanner.request_stop()
            yield {
                "Id": f"film-{i:04d}",
                "Name": f"Film {i}",
                "Type": "Movie",
                "DateCreated": "2026-01-01T00:00:00.0000000Z",
                "RunTimeTicks": 60_000_000_000,
            }


db.init_db()

# Knihovna, jak vypadá po dřívější úspěšné synchronizaci. Tyhle položky
# v Jellyfinu pořád jsou - zastavený běh je nesmí označit za zmizelé.
with db.connect() as conn:
    conn.execute(
        "INSERT INTO libraries (id, name, collection_type, synced_at) VALUES (?,?,?,?)",
        ("lib-filmy", "Filmy", "movies", db.utcnow()),
    )
    for i in range(CELKEM):
        conn.execute(
            """INSERT INTO items (id, name, type, library_id, date_created,
                                  is_missing, synced_at)
               VALUES (?,?,'Movie','lib-filmy',?,0,?)""",
            (f"film-{i:04d}", f"Film {i}", "2026-01-01 00:00:00",
             "2020-01-01 00:00:00"),
        )

check(db.query_value("SELECT COUNT(*) FROM items WHERE is_missing = 1") == 0,
      "na začátku není nic označené jako zmizelé")


print()
print("--- synchronizace, kterou uprostřed zastavíme ---")
scanner.JellyfinClient = FalesnyKlient          # type: ignore[assignment]
vysledek = asyncio.run(scanner.sync_library())

check(vysledek.get("status") == "stopped", f"úloha hlásí zastavení: {vysledek}")
check(vysledek.get("items") is not None and vysledek["items"] < CELKEM,
      f"neprošla celá knihovna ({vysledek.get('items')} z {CELKEM})")
check(vysledek.get("items", 0) >= STOP_PO,
      f"ale rozdělaná dávka se dodělala ({vysledek.get('items')} >= {STOP_PO})")


print()
print("--- a hlavně: knihovna zůstala v pořádku ---")
zmizele = db.query_value("SELECT COUNT(*) FROM items WHERE is_missing = 1")
check(zmizele == 0, f"žádný titul se neoznačil za zmizelý ({zmizele})")
check(db.query_value("SELECT COUNT(*) FROM items") == CELKEM,
      "počet titulů se nezměnil")
# Co se stihlo, je uložené - i s novým časem synchronizace.
aktualizovane = db.query_value(
    "SELECT COUNT(*) FROM items WHERE synced_at > '2021-01-01'")
check(aktualizovane >= STOP_PO,
      f"co se stihlo, je uložené ({aktualizovane} položek)")


print()
print("--- záznam v protokolu to říká nahlas ---")
zaznam = scanner.last_scan("library")
check(zaznam is not None and zaznam["status"] == "stopped",
      f"stav úlohy je 'stopped' ({zaznam['status'] if zaznam else '-'})")
check(zaznam is not None and "Zastaveno" in (zaznam["message"] or ""),
      f"a zpráva to vysvětluje: {zaznam['message'] if zaznam else '-'}")


print()
print("--- příznak po sobě nic nenechá ---")
check(not scanner.stop_requested(),
      "příznak se po skončení úlohy vyčistil (jinak by další úloha hned skončila)")
check(not scanner.is_scan_running(), "zámek je uvolněný")

# Druhý běh - už nic nezastavuje - musí projít celý.
class KlientBezZastaveni(FalesnyKlient):
    async def iter_items(self, parent_id: str):       # noqa: ANN201
        for i in range(CELKEM):
            yield {
                "Id": f"film-{i:04d}", "Name": f"Film {i}", "Type": "Movie",
                "DateCreated": "2026-01-01T00:00:00.0000000Z",
                "RunTimeTicks": 60_000_000_000,
            }


scanner.JellyfinClient = KlientBezZastaveni      # type: ignore[assignment]
vysledek = asyncio.run(scanner.sync_library())
check(vysledek.get("status") == "ok", f"druhý běh proběhl celý: {vysledek.get('status')}")
check(vysledek.get("items") == CELKEM, f"prošlo všech {CELKEM} položek ({vysledek.get('items')})")


print()
print("--- zastavit nejde, když nic neběží ---")
check(scanner.request_stop() is False, "request_stop() vrátí False")
check(not scanner.stop_requested(), "a příznak nenastaví")
# Kdyby ho nastavil, další spuštěná úloha by se rovnou ukončila.
scanner.JellyfinClient = KlientBezZastaveni       # type: ignore[assignment]
vysledek = asyncio.run(scanner.sync_library())
check(vysledek.get("status") == "ok", "další úloha se kvůli tomu neukončila")


print()
print("--- totéž u technické analýzy ---")
# Tady se soubory zpracovávají po několika najednou (semafor). Zastavení
# musí nechat rozpracované doběhnout a jen přeskočit ty, na které nedošlo -
# jinak by v databázi zůstal soubor analyzovaný napůl.
from jellyscope import probe  # noqa: E402

with db.connect() as conn:
    for i in range(CELKEM):
        conn.execute("UPDATE items SET path = ? WHERE id = ?",
                     (f"/media/film-{i:04d}.mkv", f"film-{i:04d}"))

bezici = {"soucasne": 0, "max": 0, "hotovo": 0}


async def falesne_probe(path: str, binarka: str) -> dict[str, Any]:
    bezici["soucasne"] += 1
    bezici["max"] = max(bezici["max"], bezici["soucasne"])
    await asyncio.sleep(0.002)
    bezici["hotovo"] += 1
    if bezici["hotovo"] == 40:
        scanner.request_stop()
    bezici["soucasne"] -= 1
    return {"container": "mkv", "video_codec": "h264", "size_bytes": 1}


probe.find_ffprobe = lambda p="": "/usr/bin/ffprobe"     # type: ignore[assignment]
probe.probe_file = falesne_probe                          # type: ignore[assignment]
db.set_setting("tech_source", "ffprobe")

vysledek = asyncio.run(scanner.run_tech_scan(only_missing=True))
check(vysledek.get("status") == "stopped", f"analýza hlásí zastavení: {vysledek.get('status')}")
check(vysledek.get("skipped", 0) > 0, f"něco se přeskočilo ({vysledek.get('skipped')})")
check(bezici["soucasne"] == 0, "žádný soubor nezůstal rozpracovaný")

ulozeno = db.query_value("SELECT COUNT(*) FROM items WHERE tech_source = 'ffprobe'")
check(ulozeno == vysledek["ok"],
      f"uložilo se přesně tolik, kolik se dokončilo ({ulozeno} = {vysledek['ok']})")
check(ulozeno + vysledek["skipped"] + vysledek["failed"] == CELKEM,
      "hotové + přeskočené + chybné = všechny soubory")

zaznam = scanner.last_scan("tech")
check(zaznam is not None and zaznam["status"] == "stopped", "protokol říká 'stopped'")
check(zaznam is not None and "naváže" in (zaznam["message"] or ""),
      "a zmiňuje, že příští analýza naváže")


print()
print("--- stránka si počká a obnoví se sama ---")
# Po restartu i po zastavení úlohy se dřív muselo obnovovat ručně - a co
# hůř, nebylo poznat, kdy už to jde. `?wait=...` řekne stránce, na co má
# počkat; obsluha je v base.html a ptá se /health.
from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts  # noqa: E402
from jellyscope.web import app  # noqa: E402

accounts.create("spravce", "dlouheheslo", is_admin=True)
klient = TestClient(app)
klient.post("/login", data={"username": "spravce", "password": "dlouheheslo"})

for adresa, ocekavano in (
    ("/settings?section=tasks&wait=task", 'data-wait-for="task"'),
    ("/settings?section=general&wait=restart", 'data-wait-for="restart"'),
):
    html = klient.get(adresa).text
    check(ocekavano in html, f"{adresa} nastaví čekání")
    check("wait-note" in html, "a vysvětlí, na co se čeká")

# Bez parametru se nečeká na nic. Tohle je zároveň pojistka proti
# nekonečné smyčce: po obnovení se `wait` z adresy zahodí, takže se
# čekání nespustí znovu.
check("data-wait-for" not in klient.get("/settings?section=tasks").text,
      "bez parametru se nečeká")
# Do stránky se z adresy nesmí dostat nic jiného než ty dvě hodnoty.
for podvrh in ("cokoliv", "<script>", "restart; drop"):
    html = klient.get("/settings?section=tasks", params={"wait": podvrh}).text
    check("data-wait-for" not in html, f"neznámá hodnota se ignoruje: {podvrh!r}")

# /health musí nést to, na co se čeká.
stav = klient.get("/health").json()
for klic in ("started_at", "task_running", "stop_pending"):
    check(klic in stav, f"/health hlásí {klic}")
check(stav["task_running"] is False, "a teď nic neběží")


print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
