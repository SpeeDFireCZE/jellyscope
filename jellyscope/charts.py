"""Kresleni grafu.

Grafy se tu kresli dvema zpusoby a vyber mezi nimi neni nahodny:

* **HTML + CSS** - vodorovne sloupce, deleny pruh, teplotni mapa.
  Vsechno, co je v podstate obdelnik s popiskem. Prohlizec to sam
  prizpusobi sirce okna, text zustane citelny a nic se nemusi posouvat.

* **SVG** - carove a plosne grafy, minigrafy.
  Tam, kde je potreba skutecna krivka, kterou z obdelniku neposkladas.

Puvodne bylo v SVG uplne vsechno a melo to jednu nepekou vlastnost: obrazek
mel pevnou sirku, takze se na uzsim okne musel posouvat do strany. To je
u statistiky, kterou chces prelet ocima, spatne. HTML tuhle potiz nema -
proto se vetsina grafu presunula tam.

Barvy se nikde nepisou primo. Kreslime pomoci CSS promennych
(var(--series-1)), ktere jsou ve style.css - diky tomu maji grafy spravne
barvy ve svetlem i tmavem rezimu, aniz by o tom tenhle soubor musel vedet.
"""

from __future__ import annotations

import html
import itertools
import json
import re
import math
from typing import Any, Sequence

from .i18n import translate as _t

# Slot barev pro rozliseni serii. Poradi je zamerne - je vybrane tak, aby
# sousedni dvojice rozeznal i clovek s poruchou barvocitu. Nikdy ho nemen
# a nikdy nepridavej devatou barvu; radeji zbytek sluc do "Ostatni".
SERIES_SLOTS = 8


# Pocitadlo pro jmena prechodu (gradientu). Dva grafy na jedne strance
# nesmi sahat po stejnem "id": prohlizec by druhemu podstrcil vypln toho
# prvniho. Staci rostouci cislo - v ramci jedne stranky je jedinecne.
_PORADI_PRECHODU = itertools.count(1)


def _prechod(slot: int, jmeno: str, sila: float = 0.34,
             barva: str | None = None) -> str:
    """Svisly prechod z barvy serie do prazdna.

    Plna poloprusvitna vypln ma dve vady. U prekryvu dvou serii vznikne
    treti, kalna barva, ve ktere se obe ztrati. A u zeme je nejvic
    inkoustu prave tam, kde zadna informace neni - dulezity je horni
    okraj plochy, tedy sama cara.

    Prechod obojiho zbavi: nahore drzi barvu serie, dole mizi.
    """
    barva = barva or f"var(--series-{slot})"
    return (
        f'<linearGradient id="{jmeno}" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0%" stop-color="{barva}" '
        f'stop-opacity="{sila:.2f}" />'
        f'<stop offset="100%" stop-color="{barva}" '
        f'stop-opacity="0.015" />'
        f"</linearGradient>"
    )


def _cesta_schody(body: Sequence[tuple[float, float]]) -> str:
    """Schodovita cara - drzi hodnotu, pak skoci na dalsi.

    Pro veliciny, ktere se **nemeni plynule**. Soubezny tok ze serveru je
    presne takovy: dokud nikdo nezacne ani neskonci, je porad stejny, a v
    okamziku, kdy stream nabehne, skoci. Hlazena krivka by mezi dvema
    body dokreslila naberh, ktery nikdy nenastal - a hlavne by z desiti
    minut prehravani udelala mekky kopec pres pul hodiny.

    Rozdil proti _cesta(): tam je krivka tvar (kolik hodin se kdy divalo),
    tady je to zaznam stavu.
    """
    if not body:
        return ""
    kusy = [f"M{body[0][0]:.1f},{body[0][1]:.1f}"]
    for (x1, y1), (x2, y2) in zip(body, body[1:]):
        kusy.append(f"H{x2:.1f}")
        if abs(y2 - y1) > 0.01:
            kusy.append(f"V{y2:.1f}")
    return " ".join(kusy)


def _cesta(body: Sequence[tuple[float, float]]) -> str:
    """Plynula cara pres zadane body - jako `d` pro <path>.

    Prokladame **monotonni kubikou** (Fritsch-Carlson), ne volnym
    splinem. Rozdil je podstatny a je to duvod, proc tu ta matematika je:

    * Volny spline si mezi body "rozmachne" - vyleti nad nejvyssi
      namerenou hodnotu a pod nejnizsi. Graf pak ukazuje spicku, ktera
      nikdy nenastala, a nula uprostred tydne vypada jako zaporne cislo.
    * Monotonni kubika ma zaruceno, ze mezi dvema body zustane mezi
      jejich hodnotami. Kdyz jde rada nahoru, krivka jde nahoru; kdyz je
      bod maximum, krivka nad nej nevystoupa.

    Zbyva jedna vyhrada, kterou zadna interpolace neodstrani: mezi
    ctvrtkem a patkem zadne meziden neexistuje, takze oblouk mezi nimi
    je dokresleny. Presna cisla proto vzdycky nese bublina a tabulka
    pod grafem - krivka je tvar, ne zdroj hodnot.
    """
    n = len(body)
    if n == 0:
        return ""
    if n == 1:
        return f"M{body[0][0]:.1f},{body[0][1]:.1f}"

    dx = [body[i + 1][0] - body[i][0] for i in range(n - 1)]
    dy = [body[i + 1][1] - body[i][1] for i in range(n - 1)]
    # Smernice mezi sousedy. Nulova vzdalenost by delila nulou - takovy
    # bod jen preskocime (dva body na stejnem x nemaji smysl).
    smernice = [(dy[i] / dx[i]) if dx[i] else 0.0 for i in range(n - 1)]

    tecny = [smernice[0]]
    for i in range(1, n - 1):
        if smernice[i - 1] * smernice[i] <= 0:
            # Zmena smeru = vrchol nebo dul. Vodorovna tecna je presne to,
            # co drzi krivku pod (resp. nad) namerenym bodem.
            tecny.append(0.0)
        else:
            w1 = 2 * dx[i] + dx[i - 1]
            w2 = dx[i] + 2 * dx[i - 1]
            tecny.append((w1 + w2) / (w1 / smernice[i - 1] + w2 / smernice[i]))
    tecny.append(smernice[-1])

    casti = [f"M{body[0][0]:.1f},{body[0][1]:.1f}"]
    for i in range(n - 1):
        h = dx[i] / 3
        casti.append(
            f"C{body[i][0] + h:.1f},{body[i][1] + tecny[i] * h:.1f} "
            f"{body[i + 1][0] - h:.1f},{body[i + 1][1] - tecny[i + 1] * h:.1f} "
            f"{body[i + 1][0]:.1f},{body[i + 1][1]:.1f}"
        )
    return " ".join(casti)


