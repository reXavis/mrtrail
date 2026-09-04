"""Ontario Trail Network.

Served from the province's own LIO services endpoint under the Open Government
Licence - Ontario, which prescribes its attribution statement verbatim:
"Contains information licensed under the Open Government Licence - Ontario."

6,991 segments totalling 47,090 km by the province's own length field -- more
than the plan budgeted for Ontario and British Columbia together. A good share
of it is on-road (``ON_ROAD_FLG``), which is carried through so the styling can
tell a rail trail from a road shoulder.
"""

from __future__ import annotations

from typing import Any

from ..schema import Feature
from .arcgis import ArcGisAdapter, ArcGisLayer, pick

SERVICE = (
    "https://ws.lioservices.lrc.gov.on.ca/arcgis2/rest/services/LIO_OPEN_DATA/"
    "LIO_Open04/MapServer/19"
)


class OntarioOtnAdapter(ArcGisAdapter):
    name = "ontario_otn"
    phase = "5 - Americas & Oceania wave"
    country = "CA"
    page_size = 1000

    @property
    def layers(self) -> tuple[ArcGisLayer, ...]:
        return (
            ArcGisLayer(
                key="segments",
                url=SERVICE,
                kind="mixed",
                official_status="otn_segment",
                # OGF_ID is the Ontario Geospatial Feature id, stable across loads.
                id_fields=("OGF_ID",),
                name_fields=("TRAIL_NAME",),
                extras_skip=("OBJECTID", "SHAPE", "SHAPE.LEN", "SYSTEM_DATETIME"),
            ),
        )

    def build_feature(self, local_id, geometry, props, layer, manifest, path) -> Feature:
        feature = super().build_feature(local_id, geometry, props, layer, manifest, path)
        feature.kind = _kind_for(props)
        feature.admin = "Ontario"
        website = pick(props, ("TRAIL_ASSOCIATION_WEBSITE",))
        if website:
            feature.source_url = website if website.startswith("http") else f"https://{website}"
        return feature


_USE_KINDS = (
    ("hiking or walking", "hiking"),
    ("cycling", "cycling"),
    ("cross country skiing", "ski"),
    ("snowshoeing", "hiking"),
    ("equestrian", "horse"),
    ("paddling", "paddle"),
)


def _kind_for(props: dict[str, Any]) -> str:
    """One kind from Ontario's comma-separated permitted-uses list.

    Walking-only is hiking; walking plus wheels or hooves is mixed; a trail that
    does not permit walking at all takes its first recognised use.
    """
    uses = str(props.get("PERMITTED_USES") or "").lower()
    if not uses:
        return "mixed"
    found = [kind for needle, kind in _USE_KINDS if needle in uses]
    if not found:
        return "other"
    if "hiking" in found:
        return "hiking" if set(found) <= {"hiking"} else "mixed"
    return found[0]
