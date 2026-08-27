"""A small, forgiving GPX reader.

GPX is what CNIG, EuroVelo and most national portals hand out, and in practice
every one of them writes it slightly differently: some use ``<trk>``, some
``<rte>``, some declare the 1.0 namespace, some none at all. This reader accepts
all of that and returns lon/lat lines, dropping elevation -- elevation comes from
the pack's DEM at bake time, never from the source file.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

_GPX_NAMESPACES = (
    "http://www.topografix.com/GPX/1/1",
    "http://www.topografix.com/GPX/1/0",
)


class GpxError(ValueError):
    pass


@dataclass(slots=True)
class GpxTrack:
    name: str | None
    lines: list[list[list[float]]]
    description: str | None = None
    track_type: str | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    @property
    def geometry(self) -> dict[str, Any]:
        if len(self.lines) == 1:
            return {"type": "LineString", "coordinates": self.lines[0]}
        return {"type": "MultiLineString", "coordinates": self.lines}


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child_text(element: ET.Element, name: str) -> str | None:
    for child in element:
        if _localname(child.tag) == name:
            text = (child.text or "").strip()
            return text or None
    return None


def _points(container: ET.Element, point_tag: str) -> list[list[float]]:
    line: list[list[float]] = []
    for node in container.iter():
        if _localname(node.tag) != point_tag:
            continue
        try:
            lon = float(node.attrib["lon"])
            lat = float(node.attrib["lat"])
        except (KeyError, ValueError):
            continue  # a point without usable coordinates is not worth failing over
        line.append([lon, lat])
    return line


def parse(source: Path | str | bytes) -> list[GpxTrack]:
    """Parse a GPX file (or bytes) into tracks. Waypoints are ignored."""
    try:
        if isinstance(source, bytes):
            root = ET.fromstring(source)
        else:
            root = ET.parse(str(source)).getroot()
    except ET.ParseError as exc:
        raise GpxError(f"{source if not isinstance(source, bytes) else '<bytes>'}: {exc}") from exc

    if _localname(root.tag) != "gpx":
        raise GpxError(f"root element is <{_localname(root.tag)}>, expected <gpx>")

    tracks: list[GpxTrack] = []
    for element in root:
        tag = _localname(element.tag)
        if tag == "trk":
            lines = [
                pts
                for segment in element
                if _localname(segment.tag) == "trkseg"
                and (pts := _points(segment, "trkpt"))
                and len(pts) >= 2
            ]
            if lines:
                tracks.append(
                    GpxTrack(
                        name=_child_text(element, "name"),
                        description=_child_text(element, "desc"),
                        track_type=_child_text(element, "type"),
                        lines=lines,
                    )
                )
        elif tag == "rte":
            points = _points(element, "rtept")
            if len(points) >= 2:
                tracks.append(
                    GpxTrack(
                        name=_child_text(element, "name"),
                        description=_child_text(element, "desc"),
                        track_type=_child_text(element, "type"),
                        lines=[points],
                    )
                )
    return tracks


def iter_dir(directory: Path, pattern: str = "*.gpx") -> Iterator[tuple[Path, list[GpxTrack]]]:
    """Yield (path, tracks) for every GPX under ``directory``, sorted for determinism."""
    for path in sorted(Path(directory).rglob(pattern)):
        yield path, parse(path)
