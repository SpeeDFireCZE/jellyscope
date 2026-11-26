"""The play history: filters, the page and the API.

Part of the statistics package (see jellyscope/stats/__init__.py)."""
from __future__ import annotations

from collections.abc import Iterable
from datetime import date, timedelta
from typing import Any

from .. import db
from .kinds import KIND_ALL, _kind_condition, without_kinds
from .periods import transcode_condition
from .playback import transcode_description


# What the history can be filtered by. The values are always passed as a parameter - nothing but a question mark
# ever gets from the address into the SQL.
PLAY_METHODS = ("DirectPlay", "DirectStream", "Transcode")


def _safety_bounds(where: list[str], params: list[Any], date_from: str | None,
                   date_to: str | None) -> None:
    """Bounds of `started_at` two days wider than the local day - only for the index."""
    try:
        if date_from:
            params.append((date.fromisoformat(str(date_from)[:10]) - timedelta(days=2)).isoformat())
            where.append("p.started_at >= ?")
        if date_to:
            params.append((date.fromisoformat(str(date_to)[:10]) + timedelta(days=3)).isoformat())
            where.append("p.started_at < ?")
    except (ValueError, OverflowError):
        # A nonsense date, or one two days from the calendar's edge (0001-01-01, 9999-12-31), limits nothing
        # extra - date() decides as before.
        return


def _history_filter(
    user_id: str | None = None,
    search: str | None = None,
    day: str | None = None,
    kind: str = KIND_ALL,
    date_from: str | None = None,
    date_to: str | None = None,
    method: str | None = None,
    client: str | None = None,
    language: str | None = None,
    hidden_kinds: Iterable[str] = (),
    after_id: int | None = None,
) -> tuple[list[str], list[Any]]:
    """Builds the conditions and values for a query into the history.

    One place for the list and for the counter. If each built its conditions itself, adding a filter to one of
    them would be enough - and the paging would start to lie, because the total count no longer matches the list.
    `hidden_kinds` are kinds whose plays the account may not see with the person who played them (access.hidden_kinds).
    `after_id` = only plays recorded after that row (History's bar of new plays).
    """
    where = ["p.watched_seconds > 0"]
    params: list[Any] = []

    if after_id is not None:
        where.append("p.id > ?")
        params.append(after_id)

    if user_id:
        where.append("p.user_id = ?")
        params.append(user_id)

    if search:
        where.append(f"(p.item_name LIKE ? {db.LIKE_ESCAPE} OR p.series_name LIKE ? {db.LIKE_ESCAPE})")
        # "Anywhere inside", with % and _ of the text taken as they are. The text goes into the query as a parameter,
        # it is never pasted into the SQL.
        params.extend([db.like_contains(search)] * 2)

    # One day is a special case of a period - it comes by a link from the table on the Overview. When it is filled
    # in, the period is ignored.
    # Safety bounds in UTC: `date(..., 'localtime')` cannot use an index, so a short period read the whole table.
    # The bounds are two days wider than any zone shift, so they leave nothing out - date() keeps deciding
    # exactly.
    if day:
        where.append("date(p.started_at, 'localtime') = ?")
        params.append(day)
        _safety_bounds(where, params, day, day)
    else:
        _safety_bounds(where, params, date_from, date_to)
        if date_from:
            where.append("date(p.started_at, 'localtime') >= ?")
            params.append(date_from)
        if date_to:
            where.append("date(p.started_at, 'localtime') <= ?")
            params.append(date_to)

    for condition in (_kind_condition(kind, "p."), without_kinds(hidden_kinds, "p.")):
        if condition:
            where.append(condition.replace(" AND ", "", 1))

    if method == "Transcode":
        # Every transcode, by the start of the name as the transcode share counts it (periods.transcode_condition):
        # imported history carries "Transcode (v:h264 a:direct)". Any other value - a variant from the offer
        # (filter_values) - is compared exactly.
        where.append(transcode_condition("p.play_method"))
    elif method:
        where.append("p.play_method = ?")
        params.append(method)

    if client:
        where.append("p.client = ?")
        params.append(client)

    if language:
        # "und" means "not found out" - and an empty value belongs to it too, otherwise the filter would miss most
        # records.
        if language == "und":
            where.append("(p.audio_language IS NULL OR p.audio_language = ''"
                         " OR p.audio_language = 'und')")
        else:
            where.append("p.audio_language = ?")
            params.append(language)

    return where, params


def history(
    limit: int = 50,
    offset: int = 0,
    user_id: str | None = None,
    search: str | None = None,
    day: str | None = None,
    kind: str = KIND_ALL,
    since: str | None = None,
    until: str | None = None,
    method: str | None = None,
    client: str | None = None,
    language: str | None = None,
    hidden_kinds: Iterable[str] = (),
) -> list[dict[str, Any]]:
    """The raw play history, paged and filtered.

    The filters combine: user + kind + period + play method + client + language at once. All are in the address,
    so the result can be sent as a link and what a person set stays after reloading the page.
    """
    where, params = _history_filter(user_id, search, day, kind, since, until,
                                    method, client, language, hidden_kinds)
    params.extend([limit, offset])
    rows = db.query_all(
        f"""
        SELECT p.*,
               -- The dimensions recorded with the play come first: the session
               -- knows what really flowed, whereas `items` describes the file
               -- as we know it from the last sync.
               COALESCE(p.video_height, i.height) AS height,
               COALESCE(p.video_width,  i.width)  AS width,
               -- The codecs of the file. Beside them stands p.video_codec, i.e. what
               -- really flowed to the player - and exactly the difference
               -- between them says what was transcoded.
               i.video_codec AS source_codec,
               i.audio_codec AS source_audio_codec,
               -- Where the row leads: a title from the library to its detail
               -- (/item/), a track to its row on the album page. What knows
               -- neither (deleted before the library was collected, a track
               -- without an album) is text - a link to /item/ would end in 404.
               CASE WHEN i.id IS NULL THEN 0 ELSE 1 END AS in_library,
               hs.id AS music_track_id,
               hs.album_id AS music_album_id,
               hs.artist AS music_artist
        FROM playback p
        LEFT JOIN items i ON i.id = p.item_id
        LEFT JOIN music_track hs ON hs.id = p.item_id
        WHERE {' AND '.join(where)}
        -- p.id decides a tie of times: without it the database (PostgreSQL)
        -- can return equally old records differently every time and when
        -- paging one would show twice and another not at all.
        ORDER BY p.started_at DESC, p.id DESC
        LIMIT ? OFFSET ?
        """,
        tuple(params),
    )
    for row in rows:
        # The same bubble as with "now playing" - an analysis written once serves both places.
        row["recount"] = transcode_description(row)
    return rows


