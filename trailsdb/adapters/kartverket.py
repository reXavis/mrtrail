"""Kartverket's Turrutebasen: Norway's national outdoor-route database.

The largest European source after swisstopo and the one the plan knew least
about. It is served from Geonorge's WFS as GML 3.2 and nothing else -- the
reason ``formats/gml.py`` exists -- across four feature types:

==================  ========  =======
type                count     kind
==================  ========  =======
app:Fotrute         139,910   hiking
app:Skiløype         12,263   ski
app:Sykkelrute       11,871   cycling
app:AnnenRute         2,390   other
==================  ========  =======

Each feature carries a stable ``lokalId`` UUID, a route name and number, a
marking flag, a difficulty grade (G/B/R/S: green, blue, red, black) and the
club or municipality that maintains it. Routes are published as many pieces
sharing one ``rutenummer``, which is used as the grouping key.

Those pieces average half a kilometre -- 166,434 of them for 83,824 km -- so
the source is a ``segment`` class: it is network-shaped, and a southern-Norway
cut baked as z14 routes cost 1.31 KB/km, three times a FEDME sendero, almost
all of it per-feature overhead. The z13 segment layer keeps the route name and
the parent, and drops most of that cost.

Licence: Kartverket's own terms put its free products under CC BY 4.0 with
the credit "© Kartverket"; the registry carries the quote.
"""

from __future__ import annotations

from typing import Any

from ..schema import Feature
from .wfs import WfsAdapter, WfsLayer

BASE_URL = "https://wfs.geonorge.no/skwms1/wfs.turogfriluftsruter"

_GRADE = {"G": "green_easy", "B": "blue_medium", "R": "red_demanding", "S": "black_expert"}
_SKIP = ("navnerom", "versjonId", "målemetode", "nøyaktighet", "datafangstdato", "oppdateringsdato")


class KartverketAdapter(WfsAdapter):
    name = "kartverket"
    phase = "4 - Europe wave"
    base_url = BASE_URL
    country = "NO"
    page_size = 1000

    @property
    def layers(self) -> tuple[WfsLayer, ...]:
        common = dict(output="gml", id_fields=("lokalId",), name_fields=("rutenavn",), extras_skip=_SKIP)
        return (
            WfsLayer("fotrute", "app:Fotrute", kind="hiking", official_status="norway_fotrute", **common),
            WfsLayer("skiloype", "app:Skiløype", kind="ski", official_status="norway_skiloype", **common),
            WfsLayer("sykkelrute", "app:Sykkelrute", kind="cycling", official_status="norway_sykkelrute", **common),
            WfsLayer("annenrute", "app:AnnenRute", kind="other", official_status="norway_annenrute", **common),
        )

    def build_feature(self, local_id, geometry, attrs: dict[str, Any], layer, manifest, path) -> Feature:
        feature = super().build_feature(local_id, geometry, attrs, layer, manifest, path)
        if feature.name and feature.name.strip().lower() in ("ukjent", "unknown"):
            feature.name = None
        number = attrs.get("rutenummer")
        if number:
            feature.ref = str(number)
            # Pieces of one route share its number; the name rides along as the
            # parent's label so the map can treat the route as one thing.
            feature.parent_id = self.make_id(f"rute-{number}")
            feature.parent_name = feature.name
        grade = attrs.get("gradering")
        if grade in _GRADE:
            feature.extras["difficulty"] = _GRADE[grade]
        feature.admin = attrs.get("vedlikeholdsansvarlig") or None
        return feature
