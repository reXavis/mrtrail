"""The SQLite catalog: metadata for every feature, without the geometry.

Geometry lives in the per-source GeoJSONL files; the catalog holds names, refs,
provenance, lengths and bounding boxes. That split is what makes the catalog the
80-150 MB artifact in the size model rather than another copy of the 1.2 GB
master database, and what makes two things cheap:

* the per-pack bbox cut -- ask the catalog which feature ids fall in a pack's
  box, then stream only those out of the GeoJSONL;
* offline route search later -- an FTS index over names and refs is a few tens
  of megabytes and ships with a pack if we ever want it to.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

from . import geo
from .geo import BBox
from .manifest import PullManifest
from .registry import Source
from .schema import Feature

CATALOG_SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sources (
    id             TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    publisher      TEXT,
    adapter        TEXT NOT NULL,
    feature_class  TEXT NOT NULL,
    license        TEXT NOT NULL,
    attribution    TEXT NOT NULL,
    countries      TEXT NOT NULL,
    cadence        TEXT NOT NULL,
    estimated_km   INTEGER,
    km_confidence  TEXT,
    verified_on    TEXT,
    homepage       TEXT
);

CREATE TABLE IF NOT EXISTS pulls (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id      TEXT NOT NULL REFERENCES sources(id),
    started_at     TEXT NOT NULL,
    finished_at    TEXT,
    retrieved_on   TEXT,
    file_count     INTEGER NOT NULL DEFAULT 0,
    changed_files  INTEGER NOT NULL DEFAULT 0,
    total_bytes    INTEGER NOT NULL DEFAULT 0,
    ok             INTEGER NOT NULL DEFAULT 1,
    errors         TEXT,
    warnings       TEXT
);
CREATE INDEX IF NOT EXISTS pulls_source_idx ON pulls(source_id, started_at);

CREATE TABLE IF NOT EXISTS features (
    id              TEXT PRIMARY KEY,
    source_id       TEXT NOT NULL REFERENCES sources(id),
    feature_class   TEXT NOT NULL,
    kind            TEXT NOT NULL,
    ref             TEXT,
    name            TEXT,
    parent_id       TEXT,
    parent_name     TEXT,
    stage_no        INTEGER,
    official_status TEXT,
    source_url      TEXT,
    country         TEXT,
    admin           TEXT,
    length_km       REAL NOT NULL,
    point_count     INTEGER NOT NULL,
    west            REAL NOT NULL,
    south           REAL NOT NULL,
    east            REAL NOT NULL,
    north           REAL NOT NULL,
    extras          TEXT
);
CREATE INDEX IF NOT EXISTS features_source_idx ON features(source_id);
CREATE INDEX IF NOT EXISTS features_parent_idx ON features(parent_id);
CREATE INDEX IF NOT EXISTS features_bbox_idx   ON features(west, east, south, north);
CREATE INDEX IF NOT EXISTS features_ref_idx    ON features(ref);
"""

