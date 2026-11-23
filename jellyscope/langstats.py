"""Language statistics.

It answers the questions a person asks themselves about a multilingual library:

    "What language do we actually watch in at home?"
    "How many percent is Czech and how many English?"
    "Who in the family watches in the original and who dubbed?"
    "How many titles have no Czech track at all?"
    "How often are subtitles used?"

We tell apart two different things that are easily mixed up:

  * **what is watched**  - the language of the track the viewer played (the playback table)
  * **what is available** - the languages the file contains (the items table)

The first says how you speak. The second, what your library lacks.
"""

from __future__ import annotations

import math
from typing import Any

from . import charts, db, formatting, languages, stats
from .i18n import translate as _t

# A shorter playback does not count in the language statistics.
#
# The boundary is tied to how the language is determined: the collector
# ignores the first four minutes (logos, jingles) and then waits another
# minute for whether the viewer stays with the track. So the language cannot
# be confirmed earlier than after five minutes.
#
# Were two minutes left here, a playback three minutes long would enter the
# statistics WITHOUT a language - and "Not specified" would grow where we only did
# not manage to learn the answer yet. See collector.LANGUAGE_GRACE_SECONDS.
MIN_PLAY_SECONDS = 300

# ---------------------------------------------------------------------------
# Imported history does not count in the language statistics
# ---------------------------------------------------------------------------
#
# Neither Jellystat nor the Playback Reporting plugin records the language of
# the played track - they simply do not have that detail. Imported records
# would thus add only a heap of "Not specified" to the statistics, which would
# drown out the real data from the collector.
#
# And it would not be just a cosmetic problem: "60 % Czech" computed including
# records whose language nobody knows is simply **a wrong number**. Better a
# smaller sample we know something about than a big one that lies.
#
# We filter them out by `session_key`: imported rows have the prefix
# "import:", our own collection has a key from Jellyfin's session. See the
# importers/ package.
#
# It concerns only the statistics built on the `playback` table ("what is
# watched"). The overview of what is available in the library reads the
# `items` table and has nothing to do with the import.
WITHOUT_IMPORTS = "AND session_key NOT LIKE 'import:%'"

# ...but when an imported record DOES have a language, throwing it away would be a pity.
#
# The reason for the filter above is "we do not know the language", not "it is
# an import" - the second was only a shortcut for the first. Some versions of
# the Playback Reporting plugin store the track's language and Jellyscope takes
# it over at import; such a row is as good as one from our own collection and
# belongs in the statistics.
#
# It is used only for queries on the AUDIO track. Not for subtitles: even a
# record with an audio language does not say whether subtitles were on, so it
# would look like "without subtitles" and push the number down.
WITH_KNOWN_LANGUAGE = (
    "AND (session_key NOT LIKE 'import:%'"
    "     OR (audio_language IS NOT NULL AND audio_language != ''))"
)
WITH_KNOWN_LANGUAGE_P = (
    "AND (p.session_key NOT LIKE 'import:%'"
    "     OR (p.audio_language IS NOT NULL AND p.audio_language != ''))"
)


def imported_plays(days: int | None = None) -> int:
    """How many plays from the import really did not enter the numbers.

    Not all imported records are counted, only those **without a language**.
    An import that carries the track's language (some versions of Playback
    Reporting store it) is as good as our own collection and belongs in the
    statistics - see `WITH_KNOWN_LANGUAGE` above. Were it counted here too,
    the message above the charts would claim something is missing that is in
    fact in the numbers.

    It is returned so the page can say honestly what did not enter the
    numbers. Hidden data the reader does not know about are worse than none.
    """
    without_language = ("AND session_key LIKE 'import:%'"
                        "  AND (audio_language IS NULL OR audio_language = '')")
    if days is None:
        return int(db.query_value(
            f"SELECT COUNT(*) FROM playback"
            f" WHERE watched_seconds >= ? {without_language}{stats.watching_only()}",
            (MIN_PLAY_SECONDS,),
        ))
    return int(db.query_value(
        f"SELECT COUNT(*) FROM playback"
        f" WHERE started_at >= ? AND started_at < ? AND watched_seconds >= ?"
        f"   {without_language}{stats.watching_only()}",
        (*_bounds(days), MIN_PLAY_SECONDS),
    ))


