"""A deliberately polite HTTP client.

Three properties the pull plan depends on:

*Politeness*
    CNIG is ~9,900 individual files behind a free account. At one request per
    1.5 s that is 4-6 hours, which is the budgeted number. Going faster is how
    you get the account blocked and the source lost.

*Resumability*
    The first full pull is 1-2 days of wall-clock. It will be interrupted. Every
    download writes a sidecar manifest, so a re-run skips what it already has
    instead of starting over.

*Revalidation*
    The quarterly refresh re-downloads only what changed. ETag / Last-Modified
    go back to the server; a 304 costs one request and no bytes. Where a server
    offers neither, the content hash still tells the catalog whether anything
    moved.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

USER_AGENT = "trailsdb/0.1 (+https://github.com/reXavis/mrtrail) official-trail-data-pipeline"

RETRY_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})
CHUNK = 1 << 16


class FetchError(RuntimeError):
    """A download failed after exhausting retries.

    Adapters do not swallow this. A source that has moved must fail the pull
    loudly -- silent partial data is the failure mode that ships wrong maps.
    """


@dataclass(slots=True)
class FileRecord:
    """Sidecar metadata for one downloaded file."""

    url: str
    path: str
    sha256: str
    size: int
    retrieved_on: str
    etag: str | None = None
    last_modified: str | None = None
    status: int = 200

    @property
    def cached(self) -> bool:
        return self.status == 304


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def _meta_path(dest: Path) -> Path:
    return dest.with_name(dest.name + ".meta.json")


def read_record(dest: Path) -> FileRecord | None:
    meta = _meta_path(dest)
    if not (meta.exists() and dest.exists()):
        return None
    try:
        return FileRecord(**json.loads(meta.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, TypeError):
        return None


class PoliteSession:
    """Rate-limited, retrying HTTP with resumable downloads.

    ``transport`` is any object with ``request(method, url, **kwargs)`` returning
    a requests-like response. It is injectable so the whole pipeline is testable
    without touching a network.
    """

    def __init__(
        self,
        *,
        rate_limit_s: float = 1.5,
        timeout: float = 60.0,
        revalidate: bool = True,
        retries: int = 4,
        backoff_s: float = 2.0,
        user_agent: str = USER_AGENT,
        transport: Any | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        today: Callable[[], str] | None = None,
    ) -> None:
        self.rate_limit_s = rate_limit_s
        self.timeout = timeout
        #: Whether downloads re-check the server for a file already on disk.
        #: ``pull --resume`` turns this off so an interrupted multi-gigabyte pull
        #: picks up where it stopped instead of re-fetching what it has.
        self.revalidate = revalidate
        self.retries = retries
        self.backoff_s = backoff_s
        self.user_agent = user_agent
        self._sleep = sleeper
        self._clock = clock
        self._last_request_at = -1e9
        self._transport = transport
        if today is None:
            import datetime as _dt

            today = lambda: _dt.date.today().isoformat()  # noqa: E731
        self._today = today

    # -- plumbing ------------------------------------------------------------

    @property
    def transport(self) -> Any:
        if self._transport is None:
            import requests

            session = requests.Session()
            session.headers["User-Agent"] = self.user_agent
            self._transport = session
        return self._transport

    def _wait_turn(self) -> None:
        elapsed = self._clock() - self._last_request_at
        remaining = self.rate_limit_s - elapsed
        if remaining > 0:
            self._sleep(remaining)
        self._last_request_at = self._clock()

    def request(self, method: str, url: str, **kwargs: Any) -> Any:
        """One request, paced and retried. Raises FetchError when it cannot succeed."""
        kwargs.setdefault("timeout", self.timeout)
        headers = dict(kwargs.pop("headers", None) or {})
        headers.setdefault("User-Agent", self.user_agent)
        last_error: str = "no attempt made"
        for attempt in range(self.retries + 1):
            self._wait_turn()
            retryable_response = None
            try:
                response = self.transport.request(method, url, headers=headers, **kwargs)
            except Exception as exc:  # network-layer failure
                last_error = f"{type(exc).__name__}: {exc}"
            else:
                if response.status_code not in RETRY_STATUSES:
                    return response
                last_error = f"HTTP {response.status_code}"
                retryable_response = response
            if attempt < self.retries:
                self._sleep(self._retry_delay(attempt, retryable_response))
        raise FetchError(f"{method} {url} failed after {self.retries + 1} attempts ({last_error})")

    def _retry_delay(self, attempt: int, response: Any | None) -> float:
        # Honour Retry-After when the server sends one -- a 429 that we ignore is
        # how a polite pull becomes an impolite one.
        if response is not None:
            raw = getattr(response, "headers", {}) or {}
            retry_after = raw.get("Retry-After") if hasattr(raw, "get") else None
            if retry_after:
                try:
                    return max(0.0, float(retry_after))
                except (TypeError, ValueError):
                    pass
        return self.backoff_s * (2**attempt)

    def get(self, url: str, **kwargs: Any) -> Any:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> Any:
        return self.request("POST", url, **kwargs)

    def get_json(self, url: str, **kwargs: Any) -> Any:
        response = self.get(url, **kwargs)
        if response.status_code != 200:
            raise FetchError(f"GET {url} returned HTTP {response.status_code}, expected 200")
        return response.json()

    # -- downloads -----------------------------------------------------------

    def download(
        self,
        url: str,
        dest: Path,
        *,
        method: str = "GET",
        revalidate: bool | None = None,
        force: bool = False,
        **kwargs: Any,
    ) -> FileRecord:
        """Fetch ``url`` into ``dest``, skipping work the last pull already did.

        Returns a record whose ``cached`` flag says whether bytes crossed the
        wire. Writes through a ``.part`` file so an interrupted pull never leaves
        a truncated file that looks complete.
        """
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if revalidate is None:
            revalidate = self.revalidate
        previous = None if force else read_record(dest)

        headers = dict(kwargs.pop("headers", None) or {})
        if previous and revalidate:
            if previous.etag:
                headers["If-None-Match"] = previous.etag
            if previous.last_modified:
                headers["If-Modified-Since"] = previous.last_modified
        elif previous and not revalidate:
            # Nothing to ask the server: trust what is on disk.
            return previous

        response = self.request(method, url, headers=headers, stream=True, **kwargs)

        if response.status_code == 304 and previous:
            record = FileRecord(**{**asdict(previous), "status": 304})
            _meta_path(dest).write_text(json.dumps(asdict(record), indent=2), encoding="utf-8")
            return record

        if response.status_code != 200:
            raise FetchError(f"{method} {url} returned HTTP {response.status_code}, expected 200")

        # The temp name carries the writing process's pid. A fixed ".part" is
        # shared state: two pulls of the same source, or a resumed one racing a
        # survivor of the last run, will each rename it and the loser fails with
        # a bewildering FileNotFoundError on a file it just wrote.
        part = dest.with_name(f"{dest.name}.{os.getpid()}.part")
        digest = hashlib.sha256()
        size = 0
        try:
            with open(part, "wb") as fh:
                for chunk in _iter_content(response):
                    if not chunk:
                        continue
                    fh.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
            part.replace(dest)
        except BaseException:
            part.unlink(missing_ok=True)
            raise

        response_headers = getattr(response, "headers", {}) or {}
        record = FileRecord(
            url=url,
            path=str(dest),
            sha256=digest.hexdigest(),
            size=size,
            retrieved_on=self._today(),
            etag=_header(response_headers, "ETag"),
            last_modified=_header(response_headers, "Last-Modified"),
            status=200,
        )
        _meta_path(dest).write_text(json.dumps(asdict(record), indent=2), encoding="utf-8")
        return record


def _header(headers: Any, name: str) -> str | None:
    if hasattr(headers, "get"):
        value = headers.get(name)
        return str(value) if value is not None else None
    return None


def _iter_content(response: Any):
    if hasattr(response, "iter_content"):
        return response.iter_content(chunk_size=CHUNK)
    content = getattr(response, "content", b"")
    return [content]
