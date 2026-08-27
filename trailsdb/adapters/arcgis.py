"""Shared machinery for ArcGIS-hosted sources.

Six of the remaining sources publish through ArcGIS -- DOC, USFS, NPS, Ontario,
BC, several Australian states -- and they all paginate the same way. This is the
part they share: walk ``/query`` with ``resultOffset`` until the server stops
saying there is more, writing one GeoJSON page per request.

One thing worth stating, because it changes the plan's dependency footprint: a
publisher offering an ArcGIS REST service can be read **without GDAL**. The plan
budgeted geodatabase parsing (and the optional ``geo`` extra) for USFS on the
strength of its 118 MB ``.gdb`` download; the same data comes back as GeoJSON
from ``f=geojson`` on the REST endpoint, so the stdlib path covers it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from ..fetch import FetchError
from ..formats import geojson
from ..manifest import PullManifest
from ..schema import Feature
from .base import Adapter


@dataclass(frozen=True, slots=True)
class ArcGisLayer:
    """One queryable layer, and how to read it."""

    key: str
    url: str
    kind: str = "hiking"
    official_status: str | None = None
    #: Identifier fields to try in order. These must survive a republish --
    #: OBJECTID is a row number and belongs last, if at all.
    id_fields: tuple[str, ...] = ()
    name_fields: tuple[str, ...] = ()
    extras_skip: tuple[str, ...] = field(default=("OBJECTID", "objectid", "Shape__Length"))


class ArcGisAdapter(Adapter):
    """Base for adapters that read ArcGIS Feature/Map services."""

    #: Server maxRecordCount is typically 1000-2000; stay under both.
    page_size = 1000
    #: Runaway-pagination backstop, far above any real layer.
    max_pages = 500
    country: str | None = None

    @property
    def layers(self) -> tuple[ArcGisLayer, ...]:  # pragma: no cover - abstract
        raise NotImplementedError

    # -- fetch ---------------------------------------------------------------

    def fetch(
        self, manifest: PullManifest, *, force: bool = False, limit: int | None = None
    ) -> None:
        total = 0
        for layer in self.layers:
            total += self.fetch_layer(layer, manifest, force=force, limit=limit)
        if total == 0:
            raise FetchError(
                f"{self.source.id}: every layer returned zero features -- the "
                f"service has moved or changed shape"
            )
        manifest.notes = f"features={total} pages={len(manifest.files)}"

    def fetch_layer(
        self, layer: ArcGisLayer, manifest: PullManifest, *, force: bool, limit: int | None
    ) -> int:
        target = self.raw_dir / layer.key
        target.mkdir(parents=True, exist_ok=True)
        pages = self.max_pages if limit is None else max(1, limit)

        total = 0
        for page in range(pages):
            url = (
                f"{layer.url}/query?where=1%3D1&outFields=*&outSR=4326&f=geojson"
                f"&resultOffset={page * self.page_size}&resultRecordCount={self.page_size}"
            )
            record = self.session.download(url, target / f"page-{page:04d}.geojson", force=force)
            manifest.add(record)

            payload = json.loads(Path(record.path).read_text(encoding="utf-8"))
            if "error" in payload:
                raise FetchError(f"{self.source.id}/{layer.key}: {payload['error']}")
            features = payload.get("features") or []
            total += len(features)

            # The service says explicitly when another page exists. Trusting only
            # a short page would silently truncate a layer whose count happens to
            # be an exact multiple of the page size.
            if not payload.get("exceededTransferLimit") and len(features) < self.page_size:
                break
        else:
            if limit is None:
                raise FetchError(
                    f"{self.source.id}/{layer.key}: hit the {pages}-page backstop "
                    f"-- resultOffset may not be honoured"
                )
        return total

    # -- normalize -----------------------------------------------------------

    def normalize(self, manifest: PullManifest) -> Iterator[Feature]:
        seen: set[str] = set()
        for layer in self.layers:
            directory = self.raw_dir / layer.key
            if not directory.exists():
                continue
            for path in sorted(directory.glob("page-*.geojson")):
                for raw in geojson.load_features(path):
                    feature = self.normalize_one(raw, layer, manifest, path)
                    if feature is not None and feature.id not in seen:
                        seen.add(feature.id)
                        yield feature

    def normalize_one(
        self, raw: dict[str, Any], layer: ArcGisLayer, manifest: PullManifest, path: Path
    ) -> Feature | None:
        geometry = geojson.line_geometry(raw.get("geometry"))
        if geometry is None:
            return None  # these layers routinely mix in point assets

        props = raw.get("properties") or {}
        local_id = self.stable_id(props, layer)
        if local_id is None:
            return None

        return self.build_feature(local_id, geometry, props, layer, manifest, path)

    def build_feature(
        self,
        local_id: str,
        geometry: dict[str, Any],
        props: dict[str, Any],
        layer: ArcGisLayer,
        manifest: PullManifest,
        path: Path,
    ) -> Feature:
        extras = {
            k: v for k, v in props.items() if v not in (None, "", " ") and k not in layer.extras_skip
        }
        extras["source_file"] = path.name
        extras["layer"] = layer.key
        return self.feature(
            local_id,
            geometry,
            manifest=manifest,
            kind=layer.kind,
            name=pick(props, layer.name_fields),
            official_status=layer.official_status,
            country=self.country,
            source_url=self.source.homepage or None,
            extras=extras,
        )

    def stable_id(self, props: dict[str, Any], layer: ArcGisLayer) -> str | None:
        for key in layer.id_fields:
            value = props.get(key)
            if value not in (None, "", " "):
                return str(value).strip("{}")
        return None


def pick(props: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = props.get(key)
        if value not in (None, "", " "):
            return str(value).strip()
    return None
