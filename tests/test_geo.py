import unittest

from trailsdb import geo


class TestLength(unittest.TestCase):
    def test_one_degree_of_latitude_is_about_111_km(self):
        line = {"type": "LineString", "coordinates": [[0.0, 0.0], [0.0, 1.0]]}
        self.assertAlmostEqual(geo.length_km(line), 111.19, places=1)

    def test_longitude_shrinks_towards_the_poles(self):
        equator = {"type": "LineString", "coordinates": [[0.0, 0.0], [1.0, 0.0]]}
        far_north = {"type": "LineString", "coordinates": [[0.0, 60.0], [1.0, 60.0]]}
        self.assertAlmostEqual(geo.length_km(far_north) / geo.length_km(equator), 0.5, places=2)

    def test_multilinestring_sums_its_parts(self):
        single = {"type": "LineString", "coordinates": [[0.0, 0.0], [0.0, 1.0]]}
        double = {
            "type": "MultiLineString",
            "coordinates": [[[0.0, 0.0], [0.0, 1.0]], [[10.0, 0.0], [10.0, 1.0]]],
        }
        self.assertAlmostEqual(geo.length_km(double), 2 * geo.length_km(single), places=3)

    def test_point_count(self):
        double = {
            "type": "MultiLineString",
            "coordinates": [[[0.0, 0.0], [0.0, 1.0]], [[1.0, 0.0], [1.0, 1.0], [1.0, 2.0]]],
        }
        self.assertEqual(geo.point_count(double), 5)


class TestBBox(unittest.TestCase):
    def test_bbox_of_a_line(self):
        line = {"type": "LineString", "coordinates": [[-8.5, 42.1], [-6.7, 43.8]]}
        self.assertEqual(geo.bbox(line), (-8.5, 42.1, -6.7, 43.8))

    def test_union(self):
        self.assertEqual(geo.bbox_union([(0, 0, 1, 1), (2, 2, 3, 3)]), (0, 0, 3, 3))
        self.assertIsNone(geo.bbox_union([]))

    def test_intersects(self):
        galicia = (-9.35, 41.8, -6.7, 43.8)
        self.assertTrue(geo.bbox_intersects(galicia, (-8.0, 42.0, -7.9, 42.1)))
        self.assertTrue(geo.bbox_intersects(galicia, (-6.71, 43.79, -5.0, 44.5)))  # touching
        self.assertFalse(geo.bbox_intersects(galicia, (0.0, 42.0, 1.0, 43.0)))

    def test_pad_grows_both_axes_and_clamps_at_the_poles(self):
        padded = geo.bbox_pad((0.0, 0.0, 1.0, 1.0), 111.32)
        self.assertAlmostEqual(padded[1], -1.0, places=2)
        self.assertAlmostEqual(padded[3], 2.0, places=2)
        self.assertEqual(geo.bbox_pad((0.0, 89.9, 1.0, 90.0), 500.0)[3], 90.0)


class TestCoordinatePrecision(unittest.TestCase):
    def test_rounds_to_about_eleven_centimetres(self):
        line = {"type": "LineString", "coordinates": [[-110.98106526395314, 34.33856768862416]]}
        rounded = geo.round_geometry(line)
        self.assertEqual(rounded["coordinates"], [[-110.981065, 34.338568]])

    def test_handles_multilinestring(self):
        multi = {
            "type": "MultiLineString",
            "coordinates": [[[1.123456789, 2.987654321]], [[3.111111111, 4.222222222]]],
        }
        self.assertEqual(
            geo.round_geometry(multi)["coordinates"],
            [[[1.123457, 2.987654]], [[3.111111, 4.222222]]],
        )

    def test_drops_the_third_ordinate(self):
        line = {"type": "LineString", "coordinates": [[1.0, 2.0, 300.0], [3.0, 4.0, 310.0]]}
        self.assertEqual(geo.round_geometry(line)["coordinates"], [[1.0, 2.0], [3.0, 4.0]])

    def test_the_error_it_introduces_is_below_gps_accuracy(self):
        # Six decimal places is ~11 cm; consumer GPS is metres.
        original = [-110.98106526395314, 34.33856768862416]
        rounded = geo.round_geometry(
            {"type": "LineString", "coordinates": [original]}
        )["coordinates"][0]
        self.assertLess(geo.haversine_km(original, rounded) * 1000, 0.2)

    def test_non_line_geometry_passes_through_untouched(self):
        point = {"type": "Point", "coordinates": [1.123456789, 2.0]}
        self.assertEqual(geo.round_geometry(point), point)


class TestSimplify(unittest.TestCase):
    def test_drops_points_within_tolerance(self):
        line = [[0.0, 0.0], [0.5, 0.000001], [1.0, 0.0]]
        self.assertEqual(geo.simplify(line, 50.0), [[0.0, 0.0], [1.0, 0.0]])

    def test_keeps_points_that_matter(self):
        line = [[0.0, 0.0], [0.5, 0.5], [1.0, 0.0]]
        self.assertEqual(len(geo.simplify(line, 50.0)), 3)

    def test_short_lines_and_zero_tolerance_are_untouched(self):
        self.assertEqual(geo.simplify([[0.0, 0.0], [1.0, 1.0]], 500.0), [[0.0, 0.0], [1.0, 1.0]])
        line = [[0.0, 0.0], [0.5, 0.0], [1.0, 0.0]]
        self.assertEqual(geo.simplify(line, 0.0), line)


if __name__ == "__main__":
    unittest.main()
