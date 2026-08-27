"""Pull manifests: what a source pull actually fetched, and when.

One manifest per source, written next to its raw files. It is the input to the
quarterly refresh (which files changed?), to the catalog (what is the provenance
of these features?), and to the dated-attribution machinery (EuroVelo's notice
carries the retrieval date recorded here, not today's date at bake time).
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .fetch import FileRecord

MANIFEST_NAME = "_pull.json"


@dataclass(slots=True)
class PullManifest:
    source: str
    adapter: str
    started_at: str = ""
    finished_at: str = ""
    retrieved_on: str = ""
    files: list[FileRecord] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    notes: str = ""

    # Set by adapters whose license or attribution is only knowable at ingest
    # (Geotrek instances, Australian state portals). The normalizer prefers these
    # over the registry template.
    instance_attribution: str | None = None
    resolved_license: str | None = None

    @classmethod
    def start(cls, source: str, adapter: str, *, now: dt.datetime | None = None) -> "PullManifest":
        now = now or dt.datetime.now(dt.timezone.utc)
        return cls(
            source=source,
            adapter=adapter,
            started_at=now.isoformat(timespec="seconds"),
            retrieved_on=now.date().isoformat(),
        )

    def finish(self, *, now: dt.datetime | None = None) -> "PullManifest":
        now = now or dt.datetime.now(dt.timezone.utc)
        self.finished_at = now.isoformat(timespec="seconds")
        return self

    def add(self, record: FileRecord) -> FileRecord:
        self.files.append(record)
        return record

    @property
    def total_bytes(self) -> int:
        return sum(f.size for f in self.files)

    @property
    def changed_files(self) -> int:
        """Files whose bytes actually crossed the wire this run."""
        return sum(1 for f in self.files if not f.cached)

    @property
    def ok(self) -> bool:
        return not self.errors

    def paths(self) -> list[Path]:
        return [Path(f.path) for f in self.files]

    # -- persistence ---------------------------------------------------------

    def write(self, directory: Path) -> Path:
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / MANIFEST_NAME
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        return path

    def to_dict(self) -> dict:
        data = asdict(self)
        data["total_bytes"] = self.total_bytes
        data["changed_files"] = self.changed_files
        return data

    @classmethod
    def read(cls, directory: Path) -> "PullManifest | None":
        path = Path(directory) / MANIFEST_NAME
        if not path.exists():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw.pop("total_bytes", None)
        raw.pop("changed_files", None)
        raw["files"] = [FileRecord(**f) for f in raw.get("files", [])]
        return cls(**raw)
