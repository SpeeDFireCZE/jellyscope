"""The archive: archived items, searching among the existing ones and assigning to another title.

Part of the statistics package (see jellyscope/stats/__init__.py)."""
from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any

from .. import db
from .kinds import by_titles
from .item_detail import _delete_item, file_name, item_thumbnail, item_label

# The logger's name as before the split: it shows in every line of the log.
log = logging.getLogger("jellyscope.stats")


def archived_count(library_id: str | None = None) -> int:
    """How many items are in the archive. Used for the badge at the switch."""
    where = ["is_missing = 1"]
    params: list[Any] = []
    if library_id:
        where.append("library_id = ?")
        params.append(library_id)
    return int(db.query_value(
        f"SELECT COUNT(*) FROM items WHERE {' AND '.join(where)}", tuple(params)
    ))


def only_archived(item_ids: list[str], library_id: str) -> list[str]:
    """Of the chosen ids keeps only items from this library's archive.

    Bulk deleting must not touch a title that is in the library (it would lose its history and the next sync would
    create it anew, empty), nor another library's archive. The order stays, repeated ids once.
    """
    unique = list(dict.fromkeys(i for i in item_ids if i))
    if not unique:
        return []
    placeholders = ", ".join("?" for _ in unique)
    valid = {r["id"] for r in db.query_all(
        f"SELECT id FROM items WHERE id IN ({placeholders})"
        " AND is_missing = 1 AND library_id = ?",
        (*unique, library_id))}
    return [i for i in unique if i in valid]


def delete_from_archive(item_ids: list[str], library_id: str) -> dict[str, int]:
    """Bulk-deletes items from the library's archive together with their history.

    Only what `only_archived` lets through passes. Everything in one transaction: when anything goes wrong, nothing
    is deleted.
    """
    valid = only_archived(item_ids, library_id)
    plays = 0
    with db.connect() as conn:
        for item_id in valid:
            plays += _delete_item(conn, item_id)
    log.info("deleted %s items from the archive (%s playbacks)", len(valid), plays)
    if plays:
        # An open page draws its cards over plays again (the plays' marker in /health).
        db.note_plays_rewritten()
    return {"title_count": len(valid), "playbacks": plays}


# The count of plays per item - all of them, short ones too: when assigning, what matters is how much history moves,
# not how much "counts".
# Music listening does not belong to the library's archive (music is not in `items`, so it would not pair anyway).
# It pairs with items (archived titles) - music pairs with nothing in the sum (by_titles).
_ALL_PLAYS = f"SELECT item_id, COUNT(*) AS plays FROM playback WHERE 1 = 1{by_titles()} GROUP BY item_id"


def archived_items(library_id: str | None, limit: int = 500) -> list[dict[str, Any]]:
    """Archived items one by one (episodes separately) - for bulk assigning.

    In the library list a series is one tile; here assigning goes by files, so every episode is a row. Those with
    the most plays first: it is exactly with them that the assignment matters.
    """
    conditions = ["i.is_missing = 1"]
    params: list[Any] = []
    if library_id:
        conditions.append("i.library_id = ?")
        params.append(library_id)
    params.append(limit)
    rows = db.query_all(
        f"""
        SELECT i.id, i.name, i.type, i.series_name, i.parent_index_number,
               i.index_number, i.production_year, i.path,
               COALESCE(p.plays, 0) AS plays
        FROM items i
        LEFT JOIN ({_ALL_PLAYS}) p ON p.item_id = i.id
        WHERE {' AND '.join(conditions)}
        ORDER BY COALESCE(p.plays, 0) DESC, COALESCE(i.series_name, i.name), i.id
        LIMIT ?
        """,
        tuple(params),
    )
    # Without a poster: the picture of an item from the archive is no longer in Jellyfin.
    for row in rows:
        row["description"] = item_label(row)
        row["file"] = file_name(row["path"])
    return rows


# An episode's number in a search: S06E07, S6E7, S06 (a whole season) or 6x07.
_EPISODE_CODE = re.compile(r"s(\d{1,3})(?:e(\d{1,4}))?|(\d{1,3})x(\d{1,4})", re.IGNORECASE)
# A part of a word of ASCII letters and digits. LIKE finds it regardless of case in SQLite and in PostgreSQL (ILIKE);
# with "Č" or "Š" SQLite would not manage, so the SQL does not filter by them and Python sees to them.
_ASCII_PART = re.compile(r"[A-Za-z0-9]+")
# Punctuation at the edges of a word ("Rookie," "S06E07." "(2021)" "–") is not searched for.
_WORD_EDGE = re.compile(r"^[\W_]+|[\W_]+$")
# How many candidates SQL picks at most before Python goes through them word by word. SQL filters by every word, so
# one gets here only with a very general query ("S01") - and then the search says there are more results. It
# searches while typing, so pulling more rows per key press makes no sense.
CANDIDATE_LIMIT = 1000


