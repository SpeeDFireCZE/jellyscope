"""Working with the languages of audio and subtitle streams.

The problem this file solves: **the same language has several different
codes**. Czech can show up as "ces" (ISO 639-2/T), "cze" (ISO 639-2/B),
"cs" (ISO 639-1) or "Czech". Were that not unified, the statistics would
show four different languages instead of one Czech, each with a quarter.

The solution is always the same: turn every shape into one **canonical
form** right at the input and work only with that from then on. This is
called normalisation, and you meet it everywhere - phone numbers, e-mails,
city names.
"""

from __future__ import annotations

import re

# The canonical form is a two-letter code (ISO 639-1). On the left are all
# the shapes Jellyfin or ffprobe can send.
_ALIASES: dict[str, str] = {}


def _register(canonical: str, *aliases: str) -> None:
    _ALIASES[canonical] = canonical
    for alias in aliases:
        _ALIASES[alias] = canonical


# The languages met most often in Czech libraries are at the top.
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

# Names for display. They are Czech - display() translates them through
# i18n, the same as any other text of the application.
_NAMES: dict[str, str] = {
    "cs": "Czech",
    "sk": "Slovak",
    "en": "English",
    "de": "German",
    "fr": "French",
    "es": "Spanish",
    "it": "Italian",
    "pl": "Polish",
    "ru": "Russian",
    "uk": "Ukrainian",
    "hu": "Hungarian",
    "ja": "Japanese",
    "ko": "Korean",
    "zh": "Chinese",
    "pt": "Portuguese",
    "nl": "Dutch",
    "sv": "Swedish",
    "da": "Danish",
    "no": "Norwegian",
    "fi": "Finnish",
    "tr": "Turkish",
    "ar": "Arabic",
    "hi": "Hindi",
    "ro": "Romanian",
    "el": "Greek",
    "he": "Hebrew",
    "th": "Thai",
    "vi": "Vietnamese",
    "id": "Indonesian",
    "bg": "Bulgarian",
    "hr": "Croatian",
    "sr": "Serbian",
    "sl": "Slovenian",
    "ca": "Catalan",
    "fa": "Persian",
}

# The code for "we do not know". Used when a stream does not state a
# language at all - which is fairly common in home libraries.
UNKNOWN = "und"


def normalize(raw: str | None) -> str:
    """Turns any notation of a language into the canonical two-letter code.

        normalize("ces")   -> "cs"
        normalize("cze")   -> "cs"
        normalize("CS")    -> "cs"
        normalize("")      -> "und"
        normalize("klingon") -> "klingon"  (unknown ones are left as they are)
    """
    if not raw:
        return UNKNOWN

    key = str(raw).strip().lower()
    if not key or key in ("und", "undefined", "unknown", "mis", "zxx"):
        return UNKNOWN

    if key in _ALIASES:
        return _ALIASES[key]

    # Sometimes "cs-CZ" or "en_US" comes - we take the part before the separator.
    for separator in ("-", "_"):
        if separator in key:
            head = key.split(separator, 1)[0]
            if head in _ALIASES:
                return _ALIASES[head]

    # An unknown language is not thrown away. Better it shows in the statistics
    # under its own code than quietly merge with "unknown".
    return key


def display(code: str | None) -> str:
    """The name of a language for display, in the interface language.

    The source form is Czech ("Cestina") and goes through translation the
    same as any other text of the application. When the translation is
    missing, it stays Czech - that is still better than nothing.

    The import is inside the function on purpose: i18n reaches into the
    database for the set language, and `languages` is otherwise a clean
    module without dependencies, used by the scanner too. This way only
    whoever really needs that link takes it.
    """
    from .i18n import translate

    canonical = normalize(code)
    if canonical == UNKNOWN:
        return translate("Not specified")
    return translate(_NAMES.get(canonical, canonical.upper()))


