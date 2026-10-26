import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import payload  # noqa: E402
import shopify_csv  # noqa: E402
import state  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "products.csv")
SCRIPT = os.path.join(os.path.dirname(__file__), "..", "payload.py")
MARKER = "/api/metafield_definitions/00000000-0000-4000-8000-000000000001"
MATERIAL = "/api/metafield_definitions/00000000-0000-4000-8000-000000000002"


def make_state(products, mode="draft"):
    st = state.init("acme", "u", products)
    state.decide(st, "status_mode", mode)
    state.record_shared(st, "metafield_definition:shopify.handle", MARKER, "created", "core")
    return st


class PayloadTest(unittest.TestCase):
    def setUp(self):
        self.products = shopify_csv.parse(FIXTURE)
        self.state = make_state(self.products)

    def test_product_body(self):
        body = payload.build(self.products, self.state, "tshirt-basic")["product"]["body"]
        self.assertEqual(body["title"], "T-shirt basic")
        self.assertEqual(body["tags"], "cotton,summer")
        self.assertEqual(body["status"], "/api/statuses/DRAFT")
        self.assertIn("\n", body["description"])

    def test_empty_fields_are_omitted(self):
        body = payload.build(self.products, self.state, "poster")["product"]["body"]
        self.assertNotIn("description", body)
        self.assertNotIn("tags", body)

    def test_shopify_status_mode(self):
        st = make_state(self.products, mode="shopify")
        self.assertEqual(payload.build(self.products, st, "cap")["product"]["body"]["status"],
                         "/api/statuses/ARCHIVED")

    def test_missing_status_mode_is_an_error(self):
        del self.state["decisions"]["status_mode"]
        with self.assertRaises(payload.PayloadError):
            payload.build(self.products, self.state, "mug")

    def test_marker(self):
        marker = payload.build(self.products, self.state, "mug", step="product")["marker"]
        self.assertEqual(marker["body"], {"definition": MARKER, "value": "mug"})

    def test_missing_shared_iri_is_an_error_unless_dry_run(self):
        del self.state["shared"]["metafield_definition:shopify.handle"]
        with self.assertRaises(payload.PayloadError):
            payload.build(self.products, self.state, "mug")
        marker = payload.build(self.products, self.state, "mug", placeholders=True)["marker"]
        self.assertEqual(marker["body"]["definition"], "<metafield_definition:shopify.handle IRI>")

    def test_options_and_variants(self):
        result = payload.build(self.products, self.state, "tshirt-basic")
        self.assertEqual(result["options"], [{"title": "Size", "position": 0, "values": [
            {"value": "S", "position": 0}, {"value": "M", "position": 1}]}])
        self.assertEqual(result["variants"][1], {"sku": "TS-M", "barcode": "4006381333931", "option_values": ["M"]})

    def test_step_filters_sections(self):
        result = payload.build(self.products, self.state, "mug", step="variants")
        self.assertEqual(set(result), {"handle", "variants"})

    def test_metafields_need_a_recorded_definition(self):
        self.assertEqual(payload.build(self.products, self.state, "tshirt-basic")["metafields"], [])
        state.record_shared(self.state, "metafield_definition:custom.material", MATERIAL, "created", "metafields")
        metafields = payload.build(self.products, self.state, "tshirt-basic")["metafields"]
        self.assertEqual(metafields, [{"key": "custom.material", "shared": "metafield_definition:custom.material",
                                       "body": {"definition": MATERIAL, "value": "Cotton"}}])

    def test_unknown_handle(self):
        with self.assertRaises(payload.PayloadError):
            payload.build(self.products, self.state, "nope")

    def test_dumps_escapes_invisible_characters_only(self):
        text = payload.dumps({"v": "Café crème !​­﻿ \x7f ’"})
        self.assertIn("Café", text)
        self.assertIn("’", text)
        for escape in ("\\u00a0", "\\u202f", "\\u200b", "\\u00ad", "\\ufeff", "\\u2009", "\\u007f"):
            self.assertIn(escape, text)
        for char in "  ​­﻿ \x7f":
            self.assertNotIn(char, text)
        self.assertEqual(json.loads(text)["v"], "Café crème !​­﻿ \x7f ’")

    def test_dumps_keeps_newlines_valid(self):
        data = {"a": "line 1\nline 2\ttab", "b": [1, 2]}
        self.assertEqual(json.loads(payload.dumps(data)), data)

    def test_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.products["products"][0]["title"] = "T-shirt basic"
            for name, data in (("products.json", self.products), ("state.json", self.state)):
                with open(os.path.join(tmp, name), "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False)
            result = subprocess.run([sys.executable, SCRIPT, tmp, "tshirt-basic", "--step", "product"],
                                    capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('"T-shirt\\u00a0basic"', result.stdout)
            missing = subprocess.run([sys.executable, SCRIPT, tmp, "nope"], capture_output=True, text=True)
            self.assertEqual(missing.returncode, 1)
            self.assertIn("nope", missing.stderr)


if __name__ == "__main__":
    unittest.main()
