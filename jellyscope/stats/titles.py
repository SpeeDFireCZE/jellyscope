"""Grouping plays into titles (a series, a movie) and the most watched titles.

Part of the statistics package (see jellyscope/stats/__init__.py)."""
from __future__ import annotations

import re
from typing import Any

from .. import db
from .kinds import KIND_BOTH, KIND_MOVIE, KIND_SERIES, watching_only
from .periods import _bounds


# An episode's name as the Playback Reporting plugin and some clients write it: "Series - s01e01 - Episode name".
# Such a record has neither `series_name` nor an item in the library, so the series can be told only from here -
# and without it the overviews broke it up into single episodes.
# The series ends in a visible character (the name is stripped): `.+?` tried every length against a long run of
# spaces (seconds for one name), `.*?\S` skips the lengths that end in a space.
_SERIES_FROM_NAME = re.compile(r"^(?P<series>.*?\S)\s*[-–]\s*s\d{1,3}e\d{1,4}\b", re.I)


def series_from_name(name: Any) -> str | None:
    """Pulls the series' name out of "Series - s01e01 - Episode"."""
    if not name:
        return None
    match = _SERIES_FROM_NAME.match(str(name).strip())
    if not match:
        return None
    series_name = match.group("series").strip(" -–")
    return series_name or None


def _episodes_by_name() -> dict[str, tuple[str, str]]:
    """Episode names from the library -> the id of the series they belong to.

    It serves one thing only: a history record that carries just the episode's name ("The Bells") and whose item
    the library no longer leads to can be put under the series the episode belongs to ("Game of Thrones").
    Without it the overview made a title of its own of it - a movie that does not exist.

    **Only unambiguous matches.** Episode names repeat between series ("Pilot", "Part 1"); with such a name we
    rather do not guess, because wrongly assigned history is worse than history we know is set aside.

    The keys have the prefix `episode:` so that they do not mix with series names - a series and its episode can have
    the same name.
    """
    by_name: dict[str, set[str]] = {}
    for row in db.query_all(
        "SELECT name, series_id FROM items WHERE series_id IS NOT NULL"
        "  AND name IS NOT NULL AND name != ''"
    ):
        name = str(row["name"]).strip().lower()
        by_name.setdefault(name, set()).add(str(row["series_id"]))

    names = _series_names()
    return {f"episode:{name}": (next(iter(series_ids)),
                            names.get(next(iter(series_ids)), "?"))
            for name, series_ids in by_name.items() if len(series_ids) == 1}


def _series_names() -> dict[str, str]:
    """A series' id -> its name. For the row's label."""
    return {str(r["series_id"]): str(r["name"]) for r in db.query_all(
        "SELECT series_id, MAX(series_name) AS name FROM items"
        " WHERE series_id IS NOT NULL AND series_name IS NOT NULL"
        " GROUP BY series_id")}


def extra_copies(bounds: tuple[Any, Any], condition: str, params: tuple[Any, ...] = (),
                 group_by: str | None = None) -> Any:
    """By how much `COUNT(DISTINCT item_id)` over plays overcounts titles because of movie copies.

    A movie twice in one library is one title (scanner/copies.py), but a play carries the item's id: who played
    both copies saw one title, not two. So the summaries count items as before (one pass over the plays' index,
    without joining the items) and this function says how much to subtract. It goes from the items that have
    copies - there are few - to their plays.

    `bounds` is the period's (od, do). `condition` with `params` is the rest of the filter of the summary the
    surplus is subtracted from - whole and the same as there (`p.watched_seconds > 0 AND p.user_id = ?`), the
    play has the alias `p`. With `group_by` (an expression over the play) it returns a dict {value: surplus},
    otherwise a number. The caller writes both in the code, nothing comes here from the user.
    """
    rows = db.query_all(
        f"""
        SELECT {group_by + " AS key," if group_by else ""}
               COUNT(DISTINCT p.item_id) - COUNT(DISTINCT COALESCE(i.main_id, i.id)) AS extra
          FROM items i
          JOIN playback p ON p.item_id = i.id
         WHERE (i.main_id IS NOT NULL OR i.id IN (SELECT main_id FROM items WHERE main_id IS NOT NULL))
           AND p.started_at >= ? AND p.started_at < ? AND {condition}{watching_only("p.")}
        {"GROUP BY " + group_by if group_by else ""}
        """,  # nosec B608 - `group_by` and `condition` are the caller's constants
        (*bounds, *params))
    if group_by:
        return {r["key"]: int(r["extra"] or 0) for r in rows}
    return int(rows[0]["extra"] or 0) if rows else 0


