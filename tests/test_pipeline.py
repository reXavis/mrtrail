"""End to end: pull -> normalize -> catalog -> export, with no network."""

import dataclasses
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

from tests.support import FakeResponse, FakeTransport, gpx_bytes, line
from trailsdb import geojsonl, pipeline, registry as registry_module, sizing
from trailsdb.catalog import Catalog
from trailsdb.config import Paths
from trailsdb.fetch import PoliteSession
from trailsdb.registry import Legal, Registry

GALICIA = (-9.35, 41.80, -6.70, 43.80)


def verified(registry: Registry, *source_ids: str) -> Registry:
    """A copy of the registry with the named sources marked legally verified."""
    sources = dict(registry.sources)
    for source_id in source_ids:
        source = sources[source_id]
        sources[source_id] = dataclasses.replace(
            source, legal=Legal(verified_on=dt.date(2026, 8, 27), notes=source.legal.notes)
        )
    return Registry(licenses=registry.licenses, sources=sources)


def session_for(transport):
    return PoliteSession(
        transport=transport, rate_limit_s=0, sleeper=lambda _s: None, today=lambda: "2026-08-27"
    )


class PipelineCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.paths = Paths.resolve(Path(self.tmp.name)).ensure()
        self.registry = registry_module.load()

    def seed_cnig(self, source_id="cnig_fedme", name="GR11.gpx", track="GR 11 Senda", start=(-8.5, 42.1)):
        raw_dir = self.paths.raw_dir(source_id)
        raw_dir.mkdir(parents=True, exist_ok=True)
        (raw_dir / "files.json").write_text(
            json.dumps({"files": [{"id": "1", "name": name}]}), encoding="utf-8"
        )
        transport = FakeTransport(default=FakeResponse(200, gpx_bytes(track, line(start))))
        return pipeline.pull_source(
            self.registry.get(source_id), self.paths, session=session_for(transport)
        )


    def normalize_galicia_and_nz(self):
        self.seed_cnig()
        self.seed_cnig(source_id="cnig_camino", name="frances-1.gpx",
                       track="Camino Francés - ES01c-01a-saint-jean-roncesvalles", start=(-8.4, 42.9))
        # Something well outside the Galicia box, to prove the cut actually cuts.
        raw = self.paths.raw_dir("nz_doc") / "walking"
        raw.mkdir(parents=True, exist_ok=True)
        (raw / "page-0000.geojson").write_text(
            json.dumps(
                {
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "geometry": {
                                "type": "LineString",
                                "coordinates": [[170.0, -43.0], [170.1, -43.1]],
                            },
                            "properties": {"OBJECTID": 1, "name": "Milford Track",
                                           "walkingAndTrampingWebPage": "https://www.doc.govt.nz/link/d7a3a8ec03804341b8bc15c23e21a722.aspx"},
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        from trailsdb.manifest import PullManifest

        PullManifest.start("nz_doc", "nz_doc").finish().write(self.paths.raw_dir("nz_doc"))

        for source_id in ("cnig_fedme", "cnig_camino", "nz_doc"):
            pipeline.normalize_source(self.registry.get(source_id), self.paths)


class TestPullAndNormalize(PipelineCase):
    def test_full_round_trip_writes_master_and_catalog(self):
        manifest = self.seed_cnig()
        self.assertTrue(manifest.ok, manifest.errors)

        with Catalog(self.paths.catalog) as catalog:
            result = pipeline.normalize_source(
                self.registry.get("cnig_fedme"), self.paths, catalog=catalog
            )
            self.assertEqual(result.features, 1)
            self.assertGreater(result.length_km, 0)
            self.assertEqual(catalog.totals().features, 1)
            row = catalog.search("GR 11")
            self.assertEqual(len(row), 1)

        master = self.paths.normalized_path("cnig_fedme")
        self.assertTrue(master.exists())
        self.assertEqual(master.read_bytes()[:2], b"\x1f\x8b")  # gzipped
        feature = next(iter(geojsonl.read(master)))
        feature.validate()
        self.assertEqual(feature.ref, "GR 11")

    def test_normalize_is_idempotent(self):
        self.seed_cnig()
        with Catalog(self.paths.catalog) as catalog:
            source = self.registry.get("cnig_fedme")
            first = pipeline.normalize_source(source, self.paths, catalog=catalog)
            second = pipeline.normalize_source(source, self.paths, catalog=catalog)
            self.assertEqual(first.features, second.features)
            self.assertEqual(catalog.totals().features, first.features)

    def test_normalize_without_a_catalog_still_measures(self):
        self.seed_cnig()
        result = pipeline.normalize_source(self.registry.get("cnig_fedme"), self.paths)
        self.assertEqual(result.features, 1)
        self.assertGreater(result.points_per_km, 0)
        self.assertGreater(result.kb_per_km, 0)

    def test_pull_records_the_run_in_the_catalog(self):
        raw_dir = self.paths.raw_dir("cnig_fedme")
        raw_dir.mkdir(parents=True, exist_ok=True)
        (raw_dir / "files.json").write_text(json.dumps({"files": [{"id": "1", "name": "a.gpx"}]}))
        transport = FakeTransport(default=FakeResponse(200, gpx_bytes("GR 11", line((-8.5, 42.1)))))
        with Catalog(self.paths.catalog) as catalog:
            pipeline.pull_source(
                self.registry.get("cnig_fedme"),
                self.paths,
                session=session_for(transport),
                catalog=catalog,
            )
            self.assertEqual(catalog.last_pull("cnig_fedme")["file_count"], 1)


class TestLayerAssignment(unittest.TestCase):
    def setUp(self):
        self.registry = registry_module.load()

    def test_routes_and_segments_split(self):
        self.assertEqual(pipeline.layer_for(self.registry.get("cnig_fedme")), "official")
        self.assertEqual(
            pipeline.layer_for(self.registry.get("swisstopo_wanderwege")), "official_net"
        )

    def test_share_alike_gets_its_own_layer(self):
        # Mixing CC BY-SA into a shared layer would drag the whole layer under
        # share-alike terms.
        source = self.registry.get("refuges_info")
        self.assertTrue(source.license.share_alike)
        self.assertEqual(pipeline.layer_for(source), "refuges_info")

    def test_segments_stop_a_zoom_level_earlier(self):
        route_layer = pipeline.LayerExport("official", Path("x"), 1, 10.0, "route")
        segment_layer = pipeline.LayerExport("official_net", Path("x"), 1, 10.0, "segment")
        self.assertIn("--maximum-zoom=14", pipeline.tippecanoe_args(route_layer))
        self.assertIn("--maximum-zoom=13", pipeline.tippecanoe_args(segment_layer))

    def test_the_size_estimate_assumes_the_zoom_the_layer_is_baked_at(self):
        # An estimate that charges segments the z14 rate while tippecanoe bakes
        # them at z13 overstates every segment-heavy pack twofold.
        route_layer = pipeline.LayerExport("official", Path("x"), 1, 1000.0, "route")
        segment_layer = pipeline.LayerExport("official_net", Path("x"), 1, 1000.0, "segment")
        self.assertAlmostEqual(
            segment_layer.estimated_tiles_mb,
            route_layer.estimated_tiles_mb * sizing.SEGMENT_Z13_FACTOR,
            places=4,
        )


class TestExport(PipelineCase):
    def test_unverified_sources_are_refused_by_default(self):
        self.normalize_galicia_and_nz()
        result = pipeline.export_pack(self.registry, self.paths, pack="galicia", bbox=GALICIA)
        self.assertEqual(result.layers, [])
        reasons = dict(result.skipped)
        self.assertIn("not verified", reasons["cnig_fedme"])

    def test_verified_sources_export_and_the_bbox_cuts(self):
        self.normalize_galicia_and_nz()
        registry = verified(self.registry, "cnig_fedme", "cnig_camino", "nz_doc")

        result = pipeline.export_pack(registry, self.paths, pack="galicia", bbox=GALICIA)

        self.assertEqual([layer.layer for layer in result.layers], ["official"])
        layer = result.layers[0]
        self.assertEqual(layer.features, 2)  # both Spanish sources, not the NZ one
        self.assertEqual(sorted(layer.sources), ["cnig_camino", "cnig_fedme"])
        ids = {f.id for f in geojsonl.read(layer.path)}
        self.assertTrue(all(i.startswith("cnig_") for i in ids))

    def test_allow_unverified_is_an_explicit_override(self):
        self.normalize_galicia_and_nz()
        result = pipeline.export_pack(
            self.registry, self.paths, pack="galicia", bbox=GALICIA, allow_unverified=True
        )
        self.assertTrue(result.layers)

    def test_export_writes_attribution_and_manifest(self):
        self.normalize_galicia_and_nz()
        registry = verified(self.registry, "cnig_fedme", "cnig_camino")
        result = pipeline.export_pack(registry, self.paths, pack="galicia", bbox=GALICIA)

        out_dir = self.paths.export_dir("galicia")
        attribution = json.loads((out_dir / "attribution.json").read_text())
        credits = {row["source"]: row["attribution"] for row in attribution["sources"]}
        # The IGN-prescribed citation form: product, licence, producer.
        self.assertIn("CC-BY 4.0", credits["cnig_fedme"])
        self.assertIn("Senderos FEDME", credits["cnig_fedme"])

        export = json.loads((out_dir / "export.json").read_text())
        self.assertEqual(export["pack"], "galicia")
        self.assertIn("--maximum-zoom=14", export["layers"][0]["tippecanoe"])
        self.assertGreater(result.estimated_tiles_mb, 0)

    def test_a_source_contributing_nothing_leaves_no_empty_layer_file(self):
        self.normalize_galicia_and_nz()
        registry = verified(self.registry, "nz_doc")
        result = pipeline.export_pack(registry, self.paths, pack="galicia", bbox=GALICIA)
        self.assertEqual(result.layers, [])
        self.assertFalse((self.paths.export_dir("galicia") / "official.geojsonl").exists())

    def test_not_normalized_sources_are_reported_not_silently_dropped(self):
        registry = verified(self.registry, "usfs_trails")
        result = pipeline.export_pack(registry, self.paths, pack="galicia", bbox=GALICIA)
        self.assertIn(("usfs_trails", "not normalized yet"), result.skipped)


class TestBake(PipelineCase):
    def test_bake_writes_one_pmtiles_per_layer_and_measures_it(self):
        if not pipeline.tippecanoe_available():
            self.skipTest("tippecanoe not installed")
        self.normalize_galicia_and_nz()
        registry = verified(self.registry, "cnig_fedme", "cnig_camino")
        pipeline.export_pack(registry, self.paths, pack="galicia", bbox=GALICIA)
        result = pipeline.bake_pack(self.paths, pack="galicia")
        self.assertEqual([b.layer for b in result.layers], ["official"])
        baked = result.layers[0]
        self.assertTrue(baked.pmtiles.exists())
        self.assertGreater(baked.bytes, 0)
        self.assertGreater(baked.kb_per_km, 0)
        report = json.loads((self.paths.export_dir("galicia") / "bake.json").read_text())
        self.assertEqual(report["layers"][0]["pmtiles"], "official.pmtiles")

    def test_bake_without_tippecanoe_is_a_clear_error(self):
        import unittest.mock as mock

        with mock.patch.object(pipeline, "tippecanoe_available", return_value=None):
            with self.assertRaises(RuntimeError):
                pipeline.bake_pack(self.paths, pack="galicia")


class TestHealth(unittest.TestCase):
    def setUp(self):
        self.registry = registry_module.load()

    def test_reports_reachable_and_moved_endpoints(self):
        def handler(_method, url, _kwargs):
            return FakeResponse(404 if "DOC_Walking_Experiences" in url else 200)

        session = session_for(FakeTransport(handler))
        results = pipeline.health_check(
            [self.registry.get("nz_doc"), self.registry.get("cnig_fedme")], session=session
        )
        by_id = {r.source_id: r for r in results}
        self.assertFalse(by_id["nz_doc"].ok)
        self.assertTrue(by_id["cnig_fedme"].ok)

    def test_network_failure_is_reported_not_raised(self):
        def handler(*_):
            raise ConnectionError("dns failure")

        session = session_for(FakeTransport(handler))
        session.retries = 0
        results = pipeline.health_check([self.registry.get("nz_doc")], session=session)
        self.assertFalse(results[0].ok)
        self.assertIn("dns failure", results[0].error)


class TestLicensesDocument(unittest.TestCase):
    def setUp(self):
        self.registry = registry_module.load()
        self.document = pipeline.licenses_document(self.registry)

    def test_covers_every_source_and_license(self):
        self.assertEqual(len(self.document["sources"]), len(self.registry))
        self.assertEqual(len(self.document["licenses"]), len(self.registry.licenses))

    def test_every_source_names_a_license_present_in_the_document(self):
        known = {lic["id"] for lic in self.document["licenses"]}
        for source in self.document["sources"]:
            self.assertIn(source["license"], known)

    def test_templated_attributions_are_flagged(self):
        by_id = {s["id"]: s for s in self.document["sources"]}
        self.assertTrue(by_id["eurovelo"]["attribution_is_templated"])
        self.assertFalse(by_id["cnig_fedme"]["attribution_is_templated"])

    def test_it_is_json_serializable(self):
        json.dumps(self.document)


class TestSizeModel(unittest.TestCase):
    """The model must keep reproducing the plan's headline numbers."""

    def setUp(self):
        self.registry = registry_module.load()

    def total(self, **kwargs):
        total = sizing.SizeEstimate(0.0, 0.0, 0.0)
        for source in self.registry:
            total = total + sizing.estimate(
                source.estimated_km, feature_class=source.feature_class, **kwargs
            )
        return total

    def test_master_database_is_about_1_2_gb(self):
        self.assertAlmostEqual(self.total().master_mb / 1024, 1.2, delta=0.4)

    def test_world_tiles_land_in_the_2_to_3_5_gb_range(self):
        gigabytes = self.total().tiles_mb / 1024
        self.assertGreater(gigabytes, 2.0)
        self.assertLess(gigabytes, 3.5)

    def test_the_z13_segment_lever_cuts_the_worst_case(self):
        self.assertLess(self.total(cap_segments_at_z13=True).tiles_mb, self.total().tiles_mb * 0.8)

    def test_galicia_grows_by_about_one_percent(self):
        # The plan's per-pack table: ~4-6k km of official trails, +15-20 MB, +1%.
        tiles_mb = sizing.estimate(5000).tiles_mb
        self.assertGreater(tiles_mb, 15.0)
        self.assertLess(tiles_mb, 20.0)
        self.assertLess(sizing.pack_growth_percent(tiles_mb), 1.5)

    def test_the_alps_worst_case_stays_around_twelve_percent(self):
        alps_mb = sizing.estimate(105_000, feature_class="segment").tiles_mb
        growth = sizing.pack_growth_percent(alps_mb, 3.15 * 1024**3)
        self.assertGreater(growth, 8.0)
        self.assertLess(growth, 14.0)

    def test_the_zoom_lever_pulls_the_alps_under_seven_percent(self):
        capped = sizing.estimate(105_000, feature_class="segment", cap_segments_at_z13=True)
        self.assertLess(sizing.pack_growth_percent(capped.tiles_mb, 3.15 * 1024**3), 7.0)


class TestStatus(PipelineCase):
    def test_status_reports_readiness_and_progress(self):
        self.seed_cnig()
        pipeline.normalize_source(self.registry.get("cnig_fedme"), self.paths)
        rows = {row.source.id: row for row in pipeline.status(self.registry, self.paths)}

        self.assertTrue(rows["cnig_fedme"].adapter_ready)
        self.assertEqual(rows["cnig_fedme"].pulled_files, 1)
        self.assertEqual(rows["cnig_fedme"].normalized_features, 1)
        self.assertFalse(rows["cnig_fedme"].verified)

        self.assertFalse(rows["australia_states"].adapter_ready)
        self.assertIn("Americas & Oceania wave", rows["australia_states"].phase)


if __name__ == "__main__":
    unittest.main()
