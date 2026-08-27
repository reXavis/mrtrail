"""Reading published GeoJSON, which is rarely as tidy as the spec suggests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator


class GeoJsonError(ValueError):
    pass


def load_features(source: Path | str | bytes | dict) -> list[dict[str, Any]]:
    """Return the feature list of a FeatureCollection, a bare Feature, or a list."""
    if isinstance(source, dict):
        obj = source
    elif isinstance(source, bytes):
        obj = json.loads(source.decode("utf-8"))
    else:
        with open(source, encoding="utf-8") as fh:
            obj = json.load(fh)

    if isinstance(obj, list):
        return obj
    obj_type = obj.get("type")
    if obj_type == "FeatureCollection":
        return obj.get("features") or []
    if obj_type == "Feature":
        return [obj]
    raise GeoJsonError(f"unsupported GeoJSON root type {obj_type!r}")


def line_geometry(geometry: dict[str, Any] | None) -> dict[str, Any] | None:
    """Coerce a published geometry to a 2D line geometry, or None if it is not one.

    Drops the third ordinate where a source ships XYZ: elevation is a bake-time
    concern and keeping it here would inflate every stored coordinate by ~40%.
    """
    if not geometry:
        return None
    gtype = geometry.get("type")
    coords = geometry.get("coordinates")
    if not coords:
        return None
    if gtype == "LineString":
        line = _strip_z(coords)
        return {"type": "LineString", "coordinates": line} if len(line) >= 2 else None
    if gtype == "MultiLineString":
        lines = [line for raw in coords if len(line := _strip_z(raw)) >= 2]
        if not lines:
            return None
        if len(lines) == 1:
            return {"type": "LineString", "coordinates": lines[0]}
        return {"type": "MultiLineString", "coordinates": lines}
    return None


def _strip_z(line: Any) -> list[list[float]]:
    out: list[list[float]] = []
    for position in line or []:
        if isinstance(position, (list, tuple)) and len(position) >= 2:
            out.append([float(position[0]), float(position[1])])
    return out


def iter_features(source: Path | str) -> Iterator[dict[str, Any]]:
    yield from load_features(source)
