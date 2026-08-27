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
:func:`check_for_replacement` re-reads the notice on every refresh so the
migration is caught the day it lands instead of at a pack bake.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from ..fetch import FetchError
from ..formats import geojson
from ..manifest import PullManifest
from ..schema import Feature
from .base import Adapter

ORG = "3JjYDyG3oajxU6HO"
SERVICE_ROOT = f"https://services1.arcgis.com/{ORG}/arcgis/rest/services"

#: ArcGIS item ids behind each service, used to re-read the deprecation notice.
ITEM_IDS = {
    "DOC_Walking_Experiences": "e3f63067394a46238c92f9aed63ff78b",
    "DOC_Mountain_Bike_Tracks": "0fdd22944b1b42ec87f54c11790208f6",
    "DOC_Tracks_EAM": "5cba3b0a2e1041c9ad02ec694f3f3d37",
}
ITEM_URL = "https://www.arcgis.com/sharing/rest/content/items/{item_id}?f=json"

#: Server maxRecordCount is 1000-2000 depending on the layer; stay under both.
PAGE_SIZE = 1000
MAX_PAGES = 400  # runaway-pagination backstop, far above any real layer


@dataclass(frozen=True, slots=True)
class Layer:
    key: str
    service: str
    layer_id: int
    kind: str
    official_status: str


LAYERS: dict[str, tuple[Layer, ...]] = {
    "experiences": (
        Layer("walking", "DOC_Walking_Experiences", 1, "hiking", "doc_walking_experience"),
        Layer("mtb", "DOC_Mountain_Bike_Tracks", 1, "mtb", "doc_mountain_bike_route"),
    ),
    "network": (
        Layer("tracks", "DOC_Tracks_EAM", 0, "hiking", "doc_track_asset"),
    ),
}

#: DOC's SubObjectType vocabulary on the asset network, mapped to normalized kinds.
_KIND_BY_SUBOBJECT = {
    "tramping track": "hiking",
    "walking track": "hiking",
    "route": "hiking",
    "short walk": "hiking",
    "great walk": "hiking",
    "mountain bike": "mtb",
    "cycle": "cycling",
    "bridle": "horse",
    "horse": "horse",
}

#: Identifiers that survive a republish, best first. OBJECTID is deliberately
#: absent -- it is a row number in disguise and churns on every reload.
_STABLE_ID_KEYS = ("FlocID", "GlobalID")
_NAME_KEYS = ("name", "TechObjectName")
_GUID_RE = re.compile(r"([0-9a-f]{32})", re.I)


