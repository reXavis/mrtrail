"""Australian state adapters, against pages and files cut from the real services."""

import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from tests.support import FakeResponse, FakeTransport
from trailsdb import registry as registry_module
from trailsdb.adapters import AdapterContext, build
from trailsdb.adapters import au_vic as au_vic_module
from trailsdb.config import Paths
from trailsdb.fetch import PoliteSession

FIXTURES = Path(__file__).parent / "fixtures"


def session_for(transport):
    return PoliteSession(transport=transport, rate_limit_s=0, sleeper=lambda _s: None, today=lambda: "2026-09-04")


class AdapterCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.paths = Paths.resolve(Path(self.tmp.name)).ensure()
        self.registry = registry_module.load()

    def adapter(self, source_id, transport):
        return build(AdapterContext(source=self.registry.get(source_id), paths=self.paths, session=session_for(transport)))


def sa_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("CC_BY.txt", "Information contained in this file is licensed under a Creative Commons By Attribution 4.0 Australia Licence\n")
        archive.writestr("TOPO_RecreationTrails_GDA2020.geojson", (FIXTURES / "au_sa_trails_4.geojson").read_bytes())
        archive.writestr("TOPO_RecreationTrails_GDA94.geojson", (FIXTURES / "au_sa_trails_4.geojson").read_bytes())
    return buffer.getvalue()


class TestSouthAustralia(AdapterCase):
    def test_downloads_the_zip_and_reads_one_member(self):
        transport = FakeTransport(default=FakeResponse(200, sa_zip(), {"Content-Type": "application/x-zip-compressed"}))
        adapter = self.adapter("au_sa_trails", transport)
        manifest = adapter.pull()
        self.assertTrue(manifest.ok, manifest.errors)
        self.assertEqual(len(manifest.files), 1)
        self.assertTrue(transport.urls[0].endswith("TOPO_RecreationTrails_geojson.zip"))

        features = list(adapter.normalize(manifest))
        # Four rows in: a Heysen Trail piece, an unnamed temporarily-closed
        # track, a shared-use path, and a retired trail -- two survive.
        self.assertEqual([f.name for f in features], ["Heysen Trail", "Gawler Rivers Path (Tapa Pariara)"])
        for f in features:
            f.validate()
            self.assertEqual(f.country, "AU")
            self.assertEqual(f.admin, "South Australia")
            self.assertEqual(f.license, "cc-by-4.0")
            self.assertIn("Department for Environment and Water", f.attribution)
        heysen, gawler = features
        self.assertEqual(heysen.id, "au_sa_trails:4853850")
        self.assertEqual(heysen.parent_id, "au_sa_trails:heysen-trail")
        self.assertEqual(heysen.parent_name, "Heysen Trail")
        self.assertEqual(heysen.kind, "hiking")
        self.assertEqual(gawler.kind, "mixed")
        self.assertNotIn("TRAILNAME", gawler.extras)
        self.assertEqual(gawler.extras["TRAILTYPE"], "SU:WALK,BIKE")

    def test_a_response_that_is_not_a_zip_fails_the_pull(self):
        transport = FakeTransport(default=FakeResponse(200, b"<html>maintenance</html>", {"Content-Type": "text/html"}))
        manifest = self.adapter("au_sa_trails", transport).pull()
        self.assertFalse(manifest.ok)
        self.assertIn("not a zip", manifest.errors[0])

    def test_pieces_sharing_a_persistent_id_are_not_dropped(self):
        rows = json.loads((FIXTURES / "au_sa_trails_4.geojson").read_text())
        twin = json.loads(json.dumps(rows["features"][0])); twin["properties"]["FID"] = 999
        rows["features"] = [rows["features"][0], twin]
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("TOPO_RecreationTrails_GDA2020.geojson", json.dumps(rows))
        transport = FakeTransport(default=FakeResponse(200, buffer.getvalue()))
        adapter = self.adapter("au_sa_trails", transport)
        ids = [f.id for f in adapter.normalize(adapter.pull())]
        self.assertEqual(ids, ["au_sa_trails:4853850", "au_sa_trails:4853850-999"])


