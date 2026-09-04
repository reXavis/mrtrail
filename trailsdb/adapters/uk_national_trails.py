"""England's National Trails and the King Charles III England Coast Path.

Both published by Natural England on its own ArcGIS organisation under the
Open Government Licence v3.0, whose attribution statement is prescribed:
"Contains public sector information licensed under the Open Government Licence
v3.0." The layer names themselves carry "(c) Natural England".

Two very different shapes under one source:

* **National Trails** -- 14 features, one line per trail, 4,111 km in total
  (Pennine Way 435 km, South West Coast Path 1,068 km...). Browsable routes in
  the plainest sense.
* **England Coast Path** -- 17,489 short sections of one route that is still
  being opened stretch by stretch. Each section carries a status; the ones that
  are "Not an existing walked route" are aspirations, not paths, and are dropped.
  Sections group under their named stretch so the map can treat the coast path
  as a route rather than as seventeen thousand fragments.

Wales and Scotland are separate publishers and separate registry entries when
they come.
"""

from __future__ import annotations

from typing import Any

from ..schema import Feature
from .arcgis import ArcGisAdapter, ArcGisLayer, pick

ORG = "https://services.arcgis.com/JJzESW51TqeY9uat/arcgis/rest/services"
NATIONAL_TRAILS = f"{ORG}/National_Trails_England/FeatureServer/0"
COAST_PATH = f"{ORG}/England_Coast_Path_Route/FeatureServer/0"

_NOT_A_PATH = "not an existing walked route"


class UkNationalTrailsAdapter(ArcGisAdapter):
    name = "uk_national_trails"
    phase = "4 - Europe wave"
    country = "GB"
    page_size = 1000

    @property
    def layers(self) -> tuple[ArcGisLayer, ...]:
        return (
            ArcGisLayer(
                key="national_trails",
                url=NATIONAL_TRAILS,
                kind="hiking",
                official_status="national_trail",
                id_fields=("GlobalID",),
                name_fields=("Name",),
                extras_skip=("OBJECTID", "OBJECTID_1", "Shape__Length"),
            ),
            ArcGisLayer(
                key="coast_path",
                url=COAST_PATH,
                kind="hiking",
                official_status="england_coast_path",
                id_fields=("GlobalID",),
                name_fields=("Stretch",),
                extras_skip=("OBJECTID", "Shape__Length"),
            ),
        )

    def normalize_one(self, raw, layer, manifest, path):
        if layer.key == "coast_path":
            status = str((raw.get("properties") or {}).get("Status") or "").strip().lower()
            if status == _NOT_A_PATH:
                return None
        return super().normalize_one(raw, layer, manifest, path)

    def build_feature(self, local_id, geometry, props, layer, manifest, path) -> Feature:
        feature = super().build_feature(local_id, geometry, props, layer, manifest, path)
        feature.admin = "England"
        if layer.key == "national_trails":
            if "bridleway" in (feature.name or "").lower():
                feature.kind = "mixed"
            feature.ref = None
        else:
            stretch = pick(props, ("Stretch",))
            section = pick(props, ("Section_ID",))
            if stretch:
                feature.parent_id = self.make_id(f"coast-{_slug(stretch)}")
                feature.parent_name = f"England Coast Path: {stretch}"
            if section:
                feature.name = f"England Coast Path {section}"
                feature.ref = section
            feature.kind = _coast_kind(props)
            if str(props.get("Alt_Route") or "").strip().lower() == "yes":
                feature.official_status = "england_coast_path_alternative"
        return feature


def _coast_kind(props: dict[str, Any]) -> str:
    status = str(props.get("Status") or "").lower()
    if "cycle track" in status and "pedestrian" not in status:
        return "cycling"
    if "bridleway" in status or "multi-use" in status or "byway" in status:
        return "mixed"
    return "hiking"


def _slug(text: str) -> str:
    import re

    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
