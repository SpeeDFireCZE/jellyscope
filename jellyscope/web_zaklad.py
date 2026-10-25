# -*- coding: utf-8 -*-
"""Základ webu: co potřebuje každá stránka.

Šablony, skládání kontextu, hlášky, závora přihlášení a pomocníci kolem
období. Bydlí to tady, a ne ve `web.py`, z jediného důvodu: routy jsou
rozdělené do víc souborů a tyhle věci potřebují všechny. Kdyby zůstaly
u rout, musely by se moduly importovat navzájem.

Nic z toho nezná `app`. Je to schválně - základ nesmí záviset na tom, jak
je aplikace poskládaná, jinak by se nedal použít odjinud.
"""
from __future__ import annotations

import functools
import logging
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from fastapi import HTTPException, Request, Response
from fastapi.templating import Jinja2Templates

from . import (accounts, collector, db, formatting, i18n, mapa_podrobna, pristup,
               scanner, sekce, stats, udalosti, updates)
from .config import load_config
from .i18n import translate as _t


log = logging.getLogger("jellyscope.web")


PACKAGE_DIR = Path(__file__).resolve().parent


TEMPLATES_DIR = PACKAGE_DIR / "templates"


config = load_config()


templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def current_account(request: Request) -> Optional[dict[str, Any]]:
    """Kdo je prihlaseny. Vrati None, kdyz nikdo.

    V cookie mame jen ID uctu (a to podepsane, takze ho nejde podvrhnout).
    Vsechno ostatni si radeji nacteme z databaze - kdyz uctu mezitim nekdo
    odebral prava, projevi se to hned, ne az po odhlaseni.
    """
    account_id = request.session.get("account_id")
    if not account_id:
        return None

    # Jeden dotaz na pozadavek, ne tri. Ucet chce middleware s jazykem,
    # rozsah_divaka i require_login - a obrazek nebo styl je taky pozadavek.
    # Klicem je i otisk z relace: po zmene hesla v temze pozadavku se ucet
    # nacte znovu.
    klic = (account_id, request.session.get("otisk"))
    ulozeny = getattr(request.state, "ucet_pozadavku", None)
    if ulozeny is not None and ulozeny[0] == klic:
        return ulozeny[1]

    account = accounts.get(int(account_id))
    if account is None:
        # Ucet byl mezitim smazan - cookie zahodime.
        request.session.clear()
    elif request.session.get("otisk") != accounts.otisk_relace(account):
        # Heslo se od prihlaseni zmenilo (sam, nebo spravce). Relace, ktera
        # o tom nevi, konci - jinak by ukradena cookie platila dal a zmena
        # hesla by nic neresila. Relace bez otisku (z doby pred touhle
        # kontrolou) konci taky: po aktualizaci se kazdy jednou prihlasi.
        request.session.clear()
        account = None
    request.state.ucet_pozadavku = (klic, account)
    return account


def prihlas_do_relace(request: Request, account: dict[str, Any]) -> None:
    """Zapíše přihlášení do relace - jedno místo pro heslo, kód i první účet.

    Stará relace se zahodí: brání to útoku, při kterém ti někdo podstrčí
    své ID relace ještě před přihlášením a pak se do ní „sveze". A otisk
    hesla nesmí chybět - relaci bez něj current_account() hned ukončí,
    takže cesta, která by na něj zapomněla, by člověka tiše odhlásila.
    """
    request.session.clear()
    request.session["account_id"] = account["id"]
    request.session["otisk"] = accounts.otisk_relace(account)


def require_login(request: Request) -> dict[str, Any]:
    """Zavora pred kazdou strankou. Vraci prihlaseny ucet."""
    if not accounts.any_exists():
        # Uplne prvni spusteni - jeste neexistuje zadny ucet.
        raise HTTPException(status_code=307, headers={"Location": "/setup"})

    account = current_account(request)
    if account is None:
        raise HTTPException(status_code=307, headers={"Location": "/login"})
    return account


# Pravidlo žije v pristup.py, kde ho sdílí i API klíče; odtud se jen
# vystavuje pod jménem, pod kterým ho bere web.py.
muj_divak = pristup.muj_divak


def require_admin(request: Request) -> dict[str, Any]:
    """Zavora pro veci, ktere smi jen spravce."""
    account = require_login(request)
    if not account["is_admin"]:
        raise HTTPException(status_code=403, detail="Tuhle akci smi jen spravce.")
    return account


