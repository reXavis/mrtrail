"""CNIG: the Spanish trio, and the pipeline's first real pull.

Three series come off the same Centro de Descargas flow and share one adapter:

============================  ==========  ===================================
series                        est. km     what it is
============================  ==========  ===================================
``fedme``                     50,000      FEDME homologated GR/PR/SL senderos
``camino``                    25,000      Camino de Santiago, ~2,221 stages
``caminos_naturales``         10,300      the national Caminos Naturales network
============================  ==========  ===================================

Chosen first deliberately: it is the fiddliest pull in the whole plan (a POST
flow behind a free account, ~9,900 individual files, one request per 1.5 s for
4-6 hours) and it is the one that makes the feature visible in the already-live
Galicia pack on day one. Everything after this is easier.

Discovery -- turning a series into a list of downloadable files -- is the one
piece that depends on CNIG's exact request shape, and that shape is not stable
enough to hard-code blind. So it has two paths:

``files.json`` in the source's raw directory
    A committed index of ``{"id": ..., "name": ...}`` entries. Deterministic,
    offline, and what the tests and CI use.

live discovery
    Queries the download centre and parses whatever it returns -- JSON or HTML.
    The endpoint constants below are marked UNVERIFIED and must be confirmed
    against the live site before a production pull; the health check and the
    loud failure on an empty result are what surface it when they drift.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from ..fetch import FetchError
from ..formats import archive, gpx
from ..manifest import PullManifest
from ..schema import Feature
from .base import Adapter

# --- UNVERIFIED: confirm against centrodedescargas.cnig.es before a real pull ---
# The download centre's search is a POST that returns a list of downloadable
# files for a catalogue "series". Both the endpoint and the direct-download URL
# template are recorded here so drift is a one-line fix rather than a hunt.
SEARCH_ENDPOINT = "https://centrodedescargas.cnig.es/CentroDescargas/buscarFicheros"
DOWNLOAD_TEMPLATE = "https://centrodedescargas.cnig.es/CentroDescargas/descargaDir?secDescDirLA={id}"
# -------------------------------------------------------------------------------

INDEX_NAME = "files.json"

#: Spanish waymarking codes. GR = gran recorrido (>50 km), PR = pequeno
#: recorrido, SL = sendero local; the middle group is the region letter(s), e.g.
#: "PR-G 100" is a Galician pequeno recorrido.
_REF_RE = re.compile(r"\b(GR|PR|SL)[\s\-_]?([A-Z]{1,2})?[\s\-_]?(\d+)\b", re.IGNORECASE)

#: "Etapa 12", "Etapa 12 de 31", "E12", "12 -"
_STAGE_RE = re.compile(r"\b(?:etapa|stage|e)[\s\-_.]*(\d{1,3})\b", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Series:
    key: str
    catalog_id: str
    official_status: str
    kind: str


SERIES: dict[str, Series] = {
    "fedme": Series("fedme", "senderos-homologados", "homologado", "hiking"),
    "camino": Series("camino", "camino-santiago", "camino_oficial", "hiking"),
    "caminos_naturales": Series(
        "caminos_naturales", "caminos-naturales", "camino_natural", "mixed"
    ),
}


class CnigAdapter(Adapter):
    name = "cnig"
    phase = "2 - prove it on three continents"

    @property
    def series(self) -> Series:
        key = self.source.series
        if key not in SERIES:
            raise ValueError(
                f"{self.source.id}: series {key!r} is not one of {sorted(SERIES)} "
                f"-- check the `series:` key in sources.yaml"
            )
        return SERIES[key]

    # -- fetch ---------------------------------------------------------------

    def fetch(
        self, manifest: PullManifest, *, force: bool = False, limit: int | None = None
    ) -> None:
        files = self.discover()
        if not files:
            # Empty is never "this series has no data" -- it means the flow broke.
            raise FetchError(
                f"{self.source.id}: discovery returned no files. Either "
                f"{INDEX_NAME} is missing from {self.raw_dir} or the CNIG search "
                f"flow has changed (see SEARCH_ENDPOINT in this module)."
            )
        if limit is not None:
            files = files[:limit]

        target = self.raw_dir / "files"
        for entry in files:
            url = entry.get("url") or DOWNLOAD_TEMPLATE.format(id=entry["id"])
            filename = _safe_filename(entry.get("name") or f"{entry['id']}.zip")
            manifest.add(self.session.download(url, target / filename, force=force))

        manifest.notes = (
            f"series={self.series.key} files={len(files)} "
            f"(pacing {self.session.rate_limit_s}s/request)"
        )

    def discover(self) -> list[dict[str, Any]]:
        """The file list for this series: committed index first, live query second."""
        index_path = self.raw_dir / INDEX_NAME
        if index_path.exists():
            entries = json.loads(index_path.read_text(encoding="utf-8"))
            return entries.get("files", entries) if isinstance(entries, dict) else entries
        return self.discover_live()

    def discover_live(self) -> list[dict[str, Any]]:
        """Query the download centre. UNVERIFIED request shape -- see module docstring."""
        response = self.session.post(
            SEARCH_ENDPOINT,
            data={"serie": self.series.catalog_id, "pagina": 1},
        )
        if response.status_code != 200:
            raise FetchError(
                f"{self.source.id}: CNIG search returned HTTP {response.status_code}"
            )
        entries = parse_discovery(response)
        # Cache it so a resumed pull does not re-query, and so the exact file list
        # of a pull is auditable afterwards.
        if entries:
            self.raw_dir.mkdir(parents=True, exist_ok=True)
            (self.raw_dir / INDEX_NAME).write_text(
                json.dumps({"series": self.series.key, "files": entries}, indent=2),
                encoding="utf-8",
            )
        return entries

    # -- normalize -----------------------------------------------------------

    def normalize(self, manifest: PullManifest) -> Iterator[Feature]:
        series = self.series
        for path in sorted((self.raw_dir / "files").glob("*")):
            if path.suffix.lower() == ".json" or path.name.endswith(".meta.json"):
                continue
            try:
                payloads = list(archive.iter_gpx(path))
            except archive.UnsupportedFormat:
                # Shapefile-only series need the optional geo extra; skipping
                # silently would understate coverage, so let it surface.
                raise
            for member, data in payloads:
                yield from self._features_from_gpx(path, member, data, series, manifest)

    def _features_from_gpx(
        self,
        path: Path,
        member: str,
        data: bytes,
        series: Series,
        manifest: PullManifest,
    ) -> Iterator[Feature]:
        tracks = gpx.parse(data)
        stem = Path(member).stem or path.stem
        for index, track in enumerate(tracks):
            label = track.name or stem
            ref = extract_ref(label) or extract_ref(stem)
            # The file stem is the stable part: CNIG file names outlive track
            # names, so ids survive a re-publish that only retitles a track.
            local_id = stem if len(tracks) == 1 else f"{stem}-{index + 1}"

            fields: dict[str, Any] = {
                "ref": ref,
                "name": label,
                "official_status": series.official_status,
                "country": "ES",
                "source_url": self.source.homepage or None,
            }
            if series.key == "camino":
                stage = extract_stage(label) or extract_stage(stem)
                variant = variant_name(label, stem)
                if stage is not None:
                    fields["stage_no"] = stage
                if variant:
                    fields["parent_id"] = self.make_id(_slug(variant))
                    fields["parent_name"] = variant

            extras = {"source_file": path.name}
            if member != path.name:
                extras["archive_member"] = member
            if track.description:
                extras["description"] = track.description
            if track.track_type:
                extras["source_type"] = track.track_type

            yield self.feature(
                local_id,
                track.geometry,
                manifest=manifest,
                kind=series.kind,
                extras=extras,
                **fields,
            )


# -- parsing helpers (module level so they are testable without an adapter) ----


def parse_discovery(response: Any) -> list[dict[str, Any]]:
    """Extract ``{"id", "name"}`` entries from whatever the search returns.

    The download centre has served both JSON and HTML for this over time, so
    both are handled rather than assumed.
    """
    content_type = ""
    headers = getattr(response, "headers", None)
    if headers is not None and hasattr(headers, "get"):
        content_type = (headers.get("Content-Type") or "").lower()

    if "json" in content_type:
        try:
            return _entries_from_json(response.json())
        except ValueError:
            pass
    text = getattr(response, "text", "") or ""
    if text.lstrip()[:1] in "[{":
        try:
            return _entries_from_json(json.loads(text))
        except ValueError:
            pass
    return _entries_from_html(text)


def _entries_from_json(payload: Any) -> list[dict[str, Any]]:
    rows = payload
    if isinstance(payload, dict):
        for key in ("data", "ficheros", "files", "results", "rows"):
            if isinstance(payload.get(key), list):
                rows = payload[key]
                break
    if not isinstance(rows, list):
        raise ValueError("no file list in JSON payload")

    entries: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        file_id = _first(row, ("id", "idFichero", "secDescDirLA", "codigo"))
        name = _first(row, ("name", "nombre", "nombreFichero", "fichero", "titulo"))
        if file_id is None and name is None:
            continue
        entries.append({"id": str(file_id or name), "name": str(name or file_id)})
    return entries


_HREF_RE = re.compile(r"secDescDirLA=(\d+)[^\"']*[\"'][^>]*>\s*([^<]{1,200}?)\s*<", re.IGNORECASE)


def _entries_from_html(html: str) -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    for file_id, label in _HREF_RE.findall(html):
        seen.setdefault(file_id, {"id": file_id, "name": label.strip() or f"{file_id}.zip"})
    return list(seen.values())


def _first(row: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if row.get(key) not in (None, ""):
            return row[key]
    return None


def extract_ref(text: str | None) -> str | None:
    """Pull a waymarking code out of a name: 'GR 11', 'PR-G 100', 'SL-G 12'."""
    if not text:
        return None
    match = _REF_RE.search(text)
    if not match:
        return None
    prefix, region, number = match.groups()
    prefix = prefix.upper()
    if region:
        return f"{prefix}-{region.upper()} {number}"
    return f"{prefix} {number}"


def extract_stage(text: str | None) -> int | None:
    if not text:
        return None
    match = _STAGE_RE.search(text)
    return int(match.group(1)) if match else None


def variant_name(label: str, stem: str) -> str | None:
    """The Camino variant a stage belongs to -- 'Camino Frances', 'Via de la Plata'.

    Stage labels are usually ``"<variant>. Etapa 12: A -> B"`` or
    ``"<variant> - Etapa 12"``. Take everything before the stage marker.
    """
    for text in (label, stem):
        if not text:
            continue
        head = _STAGE_RE.split(text)[0]
        head = head.strip(" -_.:;,")
        if len(head) >= 4 and head.lower() != text.lower():
            return head
    return None


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^\w.\-]+", "_", Path(str(name)).name).strip("._")
    return cleaned or "download.bin"
