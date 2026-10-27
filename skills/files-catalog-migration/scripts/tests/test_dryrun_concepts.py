import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import dryrun  # noqa: E402
from concept_helpers import read_json, run, write_workspace  # noqa: E402

CONCEPTS = ("taxonomy", "metafields", "metaobjects", "prices")


class DryRunConceptsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        write_workspace(self.dir, CONCEPTS)
        run("extract.py", self.dir, "all")
        run("codes.py", "fill", os.path.join(self.dir, "products.json"), "--prefix", "2", "--start", "1",
            "--sku-pattern", "{handle}-{index}")
        run("extract.py", self.dir, "prices")
        run("state.py", "init", self.dir, "--client", "demo", "--mcp-url", "https://demo.invalid/api/mcp")
        self.decide("data_language", value_json='"fr"')
        self.decide("status_mode", value_json='"draft"')
        for concept in CONCEPTS:
            self.decide(concept)

    def decide(self, key, value_json=None):
        value = ["--value-json", value_json] if value_json else [
            "--value-file", os.path.join(self.dir, "concepts", key + ".json")]
        run("state.py", "decide", self.dir, "--key", key, *value, "--origin", "developer")

    def edit_mapping(self, concept, change):
        path = os.path.join(self.dir, "mappings", concept + ".json")
        m = read_json(path) if os.path.exists(path) else {"version": 1, "concept": concept}
        change(m)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(m, f, ensure_ascii=False)

    def test_concept_lines(self):
        text, code = dryrun.report(self.dir)
        self.assertEqual(code, 0)
        self.assertIn("  taxonomy: 3 categories, 1 attribute definitions, 2 options, 0 products without category", text)
        self.assertIn("  metaobjects: 1 definitions, 3 entries, 6 products referenced", text)
        self.assertRegex(text, r"custom\.poids_net \| Poids net \(kg\) \| proposed number_decimal \| chosen number_decimal \| 5 values")

    def test_stale_decision_blocks(self):
        self.edit_mapping("taxonomy", lambda m: m["categories"].update(drop_levels=2))
        run("extract.py", self.dir, "taxonomy")
        text, code = dryrun.report(self.dir)
        self.assertEqual(code, 1)
        self.assertIn("stale decision: taxonomy", text)

    def test_sections_and_prices_line(self):
        text, _ = dryrun.report(self.dir)
        titles = ["To create:", "Concepts:", "  taxonomy:", "  metafields:", "  metaobjects:", "  prices:",
                  "Blocking: none", "Agent choices:"]
        positions = [text.index(title) for title in titles]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("  prices: 6 offers, 1 variant-country pairs without price, countries FR", text)
        self.assertIn("[metaobjects] definitions.0: La colonne Marque", text)

    def test_concept_file_without_decision_is_stale(self):
        self.edit_mapping("collections", lambda m: m.update(value=None, assign={"2226": ["Petit déjeuner"]}))
        run("extract.py", self.dir, "collections")
        text, code = dryrun.report(self.dir)
        self.assertEqual(code, 1)
        self.assertIn("  stale decision: collections (run state.py decide <dir> --key collections --value-file "
                      "concepts/collections.json --origin developer)", text)
        self.assertIn("  collections: 1 collections, 1 products in a collection", text)
        self.decide("collections")
        self.assertEqual(dryrun.report(self.dir)[1], 0)

    def test_only_the_five_concepts_are_checked(self):
        with open(os.path.join(self.dir, "concepts", "columns-proposal.json"), "w", encoding="utf-8") as f:
            json.dump([], f)
        self.assertEqual(dryrun.report(self.dir)[1], 0)

    def test_concept_warnings_are_listed_and_list_in_single_field_blocks(self):
        text, _ = dryrun.report(self.dir)
        self.assertIn("    conflict (", text)
        self.edit_mapping("metafields", lambda m: m["definitions"].update({"custom.matiere": {
            "name": "Matière", "type": "single_line_text_field",
            "value": {"from": ["main:matiere", "main:coloris"], "pick": "all"}}}))
        run("extract.py", self.dir, "metafields")
        self.decide("metafields")
        text, code = dryrun.report(self.dir)
        self.assertEqual(code, 1)
        blocking = text[text.index("Blocking:"):text.index("Cleaning:")]
        self.assertIn("  metafields:", blocking)
        self.assertIn("list_in_single_field (", blocking)


if __name__ == "__main__":
    unittest.main()
