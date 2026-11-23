# -*- coding: utf-8 -*-
r"""A title that lies on the disk more than once (4K beside 1080p).

Jellyfin does not join two files of the same movie in a folder into two items -
it makes **one item with more `MediaSources`**. Formerly only
the first of them was taken and the second was lost: the library said 1080p, even though 4K lay
beside it, and the detail did not show that there are two files.

Now all the versions are stored and they can be switched in the title's detail. The details
of the item itself (and so the statistics too) stay from the one Jellyfin
considers the main one - otherwise the library's size would be counted twice.

Verified live against Jellyfin 10.11.11 too: two files in a folder gave
one item with two versions and the switch in the detail changes the size,
the resolution and the path.

Run:
    .venv/bin/python tests/test_file_versions.py
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "verze.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic-dostatecne-dlouhy"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, db, jellyfin, scanner, stats, web  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


db.init_db()
db.set_setting("ui_language", "cs")  # the checks read Czech (English is the default)

# This is how Jellyfin sends it: one item, two files.
ITEM = {
    "Id": "film-1",
    "Name": "Zkouška",
    "Type": "Movie",
    "MediaSources": [
        {"Id": "zdroj-4k", "Name": "2160p", "Path": "/media/film-4k.mkv",
         "Container": "mkv", "Size": 40_000_000_000, "Bitrate": 60_000_000,
         "RunTimeTicks": 72_000_000_000,
         "MediaStreams": [
             {"Type": "Video", "Codec": "hevc", "Width": 3840, "Height": 2160,
              "VideoRange": "HDR"},
             {"Type": "Audio", "Codec": "truehd", "Channels": 8,
              "Language": "eng"},
             {"Type": "Subtitle", "Language": "cze"}]},
        {"Id": "zdroj-1080", "Name": "1080p", "Path": "/media/film-1080.mkv",
         "Container": "mp4", "Size": 8_000_000_000, "Bitrate": 10_000_000,
         "RunTimeTicks": 72_000_000_000,
         "MediaStreams": [
             {"Type": "Video", "Codec": "h264", "Width": 1920, "Height": 1080},
             {"Type": "Audio", "Codec": "aac", "Channels": 2,
              "Language": "cze"}]},
    ],
}

print("--- both versions are pulled out of Jellyfin's answer ---")
versions = jellyfin.extract_versions(ITEM)
check(len(versions) == 2, f"two versions ({len(versions)})")
check([v["name"] for v in versions] == ["2160p", "1080p"],
      f"in the order Jellyfin sends them ({[v['name'] for v in versions]})")
check(versions[0]["width"] == 3840 and versions[1]["width"] == 1920,
      "each has its own resolution")
check(versions[0]["video_codec"] == "hevc" and versions[1]["video_codec"] == "h264",
      "and its own codec")
check(versions[0]["size_bytes"] != versions[1]["size_bytes"], "and its own size")

main = jellyfin.extract_tech_from_item(ITEM)
check(main["width"] == 3840,
      "the item keeps the one Jellyfin considers the main one")
check(main["size_bytes"] == versions[0]["size_bytes"],
      "so the library's size is not counted twice")

print()
print("--- they are stored and can be read ---")
with db.connect() as conn:
    conn.execute(
        "INSERT INTO items (id, name, type, width, height, size_bytes,"
        " container, path, is_missing) VALUES"
        " ('film-1','Zkouška','Movie',3840,2160,40000000000,'mkv',"
        " '/media/film-4k.mkv',0)")
    conn.commit()
check(scanner.write_versions([("film-1", versions)]) == 2, "two were written")

stored = [dict(v) for v in stats.item_versions("film-1")]
check(len(stored) == 2, f"and two are read ({len(stored)})")
check(stored[0]["name"] == "2160p", "the order holds")
check(stored[1]["audio_languages"], "each has the audio languages too")

# When one version disappears from the disk, no row is to stay after it in the detail.
check(scanner.write_versions([("film-1", versions[:1])]) == 1, "rewritten to one")
check(len(stats.item_versions("film-1")) == 1, "the second was deleted")
scanner.write_versions([("film-1", versions)])

print()
print("--- they can be switched in the detail ---")
accounts.create("admin", "longpassword", is_admin=True)
client = TestClient(web.app)
client.post("/login", data={"username": "admin", "password": "longpassword",
                            "method": "local"})


def size(html: str) -> str:
    match = re.search(r"Velikost</th><td>([^<]+)", html)
    return match.group(1).strip() if match else "?"


def path(html: str) -> str:
    match = re.search(r"word-break:break-all\">([^<]+)", html)
    return match.group(1).strip() if match else "?"


default_page = client.get("/item/film-1").text
check("?version=zdroj-1080" in default_page and "?version=zdroj-4k" in default_page,
      "the switch offers both")
check("2160p" in default_page and "1080p" in default_page, "and names them")

switched = client.get("/item/film-1?version=zdroj-1080").text
check(size(default_page) != size(switched),
      f"the size changes by switching ({size(default_page)} -> {size(switched)})")
check("film-1080.mkv" in path(switched),
      f"and the path points to the other file ({path(switched)})")
check("film-4k.mkv" in path(default_page), "without switching it is the main file")
# The list of file versions used to be called `version` - the same as the version
# of the application base.html shows in the footer. On the title's detail it
# overwrote it and the footer showed only "v" (and a new version did not show there).
from jellyscope import __version__  # noqa: E402

check(f"v{__version__}" in default_page, f"the detail's footer shows the application's version (v{__version__})")

print()
print("--- one file: nothing switches ---")
with db.connect() as conn:
    conn.execute("INSERT INTO items (id, name, type, is_missing)"
                 " VALUES ('film-2','Sám','Movie',0)")
    conn.commit()
scanner.write_versions([("film-2", versions[:1])])
alone = client.get("/item/film-2").text
check("?version=" not in alone, "with a single version the switch is not drawn")

print()
print("--- the tasks do not wipe out the versions ---")
# Merging, cleaning up and straightening the data touch the items. The versions point
# at them by a foreign key, so they must be dealt with together - otherwise
# the foreign key falls over on PostgreSQL and on SQLite they stay hanging on an id that
# no longer exists.
source = "".join(p.read_text(encoding="utf-8")
                 for p in sorted((PROJECT / "jellyscope" / "scanner").glob("*.py")))
for name, section in (
        ("merging by tmdb", "_merge_by_tmdb"),
        ("moving the history", "move_history"),
        ("merging the archive", "merge_archive_into_live"),
        ("cleaning up foreign types", "clean_up_phantoms")):
    start = source.index(f"def {section}")
    # The body up to the next function - some are over a hundred lines long and a fixed
    # piece of characters would measure only its start.
    following = source.find(chr(10) + "def ", start + 1)
    body = source[start:following if following > 0 else len(source)]
    check("item_versions" in body, f"{name} thinks of the versions too")

# And above all: an empty answer must not delete the versions. Exactly that would happen
# if some query did not ask for `MediaSources`.
check(scanner.write_versions([("film-1", [])]) == 0,
      "an empty list writes nothing")
check(len(stats.item_versions("film-1")) == 2,
      "and above all deletes nothing - both versions stayed")

# The foreign key works: after the item is deleted no versions stay hanging.
with db.connect() as conn:
    conn.execute("DELETE FROM item_versions WHERE item_id = 'film-2'")
    conn.execute("DELETE FROM items WHERE id = 'film-2'")
    conn.commit()
check(len(stats.item_versions("film-2")) == 0, "after the item is deleted nothing is left")

print()
print("--- the backup knows about the versions ---")
from jellyscope import tasks  # noqa: E402

check("item_versions" in tasks.BACKED_UP_TABLES, "the table is backed up")
order = list(tasks.BACKED_UP_TABLES)
check(order.index("items") < order.index("item_versions"),
      "and after the items - it points at them by a foreign key")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
