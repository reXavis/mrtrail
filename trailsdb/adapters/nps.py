"""US National Park Service trails.

NPS's own service-wide dataset, served from ``mapservices.nps.gov`` -- a
government endpoint, which matters: the same data is mirrored on Esri-hosted
services whose terms of use the public-domain status of the work does not
override. 31,541 segments, refreshed continuously by park units.

Its status field carries the whole life cycle of a trail (Existing, Proposed,
Abandoned, Temporarily Closed -- and one misspelt "Exisiting"), so this adapter
keeps the ones a person can actually walk and drops the rest rather than
drawing a trail NPS has abandoned.

This is the second leg of the plan's US dedup triangle: many of these segments
run through National Forest land and reappear in USFS. That cross-source pass
belongs to phase 5 and is not attempted here.
"""

from __future__ import annotations

from typing import Any

from ..schema import Feature
from .arcgis import ArcGisAdapter, ArcGisLayer, pick

SERVICE = (
    "https://mapservices.nps.gov/arcgis/rest/services/NationalDatasets/"
    "NPS_Public_Trails_Geographic/FeatureServer/0"
)

#: Statuses that describe a trail someone can use today. "Unknown" is kept:
#: NPS uses it for a third of the network and dropping it would erase real
#: trails; "Temporarily Closed" is still a trail.
_KEEP_STATUS = {"existing", "exisiting", "unknown", "temporarily closed", ""}


class NpsAdapter(ArcGisAdapter):
    name = "nps"
    phase = "5 - Americas & Oceania wave"
    country = "US"
    page_size = 1000

    @property
    def layers(self) -> tuple[ArcGisLayer, ...]:
        return (
            ArcGisLayer(
                key="trails",
                url=SERVICE,
                kind="hiking",
                official_status="nps_park_trail",
                # GEOMETRYID is a GUID assigned per feature; FEATUREID is the
                # facility id it hangs off. Both outlive OBJECTID.
                id_fields=("GEOMETRYID", "FEATUREID"),
                name_fields=("TRLNAME", "MAPLABEL", "TRLALTNAME"),
                extras_skip=("OBJECTID", "Shape__Length", "PUBLICDISPLAY", "DATAACCESS"),
            ),
        )

    def normalize_one(self, raw, layer, manifest, path):
        props = raw.get("properties") or {}
        status = str(props.get("TRLSTATUS") or "").strip().lower()
        if status not in _KEEP_STATUS:
            return None  # Proposed, Abandoned, Decommissioned...
        return super().normalize_one(raw, layer, manifest, path)

    def build_feature(self, local_id, geometry, props, layer, manifest, path) -> Feature:
        feature = super().build_feature(local_id, geometry, props, layer, manifest, path)
        feature.kind = _kind_for(props)
        feature.admin = pick(props, ("UNITNAME",))
        feat_type = str(props.get("TRLFEATTYPE") or "").strip().lower()
        if "unofficial" in feat_type or "non-nps" in feat_type:
            # Passes through a park but is not NPS's own; keep it visible as such
            # rather than presenting it as a park trail.
            feature.official_status = "nps_non_park_trail"
        elif "unmaintained" in feat_type:
            feature.official_status = "nps_unmaintained_trail"
        # A blank name is common; a blank string is not a name.
        if feature.name is not None and not feature.name.strip():
            feature.name = None
        return feature


def _kind_for(props: dict[str, Any]) -> str:
    trail_type = str(props.get("TRLTYPE") or "").lower()
    if "water" in trail_type:
        return "paddle"
    if "snow" in trail_type:
        return "ski"
    use = str(props.get("TRLUSE") or "").lower()
    if not use or "unknown" in use:
        return "hiking"
    walks = any(tok in use for tok in ("hik", "pedestrian", "walk"))
    bikes = any(tok in use for tok in ("bicycle", "bike", "cycl"))
    rides = any(tok in use for tok in ("saddle", "horse", "equestrian"))
    if walks and (bikes or rides):
        return "mixed"
    if walks:
        return "hiking"
    if bikes:
        return "cycling"
    if rides:
        return "horse"
    return "hiking"
