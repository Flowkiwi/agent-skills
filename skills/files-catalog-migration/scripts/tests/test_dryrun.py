import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import dryrun  # noqa: E402
import extract  # noqa: E402
import state as state_module  # noqa: E402
from demo_files import write_demo_csv, write_demo_xlsx  # noqa: E402

SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
CODES = os.path.join(SCRIPTS, "codes.py")
FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "mapping-products.json")


class DryRunTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        os.makedirs(os.path.join(self.dir, "sources"))
        os.makedirs(os.path.join(self.dir, "mappings"))
        write_demo_xlsx(os.path.join(self.dir, "sources", "demo-catalogue.xlsx"))
        write_demo_csv(os.path.join(self.dir, "sources", "demo-tarifs.csv"))
        shutil.copy(FIXTURE, os.path.join(self.dir, "mappings", "products.json"))
        self.products_path = os.path.join(self.dir, "products.json")
        self.state_path = os.path.join(self.dir, "state.json")
        data = extract.main_products(self.dir)
        self.write_state(state_module.init("demo", "https://mcp.example.com", data))
        self.decide("data_language", "fr", "client")

    def products(self):
        with open(self.products_path, encoding="utf-8") as f:
            return json.load(f)

    def state(self):
        with open(self.state_path, encoding="utf-8") as f:
            return json.load(f)

    def write_state(self, state):
        with open(self.state_path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)

    def decide(self, key, value, origin, reason=None):
        self.write_state(state_module.decide(self.state(), key, value, origin, reason))

    def fill_codes_and_decide_status(self):
        subprocess.run([sys.executable, CODES, "fill", self.products_path, "--prefix", "2", "--start", "1",
                        "--sku-pattern", "{handle}-{index}"], check=True, stdout=subprocess.DEVNULL)
        extract.main_products(self.dir)
        self.decide("status_mode", "draft", "client")

    def test_blocked_until_codes(self):
        text, code = dryrun.report(self.dir)
        self.assertEqual(code, 1)
        self.assertIn("missing_sku (2)", text)

    def test_ready_report(self):
        self.fill_codes_and_decide_status()
        text, code = dryrun.report(self.dir)
        self.assertEqual(code, 0)
        self.assertIn("Blocking: none", text)
        self.assertIn("  quote: 2", text)
        self.assertIn("[products] group: Les lignes de même référence commerciale", text)
        self.assertIn("[products] product.description_html.generate:", text)
        self.assertIn("Generated codes: 2 barcodes, 2 SKUs", text)
        self.assertIn("3 image URLs kept in products.json", text)

    def test_section_titles_in_order(self):
        self.fill_codes_and_decide_status()
        text, _ = dryrun.report(self.dir)
        titles = ["Data language: fr", "Status: draft", "To create:", "Blocking:", "Cleaning:", "Agent choices:",
                  "Generated codes:", "Descriptions:", "Not migrated:"]
        positions = [text.index(title) for title in titles]
        self.assertEqual(positions, sorted(positions))

    def test_counts_created_products_apart(self):
        self.fill_codes_and_decide_status()
        state = self.state()
        state["products"]["2226"]["status"] = "created"
        self.write_state(state)
        text, _ = dryrun.report(self.dir)
        self.assertIn("  products: 5 (1 already created)", text)

    def test_missing_decisions_exit_1(self):
        self.fill_codes_and_decide_status()
        state = self.state()
        del state["decisions"]["status_mode"]
        self.write_state(state)
        text, code = dryrun.report(self.dir)
        self.assertEqual(code, 1)
        self.assertIn("Blocking: none", text)
        self.assertIn("missing decision: status_mode", text)

    def test_created_handle_missing_from_products_blocks(self):
        self.fill_codes_and_decide_status()
        state = self.state()
        state["products"]["nokey-0009"] = dict(state["products"]["2226"], status="created")
        state["products"]["gone"] = dict(state["products"]["2226"], status="deleted")
        self.write_state(state)
        text, code = dryrun.report(self.dir)
        self.assertEqual(code, 1)
        self.assertNotIn("Blocking: none", text)
        self.assertIn("identity changed or product removed: nokey-0009", text)
        self.assertIn("undo it first or restore its key", text)
        self.assertNotIn("gone", text)

    def test_handles_out_of_sync_with_the_state_block(self):
        self.fill_codes_and_decide_status()
        state = self.state()
        del state["products"]["2226"]
        state["products"]["2227"]["status"] = "skipped"
        state["products"]["old"] = dict(state["products"]["2228"])
        self.write_state(state)
        text, code = dryrun.report(self.dir)
        self.assertEqual(code, 1)
        self.assertIn("run state.py sync: 2226, 2227, old", text)
        state_module.sync_products(state, {"products": [{"handle": h} for h in
                                                         ("2226", "2227", "2228", "1004", "nokey-0001", "2230")]})
        self.write_state(state)
        self.assertEqual(dryrun.report(self.dir)[1], 0)

    def test_sync_clears_a_removed_failed_handle(self):
        self.fill_codes_and_decide_status()
        state = self.state()
        state["products"]["old"] = dict(state["products"]["2228"], status="failed", error="boom")
        self.write_state(state)
        text, code = dryrun.report(self.dir)
        self.assertEqual(code, 1)
        self.assertIn("run state.py sync: old", text)
        self.write_state(state_module.sync_products(state, self.products()))
        self.assertEqual(dryrun.report(self.dir)[1], 0)

    def test_payload_errors_are_blocking(self):
        self.fill_codes_and_decide_status()
        self.decide("modules", ["core", "taxonomy"], "developer")
        text, code = dryrun.report(self.dir)
        self.assertEqual(code, 1)
        self.assertIn("payload.py fails for 6 products", text)
        self.assertIn("2226: decision 'taxonomy' is missing", text)

    def test_unknown_status_mode_blocks(self):
        self.fill_codes_and_decide_status()
        self.decide("status_mode", "published", "developer")
        text, code = dryrun.report(self.dir)
        self.assertEqual(code, 1)
        self.assertIn("status_mode 'published' is not one of draft, active, source", text)
        self.assertNotIn("payload.py fails", text)

    def test_agent_decision_from_state(self):
        self.fill_codes_and_decide_status()
        self.decide("codes", {"prefix": "2"}, "agent", "Codes EAN internes en attendant ceux du client.")
        choices = dryrun.agent_choices(self.dir, self.state())
        self.assertEqual(choices[-1]["path"], "codes")
        self.assertEqual(choices[-1]["concept"], "decisions")
        self.assertEqual(choices[-1]["spec"], {"prefix": "2"})
        paths = [c["path"] for c in choices if c["concept"] == "products"]
        self.assertEqual(paths, ["group", "product.description_html.generate"])
        group = choices[0]
        self.assertEqual(group["spec"], {"key": {"from": ["main:ref_com", "main:sku_marchand"]}})
        self.assertIsNone(group["question"])


if __name__ == "__main__":
    unittest.main()
