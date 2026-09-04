"""Norway, BC, Sweden -- and the readers they needed: TM inverse, GML, WFS paging."""

import json
import tempfile
import unittest
from pathlib import Path

from tests.support import FakeResponse, FakeTransport
from trailsdb import registry as registry_module
from trailsdb.adapters import AdapterContext, build
from trailsdb.adapters import naturvardsverket as sweden_module
from trailsdb.config import Paths
from trailsdb.fetch import PoliteSession
from trailsdb.formats import gml
from trailsdb.manifest import PullManifest
from trailsdb.proj import sweref99_tm_to_wgs84, utm_to_wgs84

FIXTURES = Path(__file__).parent / "fixtures"


def session_for(transport):
    return PoliteSession(
        transport=transport, rate_limit_s=0, sleeper=lambda _s: None, today=lambda: "2026-09-04"
    )


class AdapterCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.paths = Paths.resolve(Path(self.tmp.name)).ensure()
        self.registry = registry_module.load()

    def adapter(self, source_id, transport):
        return build(
            AdapterContext(
                source=self.registry.get(source_id), paths=self.paths, session=session_for(transport)
            )
        )


class TestTransverseMercator(unittest.TestCase):
    def test_sweref99_tm_reference_point(self):
        lon, lat = sweref99_tm_to_wgs84(674_032, 6_580_822)  # Stockholm central station
        self.assertAlmostEqual(lon, 18.059, places=2)
        self.assertAlmostEqual(lat, 59.330, places=2)

    def test_utm_zone_origin_is_exact(self):
        lon, lat = utm_to_wgs84(33)(500_000, 0)
        self.assertAlmostEqual(lon, 15.0, places=9)
        self.assertAlmostEqual(lat, 0.0, places=9)


class TestGmlReader(unittest.TestCase):
    def test_reads_a_real_kartverket_page(self):
        payload = (FIXTURES / "kartverket_fotrute_page.gml").read_bytes()
        features = gml.parse(payload)
        self.assertEqual(len(features), 2)
        first = features[0]
        self.assertEqual(first.type_name, "Fotrute")
        self.assertIn("lokalId", first.attributes)
        self.assertIn("rutenavn", first.attributes)
        self.assertNotIn("posList", first.attributes)
        lon, lat = first.lines[0][0]
        self.assertTrue(4.0 < lon < 32.0 and 57.0 < lat < 72.0, (lon, lat))  # Norway, lon first
        # Geonorge writes numberMatched="unknown" on full pages and only counts
        # on a resultType=hits request; "unknown" must read as None, not crash.
        self.assertIsNone(gml.number_matched(payload))
        self.assertEqual(gml.number_matched(b'<wfs:FeatureCollection numberMatched="139910" numberReturned="0"/>'), 139910)

    def test_urn_srs_is_latitude_first(self):
        doc = (
            b'<wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs/2.0" '
            b'xmlns:gml="http://www.opengis.net/gml/3.2" xmlns:a="x">'
            b'<wfs:member><a:T gml:id="t1"><a:geom><gml:LineString srsName="urn:ogc:def:crs:EPSG::4326">'
            b"<gml:posList>58.89 11.01 58.88 11.02</gml:posList></gml:LineString></a:geom></a:T></wfs:member>"
            b"</wfs:FeatureCollection>"
        )
        self.assertEqual(gml.parse(doc)[0].lines[0][0], [11.01, 58.89])

    def test_projected_srs_is_reprojected(self):
        doc = (
            b'<wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs/2.0" '
            b'xmlns:gml="http://www.opengis.net/gml/3.2" xmlns:a="x">'
            b'<wfs:member><a:T gml:id="t1"><a:geom><gml:LineString srsName="EPSG:25833">'
            b"<gml:posList>500000 0 500100 0</gml:posList></gml:LineString></a:geom></a:T></wfs:member>"
            b"</wfs:FeatureCollection>"
        )
        lon, lat = gml.parse(doc)[0].lines[0][0]
        self.assertAlmostEqual(lon, 15.0, places=6)
        self.assertAlmostEqual(lat, 0.0, places=6)

    def test_a_wfs_exception_is_an_error_not_an_empty_page(self):
        doc = (
            b'<ows:ExceptionReport xmlns:ows="http://www.opengis.net/ows/1.1">'
            b"<ows:Exception><ows:ExceptionText>Cannot do natural order</ows:ExceptionText>"
            b"</ows:Exception></ows:ExceptionReport>"
        )
        with self.assertRaises(gml.GmlError):
            gml.parse(doc)