def short_plays(days: int) -> int:
    """Our own plays that are too short for the statistics.

    It is returned for the message on an empty page. Without it there stood
    "let it collect for a few days", even when Jellyscope had already recorded
    several plays - only none crossed the boundary. That is confusing: a person
    waits days for something that will not happen, instead of watching a
    longer movie.
    """
    return int(db.query_value(
        f"""
        SELECT COUNT(*) FROM playback
         WHERE started_at >= ? AND started_at < ?
           AND watched_seconds > 0
           AND watched_seconds < ?
           {WITHOUT_IMPORTS}
           {stats.watching_only()}
        """,
        (*_bounds(days), MIN_PLAY_SECONDS),
    ))


def _bounds(period: Any) -> tuple[str, str]:
    """The bounds of the chosen period - (start, end). stats computes them, so it is in
    one place; the language statistics ask for the same days as the rest."""
    from .stats import _bounds as shared

    return shared(period)


# ---------------------------------------------------------------------------
# Assigning colours - once for the whole page
# ---------------------------------------------------------------------------

def colour_map() -> dict[str, int]:
    """Assigning a colour slot to every language.

    The order is set by the **total** watched time over the whole time - on
    purpose regardless of the chosen period. Were the colours computed from the
    chosen period, changing the filter from "30 days" to "a year" would recolour
    all charts: Czech would jump from blue to orange. A reader who remembered
    "blue is Czech" would be misled.

    This is called recolouring on filtering and it is a classic mistake in
    dashboards. A colour belongs to **the thing**, not to its order in the
    current selection.

    The ninth and further language no longer gets its own colour. Eight is the
    ceiling beyond which colours start to blend even for a person without a
    colour vision deficiency.
    """
    rows = db.query_all(
        f"""
        SELECT COALESCE(audio_language, 'und') AS code,
               SUM(watched_seconds) AS total
        FROM playback
        WHERE watched_seconds >= ?
          {WITH_KNOWN_LANGUAGE}
          {stats.watching_only()}
        GROUP BY code
        ORDER BY total DESC
        """,
        (MIN_PLAY_SECONDS,),
    )
    colours = {row["code"]: index + 1
               for index, row in enumerate(rows[:charts.SERIES_SLOTS])}

    # Languages that are in the library but nobody listened to yet would stay
    # without a colour otherwise. We add them after the watched ones.
    if len(colours) < charts.SERIES_SLOTS:
        extra = db.query_all(
            "SELECT audio_languages FROM items"
            " WHERE is_missing = 0 AND audio_languages IS NOT NULL AND audio_languages != ''"
        )
        counter: dict[str, int] = {}
        for row in extra:
            for code in languages.unpack(row["audio_languages"]):
                counter[code] = counter.get(code, 0) + 1

        for code, _count in sorted(counter.items(), key=lambda pair: -pair[1]):
            if len(colours) >= charts.SERIES_SLOTS:
                break
            colours.setdefault(code, len(colours) + 1)

    return colours


def _decorate(rows: list[dict[str, Any]], colours: dict[str, int],
              total: float) -> list[dict[str, Any]]:
    """Adds a human name, percentages and a colour slot to the rows."""
    for row in rows:
        code = row.get("code") or languages.UNKNOWN
        row["code"] = code
        row["label"] = languages.display(code)
        row["percent"] = (float(row.get("hours") or 0) / total * 100) if total else 0.0
        row["value"] = row.get("hours") or 0
        row["slot"] = colours.get(code)
    return rows


# ---------------------------------------------------------------------------
# What is watched
# ---------------------------------------------------------------------------

