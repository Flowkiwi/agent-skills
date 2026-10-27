import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import catalog  # noqa: E402


def product(handle, title="T", variants=None, options=None, status=None):
    return {"handle": handle, "row": 3, "title": title, "status": status, "options": options or [],
            "variants": variants or [{"sku": "S", "sku_generated": False, "barcode": "B", "barcode_generated": False,
                                      "option_values": [], "price": "1.00"}]}


class CatalogTest(unittest.TestCase):
    def test_too_long_is_reported_not_cut(self):
        data = {"products": [product("a", title="x" * 300)], "warnings": []}
        warnings = catalog.validate(data)
        self.assertEqual([w["code"] for w in warnings], ["too_long"])
        self.assertEqual(warnings[0]["field"], "title")
        self.assertIn("300 > 255", warnings[0]["message"])
        self.assertEqual(len(data["products"][0]["title"]), 300)

    def test_duplicate_option_values_and_sku(self):
        v = {"sku": "S", "sku_generated": False, "barcode": "B", "barcode_generated": False, "option_values": ["Noir"], "price": "1"}
        data = {"products": [product("a", variants=[v, dict(v)], options=[{"name": "Couleur", "values": ["Noir"]}])], "warnings": []}
        self.assertEqual(sorted(w["code"] for w in catalog.validate(data)), ["duplicate_option_values", "duplicate_sku"])

    def test_refresh_keeps_extract_warnings(self):
        data = {"products": [product("a")], "warnings": [{"row": 3, "handle": "a", "code": "conflict", "message": "m", "stage": "extract"},
                                                          {"row": 3, "handle": "a", "code": "missing_sku", "message": "m", "stage": "validate"}]}
        self.assertEqual([w["code"] for w in catalog.refresh(data)["warnings"]], ["conflict"])

    def test_blocking_status_source(self):
        data = {"products": [product("a", status="en ligne")], "warnings": []}
        catalog.refresh(data)
        self.assertEqual(catalog.blocking(data, {"status_mode": "draft"}), [])
        self.assertEqual([w["code"] for w in catalog.blocking(data, {"status_mode": "source"})], ["unknown_status"])
        self.assertEqual(catalog.blocking(data, {"status_mode": "source", "status_map": {"en ligne": "ACTIVE"}}), [])

    def test_non_sellable(self):
        p = product("a"); p["variants"][0].update(price=None, sku="X", sku_generated=True)
        self.assertTrue(catalog.is_non_sellable(p))

    def test_sellable_with_price_or_source_sku(self):
        priced = product("a")
        self.assertFalse(catalog.is_non_sellable(priced))
        with_sku = product("b"); with_sku["variants"][0].update(price="0")
        self.assertFalse(catalog.is_non_sellable(with_sku))
        zero = product("c"); zero["variants"][0].update(price="0", sku_generated=True)
        self.assertTrue(catalog.is_non_sellable(zero))

    def test_missing_fields_and_short_option_name(self):
        v = {"sku": None, "sku_generated": False, "barcode": None, "barcode_generated": False, "option_values": ["M"], "price": "1"}
        data = {"products": [product("a", title="", variants=[v], options=[{"name": "Ta", "values": ["M"]}])], "warnings": []}
        codes = sorted(w["code"] for w in catalog.validate(data))
        self.assertEqual(codes, ["missing_barcode", "missing_sku", "missing_title", "short_option_name"])

    def test_option_name_case_and_stage(self):
        opt = lambda name: [{"name": name, "values": ["x"]}]
        data = {"products": [product("a", options=opt("Couleur")), product("b", options=opt("Couleur")),
                             product("c", options=opt("couleur"))], "warnings": []}
        warnings = catalog.validate(data)
        self.assertEqual([w["code"] for w in warnings if w["code"] == "option_name_case"], ["option_name_case"])
        self.assertTrue(all(w["stage"] == "validate" for w in warnings))

    def test_blocking_codes(self):
        data = {"products": [product("a")],
                "warnings": [{"row": 3, "handle": "a", "code": "duplicate_key", "message": "m", "stage": "extract"},
                             {"row": 3, "handle": "a", "code": "non_sellable", "message": "m", "stage": "validate"}]}
        self.assertEqual([w["code"] for w in catalog.blocking(data)], ["duplicate_key"])

    def test_format_warnings_non_sellable_first(self):
        warnings = [{"row": 3, "handle": "a", "code": "missing_sku", "message": "m1", "stage": "validate"},
                    {"row": 4, "handle": "b", "code": "missing_sku", "message": "m2", "stage": "validate"},
                    {"row": 5, "handle": "c", "code": "non_sellable", "message": "m3", "stage": "validate"}]
        lines = catalog.format_warnings(warnings).splitlines()
        self.assertEqual(lines[0], "non_sellable (1)")
        self.assertIn("missing_sku (2)", lines)
        self.assertIn("  row 3 a: m1", lines)


class CatalogCliTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = os.path.join(tmp.name, "products.json")
        self.state = os.path.join(tmp.name, "state.json")

    def write(self, path, data):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)

    def run_main(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = catalog.main(["catalog.py"] + list(argv))
        return code, out.getvalue()

    def test_validate_writes_and_exits_zero(self):
        v = {"sku": None, "sku_generated": False, "barcode": "B", "barcode_generated": False, "option_values": [], "price": "1"}
        self.write(self.path, {"products": [product("a", variants=[v])], "warnings": []})
        code, out = self.run_main("--validate", self.path)
        self.assertEqual(code, 0)
        self.assertIn("missing_sku (1)", out)
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual([w["code"] for w in json.load(f)["warnings"]], ["missing_sku"])

    def test_check_uses_state_decisions(self):
        self.write(self.path, {"products": [product("a", status="en ligne")], "warnings": []})
        self.write(self.state, {"decisions": {"status_mode": "source"}})
        code, out = self.run_main("--check", self.path, "--state", self.state)
        self.assertEqual(code, 1)
        self.assertIn("unknown_status (1)", out)
        self.write(self.state, {"decisions": {"status_mode": "draft"}})
        code, _ = self.run_main("--check", self.path, "--state", self.state)
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
