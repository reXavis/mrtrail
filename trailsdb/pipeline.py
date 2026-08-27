"""Orchestration: what the CLI actually runs.

Four stages, each independently re-runnable:

``pull``      network -> ``raw/{source}/``
``normalize`` ``raw/`` -> ``normalized/{source}.geojsonl.gz`` + catalog rows
``export``    master -> per-pack layer files ready for tippecanoe
``health``    is every source still where the registry says it is?

The split matters operationally: pull is the 1-2 day job, normalize is seconds
to minutes and gets re-run constantly while an adapter is being written, and
export is per-pack and runs on every release.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from . import geo, geojsonl, sizing
from .adapters import AdapterContext, AdapterNotImplemented, build, is_implemented, phase_of
from .catalog import Catalog, SourceStats
from .config import Paths
from .fetch import PoliteSession
from .geo import BBox
from .manifest import PullManifest
from .registry import Registry, Source

#: Packs are cut with a small pad so a route that briefly leaves the box does not
#: come back as two disconnected stubs at the edge.
PACK_PAD_KM = 5.0


# --------------------------------------------------------------------- pull --


def make_session(source: Source, *, transport: Any | None = None, **overrides: Any) -> PoliteSession:
    """A session paced for this source. CNIG gets the slow lane; bulk pulls do not."""
    kwargs: dict[str, Any] = {"rate_limit_s": source.rate_limit_s, "transport": transport}
    kwargs.update(overrides)
    return PoliteSession(**kwargs)


def pull_source(
    source: Source,
    paths: Paths,
    *,
    force: bool = False,
    limit: int | None = None,
    resume: bool = False,
    session: PoliteSession | None = None,
    catalog: Catalog | None = None,
) -> PullManifest:
    session = session or make_session(source)
    if resume:
        # Skip anything already on disk rather than re-checking it with the
        # server. A USFS page is ~12 MB and there are 87 of them.
        session.revalidate = False
    adapter = build(AdapterContext(source=source, paths=paths, session=session))
    manifest = adapter.pull(force=force, limit=limit)
    if catalog is not None:
        catalog.upsert_source(source)
        catalog.record_pull(manifest)
    return manifest


# ---------------------------------------------------------------- normalize --


@dataclass(slots=True)
class NormalizeResult:
    source_id: str
    features: int
    length_km: float
    points: int
    path: Path
    bytes_written: int

    @property
    def points_per_km(self) -> float:
        return self.points / self.length_km if self.length_km else 0.0

    @property
    def kb_per_km(self) -> float:
        return (self.bytes_written / 1024) / self.length_km if self.length_km else 0.0


def normalize_source(
    source: Source, paths: Paths, *, catalog: Catalog | None = None
) -> NormalizeResult:
    """Rebuild one source's slice of the master database from its raw tier."""
    adapter = build(
        AdapterContext(source=source, paths=paths, session=make_session(source))
    )
    manifest = adapter.load_manifest()
    out_path = paths.normalized_path(source.id)

    # Two streaming passes rather than one buffered one. The file is written
    # straight from the adapter, then read back to fill the catalog -- USFS alone
    # is ~257,000 km of segments and will not fit in a list.
    count = geojsonl.write(out_path, adapter.normalize(manifest))

    stats: SourceStats | None = None
    if catalog is not None:
        catalog.upsert_source(source)
        stats = catalog.replace_features(source.id, geojsonl.read(out_path))
        length, points = stats.length_km, stats.points
    else:
        length = points = 0
        for feature in geojsonl.read(out_path):
            length += geo.length_km(feature.geometry)
            points += geo.point_count(feature.geometry)
    return NormalizeResult(
        source_id=source.id,
        features=count,
        length_km=length,
        points=points,
        path=out_path,
        bytes_written=out_path.stat().st_size if out_path.exists() else 0,
    )


# ------------------------------------------------------------------- export --


@dataclass(slots=True)
class LayerExport:
    layer: str
    path: Path
    features: int
    length_km: float
    feature_class: str
    sources: list[str] = field(default_factory=list)

    @property
    def estimated_tiles_mb(self) -> float:
        """Estimated tile bytes for this layer, on the settings it is actually baked with.

        ``tippecanoe_args`` stops segment layers at z13, so the estimate has to
        assume the same lever -- charging them the z14 rate would overstate every
        segment-heavy pack by a factor of two, which is exactly the number the
        Alps and US cases turn on.
        """
        return sizing.estimate(
            self.length_km,
            feature_class=self.feature_class,
            cap_segments_at_z13=True,
        ).tiles_mb


