"""Spots: the point feature class, from schema to tiles, and the refuges.info adapter."""

import json
import tempfile
import unittest
from pathlib import Path

from tests.support import FakeResponse, FakeTransport
from trailsdb import geo, pipeline, registry as registry_module, sizing
from trailsdb.adapters import AdapterContext, build
from trailsdb.adapters import refuges_info as refuges_module
from trailsdb.config import Paths
from trailsdb.fetch import PoliteSession
from trailsdb.schema import Feature, ValidationError

FIXTURES = Path(__file__).parent / "fixtures"


def spot(**overrides) -> Feature:
    fields = dict(
        id="refuges_info:10248",
        source="refuges_info",
        license="cc-by-sa-2.0",
        attribution="© refuges.info contributors, CC BY-SA 2.0",
        feature_class="spot",
        geometry={"type": "Point", "coordinates": [0.89836, 42.74855]},
        name="Cabana de Moredo",
        category="shelter",
    )
    fields.update(overrides)
    return Feature(**fields)


class TestSpotSchema(unittest.TestCase):
    def test_a_spot_is_a_point_with_a_category(self):
        feature = spot()
        feature.validate()
        self.assertEqual(feature.tile_properties()["category"], "shelter")
        self.assertEqual(Feature.from_geojson(feature.to_geojson()), feature)

    def test_lines_do_not_carry_a_category(self):
        line = spot(feature_class="route", geometry={"type": "LineString", "coordinates": [[0, 42], [0.1, 42.1]]})
        with self.assertRaises(ValidationError):
            line.validate()
        line.category = None
        line.validate()
        self.assertNotIn("category", line.tile_properties())

    def test_a_spot_rejects_a_line_and_a_route_rejects_a_point(self):
        with self.assertRaises(ValidationError):
            spot(geometry={"type": "LineString", "coordinates": [[0, 42], [0.1, 42.1]]}).validate()
        with self.assertRaises(ValidationError):
            spot(feature_class="route", category=None).validate()
        with self.assertRaises(ValidationError):
            spot(category="castle").validate()

    def test_geometry_helpers_treat_a_point_as_a_zero_length_line(self):
        point = {"type": "Point", "coordinates": [0.123456789, 42.987654321]}
        self.assertEqual(geo.length_km(point), 0.0)
        self.assertEqual(geo.point_count(point), 1)
        self.assertEqual(geo.bbox(point), (0.123456789, 42.987654321, 0.123456789, 42.987654321))
        self.assertEqual(geo.round_geometry(point), {"type": "Point", "coordinates": [0.123457, 42.987654]})

    def test_spots_are_priced_per_feature(self):
        est = sizing.estimate(0.0, feature_class="spot", features=2048)
        self.assertEqual(est.km, 0.0)
        self.assertAlmostEqual(est.tiles_mb, 2 * sizing.KB_PER_SPOT_TILES)
        self.assertAlmostEqual(est.master_mb, 2 * sizing.KB_PER_SPOT_MASTER)
        self.assertEqual(sizing.estimate(0.0, feature_class="spot").tiles_mb, 0.0)


def session_for(transport):
    return PoliteSession(transport=transport, rate_limit_s=0, sleeper=lambda _s: None, today=lambda: "2026-09-04")


class RefugesCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.paths = Paths.resolve(Path(self.tmp.name)).ensure()
        self.registry = registry_module.load()
        self.page = json.loads((FIXTURES / "refuges_info_cell.geojson").read_text())

    def adapter(self, transport):
        return build(AdapterContext(source=self.registry.get("refuges_info"), paths=self.paths, session=session_for(transport)))

    def transport(self, page=None):
        body = json.dumps(page or self.page).encode("utf-8")
        return FakeTransport(default=FakeResponse(200, body, {"Content-Type": "application/json"}))