def _for_search(text: Any) -> str:
    """A text for comparing: one form of "é" (NFC) and lower case.

    lower(), not casefold(): casefold makes "ss" of "ß", which LIKE in SQL cannot do - Python would then let through
    what the preliminary pick did not pick.
    """
    return unicodedata.normalize("NFC", str(text or "")).lower()


def _episode_mark(season: int, episode: int | None) -> str:
    """S06E07 (or S06) as it tends to be in a file's name - in lower case."""
    return f"s{season:02d}" + ("" if episode is None else f"e{episode:02d}")


def _standalone(part: str, text: str) -> bool:
    """Is the code in the text as a word of its own? "s06" in "show s06e07", not in "bass06"."""
    return re.search(rf"(?<![a-z0-9]){re.escape(part)}(?![0-9])", text) is not None


def _like_anywhere(patterns: list[str], columns: tuple[str, ...] = ("i.name", "i.series_name", "i.path")
                  ) -> tuple[str, list[str]]:
    """The condition "some pattern is in some column" and its parameters."""
    parts = [f"{s} LIKE ?" for _ in patterns for s in columns]
    return "(" + " OR ".join(parts) + ")", [v for v in patterns for _ in columns]


def find_existing(query: str, limit: int = 50) -> dict[str, Any]:
    """Live items for choosing the target - by name, series, path and episode number.

    The path is here on purpose: when Jellyfin identified a title wrongly and created it anew after the fix,
    neither the name nor the id match, but the file is still the same - and the person knows its name.

    It searches by words and the order does not matter: "Rookie S06E07" finds the episode that has "Rookie" in
    the series' name and S06E07 in the season and episode numbers. The whole query as one piece of text would find
    nothing - those two things are side by side in no column, only the label puts them together like that.

    An episode code (S06E07, S06, 6x07) fits the season and episode numbers, or text in the name (the series "4x4
    Garage"). For an item without numbers in the metadata (the movie "4x4", the file "Show 6x07.mkv") it is looked
    for in the path too - as a word of its own, not a part of another.

    Returns `items` (at most `limit`) and `next` - whether more were found than are shown. Who then looks for
    the sixth season knows they are to narrow the query.
    """
    nothing: dict[str, Any] = {"items": [], "next": False}
    query = unicodedata.normalize("NFC", query or "").strip()[:100]
    if len(query) < 2:
        return nothing

    words: list[str] = []
    codes: list[tuple[int, int | None, str]] = []
    for word in query.split():
        word = _WORD_EDGE.sub("", word)
        if not word:
            continue
        code = _EPISODE_CODE.fullmatch(word)
        if code:
            season, episode = (code.group(1), code.group(2)) if code.group(1) else (code.group(3), code.group(4))
            codes.append((int(season), int(episode) if episode else None, word))
        else:
            words.append(word)
    if not words and not codes:
        return nothing

    # A preliminary pick in SQL - a superset of what Python lets through below. Every word must be in the name, the
    # series, the path or the year, so its longest ASCII part is there too. The words join by AND: there are few
    # candidates then and the cap CANDIDATE_LIMIT almost never applies.
    conditions = ["i.is_missing = 0"]
    params: list[Any] = []
    word_columns = ("i.name", "i.series_name", "i.path", "CAST(i.production_year AS TEXT)")
    for word in words:
        parts = _ASCII_PART.findall(word)
        if parts:
            patterns = [f"%{max(parts, key=len)}%"]
        else:
            # Without ASCII (Cyrillic, Greek): SQLite does not fold the case here, so the usual forms are looked for -
            # as written, lower case, Capitalized and UPPER CASE.
            patterns = [f"%{v}%" for v in dict.fromkeys((word, word.lower(), word.capitalize(), word.upper()))]
        sql, part_params = _like_anywhere(patterns, word_columns)
        conditions.append(sql)
        params += part_params
    for season, episode, word in codes:
        numbers = "i.parent_index_number = ?"
        params.append(season)
        if episode is not None:
            # An episode without an episode number in the metadata is still determined by the season.
            numbers += " AND (i.index_number = ? OR i.index_number IS NULL)"
            params.append(episode)
        in_name, in_name_params = _like_anywhere([f"%{word}%"], ("i.name", "i.series_name"))
        without_numbers, without_numbers_params = _like_anywhere([f"%{word}%", f"%{_episode_mark(season, episode)}%"])
        conditions.append(f"(({numbers}) OR {in_name} OR (i.parent_index_number IS NULL AND {without_numbers}))")
        params += in_name_params + without_numbers_params

    # Without numbers (movies) first, the same in SQLite and in PostgreSQL: NULL would sort to the start in one and
    # to the end in the other.
    rows = db.query_all(
        f"""
        SELECT i.id, i.name, i.type, i.series_name, i.parent_index_number,
               i.index_number, i.production_year, i.path,
               i.series_id, i.image_tag, i.series_image_tag,
               l.name AS library_name
        FROM items i
        LEFT JOIN libraries l ON l.id = i.library_id
        WHERE {' AND '.join(conditions)}
        ORDER BY COALESCE(i.series_name, i.name), COALESCE(i.parent_index_number, -1),
                 COALESCE(i.index_number, -1), i.id
        LIMIT ?
        """,
        (*params, CANDIDATE_LIMIT),
    )

    searched = [_for_search(t) for t in words]

    def matches(r: dict[str, Any]) -> bool:
        name = _for_search(f"{r['name'] or ''} {r['series_name'] or ''}")
        haystack = _for_search(" ".join(str(x) for x in (r["name"], r["series_name"], r["path"],
                                                         r["production_year"]) if x is not None))
        if not all(t in haystack for t in searched):
            return False
        for season, episode, word in codes:
            code = _for_search(word)
            if _standalone(code, name):
                continue                    # "4x4 Garage" - the code is part of the name
            if r["parent_index_number"] is None:
                if not (_standalone(code, haystack) or _standalone(_episode_mark(season, episode), haystack)):
                    return False
            elif int(r["parent_index_number"]) != season or (
                    episode is not None and r["index_number"] is not None and int(r["index_number"]) != episode):
                return False
        return True

    found: list[dict[str, Any]] = []
    for r in rows:
        if matches(r):
            found.append(r)
            if len(found) > limit:
                break
    return {
        "items": [{
            "id": r["id"],
            "description": item_label(r),
            "type": r["type"],
            "library": r["library_name"],
            "file": file_name(r["path"]),
            "thumbnail": item_thumbnail(r),
        } for r in found[:limit]],
        # The candidates' cap: beyond it there can be more that were not gone through.
        "next": len(found) > limit or len(rows) >= CANDIDATE_LIMIT,
    }


