# -*- coding: utf-8 -*-
"""Plánování úloh: denní čas vs. interval.

Dvě věci, které se snadno rozbijí a nikdo si toho měsíc nevšimne:

1. **Noční úloha se má držet svého času.** Dřív se počítal interval od
   posledního běhu, takže každé ruční spuštění posunulo i to automatické
   — a synchronizace, která měla běžet ve 3:30 v noci, se během pár dní
   protočila doprostřed večera.

2. **Analýza souborů má poslouchat zvolený zdroj dat.** Byla to
   samostatná úloha s vlastním zaškrtávátkem a na Sběr dat se neptala:
   při „Jen Jellyfin API" se ffprobe rozjel stejně a přepsal údaje
   z Jellyfinu. Teď je součástí synchronizace a rozhoduje zdroj.
"""
from __future__ import annotations

import asyncio
import dataclasses
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = str(_tmp)
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "planovac.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic"

from jellyscope import db, scanner, tasks  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


db.init_db()

SYNC = tasks.TASKS["sync"]
RECENT = tasks.TASKS["recent"]
ZALOHA = tasks.TASKS["backup"]


# Hodiny si v testu držíme sami. Kdyby se plánovač ptal na skutečný čas,
# test by dopadl jinak v poledne a jinak ve tři ráno - a takový test je
# horší než žádný, protože spadne v nejnevhodnější chvíli.
class FalesnyCas(datetime):
    ted = datetime(2026, 8, 17, 15, 0, 0)      # pondělí, tři odpoledne

    @classmethod
    def now(cls, tz: Any = None) -> datetime:   # type: ignore[override]
        return cls.ted if tz is None else cls.ted.astimezone(tz)


tasks.datetime = FalesnyCas                     # type: ignore[assignment]

# A totéž pro čas, který se ukládá do databáze. Plánovač si razítko
# "naposledy jsem to spustil já" zapisuje přes `db.utcnow()`; kdyby to
# byl skutečný čas, porovnával by se s falešným a test by dopadl jinak
# ráno a jinak večer.
db.utcnow = lambda: (FalesnyCas.ted.astimezone(timezone.utc)  # type: ignore[assignment]
                     .replace(tzinfo=None).strftime(db.TIME_FORMAT))


def posun_hodiny(hodina: int, minuta: int = 0) -> None:
    """Nastaví „teď" na daný čas téhož dne."""
    FalesnyCas.ted = FalesnyCas.ted.replace(hour=hodina, minute=minuta,
                                            second=0, microsecond=0)


def mistni(hodina: int, minuta: int = 0, dnu_zpet: int = 0) -> datetime:
    """Místní čas dneška (nebo o pár dní zpět) podle falešných hodin."""
    return (FalesnyCas.ted.replace(hour=hodina, minute=minuta,
                                   second=0, microsecond=0)
            - timedelta(days=dnu_zpet))


def zapis_automaticky_beh(task: Any, kdy_mistni: datetime) -> None:
    """Razítko „tohle spustil plánovač". Ruční běhy se do něj nepíšou."""
    utc = kdy_mistni.astimezone(timezone.utc).replace(tzinfo=None)
    db.set_setting(f"task_{task.key}_last_auto", utc.strftime(db.TIME_FORMAT))


def zapis_beh(kind: str, kdy_mistni: datetime) -> None:
    """Zapíše do scan_log doběhlou úlohu. Čas se ukládá v UTC."""
    # `astimezone()` nad naivním časem znamená "ber to jako místní" -
    # přesně to, co potřebujeme: v databázi je UTC, na hodinách místní čas.
    utc = kdy_mistni.astimezone(timezone.utc).replace(tzinfo=None)
    with db.connect() as conn:
        conn.execute("DELETE FROM scan_log WHERE kind = ?", (kind,))
        conn.execute(
            "INSERT INTO scan_log (kind, started_at, finished_at, status)"
            " VALUES (?,?,?,'done')",
            (kind, utc.strftime(db.TIME_FORMAT), utc.strftime(db.TIME_FORMAT)),
        )


print("--- z čeho se skládá seznam úloh ---")
check(set(tasks.TASKS) == {"sync", "recent", "tidy", "notifikace",
                           "updates", "backup"},
      f"šest úloh; analýza samostatná není, kontrola aktualizací "
      f"a upozornění ano ({sorted(tasks.TASKS)})")
check(SYNC.je_denni and ZALOHA.je_denni, "synchronizace a záloha běží v daný čas")
check(not RECENT.je_denni, "nově přidané tituly zůstávají na minutách")


print()
print("--- čas z nastavení se čte přísně ---")
db.set_setting("library_sync_time", "3:05")
check(tasks.denni_cas(SYNC) == "03:05",
      f"jednociferná hodina se doplní na dvě ({tasks.denni_cas(SYNC)})")