def _rychla_obdobi() -> list[tuple[str, str, str]]:
    """Nabidka do okna s vlastnim obdobim - (popis, od, do) po cesku.

    Nejsou to dalsi tlacitka v prepinaci, ale predvyplneni poli: mesic
    a rok jsou otazky, na ktere se clovek pta casto, a pocitat si datum
    prvniho dne minuleho mesice v hlave je otrava.
    """
    dnes = date.today()
    prvni_tohoto = dnes.replace(day=1)
    posledni_minuleho = prvni_tohoto - timedelta(days=1)
    prvni_minuleho = posledni_minuleho.replace(day=1)
    return [
        ("tento měsíc", _cesky_datum(prvni_tohoto.isoformat()),
         _cesky_datum(dnes.isoformat())),
        ("minulý měsíc", _cesky_datum(prvni_minuleho.isoformat()),
         _cesky_datum(posledni_minuleho.isoformat())),
        ("letos", _cesky_datum(dnes.replace(month=1, day=1).isoformat()),
         _cesky_datum(dnes.isoformat())),
    ]


def _obdobi_do_sablony(zadani: Any) -> dict[str, Any]:
    """Co o obdobi potrebuje prepinac nahore na strance.

    Sablona pak nemusi resit, jestli dostala cislo nebo dvojici datumu -
    dostane obojí pripravene.
    """
    if isinstance(zadani, stats.Obdobi):
        # Mistni meze, ne ty z dotazu: v UTC by u pulnoci sedel jiny den.
        od = (zadani.od_mistni or zadani.od)[:10]
        do = _posledni_den(zadani.do_mistni or zadani.do)
        return {"days": None, "od": od, "do": do,
                # Do formulare patri datum tak, jak ho clovek pise.
                "od_text": _cesky_datum(od), "do_text": _cesky_datum(do),
                # Vyber tazenim v grafu umi i cast dne. Ve formulari se to
                # napsat neda (jsou tam kalendare), ale na strance to stat
                # musi - jinak vypada vyber "od 21:30 do 23:45" jako cely
                # den a cisla pod nim nedavaji smysl.
                "od_popis": _obdobi_popis(zadani, zadani.od_mistni, od),
                "do_popis": _obdobi_popis(zadani, zadani.do_mistni, do),
                "dny": zadani.dny, "vlastni": True,
                "cela_historie": zadani.cela_historie}
    return {"days": int(zadani), "od": "", "do": "",
            "od_text": "", "do_text": "",
            "od_popis": "", "do_popis": "",
            "dny": int(zadani), "vlastni": False, "cela_historie": False}


def _obdobi_popis(obdobi: stats.Obdobi, mistni: str, den: str) -> str:
    """Datum pro cloveka, s casem jen u useku vybraneho v grafu.

    U celych dnu se cas nepise - "20.8.2026 00:00" nikomu nic nerekne.
    U presneho useku ano, protoze bez nej vypada vyber "od 21:30 do 23:45"
    jako cely den a cisla pod nim nedavaji smysl.
    """
    if obdobi.cely_den or not mistni:
        return _cesky_datum(den)
    return f"{_cesky_datum(mistni[:10])} {mistni[11:16]}"


def _posledni_den(do: str) -> str:
    """Den, ktery jeste do obdobi patri.

    Horni mez je vylucna, takze se od ni couva - ale o VTERINU, ne o den.
    U celeho dne vyjde oboji stejne (pulnoc minus den i minus vterina
    padne na predchozi den), u useku vybraneho v grafu uz ne: konec
    ve 23:45 patri porad temuz dni, kdezto minus den by ukazal vcerejsek.
    """
    try:
        return (datetime.strptime(do, db.TIME_FORMAT)
                - timedelta(seconds=1)).strftime("%Y-%m-%d")
    except ValueError:
        return do[:10]


# Meze pro stropy dlouhých seznamů. Nula by znamenala "schovat i jediný
# stream", což je zbytečné; nad padesát už karta zabere obrazovku tak jako
# tak. Hodnota samotná je v nastavení - viz _stropy().
STROP_MIN = 1


STROP_MAX = 50


# Jak se ovlada priblizovani mapy. Kolecko je pohodlnejsi, ale nad mapou
# prestane rolovat stranka - u nekoho je to past, u nekoho zvyk. Proto
# volba, ne rozhodnuti za uzivatele.
ZOOM_REZIMY = ("click", "wheel")

