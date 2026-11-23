"""The detailed offline map: Natural Earth 1:10M cut into tiles.

The built-in outline of the land (worldmap, 1:110M) is enough for the whole
world, but coarse when zoomed in. The admin downloads the detailed map with a
click on the Network page - next to GeoLite2 it is the only moment the
application reaches the internet because of the map. A **pinned version**
(VERSION) is downloaded from the Natural Earth repository on GitHub; the data
are in the public domain.

The whole file is too big for the page (the land alone has 10 MB), so after
the download it is cut into tiles in two levels of detail (DETAIL_LEVELS) and
stored in the main database - it is backed up with it and the SQLite ->
PostgreSQL conversion carries it over. The browser then takes only the tiles of
the view a person looks at (web/network.py, /network/map/tiles/...).

Areas (land, lakes, cities) are drawn with a fill only, so no seams show on the
tiles' edges; lines (rivers, borders) are clipped as segments.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
from pathlib import Path
from typing import Any
from collections.abc import Iterator

from . import config, db, i18n
from .geometry import cut_line, cut_polygon, simplify
from .worldmap import to_map

log = logging.getLogger("jellyscope.detailed_map")

VERSION = "v5.1.2"
SOURCE = ("https://raw.githubusercontent.com/nvkelso/natural-earth-vector/"
          "{version}/geojson/{file}.geojson")

# The ceiling for one file. The biggest (city outlines) has 28 MB; more means
# something else than we expect is on the other side - and the disk is not to fill up.
MAX_BYTES = 40 * 1024 * 1024

# (file, layer, kind) - they are drawn in this order too.
FILES: list[tuple[str, str, str]] = [
    ("ne_10m_land", "land", "plocha"),
    ("ne_10m_lakes", "lakes", "plocha"),
    ("ne_10m_urban_areas", "cities", "plocha"),
    ("ne_10m_rivers_lake_centerlines", "rivers", "cara"),
    ("ne_10m_admin_0_boundary_lines_land", "borders", "cara"),
    ("ne_10m_admin_1_states_provinces_lines", "regions", "cara"),
]

# Detail 1 for zooming 8-127× (a 32 × 32 grid, simplified lines, without
# shapes smaller than `min_size`), detail 2 from 128× (a 128 × 128 grid,
# everything). `places` = decimal places of the coordinates: a hundredth of a unit
# is a few pixels at 127×, at 1024× the lines would jump, so four there.
#
# Measured on v5.1.2: with a 16 × 16 grid and a tolerance of 0.02 the densest
# tile (central Europe - the regions' borders and city outlines) had 514 kB; a
# view over Europe at 8× would download over two megabytes.
DETAIL_LEVELS = {
    1: {"n": 32, "tolerance": 0.04, "places": 2, "min_size": 0.08},
    2: {"n": 128, "tolerance": 0.0, "places": 4, "min_size": 0.0},
}


# The shape of the stored tiles: the format version and the grid size of each
# detail. When DETAIL_LEVELS change in a new version of the application, the
# old data in the database do not fit the grid (the tiles would shift) - it is
# then taken as if the map were not downloaded, and the card says to download it again.
FORMAT = "1:" + ":".join(str(DETAIL_LEVELS[p]["n"]) for p in sorted(DETAIL_LEVELS))
# The names of the layers inside a tile (land, lakes, ... - Czech up to the database step 5). Part of the tiles'
# address and ETag, not of FORMAT: a browser does not take a tile with the old names from its cache (it would
# draw nothing), and the downloaded map stays valid - nothing is downloaded again.
LAYER_NAMES = "en"


def _points(coordinates: Any) -> list[tuple[float, float]]:
    """GeoJSON [longitude, latitude] -> Mercator points. What is not a finite number goes."""
    points = []
    for point in coordinates or []:
        try:
            lon, lat = float(point[0]), float(point[1])
        except (TypeError, ValueError, IndexError):
            continue
        if math.isfinite(lon) and math.isfinite(lat):
            points.append(to_map(lat, lon))
    return points


def _shapes(geojson: Any, kind: str) -> Iterator[list[tuple[float, float]]]:
    """Rings of areas (holes too - drawn `evenodd`), or lines, from GeoJSON."""
    features = geojson.get("features", []) if isinstance(geojson, dict) else []
    for feature in features:
        geometry = (feature or {}).get("geometry") or {}
        geometry_type = geometry.get("type")
        coords = geometry.get("coordinates") or []
        if kind == "plocha":
            polygons = [coords] if geometry_type == "Polygon" else coords if geometry_type == "MultiPolygon" else []
            for polygon in polygons:
                for ring in polygon or []:
                    yield _points(ring)
        else:
            lines = [coords] if geometry_type == "LineString" else coords if geometry_type == "MultiLineString" else []
            for line in lines:
                yield _points(line)


def _path(piece: list[tuple[float, float]], decimals: int, close: bool) -> str:
    """One piece as SVG path commands."""
    text = "L".join(f"{x:.{decimals}f},{y:.{decimals}f}" for x, y in piece)
    return "M" + text + ("Z" if close else "")


def process(geojson: Any, layer: str, kind: str, tiles: dict) -> None:
    """Cuts a layer into the tiles of both details.

    Fills `tiles[(detail, x, y)][layer]` - a list of SVG path pieces; only the
    saving joins them.
    """
    is_area = kind == "plocha"
    cut = cut_polygon if is_area else cut_line
    for shape in _shapes(geojson, kind):
        if len(shape) < (3 if is_area else 2):
            continue
        xs = [point[0] for point in shape]
        ys = [point[1] for point in shape]
        size = max(max(xs) - min(xs), max(ys) - min(ys))
        for detail, settings in DETAIL_LEVELS.items():
            # An area smaller than a few pixels at this zoom only inflates
            # the tile - it is left out in the coarser detail. Lines not: a short
            # stretch of a river or a border is a link of a longer line and
            # without it a gap would stay in it.
            if is_area and size < settings["min_size"]:
                continue
            points = simplify(shape, settings["tolerance"]) if settings["tolerance"] else shape
            for (x, y), pieces in cut(points, settings["n"]).items():
                paths = tiles.setdefault((detail, x, y), {}).setdefault(layer, [])
                paths.extend(_path(piece, settings["places"], is_area) for piece in pieces)


def _folder() -> Path:
    """Where it is downloaded to for a while - next to the application's database (data/), like GeoLite2."""
    return config.load_config().database_path.parent