def watched_languages(days: int, colours: dict[str, int]) -> dict[str, Any]:
    """The languages' share of the watched time."""
    rows = db.query_all(
        f"""
        SELECT COALESCE(audio_language, 'und')   AS code,
               SUM(watched_seconds) / 3600.0     AS hours,
               COUNT(*)                          AS plays,
               COUNT(DISTINCT user_id)           AS users,
               COUNT(DISTINCT item_id)           AS item_count
        FROM playback
        WHERE started_at >= ? AND started_at < ?
          AND watched_seconds >= ?
          {WITH_KNOWN_LANGUAGE}
          {stats.watching_only()}
        GROUP BY code
        ORDER BY hours DESC
        """,
        (*_bounds(days), MIN_PLAY_SECONDS),
    )
    # "Titles": a movie twice in one library is one title (stats.extra_copies), the same filter as above.
    extra = stats.extra_copies(_bounds(days), "p.watched_seconds >= ? " + WITH_KNOWN_LANGUAGE_P, (MIN_PLAY_SECONDS,),
                               group_by="COALESCE(p.audio_language, 'und')")
    for row in rows:
        row["item_count"] = (row.get("item_count") or 0) - extra.get(row["code"], 0)
    total = sum(float(row["hours"] or 0) for row in rows)
    _decorate(rows, colours, total)

    chosen = preferred_language()
    main = next((row for row in rows if row["code"] == chosen), None)
    # A second number for comparison. When English already is the preferred
    # language, showing it twice makes no sense - we take the next strongest language.
    second = next((row for row in rows
                   if row["code"] != chosen and row["code"] != languages.UNKNOWN), None)

    return {
        "rows": rows,
        "total_hours": total,
        # The two numbers users care about most.
        "preferred_code": chosen,
        "preferred_name": languages.display(chosen),
        "preferred_percent": main["percent"] if main else 0.0,
        "second_name": languages.display(second["code"]) if second else "",
        "second_percent": second["percent"] if second else 0.0,
        "known_share": sum(
            row["percent"] for row in rows if row["code"] != languages.UNKNOWN
        ),
    }


def languages_by_user(days: int, colours: dict[str, int]) -> list[dict[str, Any]]:
    """The breakdown of languages for every user apart.

    SQL returns a "long" table (user, language, hours). For display we need it
    "wide" - one row per user. This conversion is called a **pivot** and in
    Python it is more readable than in SQL.
    """
    # One viewer once, with the current name (see stats.VIEWER_NAME) - a
    # renamed one would otherwise have every language twice.
    rows = db.query_all(
        f"""
        SELECT p.user_id,
               {stats.VIEWER_NAME}               AS user_name,
               COALESCE(p.audio_language, 'und') AS code,
               SUM(p.watched_seconds) / 3600.0   AS hours
        FROM playback p
        LEFT JOIN users u ON u.id = p.user_id
        WHERE p.started_at >= ? AND p.started_at < ?
          AND p.watched_seconds >= ?
          AND p.user_name IS NOT NULL
          {WITH_KNOWN_LANGUAGE_P}
          {stats.watching_only("p.")}
        GROUP BY {stats.VIEWER_GROUP}, COALESCE(p.audio_language, 'und')
        """,
        (*_bounds(days), MIN_PLAY_SECONDS),
    )

    people: dict[str, dict[str, Any]] = {}
    for row in rows:
        # Without user_id (an import) a viewer is just a name - two different ones must not merge.
        key = row["user_id"] or "name:" + str(row["user_name"])
        person = people.setdefault(key, {
            "user_id": row["user_id"],
            "name": row["user_name"],
            "total": 0.0,
            "segments": [],
        })
        hours = float(row["hours"] or 0)
        person["total"] += hours
        person["segments"].append({
            "code": row["code"],
            "label": languages.display(row["code"]),
            "value": hours,
            "hours": hours,
            "slot": colours.get(row["code"]),
        })

    result = list(people.values())
    for person in result:
        # On a tie of hours the language code decides: the order of rows from the
        # database is different every time on PostgreSQL (GROUP BY orders nothing)
        # and the page would reshuffle between two loads. On SQLite it is the same order as before.
        person["segments"].sort(key=lambda seg: (-seg["value"], seg["code"]))
        # A sum independent of the order of rows: adding in the order from the database gave a
        # different last bit (decimal numbers) every time on PostgreSQL, and with it other percentages.
        person["total"] = math.fsum(seg["value"] for seg in person["segments"])
        for segment in person["segments"]:
            segment["percent"] = (segment["value"] / person["total"] * 100) if person["total"] else 0
        # The user's most frequent language - handy as a summary in the table.
        person["main"] = person["segments"][0] if person["segments"] else None

    # A tie of hours: in the group's order (a viewer without an id first, then by id) as before on SQLite.
    result.sort(key=lambda person: (-person["total"], person["user_id"] is not None,
                                    str(person["user_id"] or ""), str(person["name"] or "")))
    return result


