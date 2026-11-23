"""Geometry for the detailed map: simplifying lines and cutting into tiles.

The detailed map (Natural Earth 1:10M, see detailed_map.py) is too big for
the whole page. So when it is downloaded it is cut into tiles - an n × n
grid over the world of 360 × 360 Web Mercator units - and the browser takes
only the tiles of the view. All in plain Python, without a library.

Points are pairs (x, y). A polygon is a closed ring (the last point joins
the first by itself, repeating it is not needed), a line is a polyline.
"""
from __future__ import annotations

from collections.abc import Callable

Point = tuple[float, float]
Piece = list[Point]

# The world in Web Mercator (worldmap.WIDTH, HEIGHT).
WORLD = 360.0


def simplify(points: list[Point], tolerance: float) -> list[Point]:
    """Douglas-Peucker: leaves out points that turn away from the line by less than the tolerance.

    Without recursion - a coastline has hundreds of thousands of points and
    recursion would hit the depth limit. The end points always stay.
    """
    if len(points) < 3:
        return list(points)
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    tolerance2 = tolerance * tolerance
    stack = [(0, len(points) - 1)]
    while stack:
        start, end = stack.pop()
        ax, ay = points[start]
        bx, by = points[end]
        dx, dy = bx - ax, by - ay
        length2 = dx * dx + dy * dy
        farthest, where = -1.0, -1
        for i in range(start + 1, end):
            px, py = points[i]
            if length2 == 0:
                distance = (px - ax) ** 2 + (py - ay) ** 2
            else:
                t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length2))
                distance = (px - ax - t * dx) ** 2 + (py - ay - t * dy) ** 2
            if distance > farthest:
                farthest, where = distance, i
        if farthest > tolerance2:
            keep[where] = True
            stack.append((start, where))
            stack.append((where, end))
    return [point for point, kept in zip(points, keep) if kept]


def _intersection(a: Point, b: Point, axis: int, value: float) -> Point:
    """Where the segment a-b crosses the line axis = value. Exactly the value on the axis -
    otherwise a piece would stick out of the tile by a hair because of rounding."""
    t = (value - a[axis]) / (b[axis] - a[axis])
    other_axis = 1 - axis
    other_value = a[other_axis] + t * (b[other_axis] - a[other_axis])
    return (value, other_value) if axis == 0 else (other_value, value)


def _clip_polygon(ring: Piece, axis: int, value: float, lower: bool) -> list[Piece]:
    """Sutherland-Hodgman for one edge: the part of the ring on one side."""
    def inside(point: Point) -> bool:
        return point[axis] <= value if lower else point[axis] >= value

    result: Piece = []
    previous = ring[-1]
    previous_inside = inside(previous)
    for point in ring:
        point_inside = inside(point)
        if point_inside:
            if not previous_inside:
                result.append(_intersection(previous, point, axis, value))
            result.append(point)
        elif previous_inside:
            result.append(_intersection(previous, point, axis, value))
        previous, previous_inside = point, point_inside
    return [result] if len(result) >= 3 else []


def _clip_line(line: Piece, axis: int, value: float, lower: bool) -> list[Piece]:
    """The parts of a line on one side - one line can give several."""
    def inside(point: Point) -> bool:
        return point[axis] <= value if lower else point[axis] >= value

    pieces: list[Piece] = []
    current: Piece = [line[0]] if inside(line[0]) else []
    for previous, point in zip(line, line[1:]):
        a, b = inside(previous), inside(point)
        if a and b:
            current.append(point)
        elif a:
            current.append(_intersection(previous, point, axis, value))
            pieces.append(current)
            current = []
        elif b:
            current = [_intersection(previous, point, axis, value), point]
    if current:
        pieces.append(current)
    return [piece for piece in pieces if len(piece) >= 2]


Clip = Callable[[Piece, int, float, bool], list[Piece]]


def _split(pieces: list[Piece], clip: Clip, value: float, axis: int) -> tuple[list[Piece], list[Piece]]:
    """Pieces into two halves by the line axis = value.

    A piece wholly on one side goes on without clipping - a big outline
    (Eurasia) is thus cut only where the border really runs through it.
    """
    below: list[Piece] = []
    above: list[Piece] = []
    for piece in pieces:
        coords = [point[axis] for point in piece]
        if max(coords) <= value:
            below.append(piece)
        elif min(coords) >= value:
            above.append(piece)
        else:
            below.extend(clip(piece, axis, value, True))
            above.extend(clip(piece, axis, value, False))
    return below, above


def _cut(pieces: list[Piece], n: int, clip: Clip) -> dict[tuple[int, int], list[Piece]]:
    """Pieces into an n × n grid: the window is halved vertically and horizontally in turns.

    Each piece is thus gone through only as many times as there are levels of
    halving (fourteen for a 128 × 128 grid), not once for every tile.
    """
    size = WORLD / n
    # First clipping to the world - what lies outside (say because of
    # rounding) belongs to no tile.
    for axis in (0, 1):
        pieces = _split(pieces, clip, 0.0, axis)[1]
        pieces = _split(pieces, clip, WORLD, axis)[0]

    result: dict[tuple[int, int], list[Piece]] = {}
    stack = [(pieces, 0, 0, n, n)]
    while stack:
        part, x, y, width, height = stack.pop()
        if not part:
            continue
        if width == 1 and height == 1:
            result.setdefault((x, y), []).extend(part)
            continue
        if width >= height:
            half = width // 2
            left, right = _split(part, clip, (x + half) * size, 0)
            stack.append((left, x, y, half, height))
            stack.append((right, x + half, y, width - half, height))
        else:
            half = height // 2
            top, bottom = _split(part, clip, (y + half) * size, 1)
            stack.append((top, x, y, width, half))
            stack.append((bottom, x, y + half, width, height - half))
    return result


def cut_polygon(points: list[Point], n: int) -> dict[tuple[int, int], list[Piece]]:
    """A closed ring cut into an n × n grid: {(x, y): [pieces]}.

    The pieces are closed rings again. A polygon that covers a whole tile
    gives its whole square in it.
    """
    ring = list(points)
    if len(ring) > 1 and ring[0] == ring[-1]:
        ring.pop()
    if len(ring) < 3:
        return {}
    return _cut([ring], n, _clip_polygon)


def cut_line(points: list[Point], n: int) -> dict[tuple[int, int], list[Piece]]:
    """A polyline cut into an n × n grid: {(x, y): [pieces]}."""
    if len(points) < 2:
        return {}
    return _cut([list(points)], n, _clip_line)