async def _download(client: Any, url: str, target: Path) -> None:
    """Downloads a file in pieces; above MAX_BYTES it ends with an error."""
    downloaded = 0
    async with client.stream("GET", url) as response:
        response.raise_for_status()
        with target.open("wb") as file:
            async for chunk in response.aiter_bytes(1 << 20):
                downloaded += len(chunk)
                if downloaded > MAX_BYTES:
                    raise i18n.TranslatableError("{file} is larger than {mb} MB",
                                                 file=url.rsplit("/", 1)[-1], mb=MAX_BYTES // (1024 * 1024))
                file.write(chunk)


def _load(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def _save(tiles: dict) -> tuple[int, int]:
    """Replaces the stored tiles with new ones - in one transaction.

    The old map holds until the new one is written whole: when writing
    fails, the transaction rolls back and the previous one stays.
    """
    rows = []
    byte_count = 0
    for (detail, x, y), layers in sorted(tiles.items()):
        content = json.dumps({layer: "".join(paths) for layer, paths in layers.items()},
                             separators=(",", ":"))
        byte_count += len(content)
        rows.append((detail, x, y, content))
    with db.connect() as conn:
        conn.execute("DELETE FROM detailed_map_tile")
        for start in range(0, len(rows), 500):
            conn.executemany(
                "INSERT INTO detailed_map_tile (detail_level, x, y, content) VALUES (?, ?, ?, ?)",
                rows[start:start + 500])
        conn.commit()
    db.set_setting("detailed_map_version", VERSION)
    db.set_setting("detailed_map_format", FORMAT)
    db.set_setting("detailed_map_at", db.utcnow())
    db.set_setting("detailed_map_tiles", str(len(rows)))
    db.set_setting("detailed_map_bytes", str(byte_count))
    return len(rows), byte_count


async def download_and_process(client: Any = None, stop: Any = None) -> dict[str, Any]:
    """Downloads all layers, cuts them and stores them. Started by the admin.

    File by file - only one fits into memory at a time. When anything fails
    (the network, the ceiling, broken JSON), nothing is written and the old map
    holds. The temporary files are always deleted.

    `stop` is a function that says between files whether the admin stopped
    the task (Settings → Tasks) - then it ends and nothing is written.
    """
    import httpx

    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=120, follow_redirects=True)
    tiles: dict = {}
    folder = _folder()
    try:
        folder.mkdir(parents=True, exist_ok=True)
        for file_name, layer, kind in FILES:
            if stop is not None and stop():
                log.info("detailed map download stopped")
                return {"status": "stopped"}
            temporary = folder / f"mapa-{file_name}.stahuje"
            try:
                await _download(client, SOURCE.format(version=VERSION, file=file_name), temporary)
                data = await asyncio.to_thread(_load, temporary)
                await asyncio.to_thread(process, data, layer, kind, tiles)
                del data
            finally:
                temporary.unlink(missing_ok=True)
        count, byte_count = await asyncio.to_thread(_save, tiles)
    except Exception as error:  # noqa: BLE001 - network, ceiling, JSON: always only a message
        log.warning("the detailed map could not be downloaded: %s", error)
        # "error" for the task log (a key and values), "message" for whoever waits for the answer.
        return {"status": "error", "message": i18n.error_text(error), "error": error}
    finally:
        if own_client:
            await client.aclose()
    log.info("detailed map downloaded: %s tiles, %s MB", count, round(byte_count / 1e6, 1))
    return {"status": "ok", "tile_count": count, "byte_count": byte_count}


async def start_task() -> None:
    """The download as a background task - under the task lock and with a record in the task log.

    It is started by the button on the Network page (web/network.py); the page then
    waits (`?wait=task`) and reloads when it is done.
    """
    from . import scanner

    async with scanner.task_lock():
        # A stop request from the previous task does not belong here - as with the others.
        scanner._clear_stop()
        scan_id = scanner.start_task_log("map")
        result = await download_and_process(stop=scanner.stop_requested)
        if result.get("status") == "stopped":
            scanner.finish_task_log(scan_id, "stopped",
                                    message="Stopped, the detailed map did not change.")
        elif result.get("status") == "ok":
            count = int(result.get("tile_count") or 0)
            scanner.finish_task_log(
                scan_id, "done", total=count, ok=count,
                message=i18n.Message("{n} tiles, {mb} MB", n=count, mb=round(int(result.get("byte_count") or 0) / 1e6, 1)))
        else:
            scanner.finish_task_log(scan_id, "error", message=result.get("error") or str(result.get("message", "")))


def tile(detail: int, x: int, y: int) -> str | None:
    """The content of one tile (JSON with paths by layers), or None."""
    return db.query_value(
        "SELECT content FROM detailed_map_tile WHERE detail_level = ? AND x = ? AND y = ?",
        (int(detail), int(x), int(y)), default=None)


def delete() -> None:
    """Deletes the detailed map. The space in the SQLite file comes back at maintenance (VACUUM)."""
    with db.connect() as conn:
        conn.execute("DELETE FROM detailed_map_tile")
        conn.commit()
    for key in ("detailed_map_version", "detailed_map_format", "detailed_map_at",
                "detailed_map_tiles", "detailed_map_bytes"):
        db.set_setting(key, "")
    log.info("detailed map deleted")


def last_error() -> dict[str, Any] | None:
    """The last download that failed - when it is newer than the downloaded map.

    The task runs in the background and the page only reloads after it;
    without this message the admin would see the "Stáhnout" button again after
    a failure and not know why.
    """
    from . import scanner

    last = scanner.last_scan("map")
    if not last or last["status"] != "error":
        return None
    if str(last["finished_at"] or "") <= str(db.get_setting("detailed_map_at", "") or ""):
        return None
    return {"at": last["finished_at"], "reason": i18n.stored_text(last["message"], last.get("message_values"))}


def state() -> dict[str, Any]:
    """What is downloaded - from the settings, not by a query over the whole table."""
    def number(key: str) -> int:
        try:
            return int(db.get_setting(key, "0") or 0)
        except ValueError:
            return 0

    when = db.get_setting("detailed_map_at", "") or ""
    right_format = (db.get_setting("detailed_map_format", "") or "") == FORMAT
    return {
        "version": db.get_setting("detailed_map_version", "") or "",
        "at": when,
        "tile_count": number("detailed_map_tiles"),
        "byte_count": number("detailed_map_bytes"),
        # Downloaded and in the shape this version of the application can draw.
        "valid": bool(when) and right_format,
        # Downloaded, but in an older shape - it needs downloading again.
        "outdated": bool(when) and not right_format,
        # Into the tiles' address: after a new download the browser does not
        # take the old tiles from its cache (it keeps them a week).
        "fingerprint": hashlib.sha256(f"{when}|{FORMAT}|{LAYER_NAMES}".encode()).hexdigest()[:12] if when else "",
    }