# Co je pod teckami na mape. "kresleny" je obrys pevnin (offline), "osm"
# dlazdice z OpenStreetMap - ty si prohlizec stahuje z internetu, proto
# je to volba spravce a vychozi je vypnuta. Viz web.csp().
PODKLADY_MAPY = ("kresleny", "osm")


# Vzhledy aplikace. "novy" jsou barvy Jellyfinu, "klasicky" puvodni
# modra na neutralnim podkladu - viz konec style.css.
VZHLEDY = ("novy", "klasicky")


def _vzhled() -> str:
    """Vybrany vzhled. Cokoliv jineho nez zname jmeno je novy."""
    hodnota = db.get_setting("ui_skin", "novy")
    return hodnota if hodnota in VZHLEDY else "novy"


def _cas_presne() -> bool:
    """Ma se cas v grafech psat na minuty, nebo zaokrouhlene?"""
    return formatting.presny_cas()


def _stropy() -> dict[str, int]:
    """Kolik položek karta vypíše rovnou, než zbytek schová do okna.

    Čte se z nastavení, ne z konstanty: kolik streamů se vejde na
    obrazovku, ví ten, kdo se na ni dívá - u někoho je to dvojka, u
    někoho deset. Mění se v Nastavení → Rozhraní.
    """
    return {
        "strop_streamu": db.get_int_setting(
            "ui_max_streams", minimum=STROP_MIN, maximum=STROP_MAX, fallback=10),
        # Na mobilu se vejde míň: karta streamu tam zabírá celou šířku
        # a čtyři pod sebou už znamenají, že zbytek stránky je mimo
        # obrazovku. Rozhoduje o tom CSS, ne server - ten neví, na čem
        # se člověk dívá, tak pošle obojí a zobrazí se to, co platí.
        "strop_streamu_mobil": db.get_int_setting(
            "ui_max_streams_mobile", minimum=STROP_MIN, maximum=STROP_MAX,
            fallback=3),
        "strop_lidi": db.get_int_setting(
            "ui_max_viewers", minimum=STROP_MIN, maximum=STROP_MAX, fallback=10),
        # Pruh diváka je nižší než karta streamu, ale i tak jich na
        # telefon patří míň - pod deseti pruhy by legenda i tabulka pod
        # nimi začínaly až za druhou obrazovkou.
        "strop_lidi_mobil": db.get_int_setting(
            "ui_max_viewers_mobile", minimum=STROP_MIN, maximum=STROP_MAX,
            fallback=5),
        # Tabulky na strance Zjisteni: kolik radku rovnou, zbytek v okne.
        "strop_radku": db.get_int_setting(
            "ui_max_rows", minimum=STROP_MIN, maximum=STROP_MAX, fallback=10),
        "strop_radku_mobil": db.get_int_setting(
            "ui_max_rows_mobile", minimum=STROP_MIN, maximum=STROP_MAX,
            fallback=5),
    }


# Klíče kontextu, které jsou dokumentace (názvy a typy polí API), ne data
# o divácích - anonymizace na ně nesahá (viz _context).
NEANONYMIZOVAT = ("api_navod_pole",)


def _podklad_mapy() -> str:
    """Co je pod tečkami na mapě - kreslená mapa, nebo OpenStreetMap."""
    podklad = (db.get_setting("ui_map_podklad", "kresleny") or "").strip()
    return podklad if podklad in PODKLADY_MAPY else "kresleny"


def _zoom_rezim() -> str:
    """Cim se priblizuje mapa - koleckem, nebo klikanim."""
    rezim = (db.get_setting("ui_map_zoom", "click") or "").strip()
    return rezim if rezim in ZOOM_REZIMY else "click"


def _linked_note(result: dict[str, Any]) -> str:
    """Věta o dohledaných položkách - do hlášky po importu.

    Import neposílá tmdb ID, takže se položky dohledávají až po něm.
    Když se něco spárovalo, uživatel to má vědět: jinak by nechápal,
    proč se čísla v knihovně po importu změnila.
    """
    linked = result.get("linked") or {}
    if not linked.get("rows"):
        return ""
    casti = []
    if linked.get("by_tmdb"):
        casti.append(i18n.translate("{n} podle tmdb ID").format(n=linked["by_tmdb"]))
    if linked.get("by_episode"):
        casti.append(i18n.translate("{n} podle čísla dílu")
                     .format(n=linked["by_episode"]))
    if linked.get("by_name"):
        casti.append(i18n.translate("{n} podle názvu").format(n=linked["by_name"]))
    return " " + i18n.translate("Dohledáno {co} ({n} záznamů).").format(
        co=", ".join(casti), n=linked["rows"])


