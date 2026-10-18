# -*- coding: utf-8 -*-
r"""Plakát a název stejného titulu jsou pro klávesnici jeden odkaz.

U plakátu v „Nově přidaných" vedl na tentýž titul obrázek i název pod
ním, stejně tak u právě běžícího přehrávání. Myší si toho nikdo nevšimne,
ale kdo stránku ovládá klávesnicí, musel u každého plakátu zmáčknout Tab
dvakrát, a čtečka obrazovky u obrázku přečetla jen „odkaz" bez názvu,
protože obrázek nemá popisek (alt="" schválně - název je hned pod ním).

Obrázek zůstává klikací myší, jen se přeskočí při Tabu (tabindex="-1")
a ve čtečce (aria-hidden="true"). Název zůstává normálním odkazem.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_plakaty_klavesnice.py
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "plakaty.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic-dostatecne-dlouhy"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_DEMO"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, db, web  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


db.init_db()
accounts.create("spravce", "dlouheheslo", is_admin=True)
vcera = (datetime.now(timezone.utc) - timedelta(days=1)).strftime(db.TIME_FORMAT)
with db.connect() as conn:
    conn.execute("INSERT INTO libraries (id, name, collection_type) VALUES ('filmy', 'Filmy', 'movies')")
    conn.execute("INSERT INTO items (id, name, type, library_id, is_missing, date_created)"
                 " VALUES ('f1', 'Pelíšky', 'Movie', 'filmy', 0, ?)", (vcera,))
    conn.execute(
        "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name, item_type,"
        " library_id, started_at, last_seen_at, watched_seconds, is_active, play_method)"
        " VALUES ('s1', 'u1', 'Jana', 'f1', 'Pelíšky', 'Movie', 'filmy', ?, ?, 60, 1, 'DirectPlay')",
        (vcera, vcera))
    conn.commit()

klient = TestClient(web.app)
klient.post("/login", data={"username": "spravce", "password": "dlouheheslo", "zpusob": "mistni"})


def odkazy(html: str, trida: str) -> list[str]:
    """Otevírací značky <a> s danou třídou."""
    return re.findall(r'<a\b[^>]*class="' + trida + r'"[^>]*>', html, re.S)


print("--- Nově přidané ---")
pas = klient.get("/partials/recently-added").text
obrazky = odkazy(pas, "poster-frame")
check(len(obrazky) == 1, f"plakát je na stránce ({len(obrazky)})")
check(all('tabindex="-1"' in a and 'aria-hidden="true"' in a for a in obrazky),
      "obrázek se při Tabu i ve čtečce přeskočí")
nazvy = odkazy(pas, "poster-name")
check(len(nazvy) == 1 and "tabindex" not in nazvy[0] and "aria-hidden" not in nazvy[0],
      "název zůstává normálním odkazem")

print()
print("--- Právě se hraje ---")
karta = klient.get("/partials/now-playing").text
obrazky = odkazy(karta, "session-poster")
check(len(obrazky) >= 1, f"obrázek přehrávání je na stránce ({len(obrazky)})")
check(all('tabindex="-1"' in a and 'aria-hidden="true"' in a for a in obrazky),
      "obrázek se při Tabu i ve čtečce přeskočí")
check(re.search(r'<div class="session-title">\s*<a href="/item/f1"', karta) is not None,
      "název přehrávání zůstává normálním odkazem")

print()
print("--- najetí na plakát ho lehce zvětší ---")
# Aby bylo videt, na co clovek miri. Plati pro pas Nove pridanych i pro
# mrizku medii v knihovne, mysi i klavesnici (fokus na nazvu), a kdo ma
# v systemu vypnute animace, dostane zvetseni bez animace.
CSS = (PROJECT / "jellyscope" / "static" / "style.css").read_text(encoding="utf-8")


def pravidlo(selektor_obsahuje: str) -> str:
    """Selektor pravidla s :hover a scale(), které obsahuje daný text."""
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", CSS):
        selektor = m.group(1).split("*/")[-1]
        if selektor_obsahuje in selektor and ":hover" in selektor and "scale(" in m.group(2):
            return selektor
    return ""


pas = pravidlo(".poster-card")
check(":hover" in pas and ":focus-within" in pas, f"pás: myší i klávesnicí ({pas.strip()[:70]!r})")
check(".has-episodes" not in pas or ":not(.has-episodes)" in pas,
      "karta s díly se nezvětšuje (ta se po najetí rozšíří a ukáže seznam dílů)")
mrizka = pravidlo(".media-card")
check(":hover" in mrizka and ":focus-visible" in mrizka, f"mřížka médií: myší i klávesnicí ({mrizka.strip()[:70]!r})")
# Pas se posouva do strany, takze orezava vse, co z nej vycniva - bez
# rezervy by se zvetseny plakat nahore a na krajich usekl.
telo_pasu = CSS[CSS.index(".recent-card .poster-strip {"):CSS.index("}", CSS.index(".recent-card .poster-strip {"))]
nahore = re.search(r"padding:\s*(\d+)px\s+(\d+)px", telo_pasu)
check(nahore is not None and int(nahore.group(1)) >= 6 and int(nahore.group(2)) >= 6,
      f"pás má nahoře i po stranách rezervu aspoň 6 px ({nahore.group(0) if nahore else '-'})")
klidne = re.findall(r"@media \(prefers-reduced-motion: reduce\) \{(.*?)\n\}", CSS, re.S)
check(any("poster" in blok and "transition: none" in blok for blok in klidne),
      "s vypnutými animacemi se zvětší bez animace")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
