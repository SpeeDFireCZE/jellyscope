"""Prace s jazyky zvukovych stop a titulku.

Problem, ktery tenhle soubor resi: **stejny jazyk ma vic ruznych kodu**.
Cestina se muze objevit jako "ces" (ISO 639-2/T), "cze" (ISO 639-2/B),
"cs" (ISO 639-1) nebo "Czech". Kdyby se to nesjednotilo, statistika by
misto jedne cestiny ukazala ctyri ruzne jazyky, kazdy s ctvrtinou.

Reseni je vzdycky stejne: hned na vstupu prevest vsechny podoby na jeden
**kanonicky tvar** a dal uz pracovat jen s nim. Tomuhle se rika normalizace
a potkas ji vsude - u telefonnich cisel, e-mailu, nazvu mest.
"""

from __future__ import annotations

# Kanonicky tvar je dvoupismenny kod (ISO 639-1). Vlevo jsou vsechny
# podoby, ktere muze poslat Jellyfin nebo ffprobe.
_ALIASES: dict[str, str] = {}


def _register(canonical: str, *aliases: str) -> None:
    _ALIASES[canonical] = canonical
    for alias in aliases:
        _ALIASES[alias] = canonical


# Jazyky, ktere se v ceskych knihovnach potkavaji nejcasteji, jsou nahore.
_register("cs", "ces", "cze", "czech", "cesky", "cestina")
_register("sk", "slk", "slo", "slovak", "slovensky")
_register("en", "eng", "english", "en-us", "en-gb", "anglicky")
_register("de", "deu", "ger", "german", "nemecky")
_register("fr", "fra", "fre", "french")
_register("es", "spa", "spanish")
_register("it", "ita", "italian")
_register("pl", "pol", "polish")
_register("ru", "rus", "russian")
_register("uk", "ukr", "ukrainian")
_register("hu", "hun", "hungarian")
_register("ja", "jpn", "japanese")
_register("ko", "kor", "korean")
_register("zh", "zho", "chi", "chinese", "cmn")
_register("pt", "por", "portuguese", "pt-br")
_register("nl", "nld", "dut", "dutch")
_register("sv", "swe", "swedish")
_register("da", "dan", "danish")
_register("no", "nor", "nob", "norwegian")
_register("fi", "fin", "finnish")
_register("tr", "tur", "turkish")
_register("ar", "ara", "arabic")
_register("hi", "hin", "hindi")
_register("ro", "ron", "rum", "romanian")
_register("el", "ell", "gre", "greek")
_register("he", "heb", "hebrew")
_register("th", "tha", "thai")
_register("vi", "vie", "vietnamese")
_register("id", "ind", "indonesian")
_register("bg", "bul", "bulgarian")
_register("hr", "hrv", "croatian")
_register("sr", "srp", "serbian")
_register("sl", "slv", "slovenian")
_register("ca", "cat", "catalan")
_register("fa", "fas", "per", "persian")

# Nazvy pro zobrazeni. Jsou cesky - do anglictiny je prelozi display()
# pres i18n, stejne jako kazdy jiny text v aplikaci.
_NAMES: dict[str, str] = {
    "cs": "Čeština",
    "sk": "Slovenština",
    "en": "Angličtina",
    "de": "Němčina",
    "fr": "Francouzština",
    "es": "Španělština",
    "it": "Italština",
    "pl": "Polština",
    "ru": "Ruština",
    "uk": "Ukrajinština",
    "hu": "Maďarština",
    "ja": "Japonština",
    "ko": "Korejština",
    "zh": "Čínština",
    "pt": "Portugalština",
    "nl": "Nizozemština",
    "sv": "Švédština",
    "da": "Dánština",
    "no": "Norština",
    "fi": "Finština",
    "tr": "Turečtina",
    "ar": "Arabština",
    "hi": "Hindština",
    "ro": "Rumunština",
    "el": "Řečtina",
    "he": "Hebrejština",
    "th": "Thajština",
    "vi": "Vietnamština",
    "id": "Indonéština",
    "bg": "Bulharština",
    "hr": "Chorvatština",
    "sr": "Srbština",
    "sl": "Slovinština",
    "ca": "Katalánština",
    "fa": "Perština",
}

