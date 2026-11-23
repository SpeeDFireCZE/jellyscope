"""Where people watch from: the home network or the internet, the viewers' places and countries.

Part of the statistics package (see jellyscope/stats/__init__.py)."""
from __future__ import annotations

from typing import Any

from .. import db
from .periods import _bounds


# Addresses that do not lead out of the network. It is not about security, only about telling "from the couch" from
# "from elsewhere" - so prefixes are enough and we do not deal with masks.
HOME_PREFIXES = ("10.", "192.168.", "127.", "172.16.", "172.17.", "172.18.",
                 "172.19.", "172.20.", "172.21.", "172.22.", "172.23.",
                 "172.24.", "172.25.", "172.26.", "172.27.", "172.28.",
                 "172.29.", "172.30.", "172.31.", "169.254.", "::1", "fe80:",
                 "fc", "fd")


def is_home(address: Any) -> bool:
    """Is the address from the home network?"""
    text = str(address or "").strip().lower()
    if not text:
        return False
    return text.startswith(HOME_PREFIXES)


def _traffic_by_address(days: int) -> list[dict[str, Any]]:
    """How much flowed from each address - the basis for the table and the map.

    Both pages need the same sum; it used to be two almost identical queries that differed only in the map already
    leaving the empty address out in SQL. Leaving it out in Python is the same and one query is left.
    """
    return db.query_all(
        """
        SELECT remote_address                         AS address,
               SUM(watched_seconds)                   AS seconds,
               SUM(watched_seconds * COALESCE(bitrate, 0)) / 8.0 AS byte_count,
               COUNT(*)                               AS plays,
               COUNT(DISTINCT user_id)                AS people,
               MAX(started_at)                        AS last_at
          FROM playback
         WHERE started_at >= ? AND started_at < ?
           AND watched_seconds > 0
         GROUP BY remote_address
         ORDER BY SUM(watched_seconds) DESC
        """,
        (*_bounds(days),),
    )


def watching_locations(days: int, limit: int = 20) -> dict[str, Any]:
    """A breakdown by where the plays came from.

    Why not a map: an address in the home network (192.168.x.x) marks no place in the world - it is the same in
    Prague and in Sydney. Only a public address can be placed geographically, and even that needs an offline GeoIP
    database. The split "at home / from the internet" is therefore the only thing that can be computed honestly
    from what we have.

    Imported history carries no address at all - neither Jellystat nor Playback Reporting send it. Such records
    are "from unknown".
    """
    rows = _traffic_by_address(days)

    groups = {"at_home": {"plays": 0, "byte_count": 0.0, "seconds": 0},
              "internet": {"plays": 0, "byte_count": 0.0, "seconds": 0},
              "unknown_origin": {"plays": 0, "byte_count": 0.0, "seconds": 0}}
    addresses = []
    for r in rows:
        if not (r["address"] or "").strip():
            where_to = "unknown_origin"
        elif is_home(r["address"]):
            where_to = "at_home"
        else:
            where_to = "internet"
        groups[where_to]["plays"] += int(r["plays"] or 0)
        groups[where_to]["byte_count"] += float(r["byte_count"] or 0)
        groups[where_to]["seconds"] += int(r["seconds"] or 0)
        if where_to != "unknown_origin" and len(addresses) < limit:
            addresses.append({
                "address": r["address"], "home": where_to == "at_home",
                "plays": int(r["plays"] or 0), "people": int(r["people"] or 0),
                "seconds": int(r["seconds"] or 0), "byte_count": int(r["byte_count"] or 0),
                "last_at": r["last_at"],
            })

    for group_values in groups.values():
        group_values["byte_count"] = int(group_values["byte_count"])
    return {"groups": groups, "urls": addresses}


