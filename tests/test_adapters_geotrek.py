"""Geotrek: one adapter across many operators, tested on Gavarnie's real responses."""

import dataclasses
import json
import tempfile
import unittest
from pathlib import Path

from tests.support import FakeResponse, FakeTransport
from trailsdb import registry as registry_module
from trailsdb.adapters import AdapterContext, build
from trailsdb.adapters import geotrek as geotrek_module
from trailsdb.adapters.geotrek import Instance
from trailsdb.config import Paths
from trailsdb.fetch import PoliteSession

FIXTURES = Path(__file__).parent / "fixtures"


def session_for(transport):
    return PoliteSession(
        transport=transport, rate_limit_s=0, sleeper=lambda _s: None, today=lambda: "2026-09-04"
    )


def gavarnie_transport():
    """Serve the Gavarnie fixtures for any instance API base."""

    def handler(_method, url, _kwargs):
        for vocab in ("source", "trek_practice", "trek_difficulty", "trek_network"):
            if f"/{vocab}/" in url:
                return FakeResponse(200, (FIXTURES / f"geotrek_{vocab}.json").read_bytes())
        if "/trek/" in url:
            return FakeResponse(200, (FIXTURES / "geotrek_trek_page.geojson").read_bytes())
        return FakeResponse(404)

    return FakeTransport(handler)


class GeotrekCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.paths = Paths.resolve(Path(self.tmp.name)).ensure()
        self.registry = registry_module.load()

    def adapter(self, transport, instances):
        adapter = build(
            AdapterContext(
                source=self.registry.get("geotrek"), paths=self.paths, session=session_for(transport)
            )
        )
        adapter.instances = [i for i in instances if i.api and not i.closed]
        return adapter


GAVARNIE = Instance(
    key="gavarnie",
    name="Communauté de communes Pyrénées Vallées des Gaves",
    portal="https://rando.valleesdegavarnie.com/",
    api="https://geotrek65admin.openig.org/api/v2",
    licence=None,
    attribution="Communauté de communes Pyrénées Vallées des Gaves",
    verified_on=None,
)


class TestInstancesFile(unittest.TestCase):
    def test_every_instance_has_an_api_and_a_verified_one_carries_its_verdict(self):
        instances = geotrek_module.load_instances()
        self.assertGreaterEqual(len(instances), 10)
        for instance in instances:
            with self.subTest(instance=instance.key):
                self.assertTrue(instance.api.startswith("https://"))
                self.assertTrue(instance.api.endswith("/api/v2"))
                # The API carries no licence; nothing ships until an operator's
                # own text has been read. A verified instance therefore names a
                # licence (or "closed") and quotes the text that decided it, so
                # a hopeful edit cannot quietly verify one.
                if instance.verified_on is None:
                    self.assertIsNone(instance.licence)
                else:
                    self.assertTrue(instance.licence)
                    self.assertTrue(instance.terms and instance.verified_on in instance.terms)

    def test_no_operator_read_so_far_grants_reuse(self):
        # Three reserve all rights in writing, seven say nothing about the
        # data; every one of them is closed until an open publication by the
        # same operator is read and recorded.
        instances = geotrek_module.load_instances()
        self.assertTrue(all(i.closed for i in instances if i.verified_on))
        self.assertGreaterEqual(sum(1 for i in instances if i.closed), 10)

    def test_pyrenees_is_covered(self):
        keys = {i.key for i in geotrek_module.load_instances()}
        self.assertTrue(any("pyrenees" in k for k in keys))
        self.assertTrue(any("vallees-des-gaves" in k for k in keys))