@dataclass(slots=True)
class ExportResult:
    pack: str
    bbox: BBox
    layers: list[LayerExport]
    attributions: list[dict[str, str]]
    skipped: list[tuple[str, str]] = field(default_factory=list)

    @property
    def total_features(self) -> int:
        return sum(layer.features for layer in self.layers)

    @property
    def total_km(self) -> float:
        return sum(layer.length_km for layer in self.layers)

    @property
    def estimated_tiles_mb(self) -> float:
        return sum(layer.estimated_tiles_mb for layer in self.layers)


def layer_for(source: Source) -> str:
    """Which tile source-layer a source's features belong to.

    Share-alike sources get a layer to themselves. That is not tidiness -- mixing
    a share-alike source into a shared layer would drag the whole layer under its
    terms. Everything else splits by feature class: browsable routes into
    ``official``, network infrastructure into ``official_net``.
    """
    if source.license.share_alike:
        return source.id
    return "official" if source.feature_class == "route" else "official_net"


def export_pack(
    registry: Registry,
    paths: Paths,
    *,
    pack: str,
    bbox: BBox,
    sources: Iterable[Source] | None = None,
    allow_unverified: bool = False,
    pad_km: float = PACK_PAD_KM,
) -> ExportResult:
    """Cut every source down to one pack's bbox, grouped into tile layers."""
    selected = list(sources if sources is not None else registry)
    padded = geo.bbox_pad(bbox, pad_km)
    out_dir = paths.export_dir(pack)
    out_dir.mkdir(parents=True, exist_ok=True)

    # One open writer per layer, fed by every source that lands in it. Nothing is
    # buffered: the Alps cut is ~105,000 km of segments and would not fit.
    writers: dict[str, geojsonl.Writer] = {}
    layer_class: dict[str, str] = {}
    layer_sources: dict[str, list[str]] = {}
    layer_km: dict[str, float] = {}
    attributions: dict[str, dict[str, str]] = {}
    skipped: list[tuple[str, str]] = []

    try:
        for source in selected:
            if not source.verified and not allow_unverified:
                # The gate that keeps a guess in the registry out of a shipped pack.
                skipped.append((source.id, "license/attribution not verified"))
                continue
            path = paths.normalized_path(source.id)
            if not path.exists():
                skipped.append((source.id, "not normalized yet"))
                continue

            layer = layer_for(source)
            if layer not in writers:
                writers[layer] = geojsonl.Writer(out_dir / f"{layer}.geojsonl")
                layer_class[layer] = source.feature_class
                layer_sources[layer] = []
                layer_km[layer] = 0.0
            kept = 0
            for feature in geojsonl.read(path):
                if not geo.bbox_intersects(geo.bbox(feature.geometry), padded):
                    continue
                writers[layer].add(feature)
                layer_km[layer] += geo.length_km(feature.geometry)
                kept += 1

            if kept:
                # A source only joins a layer's credit line if it actually put
                # something in it. Crediting a publisher on a pack that carries
                # none of their data is exactly the kind of wrong the registry
                # exists to prevent.
                if source.id not in layer_sources[layer]:
                    layer_sources[layer].append(source.id)
                attributions[source.id] = {
                    "source": source.id,
                    "name": source.name,
                    "publisher": source.publisher,
                    "license": source.license.id,
                    "license_name": source.license.name,
                    "license_url": source.license.url,
                    "attribution": source.attribution,
                    "layer": layer,
                }
    except BaseException:
        for writer in writers.values():
            writer.abort()
        raise

    layers: list[LayerExport] = []
    for layer, writer in sorted(writers.items()):
        count = writer.close()
        if count == 0:
            # A source can be selected and contribute nothing to this bbox.
            writer.path.unlink(missing_ok=True)
            continue
        layers.append(
            LayerExport(
                layer=layer,
                path=writer.path,
                features=count,
                length_km=layer_km[layer],
                feature_class=layer_class[layer],
                sources=layer_sources[layer],
            )
        )

    result = ExportResult(
        pack=pack,
        bbox=bbox,
        layers=layers,
        attributions=list(attributions.values()),
        skipped=skipped,
    )
    _write_export_manifest(out_dir, result)
    return result