# Kam se člověk vrací po importu. Sem míří všechny čtyři routy
# v sekci Import, tak ať je adresa na jednom místě.
ZPET_NA_IMPORT = "/settings?section=import"


def _hlaska_importu(request: Request, result: dict[str, Any],
                    sablona: str) -> None:
    """Výsledek importu do hlášky. Pro všechny tři zdroje stejně.

    Liší se jen první věta (`sablona`); co následuje - dohledané položky,
    záznamy známé odjinud, opravené díly - platí pro všechny stejně.
    Byly to tři skoro totožné kusy kódu a dvakrát se stalo, že se nová
    věta doplnila jen do jednoho.
    """
    if result.get("status") != "ok":
        _flash(request, result.get("message", "Import selhal."), "error")
        return

    _flash(
        request,
        _t(sablona).format(n=result["imported"], nalezeno=result["found"],
                           duplicit=result["duplicate"])
        + _opraveno_note(result) + _known_note(result) + _linked_note(result),
        "success",
    )


def _veta(result: dict[str, Any], klic: str, sablona: str) -> str:
    """Věta k jednomu číslu z výsledku importu - nebo nic, když je nula.

    Nula se nevypisuje schválně: "0 záznamů se přeneslo" je věta, kterou
    nikdo nepotřebuje číst, a hláška po importu jich má i tak dost.
    """
    pocet = result.get(klic) or 0
    if not pocet:
        return ""
    return " " + i18n.translate(sablona).format(n=pocet)


def _opraveno_note(result: dict[str, Any]) -> str:
    """Věta o záznamech, které opakovaný import narovnal.

    Starší importy z Jellystatu braly u epizody id seriálu (viz
    importers.import_jellystat_json), takže celá historie seriálu visela
    na jednom identifikátoru. Když se tatáž záloha nahraje znovu, opraví
    se na místě - a tohle je jediné místo, kde se to člověk dozví.
    """
    return _veta(result, "repaired",
                 "{n} starších záznamů se přeneslo ze seriálu na konkrétní díl.")


def _known_note(result: dict[str, Any]) -> str:
    """Věta o záznamech, které už byly v databázi z jiného zdroje.

    Bez ní by import z druhého nástroje vypadal, že "skoro nic nenašel".
    Přitom nenašel nic **nového** - a to je dobře, právě proto se nic
    nezdvojilo.
    """
    return _veta(result, "known_elsewhere",
                 "{n} záznamů už v databázi bylo z jiného zdroje (z collectoru "
                 "nebo z druhého importu), takže se nezdvojily.")


def _flash(request: Request, message: str, level: str = "info",
           **hodnoty: Any) -> None:
    """Ulozi hlasku, ktera se ukaze na nasledujici strance.

    Preklad se dela uz **tady**, ne az v sablone. Duvod: hlaska se uklada do
    session a zobrazi se az na dalsi strance - kdyby si mezitim nekdo prepnul
    jazyk, ukazala by se v tom starem. Prelozit ji v okamziku, kdy vznikla,
    je jednoznacne.

    Hlasky s cislem nebo jmenem se predavaji jako **sablona a hodnoty
    zvlast**:

        _flash(request, "Naimportováno {n} záznamů.", "success", n=42)

    Nejdriv se prelozi sablona, teprve pak se do ni hodnoty dosadi. Obracene
    to nejde: hotova veta s cislem uvnitr v prekladovem slovniku neni
    a nikdy nebude - a prave takhle drive vsechny hlasky po importu
    a po dobehnuti ulohy zustavaly cesky, i kdyz mel clovek anglicke
    rozhrani.

    Kdyz se dosazeni nepovede (v sablone je jina znacka, nez jake prijdou
    hodnoty), radeji ukazeme neprelozenou vetu nez padneme - hlaska
    o vysledku ulohy nesmi shodit stranku.
    """
    text = i18n.translate(message)
    if hodnoty:
        try:
            text = text.format(**hodnoty)
        except (KeyError, IndexError, ValueError):
            # Zachranny scenar musi byt uplne hloupy: vratime syrovou
            # sablonu. Kdybychom tu zkusili dosadit znovu, spadli bychom
            # na tomtez - a shodili stranku kvuli hlasce o vysledku.
            log.warning("hlasku %r se nepodarilo doplnit hodnotami %r",
                        message, hodnoty)
            text = message
    request.session["flash"] = {"message": text, "level": level}


