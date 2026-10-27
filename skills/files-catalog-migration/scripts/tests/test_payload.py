import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import payload  # noqa: E402
import state  # noqa: E402

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "payload.py")
MARKER = "/api/metafield_definitions/00000000-0000-4000-8000-000000000001"
MATERIAL = "/api/metafield_definitions/00000000-0000-4000-8000-000000000002"


def make_products():
    return {"products": [
        {"key": "2226", "handle": "2226", "title": "Grille-pain 2 fentes", "description_html": "<p>Inox\n2 fentes</p>",
         "tags": ["cuisine", "petit-electromenager"], "status": None, "options": [],
         "variants": [{"sku": "GP-2226", "barcode": "3760001112226", "option_values": []}],
         "metafields": {"custom.material": "Inox"}},
        {"key": "2230", "handle": "2230", "title": "Cafetiere filtre", "description_html": "", "tags": [],
         "status": None, "options": [{"name": "Couleur", "values": ["Noir", "Rouge"]}],
         "variants": [{"sku": "CF-2230-N", "barcode": "3760001112230", "option_values": ["Noir"]},
                      {"sku": "CF-2230-R", "barcode": "3760001112231", "option_values": ["Rouge"]}],
         "metafields": {}},
    ]}


def make_state(products, mode="draft"):
    st = state.init("acme", "u", products)
    state.decide(st, "status_mode", mode, "developer")
    state.record_shared(st, "metafield_definition:import.key", MARKER, "created", "core")
    return st


