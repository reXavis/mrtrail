"""Region packs: the bounding boxes the master database gets cut into."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from .geo import BBox

DEFAULT_PACKS_PATH = Path(__file__).with_name("packs.yaml")


@dataclass(frozen=True, slots=True)
class Pack:
    id: str
    name: str
    bbox: BBox
    pack_gb: float
    status: str = "planned"

    @property
    def pack_bytes(self) -> float:
        return self.pack_gb * 1024**3


def load(path: Path | str | None = None) -> dict[str, Pack]:
    with open(Path(path) if path else DEFAULT_PACKS_PATH, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    packs: dict[str, Pack] = {}
    for pack_id, body in (raw.get("packs") or {}).items():
        west, south, east, north = body["bbox"]
        packs[pack_id] = Pack(
            id=pack_id,
            name=body.get("name", pack_id),
            bbox=(float(west), float(south), float(east), float(north)),
            pack_gb=float(body.get("pack_gb", 0.0)),
            status=body.get("status", "planned"),
        )
    return packs


def parse_bbox(text: str) -> BBox:
    parts = [p.strip() for p in text.split(",")]
    if len(parts) != 4:
        raise ValueError(f"bbox must be 'west,south,east,north', got {text!r}")
    west, south, east, north = (float(p) for p in parts)
    if west > east or south > north:
        raise ValueError(f"bbox {text!r} is inverted (expected west,south,east,north)")
    return (west, south, east, north)
