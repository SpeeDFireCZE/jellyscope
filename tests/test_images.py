# -*- coding: utf-8 -*-
r"""One image store (jellyscope/images.py) over the data/imagecache folder.

The folder and the files' names used to be known by the /image/ route (web/library.py) and by the tidying
in the scanner (scanner/writing.py) - two places that had to agree. Now
it is one module and a second kind of image was added to it: a provisional poster
of a title that is not in Jellyfin yet (requests from Seerr). Watched:

A) the names of the files from Jellyfin did not change (the cache so far still holds),
   a provisional poster has a name of its own and nothing foreign gets into the path;
B) the image's type is recognised by the first bytes;
C) a provisional poster: only a real image up to 5 MB is stored, it comes back
   with its type, it can be listed and deleted;
D) tidying an item (after a change of the tag in the scanner) deletes all its images
   and nothing foreign - neither an item with a longer id, nor the provisional posters;
E) the /image/ route: an image is downloaded from Jellyfin once, the second time it comes
   from the disk with the right type; the fallback is Primary; what does not exist is 404 and nothing
   is stored; an old file of unknown content is sent as before.

Spec: docs/superpowers/specs/2026-11-13-karty-arr-seerr-design.md

Run:
    .venv/bin/python tests/test_images.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "data" / "obrazky.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

from jellyscope import accounts, config, db  # noqa: E402

failures = 0
db.init_db()


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


def raises(error: type[Exception], action: Any) -> bool:
    try:
        action()
    except error:
        return True
    return False


JPEG = b"\xff\xd8\xff\xe0" + b"0" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64
WEBP = b"RIFF\x40\x00\x00\x00WEBPVP8 " + b"0" * 48

print("--- A) where the images lie and what they are called ---")
from jellyscope import images  # noqa: E402

FOLDER = config.load_config().database_path.parent / "imagecache"
check(images.folder() == FOLDER and FOLDER.is_dir(), f"the data/imagecache folder beside the database, it is created by itself ({images.folder()})")
check(images.item_file("abc", "Primary", 300, "t1") == FOLDER / "abc-Primary-300-t1.img"
      and images.item_file("abc", "Backdrop", 800).name == "abc-Backdrop-800.img"
      and images.item_file("0a1b-2c3d_x", "Logo", 80).name == "0a1b-2c3d_x-Logo-80.img",
      "the names of files from Jellyfin unchanged: <id>-<kind>-<width>[-<tag>].img")
check(images.title_file("movie", "603") == FOLDER / "title-movie-603.img"
      and images.title_file("tv", "70523").name == "title-tv-70523.img", "a provisional poster: title-<type>-<tmdb id>.img")
check(all(raises(ValueError, lambda t=t, i=i: images.title_file(t, i))
          for t, i in (("person", "603"), ("movie", "6o3"), ("movie", ""), ("movie", "../../x"), ("../tv", "1"), ("movie", "1/2"))),
      "a provisional poster only for movie/tv and a numeric TMDB id")
check(all(raises(ValueError, lambda i=i, d=d, o=o: images.item_file(i, d, 300, o))
          for i, d, o in (("../x", "Primary", ""), ("a/b", "Primary", ""), ("", "Primary", ""), ("abc", "../Primary", ""),
                          ("abc", "Primary", "../t"))),
      "neither a slash nor dots get into an item's file name")

print()
print("--- B) the image's type by the first bytes ---")
check([images.image_type(x) for x in (JPEG, PNG, WEBP, b"<html>", b"", b"RIFF\x40\x00\x00\x00WAVEfmt ", b"GIF89a")]
      == ["image/jpeg", "image/png", "image/webp", None, None, None, None], "JPEG, PNG, WebP; nothing else")
check(images.MAX_BYTES == 5 * 1024 * 1024, "a 5 MB ceiling")

print()
print("--- C) a title's provisional poster ---")
check(images.load_title("movie", "603") is None and images.titles_on_disk() == [], "at the start there is none")
check(images.save_title("movie", "603", b"<html>prihlaseni</html>") is False
      and not images.title_file("movie", "603").exists(), "what is not an image is not stored")
check(images.save_title("movie", "603", JPEG + b"0" * images.MAX_BYTES) is False
      and not images.title_file("movie", "603").exists(), "nor an image above 5 MB")
check(images.save_title("movie", "../x", PNG) is False and images.load_title("person", "1") is None
      and images.forget_title("movie", "x") is False, "an invalid type or id: nothing, not an exception")
check(images.save_title("movie", "603", PNG) is True and images.load_title("movie", "603") == (PNG, "image/png"),
      "a stored one comes back with its type")
check(images.save_title("tv", "70523", WEBP) and images.load_title("tv", "70523") == (WEBP, "image/webp")
      and images.titles_on_disk() == [("movie", "603"), ("tv", "70523")], "the listing of provisional posters")
check(images.save_title("movie", "603", JPEG) and images.load_title("movie", "603") == (JPEG, "image/jpeg"),
      "a new save replaces the old poster")
check([p.name for p in FOLDER.iterdir() if not p.name.endswith(".img")] == [], "no temporary file is left after writing")

print()
print("--- C2) a poster stored before 1.9.0 (titul-<type>-<tmdb id>.img) ---")
OLD_POSTER = FOLDER / "titul-movie-1891.img"
OLD_POSTER.write_bytes(PNG)
check(images.load_title("movie", "1891") == (PNG, "image/png") and ("movie", "1891") in images.titles_on_disk(),
      "is found under its old name - nothing is downloaded again")
check(images.save_title("movie", "1891", JPEG) and images.load_title("movie", "1891") == (JPEG, "image/jpeg")
      and not OLD_POSTER.exists() and (FOLDER / "title-movie-1891.img").exists()
      and images.titles_on_disk().count(("movie", "1891")) == 1, "a new one replaces it under the new name, one per title")
OLD_POSTER.write_bytes(PNG)
check(images.forget_title("movie", "1891") is True and not OLD_POSTER.exists()
      and not (FOLDER / "title-movie-1891.img").exists(), "forgetting deletes both names")

print("--- D) tidying ---")
for name in ("abc-Primary-300.img", "abc-Primary-300-t1.img", "abc-Backdrop-800.img", "abcd-Primary-300.img"):
    (FOLDER / name).write_bytes(JPEG)
check(images.forget_items(["abc", "nikdy-nebyl"]) == 3, "tidying an item deletes all its kinds, widths and tags")
check((FOLDER / "abcd-Primary-300.img").exists() and images.titles_on_disk() == [("movie", "603"), ("tv", "70523")],
      "it deletes neither an item with a longer id nor the provisional posters")
from jellyscope.scanner import writing as scanner_writing  # noqa: E402

(FOLDER / "abc-Primary-300-t2.img").write_bytes(JPEG)
check(scanner_writing._forget_images(["abc"]) == 1 and not (FOLDER / "abc-Primary-300-t2.img").exists(),
      "the scanner tidies through the same module after a change of the tag")
check(images.forget_title("tv", "70523") is True and images.forget_title("tv", "70523") is False
      and images.titles_on_disk() == [("movie", "603")], "deleting one provisional poster")
images.save_title("tv", "1", PNG)
check(images.forget_titles() == 2 and images.titles_on_disk() == [] and (FOLDER / "abcd-Primary-300.img").exists(),
      "deleting all provisional posters leaves the images from Jellyfin")

print()
print("--- E) the /image/ route ---")
calls: list[tuple[str, str, int]] = []
answers: dict[str, tuple[bytes, str] | None] = {"Primary": (PNG, "image/png")}


class FakeClient:
    """A Jellyfin that writes down who asked it for what, and returns a prepared image."""

    def __init__(self, *a: Any, **k: Any) -> None:
        pass

    async def __aenter__(self) -> "FakeClient":
        return self

    async def __aexit__(self, *a: Any) -> bool:
        return False

    async def image_bytes(self, item_id: str, kind: str = "Primary", max_width: int = 400) -> tuple[bytes, str] | None:
        calls.append((item_id, kind, max_width))
        return answers.get(kind)


images.JellyfinClient = FakeClient  # type: ignore[assignment,misc]

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import web  # noqa: E402

accounts.create("reader", "longpassword")
client = TestClient(web.app)
client.post("/login", data={"username": "reader", "password": "longpassword", "method": "local"})

r1 = client.get("/image/film-1?kind=Primary&w=300&tag=t1")
r2 = client.get("/image/film-1?kind=Primary&w=300&tag=t1")
check(r1.status_code == 200 and r1.content == PNG and r2.content == PNG and calls == [("film-1", "Primary", 300)],
      f"an image from Jellyfin is downloaded once, the second time it comes from the disk ({calls})")
check((FOLDER / "film-1-Primary-300-t1.img").read_bytes() == PNG, "it lies under the same name as before")
check(r1.headers["content-type"] == "image/png" and r2.headers["content-type"] == "image/png",
      f"from the disk too it is sent with the right type ({r2.headers['content-type']})")
check("max-age=604800" in r2.headers.get("cache-control", ""), "the browser may keep it for a week")
client.get("/image/film-1?kind=Primary&w=300&tag=t2")
check(len(calls) == 2 and (FOLDER / "film-1-Primary-300-t2.img").exists(), "another tag = another file, downloaded again")

calls.clear()
r = client.get("/image/film-1?kind=Backdrop&w=800")
check(r.status_code == 200 and calls == [("film-1", "Backdrop", 800), ("film-1", "Primary", 800)]
      and (FOLDER / "film-1-Backdrop-800.img").exists(), "a kind the item does not have: the fallback is the poster")
answers["Primary"] = None
calls.clear()
r = client.get("/image/film-2?kind=Primary&w=300")
check(r.status_code == 404 and calls == [("film-2", "Primary", 300)] and not (FOLDER / "film-2-Primary-300.img").exists(),
      "an image Jellyfin does not have: 404 and nothing is stored")
(FOLDER / "stary-Primary-400.img").write_bytes(b"GIF89a-neco-stareho")
calls.clear()
old = client.get("/image/stary?kind=Primary&w=400")
check(old.status_code == 200 and old.headers["content-type"] == "image/jpeg" and calls == [],
      "an old file of unknown content is sent as before (image/jpeg), without a query")
calls.clear()
check(client.get("/image/a..b").status_code == 404 and client.get("/image/film-1?kind=Cokoliv&w=5").status_code == 404
      and calls == [("film-1", "Primary", 80)], f"an invalid id does not ask; an unknown kind is the poster, the width at least 80 ({calls})")


# A finding of the final review: `str.isalnum()` lets through letters and digits outside ASCII too ("é", "²"),
# the store does not let them into a file's name - and the route then fell over (500) instead of 404.
def status_of(path: str) -> object:
    try:
        return client.get(path).status_code
    except Exception as error:      # noqa: BLE001 - an unhandled exception of the route = 500 in live use
        return f"exception {type(error).__name__}"


calls.clear()
check(status_of("/image/é") == 404 and status_of("/image/film²") == 404 and calls == [],
      f"an id with a letter or a digit outside ASCII: 404, without a query ({status_of('/image/é')}, {status_of('/image/film²')})")
check(status_of("/image/film-1?kind=Primary&w=300&tag=t1²é") == 200 and calls == [],
      f"the tag is cleaned to ASCII: the extra characters are dropped and the image is found ({status_of('/image/film-1?kind=Primary&w=300&tag=t1²é')})")

print()
print("--- F) a TMDB id of digits outside ASCII ---")
# str.isdigit() lets through "²" or Arabic digits too; only 0-9 belong into a file's name.
for odd in ("²", "٣", "12²"):
    check(raises(ValueError, lambda: images.title_file("tv", odd)), f"title_file refuses the TMDB id {odd!r}")
check(images.save_title("tv", "²", JPEG) is False and images.load_title("tv", "²") is None
      and not list(images.folder().glob("titul-tv-²*")), "a provisional poster with such an id is neither stored nor loaded")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