def subtitle_usage(days: int) -> dict[str, Any]:
    """How often subtitles are used and in what language."""
    summary = db.query_one(
        f"""
        SELECT SUM(CASE WHEN subtitle_language IS NOT NULL
                        THEN watched_seconds ELSE 0 END) / 3600.0 AS with_subtitles,
               SUM(CASE WHEN subtitle_language IS NULL
                        THEN watched_seconds ELSE 0 END) / 3600.0 AS without_subtitles
        FROM playback
        WHERE started_at >= ? AND started_at < ?
          AND watched_seconds >= ?
          {WITHOUT_IMPORTS}
          {stats.watching_only()}
        """,
        (*_bounds(days), MIN_PLAY_SECONDS),
    ) or {}

    with_subs = float(summary.get("with_subtitles") or 0)
    without_subs = float(summary.get("without_subtitles") or 0)
    total = with_subs + without_subs

    rows = db.query_all(
        f"""
        SELECT subtitle_language              AS code,
               SUM(watched_seconds) / 3600.0  AS hours,
               COUNT(*)                       AS plays
        FROM playback
        WHERE started_at >= ? AND started_at < ?
          AND watched_seconds >= ?
          AND subtitle_language IS NOT NULL
          {WITHOUT_IMPORTS}
          {stats.watching_only()}
        GROUP BY code
        ORDER BY hours DESC
        """,
        (*_bounds(days), MIN_PLAY_SECONDS),
    )
    for row in rows:
        row["label"] = languages.display(row["code"])

    return {
        "with_hours": with_subs,
        "without_hours": without_subs,
        "percent": (with_subs / total * 100) if total else 0.0,
        "rows": rows,
    }


def dubbed_vs_original(days: int, code: str | None = None) -> list[dict[str, Any]]:
    """Did the viewer watch in the preferred language, even when they had another to choose?

    It joins the language they played with the languages that were available
    in the file - and splits the plays into three clear groups.
    """
    code = code or preferred_language()
    name = languages.display(code)
    rows = db.query_all(
        f"""
        SELECT p.audio_language        AS chosen,
               i.audio_languages       AS available,
               SUM(p.watched_seconds) / 3600.0 AS hours
        FROM playback p
        JOIN items i ON i.id = p.item_id
        WHERE p.started_at >= ? AND p.started_at < ?
          AND p.watched_seconds >= ?
          AND p.audio_language IS NOT NULL
          AND i.audio_languages IS NOT NULL AND i.audio_languages != ''
          {WITH_KNOWN_LANGUAGE_P}
          {stats.watching_only("p.")}
        GROUP BY chosen, available
        """,
        (*_bounds(days), MIN_PLAY_SECONDS),
    )

    buckets = {
        "with_choice": 0.0,   # played the chosen language, though another was there too
        "only": 0.0,          # the chosen language, there was no other option
        "other": 0.0,         # played another language
    }

    for row in rows:
        available = languages.unpack(row["available"])
        hours = float(row["hours"] or 0)
        real_options = [option for option in available if option != languages.UNKNOWN]

        if row["chosen"] == code:
            if len(real_options) > 1:
                buckets["with_choice"] += hours
            else:
                buckets["only"] += hours
        else:
            buckets["other"] += hours

    total = sum(buckets.values())
    # The labels are put together only here, because they hold the chosen
    # language's name. They are translated through i18n already put together -
    # see the _t() calls in i18n.py.
    #
    # They are made of translatable pieces, not as a whole sentence: a whole
    # sentence holds the language's name, so it would never be found in the
    # dictionary of translations - and the label stayed Czech even in the English interface.
    labels = [
        ("with_choice", f"{name} – {_t('even though another language was available')}", 1),
        ("only", f"{name} – {_t('no other option')}", 3),
        ("other", f"{_t('A language other than')} {name.lower()}", 2),
    ]
    return [
        {
            "label": label,
            "value": buckets[key],
            "hours": buckets[key],
            "percent": (buckets[key] / total * 100) if total else 0.0,
            "slot": slot,
        }
        for key, label, slot in labels
        if buckets[key] > 0
    ]


