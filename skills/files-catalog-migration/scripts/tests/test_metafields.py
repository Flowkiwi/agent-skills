import contextlib
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import extract  # noqa: E402
import mapping  # noqa: E402
import metafields  # noqa: E402
import payload  # noqa: E402
import plan_shared  # noqa: E402
from concept_helpers import ConceptTestCase  # noqa: E402


class MetafieldsTest(ConceptTestCase):
    MAPPING = {"version": 1, "concept": "metafields", "definitions": {
        "custom.poids_net": {"name": "Poids net (kg)", "type": "number_decimal",
                             "value": {"from": ["main:dim_poids_net", "main:poids_net"], "transform": ["decimal"]}},
        "custom.composition": {"name": "Composition", "type": "list__single_line_text_field",
                               "value": {"from": ["main:matiere", "main:coloris"], "pick": "all"}}}}

    def extract_with(self, m):
        result = super().extract_with(m)
        return result, [w["code"] for w in result["warnings"]]

    def one(self, key, name, kind, spec, **extra):
        return dict(self.MAPPING, definitions={key: dict({"name": name, "type": kind, "value": spec}, **extra)})

    def test_extract(self):
        m, codes = self.extract_with(self.MAPPING)
        self.assertEqual(m["definitions"]["custom.poids_net"], {"type": "number_decimal", "name": "Poids net (kg)", "transform": "number"})
        self.assertEqual(m["values"]["2227"]["custom.poids_net"], "2.6")
        self.assertIn("conflict", codes)
        self.assertEqual(m["values"]["2226"]["custom.composition"], ["Inox", "argenté"])

    def test_payload(self):
        m, _ = self.extract_with(self.MAPPING)
        out = payload.build(self.products(), self.state_with_decision("metafields", m), "2226", placeholders=True)["metafields"]
        self.assertEqual(out[0], {"key": "custom.composition", "shared": "metafield_definition:custom.composition",
                                  "body": {"definition": "<metafield_definition:custom.composition IRI>", "value": '["Inox", "argenté"]'}})
        self.assertEqual(out[1]["body"]["value"], 1.8)

    def test_validation(self):
        for bad in ({"import.key": {"name": "Clé", "type": "json", "value": {"value": "x"}}},
                    {"c.ab": {"name": "Abc", "type": "json", "value": {"value": "x"}}},
                    {"custom.abc": {"name": "Abc", "type": "boolean", "value": {"value": "x"}}}):
            with self.assertRaises(mapping.MappingError):
                self.extract_with(dict(self.MAPPING, definitions=bad))

    def test_types_come_from_the_metafields_module(self):
        self.assertEqual(metafields.METAFIELD_TYPES, (
            "single_line_text_field", "multi_line_text_field", "number_integer", "number_decimal", "json",
            "list__single_line_text_field", "list__number_integer", "list__number_decimal"))
        self.assertFalse(hasattr(extract, "METAFIELD_TYPES"))

    def test_more_mapping_errors(self):
        spec = {"value": "x"}
        bad = [dict(self.MAPPING, extra=1), dict(self.MAPPING, concept="taxonomy"), dict(self.MAPPING, definitions=[]),
               self.one("custom.abc", "Ab", "json", spec), self.one("custom.abc", "Abc", "json", "x"),
               self.one("custom.abc", "Abc", "json", spec, transform="strip_html"),
               self.one("custom.abc", "Abc", "list__number_integer", spec, transform="number"),
               self.one("custom.abc", "Abc", "json", spec, shopify="x"),
               self.one("custom.abc", "Abc", "json", {"from": ["main:nope"]}),
               self.one("Custom.abc", "Abc", "json", spec), self.one("customabc", "Abc", "json", spec),
               self.one("custom.1bc", "Abc", "json", spec), self.one("custom.abc", "Abc", "json", spec, origin="robot")]
        for m in bad:
            with self.subTest(m=m), self.assertRaises(mapping.MappingError):
                self.extract_with(m)

    def test_list_types_wrap_a_single_value(self):
        m, _ = self.extract_with(self.one("custom.matiere", "Matière", "list__single_line_text_field",
                                          {"from": ["main:matiere"]}))
        self.assertEqual(m["definitions"]["custom.matiere"]["transform"], "list_json")
        self.assertEqual(m["values"]["2227"]["custom.matiere"], ["Acier inoxydable"])

    def test_values_come_from_the_first_row_of_a_product(self):
        m, _ = self.extract_with(self.one("custom.couleur", "Couleur", "single_line_text_field",
                                          {"from": ["main:coloris"]}))
        self.assertEqual(m["values"]["2230"], {"custom.couleur": "Noir"})
        self.assertEqual(m["definitions"]["custom.couleur"]["transform"], None)

    def test_not_a_number_is_dropped(self):
        m, codes = self.extract_with(self.one("custom.couleur", "Couleur", "number_decimal", {"from": ["main:coloris"]}))
        self.assertEqual(m["values"], {})
        self.assertEqual(codes.count("not_a_number"), 6)
        warning = next(w for w in m["warnings"] if w["code"] == "not_a_number")
        self.assertEqual((warning["field"], warning["stage"]), ("metafields.custom.couleur", "extract"))
        self.assertIn("argenté", warning["message"])

    def test_integer_type_refuses_a_decimal(self):
        m, codes = self.extract_with(self.one("custom.brut", "Poids brut", "number_integer",
                                              {"from": ["main:dim_poids_brut"], "transform": ["decimal"]}))
        self.assertEqual(m["values"], {"2227": {"custom.brut": "1964"}, "2228": {"custom.brut": "2"}})
        self.assertEqual(codes.count("not_a_number"), 3)

    def test_decimal_comma_is_a_number(self):
        m, codes = self.extract_with(self.one("custom.poids", "Poids", "number_decimal", {"from": ["main:poids_net"]}))
        self.assertEqual(m["values"]["2226"], {"custom.poids": "1,8"})
        self.assertNotIn("not_a_number", codes)
        out = payload.build(self.products(), self.state_with_decision("metafields", m), "2226", placeholders=True)
        self.assertEqual(out["metafields"][0]["body"]["value"], 1.8)

    def test_too_long_single_line_is_kept(self):
        m, codes = self.extract_with(self.one("custom.long", "Long", "single_line_text_field",
                                              {"value": "x" * 256}))
        self.assertEqual(len(m["values"]["2226"]["custom.long"]), 256)
        self.assertIn("too_long", codes)

    def test_explicit_transform_wins(self):
        m, _ = self.extract_with(self.one("custom.matiere", "Matière", "list__single_line_text_field",
                                          {"from": ["main:matiere"]}, transform="list"))
        self.assertEqual(m["definitions"]["custom.matiere"]["transform"], "list")
        st = self.state_with_decision("metafields", m)
        self.assertEqual(payload.build(self.products(), st, "2227", placeholders=True)["metafields"][0]["body"]["value"],
                         ["Acier inoxydable"])

    def test_literal_value(self):
        m, _ = self.extract_with(self.one("custom.origine", "Origine", "single_line_text_field", {"value": "France"}))
        self.assertEqual(m["values"]["1004"], {"custom.origine": "France"})
        self.assertEqual(len(m["values"]), len(self.products()["products"]))

    def test_payload_skips_a_definition_without_value(self):
        m, _ = self.extract_with(self.MAPPING)
        st = self.state_with_decision("metafields", m)
        out = payload.build(self.products(), st, "nokey-0001", placeholders=True)["metafields"]
        self.assertEqual([e["key"] for e in out], ["custom.composition"])
        out = payload.build(self.products(), st, "2228", placeholders=True)["metafields"]
        self.assertEqual([e["body"]["value"] for e in out], ['["inox", "Crème"]', 1.5])

    def test_payload_without_values_reads_the_products(self):
        st = self.state_with_decision("metafields", {"definitions": {"custom.material": {
            "type": "single_line_text_field", "name": "Material", "transform": None}}})
        self.assertEqual(payload.build(self.products(), st, "2226", placeholders=True)["metafields"], [])

    def test_transform_value_accepts_a_list(self):
        self.assertEqual(payload.transform_value(["a", "é"], "list_json"), '["a", "é"]')
        self.assertEqual(payload.transform_value(["a", "b"], "list"), ["a", "b"])
        self.assertEqual(payload.transform_value(["a", "b"], None), ["a", "b"])
        self.assertEqual(payload.transform_value("a; b", "list"), ["a", "b"])
        with self.assertRaises(payload.PayloadError):
            payload.transform_value(["1"], "number")

    def test_plan_shared_entries(self):
        m, _ = self.extract_with(self.MAPPING)
        st = self.state_with_decision("metafields", m)
        entries = [e for e in plan_shared.plan(st) if e["module"] == "metafields"]
        self.assertEqual([e["key"] for e in entries],
                         ["metafield_definition:custom.composition", "metafield_definition:custom.poids_net"])
        self.assertEqual(entries[1]["spec"], {"type": "number_decimal", "name": "Poids net (kg)"})
        self.assertEqual(entries[1]["body"], {
            "namespace": "custom", "key": "poids_net", "name": "Poids net (kg)",
            "ownerType": "/api/metafield_owner_types/products",
            "type": "/api/metafield_definition_types/number_decimal"})

    def test_decision_format(self):
        m, _ = self.extract_with(self.MAPPING)
        self.assertEqual(list(m), ["definitions", "values", "warnings", "provenance"])

    def test_cli_prints_the_preview(self):
        self.write_mapping(self.MAPPING)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = extract.main(["extract.py", self.dir, "metafields"])
        self.assertEqual(code, 0)
        lines = out.getvalue().splitlines()
        self.assertTrue(any(line.startswith("  custom.poids_net 'Poids net (kg)' (number_decimal") for line in lines))
        self.assertTrue(any(line.startswith("conflict: ") for line in lines))
        self.assertEqual(lines[-1], "2 metafield definitions")


if __name__ == "__main__":
    unittest.main()
