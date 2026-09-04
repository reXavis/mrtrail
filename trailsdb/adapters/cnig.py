"""CNIG: the Spanish series, and the pipeline's fiddliest pull.

The plan named this the first concrete step, and it was right about why: it is
the most involved download flow in the whole project, and Spanish data lights up
the already-live Galicia pack immediately.

The Centro de Descargas flow, as it actually works:

1. ``POST archivosTotalesSerie`` with ``codSerie`` returns an HTML listing and a
   hidden ``totalArchivos`` count. ``numPagina`` walks the pages.
2. Each row carries a file id in its ``tdAcciones_{id}`` cell, plus a name, a
   format and a date. Every route is published twice, as GPX and as KML.
3. ``POST descargaDir`` with ``secDescDirLA={id}`` returns the file.

Two corrections to what the plan assumed. **No account is needed** -- direct
download works unauthenticated. And the file count is a little lower than
estimated, because half of every listing is KML duplicates we skip:

======  ======================  =============  ===========
code    series                  files          GPX routes
======  ======================  =============  ===========
FEDME   Senderos homologados            6,923       ~3,461
CSANT   Caminos de Santiago             2,221       ~1,110
CACID   Camino del Cid                    299         ~149
RTPAS   Rutas de Pasion                    29          ~14
======  ======================  =============  ===========

CSANT's 2,221 is exactly the stage count the plan predicted.

Caminos Naturales is deliberately absent: it is not in the download centre under
any of these codes. It is published by the Ministerio de Agricultura separately,
so it gets its own adapter rather than a wrong guess here.

Licensing: the download centre's legal notice puts these products under an IGN
use licence "compatible con CC-BY 4.0" (Orden FOM/2807/2015), and the licence
document prescribes the exact citation form::

    <identificador del producto> <fecha> CC-BY 4.0 <atribucion de productores>

with the example ``BTN25 2014-2015 CC-BY 4.0 ign.es``. That template is what the
registry stores; the product identifier and date come from the series and the
listing.
"""

from __future__ import annotations

import html
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

BASE = "https://centrodedescargas.cnig.es/CentroDescargas/"
HOME_URL = BASE + "home"
LISTING_URL = BASE + "archivosTotalesSerie"
DOWNLOAD_URL = BASE + "descargaDir"
#: Series that moved to S3 answer this with a page carrying a pre-signed URL.
DOWNLOAD_S3_URL = BASE + "descargaDirS3"
DETAIL_URL = BASE + "detalleArchivo?sec={file_id}"
LICENCE_DOCUMENT = "https://www.ign.es/resources/licencia/Condiciones_licenciaUso_IGN.pdf"

INDEX_NAME = "files.json"

#: Spanish waymarking codes. GR = gran recorrido (>50 km), PR = pequeno
#: recorrido, SL = sendero local; the middle group is the region letter(s), so
#: "PR-G 100" is a Galician pequeno recorrido.
_REF_RE = re.compile(r"\b(GR|PR|SL)[\s\-_]?([A-Z]{1,2})?[\s\-_]?(\d+)\b", re.IGNORECASE)

#: "Etapa 12", "Etapa 12 de 31", "E12" -- how the FEDME series labels stages.
_STAGE_RE = re.compile(r"\b(?:etapa|stage|e)[\s\-_.]*(\d{1,3})\b", re.IGNORECASE)

#: The Camino series names files as
#: ``"<group> - <CODE>-<stage><variant>-<from>-<to>"``, e.g.
#: ``"Caminos del Norte - Camino Primitivo - ES05a-03b-grado-salas"``. This
#: matches all 1,073 published GPX names, and the code's country prefix is what
#: gets each stage its right country: 200 of them are in France and 123 in
#: Portugal, not Spain.
_CAMINO_RE = re.compile(
    r"^(?P<group>.+?)\s+-\s+"
    r"(?P<code>(?P<country>[A-Z]{2})\d{2}[a-z]?)-"
    r"(?P<stage>\d{1,3})(?P<variant>[a-z]?)-"
    r"(?P<section>.*)$"
)

#: The Caminos Naturales series names files ``CNT<route>-<stage><variant>-<title>``:
#: ``CNT102-0021-senda-de-souta-da-vila-ramal-petroglifos`` is route 102, stage
#: 2, variant 1. The four digits are stage x 10 + variant, so ``0400`` is stage
#: 40 of the main line. The route's own name travels inside the GPX ``<desc>``.
_CNT_RE = re.compile(r"^(?P<route>CNT\d{3})[-_](?P<stage>\d{3})(?P<variant>\d)[-_](?P<title>.+)$")
_PRESIGNED_RE = re.compile(r'id="urlPregsigned"\s+value="([^"]+)"')