class TestKartverket(AdapterCase):
    def test_pulls_gml_pages_and_groups_pieces_by_route_number(self):
        page = (FIXTURES / "kartverket_fotrute_page.gml").read_bytes()
        hits = b'<wfs:FeatureCollection xmlns:wfs="x" numberMatched="2" numberReturned="0"/>'

        def handler(_m, url, _k):
            if "resultType=hits" in url:
                # Only the foot-route layer has data in this fixture; a count
                # for the others would (rightly) trip the truncation guard.
                if "Fotrute" in url:
                    return FakeResponse(200, hits, {"Content-Type": "text/xml"})
                return FakeResponse(200, b'<wfs:FeatureCollection xmlns:wfs="x" numberMatched="0" numberReturned="0"/>', {"Content-Type": "text/xml"})
            if "Fotrute" in url:
                return FakeResponse(200, page, {"Content-Type": "text/xml"})
            # The other three route types: an empty collection.
            return FakeResponse(200, b'<wfs:FeatureCollection xmlns:wfs="x" numberMatched="0" numberReturned="0"/>', {"Content-Type": "text/xml"})

        adapter = self.adapter("kartverket_turrutebasen", FakeTransport(handler))
        manifest = adapter.pull()
        self.assertTrue(manifest.ok, manifest.errors)
        features = list(adapter.normalize(manifest))
        self.assertEqual(len(features), 2)
        for f in features:
            f.validate()
            self.assertEqual(f.country, "NO")
            self.assertEqual(f.official_status, "norway_fotrute")
            self.assertRegex(f.id, r"^kartverket_turrutebasen:[0-9a-f-]{36}$")
        with_number = [f for f in features if f.ref]
        for f in with_number:
            self.assertTrue(f.parent_id.startswith("kartverket_turrutebasen:rute-"))

    def test_requests_are_gml_with_wgs84_and_paged(self):
        transport = FakeTransport(default=FakeResponse(200, b'<wfs:FeatureCollection xmlns:wfs="x" numberMatched="0" numberReturned="0"/>', {"Content-Type": "text/xml"}))
        self.adapter("kartverket_turrutebasen", transport).pull()
        urls = transport.urls
        self.assertTrue(any("srsName=EPSG%3A4326" in u for u in urls))
        self.assertTrue(any("startIndex=0" in u for u in urls))
        self.assertFalse(any("outputFormat" in u for u in urls))  # GML only


class TestBcRecreation(AdapterCase):
    def test_keeps_active_trail_reserves_only_and_sorts_for_paging(self):
        page = (FIXTURES / "bc_recreation_page.json").read_bytes()

        def handler(_m, url, _k):
            if "resultType=hits" in url:
                return FakeResponse(200, b'<x numberMatched="3"/>', {"Content-Type": "text/xml"})
            return FakeResponse(200, page, {"Content-Type": "application/json"})

        transport = FakeTransport(handler)
        adapter = self.adapter("bc_recreation", transport)
        features = list(adapter.normalize(adapter.pull()))
        # Three rows in: one retired, one non-trail code, one keeper.
        self.assertEqual(len(features), 1)
        f = features[0]
        f.validate()
        self.assertEqual(f.country, "CA")
        self.assertEqual(f.admin, "British Columbia")
        self.assertIn("Open Government Licence - British Columbia", f.attribution)
        self.assertTrue(f.ref.startswith("REC"))
        self.assertTrue(any("sortBy=RMF_SKEY" in u for u in transport.urls))
        self.assertTrue(any("outputFormat=application%2Fjson" in u for u in transport.urls))


class TestNaturvardsverket(AdapterCase):
    def normalized(self):
        source = self.registry.get("naturvardsverket")
        raw = self.paths.raw_dir(source.id)
        raw.mkdir(parents=True, exist_ok=True)
        (raw / "Leder.geojson").write_bytes((FIXTURES / "naturvardsverket_leder.geojson").read_bytes())
        PullManifest.start(source.id, "naturvardsverket").finish().write(raw)
        adapter = self.adapter("naturvardsverket", FakeTransport())
        return list(adapter.normalize(adapter.load_manifest()))

    def test_reprojects_sweref99_into_sweden(self):
        features = self.normalized()
        self.assertEqual(len(features), 3)
        for f in features:
            f.validate()
            lon, lat = f.geometry["coordinates"][0]
            self.assertTrue(10.0 < lon < 25.0 and 55.0 < lat < 70.0, (lon, lat))
            self.assertEqual(f.country, "SE")
            self.assertEqual(f.license, "cc0-1.0")

    def test_pieces_get_their_own_ids_and_group_under_the_trail(self):
        # 12,013 rows share 3,657 LED_IDs; one trail is 163 pieces. The trail is
        # the parent, and a piece id that was just LED_ID collided in the catalog.
        features = self.normalized()
        self.assertEqual(len({f.id for f in features}), 3)
        for f in features:
            self.assertRegex(f.id, r"^naturvardsverket:\d+-\d+$")
            self.assertTrue(f.parent_id.startswith("naturvardsverket:led-"))

    def test_state_trails_are_flagged(self):
        state = [f for f in self.normalized() if f.official_status == "sweden_statlig_led"]
        self.assertEqual(len(state), 1)
        self.assertIsNotNone(state[0].ref)

    def test_kind_from_swedish_vocabulary(self):
        k = sweden_module._kind_for
        self.assertEqual(k({"LEDTYP": "Vandringsled", "LEDKATEGORI": "Barmarksled"}), "hiking")
        self.assertEqual(k({"LEDTYP": "Vandringsled", "LEDKATEGORI": "Led på snö"}), "ski")
        self.assertEqual(k({"LEDTYP": "Kanotled", "LEDKATEGORI": "Led på/i vatten"}), "paddle")
        self.assertEqual(k({"LEDTYP": "Cykelled", "LEDKATEGORI": "Barmarksled"}), "cycling")


if __name__ == "__main__":
    unittest.main()