def _e(value: Any) -> str:
    """Escapovani textu do HTML.

    Kdyz se do stranky dostane nazev filmu obsahujici "<", rozbije to
    dokument - a v horsim pripade umozni vlozit cizi znacku. Proto kazdy
    text, ktery nepochazi od nas, projde tudy.
    """
    return html.escape(str(value), quote=True)


def _slot_of(segment: dict[str, Any], index: int) -> int:
    """Ktery barevny slot serii pouzit.

    Kdyz segment nese vlastni klic "slot", ridime se jim. To je dulezitejsi,
    nez se zda: barva musi patrit **veci**, ne jejimu poradi. Kdyby se
    barvilo podle poradi, mela by cestina u jednoho uzivatele modrou
    a u druheho oranzovou - a graf by lhal.
    """
    slot = segment.get("slot")
    if isinstance(slot, int) and 1 <= slot <= SERIES_SLOTS:
        return slot
    return (index % SERIES_SLOTS) + 1


def _fmt(value: float) -> str:
    """Cislo pro popisek - bez zbytecnych desetinnych mist."""
    if value >= 100:
        return f"{value:,.0f}".replace(",", " ")
    if value >= 10:
        return f"{value:.0f}"
    if value >= 1:
        return f"{value:.1f}".rstrip("0").rstrip(".").replace(".", ",")
    return f"{value:.2f}".rstrip("0").rstrip(".").replace(".", ",")


def _udaj(value: float, unit: str = "") -> str:
    """Hodnota i s jednotkou tak, jak ji clovek cte.

    Jedno misto pro vsechny bubliny a cisla u pruhu - popisky os sem
    nepatri. Na ose je "40" spravne (je to znacka meritka), kdezto
    v bubline je to konkretni udaj, a ten muze byt na minuty presny.
    Viz formatting.presny_cas().
    """
    from . import formatting             # az tady, at nevznikne kruh

    if unit == "h" and formatting.presny_cas():
        return formatting.hodiny_hhmm(value)
    return f"{_fmt(value)} {unit}".strip()


def _bublina(nadpis: str, radky: Sequence[dict[str, Any]]) -> str:
    """Atributy bubliny: nadpis a radky, kazdy ve sve barve.

    Posila se jako JSON, ne jako hotove HTML: bublinu sklada prohlizec
    pres textContent (viz base.html), takze se do stranky nemuze dostat
    znacka z nazvu filmu. `data-tip` zustava jako prosty text - kdyby
    JSON z jakehokoli duvodu nedosel, bublina porad neco ukaze.
    """
    # V prostem textu je i nadpis - je to zaloha pro pripad, ze by JSON
    # nedosel, a bez data by "2 h" nikomu nic nereklo.
    text = " · ".join([nadpis] + [str(radek["text"]) for radek in radky]).strip(" ·")
    data = json.dumps({"nadpis": nadpis, "radky": list(radky)},
                      ensure_ascii=False, separators=(",", ":"))
    return f'data-tip="{_e(text)}" data-tip-json="{_e(data)}"'


def _empty(message: str) -> str:
    return f'<p class="chart-empty">{_e(message)}</p>'


# ---------------------------------------------------------------------------
# Vodorovne sloupce - porovnani velikosti
# ---------------------------------------------------------------------------

def hbar_chart(
    rows: Sequence[dict[str, Any]],
    label_key: str,
    value_key: str,
    unit: str = "h",
    limit: int = 12,
    link_prefix: str | None = None,
    link_key: str = "id",
    poradi: bool = False,
) -> str:
    """Porovnani hodnot mezi kategoriemi.

    Vsechny sloupce maji **stejnou barvu**, pokud radek nerekne jinak
    (klicem "slot"). Odstinovat je podle velikosti je castá chyba: delka
    sloupce velikost uz rika, a barva by tim prisla o svuj vlastni vyznam.

    Cely graf je mrizka o trech sloupcich - popisek, drazka, hodnota.
    Sirku si rozdeli prohlizec sam, takze se nic nikdy neposouva do strany.

    `link_prefix` udela z popisku odkaz: k predpone se pripoji hodnota
    z `link_key` daneho radku (napr. "/users/" + user_id). Radky bez te
    hodnoty zustanou obycejnym textem - odkaz, ktery nikam nevede, je
    horsi nez zadny.

    `poradi=True` prida pred popisek cislo radku. Zapina se jen tam, kde
    je graf opravdu **zebricek** - u nejsledovanejsich titulu nebo
    nejaktivnejsich uzivatelu. Kde se jen porovnavaji kategorie mezi
    sebou (kodeky, jazyky), by cislo lhalo o poradi, ktere zadne neni.
    """
    rows = [row for row in rows if row is not None][:limit]
    if not rows:
        return _empty(_t("Zatím žádná data"))

    maximum = max(float(row.get(value_key) or 0) for row in rows) or 1.0

    parts = ['<div class="hbars">']
    for index, row in enumerate(rows):
        value = float(row.get(value_key) or 0)
        label = str(row.get(label_key, ""))
        percent = value / maximum * 100
        # Kdyz radek nerekne "slot", neni to serie - je to jedna velicina
        # merena u ruznych kategorii (hodiny u uzivatelu, u prehravacu).
        # Takovy graf dostane barvu aplikace; paleta serii je na to, kdyz
        # se ma poznat, ktera cara je ktera.
        vlastni_slot = bool(row.get("slot"))
        slot = _slot_of(row, index) if vlastni_slot else 1
        title = f"{label}: {_udaj(value, unit)}"

        # Popisek je odkaz jen tehdy, kdyz opravdu vede na existujici
        # zaznam. Jinak by uzivatel klikal a nic by se nedelo.
        #
        # `is not None` schvalne, ne prosta pravdivost: prazdna predpona je
        # platna volba pro radky, ktere si nesou celou adresu samy. To je
        # pripad nejsledovanejsich titulu, kde serial vede na /series/
        # a film na /item/ - jedna spolecna predpona by tam nestacila.
        cil = row.get(link_key) if link_prefix is not None else None
        popisek = _e(label)
        if cil:
            popisek = (f'<a class="hbar-link" href="{_e(link_prefix)}{_e(cil)}">'
                       f'{popisek}</a>')

        # Prechod po delce sloupce: u zakladny je barva o neco tlumenejsi,
        # na konci plna. Cislo stoji prave tam, takze oko jde po sloupci
        # k nemu. Delku to nemeni - hodnotu porad nese jen ona.
        # Prechod po delce sloupce. U serie jde od tlumene k plne barve
        # (jina barva by pletla identitu); u jednobarevneho grafu jde
        # rovnou fialova -> modra, tedy prechod znacky.
        if vlastni_slot:
            barva = f"var(--series-{slot})"
            vypln = (f"linear-gradient(90deg, "
                     f"color-mix(in oklab, {barva} 62%, var(--surface-1)), "
                     f"{barva})")
        else:
            vypln = "linear-gradient(90deg, var(--accent-2), var(--accent))"
        cislo = (f'<div class="hbar-rank">{index + 1}</div>') if poradi else ""
        parts.append(
            f'<div class="hbar-row{" is-ranked" if poradi else ""}" data-tip="{_e(title)}">'
            f'{cislo}'
            f'<div class="hbar-label">{popisek}</div>'
            f'<div class="hbar-track">'
            f'<div class="hbar-fill" style="width: {percent:.2f}%; '
            f'background: {vypln}"></div>'
            f"</div>"
            f'<div class="hbar-value">{_e(_udaj(value, unit))}</div>'
            f"</div>"
        )
    parts.append("</div>")
    return "".join(parts)


