"""Kinds of content (movies, series, other) and what item types are called.

Part of the statistics package (see jellyscope/stats/__init__.py)."""
from __future__ import annotations

from typing import Any

from ..i18n import translate as _t

# Jellyfin library types that hold movies and episodes. The library sync stores every
# library (the Library page shows them all, 16. 11. 2026) but reads items only from these,
# and the video lists over libraries (insights by library, import by paths) keep to them.
# None = a library without a type ("mixed content" of older Jellyfin versions).
VIDEO_LIBRARY_TYPES = (None, "movies", "tvshows", "homevideos", "mixed")
# Folders Jellyfin lists among the libraries that are no library (23. 11. 2026): "boxsets" is
# the Collections folder - every collection is an item inside it, made of items of real
# libraries - and "playlists" the Playlists folder. The library sync does not store them.
NOT_LIBRARY_TYPES = ("boxsets", "playlists")


def is_video_library(collection_type: str | None) -> bool:
    """Does a library of this Jellyfin type hold movies and episodes?"""
    return collection_type in VIDEO_LIBRARY_TYPES


def video_library_sql(alias: str = "l") -> str:
    """SQL condition: the row `alias` of `libraries` is a video library (see VIDEO_LIBRARY_TYPES)."""
    names = ", ".join(f"'{kind}'" for kind in VIDEO_LIBRARY_TYPES if kind)
    return f"({alias}.collection_type IS NULL OR {alias}.collection_type IN ({names}))"


# What tells a movie from a series. Jellyfin types an episode as "Episode", a movie as "Movie"; anything else (a
# concert, a home video, live TV - and above all imported history, whose type we do not know at all) falls into
# "other".
KIND_MOVIE = "movies"
KIND_SERIES = "series"
KIND_OTHER = "other"
KIND_BOTH = "both"
ALLOWED_KINDS = (KIND_BOTH, KIND_MOVIE, KIND_SERIES, KIND_OTHER)

# What "series" means in the history. Besides episodes, the series itself and a season too:
#
# Imported history sometimes carries the kind "Series" instead of "Episode" - either it came that way straight from
# Jellystat (see importers._jellystat_item_type), or it was copied into the record from the item when unifying
# names. It says less than "Episode": we know only which series was watched, not which episode. It is still a
# series, though, so it does not belong to "Other" - one looks for concerts and live TV there, not for hours of
# watching a series.
SERIES_TYPES = ("Episode", "Series", "Season")
_KNOWN_TYPES = ("Movie",) + SERIES_TYPES
_SERIES_TYPES_SQL = ", ".join(f"'{type_name}'" for type_name in SERIES_TYPES)
_KNOWN_TYPES_SQL = ", ".join(f"'{type_name}'" for type_name in _KNOWN_TYPES)

# Music listening. In the history it is a row with the kind "Audio" and the watching statistics do not count it:
# who has music playing all day would otherwise beat all watching on the Overview, in the viewer ranking and in
# the Comparison. Music has its own numbers on the Music page (stats/music_listening.py).
# Books are the same: listening to an audiobook ("AudioBook") and reading ("Book") are rows of their own kinds,
# not watching; the Books section has their numbers.
#
# So every query over the history must decide: leave music and books out (`watching_only`), count only music
# (`only_music`) or only books (`only_books`), go by the kind (`_kind_condition`), or count everything (network
# traffic, History under "All", the collector). tests/test_watching_without_music.py guards it.
KIND_MUSIC = "music"
KIND_BOOKS = "books"
KIND_ALL = "all"            # absolutely everything - History and traffic; "both" is watching
MUSIC_TYPES = ("Audio",)
_MUSIC_TYPES_SQL = ", ".join(f"'{type_name}'" for type_name in MUSIC_TYPES)
BOOK_TYPES = ("Book", "AudioBook")
_BOOK_TYPES_SQL = ", ".join(f"'{type_name}'" for type_name in BOOK_TYPES)
_NOT_WATCHING_SQL = ", ".join(f"'{type_name}'" for type_name in MUSIC_TYPES + BOOK_TYPES)
# The kind switch in History, in the order on the page. The Overview keeps ALLOWED_KINDS.
HISTORY_KINDS = (KIND_ALL, KIND_MOVIE, KIND_SERIES, KIND_MUSIC, KIND_BOOKS, KIND_OTHER)


