"""Loader and validator for ``sources.yaml``.

This module is the only place that reads the registry, and it is strict on load:
a source naming a license that does not exist, or an attribution template the
license cannot fill, fails at import time rather than at bake time. That is the
point -- the legal architecture is only worth anything if it cannot be bypassed
by a typo.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

import yaml

from .schema import FEATURE_CLASSES, KINDS

DEFAULT_REGISTRY_PATH = Path(__file__).with_name("sources.yaml")

CADENCES = ("monthly", "quarterly", "annual", "on-demand")
KM_CONFIDENCES = ("official", "assumed")

# ISO 3166-1 alpha-2, plus one supranational pseudo-code for routes that are
# defined at European level rather than by a country.
_COUNTRY_RE = re.compile(r"^[A-Z]{2}$")
_EXTRA_COUNTRY_CODES = {"EU"}

_TEMPLATE_RE = re.compile(r"\{([a-z_]+)\}")


class RegistryError(ValueError):
    """Raised when the registry is internally inconsistent."""


@dataclass(frozen=True, slots=True)
class License:
    id: str
    name: str
    url: str
    family: str
    attribution_required: bool
    share_alike: bool
    dated_attribution: bool = False
    resolved_at_ingest: bool = False


@dataclass(frozen=True, slots=True)
class HealthCheck:
    url: str
    expect_status: int = 200


@dataclass(frozen=True, slots=True)
class Legal:
    verified_on: dt.date | None = None
    notes: str = ""


@dataclass(frozen=True, slots=True)
class Source:
    id: str
    name: str
    publisher: str
    countries: tuple[str, ...]
    adapter: str
    feature_class: str
    default_kind: str
    estimated_km: int
    km_confidence: str
    cadence: str
    license: License
    attribution: str
    homepage: str
    health_check: HealthCheck | None = None
    legal: Legal = field(default_factory=Legal)
    series: str | None = None
    #: Seconds between requests when pulling this source. CNIG needs the slow
    #: lane; a bulk download does not.
    rate_limit_s: float = 1.0

    @property
    def verified(self) -> bool:
        """Has a human confirmed this source's license id and attribution wording?

        ``trailsdb export`` refuses unverified sources, so this is what stands
        between a guess in the registry and a shipped pack.
        """
        return self.legal.verified_on is not None

    @property
    def attribution_placeholders(self) -> tuple[str, ...]:
        return tuple(_TEMPLATE_RE.findall(self.attribution))

    def attribution_for(
        self,
        *,
        retrieved_on: dt.date | str | None = None,
        instance_attribution: str | None = None,
    ) -> str:
        """Resolve the attribution template into the exact string stored per feature.

        EuroVelo's notice must carry the retrieval date; Geotrek and the
        multi-portal sources carry the operating body's own wording. Every other
        source resolves to itself.
        """
        values: dict[str, Any] = {}
        for placeholder in self.attribution_placeholders:
            if placeholder == "retrieved_on":
                if retrieved_on is None:
                    raise RegistryError(
                        f"{self.id}: attribution needs a retrieval date "
                        f"({self.license.id} requires a dated notice)"
                    )
                values["retrieved_on"] = (
                    retrieved_on.isoformat()
                    if isinstance(retrieved_on, dt.date)
                    else str(retrieved_on)
                )
            elif placeholder == "instance_attribution":
                if not instance_attribution:
                    raise RegistryError(
                        f"{self.id}: attribution is per-instance and the adapter "
                        f"supplied none -- an unattributed instance must be skipped"
                    )
                values["instance_attribution"] = instance_attribution
            else:
                raise RegistryError(f"{self.id}: unknown attribution placeholder {placeholder!r}")
        return self.attribution.format(**values)


@dataclass(frozen=True, slots=True)
class Registry:
    licenses: dict[str, License]
    sources: dict[str, Source]

    def __iter__(self) -> Iterator[Source]:
        return iter(self.sources.values())

    def __len__(self) -> int:
        return len(self.sources)

    def get(self, source_id: str) -> Source:
        try:
            return self.sources[source_id]
        except KeyError:
            raise RegistryError(
                f"unknown source {source_id!r}; known: {', '.join(sorted(self.sources))}"
            ) from None

    def by_adapter(self, adapter: str) -> list[Source]:
        return [s for s in self.sources.values() if s.adapter == adapter]

    @property
    def adapters(self) -> list[str]:
        return sorted({s.adapter for s in self.sources.values()})

    def select(self, ids: list[str] | None) -> list[Source]:
        """Resolve a CLI selection: source ids, adapter names, or everything."""
        if not ids:
            return list(self.sources.values())
        chosen: dict[str, Source] = {}
        for token in ids:
            if token in self.sources:
                chosen[token] = self.sources[token]
                continue
            matched = self.by_adapter(token)
            if not matched:
                raise RegistryError(f"{token!r} matches no source id and no adapter name")
            for source in matched:
                chosen[source.id] = source
        return list(chosen.values())

    @property
    def total_estimated_km(self) -> int:
        return sum(s.estimated_km for s in self.sources.values())

    def share_alike_sources(self) -> list[Source]:
        """Sources whose license forbids being combined with the rest."""
        return [s for s in self.sources.values() if s.license.share_alike]


def load(path: Path | str | None = None) -> Registry:
    path = Path(path) if path else DEFAULT_REGISTRY_PATH
    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    return parse(raw, origin=str(path))


def parse(raw: dict[str, Any], *, origin: str = "<registry>") -> Registry:
    if not isinstance(raw, dict):
        raise RegistryError(f"{origin}: registry must be a mapping")

    licenses: dict[str, License] = {}
    for license_id, body in (raw.get("licenses") or {}).items():
        try:
            licenses[license_id] = License(
                id=license_id,
                name=body["name"],
                url=body.get("url", ""),
                family=body["family"],
                attribution_required=bool(body["attribution_required"]),
                share_alike=bool(body["share_alike"]),
                dated_attribution=bool(body.get("dated_attribution", False)),
                resolved_at_ingest=bool(body.get("resolved_at_ingest", False)),
            )
        except KeyError as exc:
            raise RegistryError(f"{origin}: license {license_id!r} is missing {exc}") from None

    sources: dict[str, Source] = {}
    for source_id, body in (raw.get("sources") or {}).items():
        sources[source_id] = _parse_source(source_id, body, licenses, origin)

    return Registry(licenses=licenses, sources=sources)


def _parse_source(
    source_id: str, body: dict[str, Any], licenses: dict[str, License], origin: str
) -> Source:
    def require(key: str) -> Any:
        if key not in body:
            raise RegistryError(f"{origin}: source {source_id!r} is missing {key!r}")
        return body[key]

    license_id = require("license")
    if license_id not in licenses:
        raise RegistryError(f"{origin}: source {source_id!r} names unknown license {license_id!r}")
    lic = licenses[license_id]

    feature_class = require("feature_class")
    if feature_class not in FEATURE_CLASSES:
        raise RegistryError(
            f"{origin}: source {source_id!r} has feature_class {feature_class!r}, "
            f"expected one of {FEATURE_CLASSES}"
        )

    default_kind = body.get("default_kind", "hiking")
    if default_kind not in KINDS:
        raise RegistryError(f"{origin}: source {source_id!r} has unknown kind {default_kind!r}")

    cadence = body.get("cadence", "quarterly")
    if cadence not in CADENCES:
        raise RegistryError(f"{origin}: source {source_id!r} has unknown cadence {cadence!r}")

    km_confidence = body.get("km_confidence", "assumed")
    if km_confidence not in KM_CONFIDENCES:
        raise RegistryError(
            f"{origin}: source {source_id!r} has km_confidence {km_confidence!r}, "
            f"expected one of {KM_CONFIDENCES}"
        )

    countries = tuple(require("countries"))
    for code in countries:
        if not _COUNTRY_RE.match(code) and code not in _EXTRA_COUNTRY_CODES:
            raise RegistryError(f"{origin}: source {source_id!r} has bad country code {code!r}")

    attribution = require("attribution")
    if not attribution.strip():
        raise RegistryError(f"{origin}: source {source_id!r} has an empty attribution")
    placeholders = set(_TEMPLATE_RE.findall(attribution))
    unknown = placeholders - {"retrieved_on", "instance_attribution"}
    if unknown:
        raise RegistryError(
            f"{origin}: source {source_id!r} attribution has unknown placeholders {sorted(unknown)}"
        )
    if "retrieved_on" in placeholders and not lic.dated_attribution:
        raise RegistryError(
            f"{origin}: source {source_id!r} dates its attribution but license "
            f"{lic.id!r} is not marked dated_attribution"
        )
    if "instance_attribution" in placeholders and not (
        lic.resolved_at_ingest or len(countries) > 0
    ):  # pragma: no cover - defensive
        raise RegistryError(f"{origin}: source {source_id!r} cannot resolve instance attribution")

    hc_body = body.get("health_check")
    health_check = (
        HealthCheck(url=hc_body["url"], expect_status=int(hc_body.get("expect_status", 200)))
        if hc_body
        else None
    )

    legal_body = body.get("legal") or {}
    verified_on = legal_body.get("verified_on")
    if isinstance(verified_on, str):
        verified_on = dt.date.fromisoformat(verified_on)
    elif isinstance(verified_on, dt.datetime):
        verified_on = verified_on.date()
    elif verified_on is not None and not isinstance(verified_on, dt.date):
        raise RegistryError(f"{origin}: source {source_id!r} has a non-date verified_on")

    return Source(
        id=source_id,
        name=require("name"),
        publisher=body.get("publisher", ""),
        countries=countries,
        adapter=require("adapter"),
        feature_class=feature_class,
        default_kind=default_kind,
        estimated_km=int(require("estimated_km")),
        km_confidence=km_confidence,
        cadence=cadence,
        license=lic,
        attribution=attribution,
        homepage=body.get("homepage", ""),
        health_check=health_check,
        legal=Legal(verified_on=verified_on, notes=(legal_body.get("notes") or "").strip()),
        series=body.get("series"),
        rate_limit_s=float(body.get("rate_limit_s", 1.0)),
    )