class NzDocAdapter(Adapter):
    name = "nz_doc"
    phase = "2 - prove it on three continents"

    @property
    def layers(self) -> tuple[Layer, ...]:
        key = self.source.series or "experiences"
        if key not in LAYERS:
            raise ValueError(
                f"{self.source.id}: series {key!r} is not one of {sorted(LAYERS)} "
                f"-- check the `series:` key in sources.yaml"
            )
        return LAYERS[key]

    # -- fetch ---------------------------------------------------------------

    def fetch(
        self, manifest: PullManifest, *, force: bool = False, limit: int | None = None
    ) -> None:
        total = 0
        for layer in self.layers:
            total += self._fetch_layer(layer, manifest, force=force, limit=limit)
        if total == 0:
            raise FetchError(
                f"{self.source.id}: every layer returned zero features. DOC is "
                f"migrating these datasets -- check {SERVICE_ROOT} for the replacement."
            )

        # Not errors: the data is still current and still CC BY. They are
        # warnings that have to survive into the manifest and the catalog.
        manifest.warnings.extend(self.check_for_replacement())
        manifest.notes = f"series={self.source.series} features={total}"

    def _fetch_layer(
        self, layer: Layer, manifest: PullManifest, *, force: bool, limit: int | None
    ) -> int:
        target = self.raw_dir / layer.key
        target.mkdir(parents=True, exist_ok=True)
        query = f"{SERVICE_ROOT}/{layer.service}/FeatureServer/{layer.layer_id}/query"
        max_pages = MAX_PAGES if limit is None else max(1, limit)

        total = 0
        for page in range(max_pages):
            url = (
                f"{query}?where=1%3D1&outFields=*&outSR=4326&f=geojson"
                f"&resultOffset={page * PAGE_SIZE}&resultRecordCount={PAGE_SIZE}"
            )
            record = self.session.download(
                url, target / f"page-{page:04d}.geojson", force=force
            )
            manifest.add(record)

            payload = json.loads(Path(record.path).read_text(encoding="utf-8"))
            if "error" in payload:
                raise FetchError(f"{self.source.id}/{layer.key}: {payload['error']}")
            features = payload.get("features") or []
            total += len(features)

            # ArcGIS says explicitly when there is another page; trusting only a
            # short page would silently truncate a layer whose count is an exact
            # multiple of the page size.
            if not payload.get("exceededTransferLimit") and len(features) < PAGE_SIZE:
                break
        else:
            raise FetchError(
                f"{self.source.id}/{layer.key}: hit the {max_pages}-page backstop "
                f"-- resultOffset may not be honoured"
            )
        return total

    def check_for_replacement(self) -> list[str]:
        """Re-read DOC's deprecation notices and report any that name a successor.

        Cheap (one request per service) and run on every pull, because the whole
        cost of this migration is finding out about it late.
        """
        notices: list[str] = []
        for layer in self.layers:
            item_id = ITEM_IDS.get(layer.service)
            if not item_id:
                continue
            try:
                payload = self.session.get_json(ITEM_URL.format(item_id=item_id))
            except Exception as exc:  # a notice check must never fail a pull
                notices.append(f"{layer.service}: could not re-read the item notice ({exc})")
                continue
            text = f"{payload.get('snippet') or ''} {payload.get('description') or ''}"
            if "deprecat" in text.lower():
                notices.append(
                    f"{layer.service}: still flagged deprecated by DOC"
                    + (" -- REPLACEMENT LINKED, migrate this adapter" if _links_replacement(text) else "")
                )
        return notices

    # -- normalize -----------------------------------------------------------

    def normalize(self, manifest: PullManifest) -> Iterator[Feature]:
        seen: set[str] = set()
        for layer in self.layers:
            directory = self.raw_dir / layer.key
            if not directory.exists():
                continue
            for path in sorted(directory.glob("page-*.geojson")):
                for raw in geojson.load_features(path):
                    feature = self._normalize_one(raw, layer, manifest, path)
                    if feature is not None and feature.id not in seen:
                        seen.add(feature.id)
                        yield feature

    def _normalize_one(
        self, raw: dict[str, Any], layer: Layer, manifest: PullManifest, path: Path
    ) -> Feature | None:
        geometry = geojson.line_geometry(raw.get("geometry"))
        if geometry is None:
            return None

        props = raw.get("properties") or {}
        local_id = self._stable_id(props, layer)
        if local_id is None:
            return None

        name = _pick(props, _NAME_KEYS)
        web_page = _pick(props, ("walkingAndTrampingWebPage", "mountainBikingTrackWebPage"))
        chars = _characteristics(props)

        extras = {
            k: v
            for k, v in props.items()
            if v not in (None, "", " ")
            and not k.startswith(("CharName", "CharValue", "Shape__"))
            and k not in ("OBJECTID", "SortField")
        }
        if chars:
            extras["characteristics"] = chars
        extras["doc_layer"] = layer.key
        extras["source_file"] = path.name

        return self.feature(
            local_id,
            geometry,
            manifest=manifest,
            kind=_kind_for(props, layer),
            name=str(name) if name else None,
            official_status=layer.official_status,
            country="NZ",
            source_url=str(web_page) if web_page else self.source.homepage or None,
            extras=extras,
        )

    def _stable_id(self, props: dict[str, Any], layer: Layer) -> str | None:
        """Prefer an identifier that survives a republish over ArcGIS's OBJECTID.

        The asset network has FlocID (a functional-location code) and a GlobalID
        GUID. The experience layers have neither, so their DOC web-page GUID is
        used instead. OBJECTID is the last resort and is prefixed, so an id that
        rests on a row number is visible as such.
        """
        for key in _STABLE_ID_KEYS:
            value = props.get(key)
            if value not in (None, "", " "):
                return str(value).strip("{}")

        # The experience layers carry neither, but their DOC web-page URL ends in
        # a stable content GUID, which is a far better anchor than a row number.
        page = _pick(props, ("walkingAndTrampingWebPage", "mountainBikingTrackWebPage"))
        if page:
            match = _GUID_RE.search(str(page))
            if match:
                return f"{layer.key}-{match.group(1)}"

        objectid = props.get("OBJECTID")
        return f"{layer.key}-oid{objectid}" if objectid not in (None, "") else None


def _links_replacement(text: str) -> bool:
    lowered = text.lower()
    return "replacement" in lowered and ("http" in lowered and "will be made available" not in lowered)


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


def _kind_for(props: dict[str, Any], layer: Layer) -> str:
    text = " ".join(
        str(props.get(key) or "")
        for key in ("SubObjectType", "ObjectType", "CharValue3", "CharValue7")
    ).lower()
    for needle, kind in _KIND_BY_SUBOBJECT.items():
        if needle in text:
            return kind
    return layer.kind