class TestGeotrekAdapter(GeotrekCase):
    def test_pulls_vocabularies_and_pages_per_instance(self):
        transport = gavarnie_transport()
        manifest = self.adapter(transport, [GAVARNIE]).pull()
        self.assertTrue(manifest.ok, manifest.errors)
        self.assertEqual(len(manifest.files), 5)  # four vocabularies + one page
        self.assertTrue(any("published=true" in u and "format=geojson" in u for u in transport.urls))

    def test_maps_vocabulary_by_label_not_id(self):
        # Practice 1 is "VTT" on Gavarnie and "Cyclo" elsewhere; ids are local.
        adapter = self.adapter(gavarnie_transport(), [GAVARNIE])
        features = {f.name: f for f in adapter.normalize(adapter.pull())}
        self.assertEqual(features["A la naissance de la Gesse"].kind, "hiking")  # "A Pied"
        self.assertEqual(features["A1 Les Puyolles"].kind, "mtb")  # "VTT"
        self.assertEqual(features["A la naissance de la Gesse"].extras["practice"], "A Pied")
        self.assertEqual(features["A la naissance de la Gesse"].ref, "PR")
        self.assertEqual(features["A la naissance de la Gesse"].official_status, "geotrek_pr")

    def test_geometry_is_two_dimensional_and_ids_rest_on_uuids(self):
        adapter = self.adapter(gavarnie_transport(), [GAVARNIE])
        for f in adapter.normalize(adapter.pull()):
            f.validate()
            self.assertEqual(len(f.geometry["coordinates"][0]), 2)
            self.assertRegex(f.id, r"^geotrek:gavarnie-[0-9a-f-]{36}$")
            self.assertEqual(f.country, "FR")
            self.assertEqual(f.admin, GAVARNIE.name)

    def test_attribution_is_instance_plus_declared_sources(self):
        adapter = self.adapter(gavarnie_transport(), [GAVARNIE])
        for f in adapter.normalize(adapter.pull()):
            self.assertTrue(f.attribution.startswith(GAVARNIE.attribution))
            # An unverified instance carries the registry's per-instance licence
            # placeholder, which the export gate refuses.
            self.assertEqual(f.license, "per-instance")

    def test_an_operator_listed_as_its_own_source_is_not_credited_twice(self):
        # Gavarnie's fixture treks declare no sources; give the instance the
        # name of one of the fixture's sources and check it is not doubled.
        self_named = dataclasses.replace(GAVARNIE, attribution="Guide des balades de Cauterets")
        page = json.loads((FIXTURES / "geotrek_trek_page.geojson").read_text())
        page["features"][0]["properties"]["source"] = [7]  # "Guide des balades de Cauterets"
        good = gavarnie_transport()

        def handler(method, url, kwargs):
            if "/trek/" in url:
                return FakeResponse(200, json.dumps(page).encode())
            return good.handler(method, url, kwargs)

        adapter = self.adapter(FakeTransport(handler), [self_named])
        first = next(f for f in adapter.normalize(adapter.pull()) if f.name == "A la naissance de la Gesse")
        self.assertEqual(first.attribution, "Guide des balades de Cauterets")

    def test_a_verified_instance_stamps_its_own_licence(self):
        verified = dataclasses.replace(GAVARNIE, licence="odbl-1.0", verified_on="2026-09-04")
        adapter = self.adapter(gavarnie_transport(), [verified])
        self.assertTrue(all(f.license == "odbl-1.0" for f in adapter.normalize(adapter.pull())))

    def test_a_closed_instance_is_skipped_entirely(self):
        closed = dataclasses.replace(GAVARNIE, key="closed-one", licence="closed")
        transport = gavarnie_transport()
        adapter = self.adapter(transport, [closed, GAVARNIE])
        adapter.pull()
        self.assertFalse(any("closed-one" in Path(f.path).parts for f in adapter.load_manifest().files))

    def test_one_operator_down_does_not_lose_the_others(self):
        good = gavarnie_transport()

        def handler(method, url, kwargs):
            if "broken.example" in url:
                return FakeResponse(503)
            return good.handler(method, url, kwargs)

        broken = dataclasses.replace(GAVARNIE, key="broken", api="https://broken.example/api/v2")
        session = session_for(FakeTransport(handler))
        session.retries = 0
        adapter = build(AdapterContext(source=self.registry.get("geotrek"), paths=self.paths, session=session))
        adapter.instances = [broken, GAVARNIE]
        manifest = adapter.pull()
        self.assertTrue(manifest.ok, manifest.errors)
        self.assertTrue(any("broken" in w for w in manifest.warnings))
        self.assertEqual(len(list(adapter.normalize(manifest))), 2)

    def test_kind_mapping_covers_the_french_vocabulary(self):
        k = geotrek_module._kind_for
        self.assertEqual(k("A pied"), "hiking")
        self.assertEqual(k("Itinérance à pied"), "hiking")
        self.assertEqual(k("VTTAE"), "mtb")
        self.assertEqual(k("Gravel"), "cycling")
        self.assertEqual(k("Cheval"), "horse")
        self.assertEqual(k("Raquettes"), "ski")
        self.assertEqual(k(""), "hiking")


if __name__ == "__main__":
    unittest.main()