class TestRefugesInfo(RefugesCase):
    def test_grid_covers_europe_in_four_degree_cells(self):
        grid = refuges_module.cells()
        self.assertEqual(grid[0], (-12.0, 35.0, -8.0, 39.0))
        self.assertTrue(all(e - w <= 4.0 and n - s <= 4.0 for w, s, e, n in grid))
        self.assertLess(len(grid), 120)  # about a hundred polite requests a quarter

    def test_pull_asks_every_cell_for_all_its_points_and_checks_the_licence_line(self):
        transport = self.transport()
        manifest = self.adapter(transport).pull(limit=2)
        self.assertTrue(manifest.ok, manifest.errors)
        self.assertEqual(len(manifest.files), 2)
        self.assertTrue(all("nb_points=all" in u and "format=geojson" in u for u in transport.urls))
        self.assertIn("bbox=-12.0,35.0,-8.0,39.0", transport.urls[0])

    def test_a_changed_copyright_line_stops_the_pull(self):
        page = dict(self.page, copyright="The data is made available under ODbL")
        manifest = self.adapter(self.transport(page)).pull(limit=1)
        self.assertFalse(manifest.ok)
        self.assertIn("copyright line", manifest.errors[0])

    def test_points_normalize_to_categorised_spots_and_dedupe_across_cells(self):
        adapter = self.adapter(self.transport())
        manifest = adapter.pull(limit=2)  # the same page twice: two cells sharing an edge
        features = list(adapter.normalize(manifest))

        self.assertEqual(len(features), 4)
        by_name = {f.name: f for f in features}
        cabane = by_name["Cabana de Moredo"]
        cabane.validate()
        self.assertEqual(cabane.id, "refuges_info:10248")
        self.assertEqual(cabane.feature_class, "spot")
        self.assertEqual(cabane.category, "shelter")
        self.assertEqual(cabane.license, "cc-by-sa-2.0")
        self.assertIn("CC BY-SA 2.0", cabane.attribution)
        self.assertEqual(cabane.geometry, {"type": "Point", "coordinates": [0.89836, 42.74855]})
        self.assertEqual(cabane.extras["altitude_m"], 1856)
        self.assertEqual(cabane.extras["places"], 2)
        self.assertTrue(cabane.source_url.startswith("https://www.refuges.info/point/10248/"))
        self.assertEqual(by_name["Refuge de Saboredo"].category, "hut")
        self.assertEqual(by_name["Refuge de l'Artiga du Lin"].category, "gite")
        self.assertEqual(by_name["Fontaine parking de Bareilles"].category, "water")

    def test_category_mapping_is_by_label_prefix(self):
        self.assertEqual(refuges_module.category_for("Point de passage"), "pass")
        self.assertEqual(refuges_module.category_for("sommet"), "summit")
        self.assertEqual(refuges_module.category_for("lac"), "lake")
        self.assertEqual(refuges_module.category_for("bivouac"), "bivouac")
        self.assertEqual(refuges_module.category_for("piste de ski"), "other")
        self.assertEqual(refuges_module.category_for(None), "other")


class TestSpotExport(RefugesCase):
    def test_spots_get_their_own_layer_to_z14_and_are_counted_not_measured(self):
        adapter = self.adapter(self.transport())
        manifest = adapter.pull(limit=1)
        pipeline.normalize_source(self.registry.get("refuges_info"), self.paths)

        result = pipeline.export_pack(self.registry, self.paths, pack="pyrenees", bbox=(-1.8, 42.2, 3.2, 43.4))
        layers = {layer.layer: layer for layer in result.layers}
        self.assertIn("refuges_info", layers)  # share-alike: a layer of its own
        layer = layers["refuges_info"]
        self.assertEqual(layer.feature_class, "spot")
        self.assertEqual(layer.features, 4)
        self.assertEqual(layer.length_km, 0.0)
        self.assertGreater(layer.estimated_tiles_mb, 0.0)
        args = pipeline.tippecanoe_args(layer)
        self.assertIn("--maximum-zoom=14", args)
        self.assertIn("--include=category", args)
        rows = [json.loads(line) for line in layer.path.read_text().splitlines()]
        self.assertEqual(rows[0]["geometry"]["type"], "Point")
        self.assertEqual(rows[0]["properties"]["category"], "shelter")
        self.assertNotIn("attribution", rows[0]["properties"])


if __name__ == "__main__":
    unittest.main()
