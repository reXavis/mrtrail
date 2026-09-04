"""Adapter tests, driven by fixtures captured from the real services.

Every fixture in ``tests/fixtures/`` is a trimmed copy of a genuine response --
a CNIG listing page, a DOC FeatureServer page, a EuroVelo GPX -- so these tests
fail when a publisher changes shape, which is the only way they are worth having.
"""

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
from trailsdb.adapters import nz_doc as nz_doc_module
from trailsdb.adapters.base import AdapterNotImplemented
from trailsdb.config import Paths
from trailsdb.fetch import PoliteSession
from trailsdb.formats.archive import UnsupportedFormat

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def transport_calls(adapter):
    return adapter.session.transport.calls


def session_for(transport) -> PoliteSession:
    return PoliteSession(
        transport=transport, rate_limit_s=0, sleeper=lambda _s: None, today=lambda: "2026-08-27"
    )


class AdapterCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.paths = Paths.resolve(Path(self.tmp.name)).ensure()
        self.registry = registry_module.load()

    def adapter(self, source_id: str, transport):
        return build(
            AdapterContext(
                source=self.registry.get(source_id),
                paths=self.paths,
                session=session_for(transport),
            )
        )


# ------------------------------------------------------------------- CNIG ----


class TestCnigListingParser(unittest.TestCase):
    """Parsed against a real Centro de Descargas page, not an invented one."""

    @classmethod
    def setUpClass(cls):
        cls.html = fixture("cnig_listing_page.html").decode("utf-8")

    def test_reads_the_total_file_count(self):
        self.assertIsInstance(cnig_module.parse_total(self.html), int)

    def test_reads_id_name_format_and_date_from_each_row(self):
        rows = cnig_module.parse_rows(self.html)
        self.assertTrue(rows, "no rows parsed from the real listing fixture")
        for row in rows:
            with self.subTest(file_id=row.file_id):
                self.assertTrue(row.file_id.isdigit())
                self.assertTrue(row.name, "row parsed without a name")
                self.assertIn(row.fmt.upper(), ("GPX", "KML", "ZIP", "SHP"))
                self.assertRegex(row.date, r"^\d{2}/\d{2}/\d{4}$")

    def test_the_name_is_the_route_not_the_format(self):
        # The listing marks cells by class, and an earlier positional parser
        # happily returned "GPX" as every route's name.
        for row in cnig_module.parse_rows(self.html):
            self.assertNotIn(row.name.upper(), ("GPX", "KML"))

    def test_both_formats_appear_so_gpx_filtering_is_meaningful(self):
        formats = {r.fmt.upper() for r in cnig_module.parse_rows(self.html)}
        self.assertIn("GPX", formats)

    def test_a_page_with_no_rows_parses_to_nothing_rather_than_raising(self):
        self.assertEqual(cnig_module.parse_rows("<html><body>nada</body></html>"), [])


class TestCnigParsers(unittest.TestCase):
    def test_extracts_waymarking_refs(self):
        self.assertEqual(cnig_module.extract_ref("GR 11 Senda Pirenaica"), "GR 11")
        self.assertEqual(cnig_module.extract_ref("PR-G 100"), "PR-G 100")
        self.assertEqual(cnig_module.extract_ref("sl_g_12.gpx"), "SL-G 12")
        self.assertIsNone(cnig_module.extract_ref("Camino Frances"))

    def test_extracts_stage_numbers(self):
        self.assertEqual(cnig_module.extract_stage("Etapa 12: Burgos - Hontanas"), 12)
        self.assertIsNone(cnig_module.extract_stage("Camino Frances"))

    def test_parses_the_real_camino_naming_scheme(self):
        parsed = cnig_module.parse_camino_name(
            "Caminos del Norte - Camino Primitivo - ES05a-03b-grado-salas"
        )
        self.assertEqual(parsed["group"], "Caminos del Norte - Camino Primitivo")
        self.assertEqual(parsed["code"], "ES05a")
        self.assertEqual(parsed["stage"], 3)
        self.assertEqual(parsed["variant"], "b")
        self.assertEqual(parsed["section"], "grado-salas")

    def test_the_route_code_carries_the_country(self):
        # 200 of the published stages are in France and 123 in Portugal, so
        # stamping the whole series "ES" would be wrong for a third of it.
        self.assertEqual(
            cnig_module.parse_camino_name("Caminos en Francia - X - FR03a-12-le-puy")["country"],
            "FR",
        )
        self.assertEqual(
            cnig_module.parse_camino_name("Caminos Portugueses - Y - PT01a-03-porto")["country"],
            "PT",
        )

    def test_a_name_that_does_not_fit_falls_back_rather_than_mis_grouping(self):
        self.assertIsNone(cnig_module.parse_camino_name("GR 123. Etapa 10. Ondarroa"))
        self.assertIsNone(cnig_module.parse_camino_name(""))

    def test_variant_name_is_everything_before_the_stage(self):
        self.assertEqual(
            cnig_module.variant_name("Camino Frances. Etapa 12: Burgos", "x"), "Camino Frances"
        )

    def test_every_registered_series_is_known_to_the_adapter(self):
        registry = registry_module.load()
        for source in registry.by_adapter("cnig"):
            with self.subTest(source=source.id):
                self.assertIn(source.series, cnig_module.SERIES)