def _smi_pro(account: Optional[dict[str, Any]]):
    """Funkce pro šablonu: smí tenhle účet otevřít tuhle adresu?

    Správce smí všechno; nepřihlášený taky, protože pro něj se menu
    stejně nekreslí a záporná odpověď by jen schovala odkazy na
    přihlašovací stránce.
    """
    kdo = pristup.role(account)
    if kdo in ("", pristup.SPRAVCE):
        return lambda cesta: True
    return lambda cesta: pristup.smi_cestu(kdo, cesta, account)


def otisk_obrazku_uctu(account: Optional[dict[str, Any]]) -> str:
    """Otisk profilového obrázku účtu z Jellyfinu, nebo prázdno.

    Jen účet z Jellyfinu (místní žádný obrázek nemá) a jen když ho
    synchronizace u diváka našla. Otisk je z Jellyfinu hex - ale do adresy
    jde, tak se pro jistotu nechají jen písmena a číslice.
    """
    divak = str((account or {}).get("jellyfin_user_id") or "")
    # Jellyfin posílá totéž id jednou s pomlčkami, jednou bez (viz
    # accounts.hole_id) - porovnává se proto očištěné. Tabulka `users`
    # je malá (jeden řádek na diváka), projít ji celou nic nestojí.
    hole = accounts.hole_id(divak)
    if not hole:
        return ""
    try:
        radek = db.query_one(
            "SELECT image_tag FROM users"
            " WHERE REPLACE(LOWER(id), '-', '') = ? AND image_tag IS NOT NULL",
            (hole,))
    except Exception:  # noqa: BLE001 - obrázek je kosmetika, stránka kvůli němu nespadne
        # Třeba obnovená stará záloha bez sloupce - do restartu (migrace)
        # zůstane písmeno.
        log.warning("otisk profiloveho obrazku nejde precist", exc_info=True)
        return ""
    otisk = str((radek or {}).get("image_tag") or "")
    return "".join(c for c in otisk if c.isalnum())[:64]


def adresa_obrazku_uctu(account: Optional[dict[str, Any]]) -> str:
    """Adresa vlastního obrázku i s otiskem (nový obrázek = nová adresa)."""
    otisk = otisk_obrazku_uctu(account)
    return f"/account/obrazek?v={otisk}" if otisk else ""


