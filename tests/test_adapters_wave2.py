"""NPS, Ontario, UK National Trails, swisstopo -- against fixtures cut from the real services."""

import tempfile
import unittest
from pathlib import Path

from tests.support import FakeResponse, FakeTransport
from trailsdb import registry as registry_module
from trailsdb.adapters import AdapterContext, build
from trailsdb.adapters import nps as nps_module
from trailsdb.adapters import ontario_otn as ontario_module
from trailsdb.config import Paths
from trailsdb.fetch import PoliteSession
from trailsdb.formats import gpkg
from trailsdb.proj import lv95_to_wgs84, to_wgs84

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

    @staticmethod
    def page_transport(routes: dict[str, str]):
        """Serve a fixture per URL fragment; anything else is a 404."""

        def handler(_method, url, _kwargs):
            for fragment, name in routes.items():
                if fragment in url:
                    return FakeResponse(
                        200, (FIXTURES / name).read_bytes(), {"Content-Type": "application/json"}
                    )
            return FakeResponse(404)

        return FakeTransport(handler)


# -------------------------------------------------------------------- NPS ----


class TestNps(AdapterCase):
    def pulled(self):
        adapter = self.adapter("nps_trails", self.page_transport({"NPS_Public_Trails": "nps_page.json"}))
        return adapter, list(adapter.normalize(adapter.pull()))

    def test_keeps_existing_trails_and_drops_abandoned_and_shapeless(self):
        _, features = self.pulled()
        names = {f.name for f in features}
        self.assertIn("Stony Man Horse Trail", names)
        self.assertNotIn("Chilkoot Trail", names)  # Abandoned
        self.assertNotIn("No shape", names)  # null geometry
        for f in features:
            f.validate()
            self.assertEqual(f.country, "US")
            self.assertEqual(f.license, "us-public-domain")

    def test_ids_rest_on_geometryid_not_objectid(self):
        _, features = self.pulled()
        self.assertTrue(all(not f.id.endswith(":1") for f in features))

    def test_kind_from_the_use_string(self):
        self.assertEqual(nps_module._kind_for({"TRLUSE": "Hiker/Pedestrian"}), "hiking")
        self.assertEqual(nps_module._kind_for({"TRLUSE": "Hiker/Pedestrian | Pack and Saddle"}), "mixed")
        self.assertEqual(nps_module._kind_for({"TRLUSE": "Bicycle"}), "cycling")
        self.assertEqual(nps_module._kind_for({"TRLTYPE": "Water Trail"}), "paddle")
        self.assertEqual(nps_module._kind_for({"TRLUSE": "Unknown"}), "hiking")

    def test_the_status_filter_tolerates_nps_own_typo(self):
        # A third of the network is "Unknown" and one row is "Exisiting"; both
        # are real trails and must not be dropped.
        self.assertIn("exisiting", nps_module._KEEP_STATUS)
        self.assertIn("unknown", nps_module._KEEP_STATUS)


# ---------------------------------------------------------------- Ontario ----


class TestOntario(AdapterCase):
    def test_segments_carry_ogl_ontario_and_the_province(self):
        adapter = self.adapter("ontario_otn", self.page_transport({"LIO_Open04": "ontario_page.json"}))
        features = list(adapter.normalize(adapter.pull()))
        self.assertEqual(len(features), 3)
        for f in features:
            f.validate()
            self.assertEqual(f.country, "CA")
            self.assertEqual(f.admin, "Ontario")
            self.assertEqual(f.feature_class, "segment")
            self.assertIn("Open Government Licence - Ontario", f.attribution)
        self.assertTrue(all(f.id.startswith("ontario_otn:") and f.id.split(":")[1].isdigit() for f in features))

    def test_kind_from_permitted_uses(self):
        k = ontario_module._kind_for
        self.assertEqual(k({"PERMITTED_USES": "Hiking or Walking"}), "hiking")
        self.assertEqual(k({"PERMITTED_USES": "Hiking or Walking, Cycling"}), "mixed")
        self.assertEqual(k({"PERMITTED_USES": "Cycling"}), "cycling")
        self.assertEqual(k({"PERMITTED_USES": "Cross Country Skiing"}), "ski")
        self.assertEqual(k({"PERMITTED_USES": ""}), "mixed")
        self.assertEqual(k({"PERMITTED_USES": "Snowmobiling"}), "other")


# --------------------------------------------------------------------- UK ----


class TestUkNationalTrails(AdapterCase):
    def pulled(self):
        adapter = self.adapter(
            "uk_national_trails",
            self.page_transport(
                {
                    "National_Trails_England": "uk_national_trails_page.json",
                    "England_Coast_Path_Route": "uk_coast_path_page.json",
                }
            ),
        )
        return list(adapter.normalize(adapter.pull()))

    def test_national_trails_are_routes_with_their_names(self):
        trails = [f for f in self.pulled() if f.official_status == "national_trail"]
        self.assertEqual({f.name for f in trails}, {"Pennine Way", "Offa's Dyke Path"})
        for f in trails:
            f.validate()
            self.assertEqual(f.country, "GB")
            self.assertEqual(f.admin, "England")
            self.assertIn("Open Government Licence v3.0", f.attribution)
            self.assertIn("Natural England", f.attribution)

    def test_coast_path_sections_group_under_their_stretch_and_skip_unopened_ones(self):
        coast = [f for f in self.pulled() if "coast_path" in (f.official_status or "")]
        # Two fixture sections, one of them "Not an existing walked route".
        self.assertEqual(len(coast), 1)
        f = coast[0]
        f.validate()
        self.assertTrue(f.parent_id.startswith("uk_national_trails:coast-"))
        self.assertTrue(f.parent_name.startswith("England Coast Path: "))
        self.assertTrue(f.name.startswith("England Coast Path "))
        self.assertIsNotNone(f.ref)