class TestCaminoDelCidNames(unittest.TestCase):
    def test_mode_section_stage_and_title(self):
        parsed = cnig_module.parse_cid_name("BTT0102-burgos-santo-domingo-de-silos")
        self.assertEqual((parsed["mode"], parsed["kind"], parsed["section"], parsed["stage"]), ("BTT", "mtb", 1, 2))
        self.assertEqual(parsed["title"], "Burgos santo domingo de silos")
        self.assertEqual(cnig_module.parse_cid_name("SEN0702-gallocanta-daroca")["kind"], "hiking")
        self.assertEqual(cnig_module.parse_cid_name("CIC0401-x")["kind"], "cycling")

    def test_the_motor_variant_and_the_whole_route_file_are_not_stages(self):
        self.assertIsNone(cnig_module.parse_cid_name("MOT0101-vivar-del-cid-burgos")["kind"])
        self.assertIsNone(cnig_module.parse_cid_name("Camino del Cid"))
        self.assertIsNone(cnig_module.parse_cid_name(None))


class TestCaminosNaturalesNames(unittest.TestCase):
    def test_route_stage_variant_and_title(self):
        parsed = cnig_module.parse_cnt_name("CNT102-0021-senda-de-souta-da-vila-ramal-petroglifos")
        self.assertEqual(parsed["route"], "CNT102")
        self.assertEqual(parsed["stage"], 2)
        self.assertEqual(parsed["variant"], 1)
        self.assertEqual(parsed["title"], "Senda de souta da vila ramal petroglifos")

    def test_the_four_digits_are_stage_times_ten_plus_variant(self):
        self.assertEqual(cnig_module.parse_cnt_name("CNT614-0400-etapa-40-circuito-villanueva-del-fresno")["stage"], 40)
        self.assertEqual(cnig_module.parse_cnt_name("CNT707_0050_etapa_5_yaiza_playa_blanca")["stage"], 5)

    def test_anything_else_is_none(self):
        self.assertIsNone(cnig_module.parse_cnt_name("CaminosNaturales"))
        self.assertIsNone(cnig_module.parse_cnt_name("GR 11. Etapa 3"))
        self.assertIsNone(cnig_module.parse_cnt_name(None))


