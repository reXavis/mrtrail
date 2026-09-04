"""A GeoPackage reader on the stdlib.

A GeoPackage is a SQLite file with a few registry tables and geometry stored as
a small header in front of standard WKB. SQLite is in the stdlib and WKB is a
handful of ``struct`` calls, so a publisher that ships GeoPackage -- swisstopo,
Kartverket, BC -- can be read here without GDAL, exactly as ArcGIS services can
be read without it.

Only line geometries are decoded, because only line geometries are wanted.
"""

from __future__ import annotations

import sqlite3
import struct
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator

_WKB_LINESTRING = 2
_WKB_MULTILINESTRING = 5
_EWKB_Z = 0x80000000
_EWKB_M = 0x40000000
_EWKB_SRID = 0x20000000


class GpkgError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class FeatureTable:
    name: str
    geometry_column: str
    srs_id: int
    columns: tuple[str, ...]


def open_gpkg(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def extract_from_zip(archive: Path, into: Path) -> Path:
    """Unpack the single .gpkg inside a zip, skipping the work if it is already there."""
    into.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zf:
        members = [m for m in zf.namelist() if m.lower().endswith(".gpkg")]
        if len(members) != 1:
            raise GpkgError(f"{archive}: expected one .gpkg member, found {members}")
        target = into / Path(members[0]).name
        wanted = zf.getinfo(members[0]).file_size
        if target.exists() and target.stat().st_size == wanted:
            return target
        with zf.open(members[0]) as src, open(target, "wb") as dst:
            for chunk in iter(lambda: src.read(1 << 20), b""):
                dst.write(chunk)
    return target


def feature_tables(connection: sqlite3.Connection) -> list[FeatureTable]:
    tables = []
    for row in connection.execute(
        "SELECT c.table_name, g.column_name, g.srs_id FROM gpkg_contents c "
        "JOIN gpkg_geometry_columns g ON g.table_name = c.table_name "
        "WHERE c.data_type = 'features'"
    ):
        columns = tuple(r[1] for r in connection.execute(f'PRAGMA table_info("{row[0]}")'))
        tables.append(FeatureTable(row[0], row[1], int(row[2]), columns))
    return tables


def iter_rows(
    connection: sqlite3.Connection, table: FeatureTable, *, where: str = ""
) -> Iterator[tuple[dict[str, Any], bytes | None]]:
    """Yield (attributes, raw geometry blob) for every row, streaming."""
    sql = f'SELECT * FROM "{table.name}"'
    if where:
        sql += f" WHERE {where}"
    for row in connection.execute(sql):
        attrs = {k: row[k] for k in row.keys() if k != table.geometry_column}
        yield attrs, row[table.geometry_column]


def decode_lines(
    blob: bytes | None, transform: Callable[[float, float], tuple[float, float]] | None = None
) -> list[list[list[float]]]:
    """Decode a GeoPackageBinary blob into lon/lat lines.

    Returns an empty list for empty or non-line geometry. ``transform`` maps
    the stored (x, y) into (lon, lat); None means the data is WGS84 already.
    """
    if not blob or len(blob) < 8 or blob[:2] != b"GP":
        return []
    flags = blob[3]
    if flags & 0x10:  # empty-geometry flag
        return []
    envelope = (flags >> 1) & 0x07
    envelope_len = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}.get(envelope)
    if envelope_len is None:
        raise GpkgError(f"unsupported envelope indicator {envelope}")
    return _wkb_lines(memoryview(blob)[8 + envelope_len :], transform)


def _wkb_lines(buf: memoryview, transform) -> list[list[list[float]]]:
    pos = 0

    def read(fmt: str, order: str):
        nonlocal pos
        value = struct.unpack_from(order + fmt, buf, pos)
        pos += struct.calcsize(fmt)
        return value

    def read_geometry() -> list[list[list[float]]]:
        nonlocal pos
        order = "<" if buf[pos] == 1 else ">"
        pos += 1
        (raw_type,) = read("I", order)
        has_z = bool(raw_type & _EWKB_Z)
        has_m = bool(raw_type & _EWKB_M)
        if raw_type & _EWKB_SRID:
            read("i", order)
        base = raw_type & 0x0FFFFFFF
        if base >= 1000:  # ISO SQL/MM dimension coding: 1000=Z, 2000=M, 3000=ZM
            dims, base = divmod(base, 1000)
            has_z = has_z or dims in (1, 3)
            has_m = has_m or dims in (2, 3)
        stride = 2 + int(has_z) + int(has_m)

        if base == _WKB_LINESTRING:
            (count,) = read("I", order)
            line = []
            for _ in range(count):
                coords = read("d" * stride, order)
                x, y = coords[0], coords[1]
                if transform is not None:
                    x, y = transform(x, y)
                line.append([x, y])
            return [line] if len(line) >= 2 else []
        if base == _WKB_MULTILINESTRING:
            (count,) = read("I", order)
            lines = []
            for _ in range(count):
                lines.extend(read_geometry())
            return lines
        return []

    return read_geometry()


def lines_to_geometry(lines: list[list[list[float]]]) -> dict[str, Any] | None:
    if not lines:
        return None
    if len(lines) == 1:
        return {"type": "LineString", "coordinates": lines[0]}
    return {"type": "MultiLineString", "coordinates": lines}
