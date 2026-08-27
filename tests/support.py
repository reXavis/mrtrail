"""Test doubles. Nothing in the suite touches a network."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[1]


class FakeResponse:
    def __init__(
        self,
        status_code: int = 200,
        content: bytes = b"",
        headers: dict[str, str] | None = None,
        json_data: Any = None,
    ) -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self._json = json_data
        if json_data is not None and not content:
            content = json.dumps(json_data).encode("utf-8")
            self.headers.setdefault("Content-Type", "application/json")
        self.content = content

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", "replace")

    def json(self) -> Any:
        if self._json is not None:
            return self._json
        return json.loads(self.text)

    def iter_content(self, chunk_size: int = 65536):
        for start in range(0, len(self.content), chunk_size) or [0]:
            yield self.content[start : start + chunk_size]


class FakeTransport:
    """Records every request and answers from a handler or a URL map."""

    def __init__(
        self,
        handler: Callable[[str, str, dict], FakeResponse] | dict[str, FakeResponse] | None = None,
        *,
        default: FakeResponse | None = None,
    ) -> None:
        self.handler = handler
        self.default = default
        self.calls: list[tuple[str, str, dict]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append((method, url, kwargs))
        if callable(self.handler):
            return self.handler(method, url, kwargs)
        if isinstance(self.handler, dict):
            for pattern, response in self.handler.items():
                if pattern in url:
                    return response
        if self.default is not None:
            return self.default
        return FakeResponse(404)

    @property
    def urls(self) -> list[str]:
        return [url for _, url, _ in self.calls]


def gpx_bytes(name: str, points: list[tuple[float, float]], track_type: str | None = None) -> bytes:
    """A minimal but valid GPX track."""
    type_tag = f"<type>{track_type}</type>" if track_type else ""
    trkpts = "".join(f'<trkpt lat="{lat}" lon="{lon}"/>' for lon, lat in points)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1">'
        f"<trk><name>{name}</name>{type_tag}<trkseg>{trkpts}</trkseg></trk>"
        "</gpx>"
    ).encode("utf-8")


def line(start: tuple[float, float], count: int = 5, step: float = 0.01) -> list[tuple[float, float]]:
    lon, lat = start
    return [(lon + i * step, lat + i * step) for i in range(count)]


def no_sleep(_seconds: float) -> None:
    """Drop-in for time.sleep so rate limiting does not slow the suite down."""
    return None
