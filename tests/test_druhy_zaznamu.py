# -*- coding: utf-8 -*-
r"""Filtr „Ostatní“ a druh záznamu v historii.

Proč to existuje: v grafu „Sledovanost po dnech“ se objevila třetí série
„Ostatní“, ale nešla vybrat filtrem - a hlavně o ní nešlo zjistit vůbec
nic. Hodiny bylo vidět, ale v historii se ten záznam nedal najít, protože
seznam neukazoval, o jaký druh šlo.

Do „Ostatního“ padá všechno, co není film ani díl: živé vysílání, hudba,
domácí videa - a hlavně **převzatá historie bez typu**, kterou Jellystat
ani Playback Reporting neposílají. Právě ta prázdná hodnota je zrádná:
v SQL není NULL "různé od 'Movie'", takže ji musí podchytit vlastní
podmínka, jinak by z filtru vypadla.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_druhy_zaznamu.py
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "druhy.db")
os.environ["SECRET_KEY"] = "testovaci-klic"

from jellyscope import accounts, db, stats  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


db.init_db()
accounts.create("spravce", "dlouheheslo", is_admin=True)


def prehrani(item_type: str | None, jmeno: str, sekund: int = 3600) -> None:
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO playback (session_key, user_id, user_name, item_id,"
            " item_name, item_type, started_at, last_seen_at, ended_at,"
            " watched_seconds) VALUES (?, 'u1', 'Jana', ?, ?, ?,"
            " datetime('now','-2 hours'), datetime('now','-1 hours'),"
            " datetime('now','-1 hours'), ?)",
            (f"s-{jmeno}", f"id-{jmeno}", jmeno, item_type, sekund),
        )


prehrani("Movie", "Film jedna", 3600)
prehrani("Episode", "Díl jedna", 1800)
prehrani("TvChannel", "ČT24", 900)
prehrani(None, "Z importu", 600)      # typ vůbec není
prehrani("", "Prázdný typ", 300)      # typ je prázdný řetězec
# Převzatá historie občas nese druh "Series" nebo "Season" místo "Episode":
# víme, ze kterého seriálu se dívalo, ale ne který díl. Do „Ostatního“
# takový záznam nepatří - tam člověk hledá koncerty a živé vysílání.
prehrani("Series", "Seriál bez dílu", 1800)
prehrani("Season", "Řada bez dílu", 1800)


print("--- co spadne do „Ostatního“ ---")
den = stats.daily_activity_split(2)
soucet_other = round(sum(r["other_hours"] for r in den), 2)
# 900 + 600 + 300 vteřin = 1800 = půl hodiny
check(abs(soucet_other - 0.5) < 0.01,
      f"hodiny v „Ostatním“ sedí ({soucet_other} h)")
check(abs(round(sum(r["movie_hours"] for r in den), 2) - 1.0) < 0.01,
      "filmy se do toho nepočítají")
# 1800 + 1800 + 1800 vteřin = půldruhé hodiny: díl, seriál i řada
soucet_serialu = round(sum(r["series_hours"] for r in den), 2)
check(abs(soucet_serialu - 1.5) < 0.01,
      f"seriály berou i „Series“ a „Season“ ({soucet_serialu} h)")


print()
print("--- filtr „Ostatní“ ---")
check(stats.KIND_OTHER in stats.ALLOWED_KINDS, "filtr je mezi povolenými")

radky = stats.history(kind=stats.KIND_OTHER)
jmena = sorted(r["item_name"] for r in radky)
check(jmena == ["Prázdný typ", "Z importu", "ČT24"],
      f"historie vrátí právě ty tři záznamy ({jmena})")
# Tohle je ta past: bez zvláštní podmínky na NULL by ze tří zbyl jeden.
check(any(r["item_name"] == "Z importu" for r in radky),
      "včetně záznamu, který typ vůbec nemá (NULL)")
check(stats.history_count(kind=stats.KIND_OTHER) == 3,
      f"a počítadlo říká totéž ({stats.history_count(kind=stats.KIND_OTHER)})")

check([r["item_name"] for r in stats.history(kind=stats.KIND_MOVIE)] == ["Film jedna"],
      "filtr filmů zůstal, jak byl")
check(len(stats.history(kind=stats.KIND_BOTH)) == 7, "mix ukazuje všechno")

# Proklik z tabulky pod grafem si nese filtr s sebou, takže dělicí čára
# v seznamu musí být stejná jako v křivce nad ním.
serialy = sorted(r["item_name"] for r in stats.history(kind=stats.KIND_SERIES))
check(serialy == ["Díl jedna", "Seriál bez dílu", "Řada bez dílu"],
      f"seznam seriálů odpovídá grafu ({serialy})")


print()
print("--- z čeho se „Ostatní“ skládá ---")
rozpad = stats.rozpad_ostatnich(2)
popisky = [r["label"] for r in rozpad]
check("Živé vysílání" in popisky, f"živé vysílání se pojmenuje česky ({popisky})")
check("Neznámý (z importu)" in popisky, "a chybějící typ taky")
check(sum(r["plays"] for r in rozpad) == 3, "spuštění sedí")
# NULL a prázdný řetězec jsou pro SQL dvě skupiny, pro člověka jedna věc.
check(len(popisky) == len(set(popisky)),
      f"každý druh je v seznamu jednou ({popisky})")
check(rozpad[0]["hours"] >= rozpad[-1]["hours"], "řadí se od největšího")


print()
print("--- názvy druhů ---")
check(stats.nazev_typu("Episode") == "Díl seriálu", "Episode -> Díl seriálu")
check(stats.nazev_typu("Movie") == "Film", "Movie -> Film")
check(stats.nazev_typu(None) == "Neznámý (z importu)", "nic -> z importu")
# Neznámý druh se ukáže tak, jak přišel. Je to stopa, podle které se dá
# dohledat, o co šlo - schovat ho pod "Ostatní" by ji zahodilo.
check(stats.nazev_typu("Kdovico") == "Kdovico", "neznámý druh se neschovává")


print()
print("--- stránky to unesou ---")
from fastapi.testclient import TestClient  # noqa: E402
from jellyscope.web import app  # noqa: E402

with TestClient(app) as client:
    client.post("/login", data={"username": "spravce", "password": "dlouheheslo"},
                follow_redirects=False)

    r = client.get("/?kind=other")
    check(r.status_code == 200, f"Přehled s filtrem „Ostatní“ ({r.status_code})")
    check("Mix" in r.text, "přepínač se jmenuje Mix, ne Obojí")
    check("Obojí" not in r.text.split('id="daily-card"')[-1][:2000],
          "„Obojí“ u grafu už není")
    check("Živé vysílání" in r.text,
          "a pod grafem je vidět, z čeho se „Ostatní“ skládá")

    r = client.get("/partials/daily?kind=other")
    check(r.status_code == 200 and "Ostatní" in r.text,
          "výřez pro přepínání umí totéž")
    check("data-tip" in r.text, "legenda nese bublinu s vysvětlením")

    r = client.get("/history?kind=other")
    check(r.status_code == 200, "historie s filtrem „Ostatní“")
    check("ČT24" in r.text and "Film jedna" not in r.text,
          "a ukazuje jen ty záznamy")
    check("Živé vysílání" in r.text, "ve sloupci Druh je vidět, o co šlo")

    # Nejsledovanější tituly „Ostatní“ nenabízejí: skládají se z názvů
    # titulů, o kterých se u těchhle záznamů nic neví.
    r = client.get("/?top_kind=other")
    check(r.status_code == 200, "cizí hodnota u nejsledovanějších nic nerozbije")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