# ---------------------------------------------------------------------------
# What is available in the library
# ---------------------------------------------------------------------------

def library_languages(colours: dict[str, int],
                      library_id: str | None = None) -> list[dict[str, Any]]:
    """How many titles contain which audio track.

    One title can count more times - a movie with a Czech and an English track
    belongs to both groups. So the percentages do not add up to a hundred and
    the UI has a note about it.
    """
    where = ("WHERE is_missing = 0 AND audio_languages IS NOT NULL"
             " AND audio_languages != ''")
    params: list[Any] = []
    if library_id:
        where += " AND library_id = ?"
        params.append(library_id)

    rows = db.query_all(f"SELECT audio_languages FROM items {where}", tuple(params))

    counter: dict[str, int] = {}
    for row in rows:
        for code in languages.unpack(row["audio_languages"]):
            counter[code] = counter.get(code, 0) + 1

    total = len(rows)
    result = [
        {
            "code": code,
            "label": languages.display(code),
            "item_count": count,
            "percent": (count / total * 100) if total else 0.0,
            "slot": colours.get(code),
        }
        for code, count in counter.items()
    ]
    result.sort(key=lambda entry: -entry["item_count"])
    return result


def _combination_rows() -> list[dict[str, Any]]:
    """Track combinations as they are in the library - from the most frequent.

    One query for the card and for the window with all combinations. Two copies
    of the same SELECT would drift apart sooner or later and the window would
    then show other numbers than the card it was opened from.
    """
    return db.query_all(
        """
        SELECT audio_languages              AS packed,
               COUNT(*)                     AS item_count,
               COALESCE(SUM(size_bytes), 0) AS size_bytes
        FROM items
        WHERE is_missing = 0
          AND audio_languages IS NOT NULL
          AND audio_languages != ''
          AND audio_languages != 'und'      -- titles without a determined language do not belong here
        GROUP BY audio_languages
        ORDER BY item_count DESC
        """
    )


def all_combinations() -> list[dict[str, Any]]:
    """The full list of combinations - the base for the window behind the row "Ostatní".

    The card shows the four most frequent and sums up the rest, because nobody
    reads a long list. But whoever wants to know what hides in that "Ostatní"
    is to have somewhere to click - otherwise it is a dead row.

    The percentages are computed from the same whole as on the card, so that
    "56 %" means the same in both places.
    """
    rows = _combination_rows()
    total = sum(int(row["item_count"] or 0) for row in rows)
    for row in rows:
        row["label"] = languages.combination_label(row["packed"])
        row["value"] = row["item_count"]
        row["percent"] = (int(row["item_count"] or 0) / total * 100) \
            if total else 0.0
    return rows


def language_combinations(limit: int = 4) -> list[dict[str, Any]]:
    """The most frequent track combinations - "CS + EN", "jen EN", ...

    Exactly what a person asks about a home library: do I have both tracks for
    the movies, or just one?

    We list the `limit` most frequent combinations and sum up the rest into a
    single row "Ostatní". A long list of combinations, each of which has two
    titles, nobody reads - and the overview of the main thing gets lost in it.

    Titles whose language nobody filled in do not belong in the overview: they
    say nothing about how the library is put together, only that metadata are
    missing. We return their count apart, so it is honestly visible how many
    there are.
    """
    rows = _combination_rows()

    top = rows[:limit]
    rest = rows[limit:]

    for row in top:
        row["label"] = languages.combination_label(row["packed"])
        row["value"] = row["item_count"]

    if rest:
        top.append({
            "packed": None,
            "label": _t("Other"),
            "item_count": sum(row["item_count"] for row in rest),
            "size_bytes": sum(row["size_bytes"] or 0 for row in rest),
            "value": sum(row["item_count"] for row in rest),
            "combinations": len(rest),
        })

    # We compute the percentages from ALL combinations, not only the listed ones -
    # otherwise "56 %" would mean "of what you see right now", which is another number.
    total = sum(int(row["item_count"] or 0) for row in top)
    for row in top:
        row["percent"] = (int(row["item_count"] or 0) / total * 100) if total else 0.0

    return top


