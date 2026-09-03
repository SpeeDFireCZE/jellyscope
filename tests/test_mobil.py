# -*- coding: utf-8 -*-
r"""Průchod na mobilu - co se dá uhlídat bez prohlížeče.

Měřeno v headless Chrome na šířce 390 px (iPhone). Žádná stránka
nepřetékala do strany, ale našly se čtyři vady, a tenhle test hlídá, aby
se nevrátily:

* **Text v grafu měl 4,6 px.** SVG má pevný viewBox 760 jednotek a
  roztahuje se na šířku, takže se s ním zmenšuje i písmo - na mobilu
  vycházelo měřítko 0,42. Popisek os je proto na úzké obrazovce
  v jednotkách viewBoxu větší.
* **Ve Srovnání vypadával sloupec Rozdíl.** Čtyři sloupce se na 350 px
  nevejdou a vodorovný posuv odsunul z obrazovky právě to, kvůli čemu se
  na tu stránku člověk dívá. Na mobilu je z tabulky seznam bloků a jméno
  sloupce nese `data-popisek`.
* **Historie měla řádky vysoké 120 px** a z tabulky široké 1007 px bylo
  v 316px okně vidět třetinu. Vedlejší sloupce se na mobilu skrývají.
* **Položky menu se lámaly** na dva řádky.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_mobil.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

SABLONY = PROJECT / "jellyscope" / "templates"
STYL = (PROJECT / "jellyscope" / "static" / "style.css").read_text(encoding="utf-8")

failures = 0


def check(podminka: bool, popis: str, detail: str = "") -> None:
    global failures
    print(f"{'OK    ' if podminka else 'CHYBA '} {popis} {detail}")
    if not podminka:
        failures += 1


def mobilni_blok(sirka: int) -> str:
    """Obsah VŠECH `@media (max-width: <sirka>px)` bloků slepený za sebe.

    Bloků téže šířky je v souboru víc - pravidla stojí u toho, čeho se
    týkají, ne v jedné hromadě na konci. Kdyby se bral jen první, test by
    hlásil chybějící pravidlo, které je o pár set řádků níž.
    """
    kusy = []
    hledej = f"@media (max-width: {sirka}px)"
    od = 0
    while True:
        zacatek = STYL.find(hledej, od)
        if zacatek < 0:
            break
        hloubka, i = 0, STYL.index("{", zacatek)
        for konec in range(i, len(STYL)):
            if STYL[konec] == "{":
                hloubka += 1
            elif STYL[konec] == "}":
                hloubka -= 1
                if hloubka == 0:
                    kusy.append(STYL[i:konec])
                    od = konec
                    break
        else:
            break
    return "\n".join(kusy)


print("--- popisky os v grafu ---")
# Zakladni velikost plati na desktopu, kde se SVG roztahuje NAD 760
# jednotek a text tim naopak roste.
zakladni = re.search(r"\.axis-label\s*\{[^}]*font-size:\s*([\d.]+)px", STYL)
check(zakladni is not None, "základní velikost popisku je ve stylu")

mobil = re.search(r"\.axis-label\s*\{[^}]*font-size:\s*([\d.]+)px",
                  mobilni_blok(860))
check(mobil is not None, "a na úzké obrazovce se zvětšuje")

if zakladni and mobil:
    zakl, mob = float(zakladni.group(1)), float(mobil.group(1))
    # Merítko na 390px displeji vyslo 0,42, takze aby se popisek dostal
    # aspon na 9 skutecnych pixelu, musi mit pres 21 jednotek.
    check(mob * 0.42 >= 9,
          f"a vyjde na čitelných {mob * 0.42:.1f} px (bylo {zakl * 0.42:.1f})")
    check(mob > zakl, f"mobilní hodnota je větší ({mob} vs {zakl})")

# Levy okraj grafu musi na ten vetsi popisek stacit, jinak se orizne.
from jellyscope import charts  # noqa: E402

svg = charts.area_chart_multi(
    [{"den": f"2026-01-{d:02d}", "a": 10000.0 * d} for d in range(1, 8)],
    "den", [{"key": "a", "label": "A"}])
popisky_x = [float(x) for x in re.findall(r'<text x="([\d.]+)"[^>]*class="axis-label"', svg)]
check(bool(popisky_x), f"graf má popisky os ({len(popisky_x)})")
if popisky_x and mobil:
    # Nejlevejsi popisek je zarovnany doprava na svou souradnici, takze
    # se text kresli doleva od ni. Na mobilu je znak siroky zhruba
    # polovinu velikosti pisma.
    nejlevejsi = min(popisky_x)
    nejdelsi = max(len(t) for t in re.findall(
        r'class="axis-label">([^<]*)</text>', svg))
    potreba = nejdelsi * float(mobil.group(1)) * 0.5
    check(nejlevejsi >= potreba,
          f"a vejdou se do levého okraje ({nejlevejsi:.0f} jednotek,"
          f" potřeba ~{potreba:.0f} pro {nejdelsi} znaků)")

print()
print("--- Srovnání: na mobilu bloky, ne tabulka ---")
srovnani = (SABLONY / "srovnani.html").read_text(encoding="utf-8")
# Jen buňky uvnitř tabulky srovnání - žebříčky pod ní jsou obyčejné
# tabulky o dvou sloupcích a na mobil se vejdou.
tabulka = srovnani[srovnani.index('<table class="srovnani">'):
                   srovnani.index("</table>")]
hodnotove_bunky = re.findall(r'<td class="num[^"]*"([^>]*)>', tabulka)
check(bool(hodnotove_bunky), f"šablona má hodnotové buňky ({len(hodnotove_bunky)})")
bez_popisku = [b for b in hodnotove_bunky if "data-popisek" not in b]
check(not bez_popisku,
      f"každá nese data-popisek pro mobil (bez: {len(bez_popisku)})")

mobil_srovnani = mobilni_blok(780)
check("table.srovnani td { display: block" in mobil_srovnani.replace("\n", " ")
      or "table.srovnani td { display: block;" in mobil_srovnani
      or re.search(r"table\.srovnani[^}]*display:\s*block", mobil_srovnani) is not None,
      "a styl z tabulky na mobilu dělá bloky")
check("data-popisek" in mobil_srovnani,
      "popisek sloupce se na mobilu vypisuje z data-popisek")

# Past: sirka prvniho sloupce je desktopove pravidlo se STEJNOU
# specificitou. Kdyz stoji v souboru az za media query, prebije ji
# a na mobilu zustane sloupec siroky 34 % - presne to se stalo.
kde_sirka = STYL.find("table.srovnani th:first-child")
kde_media = STYL.find("@media (max-width: 780px)")
check(0 <= kde_sirka < kde_media,
      "desktopová šířka sloupce stojí před mobilním blokem, ne za ním")

print()
print("--- Historie: vedlejší sloupce se na mobilu skryjí ---")
historie = (SABLONY / "history.html").read_text(encoding="utf-8")
hlavicka = historie[historie.index("<thead>"):historie.index("</thead>")]
telo = historie[historie.index("<tbody>"):historie.index("{% endfor %}")]
# Pocita se na znackach, ne vyskytem retezce - jinak by se do souctu
# pripletl i komentar, ktery to vysvetluje.
th_navic = len(re.findall(r'<th[^>]*class="[^"]*sloupec-navic', hlavicka))
td_navic = len(re.findall(r'<td[^>]*class="[^"]*sloupec-navic', telo))
check(th_navic > 0, f"hlavička značí vedlejší sloupce ({th_navic})")
check(th_navic == td_navic,
      f"a v řádku jich je stejně ({th_navic} vs {td_navic})")
# Kdyby se neshodovaly, tabulka by se na mobilu rozjela: skryla by se
# hlavicka jednoho sloupce a bunka jineho.
# `count("<th")` by napočítal i samotné `<thead>`.
vsech_sloupcu = len(re.findall(r"<th[ >]", hlavicka))
check(vsech_sloupcu - th_navic == 4,
      f"zůstávají čtyři sloupce z {vsech_sloupcu} ({vsech_sloupcu - th_navic})")
check(".sloupec-navic { display: none; }" in mobilni_blok(720),
      "a styl je na mobilu skrývá")

print()
print("--- rozcestník v nastavení je na mobilu seznam ---")
nastaveni = (SABLONY / "settings.html").read_text(encoding="utf-8")
mobil_860 = mobilni_blok(860)

# Jedenáct záložek se na 390 px zalomilo do šesti řádků: 207 px odkazů,
# než člověk uvidí první nastavení.
check('class="tabs-vyber"' in nastaveni, "šablona má rozbalovací seznam")
check(".tabs-vyber + .tabs { display: none; }" in mobil_860,
      "a na mobilu záložky nahrazuje")

# Past: prosté `.tabs { display: none }` by schovalo i záložky na detailu
# knihovny, kde žádný seznam není - a nebylo by se jak přepnout.
check("    .tabs { display: none; }" not in mobil_860,
      "schovají se jen ty, které seznam nahrazuje")
detail = (SABLONY / "library_detail.html").read_text(encoding="utf-8")
check('class="tabs"' in detail and 'class="tabs-vyber"' not in detail,
      "detail knihovny má záložky bez seznamu, takže na tom závisí")

# Obojí se kreslí z jednoho seznamu - dva ručně psané by se rozešly
# a v jednom by nová sekce chyběla.
# Pocitaji se smycky, ne vyskyty retezce - jinak by se pripletl komentar.
smycky = len(re.findall(r"for [^%]*in sekce_nabidka", nastaveni))
check(smycky == 2,
      f"záložky i seznam berou položky z téhož zdroje ({smycky} smyčky)")
check('href="/settings?section=jellyfin"' not in nastaveni,
      "a nejsou vypsané po jedné")

# Bez JavaScriptu se musí dát přepnout taky - v nastavení se člověk
# nesmí zaseknout kvůli skriptu.
check("<noscript>" in nastaveni.split('class="tabs-vyber"')[1][:600],
      "bez skriptu zůstane tlačítko")

print()
print("--- menu je na mobilu pod burgerem ---")
base = (SABLONY / "base.html").read_text(encoding="utf-8")
mobil_menu = mobilni_blok(860)

# Deset položek se vedle sebe nevejde: seznam byl široký 794 px ve 390px
# okně, takže byly vidět dvě a rolovalo se do strany.
check('class="menu-prepinac"' in base and 'class="menu-tlacitko"' in base,
      "šablona má přepínač i tlačítko")
check("menu-prepinac:checked ~ .nav" in mobil_menu,
      "a otevírá se čistě stylem")

# Bez JavaScriptu: je to zaškrtávátko se štítkem, ne <button> s obsluhou.
# Kdyby se to přepsalo na tlačítko, menu by po vypnutém skriptu nešlo
# otevřít vůbec - a je to jediná cesta na ostatní stránky.
prepinac = re.search(r'<input[^>]*class="menu-prepinac"[^>]*>', base)
check(prepinac is not None and 'type="checkbox"' in prepinac.group(0),
      "přepínač je zaškrtávátko, takže menu funguje i bez skriptu")
check('for="menu-prepinac"' in base, "a štítek na něj míří")

# Na tlačítku stojí jméno otevřené stránky. Kdyby se seznam a ten popisek
# skládaly každý zvlášť, časem by tlačítko hlásilo jinou stránku, než
# která je otevřená - proto se obojí kreslí z jednoho seznamu.
check(base.count("polozky.append(") >= 9,
      f"položky menu jsou v jednom seznamu ({base.count('polozky.append(')})")
check(base.count('<a href="/insights"') == 0
      and 'href="{{ adresa }}"' in base,
      "a odkazy se z něj kreslí smyčkou, ne po jednom")

# Past: `_` je překladová funkce a `{% set _ = ... %}` ji přepíše na None,
# takže další `_("...")` spadne. Přesně to se tady stalo.
check("{% set _ = " not in base,
      "žádné `set _`, které by přebilo překladovou funkci")

check(".nav a { white-space: nowrap" in mobil_menu,
      "položky menu drží na jednom řádku")
check("minmax(150px" in mobil_menu,
      "a dlaždice se vejdou dvě vedle sebe")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