class TestCnigAdapter(AdapterCase):
    def seed_index(self, source_id, entries):
        raw = self.paths.raw_dir(source_id)
        raw.mkdir(parents=True, exist_ok=True)
        (raw / "files.json").write_text(json.dumps({"files": entries}), encoding="utf-8")

    def test_download_posts_the_file_id_to_descarga_dir(self):
        self.seed_index(
            "cnig_fedme",
            [
                {"id": "111", "name": "GR 11 Senda", "format": "GPX", "date": "31/03/2026"},
                {"id": "112", "name": "GR 11 Senda", "format": "KML", "date": "31/03/2026"},
            ],
        )
        transport = FakeTransport(default=FakeResponse(200, gpx_bytes("GR 11", line((-8.5, 42.1)))))
        manifest = self.adapter("cnig_fedme", transport).pull()

        self.assertTrue(manifest.ok, manifest.errors)
        method, url, kwargs = transport.calls[0]
        self.assertEqual(method, "POST")
        self.assertIn("descargaDir", url)
        self.assertEqual(kwargs["data"], {"secDescDirLA": "111"})

    def _s3_transport(self, gpx: bytes, *, direct_answer: FakeResponse | None = None):
        page = (
            b'<html><body><input type="hidden" id="urlPregsigned" '
            b'value="https://bucket.example/rutas/CNT101_0010.gpx?X-Amz-Signature=abc&amp;X-Amz-Expires=7199">'
            b"</body></html>"
        )

        def handler(method, url, kwargs):
            if url.endswith("descargaDirS3"):
                return FakeResponse(200, page, {"Content-Type": "text/html"})
            if "bucket.example" in url:
                return FakeResponse(200, gpx, {"Content-Type": "binary/octet-stream"})
            if url.endswith("descargaDir"):
                return direct_answer or FakeResponse(200, gpx)
            return FakeResponse(200, b"<html>home</html>", {"Content-Type": "text/html"})

        return FakeTransport(handler)

    def test_an_s3_hosted_series_follows_the_pre_signed_url(self):
        self.seed_index(
            "caminos_naturales",
            [{"id": "11602481", "name": "CNT101-0010-ruta-del-rio-catoira", "format": "GPX"}],
        )
        transport = self._s3_transport(gpx_bytes("CNT101_0010_ruta_del_rio_catoira", line((-8.7, 42.66))))
        manifest = self.adapter("caminos_naturales", transport).pull()

        self.assertTrue(manifest.ok, manifest.errors)
        methods_urls = [(m, u) for m, u, _ in transport.calls]
        self.assertEqual(methods_urls[0][0], "POST")
        self.assertTrue(methods_urls[0][1].endswith("descargaDirS3"))
        self.assertEqual(transport.calls[0][2]["data"], {"secuencial": "11602481"})
        # The HTML-escaped ampersand in the page is unescaped before the GET.
        self.assertEqual(methods_urls[1], ("GET", "https://bucket.example/rutas/CNT101_0010.gpx?X-Amz-Signature=abc&X-Amz-Expires=7199"))
        path = Path(manifest.files[0].path)
        self.assertTrue(path.read_bytes().startswith(b"<?xml"))

    def test_a_direct_series_that_answers_with_a_page_is_retried_via_s3(self):
        self.seed_index("cnig_fedme", [{"id": "111", "name": "GR 11 Senda", "format": "GPX"}])
        bounce = FakeResponse(200, b"\r\n<!doctype html>\n<html lang=\"ES\"><body>centro</body></html>", {"Content-Type": "text/html"})
        transport = self._s3_transport(gpx_bytes("GR 11", line((-8.5, 42.1))), direct_answer=bounce)
        manifest = self.adapter("cnig_fedme", transport).pull()

        self.assertTrue(manifest.ok, manifest.errors)
        urls = [u.rsplit("/", 1)[-1].split("?")[0] for _, u, _ in transport.calls]
        self.assertEqual(urls, ["descargaDir", "descargaDirS3", "CNT101_0010.gpx"])
        self.assertTrue(Path(manifest.files[0].path).read_bytes().startswith(b"<?xml"))

    def test_kml_duplicates_are_skipped(self):
        self.seed_index(
            "cnig_fedme",
            [
                {"id": "1", "name": "A", "format": "GPX"},
                {"id": "2", "name": "A", "format": "KML"},
                {"id": "3", "name": "B", "format": "GPX"},
                {"id": "4", "name": "B", "format": "KML"},
            ],
        )
        transport = FakeTransport(default=FakeResponse(200, gpx_bytes("A", line((-8.5, 42.1)))))
        manifest = self.adapter("cnig_fedme", transport).pull()
        self.assertEqual(len(manifest.files), 2)
        self.assertIn("KML skipped", manifest.notes)

    def test_pull_fails_loudly_when_discovery_finds_nothing(self):
        transport = FakeTransport(default=FakeResponse(200, b"<html>no results</html>"))
        manifest = self.adapter("cnig_fedme", transport).pull()
        self.assertFalse(manifest.ok)
        self.assertIn("discovery returned no files", manifest.errors[0])

    def test_live_discovery_walks_pages_and_caches_the_result(self):
        page = fixture("cnig_listing_page.html")
        transport = FakeTransport(default=FakeResponse(200, page, {"Content-Type": "text/html"}))
        adapter = self.adapter("cnig_camino_cid", transport)

        entries = adapter.discover_live(max_pages=3)
        self.assertTrue(entries)
        # The listing serves the same page forever past the end; discovery has to
        # stop when a page adds nothing rather than looping to max_pages.
        cached = json.loads((self.paths.raw_dir("cnig_camino_cid") / "files.json").read_text())
        self.assertEqual(len(cached["files"]), len(entries))
        self.assertEqual(cached["code"], "CACID")

    def test_camino_del_cid_stages_group_per_travel_mode_and_skip_the_driving_one(self):
        self.seed_index(
            "cnig_camino_cid",
            [
                {"id": "10901602", "name": "BTT0101-vivar-del-cid-burgos", "format": "GPX"},
                {"id": "10901700", "name": "MOT0101-vivar-del-cid-burgos", "format": "GPX"},
                {"id": "10901525", "name": "Camino del Cid", "format": "GPX"},
            ],
        )
        gpx = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1"><trk>'
            "<name>Vivar del Cid - Burgos</name><cmt>13.12 km</cmt><desc>Camino del Cid BTT-MTB</desc><trkseg>"
            '<trkpt lat="42.4" lon="-3.6"><ele>900</ele></trkpt><trkpt lat="42.35" lon="-3.7"><ele>860</ele></trkpt>'
            "</trkseg></trk></gpx>"
        ).encode("utf-8")
        adapter = self.adapter("cnig_camino_cid", self._s3_transport(gpx))
        manifest = adapter.pull()
        self.assertTrue(manifest.ok, manifest.errors)
        # An S3-hosted series never tries the direct endpoint.
        self.assertFalse(any(u.endswith("descargaDir") for _, u, _ in transport_calls(adapter)))
        features = list(adapter.normalize(manifest))

        self.assertEqual([f.id for f in features], ["cnig_camino_cid:10901602"])
        f = features[0]
        f.validate()
        self.assertEqual(f.name, "Vivar del Cid - Burgos")
        self.assertEqual(f.kind, "mtb")
        self.assertEqual(f.parent_id, "cnig_camino_cid:cid-btt")
        self.assertEqual(f.parent_name, "Camino del Cid BTT-MTB")
        self.assertEqual(f.stage_no, 101)
        self.assertEqual(f.extras["section_no"], 1)
        self.assertEqual(f.official_status, "camino_del_cid")
        self.assertEqual(f.attribution, "Obra derivada de RCE_CDC 2018-2020 CC-BY 4.0 Camino del CID")

    def test_caminos_naturales_stages_group_under_the_route_named_in_the_gpx(self):
        self.seed_index(
            "caminos_naturales",
            [{"id": "11602481", "name": "CNT101-0010-ruta-del-rio-catoira", "format": "GPX"}],
        )
        gpx = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1"><trk>'
            "<name>CNT101_0010_ruta_del_rio_catoira</name><cmt>6.1 km</cmt>"
            "<desc>Camino Natural de las rutas ecológicas del río Catoira</desc>"
            "<src>Ministerio de Agricultura, Pesca y Alimentación</src><trkseg>"
            '<trkpt lat="42.6627" lon="-8.6989"><ele>61</ele></trkpt>'
            '<trkpt lat="42.6696" lon="-8.7216"><ele>50</ele></trkpt>'
            "</trkseg></trk></gpx>"
        ).encode("utf-8")
        adapter = self.adapter("caminos_naturales", self._s3_transport(gpx))
        manifest = adapter.pull()
        features = list(adapter.normalize(manifest))

        self.assertEqual(len(features), 1)
        feature = features[0]
        self.assertEqual(feature.id, "caminos_naturales:11602481")
        self.assertEqual(feature.name, "Ruta del rio catoira")
        self.assertEqual(feature.parent_id, "caminos_naturales:cnt101")
        self.assertEqual(feature.parent_name, "Camino Natural de las rutas ecológicas del río Catoira")
        self.assertEqual(feature.stage_no, 1)
        self.assertEqual(feature.official_status, "camino_natural")
        self.assertEqual(feature.kind, "mixed")
        self.assertEqual(feature.extras["route_code"], "CNT101")
        self.assertEqual(feature.extras["stage_code"], "010")
        self.assertNotIn("variant", feature.extras)
        self.assertEqual(feature.attribution, "Obra derivada de CNT 2024 CC-BY 4.0 MAPA")

    def test_normalize_stamps_provenance_and_uses_the_listed_name(self):
        self.seed_index(
            "cnig_fedme",
            [{"id": "12633198", "name": "GR 123. Etapa 10. Ondarroa-Ispaster",
              "format": "GPX", "date": "31/03/2026"}],
        )
        transport = FakeTransport(default=FakeResponse(200, gpx_bytes("trk", line((-2.4, 43.3)))))
        adapter = self.adapter("cnig_fedme", transport)
        manifest = adapter.pull()

        features = list(adapter.normalize(manifest))
        self.assertEqual(len(features), 1)
        feature = features[0]
        feature.validate()
        # The stable id is the download-centre file id, not the published title.
        self.assertEqual(feature.id, "cnig_fedme:12633198")
        self.assertEqual(feature.name, "GR 123. Etapa 10. Ondarroa-Ispaster")
        self.assertEqual(feature.ref, "GR 123")
        self.assertEqual(feature.official_status, "homologado")
        self.assertEqual(feature.country, "ES")
        self.assertEqual(feature.extras["published_on"], "31/03/2026")
        self.assertIn("detalleArchivo", feature.source_url)

    def test_camino_stages_group_under_their_route_code(self):
        self.seed_index(
            "cnig_camino",
            [{"id": "500", "name": "Camino Francés - ES01c-05a-puente-la-reina-estella",
              "format": "GPX"}],
        )
        transport = FakeTransport(default=FakeResponse(200, gpx_bytes("trk", line((-3.7, 42.3)))))
        adapter = self.adapter("cnig_camino", transport)
        feature = next(iter(adapter.normalize(adapter.pull())))
        feature.validate()
        self.assertEqual(feature.stage_no, 5)
        self.assertEqual(feature.parent_id, "cnig_camino:es01c")
        self.assertEqual(feature.parent_name, "Camino Francés")
        self.assertEqual(feature.country, "ES")
        self.assertEqual(feature.extras["stage_code"], "05a")
        self.assertEqual(feature.extras["section"], "puente-la-reina-estella")

    def test_a_french_camino_stage_is_not_stamped_spanish(self):
        self.seed_index(
            "cnig_camino",
            [{"id": "501", "name": "Caminos en Francia - Via Podiensis - FR03a-12-le-puy",
              "format": "GPX"}],
        )
        transport = FakeTransport(default=FakeResponse(200, gpx_bytes("trk", line((2.0, 45.0)))))
        adapter = self.adapter("cnig_camino", transport)
        feature = next(iter(adapter.normalize(adapter.pull())))
        feature.validate()
        self.assertEqual(feature.country, "FR")

    def test_reads_gpx_out_of_a_zip(self):
        self.seed_index("cnig_fedme", [{"id": "7", "name": "cid", "format": "GPX"}])
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("via.gpx", gpx_bytes("Via", line((-4.0, 37.6))))
        transport = FakeTransport(default=FakeResponse(200, buffer.getvalue()))
        adapter = self.adapter("cnig_fedme", transport)
        features = list(adapter.normalize(adapter.pull()))
        self.assertEqual(len(features), 1)
        self.assertEqual(features[0].extras["archive_member"], "via.gpx")

    def test_shapefile_only_archive_raises_rather_than_undercounting(self):
        self.seed_index("cnig_fedme", [{"id": "9", "name": "shp", "format": "GPX"}])
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("senderos.shp", b"\x00\x00")
        transport = FakeTransport(default=FakeResponse(200, buffer.getvalue()))
        adapter = self.adapter("cnig_fedme", transport)
        manifest = adapter.pull()
        with self.assertRaises(UnsupportedFormat):
            list(adapter.normalize(manifest))