# -------------------------------------------------------------- swisstopo ----


class TestLv95(unittest.TestCase):
    def test_reference_points(self):
        # swisstopo's own worked example region: Bern's Zytglogge and Zurich HB,
        # both to well within the formulas' stated ~1 m accuracy.
        lon, lat = lv95_to_wgs84(2_600_670, 1_199_660)
        self.assertAlmostEqual(lon, 7.4474, places=3)
        self.assertAlmostEqual(lat, 46.9480, places=3)
        lon, lat = lv95_to_wgs84(2_683_160, 1_248_080)
        self.assertAlmostEqual(lon, 8.5398, places=3)
        self.assertAlmostEqual(lat, 47.3783, places=3)

    def test_wgs84_needs_no_transform_and_unknown_srs_is_loud(self):
        self.assertIsNone(to_wgs84(4326))
        with self.assertRaises(ValueError):
            to_wgs84(3857)


class TestGpkgReader(unittest.TestCase):
    def setUp(self):
        self.connection = gpkg.open_gpkg(FIXTURES / "swisstopo_wanderwege_3rows.gpkg")
        self.addCleanup(self.connection.close)

    def test_finds_the_feature_table_and_its_srs(self):
        tables = gpkg.feature_tables(self.connection)
        self.assertEqual([t.name for t in tables], ["tlm_strassen_strasse"])
        self.assertEqual(tables[0].geometry_column, "geom")
        self.assertEqual(tables[0].srs_id, 2056)

    def test_decodes_real_lv95_linestrings_into_switzerland(self):
        table = gpkg.feature_tables(self.connection)[0]
        rows = list(gpkg.iter_rows(self.connection, table))
        self.assertEqual(len(rows), 3)
        for attrs, blob in rows:
            lines = gpkg.decode_lines(blob, lv95_to_wgs84)
            self.assertTrue(lines)
            for lon, lat in lines[0]:
                self.assertTrue(5.9 < lon < 10.6 and 45.7 < lat < 47.9, (lon, lat))
            self.assertNotIn("geom", attrs)

    def test_rejects_garbage_gracefully(self):
        self.assertEqual(gpkg.decode_lines(None), [])
        self.assertEqual(gpkg.decode_lines(b"not a geopackage blob"), [])


class TestSwisstopo(AdapterCase):
    def test_normalizes_every_signposting_class_from_the_geopackage(self):
        source = self.registry.get("swisstopo_wanderwege")
        raw = self.paths.raw_dir(source.id)
        raw.mkdir(parents=True, exist_ok=True)
        # The adapter reads a .gpkg.zip; wrap the fixture in one.
        import zipfile

        with zipfile.ZipFile(raw / "fixture.gpkg.zip", "w") as zf:
            zf.write(FIXTURES / "swisstopo_wanderwege_3rows.gpkg", "SWISSTLM3D_WANDERWEGE.gpkg")
        from trailsdb.manifest import PullManifest

        PullManifest.start(source.id, "swisstopo").finish().write(raw)

        adapter = self.adapter("swisstopo_wanderwege", FakeTransport())
        features = list(adapter.normalize(adapter.load_manifest()))
        self.assertEqual(
            sorted(f.official_status for f in features),
            ["swiss_alpinwanderweg", "swiss_bergwanderweg", "swiss_wanderweg"],
        )
        for f in features:
            f.validate()
            self.assertEqual(f.country, "CH")
            self.assertEqual(f.feature_class, "segment")
            self.assertEqual(f.attribution, "Federal Office of Topography swisstopo")
            self.assertRegex(f.id, r"^swisstopo_wanderwege:[0-9A-F-]{36}$")

    def test_fetch_resolves_the_asset_through_stac(self):
        stac = {"features": [{"id": "swisstlm3d-wanderwege", "assets": {
            "swisstlm3d-wanderwege_2056_5728.gpkg.zip": {"href": "https://data.geo.admin.ch/x/y/w.gpkg.zip"}}}]}

        def handler(_m, url, _k):
            if "stac" in url:
                return FakeResponse(200, json_data=stac)
            return FakeResponse(200, b"PK\x05\x06" + b"\x00" * 18)  # an empty zip

        adapter = self.adapter("swisstopo_wanderwege", FakeTransport(handler))
        manifest = adapter.pull()
        self.assertTrue(manifest.ok, manifest.errors)
        self.assertEqual(Path(manifest.files[0].path).name, "w.gpkg.zip")


if __name__ == "__main__":
    unittest.main()
