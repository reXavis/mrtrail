import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from tests.support import FakeResponse, FakeTransport, gpx_bytes, line
from trailsdb import registry as registry_module
from trailsdb.adapters import AdapterContext, CnigAdapter, build
from trailsdb.adapters import cnig as cnig_module
from trailsdb.adapters.base import AdapterNotImplemented
from trailsdb.config import Paths
from trailsdb.fetch import PoliteSession
from trailsdb.formats.archive import UnsupportedFormat


def session_for(transport) -> PoliteSession:
    return PoliteSession(
        transport=transport,
        rate_limit_s=0,
        sleeper=lambda _s: None,
        today=lambda: "2026-08-27",
    )


class AdapterCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.paths = Paths.resolve(Path(self.tmp.name)).ensure()
        self.registry = registry_module.load()

    def adapter(self, source_id: str, transport):
        source = self.registry.get(source_id)
        return build(
            AdapterContext(source=source, paths=self.paths, session=session_for(transport))
        )


# ------------------------------------------------------------------- CNIG ----


class TestCnigParsers(unittest.TestCase):
    def test_extracts_waymarking_refs(self):
        self.assertEqual(cnig_module.extract_ref("GR 11 Senda Pirenaica"), "GR 11")
        self.assertEqual(cnig_module.extract_ref("PR-G 100"), "PR-G 100")
        self.assertEqual(cnig_module.extract_ref("sl_g_12.gpx"), "SL-G 12")
        self.assertIsNone(cnig_module.extract_ref("Camino Frances"))

    def test_extracts_stage_numbers(self):
        self.assertEqual(cnig_module.extract_stage("Etapa 12: Burgos - Hontanas"), 12)
        self.assertEqual(cnig_module.extract_stage("E7"), 7)
        self.assertIsNone(cnig_module.extract_stage("Camino Frances"))

    def test_variant_name_is_everything_before_the_stage(self):
        self.assertEqual(
            cnig_module.variant_name("Camino Frances. Etapa 12: Burgos", "x"), "Camino Frances"
        )
        self.assertEqual(cnig_module.variant_name("Via de la Plata - Etapa 3", "y"), "Via de la Plata")

    def test_discovery_parses_json_and_html(self):
        json_response = FakeResponse(
            200, json_data={"data": [{"idFichero": 7, "nombre": "gr11.zip"}]}
        )
        self.assertEqual(
            cnig_module.parse_discovery(json_response), [{"id": "7", "name": "gr11.zip"}]
        )

        html = '<a href="/CentroDescargas/descargaDir?secDescDirLA=1234">GR-11.zip</a>'
        html_response = FakeResponse(200, html.encode(), {"Content-Type": "text/html"})
        self.assertEqual(
            cnig_module.parse_discovery(html_response), [{"id": "1234", "name": "GR-11.zip"}]
        )