def _context(request: Request, account: Optional[dict[str, Any]] = None,
             **extra: Any) -> dict[str, Any]:
    """Spolecna data pro kazdou stranku (stav sberace, hlasky, kdo je prihlaseny)."""
    # Stranka se statistikami posila `days` - bud cislo, nebo vlastni
    # obdobi. Prepinac nahore potrebuje obojí ve stejnem tvaru, tak mu ho
    # pripravime tady a ne v kazde route zvlast.
    if "days" in extra:
        extra.setdefault("obdobi", _obdobi_do_sablony(extra["days"]))

    base = {
        "collector_status": db.get_setting(collector.STATUS_KEY, "unknown"),
        "collector_error": db.get_setting(collector.ERROR_KEY, ""),
        "last_poll": db.get_setting(collector.LAST_POLL_KEY, ""),
        "flash": request.session.pop("flash", None),
        "active_count": stats.active_session_count(),
        "account": account,
        # Podle tohohle se v sablonach schovava to, co je o jinych lidech.
        # `je_spravce` je jen zkratka - rozhoduje `muj_divak`, tedy jestli
        # je ucet privazany k jednomu divakovi.
        "muj_divak": muj_divak(account),
        "je_spravce": bool(account and account.get("is_admin")),
        # Profilový obrázek z Jellyfinu v tlačítku Můj účet, nebo prázdno
        # (písmeno). Viz web.muj_obrazek(). Funkce, ne hodnota: dotaz do
        # databáze proběhne, jen když šablona tlačítko opravdu kreslí - ne
        # u výřezů obnovovaných každých pár vteřin.
        "obrazek_uctu": functools.cache(lambda: adresa_obrazku_uctu(account)),
        # Menu se kresli jen z toho, co clovek smi otevrit - tataz
        # otazka, kterou si klade `rozsah_divaka` ve web.py, i s pravy
        # jednoho uctu. Odkaz, za kterym ceka "sem nemas pristup", je
        # horsi nez zadny.
        "smi": _smi_pro(account),
        # Jazyk, ve kterem se stranka kresli (vlastni volba uctu, jinak
        # serveru), a zvlast jazyk serveru pro Nastaveni -> Obecne. Kdyby
        # se vyber jazyka serveru predvyplnil tim prvnim, spravce s vlastni
        # anglictinou by ji jednim ulozenim nastavil vsem.
        "ui_language": i18n.current_language(),
        "jazyk_serveru": i18n.jazyk_serveru(),
        # Dnesek pro pole s datem - dal do budoucnosti nema smysl
        # chodit, statistika by byla prazdna.
        "dnes": date.today().isoformat(),
        "dnes_text": _cesky_datum(date.today().isoformat()),
        # Tri nejcastejsi odpovedi na "jake obdobi". Klik jimi vyplni
        # pole, neodesle formular - at je videt, co se vybralo.
        "rychla_obdobi": _rychla_obdobi(),
        # `?wait=restart` / `?wait=task` v adrese říká stránce, ať si počká
        # a obnoví se sama, až bude na co. Nastavuje to routa přesměrováním
        # (viz /settings/restart a /settings/stop); pevný seznam hodnot,
        # aby se z adresy nedalo do stránky propašovat nic jiného.
        "wait_for": (request.query_params.get("wait")
                     if request.query_params.get("wait") in ("restart", "task")
                     else None),
        # Kolik úloh doběhlo v okamžiku vykreslení. Stránka to porovnává
        # s /health a podle změny pozná, že mezitím nějaká skončila.
        #
        # Musí to být v HTML, ne až z prvního dotazu na /health: úloha,
        # která skončí mezi vykreslením stránky a prvním dotazem, by jinak
        # propadla a nic by se neobnovilo.
        "tasks_version": scanner.tasks_version(),
        # Kdy nastartoval proces, ktery tuhle stranku vykreslil. Ze
        # stejneho duvodu jako `tasks_version` o radek vys: cekarna po
        # restartu potrebuje vedet, OD CEHO ceka, a nesmi si to zjistovat
        # az prvnim dotazem na /health. Restart prijde do vteriny, takze
        # ta prvni odpoved uz muze patrit novemu procesu - cekarna by si
        # ho zapsala jako vychozi stav a cekala na zmenu, ktera uz nikdy
        # neprijde.
        "beh_od": int(STARTED_AT),
        # Verze se ukazuje v patičce každé stránky - je to první údaj,
        # na který se u hlášení chyby ptá kdokoliv.
        "verze": updates.stav(),
        # Stropy pro dlouhé seznamy - viz _stropy().
        **_stropy(),
        "strop_min": STROP_MIN,
        "strop_max": STROP_MAX,
        # Čím se přibližuje mapa na stránce Síť - viz _zoom_rezim().
        "mapa_zoom": _zoom_rezim(),
        "mapa_podklad": _podklad_mapy(),
        # Je stažená podrobná mapa (v tvaru, který tahle verze umí)? Z
        # nastavení, ne dotazem na tabulku. Otisk jde do adresy dlaždic.
        "mapa_podrobna": mapa_podrobna.stav()["platna"],
        "mapa_podrobna_otisk": mapa_podrobna.stav()["otisk"],
        # Vzhled - viz VZHLEDY a konec style.css. Dosazuje se do <html>
        # rovnou na serveru, ne az JavaScriptem: jinak by stranka na
        # okamzik problikla v jednom vzhledu a prepnula se do druheho.
        "ui_skin": _vzhled(),
        "ui_cas_presne": _cas_presne(),
        # Sezónní výzdoba zapnutá správcem? (Rozhraní a Můj účet.)
        "ui_udalosti": udalosti.zapnuto(),
        # Zapnutý v nastavení? Tohle potřebuje přepínač v Rozhraní.
        "ui_dashboard": sekce.je_zapnuty(),
        # Záložka v menu (sekce.vidi_prehled). Prázdný přehled se ostatním
        # neukazuje - nemá jim co říct -, ale SPRÁVCE ho vidět musí: jinak
        # by ho zapnul a neměl kudy dovnitř, aby si ho sestavil. Funkce,
        # ne hodnota: rozvržení se načte, jen když se kreslí menu - ne
        # u každého výřezu, který se sám obnovuje.
        "vlastni_prehled": functools.cache(lambda: bool(account) and (
            sekce.vidi_prehled(account)
            or (account.get("is_admin") and sekce.je_zapnuty()))),
        # Název přehledu pro všechny (menu, strom práv, Nastavení).
        "nazev_prehledu": sekce.nazev(),
        # Má účet Můj přehled s aspoň jednou kartou? Funkce, ne hodnota:
        # dotaz proběhne, jen když se kreslí menu nebo karta na Mém účtu.
        "ma_muj_prehled": functools.cache(lambda: bool(
            account and sekce.nacti_rozvrzeni(account["id"]))),
    }
    base.update(extra)
    # Popis polí API (`{"device": "text", "user_id": "text"}`) je
    # dokumentace, ne data o divácích - anonymizace by z typu udělala
    # „Zařízení ?" a návod by přišel o typ pole. Jde mimo ni.
    dokumentace = {klic: base.pop(klic) for klic in NEANONYMIZOVAT if klic in base}
    # Az uplne nakonec: co se schova, se schova ve VSEM, co jde do
    # sablony - vcetne toho, co pridala routa pres `extra`. Jedno misto
    # pro vsechny stranky: kdyby se to resilo v sablonach, stacilo by
    # zapomenout na jednu - a zrovna ta by byla ta, kterou nekdo otevre.
    return {**pristup.anonymizuj_pro(base, account), **dokumentace}


