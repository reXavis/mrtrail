"""Shared machinery for OGC WFS 2.0 sources.

The publishers that do not run ArcGIS very often run GeoServer or deegree
behind a WFS. Two of them matter now: Kartverket (GML only) and DataBC (JSON,
but only with an explicit sort). This base walks ``GetFeature`` with
``startIndex``/``count`` and hands each page to a JSON or GML reader.

``resultType=hits`` is asked first so the page count is known up front and a
truncated pull is visible as one, rather than silently short.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlencode

from ..fetch import FetchError
from ..formats import geojson, gml
from ..manifest import PullManifest
from ..schema import Feature
from .base import Adapter


@dataclass(frozen=True, slots=True)
class WfsLayer:
    key: str
    type_name: str
    kind: str = "hiking"
    official_status: str | None = None
    id_fields: tuple[str, ...] = ()
    name_fields: tuple[str, ...] = ()
    #: "json" for servers that honour outputFormat=application/json, else "gml".
    output: str = "json"
    #: Required by servers that refuse to page without a stable order.
    sort_by: str | None = None
    extras_skip: tuple[str, ...] = ()


class WfsAdapter(Adapter):
    base_url: str = ""
    page_size = 1000
    max_pages = 2000
    country: str | None = None

    @property
    def layers(self) -> tuple[WfsLayer, ...]:  # pragma: no cover - abstract
        raise NotImplementedError

    # -- fetch ---------------------------------------------------------------

    def _query(self, layer: WfsLayer, **extra: Any) -> str:
        params: dict[str, Any] = {
            "service": "WFS",
            "version": "2.0.0",
            "request": "GetFeature",
            "typeNames": layer.type_name,
            "srsName": "EPSG:4326",
        }
        if layer.output == "json":
            params["outputFormat"] = "application/json"
        if layer.sort_by:
            params["sortBy"] = layer.sort_by
        params.update(extra)
        return f"{self.base_url}?{urlencode(params)}"

    def count(self, layer: WfsLayer) -> int | None:
        response = self.session.get(self._query(layer, resultType="hits"))
        if response.status_code != 200:
            return None
        return gml.number_matched(response.content)

    def fetch(
        self, manifest: PullManifest, *, force: bool = False, limit: int | None = None
    ) -> None:
        total = 0
        for layer in self.layers:
            total += self.fetch_layer(layer, manifest, force=force, limit=limit)
        if total == 0:
            raise FetchError(f"{self.source.id}: every layer returned zero features")
        manifest.notes = f"features={total} pages={len(manifest.files)}"

    def fetch_layer(
        self, layer: WfsLayer, manifest: PullManifest, *, force: bool, limit: int | None
    ) -> int:
        target = self.raw_dir / layer.key
        target.mkdir(parents=True, exist_ok=True)
        expected = self.count(layer)
        pages = self.max_pages if limit is None else max(1, limit)
        suffix = "geojson" if layer.output == "json" else "gml"

        total = 0
        for page in range(pages):
            url = self._query(layer, startIndex=page * self.page_size, count=self.page_size)
            record = self.session.download(url, target / f"page-{page:04d}.{suffix}", force=force)
            manifest.add(record)
            payload = Path(record.path).read_bytes()
            got = self._page_count(payload, layer)
            total += got
            if got < self.page_size:
                break
        else:
            if limit is None:
                raise FetchError(f"{self.source.id}/{layer.key}: hit the {pages}-page backstop")

        if expected is not None and limit is None and total < expected:
            raise FetchError(
                f"{self.source.id}/{layer.key}: server reported {expected:,} features "
                f"but the pull yielded {total:,} -- paging is truncating"
            )
        return total

    def _page_count(self, payload: bytes, layer: WfsLayer) -> int:
        if layer.output == "json":
            data = json.loads(payload)
            if "features" not in data:
                raise FetchError(f"{self.source.id}/{layer.key}: {str(data)[:200]}")
            return len(data["features"])
        return len(gml.parse(payload))

    # -- normalize -----------------------------------------------------------

    def normalize(self, manifest: PullManifest) -> Iterator[Feature]:
        seen: set[str] = set()
        for layer in self.layers:
            directory = self.raw_dir / layer.key
            if not directory.exists():
                continue
            suffix = "geojson" if layer.output == "json" else "gml"
            # Suffix-specific: every page has a .meta.json sidecar beside it,
            # and a sidecar read as a page is "unsupported GeoJSON root type".
            for path in sorted(directory.glob(f"page-*.{suffix}")):
                for attrs, geometry in self._read_page(path, layer):
                    feature = self.normalize_one(attrs, geometry, layer, manifest, path)
                    if feature is not None and feature.id not in seen:
                        seen.add(feature.id)
                        yield feature

    def _read_page(self, path: Path, layer: WfsLayer):
        if layer.output == "json":
            for raw in geojson.load_features(path):
                yield (raw.get("properties") or {}), geojson.line_geometry(raw.get("geometry"))
        else:
            for feature in gml.parse(path.read_bytes()):
                yield feature.attributes, feature.geometry

    def normalize_one(self, attrs, geometry, layer: WfsLayer, manifest, path) -> Feature | None:
        if geometry is None:
            return None
        local_id = self.stable_id(attrs, layer)
        if local_id is None:
            return None
        return self.build_feature(local_id, geometry, attrs, layer, manifest, path)

    def stable_id(self, attrs: dict[str, Any], layer: WfsLayer) -> str | None:
        for key in layer.id_fields:
            value = attrs.get(key)
            if value not in (None, "", " "):
                return str(value).strip("{}")
        return None

    def build_feature(self, local_id, geometry, attrs, layer, manifest, path) -> Feature:
        extras = {k: v for k, v in attrs.items() if v not in (None, "", " ") and k not in layer.extras_skip}
        extras["layer"] = layer.key
        name = next((str(attrs[k]).strip() for k in layer.name_fields if attrs.get(k)), None)
        return self.feature(
            local_id,
            geometry,
            manifest=manifest,
            kind=layer.kind,
            name=name,
            official_status=layer.official_status,
            country=self.country,
            source_url=self.source.homepage or None,
            extras=extras,
        )
