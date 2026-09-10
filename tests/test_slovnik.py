# -*- coding: utf-8 -*-
r"""Překladové soubory: bez dvojích klíčů, bez mrtvých hesel, s celými větami.

Překlady jsou v `jellyscope/translations/`, jeden soubor na jazyk, a smí
do nich sáhnout i někdo, kdo neprogramuje - přes překladatelský nástroj
nebo rovnou v editoru. Tenhle test je pojistka proti tomu, co se přitom
dá pokazit, aniž by cokoliv spadlo:

* **Stejný klíč dvakrát.** JSON to dovolí zapsat a čtečka si vezme ten
  poslední - tiše. Dřív, když slovník bydlel v Pythonu, se to stalo
  šestadvacetkrát a u osmi z nich se ty dva překlady lišily:

    * na Síti stálo u zařízení „Last run" místo „Last seen",
    * u seriálu „at 3 seasons" místo „in 3 seasons",
    * u tabulek databáze „Lines" místo „Rows".

  Česky je to pokaždé stejné slovo, anglicky ne - a jeden klíč dvě věci
  neunese.

* **Heslo, které se nikde nepoužívá.** Nic nerozbije, jen dělá ze slovníku
  smetiště, ve kterém se překlad hledá hůř (a překladatel na něm ztrácí
  čas).

* **Ztracené zástupné místo.** „{n} dílů" -> „episodes" vypadá jako věta,
  jen v ní chybí číslo, kvůli kterému vznikla.

* **Překlad věty, kterou zdroj nezná.** Typicky po opravě překlepu
  v češtině: klíč se změnil a starý překlad zůstal viset.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_slovnik.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


SLOZKA = PROJECT / "jellyscope" / "translations"
SOUBORY = sorted(SLOZKA.glob("*.json")) + sorted((SLOZKA / "log").glob("*.json"))


def nazev(cesta: Path) -> str:
    return f"{cesta.parent.name}/{cesta.name}" if cesta.parent.name == "log" \
        else cesta.name


def nacti_s_duplicitami(cesta: Path) -> tuple[dict[str, str], list[str]]:
    """Obsah souboru a klíče, které v něm byly víc než jednou."""
    dvojmo: list[str] = []
    videno: set[str] = set()

    def hook(dvojice):
        for klic, _hodnota in dvojice:
            if klic in videno:
                dvojmo.append(klic)
            videno.add(klic)
        return dict(dvojice)

    return json.loads(cesta.read_text(encoding="utf-8"),
                      object_pairs_hook=hook), dvojmo


print("--- soubory se dají přečíst ---")
check(bool(SOUBORY), f"překlady se našly ({[nazev(c) for c in SOUBORY]})")
obsah: dict[str, dict[str, str]] = {}
for cesta in SOUBORY:
    try:
        data, dvojmo = nacti_s_duplicitami(cesta)
    except ValueError as chyba:
        check(False, f"{nazev(cesta)} je platný JSON ({chyba})")
        continue
    obsah[nazev(cesta)] = data
    check(not dvojmo,
          f"{nazev(cesta)}: {len(data)} vět, duplicity: {dvojmo[:3] or 'žádné'}")

print()
print("--- překlad nezná větu, kterou nezná zdroj ---")
# Klic je ceska veta. Kdyz se v ni opravi preklep, prekladu zustane
# klic stary - a nikdy uz se nepouzije. Ve Weblate se takova veta ukaze
# jako "zastarala", tady jako chyba.
for slozka, zdroj_jmeno in (("", "cs.json"), ("log/", "log/cs.json")):
    zdroj = set(obsah.get(zdroj_jmeno, {}))
    if not zdroj:
        continue
    for jmeno, data in obsah.items():
        if jmeno == zdroj_jmeno or not jmeno.startswith(slozka) \
                or (slozka == "" and jmeno.startswith("log/")):
            continue
        navic = sorted(set(data) - zdroj)
        check(not navic,
              f"{jmeno}: věty mimo zdroj: {len(navic)} {[v[:30] for v in navic[:2]]}")

print()
print("--- zdrojový soubor je opravdu zdroj ---")
# V cs.json je klic i hodnota tataz veta. Kdyby se lisily, prekladatel by
# ve Weblate videl jinou vetu, nez jaka je v aplikaci.
for jmeno in ("cs.json", "log/cs.json"):
    data = obsah.get(jmeno, {})
    jine = [k for k, v in data.items() if k != v]
    check(not jine,
          f"{jmeno}: klíč = hodnota ({len(jine)} nesedících {[k[:25] for k in jine[:2]]})")

print()
print("--- žádné heslo navíc ---")
# Klíč se v kódu může objevit rozdělený přes dva řádky ("začátek "
# "pokračování"), takže se hledá i ve slepené podobě. Bez toho by test
# hlásil jako nepoužité skoro každou delší větu.
zdroj_kodu = ""
for cesta in (list((PROJECT / "jellyscope").glob("*.py"))
              + list((PROJECT / "jellyscope" / "templates").rglob("*.html"))
              + [PROJECT / x for x in ("run.py", "manage.py", "demo.py")]):
    zdroj_kodu += cesta.read_text(encoding="utf-8") + "\n"

slepeny = re.sub(r'"\s*\n\s*"', "", zdroj_kodu)
slepeny = re.sub(r"'\s*\n\s*'", "", slepeny)
slepeny = re.sub(r"\s*\n\s*", " ", slepeny)
slepeny = re.sub(r'"\s*"', "", slepeny)

for jmeno in ("cs.json", "log/cs.json"):
    nepouzite = [k for k in obsah.get(jmeno, {})
                 if k not in zdroj_kodu and re.sub(r"\s+", " ", k) not in slepeny]
    check(not nepouzite,
          f"{jmeno}: bez použití {len(nepouzite)} {[k[:35] for k in nepouzite[:3]]}")

print()
print("--- co šablony chtějí přeložit, to ve zdroji je ---")
# Jinak se na cizojazycne strance objevi ceske slovo. Kontroluji se jen
# doslovne klice: `_(promenna)` se staticky precist neda.
zdroj = set(obsah.get("cs.json", {}))
chybi = []
for cesta in sorted((PROJECT / "jellyscope" / "templates").rglob("*.html")):
    text = cesta.read_text(encoding="utf-8")
    for nalez in re.finditer(r'_\(\s*"([^"]{2,})"\s*\)', text):
        klic = nalez.group(1)
        if klic not in zdroj and not klic.startswith("{"):
            chybi.append(f"{cesta.name}: {klic[:40]!r}")
check(not chybi, f"chybějících vět ve zdroji: {len(chybi)} {chybi[:3]}")

print()
print("--- dosazovaná místa sedí ve všech jazycích ---")
# "{n} dílů" -> "{n} episodes". Kdyby v překladu {n} chybělo, číslo se
# tiše ztratí: věta dává smysl, jen v ní není údaj, kvůli kterému vznikla.
# U logu je to totéž s "%s".
for jmeno, data in obsah.items():
    vzor = re.compile(r"%[sdrf]") if jmeno.startswith("log/") \
        else re.compile(r"\{(\w+)\}")
    spatne = []
    for klic, preklad in data.items():
        # Prazdny preklad = nepreloženo (tak to zapisuje Weblate); ten se
        # nenacita a propada na anglictinu, takze neni co porovnavat.
        if not preklad.strip():
            continue
        if sorted(vzor.findall(klic)) != sorted(vzor.findall(preklad)):
            spatne.append(f"{klic[:35]!r}")
    check(not spatne, f"{jmeno}: {len(spatne)} nesedících {spatne[:3]}")

print()
print("--- a jazyk se pozná podle souboru, ne podle kódu ---")
from jellyscope import i18n  # noqa: E402

check(set(i18n.LANGUAGES) >= {"cs", "en"},
      f"čeština a angličtina jsou v nabídce ({sorted(i18n.LANGUAGES)})")
check(i18n.LANGUAGES["en"] == "English", "a jmenují se svým jménem")

# Novy jazyk = jeden soubor navic. Zkousi se na docasne slozce, at se
# do balicku nic nepodstrkuje.
import tempfile  # noqa: E402

docasna = Path(tempfile.mkdtemp())
(docasna / "de.json").write_text('{"Přehled": "Übersicht"}', encoding="utf-8")
(docasna / "cs.json").write_text('{"Přehled": "Přehled"}', encoding="utf-8")
nactene = i18n._nacti_slozku(docasna)
check(set(nactene) == {"de"},
      f"soubor navíc = jazyk navíc, zdroj se nenačítá ({sorted(nactene)})")

# A rozbity soubor smi shodit jen sam sebe, ne aplikaci.
(docasna / "xx.json").write_text("{tohle není JSON", encoding="utf-8")
nactene = i18n._nacti_slozku(docasna)
check(set(nactene) == {"de"}, "rozbitý překlad se přeskočí, aplikace běží dál")

print()
print("--- nedodělaný překlad propadne na angličtinu, ne na češtinu ---")
# Cestina je zdroj, ale rozumi ji jen Cesi. Kdo si zapne nemcinu a preklad
# je hotovy z poloviny, ma u zbytku cist anglicky.
i18n.TRANSLATIONS["zkouska"] = {"Přehled": "Prehľad"}
try:
    check(i18n.translate("Přehled", "zkouska") == "Prehľad",
          "přeložená věta se vezme z toho jazyka")
    nahradni = i18n.translate("Knihovna", "zkouska")
    check(nahradni == i18n.EN["Knihovna"],
          f"nepřeložená propadne na angličtinu ({nahradni!r})")
    check(nahradni != "Knihovna", "tedy ne na češtinu")
    # Cestina a anglictina samy zustavaji, jak byly.
    check(i18n.translate("Knihovna", "cs") == "Knihovna", "čeština je beze změny")
    check(i18n.translate("Knihovna", "en") == i18n.EN["Knihovna"],
          "angličtina taky")
    # Veta, kterou nezna ani anglictina, zustane cesky - lepsi nez prazdno.
    check(i18n.translate("Tuhle větu nikdo nepřeložil", "zkouska")
          == "Tuhle větu nikdo nepřeložil",
          "co nezná ani angličtina, zůstane česky")

    # Totez u logu, ktery se u poruchy posila dal.
    i18n.LOG_TRANSLATIONS["zkouska"] = {}
    hlaska = "Odklizeno %s prehravani starsich nez %s dni"
    check(i18n.prelozit_log(hlaska, "zkouska") == i18n.LOG_EN[hlaska],
          "log propadne na angličtinu taky")
finally:
    i18n.TRANSLATIONS.pop("zkouska", None)
    i18n.LOG_TRANSLATIONS.pop("zkouska", None)

# Weblate zapisuje nepreložené klíče jako prázdný řetězec. Ten se nesmí
# načíst jako překlad - stránka by ukázala nic místo angličtiny.
import tempfile as _tf  # noqa: E402

_slozka = Path(_tf.mkdtemp())
(_slozka / "xx.json").write_text(
    '{"Knihovna": "", "Přehled": "   ", "Nastavení": "Ajustes"}', encoding="utf-8")
nacteno = i18n._nacti(_slozka / "xx.json")
check(nacteno == {"Nastavení": "Ajustes"},
      f"prázdný překlad z Weblate se nenačte, jen ten skutečný ({nacteno})")

print()
# Zkouska prekladu pres verejne rozhrani.
veta = i18n.translate("v {n} řadách", "en").format(n=3)
check(veta == "in 3 seasons", f"věta o řadách: {veta!r}")
obdobi = i18n.translate("za {obdobi}", "en").format(obdobi="30 days")
check(obdobi == "over 30 days", f"věta o období: {obdobi!r}")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
