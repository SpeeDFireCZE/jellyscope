"""Překlad rozhraní.

Aplikace je psaná česky a čeština je **zdrojový jazyk**: v šablonách jsou
rovnou české věty, ne umělé klíče jako `dashboard.title`.

```jinja
{{ _("Přehled") }}
```

Když je nastavený jiný jazyk, funkce `translate()` větu najde ve slovníku.
Když je nastavená čeština, vrátí ji beze změny.

Proč takhle a ne přes klíče:

* **Šablona je čitelná i bez slovníku.** Vidíš, co se vypíše, ne kód.
* **Chybějící překlad nic nerozbije** – jen se ukáže česky. U klíčů by se
  na stránce objevilo `dashboard.title`, což je horší než správná věta
  ve špatném jazyce. Právě proto smí být překlad hotový jen z poloviny,
  což je u překladu od dobrovolníků běžný stav.
* Nevýhoda: když opravíš překlep v češtině, přestane překlad sedět. Proto
  je na to test, který hlídá, že všechny klíče slovníku někde v šablonách
  opravdu existují.

Kde ty věty leží
----------------

V `jellyscope/translations/`, jeden soubor na jazyk:

    translations/cs.json        zdroj: klíč i hodnota je česká věta
    translations/en.json        anglicky
    translations/log/cs.json    hlášky do logu (zdroj)
    translations/log/en.json    hlášky do logu anglicky

Dřív to byly dva slovníky přímo v tomhle souboru. Do Pythonu ale nevidí
žádný překladatelský nástroj a nikdo, kdo neprogramuje, do něj nesáhne;
JSON umí obojí. **Nový jazyk je jeden soubor navíc** - v seznamu se
objeví sám, nic se kvůli němu neupravuje v kódu.

Log má vlastní soubory schválně: jsou to technické věty pro toho, kdo
řeší poruchu, a míchat je mezi popisky tlačítek by překladateli jen
překáželo.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger("jellyscope.i18n")

SLOZKA = Path(__file__).resolve().parent / "translations"
DEFAULT_LANGUAGE = "cs"

# Jak se jazyk jmenuje ve svém jazyce. Kdo si vybírá jazyk, ten ještě
# nerozumí tomu současnému - "Deutsch" pozná i ten, kdo česky neumí.
#
# Seznam je tu proto, aby nový překlad nepotřeboval úpravu kódu: kód, na
# který se zapomnělo, se ukáže tak, jak je (třeba "PT-BR"). Doplnit jméno
# je vítaná, ale ne nutná změna.
JMENA_JAZYKU = {
    "cs": "Čeština",
    "en": "English",
    "sk": "Slovenčina",
    "de": "Deutsch",
    "pl": "Polski",
    "es": "Español",
    "fr": "Français",
    "it": "Italiano",
    "nl": "Nederlands",
    "pt": "Português",
    "pt-br": "Português (Brasil)",
    "ru": "Русский",
    "uk": "Українська",
    "sv": "Svenska",
    "da": "Dansk",
    "nb": "Norsk bokmål",
    "fi": "Suomi",
    "hu": "Magyar",
    "ro": "Română",
    "el": "Ελληνικά",
    "tr": "Türkçe",
    "he": "עברית",
    "ar": "العربية",
    "zh": "中文",
    "zh-hans": "简体中文",
    "zh-hant": "繁體中文",
    "ja": "日本語",
    "ko": "한국어",
}


def _nacti(cesta: Path) -> dict[str, str]:
    """Jeden soubor s překladem. Rozbitý soubor nesmí položit aplikaci.

    Překlady chodí zvenku - od překladatelů, z překladatelské služby,
    z ručně upraveného souboru. Chyba v jednom jazyce proto znamená, že
    se ten jazyk nepoužije; ne že se nespustí celá aplikace, protože
    někdo zapomněl čárku.
    """
    try:
        data = json.loads(cesta.read_text(encoding="utf-8"))
    except (OSError, ValueError) as chyba:
        log.error("preklad %s nejde precist: %s", cesta.name, chyba)
        return {}
    if not isinstance(data, dict):
        log.error("preklad %s neni slovnik", cesta.name)
        return {}
    # Do slovníku patří jen text za text. Cokoliv jiného (číslo, seznam,
    # vnořený objekt) by se dřív nebo později dostalo do šablony.
    #
    # Prázdný text není překlad, ale **chybějící překlad**: Weblate
    # zapisuje nepřeložené klíče jako `"věta": ""`. Kdyby se prázdno
    # vzalo jako platná hodnota, stránka by místo záložní angličtiny
    # ukázala nic - a v logu by zůstal prázdný řádek.
    return {klic: hodnota for klic, hodnota in data.items()
            if isinstance(klic, str) and isinstance(hodnota, str)
            and hodnota.strip()}


def _nacti_slozku(slozka: Path) -> dict[str, dict[str, str]]:
    """Všechny jazyky ze složky. Zdrojová čeština se nenačítá.

    `cs.json` je jen šablona pro překladatelský nástroj: klíč i hodnota
    jsou tatáž česká věta, takže překládat češtinu do češtiny nemá co
    dělat v paměti.
    """
    if not slozka.is_dir():
        return {}
    jazyky: dict[str, dict[str, str]] = {}
    for cesta in sorted(slozka.glob("*.json")):
        kod = cesta.stem.lower()
        if kod == DEFAULT_LANGUAGE:
            continue
        vety = _nacti(cesta)
        if vety:
            jazyky[kod] = vety
    return jazyky


TRANSLATIONS: dict[str, dict[str, str]] = _nacti_slozku(SLOZKA)
LOG_TRANSLATIONS: dict[str, dict[str, str]] = _nacti_slozku(SLOZKA / "log")

# Pojmenované zkratky pro angličtinu. Používá je log a testy; ostatní
# jazyky se berou přes TRANSLATIONS.
EN: dict[str, str] = TRANSLATIONS.get("en", {})
LOG_EN: dict[str, str] = LOG_TRANSLATIONS.get("en", {})


def _jmeno(kod: str) -> str:
    return JMENA_JAZYKU.get(kod, kod.upper())


# Jazyky, mezi kterými jde přepnout: čeština vždycky (je to zdroj, existuje
# i bez souboru) a ke každému souboru jeden.
LANGUAGES: dict[str, str] = {
    DEFAULT_LANGUAGE: _jmeno(DEFAULT_LANGUAGE),
    **{kod: _jmeno(kod) for kod in sorted(TRANSLATIONS)},
}


def current_language() -> str:
    """Nastavený jazyk rozhraní. Čte se z databáze, ne z .env."""
    # Import až tady, aby se modul dal načíst i v testech bez databáze.
    from . import db

    value = db.get_setting("ui_language", DEFAULT_LANGUAGE)
    return value if value in LANGUAGES else DEFAULT_LANGUAGE


# Jazyk, na který se propadá, když překlad chybí.
#
# Ne čeština, i když je to zdroj: čeština je užitečná Čechům a nikomu
# jinému. Kdo si zapne němčinu a překlad je hotový ze dvou třetin, má
# u zbytku číst anglicky - to je jazyk, který u mediaserveru umí skoro
# každý, kdo se dostal až sem. Česky se ukáže jen to, co nemá ani
# anglický překlad, a na to je test.
ZALOZNI_JAZYK = "en"


def translate(text: str, language: str | None = None) -> str:
    """Přeloží větu.

    Když překlad v cílovém jazyce chybí, zkusí se ještě angličtina
    a teprve pak zůstane česká věta tak, jak je. Nedodělaný překlad
    tak není napůl český, ale napůl anglický - viz ZALOZNI_JAZYK.
    """
    language = language or current_language()
    if language == DEFAULT_LANGUAGE:
        return text
    slovnik = TRANSLATIONS.get(language, {})
    if text in slovnik:
        return slovnik[text]
    if language != ZALOZNI_JAZYK:
        return TRANSLATIONS.get(ZALOZNI_JAZYK, {}).get(text, text)
    return text


def prelozit_log(text: str, language: str) -> str:
    """Totéž pro hlášky do logu - ty mají vlastní slovník i vlastní volbu.

    Log se čte i cizíma očima (u poruchy se posílá dál), takže záložní
    angličtina dává smysl o to víc.
    """
    if language == DEFAULT_LANGUAGE:
        return text
    slovnik = LOG_TRANSLATIONS.get(language, {})
    if text in slovnik:
        return slovnik[text]
    if language != ZALOZNI_JAZYK:
        return LOG_TRANSLATIONS.get(ZALOZNI_JAZYK, {}).get(text, text)
    return text


def register(env: Any) -> None:
    """Zpřístupní překlad šablonám jako funkci `_`."""
    env.globals["_"] = translate
    env.globals["ui_languages"] = LANGUAGES
