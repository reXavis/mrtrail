"""Geometry maths on raw GeoJSON coordinate arrays.

Pure stdlib on purpose: the pipeline has to run wherever packs are baked, and a
GDAL/shapely dependency is a much bigger ask than a haversine. Everything here
works on WGS84 lon/lat, the only CRS the master database stores.
"""

from __future__ import annotations

import math
from typing import Any, Iterable, Sequence

EARTH_RADIUS_KM = 6371.0088

# (west, south, east, north)
BBox = tuple[float, float, float, float]


def haversine_km(a: Sequence[float], b: Sequence[float]) -> float:
    """Great-circle distance between two [lon, lat] positions, in km."""
    lon1, lat1 = math.radians(a[0]), math.radians(a[1])
    lon2, lat2 = math.radians(b[0]), math.radians(b[1])
    dlon, dlat = lon2 - lon1, lat2 - lat1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(min(1.0, h)))


def lines_of(geometry: dict[str, Any]) -> list[list[Sequence[float]]]:
    """Normalize LineString / MultiLineString to a list of lines.

    A Point is one line of one position, so bbox and point counts work on spots
    and its length is zero, as it should be.
    """
    coords = geometry.get("coordinates") or []
    gtype = geometry.get("type")
    if gtype == "Point":
        return [[coords]] if coords else []
    if gtype == "LineString":
        return [coords] if coords else []
    return [line for line in coords if line]


def length_km(geometry: dict[str, Any]) -> float:
    """Planar-free length of a line geometry, in km.

    This is the number the whole size model rests on: km x measured KB/km gives
    both the master-database and the tile estimates.
    """
    total = 0.0
    for line in lines_of(geometry):
        for i in range(1, len(line)):
            total += haversine_km(line[i - 1], line[i])
    return total


def point_count(geometry: dict[str, Any]) -> int:
    return sum(len(line) for line in lines_of(geometry))


def bbox(geometry: dict[str, Any]) -> BBox:
    west = south = math.inf
    east = north = -math.inf
    for line in lines_of(geometry):
        for lon, lat, *_ in line:
            west = min(west, lon)
            east = max(east, lon)
            south = min(south, lat)
            north = max(north, lat)
    if west is math.inf:
        raise ValueError("cannot compute bbox of an empty geometry")
    return (west, south, east, north)


def bbox_union(boxes: Iterable[BBox]) -> BBox | None:
    west = south = math.inf
    east = north = -math.inf
    seen = False
    for w, s, e, n in boxes:
        seen = True
        west, south = min(west, w), min(south, s)
        east, north = max(east, e), max(north, n)
    return (west, south, east, north) if seen else None


def bbox_intersects(a: BBox, b: BBox) -> bool:
    """Overlap test used to cut a pack out of the master database.

    Selection only -- tippecanoe does the actual geometric clipping at tile
    edges, so a feature that merely grazes the pack bbox costs nothing but is
    also never wrongly dropped.
    """
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])


def bbox_pad(box: BBox, km: float) -> BBox:
    """Grow a bbox by roughly ``km`` in every direction.

    Packs are cut with a small pad so a route that leaves and re-enters the pack
    area does not come back as two disconnected stubs.
    """
    dlat = km / 111.32
    mid_lat = (box[1] + box[3]) / 2
    scale = max(math.cos(math.radians(mid_lat)), 0.01)
    dlon = km / (111.32 * scale)
    return (
        max(-180.0, box[0] - dlon),
        max(-90.0, box[1] - dlat),
        min(180.0, box[2] + dlon),
        min(90.0, box[3] + dlat),
    )


#: Decimal places kept when a geometry is written out. Six is about 11 cm at the
#: equator -- an order of magnitude finer than consumer GPS, and far finer than a
#: z14 tile can resolve. Sources that hand back full IEEE doubles (every ArcGIS
#: service does) spend roughly 39 bytes per position storing 17 significant
#: digits of a measurement good to a few metres; at six places that is 23. Since
#: geometry is ~87 % of the master database, this is the single largest lever on
#: its size, and it discards nothing a map can draw.
COORDINATE_PRECISION = 6


def round_geometry(geometry: dict[str, Any], precision: int = COORDINATE_PRECISION) -> dict[str, Any]:
    """Return the geometry with coordinates rounded to ``precision`` decimals."""
    gtype = geometry.get("type")
    coords = geometry.get("coordinates")
    if gtype == "Point":
        return {"type": gtype, "coordinates": _round_line([coords], precision)[0]}
    if gtype == "LineString":
        return {"type": gtype, "coordinates": _round_line(coords, precision)}
    if gtype == "MultiLineString":
        return {"type": gtype, "coordinates": [_round_line(ln, precision) for ln in coords or []]}
    return geometry


def _round_line(line: Any, precision: int) -> list[list[float]]:
    return [[round(float(p[0]), precision), round(float(p[1]), precision)] for p in line or []]


def simplify(line: Sequence[Sequence[float]], tolerance_m: float) -> list[Sequence[float]]:
    """Douglas-Peucker in degrees-scaled-to-metres.

    Used only where a source publishes absurdly dense geometry (some cadastral
    exports carry a vertex per metre). The measured 51.5 points/km of the Galicia
    routes layer is the target density; anything far above that is paying tile
    bytes for nothing.
    """
    if len(line) < 3 or tolerance_m <= 0:
        return list(line)
    tolerance_deg = tolerance_m / 111_320.0
    keep = [False] * len(line)
    keep[0] = keep[-1] = True
    stack = [(0, len(line) - 1)]
    while stack:
        start, end = stack.pop()
        if end <= start + 1:
            continue
        worst, worst_i = 0.0, start
        for i in range(start + 1, end):
            d = _perpendicular_distance_deg(line[i], line[start], line[end])
            if d > worst:
                worst, worst_i = d, i
        if worst > tolerance_deg:
            keep[worst_i] = True
            stack.append((start, worst_i))
            stack.append((worst_i, end))
    return [p for p, k in zip(line, keep) if k]


def _perpendicular_distance_deg(
    point: Sequence[float], start: Sequence[float], end: Sequence[float]
) -> float:
    # Longitude degrees shrink with latitude; scale them so the distance is
    # comparable in both axes before comparing against the tolerance.
    scale = math.cos(math.radians(point[1])) or 1e-6
    px, py = point[0] * scale, point[1]
    ax, ay = start[0] * scale, start[1]
    bx, by = end[0] * scale, end[1]
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))
