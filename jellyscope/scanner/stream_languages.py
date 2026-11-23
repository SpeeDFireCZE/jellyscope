"""Filling in the languages of streams from Jellyfin and from file names.

Part of the package scanner (see jellyscope/scanner/__init__.py)."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from .. import db, languages
from ..jellyfin import JellyfinClient, extract_streams

log = logging.getLogger("jellyscope.scanner")


async def fill_languages_from_jellyfin(item_ids: list[str]) -> int:
    """For streams whose file does not state a language, we ask Jellyfin.

    ffprobe reads only what is in the file - and in many files no language is written at a stream. Jellyfin
    often knows it, though: it works it out from the file name, the folder or the metadata it keeps itself. The
    result then looks like a bug of Jellyscope ("Neuvedeno" at a stream Jellyfin reports as Czech), although
    both tools are right - each just looks elsewhere.

    So we fill in **only the gaps**. What ffprobe read stays; the language from Jellyfin is put where it is
    "not stated", and it is noted that it is not from the file (`language_source`).

    Returns the count of streams whose language was filled in.
    """
    if not item_ids:
        return 0

    async with JellyfinClient(*db.jellyfin_connection()) as client:
        items = await client.items_by_ids(list(item_ids))

    filled = 0
    for item in items:
        filled += await asyncio.to_thread(_fill_item_languages, item)
    if filled:
        log.info("language filled in from Jellyfin for %s tracks", filled)
    return filled


def _fill_item_languages(item: dict[str, Any]) -> int:
    """One item: pairs the stored streams with those from Jellyfin."""
    item_id = item.get("Id")
    if not item_id:
        return 0

    theirs = extract_streams(item)
    if not theirs:
        return 0

    ours = db.query_all(
        "SELECT stream_index, type, language FROM item_streams "
        "WHERE item_id = ? ORDER BY stream_index",
        (item_id,))
    if not ours:
        return 0

    changes = []
    for row, their_stream in _pair_streams(ours, theirs):
        if row["language"] != languages.UNKNOWN:
            continue          # the file states the language - we do not touch that
        language = languages.normalize(their_stream.get("language"))
        if language == languages.UNKNOWN:
            continue          # Jellyfin does not know it either, nothing to fill in
        changes.append((language, item_id, row["stream_index"]))

    with db.connect() as conn:
        if changes:
            conn.executemany(
                "UPDATE item_streams SET language = ?, language_source = 'jellyfin' "
                "WHERE item_id = ? AND stream_index = ?",
                changes)
        # What stayed not stated gets the mark "we asked, nobody knows".
        #
        # Without it the app would ask about the same streams at every analysis again - and the answer would be
        # the same every time. The mark disappears by itself as soon as the file is measured again (the streams
        # are rewritten), so after the metadata are fixed in Jellyfin we ask again.
        conn.execute(
            "UPDATE item_streams SET language_source = 'unknown' "
            " WHERE item_id = ? AND language = ? AND language_source IS NULL "
            "   AND type IN ('Audio', 'Subtitle')",
            (item_id, languages.UNKNOWN))

    if changes:
        _recount_item_languages(item_id)
    return len(changes)


def candidates_for_language_from_name(library_id: str | None = None,
                                      item_ids: list[str] | None = None) -> list[str]:
    """Items for which it makes sense to try guessing the language from the file name.

    Two groups, and both were easy to overlook:

    * **Streams Jellyfin did not help with.** That step marks them as "unknown", so as not to ask about them
      again next time. When streams WITHOUT a mark were then looked for, the list was always empty and the file
      name was not used at all - it worked only when Jellyfin did not answer. That is why both shapes are taken
      here.

    * **Items that have not even a stream.** The analysis of their files never succeeded - typically a library
      the container has no access to. The name is then the only source there is; without it such files would
      stay in "Soubory bez urceneho jazyka" forever.

    `library_id` and `item_ids` keep the same scope as the analysis: refreshing one item must not start going
    through the whole library.
    """
    stream_where = ["language = ?",
                    "(language_source IS NULL OR language_source = 'unknown')",
                    "type IN ('Audio', 'Subtitle')"]
    stream_values: list[Any] = [languages.UNKNOWN]
    # `audio_from_name IS NULL` makes sure it is not computed over and over for what the name gave once.
    item_where = ["is_missing = 0", "path IS NOT NULL", "path <> ''",
                  "audio_from_name IS NULL",
                  "(audio_languages IS NULL OR audio_languages = ''"
                  " OR audio_languages = ?)"]
    item_values: list[Any] = [languages.UNKNOWN]

    if library_id:
        stream_where.append("item_id IN (SELECT id FROM items WHERE library_id = ?)")
        stream_values.append(library_id)
        item_where.append("library_id = ?")
        item_values.append(library_id)
    if item_ids:
        marks = ",".join("?" for _ in item_ids)
        stream_where.append(f"item_id IN ({marks})")
        stream_values.extend(item_ids)
        item_where.append(f"id IN ({marks})")
        item_values.extend(item_ids)

    found = {row["item_id"] for row in db.query_all(
        f"SELECT DISTINCT item_id FROM item_streams "
        f"WHERE {' AND '.join(stream_where)}", tuple(stream_values))}
    found |= {row["id"] for row in db.query_all(
        f"SELECT id FROM items WHERE {' AND '.join(item_where)}",
        tuple(item_values))}
    return sorted(found)


def fill_languages_from_name(item_ids: list[str]) -> int:
    """The last rescue: the language by the file name.

    When neither the file nor Jellyfin knows it, what is left is what a person wrote into the name -
    "Duna.2021.CZ.SK.EN.1080p.mkv". A whole section between separators is looked for, so neither "Czechacek"
    nor "enigma" passes; details in languages.from_name().

    The name says only WHICH languages are in the file, though, not which stream is which. So we split them in
    order and mark them as a guess from the name - the set of languages (and so the statistics) fits, the
    order is a guess.

    Returns the count of **items** the name helped - not streams. For one item the language may get to the
    streams, for another only into the summary, and "three streams and two summaries" could not be added up.
    """
    filled = 0
    for item_id in item_ids:
        row = db.query_one("SELECT id, path FROM items WHERE id = ?", (item_id,))
        if row is None or not row["path"]:
            continue
        if _fill_item_from_name(row["id"], row["path"]):
            filled += 1
    if filled:
        log.info("language guessed from the file name for %s items", filled)
    return filled


def _fill_item_from_name(item_id: str, path: str) -> bool:
    found = languages.from_name(path)
    if not found["audio"] and not found["subtitles"]:
        return False

    streams = db.query_all(
        "SELECT stream_index, type, language FROM item_streams "
        "WHERE item_id = ? ORDER BY stream_index", (item_id,))

    changes: list[tuple[str, str, int]] = []
    to_summary: dict[str, str] = {}
    for kind, key, column in (("Audio", "audio", "audio_from_name"),
                              ("Subtitle", "subtitles", "subtitle_from_name")):
        assigned, done = _split_by_name(
            item_id, [s for s in streams if s["type"] == kind], found[key])
        changes.extend(assigned)
        if not done and found[key]:
            # It cannot be assigned to streams, but we know that language is in the file. Into the item's summary
            # with it - the statistics read from there, and they ask "which languages are there", not "which
            # stream is which".
            to_summary[column] = languages.pack(found[key])

    if not changes and not to_summary:
        return False

    with db.connect() as conn:
        if changes:
            conn.executemany(
                "UPDATE item_streams SET language = ?, language_source = 'name' "
                "WHERE item_id = ? AND stream_index = ?",
                changes)
        for column, value in to_summary.items():
            conn.execute(f"UPDATE items SET {column} = ? WHERE id = ?",
                         (value, item_id))
    _recount_item_languages(item_id)
    return True


def _split_by_name(item_id: str, streams: list[dict[str, Any]],
                   from_name: list[str]) -> tuple[list[tuple[str, str, int]], bool]:
    """Which stream to give which language from the name - or rather none.

    Returns a pair (changes, all_assigned).

    We assign only when **as many unknown streams are left as the name adds languages beyond the known ones**.
    A typical file has a Czech dub and the original sound, but the name has only "CZ" - one mark for two
    unknown streams. Which of them is the Czech one cannot be guessed.

    Two shapes of the same reasoning are worth telling apart:

    * The name names exactly as many languages as there are streams **in the whole order** - then the positions
      fit too and all are filled in (see below).
    * The languages we **already know** in the file are subtracted from the name: with streams [English,
      unknown] and the name "CZ.EN" one new language is left for one unknown stream, so it is clear what
      belongs where.

    When even that does not work out, we return emptiness and `False` - and the caller then writes the
    languages at least into the item's summary, which claims nothing about the order.
    """
    if not from_name or not streams:
        return [], False

    unknown = [s for s in streams if s["language"] == languages.UNKNOWN]
    if not unknown:
        return [], True          # nothing to fill in, everything has a language already

    # The whole order first: the name names exactly as many languages as there are streams. Then the positions
    # can be trusted too - and what we know already must agree.
    if len(streams) == len(from_name):
        changes = []
        for stream, language in zip(streams, from_name):
            if stream["language"] == languages.UNKNOWN:
                changes.append((language, item_id, stream["stream_index"]))
            elif stream["language"] != language:
                changes = []       # the order does not fit
                break
        if changes:
            return changes, True

    # Otherwise we try the languages that are not in the file yet.
    known = {s["language"] for s in streams if s["language"] != languages.UNKNOWN}
    new = [j for j in from_name if j not in known]
    if len(new) == len(unknown):
        return [(language, item_id, stream["stream_index"])
                for stream, language in zip(unknown, new)], True

    return [], False


def _pair_streams(ours: list[dict[str, Any]],
                  theirs: list[dict[str, Any]]) -> list[tuple[dict, dict]]:
    """Which of our streams matches which from Jellyfin.

    We pair by **the order within a kind** (the first audio to the first audio), not by the stream number:
    Jellyfin numbers external subtitles too, which are not in the file, so the numbers drift apart.

    When the counts do not fit, we pair nothing. A wrongly filled-in language is worse than none - with "not
    stated" at least it shows that nobody knows.
    """
    pairs = []
    for kind in ("Audio", "Subtitle"):
        mine = [s for s in ours if s["type"] == kind]
        foreign = [s for s in theirs
                   if s.get("type") == kind and not s.get("is_external")]
        if not mine or len(mine) != len(foreign):
            continue
        pairs.extend(zip(mine, foreign))
    return pairs


def _recount_item_languages(item_id: str) -> None:
    """The summary of languages at an item by what is in the streams now.

    The columns `audio_languages` and the like are the streams written out into one string for the filters and
    the statistics. When a stream's language changes and the summary does not, the item's detail shows Czech
    but the filter "Czech" does not find it.
    """
    streams = db.query_all(
        "SELECT type, language FROM item_streams WHERE item_id = ? "
        "ORDER BY stream_index", (item_id,))
    audio = [s["language"] for s in streams if s["type"] == "Audio"]
    subtitles = [s["language"] for s in streams if s["type"] == "Subtitle"]

    # And with that the languages the file name promised that could not be assigned to a particular stream.
    # Without them the statistics would not see them - and it is the only thing we know about that file.
    item = db.query_one(
        "SELECT audio_from_name, subtitle_from_name FROM items WHERE id = ?",
        (item_id,))
    if item:
        audio += languages.unpack(item["audio_from_name"] or "")
        subtitles += languages.unpack(item["subtitle_from_name"] or "")

    with db.connect() as conn:
        conn.execute(
            "UPDATE items SET audio_languages = ?, subtitle_languages = ?, "
            "default_audio_language = ? WHERE id = ?",
            (languages.pack(audio), languages.pack(subtitles),
             # The same reasoning as in probe._summarise: the first audio stream is taken, not the one marked as
             # default.
             languages.normalize(audio[0]) if audio else None,
             item_id))