def undefined_language_files(limit: int, offset: int,
                            search: str | None = None) -> list[dict[str, Any]]:
    """Files whose audio track's language nobody filled in.

    On purpose by SINGLE files, not by titles: the language is fixed for a
    concrete file, so a person needs the path to it. For a series that means
    every episode apart - exactly those that are wrong.

    The path is the main thing here; without it the list would say "something
    is wrong" and leave the searching to the user.
    """
    where, params = _without_language_filter(search)
    params.extend([limit, offset])
    return db.query_all(
        f"""
        SELECT i.id, i.name, i.series_name, i.season_name, i.index_number,
               i.parent_index_number, i.path, i.size_bytes, i.width, i.height,
               i.video_codec, i.tech_source, i.audio_languages,
               l.name AS library_name
          FROM items i
     LEFT JOIN libraries l ON l.id = i.library_id
         WHERE {' AND '.join(where)}
      ORDER BY l.name, COALESCE(i.series_name, i.name),
               i.parent_index_number, i.index_number, i.name
         LIMIT ? OFFSET ?
        """,
        tuple(params),
    )


def undefined_language_count(search: str | None = None) -> int:
    where, params = _without_language_filter(search)
    return int(db.query_value(
        f"SELECT COUNT(*) FROM items i WHERE {' AND '.join(where)}", tuple(params)))


def _without_language_filter(search: str | None) -> tuple[list[str], list[Any]]:
    """The condition "the title has no determined language" - for the list and for its count.

    Both functions must ask for the same set; when the filter was copied
    twice, it was enough to change one and the paging started to lie.

    The columns are written with the prefix `i.`, because the list joins the
    libraries and they have a `name` column too. Without the prefix the query
    was ambiguous - but only with a search filled in, so the page worked until
    the moment somebody typed something into the search field.
    """
    where = ["i.is_missing = 0",
             "(i.audio_languages IS NULL OR i.audio_languages = '' "
             "OR i.audio_languages = 'und')"]
    params: list[Any] = []
    if search:
        where.append("(i.name LIKE ? OR i.series_name LIKE ? OR i.path LIKE ?)")
        params.extend([f"%{search}%"] * 3)
    return where, params


def undefined_language_items() -> int:
    """How many titles have no language given for any track.

    It is returned apart, so the UI can say "and 12 titles without metadata on
    top" instead of them looking like another language combination.
    """
    return int(db.query_value(
        """
        SELECT COUNT(*) FROM items
        WHERE is_missing = 0
          AND (audio_languages IS NULL OR audio_languages = '' OR audio_languages = 'und')
        """
    ))


PREFERRED_SETTING = "preferred_language"
PREFERRED_DEFAULT = "cs"


def preferred_language() -> str:
    """The language this server wants to have for its titles.

    The whole Languages page used to assume Czech - it was hard-coded in SQL,
    in the names of functions and in headings. For a Czech server it fitted,
    for anybody else it was a page about nothing: a Portuguese does not need to
    learn how many percent they watch in Czech.

    The stored code is checked against the list of languages that really are in
    the library. Were a set language left that vanished from the library
    meanwhile, the page would show only zeros and there would be no telling why.
    """
    stored = languages.normalize(db.get_setting(PREFERRED_SETTING, "") or "")
    in_library = [row["code"] for row in library_language_options()]

    if stored and stored != languages.UNKNOWN and stored in in_library:
        return stored
    if PREFERRED_DEFAULT in in_library:
        return PREFERRED_DEFAULT
    # An empty library or a language that is not in it: hold on to the stored
    # choice, so the selection does not switch to something else by itself after a sync.
    return stored or PREFERRED_DEFAULT


