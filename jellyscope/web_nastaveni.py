# -*- coding: utf-8 -*-
"""Routy Nastavení - všech šestatřicet.

Bydlí zvlášť proto, že jich je skoro třetina celého webu a s ostatními
stránkami nemají nic společného. Kdo hledá, proč se něco neuložilo, má
hledat tady, a ne mezi grafy.

Používá se `APIRouter`, ne `@app.post(...)`. Modul tím nemusí vůbec vědět
o `app` - `web.py` si router jen vyzvedne a připojí. Bez toho by se oba
moduly musely importovat navzájem.

Co potřebuje každá stránka (šablony, kontext, hlášky, závory přihlášení),
je ve `web_zaklad`.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import (APIRouter, Depends, File, Form, HTTPException,
                     Request, UploadFile)
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from . import __version__
from . import (accounts, api, applog, collector, db, dbmigrate, dialect,
               formatting, geoip, i18n, importers, jellyfin, notifikace,
               odklizeni, scanner, sekce, stats, tasks, updates)
from .config import BASE_DIR as PROJECT_DIR
from .jellyfin import QUICK_TIMEOUT, JellyfinClient, JellyfinError
from .config import BASE_DIR, load_config
from .i18n import translate as _t
from .web_zaklad import (
    KAPACITA_JEDNOTKY, STROP_MAX, STROP_MIN, VZHLEDY, ZOOM_REZIMY,
    SETTINGS_SECTIONS, STARTED_AT, ZPET_NA_IMPORT, _clamp, _context,
    _flash, _hlaska_importu, config,
    current_account, log, require_admin, require_login, templates,
)

router = APIRouter()

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


@router.get("/settings", response_class=HTMLResponse)
def settings_page(
    request: Request,
    section: str = "jellyfin",
    # Volby prohlížeče logu. Jsou v adrese, ne v nastavení: je to pohled,
    # ne volba serveru - dva správci se můžou dívat každý na něco jiného.
    log_file: str = "",
    log_lines: int = 0,
    log_level: str = "",
    account: dict[str, Any] = Depends(require_login),
):
    allowed = {key for key, _name, admin_only in SETTINGS_SECTIONS
               if account["is_admin"] or not admin_only}
    if section not in allowed:
        # Čtenář má jen jednu sekci - a je to ta, kde si mění heslo.
        section = "jellyfin" if account["is_admin"] else "accounts"

    # Šabloně dáváme nastavení BEZ tajemství - API klíč se do stránky
    # nesmí dostat ani nedopatřením.
    # Nabidka sekci se kresli DVAKRAT - jako zalozky na desktopu a jako
    # rozbalovaci seznam na mobilu - a proto se sklada tady, jednou.
    # Dva rucne psane seznamy by se casem rozesly a v jednom by nova
    # sekce chybela.
    context: dict[str, Any] = {
        "section": section,
        "settings": db.get_public_settings(),
        "sekce_nabidka": [(klic, _t(nazev)) for klic, nazev, jen_admin
                          in SETTINGS_SECTIONS
                          if account["is_admin"] or not jen_admin],
    }

    if section == "jellyfin":
        # Když uživatel jen testoval spojení, nastavení se neuložilo -
        # do formuláře ale patří to, co vyplnil, ne uložená hodnota.
        # Jinak by po každém testu vyplňoval adresu znovu.
        rozepsane_jf = _draft_read(account, "jellyfin") or {}
        ulozeny_klic = db.get_setting("jellyfin_api_key", "").strip()
        context.update(
            jellyfin_url=rozepsane_jf.get("url") or db.get_setting("jellyfin_url", ""),
            has_api_key=bool(rozepsane_jf.get("api_key") or ulozeny_klic),
            # Generace serveru: co je zvolené a co se naposledy zjistilo.
            generace=db.get_setting(jellyfin.GENERACE_KLIC, "auto"),
            zjistena_verze=db.get_setting(jellyfin.VERZE_KLIC, ""),
            # Ruční volba proti tomu, co server hlásí. Když si odporují,
            # není to chyba (od toho ta volba je), ale má to být vidět -
            # jinak se ptáš proč aplikace posílá jiné dotazy, než čekáš.
            generace_nesedi=_generace_nesedi(),
            jellyfin_draft=bool(rozepsane_jf),
            last_library_scan=scanner.last_scan("library"),
            scan_running=scanner.is_scan_running(),
            stop_pending=scanner.stop_requested(),
        )
    elif section == "notifications":
        from . import notifikace

        # Ulozena tajemstvi se do stranky nevypisuji (db.TAJNA_NASTAVENI),
        # ale formular ma poznat, ze uz neco ulozeneho JE - jinak by
        # prazdne pole vypadalo jako nenastaveno.
        context.update(
            notifikace=notifikace.stav(),
            ma_smtp_heslo=bool(db.get_setting(
                notifikace.klic("smtp", "heslo"), "").strip()),
            ma_discord=bool(db.get_setting(
                notifikace.klic("discord", "webhook"), "").strip()),
            ma_telegram=bool(db.get_setting(
                notifikace.klic("telegram", "token"), "").strip()),
            nazvy_kanalu={"smtp": _t("e-mail"), "discord": "Discord",
                          "telegram": "Telegram"},
            dny_v_tydnu=_dny_v_tydnu(),
        )
    elif section == "data":
        from . import probe  # az tady, at start aplikace nic nezdrzuje
        # Karta s analýzou souborů se přestěhovala mezi Úlohy - tahle
        # sekce už jen vybírá zdroj dat, takže nepotřebuje ani pokrytí,
        # ani stav posledního běhu.
        zdroj = db.get_setting(scanner.ZDROJ_KLIC, "")
        context.update(
            ffprobe_found=probe.find_ffprobe(db.get_setting("ffprobe_path")),
            # Kapacita se uklada v bajtech, ale zadava se v jednotce, kterou
            # si clovek vybral - u dvacetiterabajtoveho pole je "30000 GB"
            # zbytecne dlouhe cislo.
            **_kapacita_do_formulare(),
            misto_zdroj=zdroj,
            zdroj_popis={
                "rucne": _t("volné místo se počítá ze zadané kapacity"),
                "jellyfin": _t("volné místo hlásí Jellyfin"),
                "disk": _t("volné místo se čte z disku pod aplikací"),
            }.get(zdroj, ""),
        )
    elif section == "tasks":
        context.update(
            # Karta "Narovnání dat" se sem přestěhovala z Importu, takže
            # sem patří i čísla, která vypisuje.
            **_pocty_uklidu(),
            # Když úloha doběhne, zatímco je člověk na téhle sekci, načte
            # se stránka sama - jinak by tu proužek "úloha běží" zůstal
            # viset a výsledek by se neobjevil. Viz hlídač v base.html.
            reload_on_task=True,
            task_list=tasks.all_statuses(),
            backups=tasks.list_backups(),
            backup_free=tasks.free_space(db.get_setting("backup_path", "")),
            # Co je na stroji za pg_dump a co za server. Bez toho se
            # nesoulad verzí hledá jen podle chybové hlášky po tom, co
            # záloha selže - viz tasks._vyber_pg_dump().
            pg_dumps=(tasks.dostupne_pg_dumpy() if db.database_config().is_postgres
                      else []),
            pg_server=(tasks.server_version() if db.database_config().is_postgres
                       else 0),
            scan_running=scanner.is_scan_running(),
            stop_pending=scanner.stop_requested(),
            last_library_scan=scanner.last_scan("library"),
            last_tech_scan=scanner.last_scan("tech"),
            coverage=stats.tech_coverage(),
            # Odklízení historie. Čísla se počítají i když je vypnuté -
            # právě podle nich se člověk rozhoduje, jestli ho zapnout.
            retence_dnu=odklizeni.retence_dnu(),
            retence_min=odklizeni.MIN_DNU,
            retence_max=odklizeni.MAX_DNU,
            odklid=odklizeni.prehled(),
            divaci=odklizeni.divaci(),
            preskladani_v=tasks.kdy_odlozene_preskladani(),
            zapomenuti=odklizeni.zapomenuti(),
        )
    elif section == "api":
        context.update(
            api_tokeny=api.seznam(),
            # Nově vyrobený klíč se ukazuje jen jednou, hned po
            # přesměrování; drží se proto v relaci, ne v adrese - a při
            # dalším načtení stránky už ho nemá kdo vypsat.
            novy_token=request.session.pop("novy_api_token", ""),
            api_ukazka_znaku=api.UKAZKA_ZNAKU,
        )
    elif section == "blocks":
        context.update(
            blocks=accounts.seznam_blokaci(),
            block_levels=accounts.STUPNE_BLOKACE,
            block_attempts=accounts.POKUSU_DO_BLOKACE,
            block_forget_hours=accounts.ZAPOMENUT_PO_HODINACH,
        )
    elif section == "import":
        context.update(
            import_stats=importers.import_summary(),
            last_import=scanner.last_scan("import"),
            # Kolik je v historii duplicitních a špatně přiřazených záznamů.
            # Ukazuje se předem, ať je vidět, jestli má úklid vůbec smysl.
            **_pocty_uklidu(),
        )
    elif section == "database":
        # Dvě různé konfigurace, a plete se to snadno:
        #
        #   ulozena  = co je v data/database.json, tedy co se použije
        #              po restartu. Tohle patří do formuláře.
        #   bezici   = co aplikace používá teď. Změna databáze se projeví
        #              až restartem, takže do restartu se tyhle dvě liší.
        #
        # Dřív se do formuláře dávala běžící konfigurace - a protože ta
        # je v paměti zakešovaná, po uložení PostgreSQL se formulář
        # přepnul zpátky na SQLite. Vypadalo to, že se uložení nepovedlo,
        # i když soubor byl zapsaný správně.
        ulozena = dialect.load_config(PROJECT_DIR, str(config.database_path))
        bezici = db.database_config()
        # Když má uživatel něco rozepsaného (otestoval spojení, ale ještě
        # neuložil), ukážeme ve formuláři to - jinak by o vyplněné údaje
        # při každém testu přišel.
        rozepsane = _draft_read(account, "database")
        context.update(
            database=rozepsane or ulozena,
            draft_pending=rozepsane is not None,
            running_database=bezici,
            restart_pending=ulozena.to_dict() != bezici.to_dict(),
            database_counts=dbmigrate.summarise(bezici),
            # Cesta k pythonu, kterým aplikace zrovna běží. Návod na
            # doinstalování psycopg tak ukáže příkaz, který jde
            # zkopírovat - ne obecné "pip install", které by v případě
            # virtuálního prostředí instalovalo někam jinam.
            python_path=sys.executable,
            psycopg_available=db.psycopg_available(),
            pool_available=db.pool_available(),
        )
    elif section == "accounts":
        context.update(
            all_accounts=accounts.all_accounts(),
            # Kolik je správců - podle toho se u posledního z nich
            # neukáže tlačítko Smazat. Viz accounts.delete().
            admin_count=accounts.admin_count(),
        )
    elif section == "log":
        # Vstup z adresy nikdy nedůvěřuj - ani vlastnímu odkazu. Jméno
        # souboru si ověří applog sám (viz _bezpecna_cesta), tady stačí
        # počet řádků a úroveň.
        radku = log_lines if log_lines > 0 else applog.DEFAULT_LINES
        uroven = log_level if log_level in applog.levels() else ""
        context.update(
            log=applog.read_lines(log_file, radku, uroven),
            log_lines=radku,
            log_level=uroven,
            log_levels=applog.levels(),
        )

    # Nabidka zon a aktualni cas v te zvolene - at je po ulozeni videt,
    # ze se opravdu neco zmenilo. Bez toho by clovek koukal na pole
    # s textem a musel hadat, jestli ta zona vubec plati.
    context.setdefault("casove_zony", CASTE_ZONY)
    context.setdefault("zona_ted",
                       datetime.now(formatting.zona()).strftime("%d.%m.%Y %H:%M"))

    return templates.TemplateResponse(
        request, "settings.html", _context(request, account, **context)
    )


@router.post("/settings")
def settings_save(
    request: Request,
    tech_source: str = Form("jellyfin"),
    poll_interval: str = Form("10"),
    library_capacity_gb: str = Form("0"),
    library_capacity_unit: str = Form("GB"),
    ffprobe_path: str = Form(""),
    ffprobe_concurrency: str = Form("3"),
    path_mappings: str = Form("[]"),
    account: dict[str, Any] = Depends(require_admin),
):
    # Vstup z formulare se nikdy neuklada bez kontroly. Uzivatel muze
    # (omylem i schvalne) poslat cokoliv.
    if tech_source not in ("jellyfin", "ffprobe"):
        tech_source = "jellyfin"

    db.set_setting("tech_source", tech_source)
    db.set_setting("poll_interval", _clamp(poll_interval, 2, 300, 10))
    # Cas synchronizace knihovny se sem uz nepise - patri k naplanovanym ulohám
    # a meni se ve vlastnim formulari. Kdyby ho ukladaly oba, prepsaly by
    # si hodnotu navzajem.
    # Kapacita se zadava v GB nebo TB. Nula znamena "zjisti si to sam".
    jednotka = library_capacity_unit if library_capacity_unit in KAPACITA_JEDNOTKY else "GB"
    db.set_setting("library_capacity_unit", jednotka)
    db.set_setting(scanner.KAPACITA_KLIC,
                   str(int(_clamp(library_capacity_gb, 0, 10 ** 6, 0))
                       * KAPACITA_JEDNOTKY[jednotka]))
    db.set_setting("ffprobe_concurrency", _clamp(ffprobe_concurrency, 1, 16, 3))
    db.set_setting("ffprobe_path", ffprobe_path.strip())

    import json
    try:
        parsed = json.loads(path_mappings or "[]")
        if not isinstance(parsed, list):
            raise ValueError
        db.set_setting("path_mappings", json.dumps(parsed))
    except ValueError:
        _flash(request, "Přepis cest není platný JSON - nechal jsem původní hodnotu.", "warning")
        return RedirectResponse("/settings?section=data", status_code=303)

    _flash(request, "Nastavení uloženo.", "success")
    return RedirectResponse("/settings?section=data", status_code=303)


@router.post("/settings/sync")
async def settings_sync(request: Request, account: dict[str, Any] = Depends(require_admin)):
    """Spusti synchronizaci knihovny na pozadi.

    Nespoustime ji primo v teto funkci - u velke knihovny by prohlizec
    cekal na odpoved nekolik minut a vypadalo by to jako zamrznuti.
    Ulohu odpalime a hned odpovime.
    """
    if scanner.is_scan_running():
        _flash(request, "Jiná úloha už běží, počkej na její dokončení.", "warning")
    else:
        asyncio.create_task(scanner.sync_library())
        _flash(request, "Synchronizace knihovny spuštěna.", "info")
        # `wait=task` necha stranku pockat a po dokonceni ji obnovi -
        # jinak clovek koukа na "bezi" a netusi, kdy uz je hotovo.
        return RedirectResponse("/settings?section=jellyfin&wait=task",
                                status_code=303)
    return RedirectResponse("/settings?section=jellyfin", status_code=303)


@router.post("/settings/stop")
async def settings_stop(
    request: Request,
    back: str = Form("tasks"),
    account: dict[str, Any] = Depends(require_admin),
):
    """Poprosi bezici ulohu, aby skoncila.

    Uloha se nepreruší uprostred prace - dodela rozdelanou polozku a teprve
    pak skonci. Proto se tady taky nic necekaji: jen se preda pokyn.
    """
    if scanner.request_stop():
        _flash(
            request,
            "Pokyn k zastavení předán. Úloha dokončí rozpracovanou položku "
            "a skončí - stránka se pak obnoví sama.",
            "info",
        )
    else:
        _flash(request, "Žádná úloha zrovna neběží.", "warning")

    sekce = back if back in ("tasks", "jellyfin", "data") else "tasks"
    # `wait=task` rekne strance, at si pocka a obnovi se sama, jakmile
    # uloha doopravdy skonci. Uzivatel tak nemusi hadat, kdy uz muze.
    cekat = "&wait=task" if scanner.stop_requested() else ""
    return RedirectResponse(f"/settings?section={sekce}{cekat}", status_code=303)


@router.post("/settings/scan")
async def settings_scan(
    request: Request,
    mode: str = Form("missing"),
    library_id: str = Form(""),
    account: dict[str, Any] = Depends(require_admin),
):
    """Spusti technickou analyzu souboru pres ffprobe.

    `library_id` spousti tlacitko primo u hlasky na detailu knihovny -
    analyzuje se pak jen ta knihovna a clovek se na ni zase vrati.
    """
    # Kam se po spusteni vratit. Jen id knihovny, ktera opravdu existuje;
    # cizi hodnota z formulare se nesmi dostat do adresy presmerovani.
    # Bez knihovny zpatky mezi Ulohy - tam ta tlacitka jsou.
    zpet = "/settings?section=tasks"
    if library_id and stats.library(library_id):
        zpet = f"/library/{library_id}"
    else:
        library_id = ""

    if db.get_setting("tech_source") != "ffprobe":
        _flash(
            request,
            "Zdroj technických dat je nastavený na Jellyfin. "
            "Přepni ho na ffprobe a ulož nastavení.",
            "warning",
        )
        return RedirectResponse(zpet, status_code=303)

    if scanner.is_scan_running():
        _flash(request, "Jiná úloha už běží, počkej na její dokončení.", "warning")
    else:
        asyncio.create_task(scanner.run_tech_scan(
            only_missing=(mode == "missing"), library_id=library_id or None))
        _flash(request, "Analýza souborů spuštěna.", "info")
        oddelovac = "&" if "?" in zpet else "?"
        return RedirectResponse(f"{zpet}{oddelovac}wait=task", status_code=303)

    return RedirectResponse(zpet, status_code=303)


@router.post("/settings/connection")
async def settings_connection(
    request: Request,
    jellyfin_url: str = Form(""),
    jellyfin_api_key: str = Form(""),
    jellyfin_generation: str = Form("auto"),
    action: str = Form("save"),
    account: dict[str, Any] = Depends(require_admin),
):
    """Ulozi nebo otestuje adresu Jellyfinu a API klic.

    Klic se do formulare nikdy nevypisuje - jen se pozna, jestli uz nejaky
    je. Prazdne pole proto znamena "nech ten stavajici", ne "smaz ho".
    Bez toho by staclo omylem ulozit formular a spojeni by prestalo fungovat.

    Testovani je soucasti **tehoz** formulare, ne samostatneho tlacitka
    vedle. Drive bylo zvlast a nic neposilalo, takze testovalo ulozene
    nastaveni misto toho vyplneneho: vyplnil jsi adresu, kliknul na
    "Otestovat spojeni" a dostal chybu o chybejicim http:// - protoze
    se testovala prazdna ulozena hodnota. A vyplnene udaje se pritom
    zahodily, protoze odeslani jednoho formulare zahodi obsah druheho.
    """
    url = jellyfin_url.strip().rstrip("/")
    if url and not url.startswith(("http://", "https://")):
        url = "http://" + url

    # Generace serveru. Adresa ani klíč se nezahazují - ty jsou pro obě
    # generace tytéž a liší se jen to, jak se posílají dotazy.
    #
    # Zahodí se ale **zjištěná verze**, a jen při skutečné změně volby:
    # patří k tomu, co bylo vybrané předtím, takže by na stránce zůstala
    # dvě čísla, která si odporují. Prázdná se zjistí znovu při nejbližším
    # testu spojení nebo synchronizaci.
    volba = jellyfin_generation if jellyfin_generation in ("auto", "12", "10") \
        else "auto"
    if volba != db.get_setting(jellyfin.GENERACE_KLIC, "auto"):
        db.set_setting(jellyfin.GENERACE_KLIC, volba)
        db.set_setting(jellyfin.VERZE_KLIC, "")

    # Prazdny klic znamena "nech stavajici". Pri testu bereme i rozepsany,
    # aby slo otestovat vic pokusu za sebou bez opakovaneho vypisovani.
    rozepsane = _draft_read(account, "jellyfin") or {}
    key = (jellyfin_api_key.strip()
           or rozepsane.get("api_key", "")
           or db.get_setting("jellyfin_api_key", ""))

    if action == "test":
        # Rozepsane si podrzime, at se formular po testu nevyprazdni.
        _draft_save(account, "jellyfin", {"url": url, "api_key": key})

        if not url:
            _flash(request, "Nejdřív vyplň adresu serveru.", "error")
        else:
            try:
                async with JellyfinClient(url, key, QUICK_TIMEOUT) as client:
                    info = await client.system_info()
                _flash(
                    request,
                    "Spojení v pořádku: {server} (Jellyfin {verze})",
                    "success",
                    server=info.get("ServerName", "?"),
                    verze=info.get("Version", "?"),
                )
            except JellyfinError as exc:
                _flash(request, "Spojení selhalo: {duvod}", "error", duvod=str(exc))
        return RedirectResponse("/settings?section=jellyfin", status_code=303)

    db.set_setting("jellyfin_url", url)
    if key:
        db.set_setting("jellyfin_api_key", key)
    _draft_clear(account, "jellyfin")

    # Verzi zjistíme rovnou při uložení, ne až při testu spojení nebo
    # noční synchronizaci: kdo přepne generaci a klikne na Uložit, má
    # hned vidět, co server hlásí - a hlavně se hned pozná, když si to
    # s ruční volbou odporuje.
    #
    # Krátký strop a spolknutá chyba: uložení nesmí selhat kvůli tomu,
    # že server zrovna neběží. Verze pak zůstane prázdná a doplní se
    # při nejbližší příležitosti.
    if url and key:
        try:
            async with JellyfinClient(url, key, QUICK_TIMEOUT) as client:
                await client.system_info()
        except JellyfinError as chyba:
            log.info("verzi Jellyfinu se pri ulozeni nepodarilo zjistit: %s",
                     chyba)

    _flash(request, "Připojení uloženo.", "success")
    return RedirectResponse("/settings?section=jellyfin", status_code=303)


@router.post("/settings/database")
def settings_database(
    request: Request,
    kind: str = Form("sqlite"),
    sqlite_path: str = Form("data/jellyscope.db"),
    pg_host: str = Form("localhost"),
    pg_port: str = Form("5432"),
    pg_database: str = Form("jellyscope"),
    pg_user: str = Form("jellyscope"),
    pg_password: str = Form(""),
    # Nezaskrtnute policko se v HTML formulari vubec neposila, proto je
    # vychozi hodnota "" = vypnuto. To je zaroven duvod, proc se sem neda
    # dat Form(True) - to by slo zapnout, ale uz nikdy vypnout.
    pg_use_pool: str = Form(""),
    action: str = Form("save"),
    account: dict[str, Any] = Depends(require_admin),
):
    """Nastaveni databaze: ulozit, otestovat, nebo prenest data.

    Tohle jedine nastaveni nemuze byt v databazi - potrebujeme ho, abychom
    se k ni vubec pripojili. Uklada se proto do souboru data/database.json.
    """
    current = db.database_config()
    # Heslo se do formuláře nikdy nevypisuje, takže prázdné pole znamená
    # "nech to, co už znám". Rozepsané nastavení je v tom pořadí první:
    # po otestování spojení chceme uložit přesně to, co se testovalo.
    rozepsane = _draft_read(account, "database")

    candidate = dialect.DatabaseConfig(
        kind=kind if kind in (dialect.SQLITE, dialect.POSTGRES) else dialect.SQLITE,
        path=sqlite_path.strip() or "data/jellyscope.db",
        host=pg_host.strip() or "localhost",
        port=int(_clamp(pg_port, 1, 65535, 5432)),
        database=pg_database.strip() or "jellyscope",
        user=pg_user.strip() or "jellyscope",
        password=pg_password or (rozepsane.password if rozepsane else "")
                 or current.password,
        use_pool=bool(pg_use_pool),
    )

    # Test ani přenos nic neukládají, ale rozepsané hodnoty si podržíme -
    # jinak by je uživatel po každém kliknutí vyplňoval znovu.
    if action in ("test", "migrate"):
        _draft_save(account, "database", candidate)

    if action == "test":
        ok, message = db.test_connection(candidate)
        _flash(request, message, "success" if ok else "error")
        return RedirectResponse("/settings?section=database", status_code=303)

    if action == "migrate":
        ok, message = db.test_connection(candidate)
        if not ok:
            _flash(request, "Cílová databáze není dostupná: {duvod}", "error",
                   duvod=message)
            return RedirectResponse("/settings?section=database", status_code=303)

        result = dbmigrate.copy_all(current, candidate)
        if result.get("status") != "ok":
            _flash(request, result.get("message", "Přenos selhal."), "error")
        else:
            _flash(
                request,
                "Přeneseno {n} řádků. Ulož nastavení a restartuj, "
                "aby se aplikace na novou databázi přepnula.",
                "success",
                n=result["total"],
            )
        return RedirectResponse("/settings?section=database", status_code=303)

    ok, message = db.test_connection(candidate)
    if not ok:
        _flash(request, "Neukládám - spojení nefunguje: {duvod}", "error",
               duvod=message)
        return RedirectResponse("/settings?section=database", status_code=303)

    dialect.save_config(PROJECT_DIR, candidate)
    _draft_clear(account, "database")   # uloženo, rozepsané už není k čemu
    _flash(
        request,
        "Nastavení databáze uloženo. Změna se projeví po restartu aplikace.",
        "success",
    )
    return RedirectResponse("/settings?section=database", status_code=303)


def _uloz_tajemstvi(klic: str, hodnota: str) -> None:
    """Ulozi heslo/token jen tehdy, kdyz neco prislo.

    Do formulare se ulozena hodnota nevypisuje (viz db.TAJNA_NASTAVENI),
    takze prazdne pole znamena "nech, jak bylo" - jinak by kazde ulozeni
    ostatnich poli heslo smazalo.
    """
    hodnota = (hodnota or "").strip()
    if hodnota:
        db.set_setting(klic, hodnota)


@router.post("/settings/notifications")
def settings_notifications(
    request: Request,
    smtp_enabled: str = Form(""),
    smtp_host: str = Form(""),
    smtp_port: str = Form("587"),
    smtp_uzivatel: str = Form(""),
    smtp_heslo: str = Form(""),
    smtp_odesilatel: str = Form(""),
    smtp_komu: str = Form(""),
    smtp_tls: str = Form(""),
    discord_enabled: str = Form(""),
    discord_webhook: str = Form(""),
    telegram_enabled: str = Form(""),
    telegram_token: str = Form(""),
    telegram_chat: str = Form(""),
    account: dict[str, Any] = Depends(require_admin),
):
    """Nastaveni kanalu, kterymi upozorneni chodi."""
    from . import notifikace

    db.set_setting(notifikace.klic("smtp", "enabled"), "1" if smtp_enabled else "0")
    db.set_setting(notifikace.klic("smtp", "host"), smtp_host.strip())
    db.set_setting(notifikace.klic("smtp", "port"),
                   _clamp(smtp_port, 1, 65535, 587))
    db.set_setting(notifikace.klic("smtp", "uzivatel"), smtp_uzivatel.strip())
    _uloz_tajemstvi(notifikace.klic("smtp", "heslo"), smtp_heslo)
    db.set_setting(notifikace.klic("smtp", "odesilatel"), smtp_odesilatel.strip())
    db.set_setting(notifikace.klic("smtp", "komu"), smtp_komu.strip())
    db.set_setting(notifikace.klic("smtp", "tls"), "1" if smtp_tls else "0")

    db.set_setting(notifikace.klic("discord", "enabled"), "1" if discord_enabled else "0")
    _uloz_tajemstvi(notifikace.klic("discord", "webhook"), discord_webhook)

    db.set_setting(notifikace.klic("telegram", "enabled"),
                   "1" if telegram_enabled else "0")
    _uloz_tajemstvi(notifikace.klic("telegram", "token"), telegram_token)
    db.set_setting(notifikace.klic("telegram", "chat"), telegram_chat.strip())

    db.forget_settings()
    # Zapnuty kanal s nevyplnenymi udaji je tichá past: nastaveni se ulozi,
    # nic nehlasi chybu - a clovek se spolehne na upozorneni, ktera nikdy
    # neprijdou. Rekneme to hned, ne az pri prvnim poplachu.
    nedodelane = [k for k in notifikace.KANALY
                  if notifikace.kanal_zapnuty(k)
                  and not notifikace.kanal_nastaveny(k)]
    if nedodelane:
        _flash(request,
               "Kanály uloženy, ale tohle je zapnuté a nevyplněné: {kanaly}. "
               "Dokud to nedoplníš, nic se přes ně neodešle.", "warning",
               kanaly=", ".join(notifikace.NAZVY.get(k, k) for k in nedodelane))
    else:
        _flash(request, "Kanály uloženy.", "success")
    return RedirectResponse("/settings?section=notifications", status_code=303)


@router.post("/settings/notifications/events")
def settings_notification_events(
    request: Request,
    event_sberac: str = Form(""),
    event_misto: str = Form(""),
    event_souhrn: str = Form(""),
    ticho_minut: str = Form(""),
    misto_dnu: str = Form(""),
    souhrn_den: str = Form("0"),
    souhrn_hodina: str = Form("9"),
    souhrn_minuta: str = Form("0"),
    account: dict[str, Any] = Depends(require_admin),
):
    """Na co upozornovat a kdy."""
    from . import notifikace

    for udalost, hodnota in (("sberac", event_sberac), ("misto", event_misto),
                             ("souhrn", event_souhrn)):
        db.set_setting(notifikace.klic_udalosti(udalost), "1" if hodnota else "0")

    db.set_setting("notify_ticho_minut",
                   _clamp(ticho_minut, 5, 1440, notifikace.VYCHOZI_TICHO_MINUT))
    db.set_setting("notify_misto_dnu",
                   _clamp(misto_dnu, 1, 365, notifikace.VYCHOZI_MISTO_DNU))
    db.set_setting("notify_souhrn_den", _clamp(souhrn_den, 0, 6, 0))
    # Hodina a minuta chodí ze dvou polí; `_clamp` ohlídá rozsah a doplní
    # nulu, `tasks.platny_cas` je poslední pojistka - do rozvrhu se nesmí
    # dostat nic jiného než "HH:MM".
    cas = (f"{int(_clamp(souhrn_hodina, 0, 23, 9)):02d}"
           f":{int(_clamp(souhrn_minuta, 0, 59, 0)):02d}")
    db.set_setting("notify_souhrn_cas", tasks.platny_cas(cas, "09:00"))

    db.forget_settings()
    _flash(request, "Uloženo.", "success")
    return RedirectResponse("/settings?section=notifications", status_code=303)


@router.post("/settings/notifications/test")
async def settings_notification_test(
    request: Request,
    kanal: str = Form(""),
    account: dict[str, Any] = Depends(require_admin),
):
    """Zkusebni zprava jednim kanalem.

    Kanal se porovnava proti pevnemu seznamu - z formulare se do
    odesilani nesmi dostat nic jineho.
    """
    from . import notifikace

    if kanal not in notifikace.KANALY:
        _flash(request, "Neznámý kanál.", "error")
        return RedirectResponse("/settings?section=notifications", status_code=303)

    vysledek = await notifikace.posli_zkusebni(kanal)
    if vysledek.get("ok"):
        _flash(request, "Zkušební zpráva odeslána.", "success")
    else:
        _flash(request, "Nepodařilo se odeslat: {chyba}", "error",
               chyba=vysledek.get("chyba") or "?")
    return RedirectResponse("/settings?section=notifications", status_code=303)


@router.post("/settings/interface")
def settings_interface(request: Request,
                       ui_max_streams: str = Form(""),
                       ui_max_streams_mobile: str = Form(""),
                       ui_max_viewers: str = Form(""),
                       ui_max_viewers_mobile: str = Form(""),
                       ui_map_zoom: str = Form("click"),
                       ui_skin: str = Form("novy"),
                       ui_cas_presne: str = Form("0"),
                       ui_dashboard: str = Form("0"),
                       account: dict[str, Any] = Depends(require_admin)):
    """Stropy dlouhých seznamů - kolik se vypíše, než se zbytek schová.

    Hodnoty se ukládají tak, jak přišly; ořezání do mezí dělá až čtení
    (`_stropy`). Kdyby se ořezávalo při ukládání, člověk by napsal 100,
    uvidel 50 a nevěděl proč - takhle se aspoň chová stránka a nastavení
    stejně.
    """
    db.set_setting("ui_max_streams", str(_clamp(ui_max_streams, STROP_MIN,
                                                STROP_MAX, 10)))
    db.set_setting("ui_max_streams_mobile",
                   _clamp(ui_max_streams_mobile, STROP_MIN,
                          STROP_MAX, 3))
    db.set_setting("ui_max_viewers", str(_clamp(ui_max_viewers, STROP_MIN,
                                                STROP_MAX, 10)))
    db.set_setting("ui_max_viewers_mobile",
                   _clamp(ui_max_viewers_mobile, STROP_MIN, STROP_MAX, 5))
    # Cokoli mimo znamé režimy je překlep nebo podvržený formulář -
    # v obou případech je správná odpověď výchozí hodnota, ne uložit to.
    db.set_setting("ui_map_zoom",
                   ui_map_zoom if ui_map_zoom in ZOOM_REZIMY else "click")
    db.set_setting("ui_skin", ui_skin if ui_skin in VZHLEDY else "novy")
    db.set_setting("ui_cas_presne", "1" if ui_cas_presne == "1" else "0")
    zapina_prehled = ui_dashboard == "1" and not sekce.je_zapnuty()
    db.set_setting(sekce.ZAPNUTO, "1" if ui_dashboard == "1" else "0")
    _flash(request, "Uloženo.", "success")

    # Kdo přehled právě zapnul, jde rovnou sestavit - zapnout a sestavit
    # jsou dva kroky téže věci, ne dvě různá místa k hledání.
    if zapina_prehled and not sekce.nacti_rozvrzeni():
        return RedirectResponse("/dashboard", status_code=303)
    return RedirectResponse("/settings?section=interface", status_code=303)


@router.post("/settings/updates")
async def settings_updates(request: Request,
                           account: dict[str, Any] = Depends(require_admin)):
    """Ruční kontrola nové verze.

    Jestli se má ptát pravidelně a kdy, se nastavuje v Úlohách jako
    u všeho ostatního, co běží samo - viz úloha "Kontrola aktualizací".
    Tady zůstalo jen jednorázové kliknutí, které se zeptá bez ohledu na
    to, jestli je úloha zapnutá: to je akce, ne rozvrh.
    """
    vysledek = await updates.zkontroluj(vynuceno=True)
    nove_vydani = bool(vysledek.get("je_novejsi"))
    preklady = int(vysledek.get("nove_preklady") or 0)
    # Ctyri odpovedi, kazda jina - clovek ma z hlasky poznat, co ho ceka,
    # driv nez cokoliv otevre. "Neco je nove" bez upresneni by ho poslalo
    # hledat.
    if vysledek.get("status") == "error":
        _flash(request, "Kontrolu se nepovedlo provést: {duvod}", "error",
               duvod=vysledek.get("message", "?"))
    elif nove_vydani and preklady:
        _flash(request,
               "Je k dispozici verze {verze} a k tomu nové překlady "
               "({n} souborů). Obojí se stáhne najednou.", "success",
               verze=vysledek.get("nalezena", "?"), n=preklady)
    elif nove_vydani:
        _flash(request, "Je k dispozici verze {verze}.", "success",
               verze=vysledek.get("nalezena", "?"))
    elif preklady:
        _flash(request,
               "Vydání je aktuální, ale na GitHubu přibyly překlady "
               "({n} souborů).", "success", n=preklady)
    else:
        _flash(request, "Máš nejnovější verzi i překlady.", "success")

    return RedirectResponse("/settings?section=general#verze", status_code=303)


@router.post("/settings/language")
def settings_language(
    request: Request,
    ui_language: str = Form("cs"),
    log_language: str = Form("cs"),
    app_timezone: str = Form(""),
    account: dict[str, Any] = Depends(require_admin),
):
    if ui_language not in i18n.LANGUAGES:
        ui_language = i18n.DEFAULT_LANGUAGE
    db.set_setting("ui_language", ui_language)

    # Jazyk logu se ukláda ze stejného formuláře, ale je to jiná volba:
    # log často čte někdo jiný, než kdo se dívá do rozhraní.
    if log_language not in i18n.LANGUAGES:
        log_language = i18n.DEFAULT_LANGUAGE
    db.set_setting("log_language", log_language)
    applog.nastav_jazyk(log_language)

    # Zona se uklada, jen kdyz ji Python zna. Preklep by jinak zpusobil,
    # ze se casy vypisuji podle systemu, a nikdo by nevedel proc.
    zona = app_timezone.strip()
    if not zona:
        db.set_setting("app_timezone", "")
    elif _zona_existuje(zona):
        db.set_setting("app_timezone", zona)
        # Prostredi procesu prepiseme rovnou - vypis casu se tim srovna
        # hned. Deleni dnu v SQL az po restartu, viz lifespan().
        os.environ["TZ"] = zona
        if hasattr(time, "tzset"):
            time.tzset()
    else:
        _flash(request, "Časovou zónu {zona} neznám – nechal jsem tu původní.",
               "error", zona=zona)
        return RedirectResponse("/settings?section=general", status_code=303)

    _flash(request, i18n.translate("Uložit jazyk", ui_language) + " ✓", "success")
    return RedirectResponse("/settings?section=general", status_code=303)


@router.post("/settings/update")
async def settings_update(request: Request,
                          account: dict[str, Any] = Depends(require_admin)):
    """Stáhne novou verzi a restartuje aplikaci.

    Dělá totéž co `deploy/update.sh`, jen bez toho posledního kroku:
    `git pull`, doinstalování závislostí a pak **restart vlastního
    procesu** (`os.execv`), tedy stejný restart jako tlačítko v Nastavení.
    Správce služby k tomu není potřeba.

    Chybějící sloupce v databázi si aplikace doplní sama při startu, takže
    po restartu je hotovo. Když se aktualizace nepovede, nic se
    nerestartuje a běží dál stará verze - to je bezpečnější pořadí.
    """
    vysledek = await updates.aktualizuj()
    if vysledek.get("status") != "ok":
        _flash(request, vysledek.get("message", "Aktualizace se nepovedla."), "error")
        return RedirectResponse("/settings?section=general", status_code=303)

    _naplanuj_restart()
    # Zamerne NE presmerovani na stranku aplikace.
    #
    # Sablony se ctou ze souboru pri kazdem pozadavku, kod aplikace bydli
    # v pameti procesu. Mezi stazenim a restartem tedy bezi STARY KOD nad
    # NOVYMI SABLONAMI - a jakmile nova sablona chce promennou, o ktere
    # stary kod nevi, skonci to chybou. Presne tohle delalo po aktualizaci
    # z prohlizece "Internal Server Error".
    #
    # Tahle stranka je slozena tady v Pythonu: zadna sablona, zadny
    # kontext, nic, co by se mohlo rozejit. Pocka na novy proces a teprve
    # pak pusti cloveka dal.
    return HTMLResponse(_stranka_aktualizace())


def _stranka_aktualizace() -> str:
    """Čekárna na restart po aktualizaci - bez šablony, schválně.

    Je to jediná stránka, kterou musí vykreslit **stará** verze aplikace
    v okamžiku, kdy na disku už leží nová. Proto nesmí sáhnout na nic,
    co se s verzí mění: ani na šablonu, ani na styl, ani na kontext.
    """
    nadpis = i18n.translate("Aktualizuji…")
    popis = i18n.translate("Nová verze je stažená. Aplikace se teď "
                           "restartuje - jakmile bude nahoře, pustím tě dál.")
    dlouho = i18n.translate("Trvá to déle, než je zdrávo.")
    pokracovat = i18n.translate("Zkusit to znovu")
    beh_od = int(STARTED_AT)
    verze_ted = __version__
    return f"""<!doctype html>
