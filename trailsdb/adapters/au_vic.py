"""Victoria: DEECA's Recreation Tracks, from the state's own GeoServer WFS.

``recweb_tracks`` is the published set -- 365 named tracks and touring routes
in State Forest, 5,065 km, with grade, distance and duration per activity. The
data.vic.gov.au record puts it under CC BY 4.0. Coordinates come back in
GDA2020 geographic (EPSG:7844), within a metre of WGS84.

The activity flags decide what a track is for: ``w_`` walking, ``h_`` horse,
``m_`` mountain bike, ``f_`` four-wheel drive, ``d_`` trail bike and ``t_``
touring. A track offered to walkers is a hiking route whatever else it allows;
one offered only to vehicles is a road tour, not a trail, and is left out.
"""

from __future__ import annotations

from typing import Any

from ..schema import Feature
from .wfs import WfsAdapter, WfsLayer

BASE_URL = "https://opendata.maps.vic.gov.au/geoserver/wfs"

#: Activity prefix -> normalized kind, in order of preference.
_KINDS = (("w", "hiking"), ("m", "mtb"), ("h", "horse"))
_MOTORISED = ("f", "d", "t")


class AuVicAdapter(WfsAdapter):
    name = "au_vic"
    phase = "5 - Americas & Oceania wave"
    base_url = BASE_URL
    country = "AU"
    page_size = 500

    @property
    def layers(self) -> tuple[WfsLayer, ...]:
        return (
            WfsLayer(
                key="recweb_tracks",
                type_name="open-data-platform:recweb_tracks",
                official_status="vic_recreation_track",
                id_fields=("serial_no",),
                name_fields=("name",),
                output="json",
                sort_by="serial_no",
                extras_skip=("export_date", "layer_edit_date", "vers_date", "photo_id_1", "photo_id_2", "photo_id_3"),
            ),
        )

    def normalize_one(self, attrs, geometry, layer, manifest, path) -> Feature | None:
        if str(attrs.get("published") or "1") != "1":
            return None
        if kind_for(attrs) is None:
            return None  # vehicles only: a 4WD tour or a trail-bike loop
        return super().normalize_one(attrs, geometry, layer, manifest, path)

    def build_feature(self, local_id, geometry, attrs, layer, manifest, path) -> Feature:
        feature = super().build_feature(local_id, geometry, attrs, layer, manifest, path)
        feature.kind = kind_for(attrs) or feature.kind
        feature.admin = "Victoria"
        # Only the activities on offer are worth carrying; the rest of the
        # hundred columns are blank per row.
        feature.extras = {
            k: v for k, v in feature.extras.items()
            if not (len(k) > 2 and k[1] == "_" and k[0] in "whmfdt") or attrs.get(f"{k[0]}_activity") == "Y"
        }
        return feature


def kind_for(attrs: dict[str, Any]) -> str | None:
    for prefix, kind in _KINDS:
        if attrs.get(f"{prefix}_activity") == "Y":
            return kind
    if any(attrs.get(f"{prefix}_activity") == "Y" for prefix in _MOTORISED):
        return None
    return "hiking"  # no flags at all: a track, and on foot is the default
