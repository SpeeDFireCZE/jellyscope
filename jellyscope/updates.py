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
# Kolik prekladovych souboru se na `main` zmenilo od commitu, ktery bezi.
# Preklady z Weblate prichazeji slucovanim, ne vydanim, takze je kontrola
# podle cisla verze nikdy nezachyti - hlida se to zvlast.
NOVE_PREKLADY = "update_translations_pending"

# Porovnani commitu na GitHubu: co je na `main` a u nas jeste ne.
POROVNANI = ("https://api.github.com/repos/SpeeDFireCZE/jellyscope"
             "/compare/{zaklad}...main")
SLOZKA_PREKLADU = "jellyscope/translations/"

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

    # Preklady zvlast: nemaji cislo verze, kterym by se daly poznat.
    # Kdyz se to nepovede, zustane, co bylo - lepsi nez zahodit
    # posledni znamy stav kvuli jednomu vypadku site.
    pocet = await _novych_prekladu(client_factory=httpx.AsyncClient)
    if pocet is not None:
        db.set_setting(NOVE_PREKLADY, str(pocet) if pocet else "")
        if pocet:
            log.info("na GitHubu pribylo prekladu: %s souboru", pocet)
    return {**stav(), "status": "ok"}


async def _muj_commit() -> str:
    """Commit, ze ktereho aplikace bezi. Prazdno, kdyz to neni git."""
    if not (KOREN / ".git").is_dir():
        return ""
    vysledek = await _git("rev-parse", "HEAD")
    if vysledek["kod"] != 0:
        return ""
    return vysledek["vystup"].strip()


async def _novych_prekladu(client_factory: Any) -> int | None:
    """Kolik prekladovych souboru se na `main` zmenilo od naseho commitu.

    None znamena "nevim" - neni to git, commit GitHub nezna (treba vlastni
    vetev) nebo sit nefunguje. To se od nuly lisi: nula rika "nic noveho",
    None rika "neptej se me".

    Pocitaji se jen soubory ve slozce s preklady. Commit do dokumentace
    nebo do kodu tu nema co hlasit - na ten je vydani.
    """
    if duvod_bez_aktualizace():
        return None
    commit = await _muj_commit()
    if not commit:
        return None
    try:
        async with client_factory(timeout=20) as client:
            odpoved = await client.get(
                POROVNANI.format(zaklad=commit),
                headers={"Accept": "application/vnd.github+json"})
            if odpoved.status_code == 404:
                # Nas commit GitHub nezna - vlastni vetev, lokalni prace.
                return None
            odpoved.raise_for_status()
            data = odpoved.json()
    except Exception as chyba:  # noqa: BLE001 - sit selhava mnoha zpusoby
        log.warning("porovnani s GitHubem se nepodarilo: %s", chyba)
        return None
    return _pocet_jen_prekladu(
        [str(soubor.get("filename") or "") for soubor in data.get("files") or []])


def _pocet_jen_prekladu(soubory: list[str]) -> int:
    """Kolik z nich je překladů - ale jen když tam nic jiného není.

    Aktualizace je `git pull` celého `main`. Kdyby tam vedle překladů
    ležel i kód (vydání, které ještě nevyšlo - tag je, CI spadlo, Release
    nevznikl), hlásili bychom „nové překlady" a kliknutí by stáhlo i ten
    kód. Takže: cokoliv mimo složku s překlady znamená „počkej na
    vydání" a překlady se nehlásí vůbec.
    """
    preklady = [s for s in soubory if s.startswith(SLOZKA_PREKLADU)]
    if len(preklady) != len(soubory):
        if preklady:
            log.info("na main je vedle prekladu i kod - preklady se nehlasi, "
                     "prijdou s vydanim")
        return 0
    return len(preklady)


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
        # Preklady, ktere na `main` pribyly od naseho commitu. Ukazuji se
        # jen kdyz neni k dispozici nove vydani - to je zahrnuje.
        "nove_preklady": int(db.get_setting(NOVE_PREKLADY, "") or 0),
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
    `pip install`. Restart si rika volajici sam (viz web_nastaveni._naplanuj_restart),
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

    # Bez vydaneho noveho vydani se smi stahnout jen preklady. Kontrola
    # to hlidala uz pri porovnani, ale mezi kontrolou (rano) a kliknutim
    # (vecer) se main mohl pohnout - tak se to overi znovu, na tom, co by
    # `git pull` doopravdy prinesl.
    if not stav()["je_novejsi"]:
        navic = await _co_prijde_mimo_preklady()
        if navic is None:
            return {"status": "error",
                    "message": "Nepodařilo se zjistit, co by aktualizace "
                               "stáhla - zkus to za chvíli."}
        if navic:
            return {"status": "error",
                    "message": "Na GitHubu je od tvé verze víc než překlady "
                               "(kód, který ještě nevyšel jako vydání). "
                               "Aktualizace počká, až vyjde."}

    pull = await _git("pull", "--ff-only")
    if pull["kod"] != 0:
        return {"status": "error", "message": pull["vystup"][-400:]}

    pip = await _spust(sys.executable, "-m", "pip", "install", "--quiet",
                       "-r", str(KOREN / "requirements.txt"))
    if pip["kod"] != 0:
        return {"status": "error", "message": pip["vystup"][-400:]}

    log.info("aktualizace stazena: %s", pull["vystup"].strip().splitlines()[-1:])
    # Co se prave stahlo, uz neni "nove". Bez tohohle by hlaska o nových
    # prekladech visela az do dalsi kontroly, i kdyz uz jsou v aplikaci.
    db.set_setting(NOVE_PREKLADY, "")
    return {"status": "ok", "vystup": pull["vystup"]}


async def _co_prijde_mimo_preklady() -> list[str] | None:
    """Soubory, které by `git pull` změnil a nejsou překlady. None = nevím."""
    fetch = await _git("fetch", "--quiet")
    if fetch["kod"] != 0:
        return None
    rozdil = await _git("diff", "--name-only", "HEAD", "@{u}")
    if rozdil["kod"] != 0:
        return None
    return [radek.strip() for radek in rozdil["vystup"].splitlines()
            if radek.strip() and not radek.strip().startswith(SLOZKA_PREKLADU)]


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
