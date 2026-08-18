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
import math
from typing import Any, Sequence

from .i18n import translate as _t

# Slot barev pro rozliseni serii. Poradi je zamerne - je vybrane tak, aby
# sousedni dvojice rozeznal i clovek s poruchou barvocitu. Nikdy ho nemen
# a nikdy nepridavej devatou barvu; radeji zbytek sluc do "Ostatni".
SERIES_SLOTS = 8


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
    label_width: int | None = None,   # ponechano kvuli starsim volanim
    limit: int = 12,
    link_prefix: str | None = None,
    link_key: str = "id",
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
        slot = _slot_of(row, index) if row.get("slot") else 1
        title = f"{label}: {_fmt(value)} {unit}".strip()

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

        parts.append(
            f'<div class="hbar-row" data-tip="{_e(title)}">'
            f'<div class="hbar-label">{popisek}</div>'
            f'<div class="hbar-track">'
            f'<div class="hbar-fill" style="width: {percent:.2f}%; '
            f'background: var(--series-{slot})"></div>'
            f"</div>"
            f'<div class="hbar-value">{_fmt(value)}{(" " + _e(unit)) if unit else ""}</div>'
            f"</div>"
        )
    parts.append("</div>")
    return "".join(parts)


# ---------------------------------------------------------------------------
# Deleny pruh - podil na celku
# ---------------------------------------------------------------------------

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
        slot = _slot_of(segment, index)
        label = segment.get("label", "")
        title = f"{label}: {_fmt(value)} {unit} ({share:.0f} %)".replace("  ", " ")

        # Popisek dovnitr jen tehdy, kdyz je cast dost siroka. Orezany text
        # uvnitr pruhu je horsi nez zadny - legenda ho stejne nese.
        inner = f"{share:.0f} %" if share >= 9 else ""
        parts.append(
            f'<div class="stack-seg" style="flex-grow: {share:.4f}; '
            f'background: var(--series-{slot})" data-tip="{_e(title)}">'
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
        parts.append(
            f'<circle class="donut-seg" cx="{stred}" cy="{stred}" r="{polomer:.2f}" '
            f'stroke="var(--series-{_slot_of(row, index)})" '
            f'stroke-dasharray="{dil * obvod:.3f} {obvod:.3f}" '
            f'stroke-dashoffset="{-posun:.3f}" '
            f'data-tip="{_e(popisek)}: {_fmt(hodnota)} {_e(unit)} '
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
        slot = _slot_of(item, index)
        tip = str(item.get("tip") or "").strip()
        atribut = f' data-tip="{_e(tip)}"' if tip else ""
        parts.append(
            f'<li{atribut}><span class="legend-swatch" '
            f'style="background: var(--series-{slot})">'
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
            title = f"{_t(DAY_NAMES[day_index])} {hour}:00 - {_fmt(value)} {unit}"
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


def area_chart_multi(
    points: Sequence[dict[str, Any]],
    x_key: str,
    series: Sequence[dict[str, Any]],
    unit: str = "h",
    height: int = 240,
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

    parts: list[str] = [
        f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="Vyvoj v case, {len(series)} serii">'
    ]

    for tick in ticks:
        y = py(tick)
        parts.append(
            f'<line x1="{margin_left}" y1="{y:.1f}" x2="{margin_left + plot_width}" '
            f'y2="{y:.1f}" stroke="var(--grid)" stroke-width="1" />'
        )
        parts.append(
            f'<text x="{margin_left - 8}" y="{y + 4:.1f}" text-anchor="end" '
            f'class="axis-label">{_fmt(tick)}</text>'
        )

    baseline = margin_top + plot_height
    for entry in series:
        values = [float(point.get(entry["key"]) or 0) for point in points]
        coordinates = [(px(i), py(v)) for i, v in enumerate(values)]
        line = " ".join(f"{x:.1f},{y:.1f}" for x, y in coordinates)
        area = (
            f"M{coordinates[0][0]:.1f},{baseline:.1f} "
            + " ".join(f"L{x:.1f},{y:.1f}" for x, y in coordinates)
            + f" L{coordinates[-1][0]:.1f},{baseline:.1f} Z"
        )
        slot = entry.get("slot", 1)
        parts.append(
            f'<path d="{area}" fill="var(--series-{slot})" fill-opacity="0.22" />'
        )
        parts.append(
            f'<polyline points="{line}" fill="none" stroke="var(--series-{slot})" '
            f'stroke-width="2" stroke-linejoin="round" stroke-linecap="round" />'
        )

    step = max(1, len(points) // 6)
    for index in range(0, len(points), step):
        label = str(points[index].get(x_key, ""))[5:]
        parts.append(
            f'<text x="{px(index):.1f}" y="{height - 10}" text-anchor="middle" '
            f'class="axis-label">{_e(label)}</text>'
        )

    # Jedna ploska na den nese hodnoty vsech serii najednou - clovek se
    # pta "co bylo tenhle den", ne "co byly tenhle den filmy".
    hit_width = plot_width / max(1, len(points) - 1) if len(points) > 1 else plot_width
    for index, point in enumerate(points):
        x = px(index)
        radky = [str(point.get(x_key, ""))]
        for entry in series:
            hodnota = float(point.get(entry["key"]) or 0)
            radky.append(f'{entry["label"]}: {_fmt(hodnota)} {unit}'.strip())
        tip = " · ".join(radky)

        body = "".join(
            f'<circle cx="{x:.1f}" '
            f'cy="{py(float(point.get(entry["key"]) or 0)):.1f}" r="4" '
            f'fill="var(--series-{entry.get("slot", 1)})" '
            f'stroke="var(--surface-1)" stroke-width="2" />'
            for entry in series
        )
        parts.append(
            f'<g class="chart-hit" data-tip="{_e(tip)}">'
            f'<rect x="{max(0.0, x - hit_width / 2):.1f}" y="{margin_top}" '
            f'width="{hit_width:.1f}" height="{plot_height}" fill="transparent" />'
            f'<line x1="{x:.1f}" y1="{margin_top}" x2="{x:.1f}" '
            f'y2="{baseline:.1f}" stroke="var(--axis)" stroke-width="1" />'
            f"{body}</g>"
        )

    parts.append("</svg>")
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
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in coordinates)
    area = (
        f"M{coordinates[0][0]:.1f},{height} "
        + " ".join(f"L{x:.1f},{y:.1f}" for x, y in coordinates)
        + f" L{coordinates[-1][0]:.1f},{height} Z"
    )
    # Plosky pro najeti mysi se orezavaji na sirku obrazku - krajni by jinak
    # trcely o pul dilku ven a chytaly mys mimo graf.
    def _hit(x: float, half: float) -> tuple[float, float]:
        left = max(0.0, x - half)
        return left, min(float(width), x + half) - left

    parts = [
        f'<svg class="sparkline" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="Vyvoj v case, {len(points)} dnu">',
        f'<path d="{area}" fill="var(--series-1)" fill-opacity="0.10" />',
        f'<polyline points="{line}" fill="none" stroke="var(--series-1)" '
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
        tip = f'{points[index].get(x_key, "")}: {_fmt(values[index])} {unit}'.strip()
        hit_x, hit_w = _hit(x, hit_width / 2)
        parts.append(
            f'<g class="chart-hit" data-tip="{_e(tip)}">'
            f'<rect x="{hit_x:.1f}" y="0" '
            f'width="{hit_w:.1f}" height="{height}" fill="transparent" />'
            f'<line x1="{x:.1f}" y1="0" x2="{x:.1f}" y2="{height}" '
            f'stroke="var(--axis)" stroke-width="1" />'
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.5" fill="var(--series-1)" '
            f'stroke="var(--surface-1)" stroke-width="2" />'
            f"</g>"
        )

    parts.append("</svg>")
    return "".join(parts)