def _write_export_manifest(out_dir: Path, result: ExportResult) -> None:
    (out_dir / "attribution.json").write_text(
        json.dumps({"pack": result.pack, "sources": result.attributions}, indent=2),
        encoding="utf-8",
    )
    (out_dir / "export.json").write_text(
        json.dumps(
            {
                "pack": result.pack,
                "bbox": list(result.bbox),
                "layers": [
                    {
                        "layer": layer.layer,
                        "file": layer.path.name,
                        "feature_class": layer.feature_class,
                        "features": layer.features,
                        "length_km": round(layer.length_km, 1),
                        "estimated_tiles_mb": round(layer.estimated_tiles_mb, 1),
                        "sources": layer.sources,
                        "tippecanoe": tippecanoe_args(layer),
                    }
                    for layer in result.layers
                ],
                "skipped": [{"source": s, "reason": r} for s, r in result.skipped],
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def tippecanoe_args(layer: LayerExport) -> list[str]:
    """Recommended tippecanoe settings, matching the existing routes layer.

    Routes go to z14 like the OSM layer they sit next to. Segments stop at z13:
    they are infrastructure rather than something a user reads a name off, and
    the extra zoom level is roughly half their tile cost -- the lever that keeps
    the Alps and US mountain-state packs under about 7 % growth.
    """
    max_zoom = "14" if layer.feature_class == "route" else "13"
    return [
        f"--layer={layer.layer}",
        "--minimum-zoom=8",
        f"--maximum-zoom={max_zoom}",
        "--drop-densest-as-needed",
        "--no-tile-size-limit",
    ]


# ------------------------------------------------------------------- health --


@dataclass(slots=True)
class HealthResult:
    source_id: str
    url: str
    ok: bool
    status: int | None = None
    error: str | None = None


def health_check(
    sources: Iterable[Source], *, session: PoliteSession | None = None
) -> list[HealthResult]:
    """Is every source still where the registry says it is?

    Run in CI on the quarterly cadence. Endpoints move -- DOC announced 2026 URL
    changes, CNIG could alter its POST flow -- and catching that here is much
    cheaper than catching it during a pack bake.
    """
    results: list[HealthResult] = []
    for source in sources:
        check = source.health_check
        if check is None:
            continue
        client = session or make_session(source)
        try:
            response = client.get(check.url, allow_redirects=True)
        except Exception as exc:
            results.append(
                HealthResult(source.id, check.url, ok=False, error=f"{type(exc).__name__}: {exc}")
            )
            continue
        status = response.status_code
        results.append(
            HealthResult(source.id, check.url, ok=status == check.expect_status, status=status)
        )
    return results


# ----------------------------------------------------------------- licenses --


def licenses_document(registry: Registry) -> dict[str, Any]:
    """The payload the app's "Data sources & licenses" screen renders.

    Generated from the registry, which is the whole point: adding a country is a
    registry entry and a rebuild, never new legal UI work.
    """
    return {
        "generated_from": "trailsdb/sources.yaml",
        "licenses": [
            {
                "id": lic.id,
                "name": lic.name,
                "url": lic.url,
                "family": lic.family,
                "share_alike": lic.share_alike,
            }
            for lic in sorted(registry.licenses.values(), key=lambda x: x.id)
        ],
        "sources": [
            {
                "id": source.id,
                "name": source.name,
                "publisher": source.publisher,
                "countries": list(source.countries),
                "license": source.license.id,
                "attribution": source.attribution,
                "attribution_is_templated": bool(source.attribution_placeholders),
                "homepage": source.homepage,
                "layer": layer_for(source),
                "verified_on": (
                    source.legal.verified_on.isoformat() if source.legal.verified_on else None
                ),
            }
            for source in sorted(registry, key=lambda s: s.id)
        ],
    }


# ------------------------------------------------------------------- status --


@dataclass(slots=True)
class SourceStatus:
    source: Source
    adapter_ready: bool
    phase: str
    pulled_files: int
    normalized_features: int
    normalized_km: float
    verified: bool


def status(registry: Registry, paths: Paths, *, catalog: Catalog | None = None) -> list[SourceStatus]:
    by_source = {s.source_id: s for s in catalog.stats()} if catalog else {}
    out: list[SourceStatus] = []
    for source in sorted(registry, key=lambda s: s.id):
        manifest = PullManifest.read(paths.raw_dir(source.id))
        stats = by_source.get(source.id)
        features = stats.features if stats else geojsonl.count(paths.normalized_path(source.id))
        out.append(
            SourceStatus(
                source=source,
                adapter_ready=is_implemented(source.adapter),
                phase=phase_of(source.adapter),
                pulled_files=len(manifest.files) if manifest else 0,
                normalized_features=features,
                normalized_km=stats.length_km if stats else 0.0,
                verified=source.verified,
            )
        )
    return out


__all__ = [
    "AdapterNotImplemented",
    "ExportResult",
    "HealthResult",
    "LayerExport",
    "NormalizeResult",
    "SourceStatus",
    "export_pack",
    "health_check",
    "layer_for",
    "licenses_document",
    "make_session",
    "normalize_source",
    "pull_source",
    "status",
    "tippecanoe_args",
]
