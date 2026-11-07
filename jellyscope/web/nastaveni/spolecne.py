"""Společné pomocníky Nastavení: rozepsané nastavení databáze, strop nahrávání, časové zóny, drobnosti.

Část podbalíku web/nastaveni (viz jellyscope/web/nastaveni/__init__.py) - kód
přesunutý beze změny z původního nastaveni.py."""
from __future__ import annotations

import time
from fastapi import Request, UploadFile
from fastapi.responses import RedirectResponse
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ... import db, importers, jellyfin, scanner
from ...i18n import translate as _t
from ..zaklad import KAPACITA_JEDNOTKY, ZPET_NA_IMPORT, _clamp, _flash, _hlaska_importu


# Rozepsané nastavení databáze.
#
# Tlačítka "Otestovat spojení" a "Přenést data" nic neukládají - po nich se
# stránka načte znovu a formulář by se vrátil k tomu, co je uložené. Vyplníš
# tedy server, port, uživatele i heslo, otestuješ spojení, ono řekne "funguje"
# - a formulář je zase prázdný a přepnutý na SQLite. Proto si rozepsané
# hodnoty na chvíli podržíme.
#
# Proč v paměti a ne v session: session je u nás **podepsaná cookie**, tedy
# něco, co si prohlížeč nese s sebou a co jde přečíst. Heslo k databázi tam
# nepatří. Tady zůstane v paměti procesu a po restartu zmizí.
_DB_DRAFT: dict[tuple[int, str], tuple[float, Any]] = {}

# Strop pro nahrany soubor pri importu historie.
#
# Musi sedet s limitem na reverzni proxy - ta odmita drive nez aplikace
# a hlaska pak prijde od nginxu, ne od nas. Priklady konfigurace ve
# slozce deploy/ maji tutez hodnotu; hlida to test_deploy.py.
#
# 200 MB je s rezervou: soubor tehle velikosti je pres milion radku
# historie, coz zadna domacnost nenasbira. A cely se cte do pameti,
# takze vetsi strop by na malem serveru delal vic skody nez uzitku.
MAX_UPLOAD_MB = 200


_DRAFT_TTL_SECONDS = 900.0   # čtvrt hodiny


# Nabidka casovych zon. Cely seznam ma pres sest set polozek, takze se
# nabizi jen ty, ktere lidi opravdu pouzivaji - napsat jde libovolnou.
CASTE_ZONY = (
    "Europe/Prague", "Europe/Bratislava", "Europe/Vienna", "Europe/Berlin",
    "Europe/Warsaw", "Europe/London", "Europe/Madrid", "Europe/Rome",
    "Europe/Kyiv", "Europe/Moscow", "UTC",
    "America/New_York", "America/Chicago", "America/Denver",
    "America/Los_Angeles", "America/Sao_Paulo",
    "Asia/Tokyo", "Asia/Shanghai", "Asia/Kolkata", "Asia/Dubai",
    "Australia/Sydney", "Pacific/Auckland",
)


async def _nacti_zalohu(request: Request, soubor: UploadFile) -> bytes | None:
    """Obsah nahraného souboru, nebo None a hláška, proč to nejde.

    Obě místa, kam se nahrává záloha (Playback Reporting i Jellystat),
    kontrolovala totéž a stejně: prázdný soubor a strop velikosti.

    Čte se po kouscích a **strop platí během čtení**, ne až po něm. Dřív
    se soubor načetl celý a teprve pak se změřil - jenže strop tu je právě
    kvůli paměti, takže se ptal až ve chvíli, kdy už byla utracená.
    Gigabajtový soubor tak aplikaci položil dřív, než stačila říct, že je
    moc velký.
    """
    strop = MAX_UPLOAD_MB * 1024 * 1024
    raw = bytearray()
    while True:
        kus = await soubor.read(1024 * 1024)
        if not kus:
            break
        raw.extend(kus)
        if len(raw) > strop:
            _flash(request, "Soubor je větší než {n} MB.", "error", n=MAX_UPLOAD_MB)
            return None
    if not raw:
        _flash(request, "Soubor je prázdný.", "error")
        return None
    return bytes(raw)

def _draft_save(account: dict[str, Any], name: str, value: Any) -> None:
    _DB_DRAFT[(int(account["id"]), name)] = (time.monotonic(), value)


def _draft_read(account: dict[str, Any], name: str) -> Any:
    """Rozepsané nastavení, pokud ještě nevypršelo."""
    klic = (int(account["id"]), name)
    zaznam = _DB_DRAFT.get(klic)
    if zaznam is None:
        return None
    ulozeno_v, value = zaznam
    if time.monotonic() - ulozeno_v > _DRAFT_TTL_SECONDS:
        _DB_DRAFT.pop(klic, None)
        return None
    return value


