"""South Australia: the Department for Environment and Water's Recreation Trails.

One GeoJSON file, zipped, on the department's own download server, with the
licence inside the zip: ``CC_BY.txt`` says "Information contained in this file
is licensed under a Creative Commons By Attribution 4.0 Australia Licence".
7,271 pieces of 1,068 named trails, 9,592 km, in GDA2020 geographic
coordinates (EPSG:7844), which sit within a metre of WGS84 and are used as is.

``PERSISTENT`` is the department's own stable identifier and is the local id;
the 53 pieces that share one get their row number appended rather than being
dropped. ``TRAILNETWO`` names the long trail a piece belongs to -- 2,330 pieces
of the Heysen Trail alone -- and becomes the parent. Retired and temporarily
closed trails are dropped; a blank status is kept, as NPS's "Unknown" is.
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterator

from ..fetch import FetchError
from ..formats import archive, geojson
from ..manifest import PullManifest
from ..schema import Feature
from .base import Adapter

URL = "https://www.waterconnect.sa.gov.au/Content/Downloads/DEWNR/TOPO_RecreationTrails_geojson.zip"
FILE_NAME = "TOPO_RecreationTrails_geojson.zip"
#: The zip carries the same data in GDA2020 and GDA94; one is enough.
MEMBER = "TOPO_RecreationTrails_GDA2020.geojson"

_KIND_BY_TYPE = {
    "WALKING": "hiking",
    "CYCLING": "cycling",
    "HORSE RIDING": "horse",
    "CANOEING": "paddle",
}
_DROP_STATUS_PREFIXES = ("CLOSED", "INACTIVE")
_GENERIC_NAMES = {"unnamed", "boardwalk", "unknown", ""}


class AuSaAdapter(Adapter):
    name = "au_sa"
    phase = "5 - Americas & Oceania wave"

    def fetch(
        self, manifest: PullManifest, *, force: bool = False, limit: int | None = None
    ) -> None:
        record = self.session.download(URL, self.raw_dir / FILE_NAME, force=force)
        manifest.add(record)
        if not archive.is_zip(self.raw_dir / FILE_NAME):
            raise FetchError(f"{self.source.id}: {FILE_NAME} is not a zip -- the download has moved")
        manifest.notes = f"files=1 member={MEMBER}"

    def normalize(self, manifest: PullManifest) -> Iterator[Feature]:
        path = self.raw_dir / FILE_NAME
        payload = next((data for name, data in archive.iter_members(path, (".geojson",)) if name.endswith(MEMBER)), None)
        if payload is None:
            raise FetchError(f"{self.source.id}: {MEMBER} not in {FILE_NAME}")
        seen: set[str] = set()
        for raw in json.loads(payload).get("features") or []:
            feature = self.normalize_one(raw, manifest, seen)
            if feature is not None:
                yield feature

    def normalize_one(self, raw: dict[str, Any], manifest: PullManifest, seen: set[str]) -> Feature | None:
        geometry = geojson.line_geometry(raw.get("geometry"))
        if geometry is None:
            return None
        props = {k: (v.strip() if isinstance(v, str) else v) for k, v in (raw.get("properties") or {}).items()}
        status = str(props.get("TRAILSTATU") or "").upper()
        if status.startswith(_DROP_STATUS_PREFIXES):
            return None
        persistent = props.get("PERSISTENT")
        if persistent in (None, ""):
            return None
        local_id = str(persistent)
        if local_id in seen:
            local_id = f"{local_id}-{props.get('FID')}"
        seen.add(local_id)

        name = props.get("TRAILNAME") or None
        if name and name.lower() in _GENERIC_NAMES:
            name = None
        network = props.get("TRAILNETWO") or None
        fields: dict[str, Any] = {
            "name": name,
            "official_status": "sa_recreation_trail",
            "country": "AU",
            "admin": "South Australia",
            "source_url": self.source.homepage or None,
        }
        if network:
            fields["parent_id"] = self.make_id(_slug(network))
            fields["parent_name"] = network
        extras = {k: v for k, v in props.items() if v not in (None, "", 0, "0") and k not in ("FID", "Shape_Leng", "TRAILNAME", "TRAILNETWO")}
        return self.feature(local_id, geometry, manifest=manifest, kind=_kind_for(props), extras=extras, **fields)


def _kind_for(props: dict[str, Any]) -> str:
    trail_type = str(props.get("TRAILTYPE") or "").upper()
    if trail_type.startswith("SU:"):
        return "mixed"  # shared use: SU:WALK,BIKE and the like
    return _KIND_BY_TYPE.get(trail_type, "hiking")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