def history_for_api(user_id: str | None = None, kind: str = KIND_ALL,
                    since: str | None = None, batch_size: int = 2000,
                    conditions: Any = (), hidden_kinds: Iterable[str] = ()):
    """The history for the API with a filter: thin rows in batches, the newest first.

    With a filter the API must go through the whole history of the chosen period. `history()` does not fit that:
    it returns p.* with everything and computes the transcode analysis for every row - on 150 000 plays that was
    over 400 MB of memory. Here are only the columns the API returns, and they are read `batch_size` rows at a
    time, so only what the filter lets through stays in memory. The conditions are the same as in `history()`
    (`_history_filter`).

    `conditions` are pairs (a piece of SQL, values) of the API's pre-filter (`api._history_prefilter`) - the
    piece of SQL is always text from the code, the values go as parameters.
    """
    where, params = _history_filter(user_id=user_id, kind=kind, date_from=since, hidden_kinds=hidden_kinds)
    for sql_part, values in conditions:
        where.append(sql_part)
        params.extend(values)
    with db.connect() as conn:
        cursor = conn.execute(
            f"""
            SELECT p.user_name, p.user_id, p.item_name, p.series_name,
                   p.item_type, p.item_id, p.started_at, p.watched_seconds,
                   p.play_method, p.client, p.device_name,
                   p.audio_language, p.subtitle_language
            FROM playback p
            WHERE {' AND '.join(where)}
            ORDER BY p.started_at DESC, p.id DESC
            """,
            tuple(params),
        )
        while True:
            batch = cursor.fetchmany(batch_size)
            if not batch:
                return
            yield [dict(row) for row in batch]


def history_count(
    user_id: str | None = None,
    search: str | None = None,
    day: str | None = None,
    kind: str = KIND_ALL,
    since: str | None = None,
    until: str | None = None,
    method: str | None = None,
    client: str | None = None,
    language: str | None = None,
    hidden_kinds: Iterable[str] = (),
    after_id: int | None = None,
) -> int:
    """How many records match the filter - the paging goes by it. `after_id` = only those recorded after that row."""
    where, params = _history_filter(user_id, search, day, kind, since, until,
                                    method, client, language, hidden_kinds, after_id)
    return int(db.query_value(
        f"""
        SELECT COUNT(*) FROM playback p
        LEFT JOIN items i ON i.id = p.item_id
        WHERE {' AND '.join(where)}
        """,
        tuple(params),
    ))


def history_last_id(
    user_id: str | None = None,
    search: str | None = None,
    day: str | None = None,
    kind: str = KIND_ALL,
    since: str | None = None,
    until: str | None = None,
    method: str | None = None,
    client: str | None = None,
    language: str | None = None,
    hidden_kinds: Iterable[str] = (),
) -> int:
    """The row of the newest record the filter finds (0 = none) - History's bar of new plays counts the plays after
    it. Of the filter's records only: the newest row of the whole table would tell a viewer, who sees only their own
    plays, how much the others play."""
    where, params = _history_filter(user_id, search, day, kind, since, until,
                                    method, client, language, hidden_kinds)
    return int(db.query_value(
        f"SELECT MAX(p.id) FROM playback p WHERE {' AND '.join(where)}", tuple(params), default=0) or 0)


def filter_values() -> dict[str, list[Any]]:
    """Everything one can filter by - by what really is in the history.

    Offering every possible client and language would mean a long list of choices that would find nothing on
    your server anyway.
    """
    clients = [r["client"] for r in db.query_all(
        "SELECT client, COUNT(*) AS n FROM playback"
        " WHERE client IS NOT NULL AND client != ''"
        " GROUP BY client ORDER BY n DESC LIMIT 25") if r["client"]]

    languages = [r["audio_language"] for r in db.query_all(
        "SELECT audio_language, COUNT(*) AS n FROM playback"
        " WHERE audio_language IS NOT NULL AND audio_language != ''"
        " GROUP BY audio_language ORDER BY n DESC LIMIT 25")
                 if r["audio_language"]]

    methods = [r["play_method"] for r in db.query_all(
        "SELECT play_method, COUNT(*) AS n FROM playback"
        " WHERE play_method IS NOT NULL AND play_method != ''"
        " GROUP BY play_method ORDER BY n DESC") if r["play_method"]]
    # "Transcode" stands for every transcode - offered even when the history holds only imported variants.
    if "Transcode" not in methods and any(m.startswith("Transcode") for m in methods):
        methods.append("Transcode")

    return {"clients": clients, "languages": languages, "methods": methods}