# ---------------------------------------------------------------------------
# Deleny pruh - podil na celku
# ---------------------------------------------------------------------------

# Kdyz casti pruhu nejsou libovolne kategorie, ale STAVY s poradim,
# nesou klic "role" a barvu si vezmou podle vyznamu. Priklad je "jak
# server obsah dorucuje": prime prehravani server nestoji nic (zelena),
# prebaleni neco malo (barva aplikace), prepocet boli (jantarova).
#
# Barva pak rika totez co odznak u prehravani nad tim - a jantarovy
# prouzek na konci je varovani, ne treti barva v poradi.
ROLE_BARVY = {
    "good": "var(--good)",
    "info": "var(--accent)",
    "warning": "var(--warning)",
    "serious": "var(--serious)",
    "critical": "var(--critical)",
    "muted": "var(--text-muted)",
}


def _barva_segmentu(segment: dict[str, Any], index: int) -> str:
    # Vlastni barva ma prednost pred rolí. Pouziva to rozpad prepoctu:
    # prvni varianta si nechava barvu role (aby bylo poznat, ze jde
    # o prepocet), dalsi dostavaji vlastni odstiny z palety serii.
    #
    # Drive se druha a dalsi varianta ztmavovala michanim s podkladem.
    # Vypadalo to spravne v tabulce barev, ale v grafu ne: casti byvaji
    # uzke par pixelu a tri odstiny tehoz hneda od sebe na takove plose
    # nikdo nerozezna.
    vlastni = str(segment.get("barva") or "").strip()
    if vlastni:
        return vlastni

    role = str(segment.get("role") or "").strip()
    if role not in ROLE_BARVY:
        return f"var(--series-{_slot_of(segment, index)})"
    return ROLE_BARVY[role]


def stacked_bar(segments: Sequence[dict[str, Any]], unit: str = "h") -> str:
    """Jeden pruh rozdeleny na casti - "z ceho se sklada celek".

    Mezi castmi je 2px mezera v barve podkladu. Delame to mezerou, ne
    obrysem: obrys prida inkoust, ktery neni data, a graf zhoustne.
    """
    segments = [s for s in segments if float(s.get("value") or 0) > 0][:SERIES_SLOTS]
    total = sum(float(s.get("value") or 0) for s in segments)
    if total <= 0:
        return _empty(_t("Zatím žádná data"))

    parts = ['<div class="stack">']
    for index, segment in enumerate(segments):
        value = float(segment.get("value") or 0)
        share = value / total * 100
        label = segment.get("label", "")
        title = f"{label}: {_udaj(value, unit)} ({share:.0f} %)"

        # Popisek dovnitr jen tehdy, kdyz je cast dost siroka. Orezany text
        # uvnitr pruhu je horsi nez zadny - legenda ho stejne nese.
        inner = f"{share:.0f} %" if share >= 9 else ""
        parts.append(
            f'<div class="stack-seg" style="flex-grow: {share:.4f}; '
            f'background: {_barva_segmentu(segment, index)}" data-tip="{_e(title)}">'
            f'<span>{inner}</span></div>'
        )
    parts.append("</div>")
    return "".join(parts)