class TestVictoria(AdapterCase):
    def transport(self):
        page = (FIXTURES / "au_vic_page.geojson").read_bytes()

        def handler(_m, url, _k):
            if "resultType=hits" in url:
                return FakeResponse(200, b'<wfs:FeatureCollection numberMatched="2" numberReturned="0"/>', {"Content-Type": "text/xml"})
            return FakeResponse(200, page, {"Content-Type": "application/json"})

        return FakeTransport(handler)

    def test_walking_tracks_are_kept_and_vehicle_tours_left_out(self):
        transport = self.transport()
        adapter = self.adapter("au_vic_tracks", transport)
        features = list(adapter.normalize(adapter.pull()))
        self.assertEqual([f.name for f in features], ["Red White And Blue Walk"])
        f = features[0]
        f.validate()
        self.assertEqual(f.id, "au_vic_tracks:121106")
        self.assertEqual(f.kind, "hiking")  # walkers first, whatever else is allowed
        self.assertEqual(f.admin, "Victoria")
        self.assertEqual(f.official_status, "vic_recreation_track")
        self.assertIn("State of Victoria", f.attribution)
        self.assertIn("w_grade", f.extras)
        self.assertNotIn("f_grade", f.extras)  # an activity not on offer here
        self.assertTrue(any("sortBy=serial_no" in u for u in transport.urls))
        self.assertTrue(any("typeNames=open-data-platform%3Arecweb_tracks" in u for u in transport.urls))

    def test_kind_prefers_walking_then_mtb_then_horse(self):
        self.assertEqual(au_vic_module.kind_for({"w_activity": "Y", "m_activity": "Y"}), "hiking")
        self.assertEqual(au_vic_module.kind_for({"m_activity": "Y", "h_activity": "Y"}), "mtb")
        self.assertEqual(au_vic_module.kind_for({"h_activity": "Y"}), "horse")
        self.assertIsNone(au_vic_module.kind_for({"f_activity": "Y"}))
        self.assertIsNone(au_vic_module.kind_for({"d_activity": "Y", "t_activity": "Y"}))
        self.assertEqual(au_vic_module.kind_for({}), "hiking")


class TestTasmania(AdapterCase):
    def test_filters_server_side_and_classifies_each_segment(self):
        page = json.loads((FIXTURES / "au_tas_page.geojson").read_text())
        page["exceededTransferLimit"] = False
        transport = FakeTransport(default=FakeResponse(200, json.dumps(page).encode(), {"Content-Type": "application/geo+json"}))
        adapter = self.adapter("au_tas_tracks", transport)
        features = list(adapter.normalize(adapter.pull()))

        self.assertEqual(len(features), 3)
        self.assertIn("where=TRAN_CLASS%20NOT%20IN%20%28%27Ferry%27%2C%27Not%20Applicable%27%29", transport.urls[0])
        shared, walking, _ = features
        for f in features:
            f.validate()
            self.assertEqual(f.name, "Lambert Rivulet Track")
            self.assertEqual(f.admin, "Tasmania")
            self.assertEqual(f.feature_class, "segment")
            self.assertEqual(f.extras["managed_by"], "Council")
        self.assertEqual(shared.id, "au_tas_tracks:6368886")
        self.assertEqual(shared.kind, "mixed")
        self.assertEqual(walking.kind, "hiking")
        self.assertEqual(walking.official_status, "tas_list_track")

    def test_closed_segments_are_dropped_and_pws_classes_are_walking_tracks(self):
        page = json.loads((FIXTURES / "au_tas_page.geojson").read_text())
        page["exceededTransferLimit"] = False
        page["features"][0]["properties"]["STATUS"] = "Closed"
        page["features"][1]["properties"]["TRAN_CLASS"] = "AS2156 Track Class 3 (PWS)"
        page["features"][2]["properties"]["STATUS"] = "Unmaintained"
        transport = FakeTransport(default=FakeResponse(200, json.dumps(page).encode()))
        adapter = self.adapter("au_tas_tracks", transport)
        features = list(adapter.normalize(adapter.pull()))
        self.assertEqual([f.official_status for f in features], ["tas_pws_walking_track", "tas_unmaintained_track"])
        self.assertEqual(features[0].kind, "hiking")


if __name__ == "__main__":
    unittest.main()
