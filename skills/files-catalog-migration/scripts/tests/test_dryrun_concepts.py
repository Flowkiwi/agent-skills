import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import dryrun  # noqa: E402
import plan_shared  # noqa: E402
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

    def decide(self, key, value_json=None, origin=("--origin", "developer")):
        value = ["--value-json", value_json] if value_json else [
            "--value-file", os.path.join(self.dir, "concepts", key + ".json")]
        run("state.py", "decide", self.dir, "--key", key, *(value + list(origin)))

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

    def test_mcp_effort_counts_concept_calls(self):
        products = read_json(os.path.join(self.dir, "products.json"))
        st = read_json(os.path.join(self.dir, "state.json"))
        concepts = {c: read_json(os.path.join(self.dir, "concepts", c + ".json")) for c in CONCEPTS}
        calls = 2 * len(plan_shared.plan(st, products.get("metafield_columns")))
        for p in products["products"]:
            handle = p["handle"]
            calls += 3 + len(p["options"]) + sum(len(o["values"]) for o in p["options"]) + len(p["variants"])
            calls += len(concepts["metafields"]["values"].get(handle) or {})
            calls += len(concepts["metaobjects"]["values"].get(handle) or {})
            calls += sum(len(by_country) for by_country in (concepts["prices"]["offers"].get(handle) or {}).values())
            calls += 1 if handle in concepts["taxonomy"]["products"] else 0
        text, _ = dryrun.report(self.dir)
        self.assertGreater(calls, 60)
        self.assertIn("MCP effort: ~%d calls, ~%d min (about 8 s per call)" % (calls, -(-calls * 8 // 60)), text)

    def test_proposed_names_are_listed(self):
        text, _ = dryrun.report(self.dir)
        concepts = text[text.index("Concepts:"):text.index("Blocking:")].splitlines()
        for line in ("    category paths:", "      Petit électroménager > Grille-pain", "    attributes:",
                     "      matiere 'Matière' (list): Acier inoxydable, Plastique", "    definitions:",
                     "      marque 'Marque': fields nom 'Nom' (single_line_text_field); reference custom.marque 'Marque'",
                     "    entries:", "      marque brioche-co 'Brioche & Co'", "    taxes:",
                     "      FR: TVA 20 FR 20% (to create)"):
            self.assertIn(line, concepts)

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
        self.assertIn("  stale decision: collections (run state.py decide %s --key collections --value-file "
                      "%s --origin developer)" % (self.dir, os.path.join(self.dir, "concepts", "collections.json")),
                      text)
        self.assertIn("  collections: 1 collections, 1 products in a collection", text)
        self.decide("collections")
        self.assertEqual(dryrun.report(self.dir)[1], 0)

    def test_stale_line_keeps_the_recorded_origin_and_reason(self):
        self.decide("taxonomy", origin=("--origin", "agent", "--reason", "Arbre proposé"))
        self.edit_mapping("taxonomy", lambda m: m["categories"].update(drop_levels=2))
        run("extract.py", self.dir, "taxonomy")
        text, _ = dryrun.report(self.dir)
        self.assertIn("  stale decision: taxonomy (run state.py decide %s --key taxonomy --value-file %s "
                      "--origin agent --reason 'Arbre proposé')" % (
                          self.dir, os.path.join(self.dir, "concepts", "taxonomy.json")), text)

    def test_mapping_changed_since_extraction_blocks(self):
        self.edit_mapping("metafields", lambda m: m.update(reason="Poids en kg."))
        text, code = dryrun.report(self.dir)
        self.assertEqual(code, 1)
        self.assertIn("  concept metafields was extracted from another mappings/metafields.json: re-run extract.py "
                      "%s metafields" % self.dir, text)
        run("extract.py", self.dir, "metafields")
        self.decide("metafields")
        self.assertEqual(dryrun.report(self.dir)[1], 0)

    def test_concept_file_without_provenance_blocks(self):
        path = os.path.join(self.dir, "concepts", "taxonomy.json")
        data = read_json(path)
        del data["provenance"]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        text, code = dryrun.report(self.dir)
        self.assertEqual(code, 1)
        self.assertIn("  concept taxonomy was extracted from another products.json", text)

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


class ProvenanceTest(unittest.TestCase):
    """The documented order skipped: codes.py fill after extract all, prices not extracted again."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        write_workspace(self.dir, CONCEPTS)
        run("extract.py", self.dir, "all")
        run("codes.py", "fill", os.path.join(self.dir, "products.json"), "--prefix", "2", "--start", "1",
            "--sku-pattern", "{handle}-{index}")
        run("state.py", "init", self.dir, "--client", "demo", "--mcp-url", "https://demo.invalid/api/mcp")
        for key, value in (("data_language", '"fr"'), ("status_mode", '"draft"')):
            run("state.py", "decide", self.dir, "--key", key, "--value-json", value, "--origin", "developer")
        for concept in CONCEPTS:
            self.decide(concept)

    def decide(self, concept):
        run("state.py", "decide", self.dir, "--key", concept, "--value-file",
            os.path.join(self.dir, "concepts", concept + ".json"), "--origin", "developer")

    def test_prices_from_before_codes_fill_block(self):
        text, code = dryrun.report(self.dir)
        self.assertEqual(code, 1)
        blocking = text[text.index("Blocking:"):text.index("Cleaning:")]
        self.assertIn("  concept prices was extracted from another products.json: re-run extract.py %s prices"
                      % self.dir, blocking)
        for concept in ("taxonomy", "metafields", "metaobjects"):
            self.assertNotIn("concept %s was extracted" % concept, blocking)
        self.assertIn("  prices:\n    missing_sku (2)\n      row 5 2228: ", blocking)
        run("extract.py", self.dir, "prices")
        self.decide("prices")
        text, code = dryrun.report(self.dir)
        self.assertEqual(code, 0, text)

    def test_removed_products_block_every_concept(self):
        path = os.path.join(self.dir, "fixes.json")
        fixes = read_json(path) if os.path.exists(path) else {}
        fixes["skip"] = ["1004"]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(fixes, f)
        run("extract.py", self.dir, "products")
        text, code = dryrun.report(self.dir)
        self.assertEqual(code, 1)
        for concept in CONCEPTS:
            self.assertIn("  concept %s was extracted from another products.json" % concept, text)


class ConceptLinesTest(unittest.TestCase):
    def test_pairs_without_price_never_negative(self):
        data = {"countries": [{"code": "FR"}], "offers": {"a": {"A-1": {"FR": {}}}, "b": {"B-1": {"FR": {}}}}}
        lines = dryrun.concept_lines("prices", data, [{"variants": [{"sku": "A-1"}]}])
        self.assertEqual(lines, ["  prices: 2 offers, 0 variant-country pairs without price, countries FR"])

    def test_names_are_capped(self):
        data = {"collections": [{"path": "Collection %02d" % i} for i in range(25)], "membership": {}}
        lines = dryrun.name_lines("collections", data)
        self.assertEqual(lines[0], "    collection paths:")
        self.assertEqual(lines[1:3], ["      Collection 00", "      Collection 01"])
        self.assertEqual(lines[-1], "      ... and 5 more")
        self.assertEqual(len(lines), 22)
        options = [{"value": "V%d" % i} for i in range(22)]
        taxonomy = {"categories": [], "attributes": [{"code": "c", "name": "C", "type": "list", "options": options}]}
        self.assertTrue(dryrun.name_lines("taxonomy", taxonomy)[1].endswith(", V19, ... and 2 more"))

    def test_missing_sku_and_too_long_in_a_concept_file_block(self):
        warnings = [{"row": 3, "handle": "a", "code": code, "field": "f", "message": "m", "stage": "extract"}
                    for code in ("missing_sku", "too_long", "missing_price")]
        text = "\n".join(dryrun.concept_blocking([("taxonomy", {"warnings": warnings})]))
        self.assertIn("missing_sku (1)", text)
        self.assertIn("too_long (1)", text)
        self.assertNotIn("missing_price", text)


if __name__ == "__main__":
    unittest.main()