# Kod pro "nevime". Pouziva se, kdyz stopa jazyk vubec neuvadi -
# coz je u domacich knihoven pomerne bezne.
UNKNOWN = "und"


def normalize(raw: str | None) -> str:
    """Prevede jakykoliv zapis jazyka na kanonicky dvoupismenny kod.

        normalize("ces")   -> "cs"
        normalize("cze")   -> "cs"
        normalize("CS")    -> "cs"
        normalize("")      -> "und"
        normalize("klingon") -> "klingon"  (nezname necháme, jak jsou)
    """
    if not raw:
        return UNKNOWN

    key = str(raw).strip().lower()
    if not key or key in ("und", "undefined", "unknown", "mis", "zxx"):
        return UNKNOWN

    if key in _ALIASES:
        return _ALIASES[key]

    # Nekdy prijde "cs-CZ" nebo "en_US" - vezmeme cast pred oddelovacem.
    for separator in ("-", "_"):
        if separator in key:
            head = key.split(separator, 1)[0]
            if head in _ALIASES:
                return _ALIASES[head]

    # Neznamy jazyk nezahazujeme. Radeji at je ve statistice videt pod
    # svym kodem, nez aby tise splynul s "nezname".
    return key


def display(code: str | None) -> str:
    """Nazev jazyka pro zobrazeni, v jazyce rozhrani.

    Zdrojovy tvar je cesky ("Cestina") a projde prekladem stejne jako
    kterykoliv jiny text v aplikaci. Kdyz preklad chybi, zustane cesky -
    to je porad lepsi nez prazdno.

    Import je uvnitr funkce zamerne: i18n si sahne do databaze pro
    nastaveny jazyk, a `languages` je jinak cisty modul bez zavislosti,
    ktery pouziva i scanner. Takhle si tu vazbu bere jen ten, kdo ji
    opravdu potrebuje.
    """
    from .i18n import translate

    canonical = normalize(code)
    if canonical == UNKNOWN:
        return translate("Neuvedeno")
    return translate(_NAMES.get(canonical, canonical.upper()))


def pack(codes) -> str:
    """Seznam jazyku slozi do jednoho retezce pro ulozeni do databaze.

    Ukladame jako "cs,en,de" - setridene a bez duplicit, aby se dva
    stejne sestavene tituly ulozily stejne a daly se porovnat.
    """
    seen = []
    for code in codes or []:
        canonical = normalize(code)
        if canonical not in seen:
            seen.append(canonical)

    # Zname jazyky napred, "neuvedeno" az na konec.
    known = sorted(c for c in seen if c != UNKNOWN)
    if UNKNOWN in seen:
        known.append(UNKNOWN)
    return ",".join(known)


def unpack(packed: str | None) -> list[str]:
    """Opak funkce pack."""
    if not packed:
        return []
    return [part for part in str(packed).split(",") if part]


def combination_label(packed: str | None, max_codes: int = 4) -> str:
    """Popisek kombinace stop, napriklad "CS + EN" nebo "CS + EN + DE + PL + ostatni".

    Presne to, na co se clovek u domaci knihovny pta: mam u toho filmu
    ceskou i anglickou stopu, nebo jen jednu?
    """
    from .i18n import translate

    codes = [c for c in unpack(packed) if c != UNKNOWN]
    if not codes:
        return translate("Neuvedeno")

    # Vypiseme az `max_codes` jazyku; co je navic, shrneme do "ostatni".
    # Ctyri kody se jeste daji precist jedinym pohledem, deset uz ne.
    shown = codes[:max_codes]
    label = " + ".join(code.upper() for code in shown)
    if len(codes) > max_codes:
        label += " + " + translate("ostatní")
    return label
