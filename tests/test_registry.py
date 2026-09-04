import datetime as dt
import unittest

import yaml

from trailsdb import registry as registry_module
from trailsdb.adapters import IMPLEMENTED, PLANNED
from trailsdb.registry import RegistryError
from trailsdb.schema import FEATURE_CLASSES, KINDS

MINIMAL = {
    "licenses": {
        "cc-by-4.0": {
            "name": "CC BY 4.0",
            "url": "https://example.invalid",
            "family": "attribution",
            "attribution_required": True,
            "share_alike": False,
        }
    },
    "sources": {
        "demo": {
            "name": "Demo",
            "countries": ["ES"],
            "adapter": "demo",
            "feature_class": "route",
            "estimated_km": 10,
            "license": "cc-by-4.0",
            "attribution": "(c) Demo",
        }
    },
}


def parse(**source_overrides):
    raw = yaml.safe_load(yaml.safe_dump(MINIMAL))
    raw["sources"]["demo"].update(source_overrides)
    return registry_module.parse(raw)


class TestShippedRegistry(unittest.TestCase):
    """The real sources.yaml is part of the product, so it is under test."""

    @classmethod
    def setUpClass(cls):
        cls.registry = registry_module.load()

    def test_loads_and_covers_the_planned_inventory(self):
        self.assertGreaterEqual(len(self.registry), 15)
        # The plan's worldwide inventory works out at roughly 810,000 km, with a
        # stated working range of 600k-1M.
        self.assertGreaterEqual(self.registry.total_estimated_km, 600_000)
        self.assertLessEqual(self.registry.total_estimated_km, 1_000_000)

    def test_every_source_is_internally_consistent(self):
        for source in self.registry:
            with self.subTest(source=source.id):
                self.assertIn(source.feature_class, FEATURE_CLASSES)
                self.assertIn(source.default_kind, KINDS)
                self.assertTrue(source.attribution.strip())
                self.assertIn(source.license.id, self.registry.licenses)
                self.assertTrue(source.countries)

    def test_every_source_has_a_health_check(self):
        # Source drift is a named risk; a source with no health check cannot be
        # caught drifting at the quarterly refresh.
        for source in self.registry:
            with self.subTest(source=source.id):
                self.assertIsNotNone(source.health_check, f"{source.id} has no health check")

    def test_every_adapter_is_implemented_or_scheduled(self):
        for adapter in self.registry.adapters:
            with self.subTest(adapter=adapter):
                self.assertTrue(
                    adapter in IMPLEMENTED or adapter in PLANNED,
                    f"adapter {adapter!r} is neither implemented nor in the execution order",
                )

    def test_no_orphan_adapter_entries(self):
        declared = set(IMPLEMENTED) | set(PLANNED)
        self.assertEqual(declared - set(self.registry.adapters), set())

    def test_share_alike_sources_are_flagged(self):
        # refuges.info is CC BY-SA and must stay in its own layer.
        share_alike = {s.id for s in self.registry.share_alike_sources()}
        self.assertIn("refuges_info", share_alike)

    def test_nothing_is_marked_verified_without_a_date(self):
        for source in self.registry:
            with self.subTest(source=source.id):
                self.assertEqual(source.verified, source.legal.verified_on is not None)


class TestValidation(unittest.TestCase):
    def test_unknown_license_is_rejected(self):
        with self.assertRaises(RegistryError):
            parse(license="made-up")

    def test_unknown_feature_class_is_rejected(self):
        with self.assertRaises(RegistryError):
            parse(feature_class="trail")

    def test_bad_country_code_is_rejected(self):
        with self.assertRaises(RegistryError):
            parse(countries=["Spain"])

    def test_empty_attribution_is_rejected(self):
        with self.assertRaises(RegistryError):
            parse(attribution="   ")

    def test_unknown_attribution_placeholder_is_rejected(self):
        with self.assertRaises(RegistryError):
            parse(attribution="(c) {whoever}")

    def test_dated_attribution_requires_a_dated_license(self):
        with self.assertRaises(RegistryError) as ctx:
            parse(attribution="(c) Demo, retrieved {retrieved_on}")
        self.assertIn("dated_attribution", str(ctx.exception))

    def test_missing_required_field_names_the_field(self):
        raw = yaml.safe_load(yaml.safe_dump(MINIMAL))
        del raw["sources"]["demo"]["attribution"]
        with self.assertRaises(RegistryError) as ctx:
            registry_module.parse(raw)
        self.assertIn("attribution", str(ctx.exception))


class TestAttributionResolution(unittest.TestCase):
    def test_plain_attribution_passes_through(self):
        self.assertEqual(parse().get("demo").attribution_for(), "(c) Demo")

    def test_dated_attribution_uses_the_retrieval_date(self):
        registry = registry_module.load()
        resolved = registry.get("eurovelo").attribution_for(retrieved_on=dt.date(2026, 8, 27))
        self.assertIn("2026-08-27", resolved)

    def test_dated_attribution_without_a_date_is_an_error(self):
        registry = registry_module.load()
        with self.assertRaises(RegistryError):
            registry.get("eurovelo").attribution_for()

    def test_per_instance_attribution_without_a_value_is_an_error(self):
        # An instance that declares no attribution must be skipped, not shipped
        # with a blank credit line.
        registry = registry_module.load()
        with self.assertRaises(RegistryError):
            registry.get("geotrek").attribution_for()

    def test_per_instance_attribution_uses_the_operator_wording(self):
        registry = registry_module.load()
        self.assertEqual(
            registry.get("geotrek").attribution_for(instance_attribution="(c) PN Ecrins"),
            "(c) PN Ecrins",
        )


class TestSelection(unittest.TestCase):
    def setUp(self):
        self.registry = registry_module.load()

    def test_empty_selection_is_everything(self):
        self.assertEqual(len(self.registry.select([])), len(self.registry))
        self.assertEqual(len(self.registry.select(None)), len(self.registry))

    def test_adapter_name_expands_to_its_sources(self):
        self.assertEqual(
            sorted(s.id for s in self.registry.select(["cnig"])),
            ["caminos_naturales", "cnig_camino", "cnig_camino_cid", "cnig_fedme"],
        )

    def test_source_ids_and_adapters_can_be_mixed_without_duplicates(self):
        selected = self.registry.select(["cnig", "cnig_fedme", "nz_doc"])
        ids = [s.id for s in selected]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertIn("nz_doc", ids)

    def test_unknown_token_is_an_error(self):
        with self.assertRaises(RegistryError):
            self.registry.select(["atlantis"])


if __name__ == "__main__":
    unittest.main()
