"""The normalized feature schema every adapter must produce.

Two feature classes, because official sources come in two shapes:

``route``
    A named thing a user browses and picks -- a FEDME homologated sendero, a DOC
    Great Walk, a EuroVelo corridor, one stage of the Camino. Rendered like the
    existing OSM ``routes`` layer, labelled, tappable.

``segment``
    Network infrastructure -- swisstopo Wanderwege, USFS centerlines, the Ontario
    Trail Network. Hundreds of thousands of short lines with no browsable
    identity. Rendered like the ways layer, and cheap to drop a zoom level.

``spot``
    A point a trail user plans around -- a hut, a shelter, a water point, a
    pass. refuges.info is the one source of these. Rendered as icons, never
    merged with any line layer, and (being share-alike) in a layer of its own.

Deliberately absent: ascent, descent, and elevation profiles. Those are computed
at pack-bake time by ``enrich_routes.py`` against the pack's own DEM, exactly as
OSM routes get today. Storing them in the master database would bloat it by
several times and bind it to one DEM.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

SCHEMA_VERSION = 1

FEATURE_CLASSES = ("route", "segment", "spot")

# Small on purpose. Anything a source says that does not map here goes into
# ``extras``, which never reaches the tiles.
KINDS = (
    "hiking",
    "cycling",
    "mtb",
    "ski",
    "horse",
    "paddle",
    "running",
    "mixed",
    "other",
)

# Attributes the pack bake writes into the vector tiles. Everything else stays
# in the master database and the catalog: ``attribution`` is per-source and comes
# from the registry at render time, ``extras`` is unbounded, ``source_url`` is a
# catalog concern. Keeping this list short is what keeps the per-pack growth
# numbers in the plan honest.
TILE_ATTRIBUTES = (
    "id",
    "source",
    "license",
    "kind",
    "ref",
    "name",
    "official_status",
    "parent_id",
    "stage_no",
    "category",
)

_ID_RE = re.compile(r"^[a-z0-9_]+:[A-Za-z0-9_.:/\-]+$")
_SOURCE_RE = re.compile(r"^[a-z0-9_]+$")

LINE_TYPES = ("LineString", "MultiLineString")
POINT_TYPES = ("Point",)

#: What a spot is, for the icon: the one attribute spots carry that lines do not.
SPOT_CATEGORIES = ("hut", "shelter", "gite", "bivouac", "water", "summit", "pass", "lake", "other")


class ValidationError(ValueError):
    """Raised when a feature does not satisfy the normalized schema."""


@dataclass(slots=True)
class Feature:
    """One normalized trail feature.

    ``id`` is ``"{source}:{stable local id}"``. The local part must be stable
    across pulls -- it is what lets a quarterly refresh diff instead of replace,
    and what the OSM cross-link matcher stores its matches against. When a source
    has no stable identifier of its own, adapters derive one from an immutable
    property (a file name, a geometry hash), never from row order.
    """

    id: str
    source: str
    license: str
    attribution: str
    feature_class: str
    geometry: dict[str, Any]

    kind: str = "hiking"
    ref: str | None = None
    name: str | None = None

    # Stage grouping. A Camino variant has ~30 stages; each stage is its own
    # feature with ``parent_id`` pointing at a synthetic parent route.
    parent_id: str | None = None
    parent_name: str | None = None
    stage_no: int | None = None

    official_status: str | None = None
    #: Spots only: one of SPOT_CATEGORIES. Lines leave it None.
    category: str | None = None
    source_url: str | None = None

    country: str | None = None
    admin: str | None = None

    # Everything the source said that the schema has no column for. Kept out of
    # tiles; available in the catalog for debugging and future features.
    extras: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not _ID_RE.match(self.id):
            raise ValidationError(f"id {self.id!r} must look like 'source:local_id'")
        if not _SOURCE_RE.match(self.source):
            raise ValidationError(f"source {self.source!r} must be lowercase snake_case")
        if not self.id.startswith(f"{self.source}:"):
            raise ValidationError(f"id {self.id!r} is not prefixed with source {self.source!r}")
        if not self.license:
            raise ValidationError(f"{self.id}: license is required on every feature")
        if not self.attribution:
            raise ValidationError(f"{self.id}: attribution is required on every feature")
        if self.feature_class not in FEATURE_CLASSES:
            raise ValidationError(
                f"{self.id}: feature_class {self.feature_class!r} not in {FEATURE_CLASSES}"
            )
        if self.kind not in KINDS:
            raise ValidationError(f"{self.id}: kind {self.kind!r} not in {KINDS}")
        if self.stage_no is not None and self.stage_no < 0:
            raise ValidationError(f"{self.id}: stage_no must be non-negative")
        if self.country is not None and not re.match(r"^[A-Z]{2}$", self.country):
            raise ValidationError(f"{self.id}: country {self.country!r} must be ISO 3166-1 alpha-2")
        if self.category is not None and self.category not in SPOT_CATEGORIES:
            raise ValidationError(f"{self.id}: category {self.category!r} not in {SPOT_CATEGORIES}")
        if self.category is not None and self.feature_class != "spot":
            raise ValidationError(f"{self.id}: only spots carry a category")
        validate_geometry(self.geometry, self.id, feature_class=self.feature_class)

    # -- serialization -------------------------------------------------------

    def to_geojson(self) -> dict[str, Any]:
        props: dict[str, Any] = {
            "source": self.source,
            "license": self.license,
            "attribution": self.attribution,
            "feature_class": self.feature_class,
            "kind": self.kind,
        }
        for key in (
            "ref",
            "name",
            "parent_id",
            "parent_name",
            "stage_no",
            "official_status",
            "category",
            "source_url",
            "country",
            "admin",
        ):
            value = getattr(self, key)
            if value is not None:
                props[key] = value
        if self.extras:
            props["extras"] = self.extras
        return {"type": "Feature", "id": self.id, "properties": props, "geometry": self.geometry}

    @classmethod
    def from_geojson(cls, obj: dict[str, Any]) -> "Feature":
        props = obj.get("properties") or {}
        known = {
            "ref",
            "name",
            "parent_id",
            "parent_name",
            "stage_no",
            "official_status",
            "category",
            "source_url",
            "country",
            "admin",
        }
        return cls(
            id=obj["id"],
            source=props["source"],
            license=props["license"],
            attribution=props["attribution"],
            feature_class=props["feature_class"],
            geometry=obj["geometry"],
            kind=props.get("kind", "hiking"),
            extras=props.get("extras") or {},
            **{k: props.get(k) for k in known},
        )

    def tile_properties(self) -> dict[str, Any]:
        """The subset of properties that the pack bake writes into vector tiles."""
        full = self.to_geojson()["properties"]
        full["id"] = self.id
        return {k: full[k] for k in TILE_ATTRIBUTES if full.get(k) is not None}


def validate_geometry(geometry: Any, ctx: str = "geometry", *, feature_class: str = "route") -> None:
    if not isinstance(geometry, dict):
        raise ValidationError(f"{ctx}: geometry must be an object")
    gtype = geometry.get("type")
    if feature_class == "spot":
        if gtype not in POINT_TYPES:
            raise ValidationError(f"{ctx}: a spot needs a Point geometry, not {gtype!r}")
        _validate_position(geometry.get("coordinates"), ctx)
        return
    if gtype not in LINE_TYPES:
        raise ValidationError(f"{ctx}: geometry type {gtype!r} not in {LINE_TYPES}")
    coords = geometry.get("coordinates")
    if not isinstance(coords, list) or not coords:
        raise ValidationError(f"{ctx}: geometry has no coordinates")
    lines = [coords] if gtype == "LineString" else coords
    for line in lines:
        if not isinstance(line, list) or len(line) < 2:
            raise ValidationError(f"{ctx}: a line needs at least two positions")
        for position in line:
            _validate_position(position, ctx)


def _validate_position(position: Any, ctx: str) -> None:
    if not isinstance(position, (list, tuple)) or len(position) < 2:
        raise ValidationError(f"{ctx}: position must be [lon, lat]")
    lon, lat = position[0], position[1]
    if not isinstance(lon, (int, float)) or not isinstance(lat, (int, float)):
        raise ValidationError(f"{ctx}: position coordinates must be numbers")
    if not -180.0 <= lon <= 180.0:
        raise ValidationError(f"{ctx}: longitude {lon} out of range (is it projected?)")
    if not -90.0 <= lat <= 90.0:
        raise ValidationError(f"{ctx}: latitude {lat} out of range (is it projected?)")


def validate_all(features: Iterable[Feature]) -> list[Feature]:
    out = []
    for feature in features:
        feature.validate()
        out.append(feature)
    return out


def dumps(feature: Feature, *, precision: int | None = None) -> str:
    """Compact single-line GeoJSON, the GeoJSONL wire format.

    Coordinates are rounded on the way out. Doing it here rather than in each
    adapter means no source can accidentally write 17 significant digits of a
    measurement good to a few metres -- the same reasoning that puts license
    stamping in one place.
    """
    from .geo import COORDINATE_PRECISION, round_geometry

    obj = feature.to_geojson()
    obj["geometry"] = round_geometry(
        obj["geometry"], COORDINATE_PRECISION if precision is None else precision
    )
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
