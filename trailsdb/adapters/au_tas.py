"""Tasmania: walking, shared-use, horse and bike tracks from the LIST.

The LIST's Transport Segments layer (``TopographyAndRelief/MapServer/24``, on
the state's own ArcGIS server) holds every track and ferry route in the state;
the ``TRAN_CLASS`` filter keeps the tracks -- Walking, Shared Use, Horse Trail,
Bike, and the Parks and Wildlife Service's AS 2156 walking-track classes -- and
leaves ferries and unclassified segments behind: 12,386 of 12,398 segments.

Segments, not routes: a named track is many rows, each with its own
``TRANSEG_ID``. ``PRI_NAME`` carries the track name and ``AUTHORITY`` who looks
after it. Closed segments are dropped; ``Unmaintained`` ones are kept and say so.

Licence: Land Tasmania's open-data page lists Transport Segments as Creative
Commons (CC BY 3.0 AU), and its attribution guidelines prescribe the credit
"<dataset> from theLIST ©State of Tasmania" for digital products.
"""

from __future__ import annotations

from ..schema import Feature
from .arcgis import ArcGisAdapter, ArcGisLayer, pick

SERVICE = "https://services.thelist.tas.gov.au/arcgis/rest/services/Public/TopographyAndRelief/MapServer/24"
WHERE = "TRAN_CLASS NOT IN ('Ferry','Not Applicable')"

_KIND_BY_CLASS = {
    "walking": "hiking",
    "shared use bike/walking": "mixed",
    "shared use track": "mixed",
    "horse trail": "horse",
    "bike": "cycling",
}


class AuTasAdapter(ArcGisAdapter):
    name = "au_tas"
    phase = "5 - Americas & Oceania wave"
    country = "AU"
    page_size = 1000

    @property
    def layers(self) -> tuple[ArcGisLayer, ...]:
        return (
            ArcGisLayer(
                key="tracks",
                url=SERVICE,
                kind="hiking",
                official_status="tas_list_track",
                id_fields=("TRANSEG_ID",),
                name_fields=("PRI_NAME", "SEC_NAME"),
                extras_skip=("OBJECTID", "Shape__Length", "PRI_NOMREG", "SEC_NOMREG", "FOREIGN_ID"),
                where=WHERE,
            ),
        )

    def normalize_one(self, raw, layer, manifest, path):
        props = raw.get("properties") or {}
        if str(props.get("STATUS") or "").strip().lower() == "closed":
            return None
        return super().normalize_one(raw, layer, manifest, path)

    def build_feature(self, local_id, geometry, props, layer, manifest, path) -> Feature:
        feature = super().build_feature(local_id, geometry, props, layer, manifest, path)
        tran_class = str(props.get("TRAN_CLASS") or "").strip()
        lowered = tran_class.lower()
        if lowered.startswith("as2156"):
            feature.kind = "hiking"
            feature.official_status = "tas_pws_walking_track"
        else:
            feature.kind = _KIND_BY_CLASS.get(lowered, "hiking")
        if str(props.get("STATUS") or "").strip().lower() == "unmaintained":
            feature.official_status = "tas_unmaintained_track"
        feature.admin = "Tasmania"
        feature.extras["managed_by"] = pick(props, ("AUTHORITY",))
        return feature
