# -*- coding: utf-8 -*-
"""Hlídání nové verze podle vydání na GitHubu.

Tři věci, které o tom stojí za to vědět:

**Ve výchozím stavu je to vypnuté.** Je to jediné odchozí spojení kromě
Jellyfinu (a stažení GeoLite2), a člověk, který si hostuje vlastní
server, má právo vědět, kam jeho aplikace volá - a rozhodnout, jestli
vůbec. Zapíná se v Nastavení.

**Nic to neinstaluje.** Jen řekne „je venku 1.2.0, ty máš 1.1.0" a odkáže
na stránku vydání. Aktualizace zůstává na `deploy/update.sh`, který umí
stáhnout novou verzi i restartovat službu.

**Ptá se jednou denně**, ne při každém načtení stránky. Výsledek se
ukládá do nastavení, takže stránky ho jen čtou z databáze. Veřejné API
GitHubu má limit 60 dotazů za hodinu na adresu; jeden denně se do něj
vejde i kdyby aplikací běželo víc.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from . import db

log = logging.getLogger("jellyscope.updates")

API = "https://api.github.com/repos/SpeeDFireCZE/jellyscope/releases/latest"
STRANKA = "https://github.com/SpeeDFireCZE/jellyscope/releases/latest"

# Klice v nastaveni. Vysledek se uklada, aby se nemuselo na sit pri
# kazdem nacteni stranky.
ZAPNUTO = "update_check_enabled"
POSLEDNI_KONTROLA = "update_last_check"
NALEZENA_VERZE = "update_latest_version"
NALEZENA_ADRESA = "update_latest_url"

INTERVAL_HODIN = 24


def je_zapnute() -> bool:
    return db.get_setting(ZAPNUTO, "0") == "1"


def _cislo_verze(text: Any) -> tuple[int, ...]:
    """Verzi na cisla, at jde porovnat.

    "v1.2.10" -> (1, 2, 10). Porovnavat verze jako retezce nejde:
    "1.10.0" je mensi nez "1.9.0", kdyz se to bere po znacich.
    Cokoliv, co neni cislo (rc, beta), se zahodi - na otazku "je venku
    neco novejsiho" to nema vliv.
    """
    cisla = re.findall(r"\d+", str(text or ""))
    return tuple(int(c) for c in cisla[:4]) or (0,)


def je_novejsi(nalezena: Any, moje: Any) -> bool:
    """Je nalezena verze novejsi nez ta nase?"""
    return _cislo_verze(nalezena) > _cislo_verze(moje)


def _ted() -> datetime:
    return datetime.now(timezone.utc)


def _je_cas() -> bool:
    """Uplynul uz den od posledni kontroly?"""
    posledni = db.get_setting(POSLEDNI_KONTROLA, "")
    if not posledni:
        return True
    try:
        kdy = datetime.strptime(posledni, db.TIME_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    return _ted() - kdy >= timedelta(hours=INTERVAL_HODIN)


async def zkontroluj(vynuceno: bool = False) -> dict[str, Any]:
    """Zeptá se GitHubu na poslední vydání.

    `vynuceno` obejde denní interval - to je tlačítko „Zkontrolovat teď".
    Bez něj se na síť jde jen tehdy, když od minule uplynul den.
    """
    from . import __version__

    if not vynuceno and (not je_zapnute() or not _je_cas()):
        return stav()

    import httpx

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            odpoved = await client.get(
                API, headers={"Accept": "application/vnd.github+json"})
            odpoved.raise_for_status()
            data = odpoved.json()
    except Exception as chyba:  # noqa: BLE001 - sit selhava mnoha zpusoby
        log.warning("kontrolu verze se nepodarilo provest: %s", chyba)
        return {**stav(), "status": "error", "message": str(chyba)}

    verze = str(data.get("tag_name") or "").lstrip("vV")
    db.set_setting(POSLEDNI_KONTROLA, _ted().strftime(db.TIME_FORMAT))
    db.set_setting(NALEZENA_VERZE, verze)
    db.set_setting(NALEZENA_ADRESA, str(data.get("html_url") or STRANKA))

    if je_novejsi(verze, __version__):
        log.info("je k dispozici nova verze %s (bezi %s)", verze, __version__)
    return {**stav(), "status": "ok"}


def stav() -> dict[str, Any]:
    """Co o verzích víme - bez sahání na síť. Tohle čtou stránky."""
    from . import __version__

    nalezena = db.get_setting(NALEZENA_VERZE, "")
    return {
        "zapnuto": je_zapnute(),
        "verze": __version__,
        "nalezena": nalezena,
        "adresa": db.get_setting(NALEZENA_ADRESA, "") or STRANKA,
        "kontrolovano": db.get_setting(POSLEDNI_KONTROLA, ""),
        "je_novejsi": bool(nalezena) and je_novejsi(nalezena, __version__),
    }
