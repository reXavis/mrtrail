"""ZIP handling, because almost nobody publishes a bare file."""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Iterator

GEO_EXTENSIONS = (".gpx", ".geojson", ".json", ".kml", ".shp", ".gpkg")


class UnsupportedFormat(RuntimeError):
    """A source shipped a format the stdlib core cannot read.

    Shapefiles and file geodatabases need the optional ``geo`` extra (fiona,
    pyproj). Raising rather than silently skipping keeps a source's coverage
    honest.
    """


def is_zip(path: Path) -> bool:
    return zipfile.is_zipfile(path)


def iter_members(path: Path, suffixes: tuple[str, ...]) -> Iterator[tuple[str, bytes]]:
    """Yield (member name, bytes) for archive members with a matching suffix."""
    with zipfile.ZipFile(path) as archive:
        for name in sorted(archive.namelist()):
            if name.endswith("/"):
                continue
            if Path(name).suffix.lower() in suffixes:
                yield name, archive.read(name)


def iter_gpx(path: Path) -> Iterator[tuple[str, bytes]]:
    """Yield GPX payloads from a file that is either a GPX or a ZIP containing some.

    Content decides, not the extension. Download endpoints hand back whatever
    they feel like under whatever name the pipeline chose -- CNIG names every
    download ``.gpx`` because that is what it asked for -- and a ZIP parsed as
    XML fails with an unreadable "invalid token" instead of saying it is a ZIP.
    """
    path = Path(path)
    if is_zip(path):
        found = False
        for name, payload in iter_members(path, (".gpx",)):
            found = True
            yield name, payload
        if found:
            return
        with zipfile.ZipFile(path) as archive:
            others = sorted({Path(n).suffix.lower() for n in archive.namelist() if Path(n).suffix})
        if {".shp", ".gpkg", ".gdb"} & set(others):
            raise UnsupportedFormat(
                f"{path}: archive holds {', '.join(others)} but no GPX -- "
                f"install the 'geo' extra (fiona, pyproj) to read it"
            )
        raise UnsupportedFormat(
            f"{path}: archive holds no GPX (members: {', '.join(others) or 'none'})"
        )

    if path.suffix.lower() == ".gpx" or path.read_bytes()[:200].lstrip()[:1] == b"<":
        yield path.name, path.read_bytes()
        return
    raise UnsupportedFormat(f"{path}: not a GPX file and not a ZIP archive")