def watching_only(prefix: str = "") -> str:
    """A piece of SQL (" AND ...") that leaves music listening and books (audiobooks, reading) out of the history.

    A row without a kind (imported history) stays: until the music sync pairs it with a track, nobody knows it
    is music.
    """
    return f" AND ({prefix}item_type IS NULL OR {prefix}item_type NOT IN ({_NOT_WATCHING_SQL}))"


def by_titles() -> str:
    """A written decision for sums by items that are then paired with `items`: an empty text.

    `items` holds only movies and episodes (scanner/item_types.py, MANAGED_TYPES), so the sum of a track's
    listening pairs with nothing and does not get into the result. The condition `watching_only` would leave
    nothing out here, it would only take the query's covering index (idx_playback_item_time,
    idx_playback_time_item): SQLite would then read the table for every play, about ten times slower. The call in
    the query tells the guard (tests/test_watching_without_music.py) that music and books were decided about.
    """
    return ""


def only_music(prefix: str = "") -> str:
    """A piece of SQL (" AND ...") that picks only music listening from the history."""
    return f" AND {prefix}item_type IN ({_MUSIC_TYPES_SQL})"


def only_books(prefix: str = "") -> str:
    """A piece of SQL (" AND ...") that picks only books (listening to audiobooks, reading) from the history."""
    return f" AND {prefix}item_type IN ({_BOOK_TYPES_SQL})"


def _kind_condition(kind: str, prefix: str = "") -> str:
    """A piece of SQL that limits a query to one kind.

    `prefix` is the table's name with a dot ("p."). A query joining playback with items needs it - both tables
    have the column item_type.

    The dividing line is the same as in the chart (`daily_activity_split`). It must be: from the table under the
    chart every day links to the history with that filter, so if they drifted apart, the list would show other
    numbers than the ones the person just clicked.
    """
    if kind == KIND_ALL:
        return ""
    if kind == KIND_MUSIC:
        return only_music(prefix)
    if kind == KIND_BOOKS:
        return only_books(prefix)
    if kind == KIND_MOVIE:
        return f" AND {prefix}item_type = 'Movie'"
    if kind == KIND_SERIES:
        return f" AND {prefix}item_type IN ({_SERIES_TYPES_SQL})"
    if kind == KIND_OTHER:
        # IS NULL must be separate: in SQL NULL is not "different from 'Movie'", NULL is different from nothing.
        # Without it imported history without a type would fall out of "other" too - and that is exactly the part
        # this filter came to be for.
        return (f" AND ({prefix}item_type IS NULL OR {prefix}item_type = ''"
                f" OR {prefix}item_type NOT IN ({_KNOWN_TYPES_SQL}, {_NOT_WATCHING_SQL}))")
    # "both" (and anything unknown) = watching: everything except music and books.
    return watching_only(prefix)


# What an item's kind is called in Czech. The keys are what Jellyfin sends.
#
# What it is for: in the history and in the chart's bubbles we want to see a readable label instead of
# "TvChannel". What is not in the list shows as it came - better than hiding it under "Other", because exactly that
# raw value is the trace by which one can find out what it was.
ITEM_TYPE_LABELS = {
    "Movie": "Movie",
    "Episode": "Episode",
    "Series": "Series",
    "Season": "Season",
    "Audio": "Music",
    "MusicVideo": "Music video",
    "MusicAlbum": "Album",
    "Video": "Video",
    "Trailer": "Trailer",
    "TvChannel": "Live TV",
    "LiveTvChannel": "Live TV",
    "TvProgram": "Programme",
    "Program": "Programme",
    "Recording": "Recording",
    "Book": "Book",
    "AudioBook": "Audiobook",
    "Photo": "Photo",
}

# What a record without any type is called in the history. Typically imported history: neither Jellystat nor
# Playback Reporting send an item's kind, so it stays empty with them.
UNKNOWN_TYPE = "Unknown (from import)"


def type_label(item_type: Any) -> str:
    """The item kind's Czech label. An empty value = an imported record."""
    text = str(item_type or "").strip()
    if not text:
        return _t(UNKNOWN_TYPE)
    translated = ITEM_TYPE_LABELS.get(text)
    return _t(translated) if translated else text
