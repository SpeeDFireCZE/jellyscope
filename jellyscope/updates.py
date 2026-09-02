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

import asyncio
import html
import logging
import pathlib
import re
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

from . import db

# Slozka aplikace - odsud se aktualizuje a tady lezi requirements.txt.
KOREN = pathlib.Path(__file__).resolve().parent.parent

log = logging.getLogger("jellyscope.updates")

API = "https://api.github.com/repos/SpeeDFireCZE/jellyscope/releases/latest"
STRANKA = "https://github.com/SpeeDFireCZE/jellyscope/releases/latest"

# Klice v nastaveni. Vysledek se uklada, aby se nemuselo na sit pri
# kazdem nacteni stranky.
# Zapnuti a rozvrh drzi uloha "Kontrola aktualizaci" (viz tasks.py),
# takze klic je jeji. Dve mista, kde se totez zapina, jsou past: clovek
# vypne jedno a druhe mu bezi dal.
ZAPNUTO = "task_updates_enabled"
POSLEDNI_KONTROLA = "update_last_check"
NALEZENA_VERZE = "update_latest_version"
NALEZENA_ADRESA = "update_latest_url"
# Popis zmen z GitHubu (markdown). Ukazuje se v okne, ktere se otevre
# kliknutim na ukazatel nove verze - clovek ma videt, co si instaluje,
# driv nez na to klikne.
NALEZENE_POZNAMKY = "update_latest_notes"

# Delsi popis uz stejne nikdo necte a do nastaveni patri hodnota, ne
# clanek. Orizneme.
MAX_POZNAMEK = 20000

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

    # Kdy se ptat, rozhoduje rozvrh ulohy - odsud uz jen kontrola, ze
    # nekdo neposila dotazy castejí, nez je slusne.
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
    db.set_setting(NALEZENE_POZNAMKY, str(data.get("body") or "")[:MAX_POZNAMEK])

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
        "poznamky": poznamky_html(db.get_setting(NALEZENE_POZNAMKY, "")),
        # Aktualizovat z prohlizece jde jen tam, kde je z ceho a kde to
        # dava smysl. Kdyz ne, misto tlacitka se rekne proc - viz
        # duvod_bez_aktualizace().
        "lze_aktualizovat": lze_aktualizovat(),
        "duvod_bez_aktualizace": duvod_bez_aktualizace(),
    }


def lze_aktualizovat() -> bool:
    """Da se aktualizovat rovnou z aplikace?"""
    return duvod_bez_aktualizace() == ""


def duvod_bez_aktualizace() -> str:
    """Proc aktualizace z prohlizece nejde. Prazdne = jde.

    Vraci hotovou vetu, protoze "nejde to" bez duvodu posle cloveka
    hledat chybu u sebe. Kazdy z techto pripadu ma jinou spravnou
    odpoved, a ta se ma rict rovnou.
    """
    from .config import load_config

    config = load_config()
    if config.demo_mode:
        return "Tohle je ukázka – aktualizovat se v ní nedá."

    # Odmitame jen NAS obraz, ne kazdy kontejner.
    #
    # V nasem obrazu je aplikace soucasti vrstvy: `git pull` by sice mohl
    # projit (kdyz si nekdo postavil obraz i s .git), jenze zmena by zila
    # do dalsiho prestaveni a pak by se tise vratila stara verze. To je
    # horsi nez tlacitko, ktere nefunguje.
    #
    # V cizim kontejneru (LXC, cizi image, Podman) muze byt aplikace
    # nainstalovana uplne bezne z gitu - a tam `git pull` funguje jako
    # kdekoliv jinde. Drive tam sedela hlaska "prestav obraz", ktera
    # nedavala smysl: zadny takovy obraz ten clovek nema.
    if config.nas_obraz:
        return ("V kontejneru se aktualizuje přestavěním obrazu: "
                "git pull && docker compose up -d --build")

    if not (KOREN / ".git").is_dir():
        return ("Aktualizovat z prohlížeče jde jen tam, kde je aplikace "
                "stažená z gitu. Jinak platí deploy/update.sh.")
    return ""


