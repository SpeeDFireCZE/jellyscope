# -*- coding: utf-8 -*-
r"""Provisional posters of requests: a title that is not in Jellyfin takes its poster from Radarr or
Sonarr, else from Seerr - and never from anywhere else.

Jellyscope never connects anywhere outside on its own for posters: it asks only the apps it has
connected. Radarr and Sonarr have the poster of every title that reached them (approved requests);
pending and declined requests are known only to Seerr, and it serves images only with "Enable Image
Caching" on. Guarded:

A) the sync stores `tvdb_id` (the search in Sonarr) and the poster path from Seerr (`poster`) - from
   the same answer as the name, and only where needed;
B) the route /seerr/poster/<type>/<tmdb id>: invalid input and an unknown title end without a single
   request outside;
C) the order of sources: the disk, Radarr/Sonarr, Seerr; the first find is stored as one file and
   the next time nobody is asked;
D) nothing and bad answers (HTML instead of an image, a huge body, a redirect, an unavailable app):
   404, nothing is stored, for five minutes nobody is asked again;
E) the poster path from the database must have the shape /<name>.<jpg|png|webp>;
F) permissions: without the Seerr area nobody gets a poster;
G) Jellyfin comes first: once the title arrives in the library the card switches to its poster and
   the provisional file is deleted; if the title leaves the library, the provisional one returns;
H) a fresh pending request (not in the database yet) has its poster path from the live card's
   memory; deleting the requests deletes the provisional posters too;
I) "Enable Image Caching": Jellyscope reads the state from Seerr (on a sync, a connection test and
   saving), after it is switched on posters are filled in for requests stored earlier too, and the
   link to Seerr's settings gives no password away.

The apps do not run: the clients talk to httpx.MockTransport handlers that note every request as
(app, path).

Spec: docs/superpowers/specs/2026-11-13-karty-arr-seerr-design.md

Run:
    .venv/bin/python tests/test_seerr_posters.py
"""
from __future__ import annotations

import asyncio
import html as html_lib
import os
import sys
import tempfile
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "seerr-posters.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

import httpx  # noqa: E402

from _apps import (  # noqa: E402
    RADARR_RESPONSES, SEERR_RESPONSES, SONARR_RESPONSES, no_network, card_html, recorded_response, login, connect_radarr, connect_seerr,
    connect_sonarr, radarr_client, seerr_client, sonarr_client,
)
from _package import substitute  # noqa: E402
from jellyscope import access, accounts, db, images, seerr, cards_seerr, stats  # noqa: E402
from jellyscope.arr import radarr, sonarr  # noqa: E402

failures = 0
db.init_db()
db.set_setting("ui_language", "cs")  # the checks read Czech (English is the default)


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


JPEG = b"\xff\xd8\xff\xe0" + b"jellyscope-test" * 8
REQUESTS = recorded_response(SEERR_RESPONSES, "request")["results"]


def recorded_request(number: int) -> dict:
    return next(r for r in REQUESTS if r["id"] == number)


# Titles of the recorded requests (see tests/seerr_responses/request.json):
IN_LIBRARY = 603        # request 1: a movie that is in the Jellyscope library
APPROVED = 27205        # request 3: an approved movie - Radarr knows it
IN_RADARR = 329865      # request 6: another approved movie in Radarr
SERIES = 63639          # request 5: an approved series - Sonarr knows it (by its TVDB id)
SERIES_TVDB = recorded_request(5)["media"]["tvdbId"]
PENDING = 872585        # request 10: pending - Radarr does not know it, only Seerr has a poster
NO_POSTER = 496243      # request 12: pending and Seerr has no poster for it
DECLINED = 95396        # request 14: a declined series - Sonarr does not know it
FRESH = 111111          # a pending request that is not in the database yet

MOVIE_ID = recorded_request(1)["media"]["jellyfinMediaId"]
with db.connect() as conn:
    conn.execute("INSERT INTO items (id, name, type, tmdb_id, image_tag, date_created, synced_at)"
                 " VALUES (?, 'The Matrix', 'Movie', '603', 'otisk1', ?, ?)", (MOVIE_ID, db.utcnow(), db.utcnow()))
    conn.commit()

# ---------------------------------------------------------------------------
# The fake apps. Every request is noted as (app, path).
# ---------------------------------------------------------------------------
requests_seen: list[tuple[str, str]] = []
api_requests: list[str] = []           # only the paths of Seerr's API (movie/603, tv/70523, …)
params: dict[tuple[str, str], dict] = {}      # the parameters of the last request for (app, path)
# What Seerr knows about a title: the poster path (None = it has none). /p<id>.jpg by default.
title_poster: dict[int, str | None] = {NO_POSTER: None}
seerr_state = {"images": True, "body": JPEG, "code": 200, "headers": {}, "title_errors": set(), "fresh": False,
               "down": False}
