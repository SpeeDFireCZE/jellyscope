# -*- coding: utf-8 -*-
"""Podrobná offline mapa: Natural Earth 1:10M rozřezaná na dlaždice.

Zabudovaný obrys pevnin (worldmap, 1:110M) stačí na celý svět, ale při
přiblížení je hrubý. Podrobnou mapu si správce stáhne na kliknutí na
stránce Síť - je to vedle GeoLite2 jediná chvíle, kdy kvůli mapě aplikace
sáhne na internet. Stahuje se **zamčená verze** (VERZE) z repozitáře
Natural Earth na GitHubu; data jsou volné dílo (public domain).

Celý soubor je na stránku moc velký (pevnina sama má 10 MB), a tak se
po stažení rozřeže na dlaždice ve dvou podrobnostech (PODROBNOSTI) a
uloží do hlavní databáze - zálohuje se s ní a převod SQLite -> PostgreSQL
ji přenese. Prohlížeč si pak bere jen dlaždice výřezu, na který se člověk
dívá (web.py, /network/mapa/dlazdice/...).

Plochy (pevnina, jezera, města) se kreslí jen výplní, aby na hranách
dlaždic nebyly vidět švy; čáry (řeky, hranice) se ořezávají jako úsečky.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
from pathlib import Path
from typing import Any, Iterator

from . import config, db
from .i18n import translate as _t
from .geometrie import rozrez_caru, rozrez_plochu, zjednodus
from .worldmap import na_mapu

log = logging.getLogger("jellyscope.mapa_podrobna")

VERZE = "v5.1.2"
ZDROJ = ("https://raw.githubusercontent.com/nvkelso/natural-earth-vector/"
         "{verze}/geojson/{soubor}.geojson")

# Strop na jeden soubor. Největší (obrysy měst) má 28 MB; víc znamená, že
# na druhé straně je něco jiného, než čekáme - a disk se plnit nemá.
STROP_BAJTU = 40 * 1024 * 1024

# (soubor, vrstva, druh) - v tomhle pořadí se i kreslí.
SOUBORY: list[tuple[str, str, str]] = [
    ("ne_10m_land", "pevnina", "plocha"),
    ("ne_10m_lakes", "jezera", "plocha"),
    ("ne_10m_urban_areas", "mesta", "plocha"),
    ("ne_10m_rivers_lake_centerlines", "reky", "cara"),
    ("ne_10m_admin_0_boundary_lines_land", "hranice", "cara"),
    ("ne_10m_admin_1_states_provinces_lines", "kraje", "cara"),
]

# Podrobnost 1 pro přiblížení 8-127× (síť 32 × 32, zjednodušené čáry, bez
# útvarů menších než `min_velikost`), podrobnost 2 od 128× (síť 128 × 128,
# všechno). `mist` = desetinná místa souřadnic: setina jednotky je při 127×
# pár pixelů, při 1024× by čáry poskakovaly, proto tam čtyři.
#
# Naměřeno na v5.1.2: se sítí 16 × 16 a tolerancí 0,02 měla nejhustší
# dlaždice (střední Evropa - hranice krajů a obrysy měst) 514 kB; výřez
# přes Evropu při 8× by stahoval přes dva megabajty.
PODROBNOSTI = {
    1: {"n": 32, "tolerance": 0.04, "mist": 2, "min_velikost": 0.08},
    2: {"n": 128, "tolerance": 0.0, "mist": 4, "min_velikost": 0.0},
}


# Tvar uložených dlaždic: verze formátu a velikost sítě každé podrobnosti.
# Když se v nové verzi aplikace změní PODROBNOSTI, stará data v databázi
# na síť nesedí (dlaždice by se posunuly) - bere se pak, jako by mapa
# stažená nebyla, a karta řekne, ať se stáhne znovu.
FORMAT = "1:" + ":".join(str(PODROBNOSTI[p]["n"]) for p in sorted(PODROBNOSTI))


def _body(souradnice: Any) -> list[tuple[float, float]]:
    """GeoJSON [delka, šířka] -> body Mercatoru. Co není konečné číslo, pryč."""
    body = []
    for bod in souradnice or []:
        try:
            lon, lat = float(bod[0]), float(bod[1])
        except (TypeError, ValueError, IndexError):
            continue
        if math.isfinite(lon) and math.isfinite(lat):
            body.append(na_mapu(lat, lon))
    return body


def _tvary(geojson: Any, druh: str) -> Iterator[list[tuple[float, float]]]:
    """Prstence ploch (i děr - kreslí se `evenodd`), nebo čáry, z GeoJSONu."""
    prvky = geojson.get("features", []) if isinstance(geojson, dict) else []
    for prvek in prvky:
        geometrie = (prvek or {}).get("geometry") or {}
        typ = geometrie.get("type")
        sour = geometrie.get("coordinates") or []
        if druh == "plocha":
            polygony = [sour] if typ == "Polygon" else sour if typ == "MultiPolygon" else []
            for polygon in polygony:
                for prstenec in polygon or []:
                    yield _body(prstenec)
        else:
            cary = [sour] if typ == "LineString" else sour if typ == "MultiLineString" else []
            for cara in cary:
                yield _body(cara)


def _cesta(kus: list[tuple[float, float]], mist: int, uzavrit: bool) -> str:
    """Jeden kus jako příkazy SVG cesty."""
    text = "L".join(f"{x:.{mist}f},{y:.{mist}f}" for x, y in kus)
    return "M" + text + ("Z" if uzavrit else "")


def zpracuj(geojson: Any, vrstva: str, druh: str, dlazdice: dict) -> None:
    """Rozřeže vrstvu do dlaždic obou podrobností.

    Doplňuje `dlazdice[(podrobnost, x, y)][vrstva]` - seznam kusů cest
    SVG; spojí je až uložení.
    """
    plocha = druh == "plocha"
    rozrez = rozrez_plochu if plocha else rozrez_caru
    for tvar in _tvary(geojson, druh):
        if len(tvar) < (3 if plocha else 2):
            continue
        xs = [bod[0] for bod in tvar]
        ys = [bod[1] for bod in tvar]
        velikost = max(max(xs) - min(xs), max(ys) - min(ys))
        for podrobnost, nastaveni in PODROBNOSTI.items():
            # Plocha menší než pár pixelů při tomhle přiblížení jen nafukuje
            # dlaždici - v hrubší podrobnosti se vynechá. Čáry ne: krátký
            # úsek řeky nebo hranice je článek delší čáry a bez něj by v ní
            # zůstala mezera.
            if plocha and velikost < nastaveni["min_velikost"]:
                continue
            body = zjednodus(tvar, nastaveni["tolerance"]) if nastaveni["tolerance"] else tvar
            for (x, y), kusy in rozrez(body, nastaveni["n"]).items():
                cesty = dlazdice.setdefault((podrobnost, x, y), {}).setdefault(vrstva, [])
                cesty.extend(_cesta(kus, nastaveni["mist"], plocha) for kus in kusy)


def _slozka() -> Path:
    """Kam se dočasně stahuje - vedle databáze aplikace (data/), jako GeoLite2."""
    return config.load_config().database_path.parent


async def _stahni(klient: Any, adresa: str, cil: Path) -> None:
    """Stáhne soubor po kusech; nad STROP_BAJTU skončí chybou."""
    stazeno = 0
    async with klient.stream("GET", adresa) as odpoved:
        odpoved.raise_for_status()
        with cil.open("wb") as soubor:
            async for kus in odpoved.aiter_bytes(1 << 20):
                stazeno += len(kus)
                if stazeno > STROP_BAJTU:
                    raise ValueError(_t("{soubor} je větší než {mb} MB").format(
                        soubor=adresa.rsplit("/", 1)[-1], mb=STROP_BAJTU // (1024 * 1024)))
                soubor.write(kus)


def _nacti(cesta: Path) -> Any:
    with cesta.open("r", encoding="utf-8") as soubor:
        return json.load(soubor)


def _uloz(dlazdice: dict) -> tuple[int, int]:
    """Nahradí uložené dlaždice novými - v jedné transakci.

    Stará mapa platí, dokud není nová zapsaná celá: když zápis spadne,
    transakce se vrátí a zůstane ta předchozí.
    """
    radky = []
    bajtu = 0
    for (podrobnost, x, y), vrstvy in sorted(dlazdice.items()):
        obsah = json.dumps({vrstva: "".join(cesty) for vrstva, cesty in vrstvy.items()},
                           separators=(",", ":"))
        bajtu += len(obsah)
        radky.append((podrobnost, x, y, obsah))
    with db.connect() as conn:
        conn.execute("DELETE FROM mapa_dlazdice")
        for od in range(0, len(radky), 500):
            conn.executemany(
                "INSERT INTO mapa_dlazdice (podrobnost, x, y, obsah) VALUES (?, ?, ?, ?)",
                radky[od:od + 500])
        conn.commit()
    db.set_setting("mapa_podrobna_verze", VERZE)
    db.set_setting("mapa_podrobna_format", FORMAT)
    db.set_setting("mapa_podrobna_kdy", db.utcnow())
    db.set_setting("mapa_podrobna_dlazdic", str(len(radky)))
    db.set_setting("mapa_podrobna_bajtu", str(bajtu))
    return len(radky), bajtu


async def stahni_a_zpracuj(klient: Any = None, zastavit: Any = None) -> dict[str, Any]:
    """Stáhne všechny vrstvy, rozřeže je a uloží. Spouští ji správce.

    Soubor po souboru - do paměti se vejde jen jeden najednou. Když cokoliv
    selže (síť, strop, rozbitý JSON), nic se nezapíše a platí stará mapa.
    Dočasné soubory se smažou vždycky.

    `zastavit` je funkce, která mezi soubory řekne, jestli správce úlohu
    zastavil (Nastavení → Úlohy) - pak se skončí a nic se nezapíše.
    """
    import httpx

    vlastni = klient is None
    if vlastni:
        klient = httpx.AsyncClient(timeout=120, follow_redirects=True)
    dlazdice: dict = {}
    slozka = _slozka()
    try:
        slozka.mkdir(parents=True, exist_ok=True)
        for soubor, vrstva, druh in SOUBORY:
            if zastavit is not None and zastavit():
                log.info("stahovani podrobne mapy zastaveno")
                return {"status": "stopped"}
            docasny = slozka / f"mapa-{soubor}.stahuje"
            try:
                await _stahni(klient, ZDROJ.format(verze=VERZE, soubor=soubor), docasny)
                data = await asyncio.to_thread(_nacti, docasny)
                await asyncio.to_thread(zpracuj, data, vrstva, druh, dlazdice)
                del data
            finally:
                docasny.unlink(missing_ok=True)
        pocet, bajtu = await asyncio.to_thread(_uloz, dlazdice)
    except Exception as chyba:  # noqa: BLE001 - síť, strop, JSON: vždy jen hláška
        log.warning("podrobnou mapu se nepodarilo stahnout: %s", chyba)
        return {"status": "error", "message": str(chyba)}
    finally:
        if vlastni:
            await klient.aclose()
    log.info("podrobna mapa stazena: %s dlazdic, %s MB", pocet, round(bajtu / 1e6, 1))
    return {"status": "ok", "dlazdic": pocet, "bajtu": bajtu}


async def spust_ulohu() -> None:
    """Stažení jako úloha na pozadí - pod zámkem úloh a se záznamem v logu úloh.

    Spouští ji tlačítko na stránce Síť (web.py); stránka si pak počká
    (`?wait=task`) a po dokončení se obnoví.
    """
    from . import scanner

    async with scanner.zamek_uloh():
        # Pokyn k zastavení z předchozí úlohy sem nepatří - jako u ostatních.
        scanner._clear_stop()
        scan_id = scanner.start_task_log("mapa")
        vysledek = await stahni_a_zpracuj(zastavit=scanner.stop_requested)
        if vysledek.get("status") == "stopped":
            scanner.finish_task_log(scan_id, "stopped",
                                    message=_t("Zastaveno, podrobná mapa se nezměnila."))
        elif vysledek.get("status") == "ok":
            pocet = int(vysledek.get("dlazdic") or 0)
            scanner.finish_task_log(
                scan_id, "done", total=pocet, ok=pocet,
                message=_t("{n} dlaždic, {mb} MB").format(
                    n=pocet, mb=round(int(vysledek.get("bajtu") or 0) / 1e6, 1)))
        else:
            scanner.finish_task_log(scan_id, "error", message=str(vysledek.get("message", "")))


def dlazdice(podrobnost: int, x: int, y: int) -> str | None:
    """Obsah jedné dlaždice (JSON s cestami po vrstvách), nebo None."""
    return db.query_value(
        "SELECT obsah FROM mapa_dlazdice WHERE podrobnost = ? AND x = ? AND y = ?",
        (int(podrobnost), int(x), int(y)), default=None)


def smaz() -> None:
    """Smaže podrobnou mapu. Místo v souboru SQLite se vrátí při údržbě (VACUUM)."""
    with db.connect() as conn:
        conn.execute("DELETE FROM mapa_dlazdice")
        conn.commit()
    for klic in ("mapa_podrobna_verze", "mapa_podrobna_format", "mapa_podrobna_kdy",
                 "mapa_podrobna_dlazdic", "mapa_podrobna_bajtu"):
        db.set_setting(klic, "")
    log.info("podrobna mapa smazana")


def posledni_chyba() -> dict[str, Any] | None:
    """Poslední stažení, které se nepovedlo - když je novější než stažená mapa.

    Úloha běží na pozadí a stránka se po ní jen obnoví; bez téhle zprávy
    by správce po neúspěchu viděl zase tlačítko „Stáhnout" a nevěděl proč.
    """
    from . import scanner

    posledni = scanner.last_scan("mapa")
    if not posledni or posledni["status"] != "error":
        return None
    if str(posledni["finished_at"] or "") <= str(db.get_setting("mapa_podrobna_kdy", "") or ""):
        return None
    return {"kdy": posledni["finished_at"], "duvod": posledni["message"] or ""}


def stav() -> dict[str, Any]:
    """Co je stažené - z nastavení, ne dotazem přes celou tabulku."""
    def cislo(klic: str) -> int:
        try:
            return int(db.get_setting(klic, "0") or 0)
        except ValueError:
            return 0

    kdy = db.get_setting("mapa_podrobna_kdy", "") or ""
    spravny_tvar = (db.get_setting("mapa_podrobna_format", "") or "") == FORMAT
    return {
        "verze": db.get_setting("mapa_podrobna_verze", "") or "",
        "kdy": kdy,
        "dlazdic": cislo("mapa_podrobna_dlazdic"),
        "bajtu": cislo("mapa_podrobna_bajtu"),
        # Stažená a v tvaru, který tahle verze aplikace umí kreslit.
        "platna": bool(kdy) and spravny_tvar,
        # Stažená, ale ve starším tvaru - je potřeba stáhnout znovu.
        "zastarala": bool(kdy) and not spravny_tvar,
        # Do adresy dlaždic: po novém stažení prohlížeč nevezme staré
        # dlaždice z mezipaměti (drží je týden).
        "otisk": hashlib.sha256(f"{kdy}|{FORMAT}".encode()).hexdigest()[:12] if kdy else "",
    }
