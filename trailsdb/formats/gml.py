"""Just enough GML to read a WFS GetFeature response.

Some national publishers -- Kartverket among them -- run WFS servers that speak
only GML 3.2. The features are simple: a member element, a handful of scalar
child elements, and one line geometry as a ``gml:posList``. This reader pulls
exactly that out with ElementTree and nothing else.

Axis order is the classic GML trap. A ``srsName`` of ``EPSG:4326`` (short form)
is longitude-first; ``urn:ogc:def:crs:EPSG::4326`` is latitude-first. Both are
handled, and a projected srsName is handed to :mod:`trailsdb.proj`.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any, Iterator

from ..proj import to_wgs84

_GEOMETRY_TAGS = {"LineString", "MultiCurve", "Curve", "LineStringSegment", "curveMember", "segments"}
_SRS_RE = re.compile(r"(\d+)\s*$")


class GmlError(ValueError):
    pass


@dataclass(slots=True)
class GmlFeature:
    type_name: str
    gml_id: str | None
    attributes: dict[str, str]
    lines: list[list[list[float]]] = field(default_factory=list)

    @property
    def geometry(self) -> dict[str, Any] | None:
        if not self.lines:
            return None
        if len(self.lines) == 1:
            return {"type": "LineString", "coordinates": self.lines[0]}
        return {"type": "MultiLineString", "coordinates": self.lines}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def number_matched(payload: bytes) -> int | None:
    """The collection's numberMatched, or None when absent or "unknown".

    Servers put it on the root element, which can sit past several kilobytes
    of namespace declarations; the search stops at the first member.
    """
    head = payload[:200_000]
    cut = head.find(b"member")
    if cut > 0:
        head = head[:cut]
    match = re.search(rb'numberMatched="(\d+)"', head)
    return int(match.group(1)) if match else None


def parse(payload: bytes) -> list[GmlFeature]:
    """Parse a WFS FeatureCollection into features with lon/lat lines."""
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise GmlError(f"not well-formed GML: {exc}") from exc
    if _local(root.tag) == "ExceptionReport":
        text = " ".join(t.strip() for t in root.itertext() if t.strip())
        raise GmlError(f"WFS exception: {text[:300]}")

    features: list[GmlFeature] = []
    for member in root.iter():
        if _local(member.tag) not in ("member", "featureMember"):
            continue
        for element in member:
            features.append(_feature(element))
    return features


def _feature(element: ET.Element) -> GmlFeature:
    attributes: dict[str, str] = {}
    lines: list[list[list[float]]] = []

    def visit(node: ET.Element, inside_geometry: bool) -> None:
        for child in node:
            name = _local(child.tag)
            if name == "posList" or name == "pos":
                srs = _srs_of(child, node)
                coords = _positions(child.text or "", srs, name == "pos")
                if len(coords) >= 2:
                    lines.append(coords)
                continue
            child_is_geometry = inside_geometry or name in _GEOMETRY_TAGS
            if len(child) == 0:
                if not child_is_geometry and child.text and child.text.strip():
                    attributes.setdefault(name, child.text.strip())
            else:
                visit(child, child_is_geometry)

    visit(element, False)
    gml_id = next((v for k, v in element.attrib.items() if _local(k) == "id"), None)
    return GmlFeature(_local(element.tag), gml_id, attributes, lines)


def _srs_of(node: ET.Element, parent: ET.Element) -> str:
    for candidate in (node, parent):
        srs = candidate.get("srsName")
        if srs:
            return srs
    # Walk up is not available in ElementTree; the WFS servers that matter put
    # srsName on the geometry element, which is the parent of posList.
    return "EPSG:4326"


def _positions(text: str, srs: str, single: bool) -> list[list[float]]:
    values = [float(v) for v in text.split()]
    dims = 3 if len(values) % 3 == 0 and len(values) % 2 != 0 else 2
    if single:
        dims = len(values)
    match = _SRS_RE.search(srs)
    epsg = int(match.group(1)) if match else 4326
    lat_first = srs.startswith("urn:") and epsg in (4326, 4258)
    transform = to_wgs84(epsg)

    out: list[list[float]] = []
    for i in range(0, len(values) - dims + 1, dims):
        x, y = values[i], values[i + 1]
        if lat_first:
            x, y = y, x
        if transform is not None:
            x, y = transform(x, y)
        out.append([x, y])
    return out


def iter_features(payload: bytes) -> Iterator[GmlFeature]:
    yield from parse(payload)
