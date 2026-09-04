"""Naturvårdsverket: Sweden's trails in protected areas and the state trail system.

Published as plain GeoJSON files on a directory index, under CC0 -- the
publisher's own description says data "kan användas utan begränsningar" and
asks only for a reference where possible. The files are in SWEREF99 TM
(EPSG:3006), reprojected here with the built-in transverse-Mercator inverse.

``Leder.geojson`` is the comprehensive set: 12,013 rows, but only 3,657 distinct
``LED_ID`` values -- one trail is published as 163 pieces. ``LED_ID`` therefore
identifies the *trail* and is the grouping key; each piece gets its own id from
the trail id plus its row number, the only per-piece identifier the file has.
Every row also carries a type (walking, ski, cycling, canoe...), a season
category (bare ground, snow, water) and the protected area it runs through.
State trails -- the *statliga leder* of the mountain regions -- are flagged by
``STATLIGLED_ID``, so the separate ``Statliga_Leder.geojson`` is not pulled: it
would be the same trails a second time.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

from ..fetch import FetchError
from ..formats import geojson
from ..manifest import PullManifest
from ..proj import to_wgs84
from ..schema import Feature
from .base import Adapter

BASE_URL = "https://geodata.naturvardsverket.se/nedladdning/friluftsliv/"
FILES = ("Leder.geojson",)

_KIND_BY_TYPE = {
    "vandringsled": "hiking",
    "skidled": "ski",
    "skoterled": "other",
    "cykelled": "cycling",
    "kanotled": "paddle",
    "ridled": "horse",
}
_KIND_BY_CATEGORY = {"led på snö": "ski", "led på/i vatten": "paddle"}


class NaturvardsverketAdapter(Adapter):
    name = "naturvardsverket"
    phase = "4 - Europe wave"

    def fetch(
        self, manifest: PullManifest, *, force: bool = False, limit: int | None = None
    ) -> None:
        for name in FILES:
            manifest.add(self.session.download(BASE_URL + name, self.raw_dir / name, force=force))
        if not manifest.files:
            raise FetchError(f"{self.source.id}: nothing downloaded")
        manifest.notes = f"files={len(manifest.files)}"

    def normalize(self, manifest: PullManifest) -> Iterator[Feature]:
        for name in FILES:
            path = self.raw_dir / name
            if not path.exists():
                continue
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            crs = str(((data.get("crs") or {}).get("properties") or {}).get("name") or "EPSG:4326")
            transform = to_wgs84(int(crs.rsplit(":", 1)[-1]))
            for raw in data.get("features") or []:
                feature = self._normalize_one(raw, transform, manifest, path)
                if feature is not None:
                    yield feature

    def _normalize_one(self, raw: dict[str, Any], transform, manifest, path: Path) -> Feature | None:
        geometry = geojson.line_geometry(raw.get("geometry"))
        if geometry is None:
            return None
        if transform is not None:
            geometry = _reproject(geometry, transform)

        props = raw.get("properties") or {}
        trail_id = props.get("LED_ID")
        if trail_id in (None, "", "None"):
            return None
        row_id = props.get("OBJECTID")
        local_id = f"{trail_id}-{row_id}" if row_id not in (None, "", "None") else str(trail_id)

        state_id = props.get("STATLIGLED_ID")
        is_state = state_id not in (None, "", "None")
        extras = {
            k: v
            for k, v in props.items()
            if v not in (None, "", "None") and k not in ("OBJECTID", "SHAPE.LEN", "LED_ID", "LEDNAMN")
        }
        extras["source_file"] = path.name

        name = _clean(props.get("LEDNAMN"))
        feature = self.feature(
            local_id,
            geometry,
            manifest=manifest,
            kind=_kind_for(props),
            name=name,
            official_status="sweden_statlig_led" if is_state else "sweden_led",
            country="SE",
            admin=_clean(props.get("SKYDDATOMRADE")),
            source_url=self.source.homepage or None,
            # The trail is the parent; its pieces hang off it.
            parent_id=self.make_id(f"led-{trail_id}"),
            parent_name=name,
            extras=extras,
        )
        if is_state:
            feature.ref = _clean(props.get("STATLIGLED"))
        return feature


def _reproject(geometry: dict[str, Any], transform) -> dict[str, Any]:
    def line(coords):
        return [list(transform(float(p[0]), float(p[1]))) for p in coords]

    if geometry["type"] == "LineString":
        return {"type": "LineString", "coordinates": line(geometry["coordinates"])}
    return {"type": "MultiLineString", "coordinates": [line(c) for c in geometry["coordinates"]]}


def _kind_for(props: dict[str, Any]) -> str:
    category = str(props.get("LEDKATEGORI") or "").strip().lower()
    if category in _KIND_BY_CATEGORY:
        return _KIND_BY_CATEGORY[category]
    trail_type = str(props.get("LEDTYP") or "").strip().lower()
    for needle, kind in _KIND_BY_TYPE.items():
        if needle in trail_type:
            return kind
    return "hiking"


def _clean(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text if text and text != "None" else None
