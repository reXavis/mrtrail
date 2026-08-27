"""New Zealand Department of Conservation.

DOC publishes through its own ArcGIS Hub (org ``3JjYDyG3oajxU6HO``) under
CC BY 4.0. Three layers matter, and they split across both feature classes:

============================  ======  =============  ===================
layer                         count   series         class
============================  ======  =============  ===================
Walking and Tramping routes    1,349  experiences    route
Mountain Bike Track Routes       198  experiences    route
Tracks (EAM asset system)      3,255  network        segment
============================  ======  =============  ===================

The first two are named experiences a user browses; the third is DOC's full
asset-management track network, which is infrastructure and renders like the
ways layer.

**The two overlap almost entirely, and that is measured, not assumed.**
Densifying both to 50 m and hashing to ~110 m cells: 97.5 % of the experience
footprint also appears in the network, and of the experiences' 13,696 km only
93 km are unique to them. The network's own contribution beyond the experiences
is about 4,090 km. So New Zealand's real official coverage is the network's
~13,900 km -- close to the plan's 14,000 km estimate -- and roughly 9,800 km of
it would render twice if both layers shipped untreated. They stay in separate
tile layers with distinct styling, and the cross-link work in phase 6 is what
lets the segment layer suppress anything with an experience twin.

**These datasets are deprecated.** DOC's own item description reads: "This
dataset is no longer being actively maintained... A new version of this dataset
will be made available soon, and this item will be updated with a link to the
replacement." No replacement was published as of the last check. This is exactly
the source drift the plan flagged, and it is handled rather than ignored: the
layers still serve current data under the same licence, so we pull them, and
:meth:`NzDocAdapter.check_for_replacement` re-reads the notice on every pull so
the migration is caught the day it lands instead of at a pack bake.
"""

from __future__ import annotations

import re
from typing import Any

from ..manifest import PullManifest
from ..schema import Feature
from .arcgis import ArcGisAdapter, ArcGisLayer

ORG = "3JjYDyG3oajxU6HO"
SERVICE_ROOT = f"https://services1.arcgis.com/{ORG}/arcgis/rest/services"
ITEM_URL = "https://www.arcgis.com/sharing/rest/content/items/{item_id}?f=json"

#: ArcGIS item ids behind each layer, used to re-read the deprecation notice.
ITEM_IDS = {
    "walking": "e3f63067394a46238c92f9aed63ff78b",
    "mtb": "0fdd22944b1b42ec87f54c11790208f6",
    "tracks": "5cba3b0a2e1041c9ad02ec694f3f3d37",
}

LAYERS: dict[str, tuple[ArcGisLayer, ...]] = {
    "experiences": (
        ArcGisLayer(
            key="walking",
            url=f"{SERVICE_ROOT}/DOC_Walking_Experiences/FeatureServer/1",
            kind="hiking",
            official_status="doc_walking_experience",
            id_fields=("FlocID", "GlobalID"),
            name_fields=("name", "TechObjectName"),
        ),
        ArcGisLayer(
            key="mtb",
            url=f"{SERVICE_ROOT}/DOC_Mountain_Bike_Tracks/FeatureServer/1",
            kind="mtb",
            official_status="doc_mountain_bike_route",
            id_fields=("FlocID", "GlobalID"),
            name_fields=("name", "TechObjectName"),
        ),
    ),
    "network": (
        ArcGisLayer(
            key="tracks",
            url=f"{SERVICE_ROOT}/DOC_Tracks_EAM/FeatureServer/0",
            kind="hiking",
            official_status="doc_track_asset",
            id_fields=("FlocID", "GlobalID"),
            name_fields=("TechObjectName", "name"),
        ),
    ),
}

#: DOC's SubObjectType vocabulary on the asset network, mapped to normalized kinds.
_KIND_BY_SUBOBJECT = {
    "tramping track": "hiking",
    "walking track": "hiking",
    "short walk": "hiking",
    "great walk": "hiking",
    "mountain bike": "mtb",
    "cycle": "cycling",
    "bridle": "horse",
    "horse": "horse",
    "route": "hiking",
}

_REGION_KEYS = ("region", "REGION", "conservancy", "district")
_PAGE_KEYS = ("walkingAndTrampingWebPage", "mountainBikingTrackWebPage")
_GUID_RE = re.compile(r"([0-9a-f]{32})", re.I)


