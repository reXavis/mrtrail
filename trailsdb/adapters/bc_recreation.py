"""British Columbia: Ministry of Forests recreation lines.

DataBC's WFS serves this layer as WGS84 GeoJSON under the Open Government
Licence - British Columbia -- but only when told how to sort, since the
underlying view has no primary key and the server refuses to page otherwise.

What the layer actually holds is tenure: 19,782 "Recreation Trail Reserve"
lines, of which **half are retired**. A retired reserve is a legal record, not
a trail, so only ``LIFE_CYCLE_STATUS_CODE = ACTIVE`` map-feature ``RTR`` lines
are kept. The other "Recreation Trails" datasets on the catalogue are
"Access Only" and are left alone, as the plan's rule requires.
"""

from __future__ import annotations

from typing import Any

from ..schema import Feature
from .wfs import WfsAdapter, WfsLayer

BASE_URL = "https://openmaps.gov.bc.ca/geo/pub/wfs"
LAYER = "pub:WHSE_FOREST_TENURE.FTEN_RECREATION_LINES_SVW"


class BcRecreationAdapter(WfsAdapter):
    name = "bc_recreation"
    phase = "5 - Americas & Oceania wave"
    base_url = BASE_URL
    country = "CA"
    page_size = 2000

    @property
    def layers(self) -> tuple[WfsLayer, ...]:
        return (
            WfsLayer(
                key="lines",
                type_name=LAYER,
                kind="hiking",
                official_status="bc_recreation_trail_reserve",
                id_fields=("RMF_SKEY",),
                name_fields=("PROJECT_NAME",),
                output="json",
                sort_by="RMF_SKEY",
                extras_skip=("OBJECTID", "SE_ANNO_CAD_DATA", "FEATURE_CLASS_SKEY", "FEATURE_LENGTH"),
            ),
        )

    def normalize_one(self, attrs: dict[str, Any], geometry, layer, manifest, path) -> Feature | None:
        if str(attrs.get("LIFE_CYCLE_STATUS_CODE") or "").upper() != "ACTIVE":
            return None
        if str(attrs.get("RECREATION_MAP_FEATURE_CODE") or "").upper() != "RTR":
            return None
        return super().normalize_one(attrs, geometry, layer, manifest, path)

    def build_feature(self, local_id, geometry, attrs, layer, manifest, path) -> Feature:
        feature = super().build_feature(local_id, geometry, attrs, layer, manifest, path)
        feature.admin = "British Columbia"
        feature.ref = attrs.get("FOREST_FILE_ID") or None
        return feature