def library_language_options() -> list[dict[str, Any]]:
    """Languages that really occur in the library - the base for the selection.

    Offering all eight hundred languages of the world would be an unusable
    list. The one a person looks for is certain to be in the library - that is
    why they build it after all.

    It is ordered by the number of titles: the most frequent language is at the
    top, where a person expects it.
    """
    rows = db.query_all(
        """
        SELECT audio_languages FROM items
        WHERE is_missing = 0
          AND audio_languages IS NOT NULL AND audio_languages != ''
        """
    )

    counts: dict[str, int] = {}
    for row in rows:
        # We do the breakdown in Python, not in SQL. Splitting a string into a list
        # every database does differently and this is once per page load.
        for code in languages.unpack(row["audio_languages"]):
            if code and code != languages.UNKNOWN:
                counts[code] = counts.get(code, 0) + 1

    # The key is on purpose not called "items". In the template `option.items`
    # would not reach the value, but the method `dict.items` - Jinja looks for an
    # attribute first and only then an item. The number would then print as a dash.
    return [
        {"code": code, "name": languages.display(code), "title_count": count}
        for code, count in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
    ]


def _series_without_stream(pattern: str) -> set[str]:
    """The ids of series that miss the track for ALL their episodes in the library.

    This is the core of a fix of one concrete bug. Originally the language
    filter was applied to single episodes already in the database and only
    then was the result grouped by series. But that dropped exactly the
    episodes that HAVE the track out of every group - and the rest then looked
    like "the whole series lacks the track", even when only a single episode of
    twenty lacked it.

    So it has to be decided from the whole series at once: how many episodes it
    has and for how many of them the track is missing. We ask the library, not
    only the watched episodes - otherwise a series of which a person saw a
    single (badly subtitled) episode would come out "whole without the track".
    """
    rows = db.query_all(
        """
        SELECT series_id,
               COUNT(*) AS total,
               SUM(CASE WHEN (',' || audio_languages || ',') NOT LIKE ?
                        THEN 1 ELSE 0 END) AS missing_count
        FROM items
        WHERE is_missing = 0
          AND series_id IS NOT NULL
          AND audio_languages IS NOT NULL AND audio_languages != ''
        GROUP BY series_id
        """,
        (pattern,),
    )
    return {str(r["series_id"]) for r in rows
            if int(r["total"] or 0) > 0 and int(r["missing_count"] or 0) == int(r["total"])}


def _episode_label(row: dict[str, Any]) -> str:
    """"S01E03" - so it can be told which episode it is (the row has season/episode)."""
    return formatting.episode_code({"parent_index_number": row.get("season"),
                                    "index_number": row.get("episode")})


def _merge_into_rows(files: list[dict[str, Any]], whole_series: set[str],
                     code: str, limit: int) -> list[dict[str, Any]]:
    """Puts the table's rows together from the files without the track.

    A series that lacks the track whole is one row. When only some episodes
    lack it, there are rows by episodes with their marks - otherwise a person
    would look for which episode they should actually get.
    """
    groups: dict[str, dict[str, Any]] = {}

    for file in files:
        series = str(file["series_id"]) if file["series_id"] else None
        whole = series is not None and series in whole_series

        if whole:
            key = f"serial:{series}"
            label = file["series_name"] or file["name"]
            link = f"/series/{series}"
        else:
            # A movie, or a single episode of a series that otherwise has the track.
            key = f"soubor:{file['item_id']}"
            episode_mark = _episode_label(file)
            if file["series_name"]:
                label = f"{file['series_name']} – {episode_mark or file['name']}"
            else:
                label = file["name"]
            link = f"/item/{file['item_id']}"

        group = groups.setdefault(key, {
            "label": label,
            "series_id": series if whole else None,
            "item_id": file["item_id"],
            "detail_url": link,
            "is_series": whole,
            # The episode's name is written under the label, but only for series.
            # For a movie it is the same as the label and would stand there twice.
            "episode_name": (file["name"]
                             if not whole and file["series_name"] else None),
            "hours": 0.0,
            "plays": 0,
            "audio": [],
            "subtitles": [],
        })
        group["hours"] += float(file["hours"] or 0)
        group["plays"] += int(file["plays"] or 0)
        group["audio"].extend(languages.unpack(file["audio_languages"]))
        group["subtitles"].extend(languages.unpack(file["subtitle_languages"]))

    rows = sorted(groups.values(), key=lambda r: r["hours"], reverse=True)[:limit]
    for row in rows:
        # pack() evens the collected codes out into one shape: sorted, without
        # duplicates, "neuvedeno" at the end.
        row["audio_languages"] = languages.pack(row.pop("audio"))
        row["audio_label"] = languages.combination_label(row["audio_languages"])
        row["has_preferred_subtitles"] = code in row.pop("subtitles")
    return rows


