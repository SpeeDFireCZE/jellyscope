# -*- coding: utf-8 -*-
"""Zeměpisné umístění veřejných adres podle databáze GeoLite2.

K čemu to je: na stránce Síť ukázat, odkud se lidé dívají, když se
nedívají z domácí sítě. Adresa z domácí sítě žádné místo neoznačuje -
`192.168.1.5` je stejná v Praze i v Sydney -, takže se hledá jen
u veřejných adres.

Tři věci, které stojí za vysvětlení:

**Databáze je soubor, ne služba.** Ptát se za běhu nějakého webu na
každou adresu by znamenalo posílat adresy diváků na cizí server - a to
je přesně to, co tahle aplikace nedělá. GeoLite2 je jeden soubor
(`.mmdb`), který leží v `data/` a odpovídá offline.

**Knihovna je nepovinná.** `maxminddb` není v requirements.txt: kdo mapu
nechce, nemá důvod ji instalovat. Bez ní se sekce prostě neukáže.

**Přesnost.** GeoLite2 je zdarma a tomu odpovídá: město sedí spíš
u pevných linek, u mobilních sítí ukazuje klidně na střed země.
Pro otázku "dívá se někdo z ciziny?" to stačí, na hledání osob ne -
a tak to má i zůstat.

Data © MaxMind, GeoLite2 (CC BY-SA 4.0). Aplikace je nestahuje sama
od sebe; soubor si stáhne správce tlačítkem v Nastavení.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from . import config, db

log = logging.getLogger("jellyscope.geoip")

# Jméno souboru je pevné. Nastavovat cestu by znamenalo další políčko
# v Nastavení kvůli něčemu, co si stejně stahuje sama aplikace.
SOUBOR = "GeoLite2-City.mmdb"

# Odkud se stahuje. Je to zrcadlo, které vydává MaxMind databáze jako
# release na GitHubu - u MaxMinda samotného je potřeba účet a klíč, což
# by z jednoho tlačítka udělalo formulář na tři pole.
ZDROJ = ("https://github.com/P3TERX/GeoLite.mmdb/releases/latest/download/"
         + SOUBOR)


def cesta_k_databazi() -> Path:
    """Kde soubor leží. Vedle databáze aplikace, tedy v data/."""
    return config.load_config().database_path.parent / SOUBOR


def je_k_dispozici() -> bool:
    """Dá se vůbec hledat? Musí být obojí - knihovna i soubor."""
    return knihovna_je() and cesta_k_databazi().is_file()


def knihovna_je() -> bool:
    try:
        import maxminddb  # noqa: F401
    except ImportError:
        return False
    return True


def velikost_databaze() -> int:
    soubor = cesta_k_databazi()
    return soubor.stat().st_size if soubor.is_file() else 0


def stari_databaze() -> str:
    """Kdy se soubor naposledy stáhl. Prázdné, když tu není."""
    soubor = cesta_k_databazi()
    if not soubor.is_file():
        return ""
    from datetime import datetime, timezone
    cas = datetime.fromtimestamp(soubor.stat().st_mtime, timezone.utc)
    return cas.strftime(db.TIME_FORMAT)


_citac: Any = None


def _otevri() -> Any:
    """Otevřený soubor databáze. Drží se otevřený - je to čtení z disku
    přes mmap, takže opakované otevírání by bylo zbytečné.
    """
    global _citac
    if _citac is None:
        import maxminddb
        _citac = maxminddb.open_database(str(cesta_k_databazi()))
    return _citac


def zapomen() -> None:
    """Zavře soubor. Volá se po stažení nové verze."""
    global _citac
    if _citac is not None:
        try:
            _citac.close()
        except Exception:  # noqa: BLE001 - zavírání nesmí nic shodit
            pass
        _citac = None


def _text(hodnota: Any, jazyk: str = "en") -> str:
    """Z názvu, který přijde jako slovník jazyků, vybere jeden."""
    if isinstance(hodnota, dict):
        return str(hodnota.get(jazyk) or hodnota.get("en") or "")
    return str(hodnota or "")


def najdi(adresa: str) -> dict[str, Any] | None:
    """Kde ta adresa je. None, když se nedá zjistit.

    Vrací zeměpisné souřadnice, město, zemi a její dvoupísmenný kód.
    Když databáze adresu nezná (nová sada, VPN, satelit), vrátí None -
    to není chyba, jen to o té adrese nikdo neví.
    """
    if not je_k_dispozici():
        return None
    try:
        zaznam = _otevri().get(adresa)
    except (ValueError, OSError) as chyba:
        # Neplatná adresa nebo poškozený soubor. Mapa je doplněk,
        # takže se kvůli ní nic neshodí.
        log.debug("adresu %s se nepodařilo umístit: %s", adresa, chyba)
        return None

    if not isinstance(zaznam, dict):
        return None

    misto = zaznam.get("location") or {}
    if misto.get("latitude") is None or misto.get("longitude") is None:
        return None

    zeme = zaznam.get("country") or zaznam.get("registered_country") or {}
    mesto = zaznam.get("city") or {}
    return {
        "lat": float(misto["latitude"]),
        "lon": float(misto["longitude"]),
        "mesto": _text(mesto.get("names")),
        "zeme": _text(zeme.get("names")),
        "kod": str(zeme.get("iso_code") or ""),
    }


async def stahni() -> dict[str, Any]:
    """Stáhne databázi ze ZDROJ do data/.

    Je to jediné místo, kde aplikace sahá jinam než na Jellyfin, a děje
    se to **jen na kliknutí**, nikdy samo. Soubor má přes 60 MB, proto
    se zapisuje po kusech - ne celý do paměti.
    """
    import httpx

    cil = cesta_k_databazi()
    rozdelane = cil.with_suffix(".stahuje")
    cil.parent.mkdir(parents=True, exist_ok=True)

    try:
        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
            async with client.stream("GET", ZDROJ) as odpoved:
                odpoved.raise_for_status()
                with rozdelane.open("wb") as soubor:
                    async for kus in odpoved.aiter_bytes(1 << 20):
                        soubor.write(kus)
    except Exception as chyba:  # noqa: BLE001 - síť selhává mnoha způsoby
        rozdelane.unlink(missing_ok=True)
        log.warning("databazi GeoLite2 se nepodarilo stahnout: %s", chyba)
        return {"status": "error", "message": str(chyba)}

    # Přejmenování až na konec: kdyby se stahování přerušilo, zůstane
    # v platnosti ta stará databáze místo poloviny nové.
    zapomen()
    rozdelane.replace(cil)
    log.info("databaze GeoLite2 stazena (%s MB)", round(velikost_databaze() / 1e6, 1))
    return {"status": "ok", "bajtu": velikost_databaze()}
