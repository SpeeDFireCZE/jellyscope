# -*- coding: utf-8 -*-
r"""Images stop being held after a fix in Jellyfin; long lists go into a dialog.

**Images.** They were stored into `data/imagecache/<id>-<kind>-<width>.img`
and a file once stored never asked again whether it still held. When
Jellyfin assigned a wrong poster and a person fixed it there, Jellyscope
kept serving the old one - and nothing could be done about it.

`ImageTags`, the image's tag from Jellyfin, solves it: it is stored with the item,
goes into the file's name in the cache, and the sync deletes the old files
when it changes.

**Long lists.** Above a certain number the plays and the language bars
hide into a dialog, otherwise every further one would push the statistics lower.

Run:
    .venv/bin/python tests/test_images_and_dialogs.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "obrazky.db")
os.environ["SECRET_KEY"] = "testovaci-klic"

from jellyscope import accounts, config, db, i18n, scanner, stats  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


db.init_db()
accounts.create("admin", "longpassword", is_admin=True)

CACHE = config.load_config().database_path.parent / "imagecache"
CACHE.mkdir(parents=True, exist_ok=True)


def item(item_id: str, tag: str | None, name: str = "Duna") -> dict:
    """What Jellyfin sends."""
    record = {"Id": item_id, "Name": name, "Type": "Movie",
              "Path": f"/media/{name}.mkv"}
    if tag:
        record["ImageTags"] = {"Primary": tag}
    return record


def write(item_id: str, tag: str | None) -> None:
    scanner._write_items(
        [scanner._item_row(item(item_id, tag), "lib", {}, db.utcnow())],
        keep_existing_tech=True)


print("--- the image's tag is stored with the item ---")
write("film-1", "abc123")
check(db.query_value("SELECT image_tag FROM items WHERE id = 'film-1'") == "abc123",
      "the tag is in the database")


print()
print("--- when the image changes in Jellyfin, the old one is forgotten ---")
# Three files in the cache: two sizes of the poster and one backdrop.
for name in ("film-1-Primary-400.img", "film-1-Primary-200.img",
             "film-1-Backdrop-600.img"):
    (CACHE / name).write_bytes(b"stary obrazek")
# And one foreign one, which must not be affected.
(CACHE / "film-2-Primary-400.img").write_bytes(b"jiny film")

write("film-1", "xyz789")   # in Jellyfin somebody fixed a wrongly identified movie

left = sorted(p.name for p in CACHE.glob("*.img"))
check(left == ["film-2-Primary-400.img"],
      f"that item's images are gone, the foreign one stayed ({left})")
check(db.query_value("SELECT image_tag FROM items WHERE id = 'film-1'") == "xyz789",
      "and the new tag was stored")


print()
print("--- the same tag deletes nothing ---")
(CACHE / "film-1-Primary-400.img").write_bytes(b"novy obrazek")
write("film-1", "xyz789")
check((CACHE / "film-1-Primary-400.img").is_file(),
      "without a change the cache is left be")
# Otherwise every sync would throw away the whole cache and Jellyfin would
# have to send all the posters again.


print()
print("--- a series' poster has a tag of its own ---")
# We keep no item for a series (`items` holds the episodes), so there was nothing to compare
# for its poster - an image downloaded once stayed there
# for ever. Jellyfin reports `SeriesPrimaryImageTag` with every episode, that is
# the tag of its series' poster.
def episode(item_id: str, series_tag: str | None) -> dict:
    record = {"Id": item_id, "Name": "1. dil", "Type": "Episode",
              "SeriesId": "serial-1", "SeriesName": "Kancelar",
              "ParentIndexNumber": 1, "IndexNumber": 1,
              "Path": "/media/kancelar/s01e01.mkv"}
    if series_tag:
        record["SeriesPrimaryImageTag"] = series_tag
    return record


def write_episode(item_id: str, series_tag: str | None) -> None:
    scanner._write_items(
        [scanner._item_row(episode(item_id, series_tag), "lib", {}, db.utcnow())],
        keep_existing_tech=True)


# A poster lying in the cache from the time when the series' tag was not stored yet.
# We do not know whether it holds - and exactly because of it the column
# came about, so it must disappear at the first filling.
(CACHE / "serial-1-Primary-400.img").write_bytes(b"plakat z doby pred otiskem")
write_episode("dil-1", "serialA")
check(db.query_value(
    "SELECT series_image_tag FROM items WHERE id = 'dil-1'") == "serialA",
    "the series poster's tag is stored with the episode")
check(not (CACHE / "serial-1-Primary-400.img").exists(),
      "and the old poster without a tag is thrown away at the first filling")

(CACHE / "serial-1-Primary-400.img").write_bytes(b"stary plakat serialu")
write_episode("dil-1", "serialB")   # in Jellyfin somebody fixed the series' poster
check(not (CACHE / "serial-1-Primary-400.img").exists(),
      "after the tag changes the series' poster is forgotten")

(CACHE / "serial-1-Primary-400.img").write_bytes(b"novy plakat")
write_episode("dil-1", "serialB")
check((CACHE / "serial-1-Primary-400.img").is_file(),
      "without a change it is left be")

series = stats.series_detail("serial-1")
check(series.get("image_tag") == "serialB",
      f"and it goes into the image's address ({series.get('image_tag')})")

print()
print("--- the tag is part of the image's address ---")
from fastapi.testclient import TestClient  # noqa: E402
from jellyscope.web import app  # noqa: E402

with TestClient(app) as client:
    client.post("/login", data={"username": "admin", "password": "longpassword"},
                follow_redirects=False)
    # Jellyfin does not answer in the test, so the image does not come - the point is that
    # different tags must not meet in one cache file.
    client.get("/image/film-1?kind=Primary&w=400&tag=xyz789")
    client.get("/image/film-1?kind=Primary&w=400&tag=jinyotisk")
    page = client.get("/library").text
    check("Velikost celkem" in page or "Total size" in page,
          "the library shows the size of all libraries together")


print()
print("--- long lists go into a dialog ---")
from jellyscope import web  # noqa: E402

limits = web._limits()
check(limits["stream_limit"] >= 1 and limits["people_limit"] >= 1,
      f"the ceilings are read from the settings ({limits})")

# They change in Settings, not in the code - and a value from the form is clipped into bounds,
# whatever somebody writes there.
db.set_setting("ui_max_streams", "3")
check(web._limits()["stream_limit"] == 3, "a change in the settings takes effect")
db.set_setting("ui_max_streams", "999")
check(web._limits()["stream_limit"] == web.LIMIT_MAX,
      f"nonsense is clipped to the ceiling ({web._limits()['stream_limit']})")
db.set_setting("ui_max_streams", "nesmysl")
check(web._limits()["stream_limit"] == 10, "and text falls back to the default value")
db.set_setting("ui_max_streams", "2")

# They have their own section in Settings - a note for next time, when choices of the look
# are added: they belong there, not into General.
sections = dict((k, n) for k, n, _a in web.SETTINGS_SECTIONS)
check(i18n.translate(sections.get("interface", ""), "cs") == "Rozhraní",
      "the look has its own settings section")
with TestClient(app) as client:
    client.post("/login", data={"username": "admin", "password": "longpassword"},
                follow_redirects=False)
    page = client.get("/settings?section=interface").text
    check('name="ui_max_streams"' in page and 'name="ui_max_viewers"' in page,
          "and both fields are in it")
    # The streams' ceiling is twofold: fewer of them fit on a phone, because the card
    # takes the whole width there.
    # Both ceilings are twofold: less fits on a phone, because the card
    # and the bar take the whole width there.
    check('name="ui_max_streams_mobile"' in page
          and 'name="ui_max_viewers_mobile"' in page,
          "and separate ceilings for a phone")
    response = client.post("/settings/interface",
                           data={"ui_max_streams": "7", "ui_max_viewers": "9",
                                 "ui_max_streams_mobile": "2",
                                 "ui_max_viewers_mobile": "4"},
                           follow_redirects=False)
    check(response.headers.get("location") == "/settings?section=interface",
          "saving returns back to the same section")
    # The form here does not send the rows of the Insights tables, so they have the default
    # values - tests/test_insights_limit.py watches them.
    # The *Arr and Seerr tables have ceilings of their own (tests/test_insights_limit.py watches them).
    check(web._limits() == {"stream_limit": 7, "stream_limit_mobile": 2,
                            "people_limit": 9, "people_limit_mobile": 4,
                            "insights_row_limit": 10, "insights_row_limit_mobile": 5,
                            "table_limits": {"insights": (10, 5), "arr": (10, 5), "seerr": (10, 5), "music": (10, 5)}},
          f"and the values from the form were saved ({web._limits()})")
db.set_setting("ui_max_streams", "2")

print()
print("--- the map's zooming is chosen by the user ---")
# The wheel over the map stops the page's scrolling. Whoever minds it switches to
# clicking - and then the wheel is not listened to at all.
from jellyscope import charts  # noqa: E402

check(web._zoom_mode() == "click", "the default is clicking, not the wheel")
check('data-zoom="click"' in charts.world_map([
    {"lat": 50.0, "lon": 14.4, "seconds": 60, "plays": 1, "people": 1}]),
    "the mode goes into the map as an attribute")

with TestClient(app) as client:
    client.post("/login", data={"username": "admin", "password": "longpassword"},
                follow_redirects=False)
    client.post("/settings/interface",
                data={"ui_max_streams": "10", "ui_max_viewers": "10",
                      "ui_map_zoom": "wheel"}, follow_redirects=False)
    check(web._zoom_mode() == "wheel", "the choice is saved")
    client.post("/settings/interface",
                data={"ui_max_streams": "10", "ui_max_viewers": "10",
                      "ui_map_zoom": "vymysl"}, follow_redirects=False)
    check(web._zoom_mode() == "click", "nonsense falls back to clicking")

# The map's script is in a file of its own (static/map.js), base.html only loads it.
map_js = (PROJECT / "jellyscope" / "static" / "map.js").read_text(encoding="utf-8")
check('map.dataset.zoom === "wheel"' in map_js,
      "the wheel's listener depends on the mode")
# The map and the card "who in which language" have a template of their own, which the page
# and the dashboard include. The marks are looked for in it - and the page must include it.
TEMPLATES = PROJECT / "jellyscope" / "templates"


def card_of_page(page: str, card: str) -> str:
    """The card's template when the page really includes it; otherwise an empty text."""
    includes = f'{{% include "{card}" %}}' in (TEMPLATES / page).read_text(encoding="utf-8")
    return (TEMPLATES / card).read_text(encoding="utf-8") if includes else ""


