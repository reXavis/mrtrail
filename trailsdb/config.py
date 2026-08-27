"""Where the pipeline keeps its bytes.

    data/
      raw/{source}/          adapter-shaped downloads, exactly as published
        _pull.json           the pull manifest
      normalized/{source}.geojsonl.gz    the master database, one file per source
      catalog.sqlite         metadata, provenance, bounding boxes
      exports/{pack}/        per-pack bbox cuts, ready for tippecanoe

``raw/`` is the 8-20 GB tier and is disposable -- it can always be re-pulled.
``normalized/`` plus ``catalog.sqlite`` are the ~1.2 GB that matter.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ENV_DATA_ROOT = "TRAILSDB_DATA"
DEFAULT_DATA_ROOT = Path("data")


@dataclass(frozen=True, slots=True)
class Paths:
    root: Path

    @classmethod
    def resolve(cls, root: Path | str | None = None) -> "Paths":
        if root is None:
            root = os.environ.get(ENV_DATA_ROOT) or DEFAULT_DATA_ROOT
        return cls(root=Path(root).expanduser())

    @property
    def raw(self) -> Path:
        return self.root / "raw"

    @property
    def normalized(self) -> Path:
        return self.root / "normalized"

    @property
    def exports(self) -> Path:
        return self.root / "exports"

    @property
    def catalog(self) -> Path:
        return self.root / "catalog.sqlite"

    def raw_dir(self, source_id: str) -> Path:
        return self.raw / source_id

    def normalized_path(self, source_id: str) -> Path:
        return self.normalized / f"{source_id}.geojsonl.gz"

    def export_dir(self, pack: str) -> Path:
        return self.exports / pack

    def ensure(self) -> "Paths":
        for directory in (self.raw, self.normalized, self.exports):
            directory.mkdir(parents=True, exist_ok=True)
        return self
