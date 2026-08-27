"""New Zealand Department of Conservation tracks.

The easy end of the spectrum and a deliberate counterweight to CNIG: one bulk
pull, a single flat schema, ~14,000 km, and it lands the pattern on a third
continent before any of it is generalised.

DOC publishes through an ArcGIS Hub, so the fetch is a paginated FeatureServer
query. DOC announced endpoint changes for 2026 -- the health check in the
registry and the loud failure on an empty page are what catch that at the
quarterly refresh instead of at bake time.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

from ..fetch import FetchError
from ..formats import geojson
from ..manifest import PullManifest
from ..schema import Feature
from .base import Adapter

# --- UNVERIFIED: confirm the current layer URL on the DOC open-data hub ---------
SERVICE_URL = (
    "https://services1.arcgis.com/Y1UutrPYYNqAtpaP/arcgis/rest/services/"
    "DOC_Tracks/FeatureServer/0/query"
)
# -------------------------------------------------------------------------------

PAGE_SIZE = 2000
MAX_PAGES = 200  # 400k features; a runaway-pagination backstop, not a real limit

_ID_KEYS = ("assetId", "ASSETID", "OBJECTID", "objectid", "id", "trackId")
_NAME_KEYS = ("name", "NAME", "trackName", "TrackName", "assetName")
_REGION_KEYS = ("region", "REGION", "conservancy", "district")
_STATUS_KEYS = ("walkTrackCategory", "trackCategory", "category", "difficulty")

#: DOC's own track categories, mapped to the normalized kind vocabulary.
_KIND_BY_CATEGORY = {
    "easiest short walk": "hiking",
    "short walk": "hiking",
    "walking track": "hiking",
    "easy tramping track": "hiking",
    "great walk": "hiking",
    "tramping track": "hiking",
    "route": "hiking",
    "cycle trail": "cycling",
    "great ride": "cycling",
    "mountain bike": "mtb",
    "horse": "horse",
}


class NzDocAdapter(Adapter):
    name = "nz_doc"
    phase = "2 - prove it on three continents"

    def fetch(
        self, manifest: PullManifest, *, force: bool = False, limit: int | None = None
    ) -> None:
        target = self.raw_dir / "pages"
        target.mkdir(parents=True, exist_ok=True)
        max_pages = MAX_PAGES if limit is None else max(1, limit)

        total = 0
        for page in range(max_pages):
            offset = page * PAGE_SIZE
            url = (
                f"{SERVICE_URL}?where=1%3D1&outFields=*&outSR=4326&f=geojson"
                f"&resultOffset={offset}&resultRecordCount={PAGE_SIZE}"
            )
            record = self.session.download(url, target / f"tracks-{page:04d}.geojson", force=force)
            manifest.add(record)

            features = geojson.load_features(Path(record.path))
            total += len(features)
            if len(features) < PAGE_SIZE:
                break
        else:
            raise FetchError(
                f"{self.source.id}: hit the {MAX_PAGES}-page backstop -- pagination "
                f"is not terminating, check whether resultOffset is being honoured"
            )

        if total == 0:
            raise FetchError(
                f"{self.source.id}: the service returned zero features. DOC announced "
                f"2026 endpoint changes -- check SERVICE_URL in this module."
            )
        manifest.notes = f"features={total} pages={len(manifest.files)}"

    def normalize(self, manifest: PullManifest) -> Iterator[Feature]:
        seen: set[str] = set()
        for path in sorted((self.raw_dir / "pages").glob("*.geojson")):
            for raw in geojson.load_features(path):
                feature = self._normalize_one(raw, manifest, path)
                if feature is not None and feature.id not in seen:
                    seen.add(feature.id)
                    yield feature

    def _normalize_one(
        self, raw: dict[str, Any], manifest: PullManifest, path: Path
    ) -> Feature | None:
        geometry = geojson.line_geometry(raw.get("geometry"))
        if geometry is None:
            return None  # DOC ships some point assets (huts, carparks) in the same layer

        props = raw.get("properties") or {}
        local_id = _pick(props, _ID_KEYS) or raw.get("id")
        if local_id is None:
            return None
        name = _pick(props, _NAME_KEYS)
        category = _pick(props, _STATUS_KEYS)

        extras = {k: v for k, v in props.items() if v not in (None, "") and k not in _ID_KEYS}
        extras["source_file"] = path.name

        return self.feature(
            local_id,
            geometry,
            manifest=manifest,
            kind=_kind_for(category, self.source.default_kind),
            name=str(name) if name else None,
            official_status=str(category) if category else None,
            country="NZ",
            admin=str(_pick(props, _REGION_KEYS) or "") or None,
            source_url=self.source.homepage or None,
            extras=extras,
        )


def _pick(props: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = props.get(key)
        if value not in (None, ""):
            return value
    return None


def _kind_for(category: Any, default: str) -> str:
    if not category:
        return default
    text = str(category).strip().lower()
    for needle, kind in _KIND_BY_CATEGORY.items():
        if needle in text:
            return kind
    return default


def write_page(path: Path, features: list[dict[str, Any]]) -> None:
    """Test/seed helper: write a FeatureCollection page in the shape fetch produces."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"type": "FeatureCollection", "features": features}), encoding="utf-8"
    )
