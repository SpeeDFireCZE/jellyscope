"""Clusters of dots on the map - for every zoom level separately.

From afar, ten places in one city (and the neighbouring cities) should merge
into one bigger dot; when zooming in they should fall apart, until at the end
every place from GeoIP stands alone. The clusters are computed here, in
Python, for the fixed levels 1×, 2×, 4× … 1024× - the map in the browser then
only switches layers. Labels, word forms and the counts of people can thus be
tested like everything else, and the real ids of viewers stay on the server.

Coordinates are in Web Mercator (worldmap.to_map), so a distance matches what
a person sees on the screen.
"""
from __future__ import annotations

from typing import Any

from . import formatting
from .worldmap import to_map

# How close places may be to still merge - in Mercator units when looking at
# the whole world (360 units across the card). With every zoom level the
# threshold halves: roughly "the dots would overlap on the screen".
CLUSTER_THRESHOLD = 8.0

# At most 1024× - GeoIP is not more precise beyond that anyway.
MAX_LEVEL = 1024


def _cluster(points: list[dict[str, Any]], threshold: float) -> list[list[dict[str, Any]]]:
    """Splits the points (sorted from the most watched) into groups.

    Each point joins the first group whose **founder** (the first, most
    watched point) is closer than the threshold, otherwise it founds a new
    one. It is compared with the founder, not with the centroid: a group thus
    cannot "creep", and the order of the result is stable.
    """
    groups: list[list[dict[str, Any]]] = []
    threshold2 = threshold * threshold
    for point in points:
        for group in groups:
            founder = group[0]
            dx = point["_x"] - founder["_x"]
            dy = point["_y"] - founder["_y"]
            if dx * dx + dy * dy < threshold2:
                group.append(point)
                break
        else:
            groups.append([point])
    return groups


def _cluster_of(group: list[dict[str, Any]]) -> dict[str, Any]:
    """One cluster from its places - sums, the centroid and a label."""
    seconds = sum(int(b.get("seconds") or 0) for b in group)
    # The centroid weighted by watched time: the dot lies where most was
    # watched. Without time (all zeros) a plain average.
    weights = [int(b.get("seconds") or 0) for b in group] if seconds else [1] * len(group)
    total = sum(weights)
    viewers: set[str] = set()
    for point in group:
        viewers |= set(point.get("viewers") or ())

    label = group[0].get("description") or "?"
    others = len({b.get("description") or "?" for b in group}) - 1
    if others:
        label = formatting.plural("{place} and {n} more places", others, place=label)
    return {
        "x": sum(b["_x"] * v for b, v in zip(group, weights)) / total,
        "y": sum(b["_y"] * v for b, v in zip(group, weights)) / total,
        "plays": sum(int(b.get("plays") or 0) for b in group),
        "seconds": seconds,
        "byte_count": sum(int(b.get("byte_count") or 0) for b in group),
        "people": len(viewers),
        "places": len(group),
        "description": label,
        # Which places it is made of (the order in the list of places) - by that
        # the drawing recognises it as the same cluster as one level lower.
        "members": tuple(sorted(b["_order"] for b in group)),
    }


def clusters(places: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Clusters for the zoom levels 1×, 2×, 4× … at most MAX_LEVEL.

    Returns `[{"level": z, "clusters": [...]}]` in ascending order. A level
    with the same clusters as the previous one is left out (the map takes the
    nearest lower one). It ends with the level on which every place stands
    alone - or MAX_LEVEL, when some places are so close they never part.

    The places are dicts from stats.hotspots (lat, lon, sekund, plays,
    bajtu, divaci, popis).
    """
    if not places:
        return []
    points = []
    for place in places:
        x, y = to_map(place["lat"], place["lon"])
        points.append({**place, "_x": x, "_y": y})
    # From the most watched; on a tie the coordinates decide, so the order is
    # the same every time.
    points.sort(key=lambda b: (-int(b.get("seconds") or 0), b["_x"], b["_y"]))
    for position, point in enumerate(points):
        point["_order"] = position

    levels: list[dict[str, Any]] = []
    previous: list[tuple[int, ...]] | None = None
    level = 1
    while level <= MAX_LEVEL:
        groups = _cluster(points, CLUSTER_THRESHOLD / level)
        # The level's fingerprint = who is with whom. The same as last time = nothing new.
        fingerprint = [tuple(id(b) for b in group) for group in groups]
        if fingerprint != previous:
            levels.append({"level": level, "clusters": [_cluster_of(s) for s in groups]})
            previous = fingerprint
        if len(groups) == len(points):
            break
        level *= 2
    return levels


# This far one can always zoom in - even on a map with one place (formerly a fixed limit).
MIN_ZOOM = 20


def max_zoom(levels: list[dict[str, Any]], place_count: int) -> int:
    """How far the map is to zoom in.

    Down to the last level on which every place stands alone. When some
    places do not part even at MAX_LEVEL (they are nearly on top of each
    other), the clusters do not have it as a level of its own - it is the
    same as the previous one -, but one should be able to zoom in there:
    the basemap under the dot is still to be seen.
    """
    if not levels:
        return MIN_ZOOM
    last = levels[-1]
    if len(last["clusters"]) < place_count:
        return MAX_LEVEL
    return max(MIN_ZOOM, last["level"])