def _draft_clear(account: dict[str, Any], name: str) -> None:
    _DB_DRAFT.pop((int(account["id"]), name), None)


def _zona_existuje(jmeno: str) -> bool:
    """Zna Python takovou zonu? Prazdne jmeno znamena "podle systemu"."""
    if not jmeno:
        return True
    try:
        ZoneInfo(jmeno)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


def _pocty_uklidu() -> dict[str, Any]:
    """Kolik je v historii záznamů, se kterými má co dělat „Narovnání dat".

    Ukazuje se na dvou místech (Úlohy a Import historie) a pokaždé
    stejně - proto jedna funkce. Jinak by se nový počet dopisoval na dvě
    místa a na jedno by se dřív nebo později zapomnělo.
    """
    return {
        "duplicate_rows": importers.duplicate_playback_count(),
        "misplaced_rows": len(importers.misplaced_episode_rows()),
        "orphan_rows": importers.orphan_playback_count(),
        "orphan_items": importers.orphan_items_count(),
        "stale_name_rows": importers.stale_name_rows(),
        "import_duplicate_rows": importers.import_duplicate_count(),
    }


async def _import_ze_souboru(request: Request, soubor: UploadFile,
                             min_seconds: str, importuj: Any,
                             sablona: str) -> RedirectResponse:
    """Import z nahrané zálohy - společný postup pro oba zdroje.

    Playback Reporting i Jellystat se liší jen v tom, která funkce data
    přečte a co se pak napíše do hlášky. Zbytek - načtení souboru, strop
    velikosti, omezení minimální délky, návrat zpátky do Importu - je
    stejný, a když byl opsaný dvakrát, měnil se pokaždé jen na jednom
    místě.
    """
    raw = await _nacti_zalohu(request, soubor)
    if raw is None:
        return RedirectResponse(ZPET_NA_IMPORT, status_code=303)

    result = await importuj(raw, min_seconds=int(_clamp(min_seconds, 0, 3600, 60)))
    _hlaska_importu(request, result, sablona)
    return RedirectResponse(ZPET_NA_IMPORT, status_code=303)


# Nastaveni je rozdelene na sekce. Drive to byla jedna dlouha stranka,
# na ktere se nedalo nic najit - a navic se pri kazdem otevreni pocitalo
# vsechno naraz, vcetne poctu radku v databazi a vypisu zaloh z disku.
#
# Ted se nacita jen to, co patri k otevrene sekci.
def _dny_v_tydnu() -> list[tuple[int, str]]:
    """(cislo, nazev) pro vyber dne - pondeli je nula, jako v Pythonu."""
    jmena = ("pondělí", "úterý", "středa", "čtvrtek", "pátek", "sobota", "neděle")
    return [(cislo, _t(jmeno)) for cislo, jmeno in enumerate(jmena)]


def _kapacita_do_formulare() -> dict[str, Any]:
    """Rucne zadana kapacita zpatky do pole - v jednotce, kterou clovek zvolil."""
    bajtu = db.get_int_setting(scanner.KAPACITA_KLIC, 0, 10 ** 18, 0)
    jednotka = db.get_setting("library_capacity_unit", "GB")
    if jednotka not in KAPACITA_JEDNOTKY:
        # Starsi nastaveni jednotku neznalo - u velkych hodnot jsou
        # terabajty citelnejsi.
        jednotka = "TB" if bajtu >= 1024 ** 4 else "GB"
    return {"kapacita_cislo": bajtu // KAPACITA_JEDNOTKY[jednotka],
            "kapacita_jednotka": jednotka}


def _generace_nesedi() -> bool:
    """Hlásí server jinou generaci, než jaká je ručně zvolená?"""
    volba = db.get_setting(jellyfin.GENERACE_KLIC, "auto")
    zjistena = db.get_setting(jellyfin.VERZE_KLIC, "")
    if volba not in ("12", "10") or not zjistena:
        return False
    return jellyfin.verze_serveru(zjistena)[0] != int(volba)


def _uloz_tajemstvi(klic: str, hodnota: str) -> None:
    """Ulozi heslo/token jen tehdy, kdyz neco prislo.

    Do formulare se ulozena hodnota nevypisuje (viz db.TAJNA_NASTAVENI),
    takze prazdne pole znamena "nech, jak bylo" - jinak by kazde ulozeni
    ostatnich poli heslo smazalo.
    """
    hodnota = (hodnota or "").strip()
    if hodnota:
        db.set_setting(klic, hodnota)