def donut_chart(
    rows: Sequence[dict[str, Any]],
    label_key: str = "label",
    value_key: str = "value",
    unit: str = "h",
    size: int = 220,
) -> str:
    """Koláčový graf s dírou uprostřed.

    Kruhový výseč je tvar, který se čte hůř než sloupce - plochy se
    porovnávají špatně. Vyplatí se jen tam, kde je otázka "jaký díl
    z celku", a dílů je málo. Přesně to je otázka "co ten člověk sleduje".

    Díra uprostřed není ozdoba: prstenec se čte podle **délky oblouku**,
    a to je pro oko snazší úloha než odhadovat plochu klínu. Navíc se do
    ní vejde celkové číslo, které jinak nemá kam.

    Kreslí se jedním kruhem a `stroke-dasharray`: každý díl je kus obvodu.
    Bez jediné cesty s ručně počítanými souřadnicemi - a bez chyby, která
    se do nich vloudí u dílu většího než půlkruh.
    """
    rows = [row for row in rows if float(row.get(value_key) or 0) > 0]
    if not rows:
        return _empty(_t("Zatím žádná data"))

    celkem = sum(float(row.get(value_key) or 0) for row in rows)
    if celkem <= 0:
        return _empty(_t("Zatím žádná data"))

    polomer = size / 2 - 18
    obvod = 2 * math.pi * polomer
    stred = size / 2

    parts = [
        f'<div class="donut-wrap">',
        f'<svg class="donut" viewBox="0 0 {size} {size}" width="{size}" height="{size}" '
        f'role="img" aria-label="{_e(_t("Podíl žánrů"))}">',
    ]

    posun = 0.0
    for index, row in enumerate(rows[:SERIES_SLOTS + 1]):
        hodnota = float(row.get(value_key) or 0)
        dil = hodnota / celkem
        popisek = str(row.get(label_key, ""))
        procenta = dil * 100
        # Mezi dily necháváme 2px mezeru v barvě podkladu - stejně jako
        # u děleného pruhu. U jednoho jediného dílu se vynechá: kroužek
        # by měl zbytečnou dírku.
        mezera = 2.0 if len(rows) > 1 else 0.0
        delka = max(1.0, dil * obvod - mezera)
        parts.append(
            f'<circle class="donut-seg" cx="{stred}" cy="{stred}" r="{polomer:.2f}" '
            f'stroke="var(--series-{_slot_of(row, index)})" '
            f'stroke-dasharray="{delka:.3f} {obvod:.3f}" '
            f'stroke-dashoffset="{-posun:.3f}" '
            f'data-tip="{_e(popisek)}: {_e(_udaj(hodnota, unit))} '
            f'({procenta:.0f} %)"></circle>'
        )
        posun += dil * obvod

    parts.append(
        f'<text class="donut-total" x="{stred}" y="{stred - 4}" '
        f'text-anchor="middle">{_fmt(celkem)}</text>'
        f'<text class="donut-unit" x="{stred}" y="{stred + 14}" '
        f'text-anchor="middle">{_e(unit)}</text>'
    )
    parts.append("</svg>")

    # Legenda s čísly. U koláče je povinná dvakrát: barva sama identitu
    # nenese a z výseče se přesná hodnota vyčíst nedá.
    parts.append('<ul class="donut-legend">')
    for index, row in enumerate(rows[:SERIES_SLOTS + 1]):
        hodnota = float(row.get(value_key) or 0)
        parts.append(
            f'<li><span class="legend-swatch" '
            f'style="background: var(--series-{_slot_of(row, index)})"></span>'
            f'<span class="donut-name">{_e(row.get(label_key, ""))}</span>'
            f'<span class="donut-value">{hodnota / celkem * 100:.0f} %</span></li>'
        )
    parts.append("</ul></div>")
    return "".join(parts)


def legend(items: Sequence[dict[str, Any]]) -> str:
    """Legenda. U dvou a vice serii je povinna.

    Text legendy je v bezne barve pisma, identitu nese barevny ctverecek
    vedle nej. Obarvovat samotny text je spatne - svetle odstiny (zluta,
    tyrkysova) jsou jako pismo na svetlem podkladu necitelne.

    Kdyz serie nese klic "tip", polozka legendy dostane bublinu. Je to
    pro pripady, kdy nazev sam o sobe nestaci - treba "Ostatní", pod
    kterym se skryva vic druhu zaznamu najednou.
    """
    items = [item for item in items if item is not None][:SERIES_SLOTS]
    if not items:
        return ""

    parts = ['<ul class="legend">']
    for index, item in enumerate(items):
        tip = str(item.get("tip") or "").strip()
        atribut = f' data-tip="{_e(tip)}"' if tip else ""
        # Stejna barva jako v grafu, vcetne barvy podle vyznamu - legenda,
        # ktera ma jiny odstin nez pruh, je horsi nez zadna.
        barva = item.get("barva") or _barva_segmentu(item, index)
        parts.append(
            f'<li{atribut}><span class="legend-swatch" '
            f'style="background: {barva}">'
            f'</span>{_e(item.get("label", ""))}</li>'
        )
    parts.append("</ul>")
    return "".join(parts)


# ---------------------------------------------------------------------------
# Teplotni mapa - kdy se sleduje
# ---------------------------------------------------------------------------

DAY_NAMES = ["Po", "Út", "St", "Čt", "Pá", "So", "Ne"]

# Odstupnovana jedna barva, od svetle po tmavou. Jedna barva, ne duha:
# duhova skala nema poradi, ktere by clovek dokazal precist.
HEAT_STEPS = ["var(--seq-250)", "var(--seq-350)", "var(--seq-450)",
              "var(--seq-550)", "var(--seq-650)"]


def heatmap(grid: Sequence[Sequence[float]], unit: str = "h") -> str:
    """Mrizka den x hodina - kdy se na serveru nejvic sleduje.

    Postavena na CSS gridu s 24 stejne sirokymi sloupci. Bunky se tim
    prizpusobi sirce okna samy a mrizka se nikdy nemusi posouvat.
    """
    maximum = max((max(row) for row in grid if row), default=0.0)
    if maximum <= 0:
        return _empty(_t("Zatím žádná data"))

    parts = ['<div class="heatmap">']

    # Hlavicka s hodinami - popisek jen kazde tri hodiny, jinak by se slily.
    parts.append('<div class="heatmap-corner"></div><div class="heatmap-hours">')
    for hour in range(24):
        text = str(hour) if hour % 3 == 0 else ""
        parts.append(f'<span>{text}</span>')
    parts.append("</div>")

    for day_index, row in enumerate(grid):
        parts.append(f'<div class="heatmap-day">{_t(DAY_NAMES[day_index])}</div>')
        parts.append('<div class="heatmap-cells">')
        for hour, value in enumerate(row):
            if value <= 0:
                colour = "var(--grid)"
            else:
                # Do ktereho z peti stupnu hodnota patri.
                bucket = min(len(HEAT_STEPS) - 1, int(value / maximum * len(HEAT_STEPS)))
                colour = HEAT_STEPS[bucket]
            title = f"{_t(DAY_NAMES[day_index])} {hour}:00 - {_udaj(value, unit)}"
            parts.append(
                f'<i style="background: {colour}" data-tip="{_e(title)}"></i>'
            )
        parts.append("</div>")

    parts.append("</div>")
    return "".join(parts)