# Optional: only created when the runtime SQLite has FTS5. Nothing depends on it
# yet -- it is the groundwork for offline route search.
_FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS features_fts USING fts5(
    name, ref, content=''
);
"""


@dataclass(slots=True)
class SourceStats:
    source_id: str
    features: int
    length_km: float
    points: int

    @property
    def points_per_km(self) -> float:
        return self.points / self.length_km if self.length_km else 0.0


class Catalog:
    """Thin, explicit wrapper over the catalog database."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.has_fts = False
        self._initialize()

    def _initialize(self) -> None:
        self.db.executescript(_SCHEMA)
        try:
            self.db.executescript(_FTS_SCHEMA)
            self.has_fts = True
        except sqlite3.OperationalError:
            self.has_fts = False  # SQLite built without FTS5; search is optional
        self.db.execute(
            "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(CATALOG_SCHEMA_VERSION),),
        )
        self.db.commit()

    def close(self) -> None:
        self.db.commit()
        self.db.close()

    def __enter__(self) -> "Catalog":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- writes --------------------------------------------------------------

    def upsert_source(self, source: Source) -> None:
        self.db.execute(
            """
            INSERT INTO sources(id, name, publisher, adapter, feature_class, license,
                                attribution, countries, cadence, estimated_km,
                                km_confidence, verified_on, homepage)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name, publisher=excluded.publisher, adapter=excluded.adapter,
                feature_class=excluded.feature_class, license=excluded.license,
                attribution=excluded.attribution, countries=excluded.countries,
                cadence=excluded.cadence, estimated_km=excluded.estimated_km,
                km_confidence=excluded.km_confidence, verified_on=excluded.verified_on,
                homepage=excluded.homepage
            """,
            (
                source.id,
                source.name,
                source.publisher,
                source.adapter,
                source.feature_class,
                source.license.id,
                source.attribution,
                ",".join(source.countries),
                source.cadence,
                source.estimated_km,
                source.km_confidence,
                source.legal.verified_on.isoformat() if source.legal.verified_on else None,
                source.homepage,
            ),
        )
        self.db.commit()

    def record_pull(self, manifest: PullManifest) -> int:
        cursor = self.db.execute(
            """
            INSERT INTO pulls(source_id, started_at, finished_at, retrieved_on,
                              file_count, changed_files, total_bytes, ok, errors, warnings)
            VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            (
                manifest.source,
                manifest.started_at,
                manifest.finished_at or None,
                manifest.retrieved_on or None,
                len(manifest.files),
                manifest.changed_files,
                manifest.total_bytes,
                1 if manifest.ok else 0,
                json.dumps(manifest.errors) if manifest.errors else None,
                json.dumps(manifest.warnings) if manifest.warnings else None,
            ),
        )
        self.db.commit()
        return int(cursor.lastrowid)

    def replace_features(self, source_id: str, features: Iterable[Feature]) -> SourceStats:
        """Swap in a source's features wholesale.

        Normalize is idempotent by construction: a re-run of the same source
        replaces its rows rather than accumulating duplicates, which matters
        because adapters get re-run constantly while they are being written.
        """
        self.db.execute("DELETE FROM features WHERE source_id = ?", (source_id,))
        if self.has_fts:
            self.db.execute(
                "DELETE FROM features_fts WHERE rowid IN "
                "(SELECT rowid FROM features WHERE source_id = ?)",
                (source_id,),
            )

        count = 0
        total_km = 0.0
        total_points = 0
        rows = []
        for feature in features:
            length = geo.length_km(feature.geometry)
            points = geo.point_count(feature.geometry)
            west, south, east, north = geo.bbox(feature.geometry)
            rows.append(
                (
                    feature.id,
                    source_id,
                    feature.feature_class,
                    feature.kind,
                    feature.ref,
                    feature.name,
                    feature.parent_id,
                    feature.parent_name,
                    feature.stage_no,
                    feature.official_status,
                    feature.source_url,
                    feature.country,
                    feature.admin,
                    length,
                    points,
                    west,
                    south,
                    east,
                    north,
                    json.dumps(feature.extras, ensure_ascii=False) if feature.extras else None,
                )
            )
            count += 1
            total_km += length
            total_points += points
            if len(rows) >= 1000:
                self._insert_features(rows)
                rows.clear()
        if rows:
            self._insert_features(rows)
        self.db.commit()
        return SourceStats(source_id, count, total_km, total_points)

    def _insert_features(self, rows: list[tuple]) -> None:
        self.db.executemany(
            "INSERT INTO features VALUES(" + ",".join("?" * 20) + ")",
            rows,
        )
        if self.has_fts:
            self.db.executemany(
                "INSERT INTO features_fts(rowid, name, ref) "
                "VALUES((SELECT rowid FROM features WHERE id = ?), ?, ?)",
                [(row[0], row[5] or "", row[4] or "") for row in rows],
            )

    # -- reads ---------------------------------------------------------------

    def stats(self) -> list[SourceStats]:
        rows = self.db.execute(
            """
            SELECT source_id,
                   COUNT(*)          AS features,
                   SUM(length_km)    AS length_km,
                   SUM(point_count)  AS points
            FROM features GROUP BY source_id ORDER BY source_id
            """
        ).fetchall()
        return [
            SourceStats(r["source_id"], r["features"], r["length_km"] or 0.0, r["points"] or 0)
            for r in rows
        ]

    def totals(self) -> SourceStats:
        row = self.db.execute(
            "SELECT COUNT(*) AS features, SUM(length_km) AS length_km, "
            "SUM(point_count) AS points FROM features"
        ).fetchone()
        return SourceStats("*", row["features"] or 0, row["length_km"] or 0.0, row["points"] or 0)

    def feature_ids_in_bbox(self, box: BBox, *, source_id: str | None = None) -> set[str]:
        """Feature ids whose bounding box overlaps ``box`` -- the pack cut."""
        west, south, east, north = box
        sql = (
            "SELECT id FROM features WHERE west <= ? AND east >= ? AND south <= ? AND north >= ?"
        )
        params: list[object] = [east, west, north, south]
        if source_id:
            sql += " AND source_id = ?"
            params.append(source_id)
        return {row["id"] for row in self.db.execute(sql, params)}

    def last_pull(self, source_id: str) -> sqlite3.Row | None:
        return self.db.execute(
            "SELECT * FROM pulls WHERE source_id = ? ORDER BY id DESC LIMIT 1", (source_id,)
        ).fetchone()

    def search(self, query: str, *, limit: int = 25) -> list[sqlite3.Row]:
        """Name/ref search. Uses FTS when available, LIKE otherwise."""
        if self.has_fts:
            return self.db.execute(
                "SELECT f.* FROM features_fts JOIN features f ON f.rowid = features_fts.rowid "
                "WHERE features_fts MATCH ? LIMIT ?",
                (query, limit),
            ).fetchall()
        like = f"%{query}%"
        return self.db.execute(
            "SELECT * FROM features WHERE name LIKE ? OR ref LIKE ? LIMIT ?", (like, like, limit)
        ).fetchall()


@contextmanager
def open_catalog(path: Path | str) -> Iterator[Catalog]:
    catalog = Catalog(path)
    try:
        yield catalog
    finally:
        catalog.close()