# ---------------------------------------------------------------------------
# The language by the file name
#
# The last resort, when neither the file nor Jellyfin knows the language:
# many libraries have it in the name ("Duna.2021.CZ.SK.EN.1080p.mkv").
#
# It all stands on one rule: a **whole part between separators** is looked
# for, not an occurrence of letters. So "Czechacek" or "enigma" never pass -
# as parts they are words that are not on the list. Looking for a substring
# would be a lottery.
#
# The second safeguard is for two-letter marks: they are taken **in upper
# case only**. Lower-case "de", "es" or "ja" are common words in movie names
# ("Casa de Papel", "Já", "Olga Hepnarová"), while a language mark is written in
# upper case.
#
# The third safeguard is the list itself: on purpose it has no shortcuts
# that are also words or names - "no" (Norwegian, but also No Time To Die),
# "it" (Italian, but also the movie IT), "el" (Greek, but also El Camino),
# "dan", "fin", "nor", "por". Better not to find a language than to get it wrong.
# ---------------------------------------------------------------------------

# Two-letter marks in UPPER case only. What does not exist in ISO (JP, KR,
# CN) is commonly written in names, so we translate it.
_UPPER_MARKS: dict[str, str] = {
    "CZ": "cs", "SK": "sk", "EN": "en", "DE": "de", "FR": "fr", "ES": "es",
    "PL": "pl", "HU": "hu", "RU": "ru", "UA": "uk", "PT": "pt", "NL": "nl",
    "JP": "ja", "KR": "ko", "CN": "zh",
}