# ---------------------------------------------------------------------------
# Plosny graf - vyvoj v case (jedine, co zustava v SVG)
# ---------------------------------------------------------------------------

def _nice_ticks(maximum: float, count: int = 4) -> list[float]:
    """Kulate hodnoty pro osu Y.

    Osa s popisky 0 / 2,5 / 5 / 7,5 se cte lip nez 0 / 2,37 / 4,74 / 7,11.
    Postup: spocitej "syrovy" krok (maximum deleno poctem dilku), zaokrouhli
    ho nahoru na nejblizsi hezky nasobek mocniny desiti (1, 2, 2,5 nebo 5)
    a od nuly po nem kracej az za maximum.
    """
    if maximum <= 0:
        return [0.0, 1.0]

    raw_step = maximum / max(1, count)
    magnitude = 10 ** math.floor(math.log10(raw_step))

    step = magnitude * 10  # zaloha, kdyby zadny nasobek nevyhovel
    for multiplier in (1, 2, 2.5, 5, 10):
        if magnitude * multiplier >= raw_step:
            step = magnitude * multiplier
            break

    # Osa musi maximum vzdycky **prekrocit**, ne se u nej zastavit. Puvodni
    # podminka `while value <= maximum` koncila u posledniho dilku, ktery se
    # jeste vesel pod maximum: pri maximu 3,4 vysla osa 0-3 a krivka pak
    # vylezla nad graf do zahlavi karty. Proto se dilek pripoji vzdy jako
    # prvni a teprve pak se testuje, jestli uz jsme za maximem.
    ticks: list[float] = []
    value = 0.0
    while len(ticks) < 12:
        ticks.append(round(value, 4))
        if value >= maximum - step * 1e-9:
            break
        value += step
    return ticks or [0.0, 1.0]


# Kolik nejvys plosek pro najeti mysi jeden graf dostane. Nad tim uz je
# kazda uzsi nez prst a HTML roste o stovky kilobajtu - viz komentar
# u jejich generovani.
NEJVIC_PLOSEK = 200