# ------------------------------------------------------------------ NZ DOC ---


class TestNzDocAdapter(AdapterCase):
    def transport_for(self, page_fixture: str, *, item_text: str = "no notice here"):
        page = fixture(page_fixture)

        def handler(_method, url, _kwargs):
            if "sharing/rest/content/items" in url:
                return FakeResponse(200, json_data={"snippet": item_text, "description": ""})
            return FakeResponse(200, page, {"Content-Type": "application/json"})

        return FakeTransport(handler)

    def test_pagination_stops_on_a_short_page(self):
        manifest = self.adapter("nz_doc", self.transport_for("nz_doc_walking_page.json")).pull()
        self.assertTrue(manifest.ok, manifest.errors)
        # Two layers in the experiences series, one page each.
        self.assertEqual(len(manifest.files), 2)

    def test_deprecation_notices_are_warnings_not_failures(self):
        transport = self.transport_for(
            "nz_doc_walking_page.json",
            item_text="Deprecated Dataset. This dataset is no longer being actively maintained.",
        )
        manifest = self.adapter("nz_doc", transport).pull()
        # DOC really has flagged these deprecated. The pull must still succeed --
        # a warning that fails the build teaches everyone to ignore the build.
        self.assertTrue(manifest.ok, manifest.errors)
        self.assertTrue(manifest.warnings)
        self.assertIn("deprecated", manifest.warnings[0].lower())

    def test_empty_service_response_is_a_hard_failure(self):
        empty = FakeResponse(200, json_data={"type": "FeatureCollection", "features": []})
        manifest = self.adapter("nz_doc", FakeTransport(default=empty)).pull()
        self.assertFalse(manifest.ok)
        self.assertIn("zero features", manifest.errors[0])

    def test_service_errors_are_surfaced(self):
        bad = FakeResponse(200, json_data={"error": {"code": 400, "message": "Invalid field"}})
        manifest = self.adapter("nz_doc", FakeTransport(default=bad)).pull()
        self.assertFalse(manifest.ok)
        self.assertIn("Invalid field", manifest.errors[0])

    def test_experiences_normalize_with_web_page_guid_ids(self):
        adapter = self.adapter("nz_doc", self.transport_for("nz_doc_walking_page.json"))
        features = list(adapter.normalize(adapter.pull()))
        self.assertTrue(features)
        for feature in features:
            feature.validate()
            self.assertEqual(feature.country, "NZ")
        walking = [f for f in features if f.extras.get("doc_layer") == "walking"]
        self.assertTrue(walking)
        self.assertTrue(all(f.official_status == "doc_walking_experience" for f in walking))
        # The walking layer has no FlocID/GlobalID, so ids come from the stable
        # content GUID in the DOC web-page URL rather than from OBJECTID.
        self.assertTrue(any(f.id.startswith("nz_doc:walking-") for f in features))
        self.assertFalse(any("oid" in f.id for f in features))

    def test_point_assets_in_the_same_layer_are_skipped(self):
        adapter = self.adapter("nz_doc", self.transport_for("nz_doc_walking_page.json"))
        names = {f.name for f in adapter.normalize(adapter.pull())}
        self.assertNotIn("A hut, not a track", names)

    def test_network_layer_uses_flocid_and_folds_characteristics(self):
        adapter = self.adapter("nz_doc_network", self.transport_for("nz_doc_network_page.json"))
        features = list(adapter.normalize(adapter.pull()))
        self.assertTrue(features)
        feature = features[0]
        feature.validate()
        self.assertEqual(feature.feature_class, "segment")
        self.assertNotIn("walking-", feature.id)
        # DOC spreads metadata across CharName1/CharValue1... pairs; they get
        # folded into one mapping rather than 40 loose keys.
        self.assertIsInstance(feature.extras.get("characteristics"), dict)
        self.assertNotIn("CharName1", feature.extras)

    def test_kind_is_read_from_the_doc_track_vocabulary(self):
        self.assertEqual(
            nz_doc_module._kind_for({"SubObjectType": "Tramping Track"}, nz_doc_module.LAYERS["network"][0]),
            "hiking",
        )
        self.assertEqual(
            nz_doc_module._kind_for({"SubObjectType": "Mountain Bike Track"}, nz_doc_module.LAYERS["network"][0]),
            "mtb",
        )

    def test_both_registered_series_are_known_to_the_adapter(self):
        for source in self.registry.by_adapter("nz_doc"):
            with self.subTest(source=source.id):
                self.assertIn(source.series, nz_doc_module.LAYERS)


