"""Stránka Nastavení (všechny sekce) a uložení obecného formuláře.

Část podbalíku web/nastaveni (viz jellyscope/web/nastaveni/__init__.py) - kód
přesunutý beze změny z původního nastaveni.py. Co která sekce potřebuje
(kontext šablony), skládá její pomocník `_sekce_…` hned nad `settings_page`;
stránka sama jen vybere sekci a pomocníka zavolá."""
from __future__ import annotations

import json
import sys
from datetime import datetime
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from typing import Any

from ... import (
    accounts,
    api,
    api_dotaz,
    applog,
    db,
    dbmigrate,
    dialect,
    formatting,
    importers,
    jellyfin,
    odklizeni,
    pristup,
    scanner,
    stats,
    tasks,
)
from ...config import BASE_DIR as PROJECT_DIR
from ...i18n import translate as _t
from ..zaklad import (
    KAPACITA_JEDNOTKY,
    SETTINGS_SECTIONS,
    _bez_mezipameti,
    _cislo_z_formulare,
    _clamp,
    _context,
    _flash,
    _novy_klic,
    config,
    require_admin,
    require_login,
    templates,
)
from .spolecne import (
    CASTE_ZONY,
    _dny_v_tydnu,
    _draft_read,
    _generace_nesedi,
    _kapacita_do_formulare,
    _pocty_uklidu,
)
from .ucty import VOLBY_KLICE, _prava_root_klice, _strom_klice

router = APIRouter()


def _sekce_jellyfin(account: dict[str, Any]) -> dict[str, Any]:
    """Sekce Jellyfin: připojení, generace serveru a přihlášení diváků."""
    context: dict[str, Any] = {}
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
        # Prihlaseni divaku jellyfinovym heslem patri sem: je to
        # o Jellyfinu a jeho uctech, ne o uctech Jellyscope.
        # Klic i hesla divaku jdou na Jellyfin citelne - sifruje az
        # https. Kdyz adresa zacina http:// a nevede na tenhle stroj,
        # ma to byt videt.
        nesifrovane=jellyfin.po_siti_nesifrovane(
            rozepsane_jf.get("url") or db.get_setting("jellyfin_url", "")),
        divak_login=pristup.login_zapnuty(),
        divak_strom=pristup.strom(pristup.DIVAK),
        divak_anonymizace=pristup.anonymizace_zapnuta(pristup.DIVAK),
    )
    return context


def _sekce_upozorneni() -> dict[str, Any]:
    """Sekce Upozornění: kanály, uložená tajemství (jen jestli jsou) a dny v týdnu."""
    context: dict[str, Any] = {}
    from ... import notifikace

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
    return context


def _sekce_data() -> dict[str, Any]:
    """Sekce Data: ffprobe a odkud se bere volné místo."""
    context: dict[str, Any] = {}
    from ... import probe  # az tady, at start aplikace nic nezdrzuje
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
    return context


