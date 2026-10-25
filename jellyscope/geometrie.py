# -*- coding: utf-8 -*-
"""Geometrie pro podrobnou mapu: zjednodušení čar a řezání do dlaždic.

Podrobná mapa (Natural Earth 1:10M, viz mapa_podrobna.py) je pro celou
stránku moc velká. Při stažení se proto rozřeže na dlaždice - síť n × n
přes svět 360 × 360 jednotek Web Mercatoru - a prohlížeč si bere jen
dlaždice výřezu. Všechno čistým Pythonem, bez knihovny.

Body jsou dvojice (x, y). Plocha je uzavřený prstenec (poslední bod se
s prvním spojí sám, opakovat ho není potřeba), čára je lomená čára.
"""
from __future__ import annotations

from typing import Callable

Bod = tuple[float, float]
Kus = list[Bod]

# Svět ve Web Mercatoru (worldmap.SIRKA, VYSKA).
SVET = 360.0


def zjednodus(body: list[Bod], tolerance: float) -> list[Bod]:
    """Douglas-Peucker: vynechá body, které od čáry odbočují méně než tolerance.

    Bez rekurze - obrys pobřeží má i statisíce bodů a rekurze by narazila
    na limit hloubky. Krajní body zůstávají vždycky.
    """
    if len(body) < 3:
        return list(body)
    nechat = [False] * len(body)
    nechat[0] = nechat[-1] = True
    tolerance2 = tolerance * tolerance
    zasobnik = [(0, len(body) - 1)]
    while zasobnik:
        od, do = zasobnik.pop()
        ax, ay = body[od]
        bx, by = body[do]
        dx, dy = bx - ax, by - ay
        delka2 = dx * dx + dy * dy
        nejdal, kde = -1.0, -1
        for i in range(od + 1, do):
            px, py = body[i]
            if delka2 == 0:
                vzdalenost = (px - ax) ** 2 + (py - ay) ** 2
            else:
                t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / delka2))
                vzdalenost = (px - ax - t * dx) ** 2 + (py - ay - t * dy) ** 2
            if vzdalenost > nejdal:
                nejdal, kde = vzdalenost, i
        if nejdal > tolerance2:
            nechat[kde] = True
            zasobnik.append((od, kde))
            zasobnik.append((kde, do))
    return [bod for bod, ano in zip(body, nechat) if ano]


def _prusecik(a: Bod, b: Bod, osa: int, hodnota: float) -> Bod:
    """Kde úsečka a-b protne přímku osa = hodnota. Na ose přesně hodnota -
    jinak by kus kvůli zaokrouhlení vylezl z dlaždice o setinu chlupu."""
    t = (hodnota - a[osa]) / (b[osa] - a[osa])
    druha = 1 - osa
    jinde = a[druha] + t * (b[druha] - a[druha])
    return (hodnota, jinde) if osa == 0 else (jinde, hodnota)


def _orez_plochu(prstenec: Kus, osa: int, hodnota: float, mensi: bool) -> list[Kus]:
    """Sutherland-Hodgman pro jednu hranu: část prstence na jedné straně."""
    def uvnitr(bod: Bod) -> bool:
        return bod[osa] <= hodnota if mensi else bod[osa] >= hodnota

    vysledek: Kus = []
    predchozi = prstenec[-1]
    predchozi_uvnitr = uvnitr(predchozi)
    for bod in prstenec:
        bod_uvnitr = uvnitr(bod)
        if bod_uvnitr:
            if not predchozi_uvnitr:
                vysledek.append(_prusecik(predchozi, bod, osa, hodnota))
            vysledek.append(bod)
        elif predchozi_uvnitr:
            vysledek.append(_prusecik(predchozi, bod, osa, hodnota))
        predchozi, predchozi_uvnitr = bod, bod_uvnitr
    return [vysledek] if len(vysledek) >= 3 else []


