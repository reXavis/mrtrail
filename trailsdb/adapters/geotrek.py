"""Geotrek: France's trail platform, one adapter across many operators.

Geotrek is open-source software run by ~80 parks, départements and tourist
offices, each with its own database, its own portal and its own admin API. The
plan's "France — Geotrek instances" is therefore not one source but a fleet,
and this adapter treats it as one: ``geotrek_instances.yaml`` lists the
deployments, and every pull walks each instance's keyless REST API v2.

Three things the API taught this adapter:

* **Vocabulary ids differ per instance.** Practice 2 is "VTT" on Ecrins and
  "Cyclo" on Gavarnie. Every instance's practice, difficulty, network and
  source tables are fetched and mapped by *label*, never by id.
* **Geometry is 3D** and dropped to 2D here; elevation comes from the pack DEM.
* **The data licence is not in the API.** Each operator's terms live on its own
  site, and the one legal page read so far (Ecrins) says nothing about the trek
  data at all. So an instance is pulled when it is public, but ships only once
  its ``licence`` in the instances file is set from the operator's own text.
  Silence is not permission.

Per-trek attribution is the instance plus the trek's declared data sources
("Parc national des Ecrins / CDRP des Hautes-Alpes"), which is what the
operators themselves credit.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

import yaml

from ..fetch import FetchError
from ..formats import geojson
from ..manifest import PullManifest
from ..schema import Feature
from .base import Adapter

INSTANCES_PATH = Path(__file__).resolve().parent.parent / "geotrek_instances.yaml"

PAGE_SIZE = 200
TREK_FIELDS = (
    "id,uuid,name,length_2d,practice,difficulty,source,networks,structure,"
    "departure,arrival,duration,ascent,external_id,update_datetime,geometry"
)
VOCABULARIES = ("source", "trek_practice", "trek_difficulty", "trek_network")

#: Practice labels (lower-cased) to normalized kinds. Anything on foot is hiking.
_KIND_BY_PRACTICE = (
    ("vtt", "mtb"),
    ("enduro", "mtb"),
    ("gravel", "cycling"),
    ("cyclo", "cycling"),
    ("vélo", "cycling"),
    ("velo", "cycling"),
    ("cheval", "horse"),
    ("équestre", "horse"),
    ("raquette", "ski"),
    ("ski", "ski"),
    ("snow", "ski"),
    ("alpinisme", "other"),
    ("canoë", "paddle"),
    ("kayak", "paddle"),
)


@dataclass(frozen=True, slots=True)
class Instance:
    key: str
    name: str
    portal: str
    api: str
    licence: str | None
    attribution: str
    verified_on: str | None
    treks: int | None = None

    @property
    def closed(self) -> bool:
        return (self.licence or "").lower() == "closed"


def load_instances(path: Path = INSTANCES_PATH) -> list[Instance]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out = []
    for key, body in (raw.get("instances") or {}).items():
        out.append(
            Instance(
                key=key,
                name=body["name"],
                portal=body.get("portal", ""),
                api=(body.get("api") or "").rstrip("/"),
                licence=body.get("licence"),
                attribution=body.get("attribution") or body["name"],
                verified_on=str(body["verified_on"]) if body.get("verified_on") else None,
                treks=body.get("treks"),
            )
        )
    return out


class GeotrekAdapter(Adapter):
    name = "geotrek"
    phase = "4 - Europe wave"

    def __init__(self, ctx) -> None:
        super().__init__(ctx)
        self.instances = [i for i in load_instances() if i.api and not i.closed]

    # -- fetch ---------------------------------------------------------------

    def fetch(
        self, manifest: PullManifest, *, force: bool = False, limit: int | None = None
    ) -> None:
        total = 0
        pulled = 0
        for instance in self.instances:
            try:
                total += self._fetch_instance(instance, manifest, force=force, limit=limit)
                pulled += 1
            except FetchError as exc:
                # One operator's outage must not lose the other ten.
                manifest.warnings.append(f"{instance.key}: {exc}")
        if pulled == 0:
            raise FetchError(f"{self.source.id}: no instance could be pulled")
        manifest.notes = f"instances={pulled}/{len(self.instances)} treks={total}"

    def _fetch_instance(
        self, instance: Instance, manifest: PullManifest, *, force: bool, limit: int | None
    ) -> int:
        target = self.raw_dir / instance.key
        target.mkdir(parents=True, exist_ok=True)
        for vocab in VOCABULARIES:
            manifest.add(
                self.session.download(
                    f"{instance.api}/{vocab}/?page_size=500&language=fr",
                    target / f"{vocab}.json",
                    force=force,
                )
            )

        total = 0
        pages = 500 if limit is None else max(1, limit)
        for page in range(1, pages + 1):
            url = (
                f"{instance.api}/trek/?format=geojson&published=true&language=fr"
                f"&page_size={PAGE_SIZE}&page={page}&fields={TREK_FIELDS}"
            )
            record = self.session.download(url, target / f"page-{page:04d}.geojson", force=force)
            manifest.add(record)
            payload = json.loads(Path(record.path).read_text(encoding="utf-8"))
            if "features" not in payload:
                raise FetchError(f"{instance.key}: {str(payload)[:200]}")
            total += len(payload["features"])
            if not payload.get("next"):
                break
        return total

    # -- normalize -----------------------------------------------------------

    def normalize(self, manifest: PullManifest) -> Iterator[Feature]:
        for instance in self.instances:
            directory = self.raw_dir / instance.key
            if not directory.exists():
                continue
            vocab = {v: _labels(directory / f"{v}.json") for v in VOCABULARIES}
            seen: set[str] = set()
            for path in sorted(directory.glob("page-*.geojson")):
                for raw in geojson.load_features(path):
                    feature = self._normalize_one(raw, instance, vocab, manifest)
                    if feature is not None and feature.id not in seen:
                        seen.add(feature.id)
                        yield feature

    def _normalize_one(self, raw, instance: Instance, vocab, manifest) -> Feature | None:
        geometry = geojson.line_geometry(raw.get("geometry"))
        if geometry is None:
            return None
        props = raw.get("properties") or {}
        uuid = props.get("uuid") or raw.get("id")
        if not uuid:
            return None

        practice = vocab["trek_practice"].get(props.get("practice"), "")
        difficulty = vocab["trek_difficulty"].get(props.get("difficulty"), "")
        networks = [vocab["trek_network"].get(n, "") for n in props.get("networks") or []]
        sources = [vocab["source"].get(s, "") for s in props.get("source") or []]
        # Operators often list themselves as a source; crediting "Parc National
        # des Ecrins / Parc national des Ecrins" helps nobody.
        sources = [
            s for s in sources
            if s and s.strip().lower() != instance.attribution.strip().lower()
        ]

        attribution = instance.attribution
        if sources:
            attribution = f"{instance.attribution} / {', '.join(sources)}"

        extras: dict[str, Any] = {
            "instance": instance.key,
            "portal": instance.portal,
            "practice": practice or None,
            "difficulty": difficulty or None,
            "networks": [n for n in networks if n] or None,
            "departure": props.get("departure") or None,
            "arrival": props.get("arrival") or None,
            "duration_h": props.get("duration"),
            "source_ascent_m": props.get("ascent"),
            "geotrek_id": props.get("id"),
            "updated": props.get("update_datetime"),
        }
        extras = {k: v for k, v in extras.items() if v not in (None, "", [])}

        return self.feature(
            f"{instance.key}-{uuid}",
            geometry,
            manifest=manifest,
            attribution=attribution,
            license_id=instance.licence if instance.licence and not instance.closed else None,
            kind=_kind_for(practice),
            name=(props.get("name") or "").strip() or None,
            ref=_ref_from_networks(networks),
            official_status=_status_from_networks(networks),
            country="FR",
            admin=instance.name,
            source_url=instance.portal or None,
            extras=extras,
        )


def _labels(path: Path) -> dict[int, str]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("results", payload) if isinstance(payload, dict) else payload
    out: dict[int, str] = {}
    for row in rows or []:
        label = row.get("name") or row.get("label") or ""
        if isinstance(label, dict):  # multilingual {"fr": ..., "en": ...}
            label = label.get("fr") or next((v for v in label.values() if v), "")
        out[row.get("id")] = str(label).replace("\xa0", " ").strip()
    return out


def _kind_for(practice: str) -> str:
    text = (practice or "").lower()
    for needle, kind in _KIND_BY_PRACTICE:
        if needle in text:
            return kind
    return "hiking"


def _ref_from_networks(networks: list[str]) -> str | None:
    for label in networks:
        if label.upper() in ("GR", "GRP", "PR"):
            return label.upper()
    return None


def _status_from_networks(networks: list[str]) -> str:
    labels = {n.upper() for n in networks}
    if "GR" in labels:
        return "geotrek_gr"
    if "GRP" in labels:
        return "geotrek_grp"
    if "PR" in labels:
        return "geotrek_pr"
    return "geotrek_trek"
