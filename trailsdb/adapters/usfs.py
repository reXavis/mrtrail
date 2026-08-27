"""USDA Forest Service: National Forest System Trails.

The largest single source in the plan -- 257,000 km, an official figure of
160,000 miles -- and the one whose access turned out easier than budgeted.

The plan assumed a 118 MB file geodatabase and the GDAL dependency that implies.
That download does exist, but the same data is served from the EDW REST endpoint
as GeoJSON, so this adapter needs nothing beyond the stdlib. 86,303 segments
across one layer.

These are network segments, not browsable routes: mostly unnamed forest trail
centerlines with a control number, a class and a surface. They render like the
ways layer and stop a zoom level earlier than named routes.

Pulled from the government endpoint only. The same data is mirrored on Esri
services that attach Esri's terms of use, which the public-domain status of the
underlying work does not override.
"""

from __future__ import annotations

from typing import Any, Iterator

from ..manifest import PullManifest
from ..schema import Feature
from .arcgis import ArcGisAdapter, ArcGisLayer, pick

SERVICE = (
    "https://apps.fs.usda.gov/arcx/rest/services/EDW/EDW_TrailNFSPublish_01/MapServer/0"
)

#: USFS records permitted use as a set of managed-use columns rather than one
#: type. Checked in this order so the most specific wins.
_KIND_BY_USE = (
    ("snow_motorized", "ski"),
    ("terra_motorized", "other"),
    ("hiker_pedestrian_managed", "hiking"),
    ("bicycle_managed", "cycling"),
    ("pack_saddle_managed", "horse"),
)


class UsfsAdapter(ArcGisAdapter):
    name = "usfs"
    phase = "2 - prove it on three continents"
    country = "US"
    page_size = 1000

    @property
    def layers(self) -> tuple[ArcGisLayer, ...]:
        return (
            ArcGisLayer(
                key="trails",
                url=SERVICE,
                kind="hiking",
                official_status="nfs_trail",
                # trail_cn is the Forest Service control number: stable across
                # publishes, unlike objectid.
                id_fields=("trail_cn", "TRAIL_CN"),
                name_fields=("trail_name", "TRAIL_NAME"),
                extras_skip=("objectid", "OBJECTID", "Shape__Length", "security_id"),
            ),
        )

    def normalize(self, manifest: PullManifest) -> Iterator[Feature]:
        # USFS splits a named trail into many segments that share a control
        # number; each segment keeps its own identity via its milepost range.
        counts: dict[str, int] = {}
        for feature in super().normalize(manifest):
            base = feature.id
            counts[base] = counts.get(base, 0) + 1
            yield feature

    def stable_id(self, props: dict[str, Any], layer: ArcGisLayer) -> str | None:
        control = super().stable_id(props, layer)
        if control is None:
            return None
        # Segments of one trail share a control number and differ by milepost.
        begin, end = props.get("bmp"), props.get("emp")
        if begin is not None and end is not None:
            return f"{control}-{begin}-{end}"
        return control

    def build_feature(self, local_id, geometry, props, layer, manifest, path) -> Feature:
        feature = super().build_feature(local_id, geometry, props, layer, manifest, path)
        feature.kind = _kind_for(props)
        designation = props.get("national_trail_designation")
        if designation not in (None, "", 0, "0"):
            # National Scenic / Historic Trail designation is the closest thing
            # USFS has to a homologation status.
            feature.official_status = "national_trail_designated"
        feature.ref = pick(props, ("trail_no", "TRAIL_NO"))
        return feature


def _kind_for(props: dict[str, Any]) -> str:
    for column, kind in _KIND_BY_USE:
        value = props.get(column)
        if value not in (None, "", " ", "N", 0, "0"):
            return kind
    return "hiking"