# ---------------------------------------------------------------- EuroVelo ---


class TestEuroVeloAdapter(AdapterCase):
    ROUTE_PAGE = b'<html><a href="/route/get-gpx/2?developed=1">GPX</a></html>'

    def transport(self):
        gpx_payload = fixture("eurovelo_ev1.gpx")

        def handler(_method, url, _kwargs):
            if "get-gpx" in url:
                return FakeResponse(200, gpx_payload)
            return FakeResponse(200, self.ROUTE_PAGE, {"Content-Type": "text/html"})

        return FakeTransport(handler)

    def test_route_table_has_seventeen_non_contiguous_corridors(self):
        from trailsdb.adapters.eurovelo import ROUTES

        self.assertEqual(len(ROUTES), 17)
        self.assertNotIn(16, ROUTES)
        self.assertNotIn(18, ROUTES)
        self.assertIn(19, ROUTES)

    def test_discovery_reads_internal_ids_from_the_route_pages(self):
        # The ids are sparse database ids -- EV1 is 2, EV13 is 1, EV14 is 512 --
        # so they must be discovered, never derived from the route number.
        adapter = self.adapter("eurovelo", self.transport())
        self.assertEqual(adapter.discover()[1], "2")
        cached = json.loads((self.paths.raw_dir("eurovelo") / "routes.json").read_text())
        self.assertEqual(cached["routes"]["1"], "2")

    def test_requests_developed_sections_only(self):
        transport = self.transport()
        self.adapter("eurovelo", transport).pull(limit=1)
        self.assertTrue(any("developed=1" in url for url in transport.urls))

    def test_odbl_attribution_carries_the_retrieval_date(self):
        adapter = self.adapter("eurovelo", self.transport())
        manifest = adapter.pull(limit=1)
        feature = next(iter(adapter.normalize(manifest)))
        feature.validate()
        self.assertEqual(feature.license, "odbl-1.0")
        self.assertIn(manifest.retrieved_on, feature.attribution)
        self.assertIn("Open Database License (ODbL)", feature.attribution)
        self.assertIn("www.EuroVelo.com", feature.attribution)

    def test_sections_group_under_their_corridor(self):
        adapter = self.adapter("eurovelo", self.transport())
        features = list(adapter.normalize(adapter.pull(limit=1)))
        # The real EV1 file ships 187 national sections; the fixture keeps two.
        self.assertGreater(len(features), 1)
        for feature in features:
            feature.validate()
            self.assertEqual(feature.ref, "EV1")
            self.assertEqual(feature.parent_id, "eurovelo:EV1")
            self.assertEqual(feature.parent_name, "Atlantic Coast Route")

    def test_a_missing_corridor_is_recorded_but_does_not_lose_the_rest(self):
        gpx_payload = fixture("eurovelo_ev1.gpx")

        def handler(_method, url, _kwargs):
            if "get-gpx/25" in url:
                return FakeResponse(404)
            if "get-gpx" in url:
                return FakeResponse(200, gpx_payload)
            number = url.rsplit("/ev", 1)[-1]
            route_id = {"1": "2", "2": "25", "3": "26"}.get(number, "99")
            return FakeResponse(
                200, f'<a href="/route/get-gpx/{route_id}">x</a>'.encode(),
                {"Content-Type": "text/html"},
            )

        adapter = self.adapter("eurovelo", FakeTransport(handler))
        manifest = adapter.pull(limit=3)
        self.assertEqual(len(manifest.files), 2)
        self.assertTrue(any("EV2" in e for e in manifest.errors))

    def test_all_corridors_failing_is_a_hard_failure(self):
        def handler(_method, url, _kwargs):
            if "get-gpx" in url:
                return FakeResponse(404)
            return FakeResponse(200, self.ROUTE_PAGE, {"Content-Type": "text/html"})

        manifest = self.adapter("eurovelo", FakeTransport(handler)).pull(limit=2)
        self.assertFalse(manifest.ok)


# ------------------------------------------------------------- unimplemented -


class TestPlannedAdapters(AdapterCase):
    def test_a_planned_adapter_says_which_phase_it_is_due_in(self):
        with self.assertRaises(AdapterNotImplemented) as ctx:
            self.adapter("australia_states", FakeTransport())
        self.assertIn("Americas & Oceania wave", str(ctx.exception))

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