def title_key(row: dict[str, Any],
              by_name: dict[str, str] | None = None) -> tuple[str, str, bool]:
    """Which group a history row belongs to: (key, label, is it a series).

    The order goes from the most reliable data to the weakest:

      1. `series_id` from the library - the only thing that does not depend on how a record was once filled in,
      2. the series' name (from the record or from the item),
      3. the series' name parsed from "Series - s01e01 - Episode",
      4. the episode of that name the library knows (see `_episodes_by_name()`),
      5. the item itself - a movie, or an episode whose series we know nothing about. A movie that is twice in
         one library has the main one's id on the secondary copy (`main_id`, scanner/copies.py): both copies
         thus fall into one group and the ranking shows one title with the plays summed.

    Points 3 and 4 are here because imported history says nothing about the series. Playback Reporting stores
    the name as "Blue - s01e17 - Calypso"; other sources only "The Bells". The overview then showed five rows of
    their own instead of one "Blue", or the episode "The Bells" as a movie beside "Game of Thrones".
    """
    if row.get("series_id"):
        return (f"series:{row['series_id']}",
                str(row.get("series_name") or row.get("i_series_name")
                    or series_from_name(row.get("item_name")) or row.get("i_name") or "?"),
                True)

    # We know the item in the library and **it is not an episode** - that ends the guessing. Everything below
    # this line is guesswork from the name and is here only for records about which nothing else can be found.
    #
    # Without this barrier this happened: a movie had the same name as an episode of another series (or as the
    # series itself), so it fell into its group. The group was then named after that other title, but the link
    # led to this movie - the label said one thing and the link led elsewhere.
    #
    # An episode without `series_id` is the exception: there guessing helps, because otherwise we have no way to
    # assign it to the series.
    if row.get("existing_id") and str(row.get("i_type") or "") != "Episode":
        return (f"item:{row.get('main_id') or row.get('item_id') or row['existing_id']}",
                str(row.get("i_name") or row.get("item_name") or "?"), False)

    # The last try, still before giving up: an episode of that name exists in the library and knows its series.
    # The record knows nothing about it and it cannot be told from the name ("The Bells") - but the library knows
    # it. Without this the overview made a title of its own of it beside the series it belongs to.
    # See `_episodes_by_name()`.
    from_library = (by_name or {}).get(
        "episode:" + str(row.get("item_name") or "").strip().lower())
    if from_library:
        series_id, name = from_library
        return (f"series:{series_id}", name, True)

    series_name = (row.get("series_name") or row.get("i_series_name")
                   or series_from_name(row.get("item_name")))
    if series_name:
        normalized = str(series_name).strip().lower()
        # When we know the same series in the library, we use its id - otherwise it would break into two rows: one
        # made of imported history (where only the name is) and the other of our own collecting (where the id is
        # too). Exactly that showed in the overview.
        known = (by_name or {}).get(normalized)
        if known:
            return (f"series:{known}", str(series_name).strip(), True)

        # A key from the name is weaker than from the id, but it still merges what belongs together. It compares
        # in lower case - "Blue" and "blue" are the same.
        return (f"name:{normalized}", str(series_name).strip(), True)

    # And one more possibility: the record's name is the series' name right away. It happens with imported history
    # that does not remember the episode, or when Jellyfin records a play at the level of the series.
    as_series = (by_name or {}).get(
        str(row.get("item_name") or "").strip().lower())
    if as_series:
        return (f"series:{as_series}", str(row["item_name"]).strip(), True)

    item = row.get("item_id") or row.get("item_name") or "?"
    return (f"item:{item}",
            str(row.get("i_name") or row.get("item_name") or "?"), False)