for nesmysl in ("25:00", "12:60", "", "brzy ráno", "3.30"):
    db.set_setting("library_sync_time", nesmysl)
    check(tasks.denni_cas(SYNC) == SYNC.default_time,
          f"{nesmysl!r} -> výchozí {tasks.denni_cas(SYNC)}")


print()
print("--- denní úloha běží, až když nastal její čas ---")
db.set_setting("task_sync_enabled", "1")
db.set_setting("library_sync_time", "03:30")

posun_hodiny(3, 0)
zapis_automaticky_beh(SYNC, mistni(3, 30, dnu_zpet=1))
check(tasks._is_due(SYNC) is False,
      "ve 3:00 se ještě nespouští, i když od posledního běhu uplynul den")

posun_hodiny(3, 30)
check(tasks._is_due(SYNC) is True, "ve 3:30 ano")

posun_hodiny(3, 31)
zapis_automaticky_beh(SYNC, mistni(3, 30))
check(tasks._is_due(SYNC) is False, "hned po doběhnutí se neopakuje")

posun_hodiny(23, 59)
check(tasks._is_due(SYNC) is False, "a do konce dne už taky ne")


print()
print("--- ruční spuštění se rozvrhu nedotkne ---")
# Tohle byl důvod celé změny. U intervalu by ruční běh odsunul i ten
# automatický, takže by se úloha den ode dne posouvala doprostřed dne.
# Tady na rozvrh nesahá vůbec: zítřejší termín zůstává a dnešní se
# neruší. Proto se plánovač ptá na vlastní razítko, ne do scan_log -
# tam jsou i ruční běhy.
posun_hodiny(12, 0)
zapis_automaticky_beh(SYNC, mistni(3, 30))   # ranní automatický běh
zapis_beh("library", mistni(12, 0))          # a teď ruční běh v poledne
zbyva = tasks.status(SYNC)["due_in"]
ocekavano = (mistni(3, 30) + timedelta(days=1) - FalesnyCas.ted).total_seconds() / 60
check(abs(zbyva - ocekavano) < 1,
      f"další běh je zítra ve 3:30 (za {zbyva:.0f} min), "
      f"ne za 24 h od poledne (čeká se {ocekavano:.0f})")
check(round(zbyva) == 15 * 60 + 30, f"tedy za 15,5 hodiny ({zbyva:.0f} min)")
check(tasks.status(SYNC)["next_is_today"] is False, "a je to až zítra")

# A naopak: ruční běh v 1:00, tedy PŘED dnešním termínem, nesmí dnešní
# automatický běh zrušit. Tohle by se stalo, kdyby se plánovač díval do
# scan_log: "dneska už jsi běžel, tak dnes nic".
posun_hodiny(1, 0)
zapis_automaticky_beh(SYNC, mistni(3, 30, dnu_zpet=1))
zapis_beh("library", mistni(1, 0))
check(tasks._is_due(SYNC) is False, "v 1:00 ještě není čas")
posun_hodiny(3, 30)
check(tasks._is_due(SYNC) is True,
      "ve 3:30 se spustí i po ručním běhu z jedné ráno")
check(tasks.status(SYNC)["due_in"] == 0.0,
      "a v přehledu stojí, že poběží při nejbližší kontrole")

# A to hlavní: aplikace ve 3:30 neběžela, člověk si v poledne pustil
# synchronizaci ručně - a dnešní automatický běh se tím nesmí zrušit.
posun_hodiny(12, 0)
zapis_automaticky_beh(SYNC, mistni(3, 30, dnu_zpet=1))
zapis_beh("library", mistni(12, 0))
check(tasks._is_due(SYNC) is True,
      "zameškaný termín platí dál, i když mezitím proběhl ruční běh")


print()
print("--- výpadek aplikace se dožene ---")
# Když ve 3:30 aplikace neběžela, úloha se nemá tiše přeskočit na celý
# den - spustí se při nejbližší kontrole po startu.
posun_hodiny(9, 0)
zapis_automaticky_beh(SYNC, mistni(3, 30, dnu_zpet=2))
check(tasks._is_due(SYNC) is True, "zameškaný termín se dožene")

db.set_setting("task_sync_last_auto", "")
check(tasks._is_due(SYNC) is True, "úloha, která ještě nikdy neběžela, taky")

db.set_setting("task_sync_enabled", "0")
check(tasks._is_due(SYNC) is False, "vypnutá úloha nikdy")
check(tasks.status(SYNC)["due_in"] is None, "a v přehledu nemá další běh")
db.set_setting("task_sync_enabled", "1")


print()
print("--- čerstvě nasazená úloha se nedohání ---")
# Tohle překvapilo v provozu: po nasazení v půl čtvrté odpoledne se hned
# rozjela noční synchronizace. Dnešní termín už nastal a záznam o běhu
# neexistoval, takže to vypadalo jako zameškaný termín. Plánovač si teď
# u úlohy, o které ještě nic neví, jen poznamená "od teď počítám".
import asyncio  # noqa: E402

