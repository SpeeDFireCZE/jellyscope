# -*- coding: utf-8 -*-
"""Ukazatel průběhu úlohy a žánrový rozpad v detailu uživatele.

Dvě věci, které spolu nesouvisí, ale obě přibyly kvůli témuž: aby stránka
řekla, co se děje, místo aby jen mlčela.

  * **Průběh** – synchronizace velké knihovny trvá minuty. Celkový počet
    zjistíme předem (Jellyfin ho posílá jako `TotalRecordCount`), takže
    jde ukázat skutečná procenta, ne jen točítko.
  * **Žánry** – jeden titul jich má víc, takže se jeho čas počítá do
    každého z nich a procenta se nesčítají na sto. To musí být vidět.
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "progress.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic"

from jellyscope import charts, db, scanner, stats  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


CELKEM = 600
snimky: list[dict[str, Any]] = []


class FalesnyKlient:
    """Jellyfin, který během procházení sbírá snímky ukazatele průběhu."""

    def __init__(self, *a: Any, **k: Any) -> None:
        pass

    async def __aenter__(self) -> "FalesnyKlient":
        return self

    async def __aexit__(self, *a: Any) -> bool:
        return False

    async def users(self) -> list[dict[str, Any]]:
        return [{"Id": "u1", "Name": "Karel", "Policy": {}}]

    async def virtual_folders(self) -> list[dict[str, Any]]:
        return [{"ItemId": "lib", "Name": "Filmy", "CollectionType": "movies"}]

    async def item_count(self, item_types: str = "", parent_id: str | None = None) -> int:
        return CELKEM

    async def iter_items(self, parent_id: str):        # noqa: ANN201
        for i in range(CELKEM):
            if i % 150 == 0:
                snimky.append(scanner.progress())
            yield {
                "Id": f"film-{i:04d}", "Name": f"Film {i}", "Type": "Movie",
                "DateCreated": "2026-01-01T00:00:00.0000000Z",
                "RunTimeTicks": 60_000_000_000,
                # Jellyfin posílá žánry jako seznam. Schválně dva u každého
                # filmu, ať je vidět, že se čas počítá do obou.
                "Genres": ["Akční", "Sci-Fi"] if i % 2 else ["Drama"],
            }


db.init_db()

print("--- když nic neběží, průběh je prázdný ---")
check(scanner.progress() == {}, f"prázdné: {scanner.progress()}")


print()
print("--- synchronizace hlásí, kolik má hotovo ---")
scanner.JellyfinClient = FalesnyKlient          # type: ignore[assignment]
vysledek = asyncio.run(scanner.sync_library())
check(vysledek.get("status") == "ok", f"proběhla: {vysledek.get('status')}")

merene = [s for s in snimky if s]
check(len(merene) >= 3, f"průběh šel číst během běhu ({len(merene)}x)")
check(all(s["total"] == CELKEM for s in merene),
      f"celkový počet zná předem: {[s['total'] for s in merene]}")
check(all(s["kind"] == "library" for s in merene), "ví, která úloha běží")

hotovo = [s["done"] for s in merene]
check(hotovo == sorted(hotovo), f"hotových jen přibývá: {hotovo}")
check(hotovo[-1] > hotovo[0], f"a doopravdy se hýbe: {hotovo}")

procenta = [s["percent"] for s in merene]
check(all(0 <= p <= 100 for p in procenta), f"procenta dávají smysl: {procenta}")

check(scanner.progress() == {}, "po skončení je průběh zase prázdný")


print()
print("--- bez známého celku se procenta nevymýšlejí ---")
# Když se počet zjistit nepovede, ukazatel to musí přiznat, ne hádat.
scanner._start_progress("library", 0)
scanner._add_progress(42)
try:
    puvodni = scanner.is_scan_running
    scanner.is_scan_running = lambda: True       # type: ignore[assignment]
    stav = scanner.progress()
finally:
    scanner.is_scan_running = puvodni            # type: ignore[assignment]
check(stav["percent"] is None, f"procenta jsou None, ne 0: {stav['percent']}")
check(stav["done"] == 42, "ale hotové položky se počítají dál")
scanner._clear_progress()


print()
print("--- žánry se uložily a rozpad sedí ---")
ulozene = db.query_value(
    "SELECT COUNT(*) FROM items WHERE genres IS NOT NULL AND genres != ''")
check(ulozene == CELKEM, f"žánry má každý titul ({ulozene})")
vzorek = db.query_one("SELECT genres FROM items WHERE id = 'film-0001'")
check(vzorek["genres"] == "Akční|Sci-Fi", f"oddělené svislítkem: {vzorek['genres']}")

# Uživatel: hodinu Drama (film-0000), dvě hodiny Akční+Sci-Fi (film-0001).
with db.connect() as conn:
    for ident, sekund in (("film-0000", 3600), ("film-0001", 7200)):
        conn.execute(
            """INSERT INTO playback (session_key, user_id, user_name, item_id,
                                     item_name, started_at, last_seen_at,
                                     watched_seconds, is_active)
               VALUES (?, 'u1', 'Karel', ?, 'Film', ?, ?, ?, 0)""",
            (f"relace-{ident}", ident, db.utcnow(), db.utcnow(), sekund),
        )

zanry = {row["label"]: row for row in stats.user_genres("u1", 30)}
check(set(zanry) == {"Akční", "Sci-Fi", "Drama"}, f"tři žánry: {set(zanry)}")
check(round(zanry["Akční"]["hours"], 2) == 2.0, f"Akční 2 h ({zanry['Akční']['hours']})")
check(round(zanry["Sci-Fi"]["hours"], 2) == 2.0, "Sci-Fi taky 2 h (týž film)")
check(round(zanry["Drama"]["hours"], 2) == 1.0, "Drama 1 h")
# Součet žánrových hodin (5) je vyšší než odsledovaný čas (3) - a to je
# správně, protože se film počítá do každého svého žánru.
check(round(sum(z["hours"] for z in zanry.values())) == 5,
      "součet přes žánry je vyšší než čas - jeden titul se počítá vícekrát")
check(abs(sum(z["percent"] for z in zanry.values()) - 100) < 0.01,
      "procenta se přesto sčítají na sto (je to podíl na součtu)")


print()
print("--- koláčový graf ---")
html = charts.donut_chart(list(zanry.values()), "label", "hours")
check("donut-seg" in html, "vykreslily se výseče")
check(html.count("<circle") == 3, f"tři díly ({html.count('<circle')})")
check("Akční" in html and "40 %" in html, "legenda nese název i podíl")
check("data-tip" in html, "po najetí myší je vidět hodnota")
check("var(--series-" in html and "#" not in html.split("stroke=")[1][:20],
      "barvy jdou z proměnných, ne natvrdo")
check("Zatím" in charts.donut_chart([], "label", "hours"),
      "prázdná data řeknou, že nic není")
# Dělení nulou: samé nuly nesmí graf položit.
check("Zatím" in charts.donut_chart([{"label": "X", "hours": 0}], "label", "hours"),
      "samé nuly taky")


print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