def _orez_caru(cara: Kus, osa: int, hodnota: float, mensi: bool) -> list[Kus]:
    """Části čáry na jedné straně - z jedné čáry jich může být víc."""
    def uvnitr(bod: Bod) -> bool:
        return bod[osa] <= hodnota if mensi else bod[osa] >= hodnota

    kusy: list[Kus] = []
    aktualni: Kus = [cara[0]] if uvnitr(cara[0]) else []
    for predchozi, bod in zip(cara, cara[1:]):
        a, b = uvnitr(predchozi), uvnitr(bod)
        if a and b:
            aktualni.append(bod)
        elif a:
            aktualni.append(_prusecik(predchozi, bod, osa, hodnota))
            kusy.append(aktualni)
            aktualni = []
        elif b:
            aktualni = [_prusecik(predchozi, bod, osa, hodnota), bod]
    if aktualni:
        kusy.append(aktualni)
    return [kus for kus in kusy if len(kus) >= 2]


Orez = Callable[[Kus, int, float, bool], list[Kus]]


def _rozdel(kusy: list[Kus], orez: Orez, hodnota: float, osa: int) -> tuple[list[Kus], list[Kus]]:
    """Kusy na dvě poloviny podle přímky osa = hodnota.

    Kus celý na jedné straně jde dál bez ořezu - velký obrys (Eurasie)
    se tak řeže jen tam, kde přes hranici opravdu vede.
    """
    mensi: list[Kus] = []
    vetsi: list[Kus] = []
    for kus in kusy:
        souradnice = [bod[osa] for bod in kus]
        if max(souradnice) <= hodnota:
            mensi.append(kus)
        elif min(souradnice) >= hodnota:
            vetsi.append(kus)
        else:
            mensi.extend(orez(kus, osa, hodnota, True))
            vetsi.extend(orez(kus, osa, hodnota, False))
    return mensi, vetsi


def _rozrez(kusy: list[Kus], n: int, orez: Orez) -> dict[tuple[int, int], list[Kus]]:
    """Kusy do sítě n × n: okno se střídavě půlí svisle a vodorovně.

    Každý kus se tak projde jen tolikrát, kolik je úrovní půlení (u sítě
    128 × 128 čtrnáctkrát), ne jednou za každou dlaždici.
    """
    velikost = SVET / n
    # Nejdřív ořez na svět - co leží mimo (třeba kvůli zaokrouhlení), do
    # žádné dlaždice nepatří.
    for osa in (0, 1):
        kusy = _rozdel(kusy, orez, 0.0, osa)[1]
        kusy = _rozdel(kusy, orez, SVET, osa)[0]

    vysledek: dict[tuple[int, int], list[Kus]] = {}
    zasobnik = [(kusy, 0, 0, n, n)]
    while zasobnik:
        cast, x, y, sirka, vyska = zasobnik.pop()
        if not cast:
            continue
        if sirka == 1 and vyska == 1:
            vysledek.setdefault((x, y), []).extend(cast)
            continue
        if sirka >= vyska:
            pul = sirka // 2
            leva, prava = _rozdel(cast, orez, (x + pul) * velikost, 0)
            zasobnik.append((leva, x, y, pul, vyska))
            zasobnik.append((prava, x + pul, y, sirka - pul, vyska))
        else:
            pul = vyska // 2
            horni, dolni = _rozdel(cast, orez, (y + pul) * velikost, 1)
            zasobnik.append((horni, x, y, sirka, pul))
            zasobnik.append((dolni, x, y + pul, sirka, vyska - pul))
    return vysledek


def rozrez_plochu(body: list[Bod], n: int) -> dict[tuple[int, int], list[Kus]]:
    """Uzavřený prstenec rozřezaný do sítě n × n: {(x, y): [kusy]}.

    Kusy jsou zase uzavřené prstence. Plocha, která pokrývá celou
    dlaždici, dá v ní její celý čtverec.
    """
    prstenec = list(body)
    if len(prstenec) > 1 and prstenec[0] == prstenec[-1]:
        prstenec.pop()
    if len(prstenec) < 3:
        return {}
    return _rozrez([prstenec], n, _orez_plochu)


def rozrez_caru(body: list[Bod], n: int) -> dict[tuple[int, int], list[Kus]]:
    """Lomená čára rozřezaná do sítě n × n: {(x, y): [kusy]}."""
    if len(body) < 2:
        return {}
    return _rozrez([list(body)], n, _orez_caru)