class NzDocAdapter(ArcGisAdapter):
    name = "nz_doc"
    phase = "2 - prove it on three continents"
    country = "NZ"

    @property
    def layers(self) -> tuple[ArcGisLayer, ...]:
        key = self.source.series or "experiences"
        if key not in LAYERS:
            raise ValueError(
                f"{self.source.id}: series {key!r} is not one of {sorted(LAYERS)} "
                f"-- check the `series:` key in sources.yaml"
            )
        return LAYERS[key]

    def fetch(
        self, manifest: PullManifest, *, force: bool = False, limit: int | None = None
    ) -> None:
        super().fetch(manifest, force=force, limit=limit)
        # Not errors: the data is still current and still CC BY. They are
        # warnings that must survive into the manifest and the catalog, because
        # a warning that fails the build teaches everyone to ignore the build.
        manifest.warnings.extend(self.check_for_replacement())
        manifest.notes = f"series={self.source.series} {manifest.notes}"

    def check_for_replacement(self) -> list[str]:
        """Re-read DOC's deprecation notices and report any that name a successor.

        Cheap -- one request per layer -- and run on every pull, because the whole
        cost of this migration is finding out about it late.
        """
        notices: list[str] = []
        for layer in self.layers:
            item_id = ITEM_IDS.get(layer.key)
            if not item_id:
                continue
            try:
                payload = self.session.get_json(ITEM_URL.format(item_id=item_id))
            except Exception as exc:  # a notice check must never fail a pull
                notices.append(f"{layer.key}: could not re-read the item notice ({exc})")
                continue
            text = f"{payload.get('snippet') or ''} {payload.get('description') or ''}"
            if "deprecat" in text.lower():
                notices.append(
                    f"{layer.key}: still flagged deprecated by DOC"
                    + (
                        " -- REPLACEMENT LINKED, migrate this adapter"
                        if _links_replacement(text)
                        else ""
                    )
                )
        return notices

    # -- normalization specifics ---------------------------------------------

    def stable_id(self, props: dict[str, Any], layer: ArcGisLayer) -> str | None:
        """Prefer an identifier that survives a republish over ArcGIS's OBJECTID.

        The asset network has FlocID (a functional-location code) and a GlobalID
        GUID. The experience layers have neither, so their DOC web-page GUID is
        used instead. OBJECTID is the last resort and is prefixed, so an id that
        rests on a row number is visible as one.
        """
        found = super().stable_id(props, layer)
        if found is not None:
            return found

        page = _pick(props, _PAGE_KEYS)
        if page:
            match = _GUID_RE.search(str(page))
            if match:
                return f"{layer.key}-{match.group(1)}"

        objectid = props.get("OBJECTID")
        return f"{layer.key}-oid{objectid}" if objectid not in (None, "") else None

    def build_feature(self, local_id, geometry, props, layer, manifest, path) -> Feature:
        feature = super().build_feature(local_id, geometry, props, layer, manifest, path)
        feature.kind = _kind_for(props, layer)
        feature.admin = _pick(props, _REGION_KEYS)

        web_page = _pick(props, _PAGE_KEYS)
        if web_page:
            feature.source_url = str(web_page)

        # DOC spreads metadata across CharName1/CharValue1... pairs. Folding them
        # into one mapping keeps extras readable instead of forty loose keys.
        chars = _characteristics(props)
        feature.extras = {
            k: v
            for k, v in feature.extras.items()
            if not k.startswith(("CharName", "CharValue", "Shape__")) and k != "SortField"
        }
        if chars:
            feature.extras["characteristics"] = chars
        feature.extras["doc_layer"] = layer.key
        return feature


def _links_replacement(text: str) -> bool:
    lowered = text.lower()
    return "replacement" in lowered and "http" in lowered and "will be made available" not in lowered


def _characteristics(props: dict[str, Any]) -> dict[str, str]:
    """Fold DOC's CharName1/CharValue1... attribute pairs into a plain mapping."""
    out: dict[str, str] = {}
    for index in range(1, 40):
        key = props.get(f"CharName{index}")
        value = props.get(f"CharValue{index}")
        if key and value not in (None, "", " "):
            out[str(key)] = str(value)
    return out


def _pick(props: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = props.get(key)
        if value not in (None, "", " "):
            return value
    return None


def _kind_for(props: dict[str, Any], layer: ArcGisLayer) -> str:
    text = " ".join(
        str(props.get(key) or "")
        for key in ("SubObjectType", "ObjectType", "CharValue3", "CharValue7")
    ).lower()
    for needle, kind in _KIND_BY_SUBOBJECT.items():
        if needle in text:
            return kind
    return layer.kind