# The columns `title_key()` builds the group from. Both queries below use them (the most watched titles and the
# transcoded files), so it is better to have them in one place than in two copies that drift apart over time.
#
# It groups by TITLE_GROUP_KEY, not by a bare `p.item_id`: a record without an item's id does not normally occur in
# Jellyscope, but if one turned up, all such rows would fall into one group and a title made of several different
# movies would come to be.
TITLE_GROUP_KEY = "COALESCE(p.item_id, p.item_name)"
TITLE_GROUP_COLUMNS = """
        MAX(p.item_id)             AS item_id,
        MAX(p.item_name)           AS item_name,
        MAX(p.series_name)         AS series_name,
        MAX(i.id)                  AS existing_id,
        MAX(i.main_id)           AS main_id,
        MAX(i.series_id)           AS series_id,
        MAX(i.series_name)         AS i_series_name,
        MAX(i.name)                AS i_name,
        MAX(i.type)                AS i_type,
        MAX(i.is_missing)          AS is_missing
"""


def _merge_titles(rows: list[dict[str, Any]], kind: str, limit: int,
                  values: dict[str, str],
                  carry: tuple[str, ...] = (),
                  hide_unsorted: bool = False,
                  copies_apart: bool = False) -> list[dict[str, Any]]:
    """Puts the rows for single items together into titles (a series = one row).

    `values` says which columns are to be summed - for the most watched titles the hours and the plays, for the
    transcoded ones the count of transcodes.

    `carry` are columns that are not summed but copied from the group's strongest episode - the codec, the
    resolution, the transcode reason. For a series they are practically the same for all episodes, so averaging
    them would bring nothing.

    `hide_unsorted` leaves out titles nothing leads to in the library **nor in the archive**. They are leftovers of
    the history whose subject cannot be found out: the record carries only "Episode 6" and every series in the
    library has such an episode, so there is no unambiguous match. Assigning them blindly would mean crediting the
    watching to someone else's series.

    In the ranking of the most watched such a row says nothing - it cannot be clicked and poses as a movie that
    does not exist. How many there were is returned in `_hidden`, so that the page can say it out loud; the watched
    time is not lost anywhere by it, History keeps showing it.

    `copies_apart` keeps every copy of a movie as a row of its own with its own link (otherwise a movie twice in one
    library is one title, see title_key). For lists that talk about files - which one is transcoded, which one is
    in poor quality: there that file is to show.

    It merges in Python, not in SQL: the group's key can fall back to parsing the name ("Series - s01e01 -
    Episode") and that cannot be written understandably in SQL in either of the two databases.
    """
    # First we go through the rows and remember which series name belongs to which id. Only then the groups are
    # built - otherwise the same series would split by whether the record knows the id.
    by_name: dict[str, str] = {}
    for row in rows:
        if not row.get("series_id"):
            continue
        for name in (row.get("series_name"), row.get("i_series_name"),
                     series_from_name(row.get("item_name"))):
            if name:
                by_name.setdefault(str(name).strip().lower(),
                                   str(row["series_id"]))

    # We add the names from the library, not only from the result's rows: a series of which only an orphaned record
    # is in the period would otherwise be missing from the map and would have nothing to be assigned to.
    for series_id, name in _series_names().items():
        by_name.setdefault(name.strip().lower(), series_id)

    # And the episode names. It solves the case where a record carries only the episode's name ("The Bells") and
    # nothing leads to the item any more - the overview would otherwise make a "movie" of its own of it beside the
    # series it belongs to.
    by_name.update(_episodes_by_name())

    groups: dict[str, dict[str, Any]] = {}

    for row in rows:
        if copies_apart and row.get("main_id"):
            row = {**row, "main_id": None}
        key, label, is_series = title_key(row, by_name)

        if kind == KIND_MOVIE and is_series:
            continue
        if kind == KIND_SERIES and not is_series:
            continue

        group = groups.get(key)
        if group is None:
            group = {"group_key": key, "label": label, "is_series": is_series,
                     "_representatives": [], "_strongest": (-1.0, {})}
            for target in values:
                group[target] = 0.0
            groups[key] = group
        elif is_series and len(label) < len(group["label"]):
            # The shorter label is the more general one ("Blue" vs. "Blue - s01e17 - Calypso"), and we speak of a
            # series by its name.
            group["label"] = label

        weight = float(row.get(next(iter(values.values()))) or 0)
        for target, source in values.items():
            group[target] += float(row.get(source) or 0)
        if weight > group["_strongest"][0]:
            group["_strongest"] = (weight, row)

        # A representative for the link. An **archived** item is taken too, i.e. one that was in the library and is
        # no longer in Jellyfin: the watching really happened, the detail opens and shows the title is in the
        # archive. A live one comes first, hence the first number (0/1).
        #
        # Without the archive a movie you once watched and have deleted since would fall among the "unsorted" and
        # `hide_unsorted` would throw it out - a valid statistic would vanish, not waste.
        if row.get("existing_id"):
            group["_representatives"].append(
                (1 if row.get("is_missing") else 0,
                 -float(row.get(next(iter(values.values()))) or 0),
                 # A movie's copy links to the main copy - that one represents the record.
                 row.get("main_id") or row["existing_id"], row.get("series_id")))

    order_key = next(iter(values))
    ordered = sorted(groups.values(), key=lambda r: r[order_key], reverse=True)

    # The link is built only here, so an "unsorted" one is known only after it. That is why it is cut to `limit`
    # only at the end - otherwise hidden rows would take room from those that are to show.
    for group in ordered:
        _, strongest = group.pop("_strongest")
        for column in carry:
            group[column] = strongest.get(column)

        representatives = group.pop("_representatives")
        if not representatives:
            # No record of this group points to a live item. But when a series from the library decided the group,
            # we have the link anyway - the key carries its id. It happens with history where nothing leads to the
            # episode's item any more, but the series itself is still in the library.
            key = str(group["group_key"])
            group["detail_url"] = ("/series/" + key[len("series:"):]
                                   if key.startswith("series:") else None)
            # A title nothing at all is left of in the library stays without a link - a link to a 404 error page is
            # worse than none.
            continue
        # Live ones first (0 < 1), within that the most watched (the weight is negative, so a plain ascending sort
        # gives the largest first). The link follows **the group's key**, not the strongest record.
        #
        # This was a reported bug: a series' group had the right name, but the link led to someone else's item. It
        # happens when a record assigned to the series by name falls into the group - it does not carry
        # `series_id` itself (that is why the name was guessed from), so the link came out to its own item. And
        # because the representative is picked by the watched time, it was enough for such a record to be the
        # strongest, and the link aimed somewhere quite different from the label.
        #
        # The key `series:<id>` is meanwhile the most reliable thing we know about the group - the series' id from
        # the library. When we have it, the link is to come from nothing else.
        key = str(group["group_key"])
        if key.startswith("series:"):
            group["detail_url"] = "/series/" + key[len("series:"):]
            continue

        representatives.sort()
        _, _, item, series_id = representatives[0]
        group["detail_url"] = (f"/series/{series_id}" if series_id
                               else f"/item/{item}")

    if not hide_unsorted:
        return ordered[:limit]

    placed = [s for s in ordered if s["detail_url"]]

    # When hiding would leave nothing at all, nothing is hidden. It happens in one situation only, but an important
    # one: the history is imported BEFORE the app connects to Jellyfin. The library is empty then, so no title has
    # a counterpart - and after the import one would stare at an empty ranking and think nothing was imported. As
    # soon as the sync runs, the titles link up and hiding starts making sense by itself.
    if not placed:
        return ordered[:limit]

    hidden = len(ordered) - len(placed)
    result = placed[:limit]
    if result:
        result[0]["_hidden"] = hidden
    return result


def top_items(days: int, limit: int = 10,
              kind: str = KIND_BOTH) -> list[dict[str, Any]]:
    """The most watched titles. Episodes of the same series are summed.

    `kind` is "both" / "movies" / "series" - the same three choices as with watching by day, so that one does not
    have to learn two different controls.

    How a series is told and where the link leads is described by `title_key()` and `_merge_titles()`.
    """
    rows = db.query_all(
        f"""
        SELECT {TITLE_GROUP_COLUMNS},
               SUM(p.watched_seconds) AS seconds,
               COUNT(*)               AS plays
        FROM playback p
   LEFT JOIN items i ON i.id = p.item_id
       WHERE p.started_at >= ? AND p.started_at < ?
       {watching_only("p.")}
         AND p.watched_seconds > 0
    GROUP BY {TITLE_GROUP_KEY}
        """,
        (*_bounds(days),),
    )

    titles = _merge_titles(rows, kind, limit,
                           {"seconds": "seconds", "plays": "plays"},
                           hide_unsorted=True)
    for title in titles:
        title["hours"] = title["seconds"] / 3600.0
        title["plays"] = int(title["plays"])
    return titles
