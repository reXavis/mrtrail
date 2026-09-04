"""Streaming GeoJSONL: one compact GeoJSON Feature per line.

The master database is a few hundred thousand features and up to ~1.6 GB. Every
read and write here is streaming -- nothing in the pipeline is allowed to hold a
whole source in memory, because the Alps and USFS cases are the ones that would
break first.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Iterable, Iterator

from .schema import Feature, dumps


def is_gzipped(path: Path) -> bool:
    return Path(path).suffix == ".gz"


def _open(path: Path, mode: str, *, gzipped: bool):
    """Open ``path``, compressing per ``gzipped``.

    Compression is decided by the *destination* name, never by the path being
    opened: writes go through a ``.tmp`` sibling whose own suffix would otherwise
    silently turn gzip off and leave a plain-text file named ``.gz``.
    """
    if gzipped:
        return gzip.open(path, mode + "t", encoding="utf-8")
    return open(path, mode, encoding="utf-8")


def write(path: Path, features: Iterable[Feature], *, validate: bool = True) -> int:
    """Write features to ``path``, returning the count.

    Writes to a sibling ``.tmp`` first and renames, so an interrupted normalize
    never leaves a half-written source that a later stage would treat as
    complete.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    count = 0
    try:
        with _open(tmp, "w", gzipped=is_gzipped(path)) as fh:
            for feature in features:
                if validate:
                    feature.validate()
                fh.write(dumps(feature))
                fh.write("\n")
                count += 1
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    tmp.replace(path)
    return count


class Writer:
    """Incremental writer for a file fed from several sources.

    ``write()`` covers the one-source-one-file case. The pack export needs the
    other shape: many sources streaming into one layer file, none of them held in
    memory. Same tmp-then-rename discipline, so an interrupted export never
    leaves a layer that looks complete.
    """

    def __init__(self, path: Path, *, validate: bool = False) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._tmp = self.path.with_name(self.path.name + ".tmp")
        self._fh = _open(self._tmp, "w", gzipped=is_gzipped(self.path))
        self._validate = validate
        self.count = 0

    def add(self, feature: Feature) -> None:
        if self._validate:
            feature.validate()
        self._fh.write(dumps(feature))
        self._fh.write("\n")
        self.count += 1

    def close(self) -> int:
        self._fh.close()
        self._tmp.replace(self.path)
        return self.count

    def abort(self) -> None:
        self._fh.close()
        self._tmp.unlink(missing_ok=True)

    def __enter__(self) -> "Writer":
        return self

    def __exit__(self, exc_type, *_) -> None:
        if exc_type is None:
            self.close()
        else:
            self.abort()


def read(path: Path) -> Iterator[Feature]:
    path = Path(path)
    with _open(path, "r", gzipped=is_gzipped(path)) as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield Feature.from_geojson(json.loads(line))
            except (json.JSONDecodeError, KeyError) as exc:
                raise ValueError(f"{path}:{lineno}: malformed GeoJSONL ({exc})") from exc


def read_raw(path: Path) -> Iterator[dict]:
    """Read without constructing Feature objects -- for counting and bbox cuts."""
    path = Path(path)
    with _open(path, "r", gzipped=is_gzipped(path)) as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def count(path: Path) -> int:
    path = Path(path)
    if not path.exists():
        return 0
    with _open(path, "r", gzipped=is_gzipped(path)) as fh:
        return sum(1 for line in fh if line.strip())
