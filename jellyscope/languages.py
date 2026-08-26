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

import re

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


# ---------------------------------------------------------------------------
# Jazyk podle názvu souboru
#
# Poslední záchrana, když jazyk nezná ani soubor, ani Jellyfin: hodně
# knihoven ho má v názvu ("Duna.2021.CZ.SK.EN.1080p.mkv").
#
# Celé to stojí na jednom pravidle: hledá se **celý úsek mezi oddělovači**,
# ne výskyt písmen. "Czechacek" ani "enigma" proto nikdy neprojdou - jako
# úsek to jsou slova, která v seznamu nejsou. Kdyby se hledal podřetězec,
# byla by to loterie.
#
# Druhá pojistka je u dvoupísmenných značek: berou se **jen velkými**.
# Malé "de", "es" nebo "ja" jsou běžná slova v názvech filmů (Casa de
# Papel, Já, Olga Hepnarová), zatímco značka jazyka se píše velkými.
#
# Třetí pojistka je seznam sám: schválně v něm nejsou zkratky, které jsou
# zároveň slovy nebo jmény - "no" (Norwegian, ale i No Time To Die),
# "it" (Italian, ale i film IT), "el" (Greek, ale i El Camino), "dan",
# "fin", "nor", "por". Radši jazyk nenajít než ho určit špatně.
# ---------------------------------------------------------------------------

# Dvoupísmenné jen VELKÝMI. To, co v ISO neexistuje (JP, KR, CN), se
# v názvech běžně píše, tak to přeložíme.
_ZNACKY_VELKE: dict[str, str] = {
    "CZ": "cs", "SK": "sk", "EN": "en", "DE": "de", "FR": "fr", "ES": "es",
    "PL": "pl", "HU": "hu", "RU": "ru", "UA": "uk", "PT": "pt", "NL": "nl",
    "JP": "ja", "KR": "ko", "CN": "zh",
}

# Delší značky projdou i malými písmeny - záměna se slovem je nepravděpodobná.
_ZNACKY_DELSI: dict[str, str] = {
    "cze": "cs", "ces": "cs", "czech": "cs", "cesky": "cs", "cestina": "cs",
    "slk": "sk", "svk": "sk", "slovak": "sk", "slovensky": "sk",
    "eng": "en", "english": "en", "anglicky": "en",
    "ger": "de", "deu": "de", "german": "de", "nemecky": "de",
    "fra": "fr", "fre": "fr", "french": "fr",
    "spa": "es", "spanish": "es",
    "ita": "it", "italian": "it",
    "pol": "pl", "polish": "pl",
    "rus": "ru", "russian": "ru",
    "ukr": "uk", "ukrainian": "uk",
    "hun": "hu", "hungarian": "hu",
    "jpn": "ja", "japanese": "ja",
    "kor": "ko", "korean": "ko",
    "chinese": "zh",
    "portuguese": "pt", "dutch": "nl",
    "swe": "sv", "swedish": "sv",
    "tur": "tr", "turkish": "tr",
}

# Slova, po kterých jazyk patří titulkům, ne zvuku. "CZ tit" jsou české
# titulky u anglického filmu - vzít to jako zvuk by statistiku otočilo.
_TITULKY = {"tit", "titulky", "tits", "sub", "subs", "subbed", "subtitle",
            "subtitles", "sk-tit", "cz-tit", "forced"}

# "CZdab", "SKdabing" - jedno slovo, ve kterém je značka slepená s dabingem.
_DABING = re.compile(r"^(cz|sk|en|de|hu|pl|ru)[-_.]?dab(ing|ovan[eyá])?$",
                     re.IGNORECASE)


# Podle čeho se pozná, že název filmu skončil a začaly značky.
_HRANICE = re.compile(r"^(?:(?:19|20)\d{2}|[Ss]\d{1,2}[Ee]\d{1,3}"
                      r"|\d{3,4}[pi]|4K|HD|SD|FullHD"
                      r"|Blu[Rr]ay|BDRip|BRRip|DVD|DVDRip|DVDScr|WEB|WEBRip"
                      r"|WEBDL|HDTV|TVRip|TvRip|SATRip|REMUX|XviD|DivX"
                      r"|x26[45]|h26[45]|HEVC|AVC|AC3|DTS|AAC)$",
                      re.IGNORECASE)


# Rok nebo rozliseni slepene se znackou: "2003CZ", "1080pCZ".
_SLEPENE = re.compile(r"^((?:19|20)\d{2}|\d{3,4}[pi])([A-Za-z]{2,3})$",
                      re.IGNORECASE)


