import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import columns  # noqa: E402
import inventory  # noqa: E402
from demo_files import write_demo_csv, write_demo_xlsx  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "mapping-products.json")


def make_workspace(test):
    tmp = tempfile.TemporaryDirectory()
    test.addCleanup(tmp.cleanup)
    directory = tmp.name
    os.makedirs(os.path.join(directory, "sources"))
    os.makedirs(os.path.join(directory, "mappings"))
    write_demo_xlsx(os.path.join(directory, "sources", "demo-catalogue.xlsx"))
    write_demo_csv(os.path.join(directory, "sources", "demo-tarifs.csv"))
    shutil.copy(FIXTURE, os.path.join(directory, "mappings", "products.json"))
    inventory.write_inventory(directory, inventory.inventory(directory))
    return directory


class ColumnsTest(unittest.TestCase):
    def setUp(self):
        self.dir = make_workspace(self)
        self.entries = columns.propose(self.dir)
        self.by_letter = {e["letter"]: e for e in self.entries if e["column"].startswith("main:")}

    def role(self, letter):
        e = self.by_letter[letter]
        return e["role"], e["type"]

    def test_triage_of_demo_columns(self):
        self.assertEqual(self.role("A"), ("core", None))
        self.assertEqual(self.role("G"), ("metafield", "number_decimal"))
        self.assertEqual(self.role("H")[0], "ignore")
        self.assertIn("similar to G (1 conflicts)", self.by_letter["H"]["reason"])
        self.assertEqual(self.role("I"), ("metafield", "number_decimal"))
        self.assertIn("suspect scale", self.by_letter["I"]["reason"])
        self.assertEqual(self.role("J")[0], "ignore")
        self.assertEqual(self.role("K"), ("attribute", "list"))
        self.assertEqual(self.role("L"), ("attribute", "list"))
        self.assertEqual(self.role("Q"), ("ignore", None))
        self.assertEqual(self.role("R"), ("metafield", "single_line_text_field"))
        self.assertEqual(self.by_letter["K"]["column"], "main:matiere")

    def test_join_columns_are_core_and_every_source_is_listed(self):
        prices = {e["column"]: e for e in self.entries if e["column"].startswith("prices:")}
        self.assertEqual(prices["prices:SKU"]["role"], "core")
        self.assertEqual(prices["prices:SKU"]["reason"], "used by products")
        self.assertEqual(prices["prices:Prix public TTC"]["role"], "core")
        self.assertEqual(len(self.by_letter), 18)

    def test_entry_shape_and_rule_reasons(self):
        self.assertEqual(set(self.by_letter["A"]), {"column", "letter", "label", "role", "type", "reason"})
        self.assertEqual(self.by_letter["A"]["reason"], "used by products")
        self.assertEqual(self.by_letter["Q"]["reason"], "empty")
        self.assertEqual(self.by_letter["H"]["reason"],
                         'similar to G (1 conflicts): merge into one "from", winner first')

    def test_missing_inventory_is_an_error(self):
        os.remove(os.path.join(self.dir, "inventory.json"))
        with self.assertRaises(ValueError):
            columns.propose(self.dir)

    def test_cli_writes_the_proposal_under_tmp_and_prints_a_table(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(columns.main(["columns.py", "propose", self.dir]), 0)
        path = os.path.join(self.dir, "tmp", "columns-proposal.json")
        with open(path, encoding="utf-8") as f:
            self.assertEqual(json.load(f), self.entries)
        self.assertFalse(os.path.exists(os.path.join(self.dir, "concepts")))
        lines = out.getvalue().splitlines()
        self.assertTrue(lines[0].startswith("| column "))
        self.assertEqual(len({len(line) for line in lines}), 1)
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(columns.main(["columns.py", "propose"]), 2)
        self.assertIn("Usage: columns.py propose <dir>", err.getvalue())


if __name__ == "__main__":
    unittest.main()
