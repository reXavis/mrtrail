import tempfile
import unittest
from pathlib import Path

from trailsdb import geojsonl
from trailsdb.schema import Feature, ValidationError


def make(index: int) -> Feature:
    return Feature(
        id=f"nz_doc:{index}",
        source="nz_doc",
        license="cc-by-4.0",
        attribution="Sourced from DOC",
        feature_class="route",
        geometry={"type": "LineString", "coordinates": [[170.0, -43.0], [170.1, -43.1]]},
        name=f"Track {index}",
    )


class TestRoundTrip(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_write_then_read(self):
        path = self.dir / "out.geojsonl"
        self.assertEqual(geojsonl.write(path, [make(1), make(2)]), 2)
        self.assertEqual([f.id for f in geojsonl.read(path)], ["nz_doc:1", "nz_doc:2"])
        self.assertEqual(geojsonl.count(path), 2)

    def test_gzip_is_chosen_by_suffix(self):
        path = self.dir / "out.geojsonl.gz"
        geojsonl.write(path, [make(i) for i in range(5)])
        self.assertEqual(path.read_bytes()[:2], b"\x1f\x8b")
        self.assertEqual(geojsonl.count(path), 5)

    def test_invalid_features_are_rejected_before_the_file_lands(self):
        path = self.dir / "out.geojsonl"
        bad = make(1)
        bad.geometry = {"type": "Point", "coordinates": [1.0, 2.0]}
        with self.assertRaises(ValidationError):
            geojsonl.write(path, [bad])
        # The tmp-then-rename discipline means no half-written file survives.
        self.assertFalse(path.exists())

    def test_malformed_line_names_the_file_and_line(self):
        path = self.dir / "bad.geojsonl"
        path.write_text('{"broken"\n', encoding="utf-8")
        with self.assertRaises(ValueError) as ctx:
            list(geojsonl.read(path))
        self.assertIn("bad.geojsonl:1", str(ctx.exception))

    def test_written_coordinates_are_rounded(self):
        # Full IEEE doubles are ~39 bytes a position for data good to a few
        # metres; geometry is ~87% of the master database, so this is its
        # largest single size lever.
        path = self.dir / "out.geojsonl"
        feature = make(1)
        feature.geometry = {
            "type": "LineString",
            "coordinates": [[-110.98106526395314, 34.33856768862416], [-110.9811, 34.3386]],
        }
        geojsonl.write(path, [feature])
        self.assertIn("-110.981065", path.read_text())
        self.assertNotIn("-110.98106526395314", path.read_text())

    def test_count_of_missing_file_is_zero(self):
        self.assertEqual(geojsonl.count(self.dir / "nope.geojsonl"), 0)


class TestWriter(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_many_sources_into_one_file(self):
        path = self.dir / "official.geojsonl"
        with geojsonl.Writer(path) as writer:
            for i in range(3):
                writer.add(make(i))
        self.assertEqual(geojsonl.count(path), 3)

    def test_abort_leaves_nothing_behind(self):
        path = self.dir / "official.geojsonl"
        writer = geojsonl.Writer(path)
        writer.add(make(1))
        writer.abort()
        self.assertFalse(path.exists())
        self.assertFalse(path.with_name(path.name + ".tmp").exists())

    def test_exception_inside_context_aborts(self):
        path = self.dir / "official.geojsonl"
        with self.assertRaises(RuntimeError):
            with geojsonl.Writer(path) as writer:
                writer.add(make(1))
                raise RuntimeError("boom")
        self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