_TOTAL_RE = re.compile(r'id="totalArchivos"[^>]*value="(\d+)"')
_ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
_FILE_ID_RE = re.compile(r'id="tdAcciones_(\d+)"')
# The listing marks its cells by class rather than position: the route name is
# left-aligned, every other value is centred. Reading them by class survives a
# column being added or reordered, which a positional parser would not.
_NAME_CELL_RE = re.compile(r'txtLeftCenterTablas">([^<]*)<')
_VALUE_CELL_RE = re.compile(r'centrarCamposTablaTd[^"]*">([^<]*)<')


@dataclass(frozen=True, slots=True)
class Series:
    key: str
    code: str
    product_id: str
    official_status: str
    kind: str
    #: "direct" (POST descargaDir streams the file) or "s3" (the centre hands
    #: back a pre-signed bucket URL instead). A direct series that answers with
    #: a page is retried the S3 way, so a migrated series keeps working.
    hosting: str = "direct"


SERIES: dict[str, Series] = {
    "fedme": Series("fedme", "FEDME", "Senderos FEDME", "homologado", "hiking"),
    "camino": Series("camino", "CSANT", "Caminos de Santiago", "camino_oficial", "hiking"),
    "camino_cid": Series("camino_cid", "CACID", "Camino del Cid", "camino_del_cid", "mixed"),
    "rutas_pasion": Series("rutas_pasion", "RTPAS", "Rutas de Pasion", "ruta_tematica", "mixed"),
    "caminos_naturales": Series(
        "caminos_naturales", "RTCNT", "Caminos Naturales", "camino_natural", "mixed", hosting="s3"
    ),
}


