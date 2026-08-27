import tempfile
import unittest
from pathlib import Path

from tests.support import FakeResponse, FakeTransport
from trailsdb.fetch import FetchError, PoliteSession, read_record


class Clock:
    """A monotonic clock the tests advance by hand, so pacing is testable."""

    def __init__(self):
        self.now = 0.0
        self.slept: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds

    def __call__(self) -> float:
        return self.now


def make_session(transport, *, clock=None, **kwargs):
    clock = clock or Clock()
    session = PoliteSession(
        transport=transport,
        sleeper=clock.sleep,
        clock=clock,
        today=lambda: "2026-08-27",
        **kwargs,
    )
    return session, clock


class TestRateLimiting(unittest.TestCase):
    def test_requests_are_spaced_by_the_rate_limit(self):
        transport = FakeTransport(default=FakeResponse(200, b"x"))
        session, clock = make_session(transport, rate_limit_s=1.5)
        for _ in range(3):
            session.get("https://example.invalid/a")
        # First request goes immediately; each later one waits its turn.
        self.assertEqual(clock.slept, [1.5, 1.5])

    def test_a_slow_response_consumes_the_wait(self):
        transport = FakeTransport(default=FakeResponse(200, b"x"))
        session, clock = make_session(transport, rate_limit_s=1.5)
        session.get("https://example.invalid/a")
        clock.now += 5.0  # the request itself took longer than the interval
        session.get("https://example.invalid/b")
        self.assertEqual(clock.slept, [])


class TestRetries(unittest.TestCase):
    def test_retries_transient_statuses_then_succeeds(self):
        responses = [FakeResponse(503), FakeResponse(503), FakeResponse(200, b"ok")]
        transport = FakeTransport(lambda *_: responses.pop(0))
        session, clock = make_session(transport, retries=3, backoff_s=2.0, rate_limit_s=0)
        self.assertEqual(session.get("https://example.invalid/a").status_code, 200)
        self.assertEqual(clock.slept, [2.0, 4.0])  # exponential

    def test_gives_up_loudly(self):
        transport = FakeTransport(default=FakeResponse(500))
        session, _ = make_session(transport, retries=2, rate_limit_s=0)
        with self.assertRaises(FetchError) as ctx:
            session.get("https://example.invalid/a")
        self.assertIn("after 3 attempts", str(ctx.exception))

    def test_honours_retry_after(self):
        responses = [FakeResponse(429, headers={"Retry-After": "7"}), FakeResponse(200, b"ok")]
        transport = FakeTransport(lambda *_: responses.pop(0))
        session, clock = make_session(transport, retries=2, rate_limit_s=0)
        session.get("https://example.invalid/a")
        self.assertEqual(clock.slept, [7.0])

    def test_does_not_retry_a_404(self):
        transport = FakeTransport(default=FakeResponse(404))
        session, _ = make_session(transport, retries=3, rate_limit_s=0)
        self.assertEqual(session.get("https://example.invalid/a").status_code, 404)
        self.assertEqual(len(transport.calls), 1)

    def test_retries_network_errors(self):
        state = {"n": 0}

        def handler(*_):
            state["n"] += 1
            if state["n"] < 3:
                raise ConnectionError("reset by peer")
            return FakeResponse(200, b"ok")

        session, _ = make_session(FakeTransport(handler), retries=3, rate_limit_s=0)
        self.assertEqual(session.get("https://example.invalid/a").status_code, 200)


class TestDownload(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_writes_file_and_sidecar(self):
        transport = FakeTransport(default=FakeResponse(200, b"payload", {"ETag": '"abc"'}))
        session, _ = make_session(transport, rate_limit_s=0)
        record = session.download("https://example.invalid/f.gpx", self.dir / "f.gpx")

        self.assertEqual((self.dir / "f.gpx").read_bytes(), b"payload")
        self.assertEqual(record.size, 7)
        self.assertEqual(record.etag, '"abc"')
        self.assertEqual(record.retrieved_on, "2026-08-27")
        self.assertFalse(record.cached)
        self.assertEqual(read_record(self.dir / "f.gpx").sha256, record.sha256)

    def test_revalidation_sends_the_etag_and_a_304_costs_no_bytes(self):
        transport = FakeTransport(default=FakeResponse(200, b"payload", {"ETag": '"abc"'}))
        session, _ = make_session(transport, rate_limit_s=0)
        session.download("https://example.invalid/f.gpx", self.dir / "f.gpx")

        transport.handler = None
        transport.default = FakeResponse(304)
        record = session.download("https://example.invalid/f.gpx", self.dir / "f.gpx")

        self.assertTrue(record.cached)
        self.assertEqual(record.size, 7)  # carried over from the previous pull
        self.assertEqual(transport.calls[-1][2]["headers"]["If-None-Match"], '"abc"')
        self.assertEqual((self.dir / "f.gpx").read_bytes(), b"payload")

    def test_resume_without_revalidation_skips_the_request_entirely(self):
        transport = FakeTransport(default=FakeResponse(200, b"payload"))
        session, _ = make_session(transport, rate_limit_s=0)
        session.download("https://example.invalid/f.gpx", self.dir / "f.gpx")
        before = len(transport.calls)

        record = session.download(
            "https://example.invalid/f.gpx", self.dir / "f.gpx", revalidate=False
        )
        self.assertEqual(len(transport.calls), before)
        self.assertEqual(record.size, 7)

    def test_force_re_downloads(self):
        transport = FakeTransport(default=FakeResponse(200, b"payload"))
        session, _ = make_session(transport, rate_limit_s=0)
        session.download("https://example.invalid/f.gpx", self.dir / "f.gpx")
        session.download("https://example.invalid/f.gpx", self.dir / "f.gpx", force=True)
        self.assertEqual(len(transport.calls), 2)
        self.assertNotIn("If-None-Match", transport.calls[-1][2]["headers"])

    def test_a_failed_download_leaves_no_partial_file(self):
        transport = FakeTransport(default=FakeResponse(404))
        session, _ = make_session(transport, rate_limit_s=0)
        with self.assertRaises(FetchError):
            session.download("https://example.invalid/f.gpx", self.dir / "f.gpx")
        self.assertFalse((self.dir / "f.gpx").exists())
        self.assertFalse((self.dir / "f.gpx.part").exists())

    def test_user_agent_identifies_the_pipeline(self):
        transport = FakeTransport(default=FakeResponse(200, b"x"))
        session, _ = make_session(transport, rate_limit_s=0)
        session.get("https://example.invalid/a")
        self.assertIn("trailsdb", transport.calls[0][2]["headers"]["User-Agent"])


if __name__ == "__main__":
    unittest.main()
