# -*- coding: utf-8 -*-
r"""The map on the Network page: places, real people and clusters by zoom.

GeoLite2 gives cities coordinates by districts - Prague has eleven of them.
They used to be rounded to a tenth of a degree, so one city fell apart into
several dots with the same label, and the number of people was a maximum over
addresses instead of the number of different viewers. Guarded here:

* one place = one set of coordinates from GeoIP, people = different viewers;
* clusters for every zoom level and their labels;
* the layers in the SVG.

GeoIP is faked with a dict address -> place, the GeoLite2 database is not
needed.

Run:
    .venv/bin/python tests/test_map_clusters.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "map.db")
os.environ["SECRET_KEY"] = "test-secret"
os.environ["JELLYSCOPE_DEMO"] = "0"

from jellyscope import db, formatting, geoip, stats  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


def place(lat: float, lon: float, city: str) -> dict:
    return {"lat": lat, "lon": lon, "city": city, "country": "Czechia", "code": "CZ"}


# Three Prague districts, Kladno and an address GeoIP knows only the country of.
PLACES = {
    "203.0.113.1": place(50.0883, 14.4124, "Prague"),
    "203.0.113.2": place(50.0471, 14.4523, "Prague"),
    "203.0.113.3": place(50.1242, 14.398, "Prague"),
    "203.0.113.4": place(50.1473, 14.1028, "Kladno"),
    "203.0.113.5": place(49.75, 15.5, ""),
}
geoip.is_available = lambda: True
geoip.lookup = lambda address: PLACES.get(address)

db.init_db()
db.set_setting("ui_language", "cs")  # the checks read Czech (English is the default)
ago = lambda days: (datetime.now(timezone.utc) - timedelta(days=days)).strftime(db.TIME_FORMAT)  # noqa: E731
PLAYS = [
    # viewer, address, seconds
    ("u1", "203.0.113.1", 600),
    ("u1", "203.0.113.2", 900),
    ("u2", "203.0.113.2", 900),
    ("u3", "203.0.113.3", 300),
    ("u4", "203.0.113.4", 200),
    ("u5", "203.0.113.5", 100),
    ("u6", "192.168.1.5", 500),   # home network - marks nothing
    ("u7", "", 500),              # an import - carries no address
]
with db.connect() as conn:
    for k, (who, address, seconds) in enumerate(PLAYS):
        conn.execute(
            "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name,"
            " item_type, started_at, last_seen_at, watched_seconds, paused_seconds,"
            " is_paused, is_active, play_method, client, remote_address, bitrate)"
            " VALUES (?, ?, ?, 'i1', 'Duna', 'Movie', ?, ?, ?, 0, 0, 0, 'DirectPlay',"
            " 'Web', ?, 8000000)",
            (f"s{k}", who, who.upper(), ago(1), ago(1), seconds, address))
    conn.commit()

print("--- places ---")
points = stats.hotspots(30)
by_coordinates = {(b["lat"], b["lon"]): b for b in points}
check(len(points) == 5, f"five places: three Prague districts, Kladno, a country only ({len(points)})")
a2 = by_coordinates.get((50.0471, 14.4523), {})
check(a2.get("people") == 2 and a2.get("viewers") == frozenset({"u1", "u2"}),
      f"people = different viewers ({a2.get('people')}, {a2.get('viewers')})")
check(by_coordinates.get((50.0883, 14.4124), {}).get("description") == "Prague, Czechia",
      "the place's label: city, country")
check(by_coordinates.get((49.75, 15.5), {}).get("description") == "Czechia",
      "without a city only the country")
check([b["seconds"] for b in points] == [1800, 600, 300, 200, 100],
      "sorted by the time watched")
check(all(b["people"] == len(b["viewers"]) for b in points), "lidi agrees with the set of viewers")

countries = {z["label"]: z for z in stats.viewer_countries(30)}
check(countries.get("Czechia", {}).get("places") == 3,
      f"the table of countries counts cities: Prague, Kladno, Czechia ({countries.get('Czechia', {}).get('places')})")

places_calls = []
# At the front door (the Map card calls stats.places) and in the submodule where it lives
# (hotspots and viewer_countries call it there) - otherwise a second computation would
# go unnoticed (test_stats_package, check e).
original_places = stats.locations.places


def counted_places(days):
    places_calls.append(days)
    return original_places(days)


stats.places = stats.locations.places = counted_places
from jellyscope import cards  # noqa: E402

card_data = cards.BY_KEY["map"].data(30)
stats.places = stats.locations.places = original_places
check(len(places_calls) == 1 and len(card_data["hotspots"]) == 5
      and card_data["countries"][0]["places"] == 3,
      f"the Map card computes the places once, not again for the table of countries ({len(places_calls)}×)")

print("--- word forms ---")
for n, expected in ((1, "1 člověk"), (3, "3 lidé"), (5, "5 lidí"), (0, "0 lidí"), (22, "22 lidí")):
    result = formatting.plural("{n} people", n)
    check(result == expected, f"plural({n}) = {expected!r} ({result!r})")

print("--- Mercator ---")
from jellyscope import map_clusters, worldmap  # noqa: E402

check(worldmap.to_map(0, 0) == (180.0, 180.0), "the equator and the prime meridian in the middle")
check(abs(worldmap.to_map(85.05112878, 0)[1]) < 1e-6, "85.05° N is the top edge")
south = worldmap.to_map(-90, 0)
check(south == worldmap.to_map(-85.05112878, 0) and all(map(lambda v: v == v and abs(v) < 1e9, south)),
      f"the south pole is clamped, no infinity ({south})")
x, y = worldmap.to_map(50.0883, 14.4124)
check(abs(x - 194.41) < 0.01 and abs(y - 121.95) < 0.01, f"Prague ({x:.2f}, {y:.2f})")
check(worldmap.DEFAULT_VIEW == (0.0, 40.41, 360.0, 215.04) and worldmap.WIDTH == worldmap.HEIGHT == 360,
      "the default view 80° N - 60° S, the world 360 × 360")

print("--- clusters ---")
levels = map_clusters.clusters(points)
first = levels[0]["clusters"] if levels else []
check(len(levels) >= 2 and levels[0]["level"] == 1 and len(first) == 1,
      f"at 1× everything is one cluster ({[len(u['clusters']) for u in levels]})")
check(bool(first) and first[0]["people"] == 5 and first[0]["places"] == 5,
      "the cluster: five different people, five places")
check(bool(first) and first[0]["description"] == "Prague, Czechia a 2 další místa",
      f"the cluster's label ({first[0]['description'] if first else None})")
last = levels[-1] if levels else {"level": 0, "clusters": []}
check(len(last["clusters"]) == 5 and all(s["places"] == 1 for s in last["clusters"])
      and last["level"] <= map_clusters.MAX_LEVEL,
      f"the last level ({last['level']}×): every place on its own")
fingerprint = lambda u: sorted((round(s["x"], 6), round(s["y"], 6), s["places"]) for s in u["clusters"])  # noqa: E731
check(all(fingerprint(a) != fingerprint(b) for a, b in zip(levels, levels[1:])),
      "no two levels in a row are the same")
check([u["level"] for u in levels] == sorted({u["level"] for u in levels}),
      "the levels ascending, without repeats")
check(map_clusters.clusters(points) == levels, "the same data = the same clusters")
close = [dict(lat=50.0, lon=14.0, seconds=10, plays=1, byte_count=0, viewers=frozenset({"a"}),
              description="X"),
         dict(lat=50.0, lon=14.001, seconds=5, plays=1, byte_count=0, viewers=frozenset({"b"}),
              description="X")]
close_levels = map_clusters.clusters(close)
end = close_levels[-1]
check(len(end["clusters"]) == 1 and end["clusters"][0]["places"] == 2
      and end["clusters"][0]["description"] == "X",
      "places 0.001 apart stay one cluster even at 1024×")
check(map_clusters.max_zoom(close_levels, len(close)) == map_clusters.MAX_LEVEL,
      "and the map zooms in up to 1024×")
check(map_clusters.max_zoom(levels, len(points)) == max(20, last["level"]),
      "otherwise up to the last level, at least 20×")
check(map_clusters.max_zoom(map_clusters.clusters(points[:1]), 1) == 20, "one place: 20×")
check(map_clusters.clusters([]) == [], "no places, no levels")

print("--- SVG ---")
import re  # noqa: E402

from jellyscope import charts  # noqa: E402

svg = charts.world_map(points)
# A dot = one cluster; a cluster that does not change over several levels is in the
# page only once (data-from / data-to), not again in every level.
dots = re.findall(r'<circle class="map-point"[^>]*>', svg)
spans = [(int(re.search(r'data-from="(\d+)"', c).group(1)), int(re.search(r'data-to="(\d+)"', c).group(1)))
         for c in dots]
check(all("members" in s for u in levels for s in u["clusters"]), "a cluster knows which places it is made of")
check(len(dots) < sum(len(u["clusters"]) for u in levels),
      f"the same cluster is not drawn again in every level ({len(dots)} dots)")
check(all(sum(1 for low, high in spans if low <= u["level"] <= high) == len(u["clusters"]) for u in levels),
      "every level shows exactly as many dots as it has clusters")
check(f'data-levels="{" ".join(str(u["level"]) for u in levels)}"' in svg, "the list of levels for the script")
check(all(('display="none"' in c) == (low != 1) for c, (low, _) in zip(dots, spans)),
      "without the script only the dots of level 1× show")
check(f'data-max="{map_clusters.max_zoom(levels, len(points))}"' in svg, "data-max by the data")
check('data-world="0 0 360 360"' in svg and 'data-default="0 40.41 360 215.04"' in svg
      and 'viewBox="0 40.41 360 215.04"' in svg, "the world, the default view and the viewBox")
check("Prague, Czechia a 2 další místa: " in svg and "5 lidí" in svg,
      "the cluster's tooltip: the label and the word form")
for css_class in ("map-base", "map-detailed", "map-tile", "map-borders"):
    check(f'class="{css_class}' in svg, f"the basemap layer .{css_class}")
check(svg.index("map-base") < svg.index("map-detailed")
      < svg.index("map-tile") < svg.index("map-borders") < svg.index('class="map-point"'),
      "the order from the bottom: the built-in outline, the detailed map, OSM, borders, dots")
check('data-basemap="drawn"' in svg and 'data-detailed="0"' in svg, "the default basemap")
check('data-grids="32 128"' in svg, "the server sends the size of the tile grid")
# A dot's size is computed within the level that is showing right now - a dot carried
# over from a lower level must not keep that lower level's scale (more time = a smaller
# dot). So the script gets the dot's time and the maximum of every level.
check(dots and all(re.search(r'data-s="\d+"', c) for c in dots), "every dot carries the time watched")
maxima = re.search(r'data-maxima="([^"]*)"', svg)
check(bool(maxima) and [int(v) for v in maxima.group(1).split()]
      == [max(s["seconds"] for s in u["clusters"]) for u in levels], "the maximum time for every level")
check('class="map-borders"' not in charts.world_map(points, basemap="osm"),
      "with OpenStreetMap the rough 1:110M borders are not drawn over the real map")
single = charts.world_map(points[:1])
check(single.count('class="map-point"') == 1 and 'data-max="20"' in single,
      "one place: one dot, zoom 20×")
# With the detailed map or OSM there is something to see under the dots even where no
# dot falls apart any more - it always zooms in up to 1024x.
check(f'data-max="{map_clusters.MAX_LEVEL}"' in charts.world_map(points[:1], detailed=True)
      and f'data-max="{map_clusters.MAX_LEVEL}"' in charts.world_map(points[:1], basemap="osm"),
      "with the detailed map or OSM it zooms in up to 1024× even for distant places")
import random  # noqa: E402

rng = random.Random(5)
europe = [dict(lat=rng.uniform(36, 60), lon=rng.uniform(-10, 30), seconds=rng.randint(1, 9999),
               plays=1, byte_count=0, viewers=frozenset({f"u{i % 30}"}), description=f"Mesto {i}, Zeme")
          for i in range(500)]
large = charts.world_map(europe)
check(len(large) < 300_000, f"500 places: the SVG under 300 kB ({len(large) // 1000} kB)")
check("Zatím žádná data" in charts.world_map([]), "no places: a text instead of the map")
hostile = [{**points[0], "description": "<script>alert(1)</script>"}]
check("<script>alert" not in charts.world_map(hostile), "the place's label is escaped")

map_js = (PROJECT / "jellyscope" / "static" / "map.js").read_text(encoding="utf-8") \
    if (PROJECT / "jellyscope" / "static" / "map.js").exists() else ""
base = (PROJECT / "jellyscope" / "templates" / "base.html").read_text(encoding="utf-8")
check('src="/static/map.js' in base and 'querySelector(".map")' not in base,
      "the map's script is in static/map.js, not in base.html")
check("dataset.s" in map_js and "dataset.maxima" in map_js,
      "map.js computes a dot's radius within the visible level")
check("dataset.from" in map_js and "dataset.max" in map_js and "dataset.world" in map_js,
      "map.js switches the layers and keeps the limits from the data")
# The detailed map's layers: map.js builds the class "v-" + layer from the keys of the stored tiles
# (detailed_map.py), so every layer it draws needs a rule `.map-detailed .v-<layer>` - a layer without one
# is drawn in the SVG default, black.
style = (PROJECT / "jellyscope" / "static" / "style.css").read_text(encoding="utf-8")
layers_js = re.search(r"LAYERS\s*=\s*\[([^\]]*)\]", map_js)
layers = re.findall(r'"([\w-]+)"', layers_js.group(1)) if layers_js else []
unstyled = [layer for layer in layers if not re.search(rf"\.map-detailed \.v-{re.escape(layer)}\b", style)]
check(len(layers) >= 6 and not unstyled,
      f"every layer of the detailed map has its rule in style.css ({len(layers)} layers, without one: {unstyled})")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
