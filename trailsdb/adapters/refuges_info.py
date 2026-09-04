"""refuges.info: huts, shelters, water points and passes for mountain walkers.

A collaborative, share-alike base (CC BY-SA 2.0) and the plan's one point
source. Its read-only API needs no key; ``/api/bbox`` returns GeoJSON points,
and every response carries the licence in its ``copyright`` line, which the
pull checks on every page so a licence change cannot slip past. The API's
*search* can return OpenStreetMap points under the ODbL; only ``/api/bbox`` is
used here, and it returns the site's own points.

The service describes itself as personal and non-commercial, so the pull is
gentle: a coarse grid of 4-degree cells over Europe, one request per cell at
one request per second, asking for every point in the cell rather than the
server's ranked 250. A quarterly refresh is about a hundred requests. Points
that straddle a cell edge come back twice and are deduplicated by id.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

from ..fetch import FetchError
from ..manifest import PullManifest
from ..schema import Feature
from .base import Adapter

BASE_URL = "https://www.refuges.info/api/bbox"
#: west, south, east, north -- the Atlantic to the Carpathians, Gibraltar to Lofoten.
GRID = (-12.0, 35.0, 32.0, 71.0)
CELL_DEG = 4.0
#: What the API promises in every response. Anything else stops the pull.
LICENCE_MARKERS = ("refuges.info", "cc by-sa 2.0")

_CATEGORY_BY_TYPE = {
    "cabane non gardée": "shelter",
    "abri": "shelter",
    "refuge gardé": "hut",
    "gîte d'étape": "gite",
    "bivouac": "bivouac",
    "point d'eau": "water",
    "sommet": "summit",
    "point de passage": "pass",
    "col": "pass",
    "lac": "lake",
}


def cells() -> list[tuple[float, float, float, float]]:
    west, south, east, north = GRID
    out = []
    lat = south
    while lat < north:
        lon = west
        while lon < east:
            out.append((lon, lat, min(lon + CELL_DEG, east), min(lat + CELL_DEG, north)))
            lon += CELL_DEG
        lat += CELL_DEG
    return out


def cell_name(cell: tuple[float, float, float, float]) -> str:
    west, south, _, _ = cell
    return f"cell_{west:+07.2f}_{south:+06.2f}.geojson"


class RefugesInfoAdapter(Adapter):
    name = "refuges_info"
    phase = "4 - Europe wave"

    def fetch(
        self, manifest: PullManifest, *, force: bool = False, limit: int | None = None
    ) -> None:
        grid = cells() if limit is None else cells()[: max(1, limit)]
        total = 0
        for cell in grid:
            west, south, east, north = cell
            url = f"{BASE_URL}?bbox={west},{south},{east},{north}&format=geojson&nb_points=all"
            record = self.session.download(url, self.raw_dir / cell_name(cell), force=force)
            manifest.add(record)
            payload = json.loads(Path(record.path).read_text(encoding="utf-8"))
            copyright_line = str(payload.get("copyright") or "").lower()
            if not all(marker in copyright_line for marker in LICENCE_MARKERS):
                raise FetchError(
                    f"{self.source.id}: the API's copyright line no longer says "
                    f"{LICENCE_MARKERS}: {payload.get('copyright')!r} -- re-read the terms"
                )
            total += len(payload.get("features") or [])
        manifest.notes = f"cells={len(grid)} points={total} (before cross-cell dedup)"

    def normalize(self, manifest: PullManifest) -> Iterator[Feature]:
        seen: set[str] = set()
        for path in sorted(self.raw_dir.glob("cell_*.geojson")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            for raw in payload.get("features") or []:
                feature = self.normalize_one(raw, manifest, path)
                if feature is not None and feature.id not in seen:
                    seen.add(feature.id)
                    yield feature

    def normalize_one(self, raw: dict[str, Any], manifest: PullManifest, path: Path) -> Feature | None:
        geometry = raw.get("geometry") or {}
        if geometry.get("type") != "Point" or not geometry.get("coordinates"):
            return None
        props = raw.get("properties") or {}
        local_id = props.get("id") or raw.get("id")
        if local_id in (None, ""):
            return None
        type_label = _value(props.get("type"), "valeur")
        extras: dict[str, Any] = {"source_file": path.name}
        if type_label:
            extras["type"] = type_label
        type_id = _value(props.get("type"), "id")
        if type_id is not None:
            extras["type_id"] = type_id
        altitude = _value(props.get("coord"), "alt")
        if altitude not in (None, ""):
            extras["altitude_m"] = altitude
        places = _value(props.get("places"), "valeur")
        if places not in (None, ""):
            extras["places"] = places
        state = _value(props.get("etat"), "valeur")
        if state:
            extras["state"] = state
        return self.feature(
            str(local_id),
            {"type": "Point", "coordinates": list(geometry["coordinates"][:2])},
            manifest=manifest,
            name=(props.get("nom") or "").strip() or None,
            category=category_for(type_label),
            source_url=props.get("lien") or None,
            extras=extras,
        )


def category_for(type_label: str | None) -> str:
    label = (type_label or "").strip().lower()
    for key, category in _CATEGORY_BY_TYPE.items():
        if label.startswith(key):
            return category
    return "other"


def _value(obj: Any, key: str) -> Any:
    """The API wraps most fields as {"nom": ..., "valeur": ...} objects."""
    if isinstance(obj, dict):
        return obj.get(key)
    return obj if key == "valeur" else None