<html lang="{i18n.current_language()}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{nadpis} &middot; Jellyscope</title>
<style>
  body {{ margin: 0; min-height: 100vh; display: grid; place-items: center;
         background: #131312; color: #eceae4; font-size: 15px;
         font-family: -apple-system, "Segoe UI", Roboto, sans-serif; }}
  .box {{ max-width: 420px; padding: 28px 30px; border-radius: 14px;
          border: 1px solid rgba(255,255,255,.10); background: #1b1b1a;
          box-shadow: 0 24px 60px rgb(0 0 0 / 45%); text-align: center; }}
  h1 {{ font-size: 19px; margin: 0 0 10px; }}
  p  {{ color: #b6b4ac; line-height: 1.5; margin: 0; }}
  .pozn:not(:empty) {{ margin-top: 14px; font-size: 14px; }}
  .pozn a {{ color: #6aa9ee; }}
  .tecka {{ display: inline-block; width: 9px; height: 9px; border-radius: 50%;
            background: #3987e5; margin-bottom: 14px;
            animation: tep 1.4s ease-in-out infinite; }}
  @keyframes tep {{ 0%, 100% {{ opacity: 1; }} 50% {{ opacity: .25; }} }}
  @media (prefers-reduced-motion: reduce) {{ .tecka {{ animation: none; }} }}
</style>
</head>
<body>
<div class="box">
  <span class="tecka"></span>
  <h1>{nadpis}</h1>
  <p>{popis}</p>
  <p class="pozn" id="pozn"></p>
</div>
<script>
  // Ptáme se na /health, dokud neodpoví nový proces - pozná se podle
  // `started_at`, které se výměnou procesu změní. Teprve pak stránku
  // pustíme dál; do té doby by ji kreslila stará verze nad novými
  // šablonami, což je právě to, čemu se tady vyhýbáme.
  //
  // Od čeho čekáme, je zapsané tady ze serveru - je to start procesu,
  // který tuhle stránku vykreslil. Zjišťovat si to až první odpovědí
  // nešlo: restart přijde do vteřiny, takže první odpověď už patřila
  // novému procesu, čekárna si ho zapsala jako výchozí stav a čekala na
  // změnu, která nikdy nepřišla. Aplikace byla přitom dávno nahoře.
  var puvodni = {beh_od};        // start procesu, ktery tuhle stranku vykreslil
  var verze = "{verze_ted}";     // a verze, kterou mel
  var pokusu = 0;
  var VZDAT_TO = 150;              // ~5 minut

  function zkus() {{
    if (pokusu++ > VZDAT_TO) {{
      document.getElementById("pozn").innerHTML =
        '{dlouho} <a href="/">{pokracovat}</a>';
      return;
    }}
    fetch("/health", {{ cache: "no-store", credentials: "same-origin" }})
      .then(function (r) {{ return r.json(); }})
      .then(function (data) {{
        // Dva signály, protože každý sám o sobě někde selže:
        //   * `version` je přesně ta otázka, na kterou čekáme - ale hlásí
        //     se jen přihlášenému,
        //   * `started_at` odpoví komukoliv, jen říká míň ("něco se
        //     restartovalo").
        // Stačí, když se změní jeden.
        if (data.started_at !== puvodni) {{ location.href = "/"; }}
        else if (data.version && data.version !== verze) {{ location.href = "/"; }}
        else {{ setTimeout(zkus, 2000); }}
      }})
      .catch(function () {{
        // Aplikace je právě dole - přesně to čekáme.
        setTimeout(zkus, 2000);
      }});
  }}

  zkus();
</script>
</body>
</html>"""


@router.post("/settings/restart")
async def settings_restart(request: Request, account: dict[str, Any] = Depends(require_admin)):
    """Restartuje **Jellyscope**, ne Jellyfin.

    Nahrazujeme vlastni proces, nic jineho. Na medialni server se tim
    nesaha - beziciho prehravani se to nedotkne. Jedine, co se stane,
    je ze tahle aplikace na par vterin prestane odpovidat.

    Vetsina nastaveni se projevi hned, protoze se cte z databaze pri kazdem
    pouziti. Restart je potreba u veci, ktere se nacitaji jednou pri startu -
    hlavne u zmeny databaze.

    Restart delame tak, ze proces nahradi sam sebe (`os.execv`). Az odpoved
    dorazi do prohlizece, aplikace se zvedne znovu.
    """
    log.info("restart aplikace vyzadan uctem %s", account["username"])
    _flash(request, "Aplikace se restartuje. Stránka se obnoví sama, jakmile bude nahoře.", "info")
    _naplanuj_restart()
    # `wait=restart`: stranka si sama pocka, az se zvedne novy proces,
    # a nacte se znovu. Drive to uzivatel musel odhadnout a obnovit rucne.
    return RedirectResponse("/settings?section=general&wait=restart", status_code=303)


def _naplanuj_restart() -> None:
    """Za chvilku nahradí proces sám sebou.

    Vlastní funkce, protože restart potřebuje víc míst - ruční tlačítko
    i obnova zálohy. Dvě kopie téhle logiky by se časem rozešly a jedna
    z nich by zapomněla zavřít spojení.
    """
    async def _restart_soon() -> None:
        # Chvilka na odeslani odpovedi - jinak by prohlizec dostal
        # preruseni spojeni misto presmerovani.
        await asyncio.sleep(1.0)
        log.info("restart na zadost uzivatele")
        # execv nahradi proces, takze zadny uklid uz nikdo neudela -
        # spojeni je potreba zavrit ted, dokud jeste bezime.
        db.close_pool()
        try:
            os.execv(sys.executable, [sys.executable, *sys.argv])
        except OSError:
            # Kdyby execv neproslo, aspon slusne skoncime - spravce sluzby
            # (nebo uzivatel) aplikaci nastartuje znovu.
            log.exception("restart pres execv selhal, koncim")
            os._exit(1)

    asyncio.create_task(_restart_soon())


@router.post("/settings/historie/zapomen")
async def historie_zapomen(request: Request, user_id: str = Form(""),
                           account: dict[str, Any] = Depends(require_admin)):
    """Smaže historii jednoho diváka. Účet zůstává v Jellyfinu.

    Vlastní routa, ne součást formuláře úloh: je to jednorázový zásah do
    dat, ne nastavení, a nemá se stát mimochodem při ukládání něčeho
    jiného.

    Pořadí je pevné: nejdřív záloha, pak mazání. Když se záloha nepovede,
    nemaže se - kdo klikl na špatné jméno, má odkud se vrátit.
    """
    user_id = (user_id or "").strip()
    if not odklizeni.pocet_relaci(user_id):
        _flash(request, "Nebylo co zapomenout - k tomu divákovi nic nemáme.",
               "info")
        return RedirectResponse("/settings?section=tasks#odklizeni",
                            status_code=303)

    zaloha = await tasks.zaloha_pred_mazanim()
    if zaloha.get("status") != "ok":
        _flash(request,
               "Historie se nesmazala - nejdřív se nepovedla záloha: {duvod}",
               "error", duvod=zaloha.get("message") or "?")
        return RedirectResponse("/settings?section=tasks#odklizeni",
                            status_code=303)

    # Do kose, ne rovnou pryc: ze statistik je divak hned, ale do noci
    # se da zasah vzit zpet - viz odklizeni.vrat_z_kose(). Presun bezi
    # ve vlakne, at velka historie nezastavi obsluhu ostatnich stranek.
    vysledek = await asyncio.to_thread(
        odklizeni.zapomen_uzivatele, user_id, True, str(zaloha.get("file") or ""))
    # Log uz zapsalo `odklizeni.zapomen_uzivatele()`. Druhy radek
    # o teze akci by v logu jen prekazel.
    kdy = tasks.kdy_odlozene_preskladani()
    _flash(request,
           "Historie diváka {jmeno} je pryč ({n} přehrávání) – ze statistik "
           "hned. Do {kdy} ji jde vrátit zpět, potom bude smazaná nadobro.",
           "success",
           jmeno=vysledek["jmeno"] or user_id, n=vysledek["smazano"],
           kdy=kdy.strftime("%d.%m. %H:%M") if kdy else "?")
    return RedirectResponse("/settings?section=tasks#odklizeni",
                            status_code=303)


@router.post("/settings/historie/vrat")
async def historie_vrat(request: Request, udalost: str = Form(""),
                        account: dict[str, Any] = Depends(require_admin)):
    """Vrátí do historie diváka, který je v koši.

    Není to nebezpečná akce - nic se nemaže, jen se vrací, co se před
    chvílí odklidilo - takže se na nic neptá a nemá vlastní okno.
    """
    vysledek = await asyncio.to_thread(odklizeni.vrat_z_kose, udalost)
    if not vysledek["vraceno"]:
        _flash(request,
               "Vrátit se nepodařilo - v koši už nic takového není.", "error")
    else:
        _flash(request, "Historie diváka {jmeno} je zpátky ({n} přehrávání).",
               "success", jmeno=vysledek["jmeno"], n=vysledek["vraceno"])
    return RedirectResponse("/settings?section=tasks#odklizeni",
                            status_code=303)


@router.post("/settings/tasks")
async def tasks_save(
    request: Request,
    backup_path: str = Form(""),
    backup_keep: str = Form("7"),
    pg_dump_path: str = Form(""),
    history_retention_days: str = Form(""),
    account: dict[str, Any] = Depends(require_admin),
):
    """Ulozi nastaveni vsech uloh najednou.

    Zaskrtavatka se do formulare posilaji jen kdyz jsou zaskrtnuta - proto
    se ctou primo z tela pozadavku, ne jako pojmenovane parametry.
    Nezaskrtnute pole se v datech vubec neobjevi.
    """
    form = await request.form()

    for task in tasks.TASKS.values():
        enabled = "1" if form.get(f"enabled_{task.key}") else "0"
        db.set_setting(task.enabled_setting, enabled)

        if task.je_denni:
            # Hodina a minuta chodí ze dvou polí zvlášť, tady se z nich
            # zase složí "HH:MM". `_clamp` ohlídá rozsah a doplní nulu
            # (napsané "3" a "5" je "03:05"), `platny_cas` je poslední
            # pojistka: co by přesto neodpovídalo tvaru, se uloží jako
            # výchozí, ne jako rozbitý rozvrh.
            #
            # Náhradou při nesmyslu je to, co je uložené teď: vymazané
            # pole tak rozvrh nezmění. Nula by z něj tiše udělala půlnoc.
            soucasne = tasks.denni_cas(task).split(":")
            hodina = _clamp(str(form.get(f"time_{task.key}_h", "")),
                            0, 23, int(soucasne[0]))
            minuta = _clamp(str(form.get(f"time_{task.key}_m", "")),
                            0, 59, int(soucasne[1]))
            cas = f"{int(hodina):02d}:{int(minuta):02d}"
            db.set_setting(task.time_setting,
                           tasks.platny_cas(cas, task.default_time))
        else:
            db.set_setting(
                task.interval_setting,
                _clamp(str(form.get(f"minutes_{task.key}", task.default_minutes)),
                       0, 10080, task.default_minutes),
            )

    # Kolik historie se necha. Ulozi se i kdyz je odklizeni vypnute -
    # az se zapne, ma platit cislo, ktere clovek videl na strance.
    db.set_setting(odklizeni.RETENCE_KLIC,
                   _clamp(history_retention_days, odklizeni.MIN_DNU,
                          odklizeni.MAX_DNU, odklizeni.VYCHOZI_DNU))
    db.set_setting("backup_path", backup_path.strip())
    db.set_setting("backup_keep", _clamp(backup_keep, 1, 365, 7))
    db.set_setting("pg_dump_path", pg_dump_path.strip())

    _flash(request, "Nastavení úloh uloženo.", "success")
    return RedirectResponse("/settings?section=tasks", status_code=303)


@router.get("/settings/backup/download")
def backup_download(name: str = "",
                    account: dict[str, Any] = Depends(require_admin)):
    """Stáhne jednu zálohu databáze.

    Jméno se ověří proti tomu, co ve složce se zálohami doopravdy leží -
    viz `tasks.backup_file()`. Bez toho by šlo přes adresu stáhnout
    libovolný soubor ze stroje.
    """
    cesta = tasks.backup_file(name)
    if cesta is None:
        raise HTTPException(status_code=404, detail="Taková záloha tu není.")

    # `media_type` schválně octet-stream: prohlížeč soubor uloží, místo
    # aby se pokusil zobrazit SQL jako text ve stránce.
    return FileResponse(cesta, filename=cesta.name,
                        media_type="application/octet-stream")


@router.post("/settings/backup/delete")
def backup_delete(request: Request, name: str = Form(""),
                  account: dict[str, Any] = Depends(require_admin)):
    """Smaže jednu zálohu."""
    if tasks.delete_backup(name):
        _flash(request, "Záloha {nazev} smazána.", "success", nazev=name)
    else:
        _flash(request, "Takovou zálohu se nepodařilo najít.", "error")
    return RedirectResponse("/settings?section=tasks", status_code=303)


@router.post("/settings/backup/restore")
async def backup_restore(request: Request, name: str = Form(""),
                         account: dict[str, Any] = Depends(require_admin)):
    """Obnoví databázi ze zálohy a restartuje aplikaci.

    Restart není kosmetika: aplikace má v paměti nastavení i otevřená
    spojení do databáze, která po obnově už neplatí.
    """
    vysledek = await asyncio.to_thread(tasks.restore_backup, name)
    if vysledek.get("status") != "ok":
        _flash(request, vysledek.get("message", "Obnova selhala."), "error")
        return RedirectResponse("/settings?section=tasks", status_code=303)

    _flash(request,
           "Databáze obnovena ze zálohy {nazev}. Stav před obnovou zůstal "
           "uložený jako {zaloha}. Aplikace se restartuje.",
           "success", nazev=name, zaloha=vysledek["safety"])
    _naplanuj_restart()
    return RedirectResponse("/settings?section=tasks&wait=restart", status_code=303)


@router.post("/settings/tasks/run")
async def tasks_run(
    request: Request,
    key: str = Form(...),
    account: dict[str, Any] = Depends(require_admin),
):
    """Rucni spusteni ulohy.

    Ulohu odpalime na pozadi a hned odpovime - u velke knihovny by jinak
    prohlizec cekal nekolik minut a vypadalo by to jako zamrznuti.
    """
    task = tasks.TASKS.get(key)
    if task is None:
        _flash(request, "Neznámá úloha.", "error")
    elif scanner.is_scan_running() and key in ("sync", "recent", "tech"):
        # "recent" tu drive chybelo, takze slo spustit soubezne se scanem -
        # a druha uloha pak jen narazila na zamek a tise skoncila.
        _flash(request, "Jiná úloha už běží, počkej na její dokončení.", "warning")
    else:
        asyncio.create_task(tasks.run_now(key))
        # Název úlohy je česká konstanta z tasks.py - v anglickém
        # rozhraní se musí přeložit stejně jako zbytek hlášky.
        _flash(request, "{uloha}: {stav}", "info",
               uloha=i18n.translate(task.name),
               stav=i18n.translate("spuštěno."))
        # Stranka si pocka a po dokonceni se obnovi sama - stejne jako
        # u synchronizace knihovny.
        return RedirectResponse("/settings?section=tasks&wait=task", status_code=303)

    return RedirectResponse("/settings?section=tasks", status_code=303)


@router.post("/settings/import/detect")
async def import_detect(request: Request, account: dict[str, Any] = Depends(require_admin)):
    """Zjisti, jestli je v Jellyfinu plugin Playback Reporting."""
    available, message = await importers.playback_reporting_available()
    _flash(request, message, "success" if available else "warning")
    return RedirectResponse(ZPET_NA_IMPORT, status_code=303)


@router.post("/settings/import/playback-reporting")
async def import_playback_reporting(
    request: Request,
    min_seconds: str = Form("60"),
    account: dict[str, Any] = Depends(require_admin),
):
    result = await importers.import_playback_reporting(
        min_seconds=int(_clamp(min_seconds, 0, 3600, 60))
    )
    _hlaska_importu(
        request, result,
        "Playback Reporting: naimportováno {n} záznamů "
        "(z {nalezeno} nalezených, {duplicit} už existovalo).")

    return RedirectResponse(ZPET_NA_IMPORT, status_code=303)


@router.post("/settings/import/playback-reporting-file")
async def import_playback_reporting_file(
    request: Request,
    backup: UploadFile = File(...),
    min_seconds: str = Form("60"),
    account: dict[str, Any] = Depends(require_admin),
):
    """Import ze zalohy pluginu Playback Reporting (soubor TSV).

    Zaloha pro pripad, kdy plugin pres API nefunguje - viz
    importers.import_playback_reporting_tsv().
    """
    return await _import_ze_souboru(
        request, backup, min_seconds, importers.import_playback_reporting_tsv,
        "Playback Reporting (záloha): naimportováno {n} záznamů "
        "(z {nalezeno} nalezených, {duplicit} už existovalo).")


@router.post("/settings/history/tidy")
async def history_tidy(request: Request,
                       account: dict[str, Any] = Depends(require_admin)):
    """Narovnání dat - jedno tlačítko pro celý úklid historie.

    Dřív to byly dvě akce a člověk musel vědět, kterou pustit dřív.
    Pořadí přitom není na výběr: dohledání v Jellyfinu vyrábí vazby, se
    kterými pracuje všechno ostatní. Proto jedna routa - a stejnou funkci
    pouští i naplánovaná úloha, takže se ruční a automatický běh nemůžou
    rozejít.

    Pouštět se to dá opakovaně; podruhé už nenajde nic.
    """
    vysledek = await importers.narovnej_data()

    jf = vysledek.get("jellyfin") or {}
    if jf.get("status") == "error":
        # Jellyfin je jen první krok. Zbytek proběhl, takže to není chyba
        # celé akce - ale musí to být vidět, jinak by člověk marně čekal,
        # že se osiřelé záznamy dohledají.
        _flash(request, "Jellyfin neodpověděl ({duvod}), zbytek proběhl.",
               "warning", duvod=jf.get("message", "?"))
    elif not vysledek["casti"]:
        _flash(request, "Nebylo co narovnávat, historie je v pořádku.", "success")
    else:
        vety = [_t(sablona).format(**hodnoty)
                for sablona, hodnoty in vysledek["casti"]]
        zprava = ", ".join(vety)
        if vysledek["zbyva"]:
            zprava += ". " + _t("Zbývá {n} nezařazených záznamů.").format(
                n=vysledek["zbyva"])
        _flash(request, "Narovnání dat: {co}", "success", co=zprava)

    return RedirectResponse("/settings?section=tasks#uklid", status_code=303)


@router.get("/settings/history/orphans", response_class=HTMLResponse)
def history_orphans(request: Request, priradit: str = "", q: str = "",
                    account: dict[str, Any] = Depends(require_admin)):
    """Seznam záznamů, které se nepodařilo zařadit - i s důvodem.

    Nic se nikam neukládá, seznam se počítá pokaždé znovu. Uložený by
    ukazoval stav po posledním úklidu, tedy něco, co už nemusí platit -
    mezitím mohla proběhnout synchronizace nebo další import.
    """
    osirele = importers.rozbor_osirelych()
    vybrany = None
    if priradit:
        vybrany = next((o for o in osirele if str(o["item_id"]) == priradit), None)

    return templates.TemplateResponse(request, "orphans.html", _context(
        request, account,
        orphans=osirele,
        duvody=importers.DUVODY_POPIS,
        vybrany=vybrany,
        hledat=q,
        kandidati=importers.kandidati_pro_osireleho(q) if vybrany else [],
    ))


@router.post("/settings/history/assign")
def history_assign(request: Request, item_id: str = Form(""),
                   target_id: str = Form(""),
                   account: dict[str, Any] = Depends(require_admin)):
    """Ruční přiřazení osiřelých záznamů k položce z knihovny."""
    vysledek = importers.prirad_rucne(item_id.strip(), target_id.strip())
    if vysledek.get("status") != "ok":
        _flash(request, vysledek.get("message", "Nepovedlo se."), "error")
    else:
        _flash(request, "Přiřazeno k „{nazev}“ – {n} záznamů.", "success",
               nazev=vysledek["name"], n=vysledek["rows"])
    return RedirectResponse("/settings/history/orphans", status_code=303)


@router.post("/settings/import/jellystat")
async def import_jellystat(
    request: Request,
    backup: UploadFile = File(...),
    min_seconds: str = Form("60"),
    account: dict[str, Any] = Depends(require_admin),
):
    """Import z nahraneho JSON souboru se zalohou Jellystatu."""
    return await _import_ze_souboru(
        request, backup, min_seconds, importers.import_jellystat_json,
        "Jellystat: naimportováno {n} záznamů "
        "(z {nalezeno} nalezených, {duplicit} už existovalo).")


@router.post("/settings/blocks/unblock")
def blocks_unblock(request: Request, ip: str = Form(""),
                   account: dict[str, Any] = Depends(require_admin)):
    """Zruší blokaci přihlašování pro jednu adresu."""
    if accounts.odblokuj(ip.strip()):
        _flash(request, "Adresa {ip} je odblokovaná.", "success", ip=ip.strip())
    else:
        _flash(request, "Taková blokace v seznamu není.", "warning")
    return RedirectResponse("/settings?section=blocks", status_code=303)


@router.post("/settings/blocks/permanent")
def blocks_permanent(request: Request, ip: str = Form(""),
                     account: dict[str, Any] = Depends(require_admin)):
    """Zablokuje adresu natrvalo - dokud ji správce sám nepustí."""
    adresa = ip.strip()
    if not adresa:
        _flash(request, "Chybí adresa.", "error")
    else:
        accounts.zablokuj_natrvalo(adresa)
        _flash(request, "Adresa {ip} je zablokovaná natrvalo.", "success",
               ip=adresa)
    return RedirectResponse("/settings?section=blocks", status_code=303)


@router.post("/settings/accounts/create")
def account_create(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    password_again: str = Form(""),
    is_admin: str = Form(""),
    account: dict[str, Any] = Depends(require_admin),
):
    try:
        accounts.create(username, password, password_again, is_admin=bool(is_admin))
        _flash(request, "Účet '{jmeno}' vytvořen.", "success", jmeno=username)
    except accounts.AccountError as exc:
        _flash(request, exc.prelozena(), "error")
    return RedirectResponse("/settings?section=accounts", status_code=303)


@router.post("/settings/accounts/password")
def account_password(
    request: Request,
    account_id: int = Form(...),
    password: str = Form(""),
    password_again: str = Form(""),
    account: dict[str, Any] = Depends(require_login),
):
    """Zmena hesla.

    Sve vlastni heslo si smi zmenit kazdy. Cizi jen spravce - proto tahle
    routa nepouziva require_admin, ale kontroluje opravneni sama.
    """
    if account_id != account["id"] and not account["is_admin"]:
        raise HTTPException(status_code=403, detail="Cizi heslo smi menit jen spravce.")

    try:
        accounts.set_password(account_id, password, password_again)
        _flash(request, "Heslo změněno.", "success")
    except accounts.AccountError as exc:
        _flash(request, exc.prelozena(), "error")
    return RedirectResponse("/settings?section=accounts", status_code=303)


@router.post("/settings/accounts/role")
def account_role(
    request: Request,
    account_id: int = Form(...),
    is_admin: str = Form(""),
    account: dict[str, Any] = Depends(require_admin),
):
    try:
        accounts.set_admin(account_id, bool(is_admin))
        _flash(request, "Oprávnění změněno.", "success")
    except accounts.AccountError as exc:
        _flash(request, exc.prelozena(), "error")
    return RedirectResponse("/settings?section=accounts", status_code=303)


@router.post("/settings/api/token")
def api_token_novy(request: Request, name: str = Form(""),
                   account: dict[str, Any] = Depends(require_admin)):
    """Vyrobí token pro čtecí API a jednou ho ukáže."""
    vysledek = api.vytvor(name)
    # Do relace, ne do adresy: adresa se objeví v logu proxy i v historii
    # prohlížeče, a tohle je jediná chvíle, kdy je token čitelný.
    request.session["novy_api_token"] = vysledek["token"]
    _flash(request, "Klíč „{jmeno}“ vznikl.", "success",
           jmeno=vysledek["jmeno"])
    return RedirectResponse("/settings?section=api", status_code=303)


@router.post("/settings/api/token/zrusit")
def api_token_zrusit(request: Request, token_id: str = Form(""),
                     account: dict[str, Any] = Depends(require_admin)):
    """Zneplatní jeden token. Ostatních se to nedotkne."""
    try:
        cislo = int(token_id)
    except (TypeError, ValueError):
        cislo = 0
    jmeno = api.zrus(cislo) if cislo else ""
    if jmeno:
        _flash(request, "Klíč „{jmeno}“ už neplatí.", "success",
               jmeno=jmeno)
    else:
        _flash(request, "Takový klíč tu není.", "info")
    return RedirectResponse("/settings?section=api", status_code=303)


@router.post("/settings/accounts/delete")
def account_delete(
    request: Request,
    account_id: int = Form(...),
    account: dict[str, Any] = Depends(require_admin),
):
    if account_id == account["id"]:
        _flash(request, "Vlastní účet smazat nemůžeš.", "warning")
        return RedirectResponse("/settings?section=accounts", status_code=303)

    try:
        accounts.delete(account_id)
        _flash(request, "Účet smazán.", "success")
    except accounts.AccountError as exc:
        _flash(request, exc.prelozena(), "error")
    return RedirectResponse("/settings?section=accounts", status_code=303)