class TestCnigAdapter(AdapterCase):
    def seed_index(self, source_id: str, entries):
        raw_dir = self.paths.raw_dir(source_id)
        raw_dir.mkdir(parents=True, exist_ok=True)
        (raw_dir / "files.json").write_text(json.dumps({"files": entries}), encoding="utf-8")

    def test_pull_uses_the_committed_index_and_paces_requests(self):
        self.seed_index("cnig_fedme", [{"id": "1", "name": "GR11.gpx"}, {"id": "2", "name": "PR-G 100.gpx"}])
        payload = gpx_bytes("GR 11", line((-8.5, 42.1)))
        transport = FakeTransport(default=FakeResponse(200, payload))

        manifest = self.adapter("cnig_fedme", transport).pull()

        self.assertTrue(manifest.ok, manifest.errors)
        self.assertEqual(len(manifest.files), 2)
        self.assertIn("secDescDirLA=1", transport.urls[0])
        # Spaces in a published name must not become a bad path.
        self.assertTrue((self.paths.raw_dir("cnig_fedme") / "files" / "PR-G_100.gpx").exists())

    def test_pull_fails_loudly_when_discovery_finds_nothing(self):
        transport = FakeTransport(default=FakeResponse(200, b"<html>no results</html>"))
        manifest = self.adapter("cnig_fedme", transport).pull()
        self.assertFalse(manifest.ok)
        self.assertIn("discovery returned no files", manifest.errors[0])

    def test_live_discovery_caches_its_result(self):
        transport = FakeTransport(
            default=FakeResponse(200, json_data=[{"id": 5, "nombre": "a.gpx"}])
        )
        adapter = self.adapter("cnig_fedme", transport)
        self.assertEqual(adapter.discover_live(), [{"id": "5", "name": "a.gpx"}])
        cached = json.loads((self.paths.raw_dir("cnig_fedme") / "files.json").read_text())
        self.assertEqual(cached["files"], [{"id": "5", "name": "a.gpx"}])

    def test_normalize_stamps_provenance_and_extracts_refs(self):
        self.seed_index("cnig_fedme", [{"id": "1", "name": "GR11.gpx"}])
        transport = FakeTransport(default=FakeResponse(200, gpx_bytes("GR 11 Senda", line((-8.5, 42.1)))))
        adapter = self.adapter("cnig_fedme", transport)
        manifest = adapter.pull()

        features = list(adapter.normalize(manifest))
        self.assertEqual(len(features), 1)
        feature = features[0]
        feature.validate()
        self.assertEqual(feature.id, "cnig_fedme:GR11")
        self.assertEqual(feature.ref, "GR 11")
        self.assertEqual(feature.license, "cc-by-4.0")
        self.assertEqual(feature.attribution, "(c) Instituto Geografico Nacional de Espana")
        self.assertEqual(feature.official_status, "homologado")
        self.assertEqual(feature.country, "ES")
        self.assertEqual(feature.feature_class, "route")

    def test_camino_stages_group_under_a_parent(self):
        self.seed_index("cnig_camino", [{"id": "1", "name": "frances-12.gpx"}])
        payload = gpx_bytes("Camino Frances. Etapa 12: Burgos - Hontanas", line((-3.7, 42.3)))
        transport = FakeTransport(default=FakeResponse(200, payload))
        adapter = self.adapter("cnig_camino", transport)
        manifest = adapter.pull()

        feature = next(iter(adapter.normalize(manifest)))
        feature.validate()
        self.assertEqual(feature.stage_no, 12)
        self.assertEqual(feature.parent_name, "Camino Frances")
        self.assertEqual(feature.parent_id, "cnig_camino:camino-frances")

    def test_reads_gpx_out_of_a_zip(self):
        self.seed_index("cnig_caminos_naturales", [{"id": "1", "name": "cn.zip"}])
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("via-verde.gpx", gpx_bytes("Via Verde del Aceite", line((-4.0, 37.6))))
        transport = FakeTransport(default=FakeResponse(200, buffer.getvalue()))
        adapter = self.adapter("cnig_caminos_naturales", transport)
        manifest = adapter.pull()

        features = list(adapter.normalize(manifest))
        self.assertEqual(len(features), 1)
        self.assertEqual(features[0].kind, "mixed")
        self.assertEqual(features[0].extras["archive_member"], "via-verde.gpx")

    def test_shapefile_only_archive_raises_rather_than_undercounting(self):
        self.seed_index("cnig_fedme", [{"id": "1", "name": "shp.zip"}])
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("senderos.shp", b"\x00\x00")
            archive.writestr("senderos.dbf", b"\x00\x00")
        transport = FakeTransport(default=FakeResponse(200, buffer.getvalue()))
        adapter = self.adapter("cnig_fedme", transport)
        manifest = adapter.pull()

        with self.assertRaises(UnsupportedFormat):
            list(adapter.normalize(manifest))

    def test_multi_track_files_get_distinct_stable_ids(self):
        self.seed_index("cnig_fedme", [{"id": "1", "name": "two.gpx"}])
        payload = (
            b'<?xml version="1.0"?><gpx xmlns="http://www.topografix.com/GPX/1/1">'
            b"<trk><name>GR 11</name><trkseg>"
            b'<trkpt lat="42.1" lon="-8.5"/><trkpt lat="42.2" lon="-8.4"/>'
            b"</trkseg></trk>"
            b"<trk><name>GR 12</name><trkseg>"
            b'<trkpt lat="43.1" lon="-7.5"/><trkpt lat="43.2" lon="-7.4"/>'
            b"</trkseg></trk></gpx>"
        )
        transport = FakeTransport(default=FakeResponse(200, payload))
        adapter = self.adapter("cnig_fedme", transport)
        manifest = adapter.pull()

        ids = [f.id for f in adapter.normalize(manifest)]
        self.assertEqual(ids, ["cnig_fedme:two-1", "cnig_fedme:two-2"])


# ------------------------------------------------------------------ NZ DOC ---


def doc_feature(asset_id, coords, **props):
    return {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": coords},
        "properties": {"assetId": asset_id, **props},
    }


