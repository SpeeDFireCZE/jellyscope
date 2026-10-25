# -*- coding: utf-8 -*-
"""Shluky teček na mapě - pro každou úroveň přiblížení zvlášť.

Z dálky má deset míst v jednom městě (a sousední města) splynout do
jedné větší tečky, při přibližování se mají rozpadat, až je na konci
každé místo z GeoIP samostatně. Shluky se počítají tady, v Pythonu,
pro pevné úrovně 1×, 2×, 4× … 1024× - mapa v prohlížeči pak jen
přepíná vrstvy. Popisky, tvary čísel a počty lidí se tak dají otestovat
jako všechno ostatní a skutečná id diváků zůstanou na serveru.

Souřadnice jsou ve Web Mercatoru (worldmap.na_mapu), takže vzdálenost
odpovídá tomu, co člověk vidí na obrazovce.
"""
from __future__ import annotations

from typing import Any

from . import formatting
from .worldmap import na_mapu

# Jak blízko u sebe smí být místa, aby ještě splynula - v jednotkách
# Mercatoru při pohledu na celý svět (360 jednotek na šířku karty).
# S každou úrovní přiblížení se práh půlí: zhruba „tečky by se na
# obrazovce překrývaly".
PRAH_SHLUKU = 8.0

# Nejvíc 1024× - dál už GeoIP stejně přesnější není.
MAX_UROVEN = 1024


def _shlukuj(body: list[dict[str, Any]], prah: float) -> list[list[dict[str, Any]]]:
    """Rozdělí body (seřazené od nejsledovanějšího) do skupin.

    Každý bod se přidá k první skupině, jejíž **zakladatel** (první,
    nejsledovanější bod) je blíž než práh, jinak založí novou. Porovnává
    se se zakladatelem, ne s těžištěm: skupina se tak nemůže „plazit"
    a pořadí výsledku je stálé.
    """
    skupiny: list[list[dict[str, Any]]] = []
    prah2 = prah * prah
    for bod in body:
        for skupina in skupiny:
            zakladatel = skupina[0]
            dx = bod["_x"] - zakladatel["_x"]
            dy = bod["_y"] - zakladatel["_y"]
            if dx * dx + dy * dy < prah2:
                skupina.append(bod)
                break
        else:
            skupiny.append([bod])
    return skupiny


def _shluk(skupina: list[dict[str, Any]]) -> dict[str, Any]:
    """Jeden shluk z jeho míst - součty, těžiště a popisek."""
    sekund = sum(int(b.get("sekund") or 0) for b in skupina)
    # Těžiště vážené odsledovaným časem: tečka leží tam, kde se dívalo
    # nejvíc. Bez času (samé nuly) obyčejný průměr.
    vahy = [int(b.get("sekund") or 0) for b in skupina] if sekund else [1] * len(skupina)
    celkem = sum(vahy)
    divaci: set[str] = set()
    for bod in skupina:
        divaci |= set(bod.get("divaci") or ())

    popis = skupina[0].get("popis") or "?"
    dalsich = len({b.get("popis") or "?" for b in skupina}) - 1
    if dalsich:
        popis = formatting.tvar(dalsich, "{misto} a {n} další místo",
                                "{misto} a {n} další místa",
                                "{misto} a {n} dalších míst", misto=popis)
    return {
        "x": sum(b["_x"] * v for b, v in zip(skupina, vahy)) / celkem,
        "y": sum(b["_y"] * v for b, v in zip(skupina, vahy)) / celkem,
        "plays": sum(int(b.get("plays") or 0) for b in skupina),
        "sekund": sekund,
        "bajtu": sum(int(b.get("bajtu") or 0) for b in skupina),
        "lidi": len(divaci),
        "mist": len(skupina),
        "popis": popis,
        # Ze kterých míst je (pořadí v seznamu míst) - podle toho kreslení
        # pozná, že je to tentýž shluk jako o úroveň níž.
        "clenove": tuple(sorted(b["_poradi"] for b in skupina)),
    }


def shluky(mista: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Shluky pro úrovně přiblížení 1×, 2×, 4× … nejvýš MAX_UROVEN.

    Vrací `[{"uroven": z, "shluky": [...]}]` vzestupně. Úroveň, která
    má stejné shluky jako předchozí, se vynechá (mapa vezme nejbližší
    nižší). Končí se úrovní, na které je každé místo samostatně - nebo
    MAX_UROVEN, když jsou některá místa tak blízko, že nesplynou nikdy.

    Místa jsou slovníky ze stats.hotspoty (lat, lon, sekund, plays,
    bajtu, divaci, popis).
    """
    if not mista:
        return []
    body = []
    for misto in mista:
        x, y = na_mapu(misto["lat"], misto["lon"])
        body.append({**misto, "_x": x, "_y": y})
    # Od nejsledovanějšího; při shodě rozhodují souřadnice, ať je pořadí
    # pokaždé stejné.
    body.sort(key=lambda b: (-int(b.get("sekund") or 0), b["_x"], b["_y"]))
    for poradi, bod in enumerate(body):
        bod["_poradi"] = poradi

    urovne: list[dict[str, Any]] = []
    predchozi: list[tuple[int, ...]] | None = None
    uroven = 1
    while uroven <= MAX_UROVEN:
        skupiny = _shlukuj(body, PRAH_SHLUKU / uroven)
        # Otisk úrovně = kdo je s kým. Stejný jako minule = nic nového.
        otisk = [tuple(id(b) for b in skupina) for skupina in skupiny]
        if otisk != predchozi:
            urovne.append({"uroven": uroven, "shluky": [_shluk(s) for s in skupiny]})
            predchozi = otisk
        if len(skupiny) == len(body):
            break
        uroven *= 2
    return urovne


# Tak daleko jde přiblížit vždycky - i mapa s jedním místem (dřív pevná mez).
MIN_PRIBLIZENI = 20


def max_priblizeni(urovne: list[dict[str, Any]], pocet_mist: int) -> int:
    """Jak daleko má jít mapu přiblížit.

    Do poslední úrovně, na které je každé místo samostatně. Když se
    některá místa nerozdělí ani na MAX_UROVEN (jsou skoro na sobě),
    shluky ji nemají jako zvláštní úroveň - je stejná jako předchozí -,
    ale přiblížit se tam jít má: podklad pod tečkou je pořád k vidění.
    """
    if not urovne:
        return MIN_PRIBLIZENI
    posledni = urovne[-1]
    if len(posledni["shluky"]) < pocet_mist:
        return MAX_UROVEN
    return max(MIN_PRIBLIZENI, posledni["uroven"])
