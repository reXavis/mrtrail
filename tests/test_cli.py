import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from trailsdb.cli import main


def run(*argv, data_dir):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(["--data", str(data_dir), *argv])
    return code, out.getvalue(), err.getvalue()


class TestCli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)

    def test_no_command_prints_help(self):
        code, out, _ = run(data_dir=self.data)
        self.assertEqual(code, 2)
        self.assertIn("usage", out)

    def test_status_lists_every_source_and_summarises(self):
        code, out, _ = run("status", data_dir=self.data)
        self.assertEqual(code, 0)
        self.assertIn("cnig_fedme", out)
        self.assertIn("swisstopo_wanderwege", out)
        self.assertIn("adapters implemented", out)
        self.assertIn("legally verified", out)

    def test_estimate_reproduces_the_plan_numbers(self):
        code, out, _ = run("estimate", data_dir=self.data)
        self.assertEqual(code, 0)
        self.assertIn("master database", out)
        self.assertIn("all tiles worldwide", out)

    def test_estimate_accepts_the_zoom_lever(self):
        _, plain, _ = run("estimate", data_dir=self.data)
        _, capped, _ = run("estimate", "--cap-segments-at-z13", data_dir=self.data)
        self.assertNotEqual(plain, capped)

    def test_licenses_writes_the_app_payload(self):
        target = self.data / "licenses.json"
        code, out, _ = run("licenses", "-o", str(target), data_dir=self.data)
        self.assertEqual(code, 0)
        document = json.loads(target.read_text())
        self.assertIn("sources", document)
        self.assertIn("not legally verified yet", out)

    def test_licenses_to_stdout_is_valid_json(self):
        code, out, _ = run("licenses", data_dir=self.data)
        self.assertEqual(code, 0)
        self.assertIn("licenses", json.loads(out))

    def test_export_rejects_an_unknown_pack(self):
        code, _, err = run("export", "--pack", "atlantis", data_dir=self.data)
        self.assertEqual(code, 2)
        self.assertIn("unknown pack", err)

    def test_export_accepts_an_explicit_bbox(self):
        # The "=" form matters: with a space, argparse reads a negative west
        # longitude as another option.
        code, out, _ = run("export", "--pack", "adhoc", "--bbox=-9,41,-6,44", data_dir=self.data)
        self.assertEqual(code, 0)
        self.assertIn("nothing exported", out)

    def test_export_rejects_a_malformed_bbox(self):
        code, _, err = run("export", "--pack", "adhoc", "--bbox=1,2,3", data_dir=self.data)
        self.assertEqual(code, 2)
        self.assertIn("bad --bbox", err)

    def test_export_rejects_an_inverted_bbox(self):
        code, _, err = run("export", "--pack", "adhoc", "--bbox=10,41,-6,44", data_dir=self.data)
        self.assertEqual(code, 2)
        self.assertIn("inverted", err)

    def test_unknown_source_selection_is_a_clean_error(self):
        code, _, err = run("normalize", "atlantis", data_dir=self.data)
        self.assertEqual(code, 2)
        self.assertIn("registry error", err)

    def test_search_without_a_catalog_explains_itself(self):
        code, _, err = run("search", "Camino", data_dir=self.data)
        self.assertEqual(code, 2)
        self.assertIn("run `trailsdb normalize` first", err)

    def test_normalize_skips_sources_with_nothing_pulled(self):
        code, out, _ = run("normalize", "cnig_fedme", data_dir=self.data)
        self.assertEqual(code, 0)
        self.assertIn("nothing pulled yet", out)

    def test_pull_skips_unimplemented_adapters_without_failing(self):
        code, out, _ = run("pull", "swisstopo_wanderwege", data_dir=self.data)
        self.assertEqual(code, 0)
        self.assertIn("adapter not implemented yet", out)


if __name__ == "__main__":
    unittest.main()
