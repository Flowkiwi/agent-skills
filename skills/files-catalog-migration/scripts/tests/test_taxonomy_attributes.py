import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import mapping  # noqa: E402
import payload  # noqa: E402
import plan_shared  # noqa: E402
import taxonomy  # noqa: E402
from concept_helpers import ConceptTestCase, fixture  # noqa: E402

FIXTURE = fixture("mapping-taxonomy.json")
MATIERE = {"name": "Matière", "type": "list", "value": {"from": ["main:matiere"],
                                                        "map": {"inox": "Acier inoxydable"}, "map_ignore_case": True}}


class AttributesTest(ConceptTestCase):
    def extract_attributes(self, attributes, **changes):
        t = self.extract_with(dict(FIXTURE, attributes=attributes, **changes))
        return t, [w["code"] for w in t["warnings"]]

    def attribute(self, entry):
        return self.extract_attributes([entry])

    def test_value_map_merges_variants(self):
        t, codes = self.attribute({"name": "Matière", "type": "list", "value": {"from": ["main:matiere"],
                                   "map": {"inox": "Acier inoxydable"}, "map_ignore_case": True}})
        a = t["attributes"][0]
        self.assertEqual(a["code"], "matiere")
        self.assertEqual([o["value"] for o in a["options"]], ["Acier inoxydable", "Plastique"])
        self.assertEqual(a["options"][0]["source"], "Inox")
        self.assertEqual(a["values"]["2228"], ["Acier inoxydable"])
        self.assertEqual(a["categories"], ["Petit électroménager"])
        self.assertNotIn("option_case", codes)

    def test_case_variants_are_reported(self):
        _, codes = self.attribute({"name": "Matière", "type": "list", "value": {"from": ["main:matiere"]}})
        self.assertIn("option_case", codes)

    def test_boolean_and_empty(self):
        t, _ = self.attribute({"name": "Décongélation", "type": "list",
                               "value": {"from": ["main:decongelation"], "transform": [{"boolean": ["Oui", "Non"]}]}})
        a = t["attributes"][0]
        self.assertEqual([o["value"] for o in a["options"]], ["Oui", "Non"])
        self.assertNotIn("1004", a["values"])
        self.assertEqual(a["values"]["2227"], ["Non"])

    def test_multiple_values_in_a_group(self):
        t, codes = self.attribute({"name": "Coloris", "type": "list", "value": {"from": ["main:coloris"]}})
        self.assertEqual(t["attributes"][0]["values"]["2230"], ["Noir"])
        self.assertIn("multiple_values", codes)

    def test_bad_code(self):
        with self.assertRaises(mapping.MappingError):
            self.attribute({"code": "Matière!", "name": "M", "type": "list", "value": {"from": ["main:matiere"]}})

    def test_payload_attributes(self):
        t, _ = self.attribute({"name": "Matière", "type": "list", "value": {"from": ["main:matiere"], "map": {"inox": "Acier inoxydable"}, "map_ignore_case": True}})
        body = payload.build(self.products(), self.state_with_decision("taxonomy", t), "2226", placeholders=True)["product"]["body"]
        self.assertEqual(body["attributes"], [{"definition": "<attribute_definition:matiere IRI>",
                                               "option": "<attribute_option:matiere:Acier inoxydable IRI>"}])

    def test_built_entry_and_option_sources(self):
        t, codes = self.attribute(dict(MATIERE, value=dict(MATIERE["value"], map={"inox": "Acier inoxydable",
                                                                                 "plastique": "Plastique"})))
        a = t["attributes"][0]
        self.assertEqual(list(a), ["code", "name", "type", "multiple", "categories", "options", "values"])
        self.assertEqual(a["options"], [{"value": "Acier inoxydable", "source": "Inox"},
                                        {"value": "Plastique", "source": "Plastique"}])
        self.assertEqual(a["values"], {"2226": ["Acier inoxydable"], "2227": ["Acier inoxydable"],
                                       "2228": ["Acier inoxydable"], "1004": ["Plastique"],
                                       "nokey-0001": ["Acier inoxydable"], "2230": ["Acier inoxydable"]})
        self.assertEqual(codes, [])

    def test_multiple_keeps_every_value(self):
        t, codes = self.attribute({"name": "Coloris", "type": "list", "multiple": True,
                                   "value": {"from": ["main:coloris"]}})
        a = t["attributes"][0]
        self.assertEqual(a["values"]["2230"], ["Noir", "Rouge"])
        self.assertTrue(a["multiple"])
        self.assertNotIn("multiple_values", codes)

    def test_string_attribute_and_too_long(self):
        t, codes = self.attribute({"name": "Notice", "type": "string",
                                   "value": {"from": ["main:notice"], "map": {"Voir notice": "x" * 256}}})
        a = t["attributes"][0]
        self.assertEqual((a["code"], a["options"]), ("notice", []))
        self.assertEqual(a["values"]["2227"], ["x" * 256])
        self.assertEqual(codes, ["too_long"])
        body = payload.build(self.products(), self.state_with_decision("taxonomy", t), "1004",
                             placeholders=True)["product"]["body"]
        self.assertEqual(body["attributes"], [{"definition": "<attribute_definition:notice IRI>",
                                               "value": "https://example.com/notice/1004.pdf"}])

    def test_products_without_category_lose_their_values(self):
        categories = dict(FIXTURE["categories"], map=dict(FIXTURE["categories"]["map"], **{"PEM > Bouilloire": None}))
        t, codes = self.extract_attributes([MATIERE], categories=categories)
        a = t["attributes"][0]
        self.assertNotIn("1004", a["values"])
        self.assertEqual([o["value"] for o in a["options"]], ["Acier inoxydable"])
        self.assertEqual(a["categories"], ["Petit électroménager > Grille-pain"])
        dropped = [w for w in t["warnings"] if w["code"] == "uncategorized_values"]
        self.assertEqual(dropped[0]["message"], "attribute 'matiere': 1 products without category lose their value (1004)")

    def test_plan_shared_reads_attributes(self):
        t, _ = self.attribute(MATIERE)
        keys = [e["key"] for e in plan_shared.plan(self.state_with_decision("taxonomy", t))]
        self.assertEqual(keys[-3:], ["attribute_definition:matiere", "attribute_option:matiere:Acier inoxydable",
                                     "attribute_option:matiere:Plastique"])

    def test_attribute_code(self):
        self.assertEqual(taxonomy.attribute_code("Cœur"), "coeur")
        self.assertEqual(taxonomy.attribute_code("Ætat ŒIL"), "aetat-oeil")
        self.assertEqual(taxonomy.attribute_code("Poids (kg)"), "poids-kg")
        self.assertEqual(len(taxonomy.attribute_code("a" * 63 + " b")), 63)

    def test_mapping_errors(self):
        bad = [[dict(MATIERE, code="m")], [dict(MATIERE, name="")], [dict(MATIERE, type="number")],
               [dict(MATIERE, multiple="no")], [dict(MATIERE, value=None)], [dict(MATIERE, colour="x")],
               [dict(MATIERE, value={"from": ["main:nope"]})], [dict(MATIERE, origin="me")],
               [MATIERE, dict(MATIERE, name="Matiere")], ["matiere"]]
        for attributes in bad:
            with self.subTest(attributes=attributes), self.assertRaises(mapping.MappingError):
                self.extract_attributes(attributes)

    def test_preview_lists_attributes(self):
        t, _ = self.attribute(MATIERE)
        self.assertIn("  matiere 'Matière' (list): 6 products, single value, on ['Petit électroménager'], "
                      "options: Acier inoxydable (from Inox), Plastique", taxonomy.format_preview(t).splitlines())


if __name__ == "__main__":
    unittest.main()
