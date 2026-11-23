# -*- coding: utf-8 -*-
r"""Seerr's statistics (stats/seerr.py) over hand-made requests with a known result.

The data (the period Sat 31. 10. - Mon 2. 11. 2026, the app's zone UTC; the Jellyscope viewers Jana `aaaa-1111` and
Petr `bbbb2222`, "Guest" is only in Seerr):

    z1 movie 100  "Movie A"   10-31 20:00  done/available      Jana   available 10-31 23:00  approved 20:30 (seen waiting)
    z2 series 200 "Series B"  11-01 10:00  approved/available  Petr   available 11-01 16:00  approved 11:00 (seen waiting)
    z3 movie 300  "Movie C"   11-01 12:00  declined            Petr   4K, declined 13:00
    z4 movie 400  "Movie D"   11-02 08:00  waiting             Guest  automatic
    z5 series 500 "Series E"  11-02 09:00  approved/partly     Jana   available 2026-01-01 (the series was in the library already)
    z6 movie 600  "Movie F"   11-01 15:00  done/available      Jana   available 11-02 15:00, deleted in Seerr
    z7 movie 700  "Movie G"   10-20 10:00  outside the period

Plays: Jana the movie 100 (11-01, after the request), Jana an episode of the series 200 (11-01 20:00), Petr the movie
600 (10-01, before the request - does not count), Jana an episode of the series 500 with 0 seconds (does not count).

From that:
- "Requested and watches?": available z1, z2, z5, z6 - pairing by the title's id in Jellyfin (for a series the
  series' id, episodes carry no TMDB id), without it by TMDB (z6); z1 was played by the requester, z2 only by someone
  else (Jana, Petr requested), z5 and z6 by nobody;
- from the request to the library: 3 h, 6 h, 24 h (z5 is a continuation) -> the median 6 h, p90 20.4 h;
- from the request to the approval: 30 min and 1 h -> the median 45 min, p90 57 min.
Finally midnight in the app's zone: a request at 10-31 23:30 UTC belongs to Sunday 1. 11. in Prague.

Run:
    .venv/bin/python tests/test_seerr_stats.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "seerr-test.db")
os.environ["SECRET_KEY"] = "test-key"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

from jellyscope import db, stats  # noqa: E402

failures = 0
db.init_db()


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


def set_zone(name: str) -> None:
    db.set_setting("app_timezone", name)
    db.forget_settings()


def add_request(request_id: int, media_type: str, tmdb: str, title: str, created: str, state: int, title_state: int,
                requester_jellyfin_id: str, requester_name: str, **columns) -> None:
    """One Seerr request; `columns` are further columns of the table by their names."""
    row = {"id": request_id, "media_type": media_type, "tmdb_id": tmdb, "title": title, "seasons": 1 if media_type == "tv" else None,
           "is_4k": 0, "auto_request": 0, "status": state, "media_status": title_state, "requester_seerr_id": request_id,
           "requester_name": requester_name, "requester_jellyfin_id": requester_jellyfin_id, "handled_by_name": "admin",
           "created_at": created, "updated_at": created, "available_at": None, "seen_pending": 0, "approved_at": None,
           "declined_at": None, "deleted_at": None, "synced_at": created, **columns}
    with db.connect() as conn:
        conn.execute(f"INSERT INTO seerr_request ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
                     tuple(row.values()))
        conn.commit()


with db.connect() as conn:
    for uid, name in (("aaaa-1111", "Jana"), ("bbbb2222", "Petr")):
        conn.execute("INSERT INTO users (id, name) VALUES (?, ?)", (uid, name))
    # Episodes have no TMDB id (Jellyfin 12 does not send SeriesProviderIds with episodes - found out for real): a series
    # pairs by the series' id in Jellyfin (Seerr: media.jellyfinMediaId = items.series_id).
    for item_id, item_type, tmdb, name, series, series_id in (("m100", "Movie", "100", "Movie A", None, None),
                                                              ("e200a", "Episode", None, "Pilot", "Series B", "s200"),
                                                              ("e200b", "Episode", None, "Second", "Series B", "s200"),
                                                              ("m600", "Movie", "600", "Movie F", None, None),
                                                              ("e500a", "Episode", None, "First", "Series E", "s500")):
        conn.execute("INSERT INTO items (id, name, type, tmdb_id, series_name, series_id, synced_at)"
                     " VALUES (?, ?, ?, ?, ?, ?, ?)", (item_id, name, item_type, tmdb, series, series_id, db.utcnow()))
    for i, (uid, item_id, when, seconds) in enumerate((("aaaa-1111", "m100", "2026-11-01 10:00:00", 3000),
                                                       ("aaaa1111", "e200a", "2026-11-01 20:00:00", 1500),
                                                       ("bbbb2222", "m600", "2026-10-01 10:00:00", 4000),
                                                       ("aaaa-1111", "e500a", "2026-11-02 10:00:00", 0))):
        conn.execute("INSERT INTO playback (session_key, user_id, item_id, started_at, last_seen_at, watched_seconds)"
                     " VALUES (?, ?, ?, ?, ?, ?)", (f"s{i}", uid, item_id, when, when, seconds))
    conn.commit()

set_zone("UTC")
add_request(1, "movie", "100", "Movie A", "2026-10-31 20:00:00", 5, 5, "aaaa1111", "jana", jellyfin_id="m100",
            available_at="2026-10-31 23:00:00", seen_pending=1, approved_at="2026-10-31 20:30:00")
add_request(2, "tv", "200", "Series B", "2026-11-01 10:00:00", 2, 5, "bbbb2222", "petr", jellyfin_id="s200",
            available_at="2026-11-01 16:00:00", seen_pending=1, approved_at="2026-11-01 11:00:00")
add_request(3, "movie", "300", "Movie C", "2026-11-01 12:00:00", 3, 1, "bbbb2222", "petr", is_4k=1,
            seen_pending=1, declined_at="2026-11-01 13:00:00")
add_request(4, "movie", "400", "Movie D", "2026-11-02 08:00:00", 1, 2, "", "Guest", auto_request=1, seen_pending=1)
add_request(5, "tv", "500", "Series E", "2026-11-02 09:00:00", 2, 4, "aaaa1111", "jana", jellyfin_id="s500",
            available_at="2026-01-01 00:00:00")
add_request(6, "movie", "600", "Movie F", "2026-11-01 15:00:00", 5, 5, "aaaa1111", "jana",
            available_at="2026-11-02 15:00:00", deleted_at="2026-11-02 20:00:00")
add_request(7, "movie", "700", "Movie G", "2026-10-20 10:00:00", 5, 5, "aaaa1111", "jana", available_at="2026-10-20 12:00:00")

PERIOD = stats.period_between("2026-10-31", "2026-11-02")

print("--- When and how many ---")
check(stats.seerr_summary(PERIOD) == {"count": 6, "movies": 4, "series_list": 2, "four_k": 1, "automatic": 1},
      f"summary ({stats.seerr_summary(PERIOD)})")
by_day = [(r["day"], r["movies"], r["series_list"]) for r in stats.seerr_by_day(PERIOD)]
check(by_day == [("2026-10-31", 1, 0), ("2026-11-01", 2, 1), ("2026-11-02", 1, 1)], f"by day ({by_day})")
h = stats.seerr_heatmap(PERIOD)
check(len(h) == 7 and all(len(r) == 24 for r in h) and h[5][20] == h[6][10] == h[6][12] == h[6][15] == h[0][8]
      == h[0][9] == 1 and sum(map(sum, h)) == 6, "heatmap: Sat 20, Sun 10/12/15, Mon 8/9 h (row 0 = Monday)")

print()
print("--- Who requests ---")
requesters = [(z["user_name"], z["user_id"], z["count"], z["approved"], z["declined"], z["available"], z["watched_it_self"])
              for z in stats.seerr_requesters(PERIOD)]
check(requesters == [("Jana", "aaaa-1111", 3, 3, 0, 3, 1), ("Petr", "bbbb2222", 2, 1, 1, 1, 0), ("Guest", "", 1, 0, 0, 0, 0)],
      f"the ranking: the viewer's name and id from Jellyscope, otherwise the name from Seerr ({requesters})")

print()
print("--- Handling ---")
v = stats.seerr_handling(PERIOD)
check(v["states"] == {"deleted": 1, "declined": 1, "failed": 0, "waiting": 1, "available": 3, "approved": 0, "removed": 0},
      f"states, every request in one group ({v['states']})")
check(v["to_library"] == {"median": 21600, "p90": 73440, "count": 3, "continuation": 1},
      f"from the request to the library; a series' continuation separately ({v['to_library']})")
check(v["to_approve"] == {"median": 2700, "p90": 3420, "count": 2}, f"from the request to the approval ({v['to_approve']})")
empty = stats.seerr_handling(stats.period_between("2026-01-05", "2026-01-06"))
check(empty["to_library"]["median"] is None and empty["to_approve"]["count"] == 0, "an empty period: None, not zero")

print()
print("--- Requested and watches? ---")
d = stats.seerr_watching(PERIOD)
check((d["available_count"], d["requester"], d["other"], d["nobody"]) == (4, 1, 1, 2),
      f"4 available: the requester played 1, only someone else 1, nobody 2 ({d['available_count']}, {d['requester']}, {d['other']}, {d['nobody']})")
check([(x["title"], x["user_name"]) for x in d["nobody_list"]] == [("Series E", "Jana"), ("Movie F", "Jana")],
      f"\"Nobody played\" by when it was added to the library ({d['nobody_list']})")
check(stats.seerr_first() == "2026-10-20 10:00:00", "the first request (the start of the whole history)")
u = stats.seerr_events(PERIOD)
check(stats.seerr_summary(PERIOD, analysis=u) == stats.seerr_summary(PERIOD) and stats.seerr_watching(PERIOD, analysis=u) == d,
      "the cards can be computed from one pick (analysis=)")

print()
print("--- The latest requests and posters from the library (cards with posters, 13. 11. 2026) ---")
latest = stats.seerr_latest(PERIOD)
check([(z["request_id"], z["group"]) for z in latest]
      == [(5, "available"), (4, "waiting"), (6, "deleted"), (3, "declined"), (2, "available"), (1, "available")],
      f"from the newest, with how each ended ({[(z['request_id'], z['group']) for z in latest]})")
check([z["request_id"] for z in stats.seerr_latest(PERIOD, limit=2)] == [5, 4] and stats.seerr_latest(PERIOD, analysis=u) == latest,
      "the limit takes the newest; it can be computed from the same pick")
posters = {z["request_id"]: (z["poster_id"], z["poster_tag"]) for z in latest}
check(posters == {1: ("m100", ""), 2: ("s200", ""), 5: ("s500", ""), 6: ("m600", ""), 3: ("", ""), 4: ("", "")},
      f"the poster: a movie by id or TMDB id, a series by the series' id, outside the library nothing ({posters})")
check(all("id" not in z and {"media_type", "tmdb_id", "title", "created_at", "is_4k", "user_id", "user_name"} <= set(z) for z in latest)
      and next(z for z in latest if z["request_id"] == 3)["is_4k"] is True,
      "the rows carry what the card draws; the request's id as zadost_id")
check([(x["media_type"], x["tmdb_id"], x["jellyfin_id"]) for x in d["nobody_list"]] == [("tv", "500", "s500"), ("movie", "600", "")]
      and all("poster_id" not in x for x in d["nobody_list"]),
      "\"Nobody played\" carries the type, the TMDB id and the id from Jellyfin; the poster is looked up only for the rows that are drawn")
check([(x["title"], x["poster_id"]) for x in stats.seerr_with_posters(d["nobody_list"])] == [("Series E", "s500"), ("Movie F", "m600")]
      and all("poster_id" not in x for x in d["nobody_list"]), "seerr_with_posters adds the poster to copies of the rows")
with db.connect() as conn:
    conn.execute("UPDATE items SET image_tag = 'tag-a' WHERE id = 'm100'")
    conn.execute("UPDATE items SET series_image_tag = 'tag-b' WHERE series_id = 's200'")
    conn.execute("UPDATE items SET is_missing = 1 WHERE id = 'm600'")
    conn.commit()
found = stats.seerr_posters([("movie", "m100", "100"), ("tv", "s200", "200"), ("movie", "", "600"), ("tv", "", "200"),
                             ("movie", "not-here", "100"), ("tv", "", "999")])
# ("tv", "", "200"): a series without an id of its own (a 4K request, until there is a 4K copy) is found via another
# request for the same title (request 2 has the series' id) - not by the episodes' TMDB id, that does not pair.
check(found == {("movie", "m100", "100"): ("m100", "tag-a"), ("tv", "s200", "200"): ("s200", "tag-b"),
                ("movie", "not-here", "100"): ("m100", "tag-a"), ("tv", "", "200"): ("s200", "tag-b")},
      f"seerr_posters: the picture's tag, not an item gone from Jellyfin, a series without an id via another request ({found})")
with db.connect() as conn:
    conn.execute("UPDATE items SET image_tag = NULL WHERE id = 'm100'")
    conn.execute("UPDATE items SET series_image_tag = NULL WHERE series_id = 's200'")
    conn.execute("UPDATE items SET is_missing = 0 WHERE id = 'm600'")
    conn.commit()

print()
print("--- Findings of the final review (a period of its own, 1.-3. 12. 2026) ---")
# z20: the movie's id stored in the library with dashes, from Seerr without them - it pairs (without functions in SQL).
# z21: Seerr keeps an old id (the title was added anew in Jellyfin) - the TMDB id saves it.
# z22: a declined request whose title was added through someone else's request - it is not "handled into the library".
# z23: a done request whose title disappeared from the library (title state 7) - a group of its own, not "not available
#      yet".
# z24, z25: two requesters only from Seerr with the same name - two rows, not one.
with db.connect() as conn:
    conn.execute("INSERT INTO items (id, name, type, tmdb_id, synced_at) VALUES"
                 " ('01234567-89ab-cdef-0123-456789abcdef', 'Movie S', 'Movie', '2000', ?)", (db.utcnow(),))
    conn.execute("INSERT INTO items (id, name, type, tmdb_id, synced_at) VALUES ('m2100', 'Movie T', 'Movie', '2100', ?)",
                 (db.utcnow(),))
    for i, (uid, item_id) in enumerate((("aaaa-1111", "01234567-89ab-cdef-0123-456789abcdef"), ("bbbb2222", "m2100"))):
        conn.execute("INSERT INTO playback (session_key, user_id, item_id, started_at, last_seen_at, watched_seconds)"
                     " VALUES (?, ?, ?, '2026-12-02 20:00:00', '2026-12-02 20:00:00', 900)", (f"r{i}", uid, item_id))
    conn.commit()
add_request(20, "movie", "2000", "Movie S", "2026-12-01 10:00:00", 5, 5, "aaaa1111", "jana",
            jellyfin_id="0123456789abcdef0123456789abcdef", available_at="2026-12-01 12:00:00")
add_request(21, "movie", "2100", "Movie T", "2026-12-01 11:00:00", 5, 5, "aaaa1111", "jana",
            jellyfin_id="ffffffffffffffffffffffffffffffff", available_at="2026-12-01 15:00:00")
add_request(22, "movie", "2000", "Movie S", "2026-12-01 12:00:00", 3, 5, "aaaa1111", "jana",
            jellyfin_id="0123456789abcdef0123456789abcdef", available_at="2026-12-01 12:00:00")
add_request(23, "movie", "2300", "Movie U", "2026-12-01 13:00:00", 5, 7, "aaaa1111", "jana")
# z26: a series without an id in Jellyfin, the library has an episode of SOMEONE ELSE'S series with the same number as
# its own TMDB id (the episode's id) and someone watched it - it must not count as watching the requested series.
with db.connect() as conn:
    conn.execute("INSERT INTO items (id, name, type, tmdb_id, series_name, series_id, synced_at) VALUES"
                 " ('e-other', 'Episode', 'Episode', '2600', 'Other series', 's-other', ?)", (db.utcnow(),))
    conn.execute("INSERT INTO playback (session_key, user_id, item_id, started_at, last_seen_at, watched_seconds)"
                 " VALUES ('r9', 'bbbb2222', 'e-other', '2026-12-02 21:00:00', '2026-12-02 21:00:00', 900)")
    conn.commit()
add_request(26, "tv", "2600", "Series X", "2026-12-01 14:00:00", 5, 5, "aaaa1111", "jana")
add_request(24, "movie", "2400", "Movie V", "2026-12-02 09:00:00", 1, 2, "", "Guest")
add_request(25, "movie", "2500", "Movie W", "2026-12-02 10:00:00", 1, 2, "", "Guest")
DECEMBER = stats.period_between("2026-12-01", "2026-12-03")
sql_queries: list[str] = []
original_query_all = db.query_all
db.query_all = lambda sql, *a, **k: (sql_queries.append(sql), original_query_all(sql, *a, **k))[1]
d = stats.seerr_watching(DECEMBER)
db.query_all = original_query_all
check(not [q for q in sql_queries if "REPLACE(" in q.upper() or "LOWER(" in q.upper()],
      "pairing with the library wraps no column in a function (it goes through the index)")
check((d["available_count"], d["requester"], d["other"], d["nobody"]) == (3, 1, 1, 1),
      f"an id with dashes pairs (the requester), the movie's old id is saved by TMDB (only someone else), a declined one does "
      f"not count, a series does not pair by an episode's TMDB id (nobody) ({d['available_count']}, {d['requester']}, {d['other']}, {d['nobody']})")
v = stats.seerr_handling(DECEMBER)
check(v["states"] == {"deleted": 0, "declined": 1, "failed": 0, "waiting": 2, "available": 3, "approved": 0, "removed": 1},
      f"a title that disappeared from the library has a group of its own ({v['states']})")
check(v["to_library"]["count"] == 2, f"a declined request does not count into \"from the request to the library\" ({v['to_library']})")
requesters = [(z["user_name"], z["count"], z["approved"], z["declined"], z["available"], z["watched_it_self"])
              for z in stats.seerr_requesters(DECEMBER)]
check(requesters == [("Jana", 5, 4, 1, 3, 1), ("Guest", 1, 0, 0, 0, 0), ("Guest", 1, 0, 0, 0, 0)],
      f"a declined one is not \"in the library\"; two requesters of the same name from Seerr are two rows ({requesters})")

print()
print("--- Deferred minors of the review (13. 11. 2026) ---")
# Posters were looked up for all the period's requests on every view, although only the rows of two cards draw them
# (at most a hundred each).
sys.path.insert(0, str(PROJECT / "tests"))
from _package import substitute  # noqa: E402

looked_up: list[int] = []
original_posters = stats.seerr_posters


def _count(titles):
    titles = set(titles)
    looked_up.append(len(titles))
    return original_posters(titles)


restore = substitute(stats, "seerr_posters", _count)
u = stats.seerr_events(DECEMBER)
check(len(u["requests"]) == 7 and looked_up == [] and all("poster_id" not in z for z in u["requests"]),
      f"picking the period's requests looks up no posters ({looked_up})")
two = stats.seerr_latest(DECEMBER, limit=2, analysis=u)
check(looked_up == [2] and all("poster_id" in z for z in two), f"the latest requests look up a poster only for the rows they return ({looked_up})")
looked_up.clear()
nobody = stats.seerr_watching(DECEMBER, analysis=u)["nobody_list"]
check(looked_up == [] and len(nobody) == 1, f"\"Requested and watches?\" looks up no posters ({looked_up})")
restore()

# A request for the 4K version carries the 4K copy's id - empty until there is a 4K copy. A series does not pair by the
# TMDB id, so such a request kept a provisional poster (the hourly cleanup deleted it, the next view downloaded it again)
# and had no link, although the regular copy of the series is in the library.
add_request(30, "tv", "200", "Series B", "2026-12-02 12:00:00", 2, 3, "bbbb2222", "petr", is_4k=1)
r30 = next(z for z in stats.seerr_latest(DECEMBER) if z["request_id"] == 30)
check((r30["poster_id"], r30["is_4k"]) == ("s200", True), f"a request for the 4K version of a series in the library has its poster ({r30['poster_id']!r})")
check(stats.seerr_posters([("tv", "", "2600")]) == {}, "a series that has no id in any request is still not found (not by the episodes' TMDB id)")
add_request(31, "tv", "3100", "Series Y", "2026-12-02 13:00:00", 5, 5, "bbbb2222", "petr", jellyfin_id="no-longer-in-library")
add_request(32, "tv", "3100", "Series Y", "2026-12-02 14:00:00", 2, 3, "bbbb2222", "petr", is_4k=1)
check(stats.seerr_posters([("tv", "", "3100"), ("tv", "no-longer-in-library", "3100")]) == {},
      "another request has the id of a series that is not in the library: nothing")
with db.connect() as conn:      # a movie in the library without a TMDB id; a 4K request finds it via the regular request
    conn.execute("INSERT INTO items (id, name, type, synced_at) VALUES ('m3300', 'Movie Z', 'Movie', ?)", (db.utcnow(),))
    conn.commit()
add_request(33, "movie", "3300", "Movie Z", "2026-12-02 15:00:00", 5, 5, "bbbb2222", "petr", jellyfin_id="m3300")
check(stats.seerr_posters([("movie", "", "3300")]) == {("movie", "", "3300"): ("m3300", "")},
      "a movie without a TMDB id in the library: a request without an id finds it via another request for the same title")

# Code review (14. 11. 2026): only the posters had the fallback via another request. A request without an id of its own
# thus had a poster and a link into the library, but "Requested and watches?" did not find its plays and the title ended
# in "Nobody played", although the requester watched it.
with db.connect() as conn:
    conn.execute("INSERT INTO items (id, name, type, series_name, series_id, synced_at) VALUES"
                 " ('e4000', 'Pilot', 'Episode', 'Series Q', 's4000', ?)", (db.utcnow(),))
    conn.execute("INSERT INTO playback (session_key, user_id, item_id, started_at, last_seen_at, watched_seconds)"
                 " VALUES ('q1', 'aaaa-1111', 'e4000', '2027-01-03 20:00:00', '2027-01-03 20:00:00', 1200)")
    conn.commit()
add_request(40, "tv", "4000", "Series Q", "2027-01-02 10:00:00", 5, 5, "bbbb2222", "petr", jellyfin_id="s4000",
            available_at="2027-01-02 12:00:00")
add_request(41, "tv", "4000", "Series Q", "2027-01-02 11:00:00", 5, 5, "aaaa1111", "jana", is_4k=1, available_at="2027-01-02 12:00:00")
JANUARY = stats.period_between("2027-01-01", "2027-01-05")
watching_q = {z["id"]: z["watching"] for z in stats.seerr_events(JANUARY)["requests"]}
check(watching_q == {40: "other", 41: "requester"},
      f"a request without an id of its own finds its plays via another request for the same title ({watching_q})")


# Code review: the fallback asked the library again even for an id that was not found a moment ago (request 31 has an id
# that is not in the library, and another request for the same title carries the same id).
def library_queries(titles: list) -> int:
    sql_queries.clear()
    db.query_all = lambda sql, *a, **k: (sql_queries.append(sql), original_query_all(sql, *a, **k))[1]
    try:
        stats.seerr_posters(titles)
    finally:
        db.query_all = original_query_all
    return sum("FROM items" in q for q in sql_queries)


check(library_queries([("tv", "no-longer-in-library", "3100")]) == library_queries([("tv", "nowhere", "9999")]),
      "an id just not found in the library is not looked up a second time via another request")

print()
print("--- Midnight in the app's zone ---")
add_request(8, "movie", "800", "Movie H", "2026-10-31 23:30:00", 1, 2, "", "Guest")
set_zone("Europe/Prague")
PERIOD = stats.period_between("2026-10-31", "2026-11-02")
by_day = {r["day"]: r["movies"] for r in stats.seerr_by_day(PERIOD)}
check(by_day.get("2026-11-01") == 3 and by_day.get("2026-10-31") == 1, f"a request at 23:30 UTC is 1. 11. in Prague ({by_day})")
h = stats.seerr_heatmap(PERIOD)
check(h[6][0] == 1 and h[5][21] == 1, "the heatmap in the app's zone: Sunday 0 h, Saturday 21 h")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