@dataclass(frozen=True, slots=True)
class ListedFile:
    file_id: str
    name: str
    fmt: str
    date: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"id": self.file_id, "name": self.name, "format": self.fmt, "date": self.date}


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
                f"{INDEX_NAME} is missing from {self.raw_dir} or the Centro de "
                f"Descargas listing has changed (see LISTING_URL in this module)."
            )

        # Every route is published as both GPX and KML; GPX alone halves the pull.
        gpx_files = [f for f in files if f.fmt.upper() == "GPX"] or files
        if limit is not None:
            gpx_files = gpx_files[:limit]

        target = self.raw_dir / "files"
        for entry in gpx_files:
            manifest.add(self._download(entry, target, force=force))

        manifest.notes = (
            f"series={self.series.key} code={self.series.code} "
            f"files={len(gpx_files)} of {len(files)} listed (GPX only, KML skipped) "
            f"pacing={self.session.rate_limit_s}s"
        )

    def _download(self, entry: ListedFile, target: Path, *, force: bool):
        dest = target / _safe_filename(f"{entry.file_id}_{entry.name}.gpx")
        if self.series.hosting == "s3":
            return self._download_via_s3(entry, dest, force=force)
        record = self.session.download(
            DOWNLOAD_URL, dest, method="POST", data={"secDescDirLA": entry.file_id}, force=force
        )
        if record.status == 200 and _is_html(dest):
            # A page where the file should be: the centre serves this one from
            # S3 now. Never keep it -- an HTML "GPX" would normalize to nothing.
            return self._download_via_s3(entry, dest, force=True)
        return record

    def _download_via_s3(self, entry: ListedFile, dest: Path, *, force: bool):
        page = self.session.post(DOWNLOAD_S3_URL, data={"secuencial": entry.file_id})
        match = _PRESIGNED_RE.search(getattr(page, "text", "") or "")
        if page.status_code != 200 or not match:
            raise FetchError(
                f"{self.source.id}: no pre-signed URL for file {entry.file_id} "
                f"({entry.name}); descargaDirS3 returned HTTP {page.status_code}"
            )
        record = self.session.download(html.unescape(match.group(1)), dest, force=force)
        if record.status == 200 and _is_html(dest):
            dest.unlink(missing_ok=True)
            raise FetchError(f"{self.source.id}: S3 returned a page for file {entry.file_id}")
        return record

    def discover(self) -> list[ListedFile]:
        """The file list for this series: cached index first, live listing second."""
        index_path = self.raw_dir / INDEX_NAME
        if index_path.exists():
            raw = json.loads(index_path.read_text(encoding="utf-8"))
            rows = raw.get("files", raw) if isinstance(raw, dict) else raw
            return [
                ListedFile(r["id"], r.get("name", ""), r.get("format", ""), r.get("date", ""))
                for r in rows
            ]
        return self.discover_live()

    def discover_live(self, *, max_pages: int = 500) -> list[ListedFile]:
        """Walk the paginated listing for this series.

        The download centre keeps listing state in a session, so the home page is
        fetched once first. Pagination stops when a page repeats what the last one
        gave -- the listing happily serves the final page forever past the end.
        """
        self.session.get(HOME_URL)
        series = self.series
        found: dict[str, ListedFile] = {}
        total: int | None = None

        for page in range(1, max_pages + 1):
            response = self.session.post(
                LISTING_URL,
                data={
                    "codSerie": series.code,
                    "numPagina": str(page),
                    "totalArchivos": str(total or ""),
                },
            )
            if response.status_code != 200:
                raise FetchError(
                    f"{self.source.id}: listing page {page} returned HTTP {response.status_code}"
                )
            html_text = getattr(response, "text", "") or ""
            if total is None:
                total = parse_total(html_text)

            before = len(found)
            for listed in parse_rows(html_text):
                found.setdefault(listed.file_id, listed)
            if len(found) == before:
                break  # the listing has stopped yielding anything new
            if total and len(found) >= total:
                break

        entries = list(found.values())
        if entries:
            self.raw_dir.mkdir(parents=True, exist_ok=True)
            (self.raw_dir / INDEX_NAME).write_text(
                json.dumps(
                    {
                        "series": series.key,
                        "code": series.code,
                        "total_listed": total,
                        "files": [e.as_dict() for e in entries],
                    },
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        return entries

    # -- normalize -----------------------------------------------------------

    def normalize(self, manifest: PullManifest) -> Iterator[Feature]:
        series = self.series
        listed = {f.file_id: f for f in self.discover()}
        for path in sorted((self.raw_dir / "files").glob("*")):
            if path.name.endswith(".meta.json") or path.suffix.lower() == ".json":
                continue
            file_id = path.name.split("_", 1)[0]
            meta = listed.get(file_id)
            for member, data in archive.iter_gpx(path):
                yield from self._features_from_gpx(path, member, data, series, meta, manifest)

    def _features_from_gpx(
        self,
        path: Path,
        member: str,
        data: bytes,
        series: Series,
        meta: ListedFile | None,
        manifest: PullManifest,
    ) -> Iterator[Feature]:
        tracks = gpx.parse(data)
        stem = Path(member).stem or path.stem
        listed_name = meta.name if meta else None

        for index, track in enumerate(tracks):
            label = listed_name or track.name or stem
            ref = extract_ref(label) or extract_ref(stem)
            # The download-centre file id is the stable anchor: it outlives the
            # published file name, which carries the route title.
            local_id = (
                meta.file_id if meta and len(tracks) == 1
                else f"{meta.file_id}-{index + 1}" if meta
                else (stem if len(tracks) == 1 else f"{stem}-{index + 1}")
            )

            fields: dict[str, Any] = {
                "ref": ref,
                "name": label,
                "official_status": series.official_status,
                "country": "ES",
                "source_url": DETAIL_URL.format(file_id=meta.file_id) if meta else None,
            }
            camino = parse_camino_name(label) if series.key == "camino" else None
            natural = (
                parse_cnt_name(listed_name) or parse_cnt_name(stem)
                if series.key == "caminos_naturales"
                else None
            )
            if camino:
                # The route code is the stable grouping key; the group name is
                # what a user recognises ("Caminos del Norte - Camino Primitivo").
                fields["parent_id"] = self.make_id(camino["code"].lower())
                fields["parent_name"] = camino["group"]
                fields["stage_no"] = camino["stage"]
                fields["country"] = camino["country"]
            elif natural:
                # The file name carries the stage title; the GPX description
                # carries the Camino Natural the stage belongs to.
                fields["name"] = natural["title"]
                fields["parent_id"] = self.make_id(natural["route"].lower())
                fields["parent_name"] = track.description or None
                fields["stage_no"] = natural["stage"]
            elif series.key in ("camino", "camino_cid"):
                stage = extract_stage(label) or extract_stage(stem)
                variant = variant_name(label, stem)
                if stage is not None:
                    fields["stage_no"] = stage
                if variant:
                    fields["parent_id"] = self.make_id(_slug(variant))
                    fields["parent_name"] = variant

            extras = {"source_file": path.name, "cnig_product": series.product_id}
            if camino:
                extras.update(
                    route_code=camino["code"],
                    stage_code=f"{camino['stage']:02d}{camino['variant']}",
                    section=camino["section"],
                )
                if camino["variant"]:
                    extras["variant"] = camino["variant"]
            if natural:
                extras.update(
                    route_code=natural["route"],
                    stage_code=f"{natural['stage']:02d}{natural['variant']}",
                )
                if natural["variant"]:
                    extras["variant"] = str(natural["variant"])
            if meta and meta.date:
                extras["published_on"] = meta.date
            if member != path.name:
                extras["archive_member"] = member
            if track.name and track.name != label:
                extras["track_name"] = track.name
            if track.description:
                extras["description"] = track.description

            yield self.feature(
                local_id,
                track.geometry,
                manifest=manifest,
                kind=series.kind,
                extras=extras,
                **fields,
            )


# -- parsing helpers (module level so they are testable without an adapter) ----


def parse_total(html_text: str) -> int | None:
    match = _TOTAL_RE.search(html_text)
    return int(match.group(1)) if match else None


def parse_rows(html_text: str) -> list[ListedFile]:
    """Extract one entry per listing row: file id, name, format, publication date."""
    out: list[ListedFile] = []
    for row in _ROW_RE.findall(html_text):
        id_match = _FILE_ID_RE.search(row)
        if not id_match:
            continue
        name_match = _NAME_CELL_RE.search(row)
        values = [c.strip() for c in _VALUE_CELL_RE.findall(row) if c.strip()]
        fmt = next((v for v in values if v.upper() in ("GPX", "KML", "ZIP", "SHP")), "")
        date = next((v for v in values if re.fullmatch(r"\d{2}/\d{2}/\d{4}", v)), "")
        name = _unescape(name_match.group(1)) if name_match else ""
        out.append(ListedFile(id_match.group(1), name, fmt, date))
    return out


def _unescape(text: str) -> str:
    import html as _html

    return _html.unescape(text).strip()


def extract_ref(text: str | None) -> str | None:
    """Pull a waymarking code out of a name: 'GR 11', 'PR-G 100', 'SL-G 12'."""
    if not text:
        return None
    match = _REF_RE.search(text)
    if not match:
        return None
    prefix, region, number = match.groups()
    prefix = prefix.upper()
    return f"{prefix}-{region.upper()} {number}" if region else f"{prefix} {number}"


def parse_camino_name(name: str | None) -> dict | None:
    """Split a Camino file name into group, route code, stage and section.

    Returns ``None`` for anything that does not fit, so an unexpected name falls
    back to the generic heuristics rather than being silently mis-grouped.
    """
    if not name:
        return None
    match = _CAMINO_RE.match(name.strip())
    if not match:
        return None
    return {
        "group": match.group("group").strip(),
        "code": match.group("code"),
        "country": match.group("country"),
        "stage": int(match.group("stage")),
        "variant": match.group("variant") or "",
        "section": match.group("section"),
    }


def parse_cnt_name(name: str | None) -> dict | None:
    """Split a Caminos Naturales file name into route, stage, variant and title.

    ``None`` for anything else, so an unexpected name keeps the generic path.
    """
    if not name:
        return None
    match = _CNT_RE.match(name.strip())
    if not match:
        return None
    title = re.sub(r"[-_]+", " ", match.group("title")).strip()
    return {
        "route": match.group("route"),
        "stage": int(match.group("stage")),
        "variant": int(match.group("variant")),
        "title": title[:1].upper() + title[1:],
    }


def extract_stage(text: str | None) -> int | None:
    if not text:
        return None
    match = _STAGE_RE.search(text)
    return int(match.group(1)) if match else None


def variant_name(label: str, stem: str) -> str | None:
    """The Camino variant a stage belongs to -- 'Camino Frances', 'Via de la Plata'."""
    for text in (label, stem):
        if not text:
            continue
        head = _STAGE_RE.split(text)[0].strip(" -_.:;,")
        if len(head) >= 4 and head.lower() != text.lower():
            return head
    return None


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _is_html(path: Path) -> bool:
    try:
        head = path.read_bytes()[:512].lstrip().lower()
    except OSError:
        return False
    return head.startswith(b"<!doctype html") or head.startswith(b"<html")


def _safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^\w.\-]+", "_", Path(str(name)).name).strip("._")
    return (cleaned or "download.gpx")[:150]