def _sekce_ulohy() -> dict[str, Any]:
    """Sekce Úlohy: plánované úlohy, zálohy, analýza souborů a odklízení historie."""
    context: dict[str, Any] = {}
    context.update(
        # Karta "Narovnání dat" se sem přestěhovala z Importu, takže
        # sem patří i čísla, která vypisuje.
        **_pocty_uklidu(),
        # Když úloha doběhne, zatímco je člověk na téhle sekci, načte
        # se stránka sama - jinak by tu proužek "úloha běží" zůstal
        # viset a výsledek by se neobjevil. Viz hlídač ve static/js/app.js.
        reload_on_task=True,
        task_list=tasks.all_statuses(),
        logrotate_dni=tasks.dni_rotace(),
        logrotate_pocet=tasks.pocet_rotace(),
        logrotate_dni_max=applog.DNI_MAX,
        logrotate_pocet_max=applog.POCET_MAX,
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
    return context


def _sekce_api(request: Request, account: dict[str, Any]) -> dict[str, Any]:
    """Sekce API: klíče, jejich práva, návod a právě vyrobený klíč."""
    context: dict[str, Any] = {}
    tokeny = api.seznam()
    context.update(
        api_tokeny=tokeny,
        # Root klíče: co smí (sloupec Typ a práva) a předvyplněné okno
        # Práva. Nový klíč začíná s ničím zaškrtnutým - práva se mu
        # nastavují vědomě.
        api_prava_klicu={t["id"]: _prava_root_klice(t["prava"])
                         for t in tokeny if t["account_id"] is None},
        api_novy_klic=_strom_klice([], historie=False, anonymizace=False, vse=False),
        # Okno „Jak se to používá" (_api_navod.html) - správci vyjdou
        # všechny adresy, ale seznam se předává vždycky výslovně.
        api_navod_adresy=api.povolene_adresy(account),
        api_navod_pole=api.pole_adres(api.povolene_adresy(account)),
        api_navod_operatory=api_dotaz.OPERATORY,
        # Nově vyrobený klíč se ukazuje jen jednou, hned po
        # přesměrování; drží se proto v relaci, ne v adrese - a při
        # dalším načtení stránky už ho nemá kdo vypsat. Ukáže se
        # v řádku svého klíče (makro bunka_klice).
        novy_token=_novy_klic(request, "novy_api_token"),
    )
    return context


def _sekce_blokace() -> dict[str, Any]:
    """Sekce Blokace: zablokované adresy a pravidla blokování."""
    context: dict[str, Any] = {}
    context.update(
        blocks=accounts.seznam_blokaci(),
        block_levels=accounts.STUPNE_BLOKACE,
        block_attempts=accounts.POKUSU_DO_BLOKACE,
        block_forget_hours=accounts.ZAPOMENUT_PO_HODINACH,
    )
    return context


def _sekce_import() -> dict[str, Any]:
    """Sekce Import: souhrn importované historie a počty k narovnání."""
    context: dict[str, Any] = {}
    context.update(
        import_stats=importers.import_summary(),
        last_import=scanner.last_scan("import"),
        # Kolik je v historii duplicitních a špatně přiřazených záznamů.
        # Ukazuje se předem, ať je vidět, jestli má úklid vůbec smysl.
        **_pocty_uklidu(),
    )
    return context


def _sekce_databaze(account: dict[str, Any]) -> dict[str, Any]:
    """Sekce Databáze: uložená a běžící konfigurace, rozepsaný formulář."""
    context: dict[str, Any] = {}
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
    return context


def _sekce_ucty() -> dict[str, Any]:
    """Sekce Účty: práva skupin a každého účtu bez práv správce."""
    context: dict[str, Any] = {}
    context.update(
        ctenar_strom=pristup.strom(pristup.CTENAR),
        ctenar_anonymizace=pristup.anonymizace_zapnuta(pristup.CTENAR),
        ctenar_smi_klic=pristup.smi_klic_skupina(pristup.CTENAR),
        divak_smi_klic=pristup.smi_klic_skupina(pristup.DIVAK),
    )
    vsechny = accounts.all_accounts()
    context.update(
        all_accounts=vsechny,
        # Kolik je správců - podle toho se u posledního z nich
        # neukáže tlačítko Smazat. Viz accounts.delete().
        admin_count=accounts.admin_count(),
        # Vlastní práva každého účtu bez práv správce: strom tak,
        # jak pro něj platí (vlastní, nebo zděděný od skupiny), a
        # jestli je vlastní. Správce žádný strom nemá - vidí všechno.
        prava_uctu={
            ucet["id"]: {
                "vlastni": pristup.vlastni_prava(ucet) is not None,
                "skupina": pristup.role(ucet),
                "strom": pristup.strom(pristup.role(ucet), ucet),
                "anonymizace": pristup.anonymizace_zapnuta(
                    pristup.role(ucet), ucet),
                # Vyjimka pro vlastni API klic: skupina / ano / ne.
                # Je zvlast od stromu a plati v obou rezimech. Volba
                # se cte zpatky z VOLBY_KLICE, at jsou obe strany jedna.
                "smi_klic": {hodnota: volba for volba, hodnota
                             in VOLBY_KLICE.items()}.get(
                    ucet.get("smi_klic"), "skupina"),
                "smi_klic_skupina": pristup.smi_klic_skupina(pristup.role(ucet)),
            }
            for ucet in vsechny if not ucet.get("is_admin")
        },
    )
    return context


def _sekce_log(log_file: str, log_lines: int, log_level: str, log_druh: str,
               log_ucet: str, log_klic: str, log_ip: str) -> dict[str, Any]:
    """Sekce Log: log aplikace, nebo log API s filtry z adresy."""
    context: dict[str, Any] = {}
    # Vstup z adresy nikdy nedůvěřuj - ani vlastnímu odkazu. Jméno
    # souboru si ověří applog sám (viz _bezpecna_cesta), tady stačí
    # počet řádků, úroveň a filtry.
    radku = log_lines if log_lines > 0 else applog.DEFAULT_LINES
    context.update(log_druh="api" if log_druh == "api" else "app", log_lines=radku)
    if context["log_druh"] == "api":
        klice = api.seznam()
        filtr_klic = _cislo_z_formulare(log_klic) or ""
        context.update(
            api_log=applog.read_api(radku, log_ucet.strip(), filtr_klic,
                                    log_ip.strip()[:64]),
            # Nabídka filtrů: klíče, které existují, a jejich vlastníci
            # („-" = root klíč). Smazaný klíč v logu zůstane, jen se
            # podle něj nedá vybrat.
            api_log_klice=klice,
            api_log_vlastnici=sorted({k["vlastnik"] or "-" for k in klice}),
            log_ucet=log_ucet.strip(), log_klic=filtr_klic,
            log_ip=log_ip.strip()[:64],
            log_zalohy=applog.zalohy(applog.API_NAME),
        )
    else:
        uroven = log_level if log_level in applog.levels() else ""
        vypis = applog.read_lines(log_file, radku, uroven)
        context.update(
            log=vypis,
            log_level=uroven,
            log_levels=applog.levels(),
            log_zalohy=applog.zalohy(vypis["name"]),
        )
    return context


@router.get("/settings", response_class=HTMLResponse)
def settings_page(
    request: Request,
    section: str = "jellyfin",
    # Volby prohlížeče logu. Jsou v adrese, ne v nastavení: je to pohled,
    # ne volba serveru - dva správci se můžou dívat každý na něco jiného.
    log_file: str = "",
    log_lines: int = 0,
    log_level: str = "",
    # Který log: aplikace („app"), nebo API. U logu API filtry: vlastník
    # klíče, klíč a IP. Text, ne číslo - nesmysl v adrese má dát celý
    # log, ne 422.
    log_druh: str = "",
    log_ucet: str = "",
    log_klic: str = "",
    log_ip: str = "",
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
        context.update(_sekce_jellyfin(account))
    elif section == "notifications":
        context.update(_sekce_upozorneni())
    elif section == "data":
        context.update(_sekce_data())
    elif section == "tasks":
        context.update(_sekce_ulohy())
    elif section == "api":
        context.update(_sekce_api(request, account))
    elif section == "blocks":
        context.update(_sekce_blokace())
    elif section == "import":
        context.update(_sekce_import())
    elif section == "database":
        context.update(_sekce_databaze(account))
    elif section == "accounts":
        context.update(_sekce_ucty())
    elif section == "log":
        context.update(_sekce_log(log_file, log_lines, log_level, log_druh,
                                  log_ucet, log_klic, log_ip))

    # Nabidka zon a aktualni cas v te zvolene - at je po ulozeni videt,
    # ze se opravdu neco zmenilo. Bez toho by clovek koukal na pole
    # s textem a musel hadat, jestli ta zona vubec plati.
    context.setdefault("casove_zony", CASTE_ZONY)
    context.setdefault("zona_ted",
                       datetime.now(formatting.zona()).strftime("%d.%m.%Y %H:%M"))

    odpoved = templates.TemplateResponse(
        request, "settings.html", _context(request, account, **context)
    )
    if context.get("novy_token"):
        _bez_mezipameti(odpoved)
    return odpoved


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

    try:
        parsed = json.loads(path_mappings or "[]")
        if not isinstance(parsed, list):
            raise ValueError
        db.set_setting("path_mappings", json.dumps(parsed))
    except ValueError:
        _flash(request, "Přepis cest není platný JSON, nechal jsem původní hodnotu.", "warning")
        return RedirectResponse("/settings?section=data", status_code=303)

    _flash(request, "Nastavení uloženo.", "success")
    return RedirectResponse("/settings?section=data", status_code=303)