def places(days: int) -> list[dict[str, Any]]:
    """All the places of the period plays came from - without a limit and without sorting.

    A place = one pair of coordinates from GeoIP. It is not rounded: GeoLite2 gives cities coordinates by districts
    (Prague has eleven) and rounding to a tenth of a degree made several dots of the same name out of one city.
    Merging by zoom is done only by map_clusters.clusters().

    **Only public addresses** are placed: one from the home network marks no place. When the GeoLite2 database is
    missing (or the library that can read it is missing), an empty list is returned and the page does not show
    the map at all.

    One query by address-viewer pairs: both the sums and the real people come from it. The count of people cannot
    be put together from COUNT(DISTINCT) by address - a person from two addresses would be two, three people from
    three addresses one. `viewers` is a set of user_id only for Python (the clusters unite it), it does not get into
    the page.
    """
    from .. import geoip

    if not geoip.is_available():
        return []

    addresses: dict[str, dict[str, Any]] = {}
    for r in db.query_all(
        """
        SELECT remote_address                         AS address,
               user_id,
               SUM(watched_seconds)                   AS seconds,
               SUM(watched_seconds * COALESCE(bitrate, 0)) / 8.0 AS byte_count,
               COUNT(*)                               AS plays
          FROM playback
         WHERE started_at >= ? AND started_at < ?
           AND watched_seconds > 0
           AND remote_address IS NOT NULL AND remote_address != ''
         GROUP BY remote_address, user_id
        """,
        (*_bounds(days),),
    ):
        address = str(r["address"] or "")
        # An empty address is an import (that carries none), a home one marks nothing.
        if not address or is_home(address):
            continue
        record = addresses.setdefault(address, {"plays": 0, "seconds": 0, "byte_count": 0.0,
                                                "viewers": set()})
        record["plays"] += int(r["plays"] or 0)
        record["seconds"] += int(r["seconds"] or 0)
        record["byte_count"] += float(r["byte_count"] or 0)
        if r["user_id"]:
            record["viewers"].add(str(r["user_id"]))

    by_coordinates: dict[tuple[float, float], dict[str, Any]] = {}
    for address, record in addresses.items():
        place = geoip.lookup(address)
        if not place:
            continue
        key = (float(place["lat"]), float(place["lon"]))
        point = by_coordinates.setdefault(key, {
            "lat": key[0], "lon": key[1],
            "city": place["city"], "country": place["country"], "code": place["code"],
            "plays": 0, "seconds": 0, "byte_count": 0.0, "url_count": 0, "viewers": set(),
        })
        point["plays"] += record["plays"]
        point["seconds"] += record["seconds"]
        point["byte_count"] += record["byte_count"]
        point["url_count"] += 1
        point["viewers"] |= record["viewers"]

    result = list(by_coordinates.values())
    for point in result:
        point["byte_count"] = int(point["byte_count"])
        point["viewers"] = frozenset(point["viewers"])
        point["people"] = len(point["viewers"])
        point["description"] = ", ".join(x for x in (point["city"], point["country"]) if x) or "?"
    return result


def hotspots(days: int, limit: int = 500,
             precomputed: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """The `limit` most active places for the map, from the most watched.

    `precomputed` are the places from places() - the Map card computes them once for the map and the table of
    countries. On a tie of time the coordinates decide, so that the order is the same every time and the clusters
    on the map do not jump.
    """
    every_place = precomputed if precomputed is not None else places(days)
    return sorted(every_place, key=lambda b: (-b["seconds"], b["lat"], b["lon"]))[:limit]


def viewer_countries(days: int,
                     precomputed: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """The same as hotspots, only summed by country - for the table beside the map.

    It sums over all places (the map draws only the 500 most active), and the "places" are different **cities**:
    Prague is one place, although it has a dot for every district on the map.
    """
    by_country: dict[str, dict[str, Any]] = {}
    for point in (precomputed if precomputed is not None else places(days)):
        record = by_country.setdefault(point["country"] or "?", {
            "label": point["country"] or "?", "code": point["code"],
            "plays": 0, "seconds": 0, "byte_count": 0, "cities": set(),
        })
        record["plays"] += point["plays"]
        record["seconds"] += point["seconds"]
        record["byte_count"] += point["byte_count"]
        record["cities"].add(point["description"])
    for record in by_country.values():
        record["places"] = len(record.pop("cities"))
    return sorted(by_country.values(), key=lambda z: z["seconds"], reverse=True)