check('data-map="closer"' in card_of_page("network.html", "_card_map.html"),
      "above the map there are buttons for zooming in")
db.set_setting("ui_max_streams", "2")

template = (PROJECT / "jellyscope" / "templates" / "_now_playing.html").read_text(
    encoding="utf-8")
check('data-modal="modal-streams"' in template, "a button opens the dialog with the streams")
check('data-filter-modal="modal-streams"' in template, "and the dialog has a users' filter")
# Three times: in the card (up to the phone ceiling), in the card above it (hidden
# on a phone) and in the dialog. Everywhere the same macro - three copies would drift apart over time.
check(template.count("{{ stream(row) }}") == 3,
      f"a stream is drawn by one macro everywhere ({template.count('{{ stream(row) }}')}x)")

template = card_of_page("video/languages.html", "_card_viewer_languages.html")
check('data-modal="modal-languages"' in template, "the same with the languages")
check('data-filter-modal="modal-languages"' in template, "with the filter too")

# The filter may hold only in the dialog - that is why it filters in the browser and is
# stored nowhere. Were it to go into the address, it would survive closing the dialog.
# Since the rebuild for CSP the scripts are without inline code, in static/js/app.js.
base = ((PROJECT / "jellyscope" / "templates" / "base.html").read_text(encoding="utf-8")
        + (PROJECT / "jellyscope" / "static" / "js" / "app.js").read_text(encoding="utf-8"))
check("data-filter-modal" in base, "the filter's handler is in the base template")
check("wasOpen" in base,
      "and an open dialog survives the card's automatic refresh")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