class PayloadTest(unittest.TestCase):
    def setUp(self):
        self.products = make_products()
        self.state = make_state(self.products)

    def test_product_body(self):
        body = payload.build(self.products, self.state, "2226")["product"]["body"]
        self.assertEqual(body["title"], "Grille-pain 2 fentes")
        self.assertEqual(body["tags"], "cuisine,petit-electromenager")
        self.assertEqual(body["status"], "/api/statuses/DRAFT")
        self.assertIn("\n", body["description"])

    def test_empty_fields_are_omitted(self):
        body = payload.build(self.products, self.state, "2230")["product"]["body"]
        self.assertNotIn("description", body)
        self.assertNotIn("tags", body)

    def test_marker_is_the_handle(self):
        payload_ = payload.build(self.products, self.state, "2230")
        self.assertEqual(payload_["marker"], {"shared": "metafield_definition:import.key",
                                              "body": {"definition": MARKER, "value": "2230"}})
        self.assertEqual(payload_["variants"][1]["option_values"], ["Rouge"])

    def test_status_modes(self):
        state.decide(self.state, "status_mode", "active", "developer")
        self.assertEqual(payload.build(self.products, self.state, "2226")["product"]["body"]["status"],
                         "/api/statuses/ACTIVE")
        state.decide(self.state, "status_mode", "source", "developer")
        with self.assertRaises(payload.PayloadError):
            payload.build(self.products, self.state, "2226")

    def test_source_status_uses_the_status_map(self):
        st = make_state(self.products, mode="source")
        self.products["products"][0]["status"] = "Archived"
        self.assertEqual(payload.build(self.products, st, "2226")["product"]["body"]["status"],
                         "/api/statuses/ARCHIVED")
        self.products["products"][0]["status"] = "unlisted"
        with self.assertRaises(payload.PayloadError) as error:
            payload.build(self.products, st, "2226")
        self.assertIn("source status 'unlisted' has no mapping", str(error.exception))
        state.decide(st, "status_map", {"unlisted": "DRAFT"}, "developer")
        self.assertEqual(payload.build(self.products, st, "2226")["product"]["body"]["status"],
                         "/api/statuses/DRAFT")

    def test_missing_status_mode_is_an_error(self):
        del self.state["decisions"]["status_mode"]
        with self.assertRaises(payload.PayloadError):
            payload.build(self.products, self.state, "2226")

    def test_missing_shared_iri_is_an_error_unless_dry_run(self):
        del self.state["shared"]["metafield_definition:import.key"]
        with self.assertRaises(payload.PayloadError):
            payload.build(self.products, self.state, "2226")
        marker = payload.build(self.products, self.state, "2226", placeholders=True)["marker"]
        self.assertEqual(marker["body"]["definition"], "<metafield_definition:import.key IRI>")

    def test_options_and_variants(self):
        result = payload.build(self.products, self.state, "2230")
        self.assertEqual(result["options"], [{"title": "Couleur", "position": 0, "values": [
            {"value": "Noir", "position": 0}, {"value": "Rouge", "position": 1}]}])
        self.assertEqual(result["variants"][1], {"sku": "CF-2230-R", "barcode": "3760001112231",
                                                 "option_values": ["Rouge"]})
        self.assertEqual(payload.build(self.products, self.state, "2226")["options"], [])

    def test_step_filters_sections(self):
        result = payload.build(self.products, self.state, "2226", step="variants")
        self.assertEqual(set(result), {"handle", "variants"})

    def test_metafields_follow_the_decision(self):
        self.assertEqual(payload.build(self.products, self.state, "2226")["metafields"], [])
        state.decide(self.state, "metafields", {"definitions": {
            "custom.material": {"type": "single_line_text_field", "transform": None},
            "import.key": {"type": "single_line_text_field"}}}, "developer")
        with self.assertRaises(payload.PayloadError):
            payload.build(self.products, self.state, "2226")
        state.record_shared(self.state, "metafield_definition:custom.material", MATERIAL, "created", "metafields")
        metafields = payload.build(self.products, self.state, "2226")["metafields"]
        self.assertEqual(metafields, [{"key": "custom.material", "shared": "metafield_definition:custom.material",
                                       "body": {"definition": MATERIAL, "value": "Inox"}}])
        self.assertEqual(payload.build(self.products, self.state, "2230")["metafields"], [])

    def test_renamed_metafield_reads_its_source(self):
        state.decide(self.state, "metafields", {"definitions": {"custom.fabric": {
            "type": "single_line_text_field", "source": "custom.material"}}}, "developer")
        state.record_shared(self.state, "metafield_definition:custom.fabric", MATERIAL, "created", "metafields")
        (entry,) = payload.build(self.products, self.state, "2226")["metafields"]
        self.assertEqual((entry["key"], entry["body"]["value"]), ("custom.fabric", "Inox"))

    def test_module_decisions_must_be_machine_readable(self):
        state.decide(self.state, "modules", ["core", "taxonomy"], "developer")
        with self.assertRaises(payload.PayloadError):
            payload.build(self.products, self.state, "2226")
        state.decide(self.state, "taxonomy", {"tree": "free text"}, "developer")
        with self.assertRaises(payload.PayloadError):
            payload.build(self.products, self.state, "2226")

    def test_metafield_transforms(self):
        self.assertEqual(payload.transform_value('["a", "b"]', "list_json"), '["a", "b"]')
        self.assertEqual(payload.transform_value("a; b\nc", "list"), ["a", "b", "c"])
        self.assertEqual(payload.transform_value("12", "number"), 12)
        self.assertEqual(payload.transform_value("1,5", "number"), 1.5)
        self.assertEqual(payload.transform_value('{"a": 1}', "json"), {"a": 1})
        self.assertEqual(payload.transform_value("<p>A &amp; B</p><p>C<br>D</p>", "strip_html"), "A & B\nC\nD")
        self.assertEqual(payload.transform_value("as is", None), "as is")
        for value, transform in (("x", "number"), ("{", "json"), ("x", "upper")):
            with self.assertRaises(payload.PayloadError):
                payload.transform_value(value, transform)

    def test_taxonomy_fields(self):
        decision = {"categories": [{"path": "Cuisine", "name": "Cuisine", "parent": None}],
                    "products": {"2226": "Cuisine"},
                    "attributes": [{"code": "fit", "name": "Fit", "type": "list", "multiple": True,
                                    "categories": ["Cuisine"], "options": [{"value": "Slim"}, {"value": "Loose"}],
                                    "values": {"2226": ["Slim", "Loose"]}},
                                   {"code": "care", "name": "Care", "type": "string", "multiple": False,
                                    "categories": ["Cuisine"], "values": {"2226": ["30C"]}}]}
        state.decide(self.state, "taxonomy", decision, "developer")
        with self.assertRaises(payload.PayloadError):
            payload.build(self.products, self.state, "2226")
        dry = payload.build(self.products, self.state, "2226", placeholders=True)["product"]["body"]
        self.assertEqual(dry["category"], "<category:Cuisine IRI>")
        shared = {"category:Cuisine": "/api/categories/c", "attribute_definition:fit": "/api/attribute_definitions/f",
                  "attribute_option:fit:Slim": "/api/attribute_options/s",
                  "attribute_option:fit:Loose": "/api/attribute_options/l",
                  "attribute_definition:care": "/api/attribute_definitions/c"}
        for key, value in shared.items():
            state.record_shared(self.state, key, value, "created", "taxonomy")
        fields = payload.build(self.products, self.state, "2226", step="taxonomy")["taxonomy"]
        self.assertEqual(fields, {"category": "/api/categories/c", "attributes": [
            {"definition": "/api/attribute_definitions/f", "option": "/api/attribute_options/s"},
            {"definition": "/api/attribute_definitions/f", "option": "/api/attribute_options/l"},
            {"definition": "/api/attribute_definitions/c", "value": "30C"}]})
        self.assertNotIn("category", payload.build(self.products, self.state, "2230")["product"]["body"])

    def test_unknown_handle(self):
        with self.assertRaises(payload.PayloadError):
            payload.build(self.products, self.state, "nope")

    def test_special_characters_escaped(self):
        self.products["products"][0]["description_html"] = "<p>5 €</p>"
        self.assertIn("\\u00a0", payload.dumps(payload.build(self.products, self.state, "2226")))

    def test_dumps_escapes_invisible_characters_only(self):
        value = "Café crème !  ​­﻿ \x7f ’"
        text = payload.dumps({"v": value})
        self.assertIn("Café", text)
        self.assertIn("’", text)
        for escape in ("\\u00a0", "\\u202f", "\\u200b", "\\u00ad", "\\ufeff", "\\u2009", "\\u007f"):
            self.assertIn(escape, text)
        for char in "  ​­﻿ \x7f":
            self.assertNotIn(char, text)
        self.assertEqual(json.loads(text)["v"], value)

    def test_dumps_keeps_newlines_valid(self):
        data = {"a": "line 1\nline 2\ttab", "b": [1, 2]}
        self.assertEqual(json.loads(payload.dumps(data)), data)

    def write_dir(self, tmp):
        for name, data in (("products.json", self.products), ("state.json", self.state)):
            with open(os.path.join(tmp, name), "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)

    def test_cli(self):
        self.products["products"][0]["title"] = "Grille-pain XL"
        with tempfile.TemporaryDirectory() as tmp:
            self.write_dir(tmp)
            result = subprocess.run([sys.executable, SCRIPT, tmp, "2226", "--step", "product"],
                                    capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('"Grille-pain\\u00a0XL"', result.stdout)
            missing = subprocess.run([sys.executable, SCRIPT, tmp, "nope"], capture_output=True, text=True)
            self.assertEqual(missing.returncode, 1)
            self.assertIn("nope", missing.stderr)

    def test_cli_dry_run_placeholder(self):
        del self.state["shared"]["metafield_definition:import.key"]
        with tempfile.TemporaryDirectory() as tmp:
            self.write_dir(tmp)
            result = subprocess.run([sys.executable, SCRIPT, tmp, "2230", "--dry-run"],
                                    capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(result.returncode, 0, result.stderr)
            out = json.loads(result.stdout)
            self.assertEqual(out["marker"]["body"]["definition"], "<metafield_definition:import.key IRI>")
            self.assertEqual(out["marker"]["body"]["value"], "2230")


if __name__ == "__main__":
    unittest.main()
