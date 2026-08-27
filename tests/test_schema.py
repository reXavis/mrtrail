import unittest

from trailsdb.schema import Feature, ValidationError, validate_geometry

GEOM = {"type": "LineString", "coordinates": [[-8.5, 42.1], [-8.4, 42.2]]}


def make(**overrides) -> Feature:
    fields = dict(
        id="cnig_fedme:GR11",
        source="cnig_fedme",
        license="cc-by-4.0",
        attribution="(c) IGN",
        feature_class="route",
        geometry=GEOM,
    )
    fields.update(overrides)
    return Feature(**fields)


class TestFeatureValidation(unittest.TestCase):
    def test_valid_feature_passes(self):
        make().validate()

    def test_id_must_be_prefixed_with_source(self):
        with self.assertRaises(ValidationError):
            make(id="other:GR11").validate()

    def test_license_and_attribution_are_required(self):
        with self.assertRaises(ValidationError):
            make(license="").validate()
        with self.assertRaises(ValidationError):
            make(attribution="").validate()

    def test_rejects_unknown_feature_class_and_kind(self):
        with self.assertRaises(ValidationError):
            make(feature_class="trail").validate()
        with self.assertRaises(ValidationError):
            make(kind="skateboard").validate()

    def test_rejects_non_iso_country(self):
        with self.assertRaises(ValidationError):
            make(country="esp").validate()

    def test_rejects_projected_coordinates(self):
        # A source that forgot to reproject shows up as huge easting/northing
        # values, which is the failure this catches.
        projected = {"type": "LineString", "coordinates": [[537_000.0, 4_720_000.0], [1.0, 2.0]]}
        with self.assertRaises(ValidationError):
            make(geometry=projected).validate()

    def test_rejects_single_point_lines(self):
        with self.assertRaises(ValidationError):
            validate_geometry({"type": "LineString", "coordinates": [[1.0, 2.0]]})

    def test_rejects_non_line_geometry(self):
        with self.assertRaises(ValidationError):
            validate_geometry({"type": "Point", "coordinates": [1.0, 2.0]})


class TestSerialization(unittest.TestCase):
    def test_round_trip(self):
        original = make(ref="GR 11", name="Senda", stage_no=3, extras={"x": 1})
        self.assertEqual(Feature.from_geojson(original.to_geojson()), original)

    def test_none_fields_are_omitted(self):
        props = make().to_geojson()["properties"]
        self.assertNotIn("ref", props)
        self.assertNotIn("name", props)

    def test_tile_properties_exclude_attribution_extras_and_urls(self):
        feature = make(
            ref="GR 11",
            name="Senda",
            source_url="https://example.invalid",
            extras={"big": "payload"},
        )
        tile = feature.tile_properties()
        self.assertIn("ref", tile)
        self.assertIn("license", tile)
        # Attribution is per-source and comes from the registry at render time;
        # extras and source URLs never earn their tile bytes.
        self.assertNotIn("attribution", tile)
        self.assertNotIn("extras", tile)
        self.assertNotIn("source_url", tile)

    def test_no_elevation_fields_in_schema(self):
        # Ascent, descent and profiles are computed at pack-bake time against the
        # pack's own DEM. Storing them here would bloat the master database and
        # bind it to one elevation model.
        for banned in ("ascent", "descent", "profile", "elevation"):
            self.assertNotIn(banned, Feature.__slots__)


if __name__ == "__main__":
    unittest.main()