def missing_preferred(days: int, code: str, limit: int = 15) -> dict[str, Any]:
    """Watched titles that have no audio track in the preferred language.

    A useful list: these are titles for which it would pay to look around for
    another version - because you really watch them.
    """
    # The searched code goes into LIKE as a parameter, not into the query's
    # text - it is a value from the settings and that never belongs in SQL directly.
    #
    # We wrap it in commas from both sides: the stored shape is "cs,en,de", so
    # searching a bare '%cs%' would find a code that only contains Czech as a
    # part too. With the commas the comparison is exact.
    pattern = f"%,{code},%"

    # One row per FILE, not per series. Grouping only in Python is a necessity
    # here, not a convenience - see `_series_without_stream()` below.
    files = db.query_all(
        f"""
        SELECT i.id                            AS item_id,
               i.name                          AS name,
               i.series_id                     AS series_id,
               i.series_name                   AS series_name,
               i.parent_index_number           AS season,
               i.index_number                  AS episode,
               i.audio_languages               AS audio_languages,
               i.subtitle_languages            AS subtitle_languages,
               SUM(p.watched_seconds) / 3600.0 AS hours,
               COUNT(*)                        AS plays
        FROM playback p
        JOIN items i ON i.id = p.item_id
        WHERE p.started_at >= ? AND p.started_at < ?
          AND p.watched_seconds >= ?
          AND i.is_missing = 0
          {stats.watching_only("p.")}
          AND i.audio_languages IS NOT NULL AND i.audio_languages != ''
          AND (',' || i.audio_languages || ',') NOT LIKE ?
        GROUP BY i.id, i.name, i.series_id, i.series_name,
                 i.parent_index_number, i.index_number,
                 i.audio_languages, i.subtitle_languages
        """,
        (*_bounds(days), MIN_PLAY_SECONDS, pattern),
    )

    whole_series = _series_without_stream(pattern)
    rows = _merge_into_rows(files, whole_series, code, limit)

    total = db.query_value(
        """
        SELECT COUNT(*) FROM items
        WHERE is_missing = 0
          AND audio_languages IS NOT NULL AND audio_languages != ''
          AND (',' || audio_languages || ',') NOT LIKE ?
        """,
        (pattern,),
    )
    return {"rows": rows, "total": total, "code": code,
            "name": languages.display(code)}


def coverage() -> dict[str, Any]:
    """How much data about languages we actually have.

    Without this number the whole page could be read wrongly: when only a
    quarter of the files have the language filled in, "60 % Czech" means
    something wholly different than when all of them have it.
    """
    items = db.query_one(
        """
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN audio_languages IS NOT NULL AND audio_languages != ''
                        THEN 1 ELSE 0 END) AS with_languages
        FROM items WHERE is_missing = 0
        """
    ) or {}

    plays = db.query_one(
        f"""
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN audio_language IS NOT NULL THEN 1 ELSE 0 END) AS with_language
        FROM playback WHERE watched_seconds >= ?
          {WITHOUT_IMPORTS}
          {stats.watching_only()}
        """,
        (MIN_PLAY_SECONDS,),
    ) or {}

    item_total = items.get("total") or 0
    play_total = plays.get("total") or 0
    return {
        "items_total": item_total,
        "items_with_languages": items.get("with_languages") or 0,
        "items_percent": ((items.get("with_languages") or 0) / item_total * 100) if item_total else 0.0,
        "plays_total": play_total,
        "plays_with_language": plays.get("with_language") or 0,
        "plays_percent": ((plays.get("with_language") or 0) / play_total * 100) if play_total else 0.0,
    }