def _rozdel_slepene(usek: str) -> list[str]:
    """Z "2003CZ" udělá ["2003", "CZ"]. Z "x264" nechá ["x264"].

    Dělíme hned při krájení názvu, ne až při porovnávání: kdyby zůstalo
    "2003CZ" vcelku, nenašla by se v názvu ani hranice - a bez ní se
    značka za ní neuzná. Přesně tak propadlo "Film-2003CZ.mp4".

    Dělí se jen rok nebo rozlišení slepené s dvou až třípísmennou
    značkou. Kdyby se dělilo cokoliv, rozpadl by se i "x264" nebo "C4U".
    """
    nalez = _SLEPENE.match(usek)
    return [nalez.group(1), nalez.group(2)] if nalez else [usek]


def _konec_nazvu(useky: list[str]) -> int:
    """Index prvního úseku, který už není součástí názvu filmu.

    Bereme **první** takový, ne poslední: značky jazyka stojí za rokem,
    ale klidně před rozlišením ("Serial.S01E03.CZdab.720p").
    """
    for i, usek in enumerate(useky):
        if _HRANICE.match(usek):
            return i
    return -1         # rok ani rozlišení tam nejsou


def z_nazvu(cesta: str | None) -> dict[str, list[str]]:
    """Jazyky, které slibuje název souboru. Zvlášť zvuk, zvlášť titulky.

        z_nazvu("Duna.2021.CZ.SK.EN.1080p.mkv")
            -> {"zvuk": ["cs", "sk", "en"], "titulky": []}
        z_nazvu("Enigma.2001.1080p.mkv")      -> prázdné
        z_nazvu("Czechacek.2020.mkv")         -> prázdné

    Vrací pořadí, v jakém se značky v názvu objevily. Neříká to ale, která
    značka patří které stopě - o tom rozhoduje ten, kdo výsledek použije.
    """
    if not cesta:
        return {"zvuk": [], "titulky": []}

    nazev = str(cesta).replace("\\", "/").rsplit("/", 1)[-1]
    nazev = nazev.rsplit(".", 1)[0] if "." in nazev else nazev

    # Oddělovačem je cokoliv, co není písmeno ani číslice. Tím se z názvu
    # stanou úseky, které porovnáváme celé - o to tu jde.
    useky = [rozdeleny
             for u in re.split(r"[^0-9A-Za-zÀ-ž]+", nazev) if u
             for rozdeleny in _rozdel_slepene(u)]

    # Kde končí název filmu a začínají značky. Skoro každý název má někde
    # rok, číslo dílu nebo rozlišení - a co je před ním, je titul.
    #
    # Je to potřeba kvůli názvům jako "The Italian Job" nebo "Polish
    # Wedding": anglické jméno jazyka je běžné slovo v názvu filmu. Za
    # rokem už tam nikdo film nepojmenovává.
    hranice = _konec_nazvu(useky)

    zvuk: list[str] = []
    titulky: list[str] = []

    for i, usek in enumerate(useky):
        kod = None
        za_hranici = 0 <= hranice < i

        if usek in _ZNACKY_VELKE:                 # velkými kdekoliv
            kod = _ZNACKY_VELKE[usek]
        elif za_hranici and usek.upper() in _ZNACKY_VELKE:
            # Za rokem nebo rozlišením už je zóna značek, ne názvu -
            # tam projde i "cz" malými. Před hranicí ne: tam by se chytlo
            # "de" z Casa de Papel nebo "es" z názvu.
            kod = _ZNACKY_VELKE[usek.upper()]
        elif usek.lower() in _ZNACKY_DELSI:
            # Celá slova ("italian", "cesky") bereme jen za hranicí názvu.
            # Zkratky (CZ, ENG) můžou být kdekoliv - ty se jako slovo
            # v názvu filmu neobjeví.
            #
            # Když v názvu žádná hranice není ("The Italian Job.mkv"),
            # celá slova neuznáváme vůbec: nemáme podle čeho poznat, že
            # nejsou součástí názvu.
            if len(usek) > 3 and not za_hranici:
                continue
            kod = _ZNACKY_DELSI[usek.lower()]
        elif _DABING.match(usek):
            kod = _ZNACKY_VELKE.get(usek[:2].upper())

        if kod is None:
            continue

        # Sousední slovo rozhoduje, jestli jde o zvuk, nebo o titulky.
        okoli = {useky[i - 1].lower() if i else "",
                 useky[i + 1].lower() if i + 1 < len(useky) else ""}
        kam = titulky if okoli & _TITULKY else zvuk
        if kod not in kam:
            kam.append(kod)

    return {"zvuk": zvuk, "titulky": titulky}


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
