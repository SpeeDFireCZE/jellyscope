"""Druhy obsahu (filmy, seriály, ostatní) a jak se jmenují typy položek.

Část balíku statistik (viz jellyscope/stats/__init__.py) - kód přesunutý
beze změny z původního stats.py."""
from __future__ import annotations

from typing import Any

from ..i18n import translate as _t


# Podle ceho poznáme film od serialu. Jellyfin typuje epizodu jako
# "Episode", film jako "Movie"; cokoliv jineho (koncert, domaci video,
# zive vysilani - a hlavne prevzata historie, u ktere typ vubec neznáme)
# padne do "ostatniho".
KIND_MOVIE = "movies"
KIND_SERIES = "series"
KIND_OTHER = "other"
KIND_BOTH = "both"
ALLOWED_KINDS = (KIND_BOTH, KIND_MOVIE, KIND_SERIES, KIND_OTHER)

# Co v historii znamena "serial". Krome dilu i samotny serial a rada:
#
# Prevzata historie obcas nese druh "Series" misto "Episode" - bud tak
# prisel primo z Jellystatu (viz importers._jellystat_item_type), nebo se do
# zaznamu zkopiroval z polozky pri sjednoceni nazvu. Rika to min nez
# "Episode": vime jen ze ktereho serialu se divalo, ne ktery dil.
# Porad je to ale serial, takze do "Ostatního" nepatri - tam clovek
# hleda koncerty a zive vysilani, ne hodiny sledovani serialu.
SERIALOVE_TYPY = ("Episode", "Series", "Season")
_ZNAME_TYPY = ("Movie",) + SERIALOVE_TYPY
_SEZNAM_SERIALOVYCH = ", ".join(f"'{typ}'" for typ in SERIALOVE_TYPY)
_SEZNAM_ZNAMYCH = ", ".join(f"'{typ}'" for typ in _ZNAME_TYPY)


def _kind_condition(kind: str, prefix: str = "") -> str:
    """Kus SQL, ktery omezi dotaz na jeden druh.

    `prefix` je nazev tabulky s teckou ("p."). Potrebuje ho dotaz, ktery
    spojuje playback s items - obe tabulky maji sloupec item_type.

    Delici cara je stejna jako v grafu (`daily_activity_split`). Musi byt:
    z tabulky pod grafem vede na kazdem dni proklik do historie i s tim
    filtrem, takze kdyby se rozesly, ukazal by seznam jina cisla, nez na
    ktera clovek prave kliknul.
    """
    if kind == KIND_MOVIE:
        return f" AND {prefix}item_type = 'Movie'"
    if kind == KIND_SERIES:
        return f" AND {prefix}item_type IN ({_SEZNAM_SERIALOVYCH})"
    if kind == KIND_OTHER:
        # IS NULL musi byt zvlast: v SQL neni NULL "ruzne od 'Movie'",
        # NULL neni ruzne od niceho. Bez teho by prevzata historie bez
        # typu vypadla i z "ostatniho" - a to je prave ta cast, kvuli
        # ktere tenhle filtr vznikl.
        return (f" AND ({prefix}item_type IS NULL OR {prefix}item_type = ''"
                f" OR {prefix}item_type NOT IN ({_SEZNAM_ZNAMYCH}))")
    return ""


# Jak se druhu polozky rika cesky. Klice jsou to, co posila Jellyfin.
#
# K cemu to je: v historii i v bublinach grafu chceme misto "TvChannel"
# videt "Živé vysílání". Co v seznamu neni, ukaze se tak, jak to prislo -
# lepsi nez schovat to pod "Ostatní", protoze prave ta surova hodnota je
# stopa, podle ktere se da dohledat, o co slo.
TYPY_POLOZEK = {
    "Movie": "Film",
    "Episode": "Díl seriálu",
    "Series": "Seriál",
    "Season": "Řada",
    "Audio": "Hudba",
    "MusicVideo": "Videoklip",
    "MusicAlbum": "Album",
    "Video": "Video",
    "Trailer": "Upoutávka",
    "TvChannel": "Živé vysílání",
    "LiveTvChannel": "Živé vysílání",
    "TvProgram": "Pořad",
    "Program": "Pořad",
    "Recording": "Nahrávka",
    "Book": "Kniha",
    "AudioBook": "Audiokniha",
    "Photo": "Fotka",
}

# Jak se v historii pojmenuje zaznam, u ktereho typ vubec neni. Typicky
# prevzata historie: Jellystat ani Playback Reporting druh polozky
# neposilaji, takze u nich zustane prazdny.
TYP_NEZNAMY = "Neznámý (z importu)"


def nazev_typu(item_type: Any) -> str:
    """Cesky nazev druhu polozky. Prazdna hodnota = prevzaty zaznam."""
    text = str(item_type or "").strip()
    if not text:
        return _t(TYP_NEZNAMY)
    prelozeny = TYPY_POLOZEK.get(text)
    return _t(prelozeny) if prelozeny else text