# Longer marks pass in lower case too - mistaking them for a word is unlikely.
_LONGER_MARKS: dict[str, str] = {
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

# Words after which the language belongs to subtitles, not to the sound. "CZ tit"
# are Czech subtitles of an English movie - taking it as sound would turn the
# statistics upside down.
_SUBTITLES = {"tit", "titulky", "tits", "sub", "subs", "subbed", "subtitle",
              "subtitles", "sk-tit", "cz-tit", "forced"}

# "CZdab", "SKdabing" - one word with the mark glued to the dubbing.
_DUBBING = re.compile(r"^(cz|sk|en|de|hu|pl|ru)[-_.]?dab(ing|ovan[eyá])?$",
                      re.IGNORECASE)


# What tells that the movie's name has ended and the marks have started.
_BOUNDARY = re.compile(r"^(?:(?:19|20)\d{2}|[Ss]\d{1,2}[Ee]\d{1,3}"
                       r"|\d{3,4}[pi]|4K|HD|SD|FullHD"
                       r"|Blu[Rr]ay|BDRip|BRRip|DVD|DVDRip|DVDScr|WEB|WEBRip"
                       r"|WEBDL|HDTV|TVRip|TvRip|SATRip|REMUX|XviD|DivX"
                       r"|x26[45]|h26[45]|HEVC|AVC|AC3|DTS|AAC)$",
                       re.IGNORECASE)


# A year or a resolution glued to a mark: "2003CZ", "1080pCZ".
_GLUED = re.compile(r"^((?:19|20)\d{2}|\d{3,4}[pi])([A-Za-z]{2,3})$",
                    re.IGNORECASE)


def _split_glued(part: str) -> list[str]:
    """Makes ["2003", "CZ"] of "2003CZ". Leaves ["x264"] of "x264".

    It is split right when the name is cut up, not only when comparing:
    were "2003CZ" to stay whole, not even the boundary would be found in the
    name - and without it the mark after it is not accepted. Exactly so
    "Film-2003CZ.mp4" fell through.

    Only a year or a resolution glued to a two- to three-letter mark is
    split. Were anything split, "x264" or "C4U" would fall apart too.
    """
    match = _GLUED.match(part)
    return [match.group(1), match.group(2)] if match else [part]


def _name_end(parts: list[str]) -> int:
    """The index of the first part that is no longer part of the movie's name.

    We take the **first** such, not the last: language marks stand after the
    year, but can well stand before the resolution ("Serial.S01E03.CZdab.720p").
    """
    for i, part in enumerate(parts):
        if _BOUNDARY.match(part):
            return i
    return -1         # neither a year nor a resolution is there


def from_name(path: str | None) -> dict[str, list[str]]:
    """The languages the file name promises. The sound separately, the subtitles separately.

        from_name("Duna.2021.CZ.SK.EN.1080p.mkv")
            -> {"audio": ["cs", "sk", "en"], "subtitles": []}
        from_name("Enigma.2001.1080p.mkv")      -> empty
        from_name("Czechacek.2020.mkv")         -> empty

    It returns the order in which the marks appeared in the name. It does not
    say which mark belongs to which stream, though - whoever uses the result
    decides that.
    """
    if not path:
        return {"audio": [], "subtitles": []}

    name = str(path).replace("\\", "/").rsplit("/", 1)[-1]
    name = name.rsplit(".", 1)[0] if "." in name else name

    # The separator is anything that is not a letter or a digit. That turns the
    # name into parts that are compared whole - which is the point here.
    parts = [split
             for u in re.split(r"[^0-9A-Za-zÀ-ž]+", name) if u
             for split in _split_glued(u)]

    # Where the movie's name ends and the marks start. Nearly every name has a
    # year, an episode number or a resolution somewhere - and what is before
    # it is the title.
    #
    # It is needed because of names like "The Italian Job" or "Polish
    # Wedding": the English name of a language is a common word in a movie's
    # name. After the year nobody names the movie any more.
    boundary = _name_end(parts)

    sound: list[str] = []
    subtitles: list[str] = []

    for i, part in enumerate(parts):
        code = None
        after_boundary = 0 <= boundary < i

        if part in _UPPER_MARKS:                 # upper case anywhere
            code = _UPPER_MARKS[part]
        elif after_boundary and part.upper() in _UPPER_MARKS:
            # After the year or the resolution it is the zone of marks, not of
            # the name - there "cz" in lower case passes too. Not before the
            # boundary: there "de" from Casa de Papel or "es" from a name
            # would be caught.
            code = _UPPER_MARKS[part.upper()]
        elif part.lower() in _LONGER_MARKS:
            # Whole words ("italian", "cesky") are taken only after the
            # boundary of the name. Shortcuts (CZ, ENG) can be anywhere -
            # they do not appear as a word in a movie's name.
            #
            # When the name has no boundary at all ("The Italian Job.mkv"),
            # whole words are not accepted at all: there is nothing to tell
            # that they are not part of the name.
            if len(part) > 3 and not after_boundary:
                continue
            code = _LONGER_MARKS[part.lower()]
        elif _DUBBING.match(part):
            code = _UPPER_MARKS.get(part[:2].upper())

        if code is None:
            continue

        # The neighbouring word decides whether it is sound or subtitles.
        around = {parts[i - 1].lower() if i else "",
                  parts[i + 1].lower() if i + 1 < len(parts) else ""}
        target = subtitles if around & _SUBTITLES else sound
        if code not in target:
            target.append(code)

    return {"audio": sound, "subtitles": subtitles}


def pack(codes) -> str:
    """Puts a list of languages into one string for storing in the database.

    Stored as "cs,en,de" - sorted and without duplicates, so two titles put
    together the same are stored the same and can be compared.
    """
    seen = []
    for code in codes or []:
        canonical = normalize(code)
        if canonical not in seen:
            seen.append(canonical)

    # Known languages first, "not stated" at the end.
    known = sorted(c for c in seen if c != UNKNOWN)
    if UNKNOWN in seen:
        known.append(UNKNOWN)
    return ",".join(known)


def unpack(packed: str | None) -> list[str]:
    """The opposite of pack."""
    if not packed:
        return []
    return [part for part in str(packed).split(",") if part]


def combination_label(packed: str | None, max_codes: int = 4) -> str:
    """A label of a combination of streams, for example "CS + EN" or "CS + EN + DE + PL + others".

    Exactly what a person asks about a home library: does this movie have a
    Czech and an English stream, or just one?
    """
    from .i18n import translate

    codes = [c for c in unpack(packed) if c != UNKNOWN]
    if not codes:
        return translate("Not specified")

    # At most `max_codes` languages are listed; the rest is summed up as
    # "others". Four codes can still be read at a glance, ten no longer.
    shown = codes[:max_codes]
    label = " + ".join(code.upper() for code in shown)
    if len(codes) > max_codes:
        label += " + " + translate("other")
    return label