class TestNzDocAdapter(AdapterCase):
    def test_pull_paginates_until_a_short_page(self):
        pages = [
            FakeResponse(
                200,
                json_data={
                    "type": "FeatureCollection",
                    "features": [
                        doc_feature(i, [[170.0, -43.0], [170.1, -43.1]], name=f"Track {i}")
                        for i in range(3)
                    ],
                },
            )
        ]
        transport = FakeTransport(lambda *_: pages[0])
        manifest = self.adapter("nz_doc", transport).pull()

        self.assertTrue(manifest.ok, manifest.errors)
        self.assertEqual(len(manifest.files), 1)  # short page stops pagination
        self.assertIn("features=3", manifest.notes)

    def test_pull_fails_when_the_endpoint_returns_nothing(self):
        empty = FakeResponse(200, json_data={"type": "FeatureCollection", "features": []})
        manifest = self.adapter("nz_doc", FakeTransport(default=empty)).pull()
        self.assertFalse(manifest.ok)
        self.assertIn("zero features", manifest.errors[0])

    def test_normalize_maps_categories_skips_points_and_dedupes(self):
        features = [
            doc_feature(1, [[170.0, -43.0], [170.1, -43.1]], name="Milford Track",
                        walkTrackCategory="Great Walk", region="Southland"),
            doc_feature(2, [[171.0, -44.0], [171.1, -44.1]], name="Alps 2 Ocean",
                        walkTrackCategory="Cycle Trail"),
            {  # DOC ships point assets in the same layer
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [170.0, -43.0]},
                "properties": {"assetId": 3, "name": "Hut"},
            },
            doc_feature(1, [[170.0, -43.0], [170.1, -43.1]], name="Milford Track (duplicate)"),
        ]
        response = FakeResponse(200, json_data={"type": "FeatureCollection", "features": features})
        adapter = self.adapter("nz_doc", FakeTransport(default=response))
        manifest = adapter.pull()

        normalized = list(adapter.normalize(manifest))
        for feature in normalized:
            feature.validate()
        self.assertEqual([f.id for f in normalized], ["nz_doc:1", "nz_doc:2"])
        self.assertEqual(normalized[0].kind, "hiking")
        self.assertEqual(normalized[0].official_status, "Great Walk")
        self.assertEqual(normalized[0].admin, "Southland")
        self.assertEqual(normalized[1].kind, "cycling")
        self.assertEqual(normalized[0].country, "NZ")


# ---------------------------------------------------------------- EuroVelo ---


class TestEuroVeloAdapter(AdapterCase):
    def test_route_table_has_seventeen_non_contiguous_corridors(self):
        from trailsdb.adapters.eurovelo import ROUTES

        self.assertEqual(len(ROUTES), 17)
        self.assertNotIn(16, ROUTES)
        self.assertNotIn(18, ROUTES)
        self.assertIn(19, ROUTES)

    def test_attribution_carries_the_retrieval_date(self):
        transport = FakeTransport(default=FakeResponse(200, gpx_bytes("EV1", line((-9.1, 38.7)))))
        adapter = self.adapter("eurovelo", transport)
        manifest = adapter.pull(limit=1)

        feature = next(iter(adapter.normalize(manifest)))
        feature.validate()
        self.assertEqual(feature.ref, "EV1")
        self.assertIn(manifest.retrieved_on, feature.attribution)
        self.assertIn("European Cyclists", feature.attribution)
        self.assertEqual(feature.kind, "cycling")
        self.assertEqual(feature.extras["official_name"], "Atlantic Coast Route")

    def test_a_missing_corridor_is_recorded_but_does_not_lose_the_rest(self):
        def handler(_method, url, _kwargs):
            if "ev2" in url:
                return FakeResponse(404)
            return FakeResponse(200, gpx_bytes("EV", line((-9.1, 38.7))))

        adapter = self.adapter("eurovelo", FakeTransport(handler))
        manifest = adapter.pull(limit=3)

        self.assertEqual(len(manifest.files), 2)
        self.assertEqual(len(manifest.errors), 1)
        self.assertIn("EV2", manifest.errors[0])

    def test_all_corridors_failing_is_a_hard_failure(self):
        adapter = self.adapter("eurovelo", FakeTransport(default=FakeResponse(404)))
        manifest = adapter.pull(limit=2)
        self.assertFalse(manifest.ok)
        self.assertTrue(any("no EuroVelo files downloaded" in e for e in manifest.errors))


# ------------------------------------------------------------- unimplemented -


class TestPlannedAdapters(AdapterCase):
    def test_a_planned_adapter_says_which_phase_it_is_due_in(self):
        with self.assertRaises(AdapterNotImplemented) as ctx:
            self.adapter("swisstopo_wanderwege", FakeTransport())
        self.assertIn("Europe wave", str(ctx.exception))

    def test_cnig_series_must_be_one_the_adapter_knows(self):
        source = self.registry.get("cnig_fedme")
        bad = type(source)(**{**{f: getattr(source, f) for f in source.__slots__}, "series": "nope"})
        adapter = CnigAdapter(
            AdapterContext(source=bad, paths=self.paths, session=session_for(FakeTransport()))
        )
        with self.assertRaises(ValueError):
            _ = adapter.series


if __name__ == "__main__":
    unittest.main()
