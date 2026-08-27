"""The adapter contract.

An adapter does exactly two things:

``fetch``
    Put the source's bytes under ``raw/{source}/``, exactly as published, and
    record every file in the manifest. No transformation here -- keeping the raw
    tier byte-faithful is what makes a bad normalizer a re-run rather than a
    re-download.

``normalize``
    Turn those bytes into normalized :class:`~trailsdb.schema.Feature` objects.
    Pure function of the raw tier: no network, so it can be iterated on in
    seconds against data already on disk.

Adapters never construct a Feature directly -- they call :meth:`Adapter.feature`,
which stamps source, license and attribution from the registry. That is the one
mechanism standing between "15 adapters" and "15 chances to ship unattributed
data".
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from ..config import Paths
from ..fetch import PoliteSession
from ..manifest import PullManifest
from ..registry import Source
from ..schema import Feature

_UNSAFE_ID = re.compile(r"[^A-Za-z0-9_.:/\-]+")


class AdapterNotImplemented(NotImplementedError):
    """Raised for a source whose adapter is planned but not written yet."""


@dataclass(slots=True)
class AdapterContext:
    source: Source
    paths: Paths
    session: PoliteSession


class Adapter(ABC):
    #: Matches the ``adapter:`` key in sources.yaml.
    name: str = ""
    #: Which wave of the execution order this adapter belongs to; reported by the CLI.
    phase: str = ""

    def __init__(self, ctx: AdapterContext) -> None:
        self.ctx = ctx
        self.source = ctx.source
        self.paths = ctx.paths
        self.session = ctx.session

    # -- lifecycle -----------------------------------------------------------

    @property
    def raw_dir(self) -> Path:
        return self.paths.raw_dir(self.source.id)

    def pull(self, *, force: bool = False, limit: int | None = None) -> PullManifest:
        """Run ``fetch`` inside a manifest, capturing failures rather than hiding them."""
        manifest = PullManifest.start(self.source.id, self.name)
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.fetch(manifest, force=force, limit=limit)
        except Exception as exc:
            # A source that moved must be visible in the manifest and the catalog,
            # not swallowed into a quietly smaller dataset.
            manifest.errors.append(f"{type(exc).__name__}: {exc}")
        manifest.finish()
        manifest.write(self.raw_dir)
        return manifest

    @abstractmethod
    def fetch(self, manifest: PullManifest, *, force: bool = False, limit: int | None = None):
        """Download this source into ``self.raw_dir``, recording files on ``manifest``."""

    @abstractmethod
    def normalize(self, manifest: PullManifest) -> Iterator[Feature]:
        """Yield normalized features from the raw tier. Must not touch the network."""

    def load_manifest(self) -> PullManifest:
        manifest = PullManifest.read(self.raw_dir)
        if manifest is None:
            raise FileNotFoundError(
                f"{self.source.id}: no pull manifest in {self.raw_dir} -- run `trailsdb pull` first"
            )
        return manifest

    # -- helpers every adapter uses ------------------------------------------

    def make_id(self, local_id: str | int) -> str:
        """``source:local_id``, with characters the schema rejects folded to '-'."""
        cleaned = _UNSAFE_ID.sub("-", str(local_id)).strip("-")
        if not cleaned:
            raise ValueError(f"{self.source.id}: cannot build a stable id from {local_id!r}")
        return f"{self.source.id}:{cleaned}"

    def feature(
        self,
        local_id: str | int,
        geometry: dict[str, Any],
        *,
        manifest: PullManifest | None = None,
        attribution: str | None = None,
        license_id: str | None = None,
        feature_class: str | None = None,
        kind: str | None = None,
        **fields: Any,
    ) -> Feature:
        """Build a normalized feature with provenance already attached."""
        resolved_attribution = attribution or self.resolve_attribution(manifest)
        return Feature(
            id=self.make_id(local_id),
            source=self.source.id,
            license=license_id
            or (manifest.resolved_license if manifest else None)
            or self.source.license.id,
            attribution=resolved_attribution,
            feature_class=feature_class or self.source.feature_class,
            kind=kind or self.source.default_kind,
            geometry=geometry,
            **fields,
        )

    def resolve_attribution(self, manifest: PullManifest | None) -> str:
        """Fill the registry's attribution template for this pull.

        EuroVelo's notice needs the retrieval date the manifest recorded; Geotrek
        and the state portals need the operator's own wording, which the adapter
        put on the manifest at ingest.
        """
        return self.source.attribution_for(
            retrieved_on=manifest.retrieved_on if manifest else None,
            instance_attribution=manifest.instance_attribution if manifest else None,
        )
