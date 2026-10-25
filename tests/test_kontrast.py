# -*- coding: utf-8 -*-
r"""Kontrast barev ve všech čtyřech podobách vzhledu.

Barvy se v aplikaci nepíšou přímo, jsou to proměnné v `style.css`
(--text-muted, --series-1, ...) a každý motiv je přepisuje. Od oka se
kontrast odhaduje špatně: šedý pomocný text měl ve světlém motivu 3,5 : 1
a v tmavém 4,3 : 1, přitom běžný text potřebuje aspoň 4,5 : 1 (WCAG AA).
Bílá procenta na tyrkysovém pruhu měla 1,9 : 1.

Tenhle test počítá kontrast z hodnot v CSS, takže ho úprava palety nemůže
tiše rozbít:

* text (primární, vedlejší, pomocný) na podkladu stránky i obou ploch,
* písmo na každé barvě, která může být podkladem textu (dílky pruhů,
  červené tlačítko) - přes proměnné --ink-*,
* že pruh opravdu bere písmo z --ink-* a tlačítko pro mazání taky,
* písmo menší než 12 px jen v popiscích grafů.

Motivy se skládají tak, jak je skládá prohlížeč: tmavý přepisuje světlý,
klasický vzhled přepisuje nový a klasický tmavý jde poslední.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_kontrast.py
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "kontrast.db")
os.environ["SECRET_KEY"] = "testovaci-klic"

from jellyscope import charts  # noqa: E402

failures = 0


def check(podminka: bool, popis: str, detail: str = "") -> None:
    global failures
    print(f"{'OK    ' if podminka else 'CHYBA '} {popis} {detail}")
    if not podminka:
        failures += 1


CSS = (PROJECT / "jellyscope" / "static" / "style.css").read_text(encoding="utf-8")


def blok(selektor: str, od: int = 0) -> dict[str, str]:
    """Proměnné jednoho bloku `selektor { ... }`."""
    i = CSS.index(selektor + " {", od)
    konec = CSS.index("}", i)
    return dict(re.findall(r"(--[\w-]+):\s*([^;]+);", CSS[i:konec]))


ZAKLAD = blok(":root")
TMAVY = blok(':root[data-theme="dark"]')
KLASICKY = blok(':root[data-skin="klasicky"]')
KLASICKY_TMAVY = blok(':root[data-skin="klasicky"][data-theme="dark"]')
TMAVY_PODLE_SYSTEMU = blok(':root:not([data-theme="light"])')
KLASICKY_PODLE_SYSTEMU = blok(':root[data-skin="klasicky"]:not([data-theme="light"])')

MOTIVY = {
    "nový světlý": [ZAKLAD],
    "nový tmavý": [ZAKLAD, TMAVY],
    "klasický světlý": [ZAKLAD, KLASICKY],
    "klasický tmavý": [ZAKLAD, TMAVY, KLASICKY, KLASICKY_TMAVY],
}


def slozit(bloky: list[dict[str, str]]) -> dict[str, str]:
    vysledek: dict[str, str] = {}
    for b in bloky:
        vysledek.update(b)
    return vysledek


def hodnota(promenne: dict[str, str], jmeno: str) -> str:
    v = promenne[jmeno].strip()
    m = re.match(r"var\((--[\w-]+)\)$", v)
    return hodnota(promenne, m.group(1)) if m else v


def jas(barva: str) -> float:
    h = barva.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    kanaly = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    kanaly = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in kanaly]
    return 0.2126 * kanaly[0] + 0.7152 * kanaly[1] + 0.0722 * kanaly[2]


def kontrast(a: str, b: str) -> float:
    la, lb = jas(a), jas(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


print("--- tmavý motiv podle systému = tmavý motiv přepnutý ručně ---")
# Tmave hodnoty jsou v CSS dvakrat: jednou pro "podle systemu", jednou
# pro rucni prepnuti. Kdyz se opravi jen jedna kopie, polovina lidi
# uvidi starou barvu.
check(TMAVY_PODLE_SYSTEMU == TMAVY, "nový vzhled: obě tmavé kopie sedí",
      str(sorted(set(TMAVY_PODLE_SYSTEMU.items()) ^ set(TMAVY.items())))[:120])
check(KLASICKY_PODLE_SYSTEMU == KLASICKY_TMAVY, "klasický vzhled: obě tmavé kopie sedí",
      str(sorted(set(KLASICKY_PODLE_SYSTEMU.items()) ^ set(KLASICKY_TMAVY.items())))[:120])

print()
print("--- text na podkladu: aspoň 4,5 : 1 ---")
for motiv, bloky in MOTIVY.items():
    p = slozit(bloky)
    for text in ("--text-primary", "--text-secondary", "--text-muted"):
        nejhorsi = min((kontrast(hodnota(p, text), hodnota(p, podklad)), podklad)
                       for podklad in ("--page", "--surface-1", "--surface-2"))
        check(nejhorsi[0] >= 4.5, f"{motiv}: {text}",
              f"{nejhorsi[0]:.2f} : 1 na {nejhorsi[1]}")

print()
print("--- písmo na barevném podkladu: aspoň 4,5 : 1 ---")
# Dilky deleneho pruhu nesou procenta, cervene tlacitko nese "Smazat".
# Kazda barva, ktera muze byt podkladem textu, ma vlastni --ink-*.
# Barva aplikace ma inkoust odjakziva: --accent-ink (tlacitko Ulozit,
# vybrany chip). Druhy token pro totez by se casem rozesel.
PODKLADY = [f"--series-{i}" for i in range(1, 9)] + [f"--linka-{i}" for i in range(1, 4)] + [
    "--good", "--accent", "--warning", "--serious", "--critical", "--text-muted"]


def inkoust_k(podklad: str) -> str:
    return "--accent-ink" if podklad == "--accent" else "--ink-" + podklad[2:]


for motiv, bloky in MOTIVY.items():
    p = slozit(bloky)
    spatne = []
    for podklad in PODKLADY:
        inkoust = inkoust_k(podklad)
        if inkoust not in p:
            spatne.append(f"{inkoust} chybí")
            continue
        pomer = kontrast(hodnota(p, podklad), hodnota(p, inkoust))
        if pomer < 4.5:
            spatne.append(f"{podklad} {pomer:.2f}")
    check(not spatne, f"{motiv}: písmo na všech barvách", str(spatne))

print()
print("--- pruh a tlačítko písmo z --ink-* opravdu berou ---")
pruh = charts.stacked_bar([
    {"label": "a", "value": 5},
    {"label": "b", "value": 4, "role": "good"},
    {"label": "c", "value": 3, "barva": "var(--series-7)"},
    {"label": "d", "value": 2, "role": "info"},
])
dilky = re.findall(r'<div class="stack-seg" style="([^"]*)"', pruh)
check(len(dilky) == 4, f"pruh má čtyři dílky ({len(dilky)})")
for styl in dilky:
    podklad = re.search(r"background: var\((--[\w-]+)\)", styl)
    inkoust = re.search(r"color: var\((--[\w-]+)(?:, #[0-9a-f]+)?\)", styl)
    check(bool(podklad and inkoust and inkoust_k(podklad.group(1)) == inkoust.group(1)),
          "dílek má písmo ke své barvě", styl[:90])
check("color: #fff" not in CSS[CSS.index(".stack-seg span"):CSS.index(".stack-seg span") + 200],
      "procenta v pruhu nemají natvrdo bílou")

tlacitko = CSS[CSS.index(".btn.danger {"):CSS.index("}", CSS.index(".btn.danger {"))]
check("var(--ink-critical)" in tlacitko, "červené tlačítko bere písmo z --ink-critical")

# Natvrdo zadana bila na barve motivu. Vybrany den v kalendari (bila na
# --series-1) mela v tmavem motivu 2,2 : 1 - kontrola tokenu vyse ji
# nevidela, protoze bila neni token.
natvrdo = []
for pravidlo in re.finditer(r"([^{}]+)\{([^{}]*)\}", CSS):
    telo = pravidlo.group(2)
    podklad = re.search(r"background(?:-color)?:\s*var\((--[\w-]+)\)", telo)
    if (podklad and podklad.group(1) in PODKLADY
            and re.search(r"(?<![-\w])color:\s*(#fff\b|#ffffff|white)", telo)):
        natvrdo.append(" ".join(pravidlo.group(1).split()).split("*/")[-1].strip())
check(not natvrdo, f"žádná natvrdo bílá na barvě motivu ({natvrdo})")

print()
print("--- písmo pod 12 px jen v popiscích grafů ---")
# Popisky os, teplotni mapy a kalendare jsou soucast grafu a maji vlastni
# meritko. Jinde (odznaky, popisky dlazdic, plakaty) je 12 px minimum.
GRAFY = {".axis-label", ".heatmap-hours span", ".heatmap-day", ".donut-unit"}
male = []
for pravidlo in re.finditer(r"([^{}]+)\{([^{}]*)\}", CSS):
    selektor = " ".join(pravidlo.group(1).split()).split("*/")[-1].strip()
    velikost = re.search(r"font-size:\s*([\d.]+)px", pravidlo.group(2))
    if velikost and float(velikost.group(1)) < 12 and selektor not in GRAFY:
        male.append(f"{selektor} {velikost.group(1)}px")
check(not male, f"žádné drobné písmo mimo grafy ({len(male)}: {male[:4]})")

print()
print("--- akcenty sezónní výzdoby ---")
# Svatek prebiji --accent, --accent-2 a --accent-ink. Akcent je i barva
# textu (odkazy, aktivni prvky), takze musi byt citelny na podkladu,
# a pismo na tlacitku v akcentu musi byt citelne na nem.
for klic in ("vanoce", "halloween", "silvestr", "velikonoce"):
    soubor = PROJECT / "jellyscope" / "static" / "udalosti" / f"{klic}.css"
    css = soubor.read_text(encoding="utf-8") if soubor.exists() else ""

    def blok_svatku(selektor: str) -> dict[str, str]:
        i = css.find(selektor + " {")
        if i < 0:
            return {}
        return dict(re.findall(r"(--[\w-]+):\s*([^;]+);", css[i:css.index("}", i)]))

    svetly = blok_svatku(f':root[data-udalost="{klic}"]')
    tmavy = blok_svatku(f':root[data-udalost="{klic}"][data-theme="dark"]')
    systemovy = blok_svatku(f':root[data-udalost="{klic}"]:not([data-theme="light"])')
    check(bool(svetly) and tmavy == systemovy and set(tmavy) == set(svetly),
          f"{klic}: světlý blok a dvě shodné tmavé kopie se stejnými proměnnými",
          str(sorted(set(tmavy.items()) ^ set(systemovy.items())))[:100])
    # Svatek smi menit jen barvu aplikace a barvy svych efektu - barvu dat
    # (--barva-dat*: stav "info", jednobarevne grafy, voda na mape) ne.
    cizi = sorted(k for k in set(svetly) | set(tmavy)
                  if k not in ("--accent", "--accent-2", "--accent-ink")
                  and not k.startswith("--udalost-"))
    check(not cizi, f"{klic}: mění jen barvu aplikace a své efekty ({cizi})")
    for motiv, bloky in MOTIVY.items():
        p = slozit(bloky + ([svetly, tmavy] if "tmavý" in motiv else [svetly]))
        try:
            ink = kontrast(hodnota(p, "--accent"), hodnota(p, "--accent-ink"))
            # Tlacitka, cipy a znacka v menu jsou prechod --accent-2 ->
            # --accent s pismem --accent-ink: citelne musi byt na obou koncich.
            ink2 = kontrast(hodnota(p, "--accent-2"), hodnota(p, "--accent-ink"))
            text = min(kontrast(hodnota(p, "--accent"), hodnota(p, podklad))
                       for podklad in ("--page", "--surface-1"))
        except (KeyError, ValueError):
            ink = ink2 = text = 0.0
        check(ink >= 4.5 and ink2 >= 4.5 and text >= 4.5, f"{klic} / {motiv}: akcent",
              f"písmo na akcentu {ink:.2f}, na akcentu 2 {ink2:.2f}, akcent na podkladu {text:.2f}")

print()
print("--- barva dat = barva aplikace (dokud není svátek) ---")
# Barva dat je kopie barvy aplikace, kterou svatek nemeni. Mimo svatek
# musi byt stejna, jinak by se grafy a stavy zmenily i bez vyzdoby.
for motiv, bloky in MOTIVY.items():
    p = slozit(bloky)
    try:
        stejne = all(hodnota(p, dat) == hodnota(p, aplikace) for dat, aplikace in (
            ("--barva-dat", "--accent"), ("--barva-dat-2", "--accent-2"),
            ("--ink-barva-dat", "--accent-ink")))
    except KeyError as chybi:
        stejne, chybi_text = False, str(chybi)
    else:
        chybi_text = ""
    check(stejne, f"{motiv}: --barva-dat* = --accent*", chybi_text)

print()
print("--- klávesnice: viditelný fokus ---")
check(re.search(r":focus-visible\s*\{[^}]*outline:\s*2px solid var\(--accent\)", CSS) is not None,
      "odkazy a tlačítka mají jednotný rámeček fokusu v barvě aplikace")
# Odkaz v popisku sloupcoveho grafu sedi v .hbar-label s overflow: hidden.
# Ramecek kresleny ven by se orizl a fokus by nebyl videt.
check(re.search(r"\.hbar-link:focus-visible\s*\{[^}]*outline-offset:\s*-", CSS) is not None,
      "v popisku grafu se rámeček kreslí dovnitř, aby se neořízl")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