# Seerr's public settings (settings/public): `cacheImages` = "Enable Image Caching".
public: dict[str, object] = dict(recorded_response(SEERR_RESPONSES, "settings_public"))
radarr_state = {"movies": {APPROVED: 6, IN_RADARR: 8}, "ignores_filter": False, "body": JPEG, "down": False}
sonarr_state = {"series": {SERIES_TVDB: 4}}


def fake_seerr(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    requests_seen.append(("seerr", path))
    if seerr_state["down"]:
        raise httpx.ConnectError("Connection refused", request=request)
    if path.startswith("/imageproxy/"):
        if not seerr_state["images"]:
            return httpx.Response(404, json={"message": "Image caching is disabled."})
        return httpx.Response(seerr_state["code"], content=seerr_state["body"],
                              headers={"Content-Type": "image/jpg", **seerr_state["headers"]})
    api = path.split("/api/v1/", 1)[-1]
    api_requests.append(api)
    if api == "request":
        if request.url.params.get("filter") == "pending":
            pending = recorded_response(SEERR_RESPONSES, "request_pending")
            results = list(pending["results"])
            if seerr_state["fresh"]:
                results.append({**results[0], "id": 99, "media": {**results[0]["media"], "tmdbId": FRESH}})
            return httpx.Response(200, json={**pending, "results": results})
        return httpx.Response(200, json=recorded_response(SEERR_RESPONSES, "request"))
    if api.startswith(("movie/", "tv/")):
        number = int(api.split("/")[1])
        if number in seerr_state["title_errors"]:
            return httpx.Response(500, text="TMDB je dole")
        return httpx.Response(200, json={"title": f"Film {number}", "name": f"Seriál {number}",
                                         "posterPath": title_poster.get(number, f"/p{number}.jpg")})
    if api == "settings/about":
        return httpx.Response(200, json=recorded_response(SEERR_RESPONSES, "settings_about"))
    if api == "settings/public":
        return httpx.Response(200, json=public)
    return httpx.Response(404)


def fake_radarr(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    requests_seen.append(("radarr", path))
    params[("radarr", path)] = dict(request.url.params)
    if radarr_state["down"]:
        raise httpx.ConnectError("Connection refused", request=request)
    if path == "/api/v3/movie":
        template = recorded_response(RADARR_RESPONSES, "movie_tmdb")[0]
        every = [{**template, "id": number, "tmdbId": tmdb} for tmdb, number in radarr_state["movies"].items()]
        if radarr_state["ignores_filter"]:
            return httpx.Response(200, json=every)      # an old version: it does not know tmdbId and sends everything
        return httpx.Response(200, json=[m for m in every if str(m["tmdbId"]) == request.url.params.get("tmdbId")])
    if path.startswith("/api/v3/mediacover/") and path.endswith("/poster-250.jpg"):
        return httpx.Response(200, content=radarr_state["body"], headers={"Content-Type": "image/jpeg"})
    return httpx.Response(404)


def fake_sonarr(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    requests_seen.append(("sonarr", path))
    params[("sonarr", path)] = dict(request.url.params)
    if path == "/api/v3/series":
        template = recorded_response(SONARR_RESPONSES, "series_tvdb")[0]
        return httpx.Response(200, json=[{**template, "id": number, "tvdbId": tvdb} for tvdb, number in sonarr_state["series"].items()
                                         if str(tvdb) == request.url.params.get("tvdbId")])
    if path.startswith("/api/v3/mediacover/") and path.endswith("/poster-250.jpg"):
        return httpx.Response(200, content=JPEG, headers={"Content-Type": "image/jpeg"})
    return httpx.Response(404)


def hosts() -> set[str]:
    return {app for app, _ in requests_seen}


def row(number: int) -> dict:
    return db.query_one("SELECT * FROM seerr_request WHERE id = ?", (number,)) or {}


def card_row(tmdb: int) -> dict:
    """A request's row as the card "Poslední žádosti" (latest requests) gets it."""
    return next(r for r in stats.seerr_latest(3650) if r["tmdb_id"] == str(tmdb))


def synchronize() -> None:
    asyncio.run(seerr.synchronize())


no_network()
substitute(radarr, "RadarrClient", radarr_client(fake_radarr))
substitute(sonarr, "SonarrClient", sonarr_client(fake_sonarr))
substitute(seerr, "SeerrClient", seerr_client(fake_seerr))
connect_radarr()
connect_sonarr()
connect_seerr()
accounts.create("admin", "longpassword", is_admin=True)
accounts.create("reader", "longpassword")
reader_account = accounts.get_by_name("reader")

print("--- A) sync: tvdb_id and the poster path ---")
title_poster[IN_RADARR] = "/../zle.jpg"       # Seerr sends a path that is no poster path
seerr_state["title_errors"] = {DECLINED}      # and the look-up fails for one title
synchronize()
check(row(1)["tvdb_id"] == "" and row(2)["tvdb_id"] == str(recorded_request(2)["media"]["tvdbId"]) and row(5)["tvdb_id"] == str(SERIES_TVDB),
      f"the series' tvdb_id is stored, a movie has none ({row(5).get('tvdb_id')!r})")
check(row(10)["poster"] == f"/p{PENDING}.jpg" and row(3)["poster"] == f"/p{APPROVED}.jpg" and row(10)["title"] == f"Film {PENDING}",
      f"the poster path from the same answer as the name ({row(10).get('poster')!r})")
check(row(12)["poster"] == "-" and row(12)["title"] == f"Film {NO_POSTER}", "Seerr has no poster: a dash (not asked next time), the name yes")
check(row(6)["poster"] == "-", "a path that is no poster path is not stored")
check(row(1)["poster"] == "" and row(1)["title"] == "The Matrix" and f"movie/{IN_LIBRARY}" not in api_requests,
      "a title from the library: the name from the library, the poster from Jellyfin - Seerr is not asked")
check(row(14)["poster"] == "" and row(14)["title"] == "", "a failed look-up leaves both empty")
api_requests.clear()
seerr_state["title_errors"] = set()
with db.connect() as conn:      # a request stored before the poster path was stored: it has a name, no poster
    conn.execute("UPDATE seerr_request SET poster = '' WHERE id = 3")
    conn.commit()
synchronize()
titles = sorted(r for r in api_requests if r.startswith(("movie/", "tv/")))
check(titles == [f"movie/{APPROVED}", f"tv/{DECLINED}"],
      f"the next sync looks up only what is missing: the poster of a request with a name and the title that failed last time ({titles})")
check(row(3)["poster"] == f"/p{APPROVED}.jpg" and row(3)["title"] == f"Film {APPROVED}" and row(14)["poster"] == f"/p{DECLINED}.jpg",
      "the poster is filled in, the name is not overwritten")
api_requests.clear()
synchronize()
check(not [r for r in api_requests if r.startswith(("movie/", "tv/"))] and row(10)["poster"] == f"/p{PENDING}.jpg" and row(12)["poster"] == "-",
      "the third sync does not ask about titles and leaves the stored paths")
check(seerr.poster_path("movie", str(PENDING)) == f"/p{PENDING}.jpg" and seerr.poster_path("movie", str(NO_POSTER)) == ""
      and seerr.poster_path("movie", "999999") == "", "poster_path: from the database; a dash and an unknown title nothing")
# A review (13. 11. 2026): the dash ("Seerr has no poster") held for ever. A title requested before
# TMDB added its poster never got one while the request was pending or declined. Now the titles with
# a dash are tried again once a day (the mark seerr_posters_retry).
title_poster[NO_POSTER] = f"/p{NO_POSTER}.jpg"          # TMDB has added a poster meanwhile
db.forget_settings()
mark = db.get_setting("seerr_posters_retry", "")
api_requests.clear()
synchronize()
check(mark != "" and row(12)["poster"] == "-" and not [r for r in api_requests if r.startswith(("movie/", "tv/"))],
      f"the same day a title without a poster is not asked about again (mark {mark!r})")
from datetime import timedelta  # noqa: E402

from jellyscope.seerr import sync as seerr_sync  # noqa: E402

# The recorded requests have fixed dates: the test sets the age up to which a request is still tried
# itself, so it does not start failing in a few months.
original_max_age = seerr_sync.POSTERS_RETRY_MAX_AGE
seerr_sync.POSTERS_RETRY_MAX_AGE = timedelta(days=36500)
db.set_setting("seerr_posters_retry", "2026-01-01 00:00:00")
db.forget_settings()
# Code review (14. 11. 2026): a try that found nothing (Seerr cannot reach TMDB) moved the mark too -
# the titles with a dash then waited another day, with bad luck for ever.
seerr_state["title_errors"] = {NO_POSTER, IN_RADARR}
synchronize()
db.forget_settings()
check(db.get_setting("seerr_posters_retry", "") == "2026-01-01 00:00:00" and row(12)["poster"] == "-",
      f"a try that found nothing does not move the mark ({db.get_setting('seerr_posters_retry', '')!r})")
seerr_state["title_errors"] = set()
# Code review: it was repeated for every title with a dash for ever. Now only for requests that wait
# or are not older than POSTERS_RETRY_MAX_AGE - request 12 waits, request 6 is approved.
seerr_sync.POSTERS_RETRY_MAX_AGE = timedelta(0)
check(seerr_sync._titles_to_look_up(True) == [("movie", str(NO_POSTER))],
      f"an old handled request with a dash is not tried again, a pending one is ({seerr_sync._titles_to_look_up(True)})")
seerr_sync.POSTERS_RETRY_MAX_AGE = timedelta(days=36500)
api_requests.clear()
synchronize()
db.forget_settings()
titles = sorted(r for r in api_requests if r.startswith(("movie/", "tv/")))
check(titles == sorted([f"movie/{NO_POSTER}", f"movie/{IN_RADARR}"]) and row(12)["poster"] == f"/p{NO_POSTER}.jpg",
      f"after a day the titles with a dash are tried again and a poster added meanwhile is stored ({titles}, {row(12)['poster']!r})")
check(row(6)["poster"] == "-" and row(12)["title"] == f"Film {NO_POSTER}" and db.get_setting("seerr_posters_retry", "") > "2026-11-01",
      "where there still is no poster the dash stays; the name is not overwritten; the mark moves")
api_requests.clear()
synchronize()
check(not [r for r in api_requests if r.startswith(("movie/", "tv/"))], "and the next sync of the same day does not ask again")
seerr_sync.POSTERS_RETRY_MAX_AGE = original_max_age
title_poster[NO_POSTER] = None                              # back: the next sections count on a title without a poster
with db.connect() as conn:
    conn.execute("UPDATE seerr_request SET poster = '-' WHERE id = 12")
    conn.commit()

print()
print("--- B) the route: input ---")
admin, reader = login("admin"), login("reader")
requests_seen.clear()
codes = [admin.get(a).status_code for a in ("/seerr/poster/person/603", "/seerr/poster/movie/6o3", "/seerr/poster/movie/999999",
                                            "/seerr/poster/movie/-1", f"/seerr/poster/tv/{APPROVED}")]
check(codes == [404] * 5 and not requests_seen,
      f"an invalid type, id, an unknown title or another type of the title: 404 without a request outside ({codes}, {requests_seen})")

print()
print("--- C) the order of sources ---")
r = admin.get(f"/seerr/poster/movie/{APPROVED}")
check(r.status_code == 200 and r.content == JPEG and r.headers["content-type"] == "image/jpeg" and hosts() == {"radarr"},
      f"an approved movie: the poster from Radarr, Seerr is not asked ({r.status_code}, {sorted(hosts())})")
check(requests_seen == [("radarr", "/api/v3/movie"), ("radarr", "/api/v3/mediacover/6/poster-250.jpg")]
      and params[("radarr", "/api/v3/movie")] == {"tmdbId": str(APPROVED)},
      f"a movie is looked up in Radarr by its TMDB id, the poster is from mediacover ({requests_seen})")
check(r.headers.get("cache-control") == "private, max-age=86400", "a browser may keep a provisional poster for a day")
requests_seen.clear()
r2 = admin.get(f"/seerr/poster/movie/{APPROVED}")
check(r2.status_code == 200 and r2.content == JPEG and not requests_seen, "the second time from the disk, nobody is asked")
check(images.titles_on_disk() == [("movie", str(APPROVED))], "one provisional file per title")
requests_seen.clear()
r = admin.get(f"/seerr/poster/movie/{PENDING}")
check(r.status_code == 200 and requests_seen == [("radarr", "/api/v3/movie"), ("seerr", f"/imageproxy/tmdb/t/p/w342/p{PENDING}.jpg")],
      f"pending: Radarr does not know it, Seerr gives it ({requests_seen})")
requests_seen.clear()
r = admin.get(f"/seerr/poster/tv/{SERIES}")
check(r.status_code == 200 and requests_seen == [("sonarr", "/api/v3/series"), ("sonarr", "/api/v3/mediacover/4/poster-250.jpg")]
      and params[("sonarr", "/api/v3/series")] == {"tvdbId": str(SERIES_TVDB)}, f"a series: in Sonarr by its TVDB id ({requests_seen})")
radarr_state["ignores_filter"] = True
requests_seen.clear()
r = admin.get(f"/seerr/poster/movie/{IN_RADARR}")
check(r.status_code == 200 and ("radarr", "/api/v3/mediacover/8/poster-250.jpg") in requests_seen and hosts() == {"radarr"},
      "a Radarr that does not know the filter tmdbId and sends every movie: the right one is picked")
radarr_state["ignores_filter"] = False
check(images.titles_on_disk() == [("movie", str(APPROVED)), ("movie", str(IN_RADARR)), ("movie", str(PENDING)), ("tv", str(SERIES))],
      f"four titles, four files ({images.titles_on_disk()})")

print()
print("--- D) nothing and bad answers ---")
from jellyscope.seerr import posters  # noqa: E402

seerr_state["images"] = False              # "Enable Image Caching" off: Seerr answers 404
requests_seen.clear()
r = admin.get(f"/seerr/poster/tv/{DECLINED}")
check(r.status_code == 404 and r.headers.get("cache-control") == "no-store" and hosts() == {"sonarr", "seerr"},
      f"nobody has it: 404, the browser does not keep the answer ({r.status_code}, {sorted(hosts())})")
requests_seen.clear()
check(admin.get(f"/seerr/poster/tv/{DECLINED}").status_code == 404 and not requests_seen, "for five minutes nobody is asked again")
check(posters.MISSING_SECONDS == 300, "the note \"none\" holds five minutes")
posters._MISSING[("tv", str(DECLINED))] = time.monotonic() - posters.MISSING_SECONDS - 1
admin.get(f"/seerr/poster/tv/{DECLINED}")
check(bool(requests_seen), "after five minutes it asks again")
requests_seen.clear()
seerr.forget_missing()
admin.get(f"/seerr/poster/tv/{DECLINED}")
check(bool(requests_seen), "after forgetting (a sync, a connection test) it asks at once")
seerr_state["images"] = True
file = images.title_file("tv", str(DECLINED))
for label, change in (("a proxy's HTML login with code 200", {"body": b"<html>login</html>"}),
                      ("a 20 MB body", {"body": JPEG + b"0" * 20_000_000}),
                      ("error 500", {"code": 500}),
                      ("a redirect elsewhere", {"code": 302, "headers": {"Location": "http://jinde.test/x.jpg"}})):
    seerr.forget_missing()
    seerr_state.update(change)
    requests_seen.clear()
    r = admin.get(f"/seerr/poster/tv/{DECLINED}")
    check(r.status_code == 404 and not file.exists() and hosts() <= {"sonarr", "seerr"}, f"{label}: 404, nothing is stored, it goes nowhere else")
    seerr_state.update(body=JPEG, code=200, headers={})
requests_seen.clear()
check(admin.get(f"/seerr/poster/tv/{DECLINED}").status_code == 404 and not requests_seen, "after a bad answer too nobody is asked for five minutes")
seerr.forget_missing()
check(admin.get(f"/seerr/poster/tv/{DECLINED}").status_code == 200 and file.exists(), "with images on Seerr serves it and it is stored")
# Radarr answers with a login page instead of a poster, or does not answer: Seerr is tried.
with db.connect() as conn:
    conn.execute("UPDATE seerr_request SET poster = ? WHERE id = 6", (f"/p{IN_RADARR}.jpg",))
    conn.commit()
for label, change in (("HTML instead of a poster", {"body": b"<html>login</html>"}), ("unavailable", {"down": True})):
    images.forget_title("movie", str(IN_RADARR))
    seerr.forget_missing()
    radarr_state.update(change)
    requests_seen.clear()
    r = admin.get(f"/seerr/poster/movie/{IN_RADARR}")
    check(r.status_code == 200 and ("seerr", f"/imageproxy/tmdb/t/p/w342/p{IN_RADARR}.jpg") in requests_seen,
          f"Radarr {label}: Seerr gives the poster ({r.status_code})")
    radarr_state.update(body=JPEG, down=False)

print()
print("--- E) the shape of the poster path ---")
for bad in ("/../x.jpg", "http://jinde.test/x.jpg", "/a/b.jpg", "/x.svg", "x.jpg", "/x.jpg?y=1", "/x y.jpg", "//jinde.test/x.jpg"):
    with db.connect() as conn:
        conn.execute("UPDATE seerr_request SET poster = ? WHERE id = 14", (bad,))
        conn.commit()
    check(seerr.poster_path("tv", str(DECLINED)) == "", f"the path {bad} is not used")
images.forget_title("tv", str(DECLINED))
seerr.forget_missing()
requests_seen.clear()
r = admin.get(f"/seerr/poster/tv/{DECLINED}")
check(r.status_code == 404 and not any(p.startswith("/imageproxy/") for _, p in requests_seen), "with a bad path Seerr is not asked for an image")
with db.connect() as conn:
    conn.execute("UPDATE seerr_request SET poster = ? WHERE id = 14", (f"/p{DECLINED}.png",))
    conn.commit()
check(seerr.poster_path("tv", str(DECLINED)) == f"/p{DECLINED}.png", "a valid path (.png too) goes through")

print()
print("--- F) permissions ---")
check(reader.get(f"/seerr/poster/movie/{APPROVED}").status_code == 200, "a reader with the Seerr area (the default) gets the poster")
accounts.save_permissions(reader_account["id"], access.pack_permissions(["knihovna", "prehled"], False))
code = reader.get(f"/seerr/poster/movie/{APPROVED}", follow_redirects=False).status_code
check(code in (303, 403), f"without the Seerr area no poster ({code})")
accounts.save_permissions(reader_account["id"], None)
from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import web  # noqa: E402

code = TestClient(web.app).get(f"/seerr/poster/movie/{APPROVED}", follow_redirects=False).status_code
check(code in (302, 303, 401, 403), f"nor a signed-out visitor ({code})")

print()
print("--- G) Jellyfin comes first ---")
check(cards_seerr.poster(card_row(APPROVED)) == f"/seerr/poster/movie/{APPROVED}"
      and cards_seerr.poster(card_row(SERIES)) == f"/seerr/poster/tv/{SERIES}", "a title outside the library: the provisional address")
check(cards_seerr.poster(card_row(IN_LIBRARY)).startswith(f"/image/{MOVIE_ID}?"), "a title from the library: the poster from Jellyfin")
check(cards_seerr.poster({"media_type": "movie", "tmdb_id": "", "poster_id": ""}) == ""
      and cards_seerr.poster({"media_type": "person", "tmdb_id": "5", "poster_id": ""}) == "", "without a TMDB id or with another type no address")
s = admin.get("/seerr?days=365").text
waiting = html_lib.unescape(card_html(s, "seerr_pending"))
check(f'src="/seerr/poster/movie/{PENDING}"' in waiting and waiting.count('data-on-error="fallback"') == 2 and waiting.count("Plakát chybí") == 2
      and "no-image" not in waiting, "a pending request has a provisional poster and a placeholder ready in case it does not load")
check(images.title_file("movie", str(APPROVED)).exists(), "the provisional poster of the approved movie is on the disk")
with db.connect() as conn:      # the movie arrived in Jellyfin (Seerr does not know yet: pairing by the TMDB id)
    conn.execute("INSERT INTO items (id, name, type, tmdb_id, image_tag, date_created, synced_at)"
                 " VALUES ('jf-inception', 'Inception', 'Movie', ?, 'otisk-i', ?, ?)", (str(APPROVED), db.utcnow(), db.utcnow()))
    conn.commit()
check(cards_seerr.poster(card_row(APPROVED)) == "/image/jf-inception?kind=Primary&w=300&tag=otisk-i",
      "the title arrived in the library: the card switches to the poster from Jellyfin at once")
synchronize()
check(not images.title_file("movie", str(APPROVED)).exists() and images.title_file("tv", str(SERIES)).exists(),
      "the sync deletes the provisional poster of a title in the library and leaves the others")
with db.connect() as conn:      # and it left Jellyfin again
    conn.execute("UPDATE items SET is_missing = 1 WHERE id = 'jf-inception'")
    conn.commit()
requests_seen.clear()
check(cards_seerr.poster(card_row(APPROVED)) == f"/seerr/poster/movie/{APPROVED}"
      and admin.get(f"/seerr/poster/movie/{APPROVED}").status_code == 200 and hosts() == {"radarr"},
      "the title left the library: back to the provisional poster, fetched again")

print()
print("--- G2) a request for the 4K version of a series that is in the library ---")
# A review (13. 11. 2026): a 4K request carries the id of the 4K copy - empty until there is one. So a
# series from the library kept a provisional poster: the hourly clean-up deleted it (the normal
# request for the same title is in the library), the next view fetched it again, over and over. And
# it had no link to the library.
template = dict(row(5))
with db.connect() as conn:
    conn.execute("INSERT INTO items (id, name, type, series_id, series_name, series_image_tag, date_created, synced_at)"
                 " VALUES ('dil4k', 'Díl', 'Episode', 'serial4kvknihovne', 'Seriál se 4K', 'otisk-s', ?, ?)",
                 (db.utcnow(), db.utcnow()))
    for number, jellyfin_id, is_4k, created in ((9001, "serial4kvknihovne", 0, "2026-11-01 10:00:00"),
                                                (9002, "", 1, "2026-11-02 10:00:00")):
        new = {**template, "id": number, "tmdb_id": "777000", "tvdb_id": "", "jellyfin_id": jellyfin_id, "is_4k": is_4k,
               "created_at": created, "poster": ""}
        conn.execute(f"INSERT INTO seerr_request ({', '.join(new)}) VALUES ({', '.join('?' * len(new))})", tuple(new.values()))
    conn.commit()
four_k = next(r for r in stats.seerr_latest(3650) if r["request_id"] == 9002)
check(cards_seerr.poster(four_k) == "/image/serial4kvknihovne?kind=Primary&w=300&tag=otisk-s"
      and cards_seerr.detail(four_k) == "/series/serial4kvknihovne",
      f"a request for the 4K version: the series' poster from the library and a link to it ({cards_seerr.poster(four_k)}, {cards_seerr.detail(four_k)})")
images.save_title("tv", "777000", JPEG)
check(seerr.clean_up_posters() == 1 and not images.title_file("tv", "777000").exists()
      and not cards_seerr.poster(four_k).startswith("/seerr/poster/"),
      "the clean-up deletes the provisional poster and the card does not ask for it again (no deleting and fetching over and over)")
with db.connect() as conn:
    conn.execute("DELETE FROM seerr_request WHERE id IN (9001, 9002)")
    conn.execute("DELETE FROM items WHERE id = 'dil4k'")
    conn.commit()

print()
print("--- H) a fresh request, deleting data, a disconnected Seerr ---")
check(admin.get(f"/seerr/poster/movie/{FRESH}").status_code == 404, "a title nobody requested has no poster")
seerr_state["fresh"] = True
seerr.live.CACHE.clear()
seerr.forget_missing()
s = admin.get("/seerr?days=365").text
check(f'/seerr/poster/movie/{FRESH}' in card_html(s, "seerr_pending") and f"Film {FRESH}" in s
      and db.query_one("SELECT id FROM seerr_request WHERE tmdb_id = ?", (str(FRESH),)) is None,
      "a fresh pending request is on the card, not in the database yet")
requests_seen.clear()
r = admin.get(f"/seerr/poster/movie/{FRESH}")
check(r.status_code == 200 and ("seerr", f"/imageproxy/tmdb/t/p/w342/p{FRESH}.jpg") in requests_seen,
      f"the live card's memory knows the path of its poster ({r.status_code})")
seerr.save_settings(False, "http://seerr.test", "")
db.forget_settings()
requests_seen.clear()
images.forget_title("movie", str(PENDING))
check(admin.get(f"/seerr/poster/movie/{FRESH}").status_code == 200
      and admin.get(f"/seerr/poster/movie/{PENDING}").status_code == 404 and "seerr" not in hosts(),
      "a disconnected Seerr: stored posters still show, new ones are not fetched from it")
connect_seerr()
check(len(images.titles_on_disk()) >= 3 and seerr.delete_data() >= 9 and images.titles_on_disk() == [],
      "deleting the stored requests deletes the provisional posters too")
images.item_file("film-1", "Primary", 300).write_bytes(JPEG)
images.save_title("movie", "5", JPEG)
seerr.delete_data()
check(images.item_file("film-1", "Primary", 300).exists() and images.titles_on_disk() == [], "the images from Jellyfin stay")
# Audit QA-5: the deletion took every provisional poster - the covers of Lidarr's albums and Readarr's books, and the
# posters the card "Stahuje se" of Sonarr and Radarr shows (the same file as a request's: the kind and the TMDB id).
# Only the posters of movies and series Radarr and Sonarr do not know go with Seerr's data now.
with db.connect() as conn:
    conn.execute("INSERT INTO radarr_title (title_id, name, tmdb_id) VALUES (70, 'A movie in Radarr', '700')")
    conn.execute("INSERT INTO sonarr_title (title_id, name, tmdb_id) VALUES (80, 'A series in Sonarr', '800')")
    conn.commit()
for kind, title_id in (("album", "31"), ("book", "22"), ("movie", "700"), ("tv", "800"), ("movie", "5"), ("tv", "6")):
    images.save_title(kind, title_id, JPEG)
seerr.delete_data()
check(images.titles_on_disk() == [("album", "31"), ("book", "22"), ("movie", "700"), ("tv", "800")],
      f"the covers of albums and books and the posters of titles Radarr and Sonarr know stay ({images.titles_on_disk()})")
with db.connect() as conn:
    conn.execute("DELETE FROM radarr_title WHERE title_id = 70")
    conn.execute("DELETE FROM sonarr_title WHERE title_id = 80")
    conn.commit()
images.forget_titles()

print()
print("--- I) \"Enable Image Caching\": the state, filling in after switching on, the link to Seerr ---")
check(recorded_response(SEERR_RESPONSES, "settings_public").get("cacheImages") in (True, False), "Seerr's recorded public settings carry cacheImages")
for answer, expected in (({"cacheImages": False}, "0"), ({"cacheImages": True}, "1"), ({}, ""), ({"cacheImages": "ano"}, "")):
    public.clear()
    public.update(answer)
    state = asyncio.run(seerr.check_images())
    check(state == expected == seerr.images_state(), f"cacheImages {answer.get('cacheImages')!r} -> state {expected!r} ({state!r})")
db.set_setting("seerr_images", "1")
seerr_state["down"] = True
check(asyncio.run(seerr.check_images()) == "1" == seerr.images_state(), "an unavailable Seerr does not change the state")
seerr_state["down"] = False
public.clear()
public.update(cacheImages=False)
synchronize()
check(seerr.images_state() == "0" and row(14).get("poster") == f"/p{DECLINED}.jpg",
      "the sync reads the state - and stores the poster path even while Seerr does not serve images")
# Filling in: the request is stored, images off -> no poster; somebody switches them on in Seerr.
seerr_state["images"] = False
requests_seen.clear()
check(admin.get(f"/seerr/poster/tv/{DECLINED}").status_code == 404, "off: a declined request has no poster")
seerr_state["images"] = True
public.update(cacheImages=True)
check(admin.get(f"/seerr/poster/tv/{DECLINED}").status_code == 404, "right after switching on the note \"none\" still holds (five minutes)")
r = admin.post("/settings/seerr", data={"enabled": "1", "url": "http://seerr.test", "api_key": "", "action": "test"},
               follow_redirects=False)
check(r.status_code == 303 and seerr.images_state() == "1", f"\"Test connection\" reads the state again ({seerr.images_state()!r})")
check(admin.get(f"/seerr/poster/tv/{DECLINED}").status_code == 200, "and the poster of a request stored earlier is filled in at once")
public.update(cacheImages=False)
admin.post("/settings/seerr", data={"enabled": "1", "url": "http://seerr.test", "api_key": "", "action": "save"},
           follow_redirects=False)
check(seerr.images_state() == "0", "saving the settings reads the state too")
seerr.forget_missing()
check(admin.get(f"/seerr/poster/movie/{PENDING}").status_code == 200 and seerr.images_state() == "1",
      "Seerr served an image: the state switches to on at once, without waiting for a sync")
# The link to Seerr's settings: from the address Seerr has set for people, else from the one Jellyscope knows.
check(seerr.settings_link() == "http://seerr.test/settings/main", f"the link from Seerr's address ({seerr.settings_link()})")
public.update(applicationUrl="https://zadosti.example.org/")
asyncio.run(seerr.check_images())
check(seerr.settings_link() == "https://zadosti.example.org/settings/main", "Seerr has an \"Application URL\" set: the link leads there")
for bad in ("javascript:alert(1)", "ftp://zadosti.example.org", "https://urluser:secretpass@requests.example.org", "//zadosti.example.org",
            "https://zadosti.example.org/a b", ""):
    public.update(applicationUrl=bad)
    asyncio.run(seerr.check_images())
    check(seerr.settings_link() == "http://seerr.test/settings/main", f"the Application URL {bad!r} is not used")
seerr.save_settings(True, "http://urluser:secretpass@seerr.test/seerr/", "")
db.forget_settings()
check(seerr.settings_link() == "http://seerr.test/seerr/settings/main", f"the password from Seerr's address is not in the link ({seerr.settings_link()})")
seerr.save_settings(False, "", "")
db.forget_settings()
check(seerr.settings_link() == "", "without an address no link")

print()
print("--- digits outside ASCII and a name without a password in the address ---")
# A security pass (13. 11. 2026): str.isdigit() lets "²" or Arabic digits through too.
from jellyscope.seerr import client as client_module  # noqa: E402

check(client_module._tmdb_id("²") == "" and client_module._tmdb_id("٣") == "" and client_module._tmdb_id(603) == "603",
      "a TMDB id from Seerr's answer: only the digits 0-9")
check(cards_seerr.poster({"media_type": "tv", "tmdb_id": "²"}) == "" and cards_seerr.poster({"media_type": "tv", "tmdb_id": "63639"}) != "",
      "the address of a provisional poster only for a TMDB id of the digits 0-9")


async def odd_title_id() -> object:
    async with radarr.RadarrClient("http://radarr.test", "k", transport=httpx.MockTransport(fake_radarr)) as client:
        return await client.title_id(tmdb_id="٣")

requests_seen.clear()
check(asyncio.run(odd_title_id()) is None and requests_seen == [],
      f"Radarr is never asked about a TMDB id of digits outside ASCII ({requests_seen})")
requests_seen.clear()
check(asyncio.run(seerr.provisional_poster("tv", "²")) is None and requests_seen == [], "a provisional poster for such an id: nothing, without a request")


# An address with only a name (http://TOKEN@server: a token in place of the name, no password). Only
# the password was masked, so the token stayed in the text of the image download's error (the image
# address is complete). The client is still faked here: it talks to fake_seerr, which now answers
# the image with an error.
async def image_error() -> str:
    try:
        async with seerr.SeerrClient("http://zeton-9f3a7c2e@seerr.test", "tajny-klic") as client:
            await client.tmdb_poster("/abc.jpg")
    except seerr.SeerrError as error:
        return str(error)
    return "no error"

seerr_state["code"] = 500
error_text = asyncio.run(image_error())
seerr_state["code"] = 200
check("zeton-9f3a7c2e" not in error_text and "500" in error_text, f"a token in place of the name in the address does not get into the image error ({error_text})")


# Code review (14. 11. 2026): a short name without a password is not masked (see
# test_sonarr_settings), but the image address goes into the error text without credentials anyway.
async def image_error_short() -> str:
    try:
        async with seerr.SeerrClient("http://abc@seerr.test", "tajny-klic") as client:
            await client.tmdb_poster("/abc.jpg")
    except seerr.SeerrError as error:
        return str(error)
    return "no error"

seerr_state["code"] = 500
error_text = asyncio.run(image_error_short())
seerr_state["code"] = 200
check("abc@" not in error_text and "http://seerr.test/imageproxy/" in error_text,
      f"the image address is in the error without credentials ({error_text})")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