posun_hodiny(15, 30)
db.set_setting("library_sync_time", "03:30")
db.set_setting("task_sync_last_auto", "")
db.set_setting("task_recent_enabled", "0")
db.set_setting("task_backup_enabled", "0")

spusteno: list[str] = []


async def falesny_beh() -> dict[str, Any]:
    spusteno.append("sync")
    return {"status": "ok"}


puvodni_runner = tasks.TASKS["sync"]
tasks.TASKS["sync"] = dataclasses.replace(puvodni_runner, runner=falesny_beh)
puvodni_tick = tasks.TICK_SECONDS
try:
    # Jedno kolo plánovače: uspíme ho hned po prvním průchodu.
    async def jedno_kolo() -> None:
        uloha = asyncio.create_task(tasks.run_scheduler())
        await asyncio.sleep(0.05)
        uloha.cancel()
        try:
            await uloha
        except asyncio.CancelledError:
            pass

    tasks.TICK_SECONDS = 0.01
    # `run_scheduler` čeká 25 s, než začne - v testu to obejdeme voláním
    # téhož rozhodování napřímo.
    check(tasks.posledni_automaticky_beh(tasks.TASKS["sync"]) is None,
          "výchozí stav: o automatickém běhu nic nevíme")
    check(tasks._is_due(tasks.TASKS["sync"]) is True,
          "sama podmínka by ho spustila (termín dnes už byl)")

    # ...a přesně proto plánovač napřed orazítkuje.
    if tasks.TASKS["sync"].je_denni and tasks.posledni_automaticky_beh(
            tasks.TASKS["sync"]) is None:
        tasks._poznamenej_automaticky_beh(tasks.TASKS["sync"])
    check(tasks._is_due(tasks.TASKS["sync"]) is False,
          "po orazítkování se dnes už nespustí")

    posun_hodiny(3, 30)
    FalesnyCas.ted = FalesnyCas.ted + timedelta(days=1)
    check(tasks._is_due(tasks.TASKS["sync"]) is True,
          "zítra ve 3:30 běží normálně")
finally:
    tasks.TASKS["sync"] = puvodni_runner
    tasks.TICK_SECONDS = puvodni_tick
    FalesnyCas.ted = datetime(2026, 8, 17, 15, 0, 0)


print()
print("--- intervalová úloha zůstala, jak byla ---")
db.set_setting("task_recent_enabled", "1")
db.set_setting("recent_sync_minutes", "15")
zapis_beh("recent", FalesnyCas.ted - timedelta(minutes=20))
check(tasks._is_due(RECENT) is True, "po 20 minutách při intervalu 15 -> běž")
zapis_beh("recent", FalesnyCas.ted - timedelta(minutes=5))
check(tasks._is_due(RECENT) is False, "po 5 minutách ještě ne")
db.set_setting("recent_sync_minutes", "0")
check(tasks._is_due(RECENT) is False, "nula znamená nikdy")


print()
print("--- synchronizace se řídí zvoleným zdrojem dat ---")
# Analýza už nemá vlastní zaškrtávátko. Jestli se po synchronizaci
# rozjede, rozhoduje jedině Sběr dat.
volani: list[str] = []


async def falesny_sync() -> dict[str, Any]:
    volani.append("sync")
    return {"status": "ok", "items": 3}


async def falesna_analyza(**kwargs: Any) -> dict[str, Any]:
    volani.append(f"tech(only_missing={kwargs.get('only_missing')})")
    return {"status": "ok", "ok": 3}


scanner_sync, scanner_tech = scanner.sync_library, scanner.run_tech_scan
scanner.sync_library = falesny_sync            # type: ignore[assignment]
scanner.run_tech_scan = falesna_analyza        # type: ignore[assignment]
try:
    db.set_setting("tech_source", "jellyfin")
    volani.clear()
    asyncio.run(tasks.run_now("sync"))
    check(volani == ["sync"],
          f"při zdroji Jellyfin se ffprobe nespustí ({volani})")

    db.set_setting("tech_source", "ffprobe")
    volani.clear()
    vysledek = asyncio.run(tasks.run_now("sync"))
    check(volani == ["sync", "tech(only_missing=True)"],
          f"při ffprobe naváže analýza chybějících ({volani})")
    check(vysledek.get("tech", {}).get("ok") == 3,
          "a výsledek analýzy je vidět ve výsledku úlohy")

    # Zastavená nebo spadlá synchronizace nesmí měřit neúplný seznam.
    async def spadly_sync() -> dict[str, Any]:
        volani.append("sync")
        return {"status": "stopped"}

    scanner.sync_library = spadly_sync         # type: ignore[assignment]
    volani.clear()
    asyncio.run(tasks.run_now("sync"))
    check(volani == ["sync"], f"po zastavení se analýza nespouští ({volani})")
finally:
    scanner.sync_library = scanner_sync        # type: ignore[assignment]
    scanner.run_tech_scan = scanner_tech       # type: ignore[assignment]

check(tasks.TASKS.get("tech") is None, "samostatná úloha 'tech' už neexistuje")


print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