class AssignmentError(ValueError):
    """The assignment cannot be done - the message is for a person."""


def assign_to_other(old_id: str, new_id: str) -> dict[str, Any]:
    """The history and statistics of an archived item move to another, live one.

    What for: Jellyfin identified a title wrongly (say as "Behind the scenes"), created it anew after the fix - and
    neither the name, tmdb nor id match, so the automatic pairing has nothing to go by. A person knows what belongs
    to what.

    What happens, in one transaction:

    * the plays (those in the bin of forgotten viewers too) get the new id **and with it the name, kind, series and
      library** - the history and the rankings then show the fixed title, not the wrongly identified one,
    * the archived item is deleted with its streams and versions. It described the file as Jellyfin wrongly
      identified it; the live item describes it right.

    Only archived into live: deleting would take a live one from the library where it really is, and an archived
    one as the target would bury the history.
    """
    if not old_id or not new_id or old_id == new_id:
        raise AssignmentError("Choose a different title than the archived one.")
    old = db.query_one("SELECT id, name, series_name, is_missing FROM items WHERE id = ?",
                       (old_id,))
    if old is None or not old["is_missing"]:
        raise AssignmentError("Only an archived item can be assigned.")
    new = db.query_one(
        "SELECT id, name, type, series_name, library_id, parent_index_number,"
        " index_number, production_year, is_missing FROM items WHERE id = ?",
        (new_id,))
    if new is None or new["is_missing"]:
        raise AssignmentError("The target has to be a title that is in the library.")

    from ..db import PLAYBACK_BIN                    # only here: just for the table's name

    with db.connect() as conn:
        moved = 0
        for table in ("playback", PLAYBACK_BIN):
            if not db.columns_of(conn, table):
                continue                   # the bin comes to be only with the first forgetting
            cursor = conn.execute(
                f"UPDATE {table} SET item_id = ?, item_name = ?, item_type = ?,"
                " series_name = ?, library_id = ? WHERE item_id = ?",
                (new["id"], new["name"], new["type"], new["series_name"],
                 new["library_id"], old_id))
            if table == "playback":
                moved = int(cursor.rowcount or 0)
        conn.execute("DELETE FROM item_streams WHERE item_id = ?", (old_id,))
        conn.execute("DELETE FROM item_versions WHERE item_id = ?", (old_id,))
        conn.execute("DELETE FROM items WHERE id = ?", (old_id,))
        conn.commit()

    log.info("archived item %s assigned to %s (%s playbacks)",
             old_id, new_id, moved)
    return {"status": "ok", "z": item_label(dict(old)),
            "to": item_label(dict(new)), "playbacks": moved,
            "new_id": new["id"]}
