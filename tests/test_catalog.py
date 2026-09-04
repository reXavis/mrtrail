import tempfile
import unittest
from pathlib import Path

from trailsdb import registry as registry_module
from trailsdb.catalog import Catalog
from trailsdb.manifest import PullManifest
from trailsdb.schema import Feature


def make(index: int, *, source="nz_doc", lon=170.0, lat=-43.0, name=None) -> Feature:
    return Feature(
        id=f"{source}:{index}",
        source=source,
        license="cc-by-4.0",
        attribution="Sourced from DOC",
        feature_class="route",
        geometry={"type": "LineString", "coordinates": [[lon, lat], [lon + 0.1, lat - 0.1]]},
        name=name or f"Track {index}",
        ref=f"T{index}",
    )


class TestCatalog(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.catalog = Catalog(Path(self.tmp.name) / "catalog.sqlite")
        self.addCleanup(self.catalog.close)
        self.registry = registry_module.load()
        self.catalog.upsert_source(self.registry.get("nz_doc"))

    def test_upsert_source_is_idempotent(self):
        self.catalog.upsert_source(self.registry.get("nz_doc"))
        rows = self.catalog.db.execute("SELECT COUNT(*) AS n FROM sources").fetchone()
        self.assertEqual(rows["n"], 1)

    def test_replace_features_reports_stats(self):
        stats = self.catalog.replace_features("nz_doc", [make(1), make(2)])
        self.assertEqual(stats.features, 2)
        self.assertGreater(stats.length_km, 0)
        self.assertEqual(stats.points, 4)
        self.assertGreater(stats.points_per_km, 0)

    def test_replace_features_does_not_accumulate_duplicates(self):
        self.catalog.replace_features("nz_doc", [make(1), make(2)])
        self.catalog.replace_features("nz_doc", [make(1)])
        self.assertEqual(self.catalog.totals().features, 1)

    def test_totals_and_per_source_stats(self):
        self.catalog.replace_features("nz_doc", [make(i) for i in range(3)])
        stats = self.catalog.stats()
        self.assertEqual([s.source_id for s in stats], ["nz_doc"])
        self.assertEqual(self.catalog.totals().features, 3)

    def test_bbox_query_is_the_pack_cut(self):
        self.catalog.replace_features(
            "nz_doc",
            [make(1, lon=170.0, lat=-43.0), make(2, lon=-8.0, lat=42.0)],
        )
        nz_box = (165.0, -47.0, 179.0, -34.0)
        self.assertEqual(self.catalog.feature_ids_in_bbox(nz_box), {"nz_doc:1"})

    def test_bbox_query_can_be_scoped_to_a_source(self):
        self.catalog.upsert_source(self.registry.get("eurovelo"))
        self.catalog.replace_features("nz_doc", [make(1)])
        self.catalog.replace_features("eurovelo", [make(9, source="eurovelo")])
        world = (-180.0, -90.0, 180.0, 90.0)
        self.assertEqual(self.catalog.feature_ids_in_bbox(world, source_id="eurovelo"), {"eurovelo:9"})

    def test_pull_runs_are_recorded_for_the_refresh_cycle(self):
        manifest = PullManifest.start("nz_doc", "nz_doc")
        manifest.notes = "features=12"
        manifest.finish()
        self.catalog.record_pull(manifest)
        row = self.catalog.last_pull("nz_doc")
        self.assertEqual(row["source_id"], "nz_doc")
        self.assertEqual(row["ok"], 1)

    def test_failed_pull_is_recorded_as_failed(self):
        manifest = PullManifest.start("nz_doc", "nz_doc")
        manifest.errors.append("FetchError: endpoint moved")
        self.catalog.record_pull(manifest.finish())
        self.assertEqual(self.catalog.last_pull("nz_doc")["ok"], 0)

    def test_re_normalizing_does_not_leave_stale_search_hits(self):
        # Contentless FTS5 was never being cleaned: a renamed or removed trail
        # kept matching under its old name after every re-run.
        self.catalog.replace_features("nz_doc", [make(1, name="Old Ghost Road")])
        self.catalog.replace_features("nz_doc", [make(1, name="Renamed Track")])
        self.assertEqual([r["id"] for r in self.catalog.search("Ghost")], [])
        self.assertEqual([r["id"] for r in self.catalog.search("Renamed")], ["nz_doc:1"])

    def test_search_finds_by_name(self):
        self.catalog.replace_features("nz_doc", [make(1, name="Milford Track"), make(2)])
        hits = self.catalog.search("Milford")
        self.assertEqual([row["id"] for row in hits], ["nz_doc:1"])

    def test_a_failed_replace_leaves_the_previous_rows_intact(self):
        # A duplicate id from an adapter used to leave the source three
        # features long: the delete and the first rows were committed on close.
        self.catalog.replace_features("nz_doc", [make(1), make(2), make(3)])

        def with_a_duplicate():
            yield make(10)
            yield make(11)
            yield make(10)  # primary key collision

        with self.assertRaises(Exception):
            self.catalog.replace_features("nz_doc", with_a_duplicate())
        self.assertEqual(self.catalog.totals().features, 3)
        self.assertEqual({r["id"] for r in self.catalog.search("Track")}, {"nz_doc:1", "nz_doc:2", "nz_doc:3"})

    def test_catalog_stores_no_geometry(self):
        self.catalog.replace_features("nz_doc", [make(1)])
        columns = {row[1] for row in self.catalog.db.execute("PRAGMA table_info(features)")}
        self.assertNotIn("geometry", columns)
        self.assertLessEqual({"west", "south", "east", "north", "length_km"}, columns)


if __name__ == "__main__":
    unittest.main()