def poznamky_html(text: str) -> str:
    """Popis vydani z markdownu do HTML - jen to, co GitHub opravdu posila.

    Zamerne bez knihovny na markdown: poznamky k vydani pisu sam a vejdou
    se do peti znacek. Vsechno projde escapovanim JAKO PRVNI, takze i
    kdyby v poznamkach byla znacka, do stranky se dostane jako text.

    Umi: nadpisy (###), odrazky (-), **tucne**, `kod` a odstavce.
    """
    if not text:
        return ""

    hotovo: list[str] = []
    v_seznamu = False

    def inline(radek: str) -> str:
        radek = html.escape(radek)
        radek = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", radek)
        radek = re.sub(r"`([^`]+)`", r"<code>\1</code>", radek)
        return radek

    for radek in text.replace("\r\n", "\n").split("\n"):
        holy = radek.strip()
        if holy.startswith("#"):
            if v_seznamu:
                hotovo.append("</ul>")
                v_seznamu = False
            hotovo.append(f"<h4>{inline(holy.lstrip('#').strip())}</h4>")
        elif holy.startswith(("- ", "* ")):
            if not v_seznamu:
                hotovo.append("<ul>")
                v_seznamu = True
            hotovo.append(f"<li>{inline(holy[2:])}</li>")
        elif not holy:
            if v_seznamu:
                hotovo.append("</ul>")
                v_seznamu = False
        elif v_seznamu:
            # Pokracovani odrazky na dalsim radku - patri do te posledni.
            hotovo[-1] = hotovo[-1][:-len("</li>")] + " " + inline(holy) + "</li>"
        else:
            hotovo.append(f"<p>{inline(holy)}</p>")

    if v_seznamu:
        hotovo.append("</ul>")
    return "".join(hotovo)


async def aktualizuj() -> dict[str, Any]:
    """Stahne novou verzi a doinstaluje zavislosti. Nerestartuje.

    Deleji se presne dva kroky z `deploy/update.sh` - `git pull` a
    `pip install`. Restart si rika volajici sam (viz web._naplanuj_restart),
    protoze aplikace umi nahradit svuj proces a nepotrebuje k tomu
    spravce sluzby.

    Zalohu si nedelame tady: bezi bud denni uloha, nebo si ji clovek
    spusti tlacitkem. Delat ji potichu pri kazde aktualizaci by znamenalo
    kopii databaze, o kterou nikdo nezadal.

    Kdyz jsou ve slozce vlastni upravy, aktualizace se NEDELA - `git pull`
    by je bud prepsal, nebo skoncil konfliktem uprostred. Radeji to rekneme.
    """
    duvod = duvod_bez_aktualizace()
    if duvod:
        return {"status": "error", "message": duvod}

    zmeny = await _git("diff", "--quiet")
    if zmeny["kod"] != 0:
        return {"status": "error",
                "message": "Ve složce aplikace jsou vlastní úpravy. "
                           "Aktualizace by o ně přišla, tak jsem ji nespustil."}

    pull = await _git("pull", "--ff-only")
    if pull["kod"] != 0:
        return {"status": "error", "message": pull["vystup"][-400:]}

    pip = await _spust(sys.executable, "-m", "pip", "install", "--quiet",
                       "-r", str(KOREN / "requirements.txt"))
    if pip["kod"] != 0:
        return {"status": "error", "message": pip["vystup"][-400:]}

    log.info("aktualizace stazena: %s", pull["vystup"].strip().splitlines()[-1:])
    return {"status": "ok", "vystup": pull["vystup"]}


async def _git(*argumenty: str) -> dict[str, Any]:
    return await _spust("git", "-C", str(KOREN), *argumenty)


async def _spust(*prikaz: str) -> dict[str, Any]:
    """Spusti prikaz a vrati navratovy kod i vystup dohromady."""
    proces = await asyncio.create_subprocess_exec(
        *prikaz,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=str(KOREN),
    )
    vystup, _ = await proces.communicate()
    return {"kod": proces.returncode,
            "vystup": vystup.decode("utf-8", "replace")}