def area_chart_multi(
    points: Sequence[dict[str, Any]],
    x_key: str,
    series: Sequence[dict[str, Any]],
    unit: str = "h",
    height: int = 240,
    schody: bool = False,
    vyber: bool = False,
) -> str:
    """Vic serii v jednom case, prekryte pres sebe.

    `series` je seznam slovniku {"key": "movie_hours", "label": "Filmy",
    "slot": 1}. Kazda serie ma vlastni barevny slot a ten se **nemeni**,
    kdyz nektera serie zmizi - barva patri veci, ne poradi. Kdyby se
    barvilo podle indexu, prepnuti filtru by prebarvilo to, co zbylo,
    a graf by lhal.

    Proc prekryt a ne naskladat na sebe: prekryv umozni porovnat filmy
    a serialy navzajem ("cteme cistou vysku obou car"). Skladany graf
    umi jen soucet - horni serie se cte spatne, protoze jeji zaklad
    skace podle spodni.

    Vypln je proto poloprusvitna: tam, kde se serie prekryvaji, je videt
    obe. Cara zustava plna, aby se dala sledovat i v prekryvu.
    """
    if not points or not series:
        return _empty(_t("Zatím žádná data"))

    width = 760
    margin_left, margin_right = 46, 22
    margin_top, margin_bottom = 16, 30
    plot_width = width - margin_left - margin_right
    plot_height = height - margin_top - margin_bottom

    all_values = [
        float(point.get(entry["key"]) or 0)
        for entry in series for point in points
    ]
    maximum = max(all_values) if all_values else 0.0
    ticks = _nice_ticks(maximum or 1.0)
    scale_max = max(ticks[-1], maximum) or 1.0

    def px(index: int) -> float:
        if len(points) == 1:
            return margin_left + plot_width / 2
        return margin_left + plot_width * index / (len(points) - 1)

    def py(value: float) -> float:
        return margin_top + plot_height * (1 - value / scale_max)

    # Kazda serie ma vlastni prechod a jeho jmeno musi byt na strance
    # jedinecne - viz _prechod().
    cislo = next(_PORADI_PRECHODU)
    prechody = {
        entry.get("slot", 1): f"prechod-{cislo}-{index}"
        for index, entry in enumerate(series)
    }
    # Serie muze nest vlastni barvu misto cisla slotu. Pouziva to graf
    # na Prehledu, kde jsou dve az tri cary a maji drzet barvy aplikace;
    # tam, kde je kategorii vic (jazyky), zustava paleta serii, protoze
    # ta je stavena tak, aby se navzajem nepletly.
    barvy = {entry.get("slot", 1): entry.get("barva")
             for entry in series if entry.get("barva")}

    def barva_serie(slot: int) -> str:
        return barvy.get(slot) or f"var(--series-{slot})"

    # Vyber tazenim: graf rekne, kde na ose lezi jaky okamzik, a zbytek
    # uz zvladne prohlizec. Souradnice jsou ve viewBoxu, ne v pixelech -
    # SVG se skaluje podle sirky stranky, takze prepocet dela JS.
    #
    # Dva rezimy podle toho, co je na ose:
    #   "cas" - bod je okamzik (ziva krivka). Vybrat jde libovolny usek.
    #   "dny" - bod je cely den. Vyber se **zaokrouhli na dny**, protoze
    #           jemneji graf stejne nic nevi; kdyby posilal cas, tvaril by
    #           se presneji, nez ve skutecnosti je.
    lze_vybirat = vyber and len(points) > 1
    dny = [str(b.get("den") or b.get("day") or "") for b in points]
    ma_cas = lze_vybirat and all(b.get("cas") for b in points)
    ma_dny = lze_vybirat and all(re.match(r"^\d{4}-\d{2}-\d{2}$", d) for d in dny)
    lze_vybirat = ma_cas or ma_dny

    vyber_atributy = ""
    if lze_vybirat:
        osa = (f' data-x-od="{margin_left}" data-x-do="{margin_left + plot_width}"'
               f' data-y-od="{margin_top}" data-y-do="{margin_top + plot_height}"')
        if ma_cas:
            vyber_atributy = (f' data-vyber="cas"{osa}'
                              f' data-cas-od="{int(points[0]["cas"])}"'
                              f' data-cas-do="{int(points[-1]["cas"])}"')
        else:
            vyber_atributy = f' data-vyber="dny"{osa} data-dny="{",".join(dny)}"' 

    parts: list[str] = [
        f'<svg class="chart{" chart-lze-vybirat" if lze_vybirat else ""}" '
        f'viewBox="0 0 {width} {height}" role="img"{vyber_atributy} '
        f'aria-label="Vyvoj v case, {len(series)} serii">',
        "<defs>",
        # Cim vic serii, tim slabsi vypln. U jedne krivky plocha pomaha -
        # ukazuje objem. U dvou se plochy prekryvaji a vznika treti, kalna
        # barva, ve ktere se obe ztrati; tam uz nese informaci hlavne
        # sama cara. Proto se u dvou a vic serii vypln stahuje.
        *(_prechod(slot, jmeno, sila=0.34 if len(series) == 1 else 0.16,
                   barva=barvy.get(slot))
          for slot, jmeno in prechody.items()),
        "</defs>",
    ]

    baseline = margin_top + plot_height
    for tick in ticks:
        y = py(tick)
        # Nulova cara je osa, od ktere se vsechno meri - ta zustava plna.
        # Ostatni jsou jen voditka, aby sla vyska odhadnout; tecky staci
        # a neberou grafu pozornost.
        je_zaklad = abs(y - baseline) < 0.5
        parts.append(
            f'<line x1="{margin_left}" y1="{y:.1f}" x2="{margin_left + plot_width}" '
            f'y2="{y:.1f}" stroke="var({"--axis" if je_zaklad else "--grid"})" '
            f'stroke-width="1"'
            + ("" if je_zaklad else ' stroke-dasharray="1 5" stroke-linecap="round"')
            + " />"
        )
        parts.append(
            f'<text x="{margin_left - 8}" y="{y + 4:.1f}" text-anchor="end" '
            f'class="axis-label">{_fmt(tick)}</text>'
        )

    for entry in series:
        values = [float(point.get(entry["key"]) or 0) for point in points]
        coordinates = [(px(i), py(v)) for i, v in enumerate(values)]
        # Jeden bod nema delku, takze by z nej byla cara o nulove sirce -
        # graf vypadal prazdny, i kdyz se ten den koukalo. Stava se to
        # u obdobi kratsiho nez dva dny (treba useku vybraneho tazenim).
        # Rozpustime ho na sirku grafu: cte se to jako "tuhle celou dobu
        # to bylo takhle", coz je presne, co jeden bod znamena.
        if len(coordinates) == 1:
            vyska = coordinates[0][1]
            coordinates = [(margin_left, vyska), (margin_left + plot_width, vyska)]
        # Cara i plocha jdou po tomtez tvaru - jinak by vypln vykukovala
        # zpod cary. Viz _cesta() a _cesta_schody().
        line = _cesta_schody(coordinates) if schody else _cesta(coordinates)
        area = (
            f"M{coordinates[0][0]:.1f},{baseline:.1f} L"
            + line[1:]
            + f" L{coordinates[-1][0]:.1f},{baseline:.1f} Z"
        )
        slot = entry.get("slot", 1)
        parts.append(f'<path d="{area}" fill="url(#{prechody[slot]})" />')
        parts.append(
            f'<path d="{line}" fill="none" stroke="{barva_serie(slot)}" '
            f'stroke-width="2" stroke-linejoin="round" stroke-linecap="round" />'
        )

    step = max(1, len(points) // 6)
    for index in range(0, len(points), step):
        # Datum z databaze zacina rokem ("2026-08-14"). Rok na ose nikoho
        # nezajima a bere misto, tak ho ukrojime - ale jen kdyz tam opravdu
        # je. Drive se rezalo vzdycky, takze popisek "02.08. 22:11" se
        # zmenil na ". 22:11".
        surovy = str(points[index].get(x_key, ""))
        label = surovy[5:] if re.match(r"^\d{4}-", surovy) else surovy
        parts.append(
            f'<text x="{px(index):.1f}" y="{height - 10}" text-anchor="middle" '
            f'class="axis-label">{_e(label)}</text>'
        )

    # Jedna ploska nese hodnoty vsech serii najednou - clovek se pta
    # "co bylo tenhle den", ne "co byly tenhle den filmy".
    #
    # Plosek je nejvys NEJVIC_PLOSEK, i kdyz bodu je tisic. Kazda z nich
    # je vlastni <g> s bublinou v atributu, takze u jemne krivky (zivy tok
    # ma bod na pet minut) by z toho bylo skoro megabajt HTML - a mys
    # stejne neni schopna trefit ploshu uzsi nez par pixelu.
    krok_plosek = max(1, len(points) // NEJVIC_PLOSEK + (1 if len(points) % NEJVIC_PLOSEK else 0))
    hit_width = (plot_width / max(1, len(points) - 1) * krok_plosek
                 if len(points) > 1 else plot_width)
    for index in range(0, len(points), krok_plosek):
        # Ploska muze zastupovat vic bodu - a pak nesmi ukazovat hodnotu
        # toho prvniho, ale **nejvyssi v tom pasmu**. Spicka byva jeden
        # bod siroka; kdyby bublina hlasila jejiho souseda, byl by graf
        # k necemu jen na pohled a presne cislo by se z nej vycist nedalo.
        pasmo = points[index:index + krok_plosek]
        vrcholy = {}
        for entry in series:
            klic = entry["key"]
            nejvyssi = max(pasmo, key=lambda b: float(b.get(klic) or 0))
            vrcholy[klic] = (float(nejvyssi.get(klic) or 0), nejvyssi)

        # Cas bereme od te serie, ktera v pasmu vrcholi nejvys - to je ten
        # okamzik, na ktery se clovek pta.
        hlavni = max(vrcholy.values(), key=lambda dvojice: dvojice[0])[1]
        x = px(index)

        # Nadpis je cas, pod nim kazda serie na svem radku a ve sve barve -
        # v prekryvu dvou car je to jediny zpusob, jak poznat, ktere cislo
        # patri ktere.
        radky_bubliny = [
            {"text": f'{entry["label"]}: {_udaj(vrcholy[entry["key"]][0], unit)}',
             "barva": barva_serie(entry.get("slot", 1))}
            for entry in series
        ]
        if hlavni.get("streamu"):
            radky_bubliny.append(
                {"text": _t("souběžných streamů: {n}").format(n=hlavni["streamu"])})
        tip = _bublina(str(hlavni.get(x_key, "")), radky_bubliny)

        body = "".join(
            f'<circle cx="{x:.1f}" '
            f'cy="{py(vrcholy[entry["key"]][0]):.1f}" r="4" '
            f'fill="{barva_serie(entry.get("slot", 1))}" '
            f'stroke="var(--surface-1)" stroke-width="2" />'
            for entry in series
        )
        parts.append(
            f'<g class="chart-hit" {tip}>'
            f'<rect x="{max(0.0, x - hit_width / 2):.1f}" y="{margin_top}" '
            f'width="{hit_width:.1f}" height="{plot_height}" fill="transparent" />'
            f'<line x1="{x:.1f}" y1="{margin_top}" x2="{x:.1f}" '
            f'y2="{baseline:.1f}" stroke="var(--axis)" stroke-width="1" />'
            f"{body}</g>"
        )

    if lze_vybirat:
        # Obdelnik vyberu kresli JS, ale patri do dokumentu uz ted - takhle
        # ho styluje CSS jako vsechno ostatni a JS jen meni souradnice.
        # `pointer-events: none`, aby nesebral bubliny pod sebou.
        parts.append(
            f'<rect class="chart-vyber" x="0" y="{margin_top}" width="0" '
            f'height="{plot_height}" hidden />'
        )

    parts.append("</svg>")
    if lze_vybirat:
        # Funkce, o ktere se neni jak dozvedet, neexistuje. Kurzor sice
        # naznaci, ze se s grafem da neco delat, ale co, uz ne - stejne
        # jako u mapy pod tim je proto pod grafem jedna radka.
        parts.append(
            f'<p class="card-note chart-napoveda">'
            f'{_e(_t("Tažením v grafu vybereš rozmezí."))}</p>'
        )
    return "".join(parts)


def sparkline(
    points: Sequence[dict[str, Any]],
    x_key: str = "day",
    y_key: str = "hours",
    unit: str = "h",
    width: int = 760,
    height: int = 64,
) -> str:
    """Minigraf pod hlavnim cislem.

    Bere stejna data jako velky graf nize a chova se stejne: po najeti mysi
    ukaze, kolik bylo ktery den. Drive to byl jen ozdobny tvar bez hodnot -
    jenze co vypada jako graf, to clovek zkusi pouzit jako graf. Bud tedy
    interaktivni byt ma, nebo tam nema byt vubec.

    Osy a popisky tu nejsou zamerne: presna cisla nese graf pod tim a jeho
    tabulka. Tohle je rychly tvar, ne nahrada.
    """
    values = [float(p.get(y_key) or 0) for p in points]
    if not values or max(values) <= 0:
        return ""
    # Jeden bod neni vyvoj. Stava se to u vlastniho obdobi kratsiho nez
    # dva dny (treba useku vybraneho tazenim v grafu) - a kreslila se pak
    # prazdna plocha pod hlavnim cislem, ktera vypadala jako chybejici graf.
    if len(values) < 2:
        return ""

    maximum = max(values)
    # Odsazeni musi pokryt polomer bodu i s prstencem (3,5 + 2). Bez nej
    # by se bod na kraji nebo na vrcholu kreslil pulkou ven z obrazku -
    # SVG tu neni orezane, takze by prelezl pres okraj karty.
    #
    # Plati to i po tom, co koncovy bod zmizel: body se sice ukazuji az
    # po najeti mysi, ale kresli se na tomtez miste.
    padding = 5
    pad_x = 6
    step = (width - pad_x * 2) / max(1, len(values) - 1)

    coordinates = [
        (pad_x + index * step,
         height - padding - (value / maximum) * (height - padding * 2))
        for index, value in enumerate(values)
    ]
    line = _cesta(coordinates)
    area = (
        f"M{coordinates[0][0]:.1f},{height} L"
        + line[1:]
        + f" L{coordinates[-1][0]:.1f},{height} Z"
    )
    # Plosky pro najeti mysi se orezavaji na sirku obrazku - krajni by jinak
    # trcely o pul dilku ven a chytaly mys mimo graf.
    def _hit(x: float, half: float) -> tuple[float, float]:
        left = max(0.0, x - half)
        return left, min(float(width), x + half) - left

    jmeno = f"prechod-mini-{next(_PORADI_PRECHODU)}"
    parts = [
        f'<svg class="sparkline" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="Vyvoj v case, {len(points)} dnu">',
        f"<defs>{_prechod(1, jmeno, sila=0.28, barva='var(--accent)')}</defs>",
        f'<path d="{area}" fill="url(#{jmeno})" />',
        f'<path d="{line}" fill="none" stroke="var(--accent)" '
        f'stroke-width="2" stroke-linejoin="round" stroke-linecap="round" />',
    ]

    # Zadny trvaly bod na konci.
    #
    # Drive tu jeden byl - jako zvyrazneni posledniho dne. Jenze vsechny
    # ostatni body se objevuji az po najeti mysi, takze ten koncovy vypadal
    # jako bod, ktery se "zasekl". Velky graf pod tim (area_chart_multi)
    # ho taky nema; dva tvary tehoz grafu se maji chovat stejne.

    # Neviditelne plosky pro najeti mysi - jedna na kazdy den. Musi byt
    # dost siroke, aby se do nich dalo trefit; samotny bod je maly cil.
    hit_width = step if len(coordinates) > 1 else width
    for index, (x, y) in enumerate(coordinates):
        tip = _bublina(
            str(points[index].get(x_key, "")),
            [{"text": _udaj(values[index], unit),
              "barva": "var(--accent)"}])
        hit_x, hit_w = _hit(x, hit_width / 2)
        parts.append(
            f'<g class="chart-hit" {tip}>'
            f'<rect x="{hit_x:.1f}" y="0" '
            f'width="{hit_w:.1f}" height="{height}" fill="transparent" />'
            f'<line x1="{x:.1f}" y1="0" x2="{x:.1f}" y2="{height}" '
            f'stroke="var(--axis)" stroke-width="1" />'
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.5" fill="var(--accent)" '
            f'stroke="var(--surface-1)" stroke-width="2" />'
            f"</g>"
        )

    parts.append("</svg>")
    return "".join(parts)

# ---------------------------------------------------------------------------
# Mapa sveta - odkud se divaji
# ---------------------------------------------------------------------------

def mapa_sveta(body: Sequence[dict[str, Any]], rezim: str = "click") -> str:
    """Obrys pevnin a na nem tecky podle toho, odkud se prehravalo.

    Kresli se stejnou rukou jako zbytek grafu: jedno SVG, zadna knihovna,
    zadna dlazdicova mapa z internetu. Obrys je v `worldmap.PEVNINY`
    (Natural Earth, public domain) a lezi ve stejne soustave souradnic
    jako tecky, takze staci jedno `viewBox`.

    `rezim` rika, cim se mapa priblizuje - "wheel" koleckem, "click"
    klikanim a tlacitky. Rozhoduje o tom uzivatel v Nastaveni; sem to
    jde jen jako atribut, obsluha je v base.html. Kolecko nad mapou
    totiz zastavi rolovani stranky, coz nekomu vadi a nekomu vyhovuje.

    Velikost tecky roste s **odmocninou** odsledovaneho casu, ne s casem
    samotnym: oko porovnava plochy, takze dvojnasobny cas ma mit
    dvojnasobnou plochu, ne dvojnasobny polomer. Bez odmocniny by jedno
    aktivni misto prekrylo pul kontinentu.
    """
    from .worldmap import HRANICE, PEVNINY, SIRKA, VYSKA, na_mapu

    if not body:
        return _empty(_t("Zatím žádná data"))

    nejvic = max(float(b.get("sekund") or 0) for b in body) or 1.0

    casti = [
        f'<svg class="mapa" viewBox="0 0 {SIRKA} {VYSKA}" '
        f'data-vychozi="0 0 {SIRKA} {VYSKA}" '
        f'data-zoom="{_e(rezim if rezim in ("click", "wheel") else "click")}" '
        f'preserveAspectRatio="xMidYMid meet" role="img" '
        f'aria-label="{_e(_t("Odkud se dívají"))}">',
        # Zare kolem bodu. Jeden pruhledny prechod pro vsechny tecky:
        # gradient se pocita v souradnicich prvku, takze se sam prizpusobi
        # jeho velikosti a nemusi se definovat pro kazdou zvlast.
        # Tecky na mape nejsou serie - je to jedna velicina na ruznych
        # mistech. Barva aplikace, stejna jako u krivky na Prehledu.
        '<defs><radialGradient id="zare-mapa">'
        '<stop offset="0%" stop-color="var(--accent)" stop-opacity="0.95" />'
        '<stop offset="35%" stop-color="var(--accent)" stop-opacity="0.35" />'
        '<stop offset="100%" stop-color="var(--accent)" stop-opacity="0" />'
        '</radialGradient></defs>',
        f'<rect x="0" y="0" width="{SIRKA}" height="{VYSKA}" '
        f'fill="var(--surface-2)" />',
        f'<path d="{PEVNINY}" fill="var(--axis)" fill-opacity="0.35" '
        f'stroke="var(--axis)" stroke-width="0.2" />',
        # Hranice statu se kresli az po priblizeni - pri pohledu na cely
        # svet by z nich byla jen sit car pres kontinenty. Sirka cary se
        # pri priblizeni zmensuje spolu s vyrezem, aby zustala vlasova.
        # Hranice kreslime barvou textu, ne carou grafu: --axis ma skoro
        # tentyz odstin jako vypln pevnin, takze v ni hranice splyvaly.
        f'<path class="mapa-hranice" d="{HRANICE}" fill="none" '
        f'stroke="var(--text-muted)" stroke-width="0.16" '
        f'stroke-opacity="0" stroke-linejoin="round" />',
        # Vsechno, co se posouva a priblizuje, je v jedne skupine - JS pak
        # meni jen viewBox a nemusi sahat na jednotlive prvky.
        '<g class="mapa-body">',
    ]

    for bod in body:
        x, y = na_mapu(float(bod["lat"]), float(bod["lon"]))
        podil = (float(bod.get("sekund") or 0) / nejvic) ** 0.5
        polomer = 1.2 + podil * 4.0
        tip = _t("{misto}: {n}× · {lidi} lidí").format(
            misto=bod.get("popis") or "?", n=bod.get("plays") or 0,
            lidi=bod.get("lidi") or 0)
        # Dve kruznice na bod: mekka zare a v ni ostre jadro. Samotna
        # tecka pusobi jako spinavy bod na skle; zare vypada jako svetlo
        # videne z vesmiru - a hlavne je videt i tam, kde je bodu vic
        # blizko sebe.
        casti.append(
            f'<g class="mapa-bod" data-tip="{_e(tip)}">'
            f'<circle class="zare" cx="{x:.1f}" cy="{y:.1f}" '
            f'r="{polomer * 2.2:.2f}" data-r="{polomer * 2.2:.2f}" '
            f'fill="url(#zare-mapa)" />'
            f'<circle class="jadro" cx="{x:.1f}" cy="{y:.1f}" '
            f'r="{polomer * 0.4:.2f}" data-r="{polomer * 0.4:.2f}" '
            f'fill="#fff" fill-opacity="0.85" />'
            f'</g>'
        )

    casti.append("</g>")
    casti.append("</svg>")
    return "".join(casti)