def _cesky_datum(iso: Optional[str]) -> str:
    """Opak: 2026-08-01 -> 1.8.2026, pro vypsání zpátky do formuláře.

    Vlastní práci dělá `formatting.cesky_datum` - potřebuje ho i sekce
    vlastního přehledu a dvě kopie téhož převodu jsou o jednu moc.
    """
    return formatting.cesky_datum(iso)


# V cem se zadava kapacita uloziste. Klic je to, co stoji ve vyberu.
KAPACITA_JEDNOTKY = {"GB": 1024 ** 3, "TB": 1024 ** 4}


def _clamp(raw: str, minimum: int, maximum: int, fallback: int) -> str:
    try:
        value = int(float(raw))
    except (TypeError, ValueError):
        value = fallback
    return str(max(minimum, min(maximum, value)))


def _novy_klic(request: Request, klic: str) -> Optional[dict[str, Any]]:
    """Čerstvě vyrobený API klíč z relace - jednou, a hned se zahodí.

    V relaci je {"id", "token"}; podle id se klíč ukáže celý v řádku
    svého klíče (makro bunka_klice). Cokoliv jiného - třeba relace z doby,
    kdy tam byl jen text - se zahodí taky.
    """
    novy = request.session.pop(klic, None)
    if isinstance(novy, dict) and novy.get("token") and novy.get("id"):
        return novy
    return None


def _bez_mezipameti(odpoved: Response) -> Response:
    """Stránka, kterou prohlížeč nesmí schovat - je v ní celý API klíč.

    Bez toho by ji tlačítko Zpět vrátilo z mezipaměti (bfcache) i s
    klíčem, třeba i po odhlášení na sdíleném počítači. Klíč se má ukázat
    jen jednou.
    """
    odpoved.headers["Cache-Control"] = "no-store"
    return odpoved


def _cislo_z_formulare(text: Any) -> int:
    """Kladné celé číslo z formuláře nebo adresy; nesmysl = 0."""
    try:
        cislo = int(str(text).strip())
    except (TypeError, ValueError):
        return 0
    return cislo if cislo > 0 else 0


# Kdy tenhle proces nastartoval. Po restartu je hodnota jina - a to je
# jediny spolehlivy zpusob, jak z prohlizece poznat, ze uz bezi nova
# instance. Cekat na "prestane odpovidat a zase zacne" nestaci: restart
# trva chvilku a dotaz se do te mezery nemusi vubec trefit.
STARTED_AT = time.time()


SETTINGS_SECTIONS = [
    ("jellyfin", "Jellyfin", True),
    ("data", "Sběr dat", True),
    ("tasks", "Úlohy a zálohy", True),
    ("notifications", "Upozornění", True),
    ("import", "Import historie", True),
    ("database", "Databáze", True),
    ("accounts", "Účty", False),      # False = vidí i čtenář
    ("api", "API", True),
    ("blocks", "Blokace", True),
    ("log", "Log", True),
    ("interface", "Rozhraní", True),   # vzhled stránek
    ("general", "Obecné", True),
]
