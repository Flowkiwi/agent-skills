import copy
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import codes  # noqa: E402

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "codes.py")


def variant(sku, barcode, option_values, price):
    return {"sku": sku, "sku_generated": False, "barcode": barcode, "barcode_generated": False,
            "option_values": option_values, "price": price, "compare_at_price": None, "cost": None, "grams": None,
            "taxable": None, "market_prices": {}}


def product(handle, row, title, variants, options=None, metafields=None):
    return {"handle": handle, "key": handle, "row": row, "rows": {"main": [row]}, "title": title,
            "description_html": None, "description_origin": None, "vendor": "Acme", "type": None,
            "product_category": None, "tags": [], "status": None, "published": None, "options": options or [],
            "variants": variants, "images": [], "metafields": metafields or {}}


def sample_products():
    return {"version": 1, "warnings": [], "cleaning": {}, "sources": {}, "metafield_columns": {}, "products": [
        product("tshirt-basic", 3, "T-shirt basic",
                [variant("TS-S", None, ["S"], "19.90"), variant("TS-M", "4006381333931", ["M"], "19.90")],
                [{"name": "Size", "values": ["S", "M"]}], {"custom.material": "Cotton"}),
        product("mug", 5, "Mug", [variant("MUG-1", "4006381333924", [], "9.00")]),
        product("poster", 6, "Poster", [variant(None, None, ["A3"], "15.00")], [{"name": "Sz", "values": ["A3"]}]),
        product("cap", 7, "Cap", [variant("TS-S", "4006381333917", [], "14.00")]),
    ]}


def variants(data, handle):
    return next(p for p in data["products"] if p["handle"] == handle)["variants"]


def by_handle(data, handle):
    return next(p for p in data["products"] if p["handle"] == handle)


class CodesTest(unittest.TestCase):
    def setUp(self):
        self.data = sample_products()

    def test_check_digit_known_value(self):
        self.assertEqual(codes.ean13_check_digit("400638133393"), "1")

    def test_generate_prefix_and_length(self):
        generated = codes.generate_barcodes("2", 1, 2, set())
        self.assertEqual(len(generated), 2)
        for barcode in generated:
            self.assertEqual(len(barcode), 13)
            self.assertTrue(barcode.startswith("2"))
        self.assertEqual(generated[0], "200000000001" + codes.ean13_check_digit("200000000001"))

    def test_generate_skips_used(self):
        first = codes.generate_barcodes("2", 1, 1, set())[0]
        generated = codes.generate_barcodes("2", 1, 1, {first})
        self.assertNotEqual(generated[0], first)

    def test_fill_only_missing(self):
        codes.fill(self.data, "2", 1, None)
        tshirt = variants(self.data, "tshirt-basic")
        self.assertTrue(tshirt[0]["barcode"].startswith("2"))
        self.assertTrue(tshirt[0]["barcode_generated"])
        self.assertEqual(tshirt[1]["barcode"], "4006381333931")
        self.assertFalse(tshirt[1]["barcode_generated"])

    def test_fill_never_reuses_existing_barcodes(self):
        taken = "200000000001" + codes.ean13_check_digit("200000000001")
        variants(self.data, "mug")[0]["barcode"] = taken
        codes.fill(self.data, "2", 1, None)
        all_barcodes = [v["barcode"] for p in self.data["products"] for v in p["variants"]]
        self.assertEqual(len(all_barcodes), len(set(all_barcodes)))

    def test_fill_sku_pattern(self):
        codes.fill(self.data, "2", 1, "{handle}-{index}")
        poster = variants(self.data, "poster")[0]
        self.assertEqual(poster["sku"], "POSTER-1")
        self.assertTrue(poster["sku_generated"])

    def test_fill_without_sku_pattern_leaves_sku_null(self):
        codes.fill(self.data, "2", 1, None)
        self.assertIsNone(variants(self.data, "poster")[0]["sku"])

    def test_fill_summary(self):
        summary = codes.fill(self.data, "2", 1, "{handle}-{index}")
        self.assertEqual(summary["barcodes"], 2)
        self.assertEqual(summary["skus"], 1)
        self.assertEqual(summary["next_barcode_start"], 3)

    def test_fill_is_idempotent(self):
        codes.fill(self.data, "2", 1, "{handle}-{index}")
        snapshot = copy.deepcopy(self.data)
        summary = codes.fill(self.data, "2", 1, "{handle}-{index}")
        self.assertEqual(summary["barcodes"], 0)
        self.assertEqual(summary["skus"], 0)
        self.assertEqual(self.data, snapshot)

    def test_preview_does_not_modify_and_lists_samples(self):
        snapshot = copy.deepcopy(self.data)
        preview = codes.preview(self.data, "2", 1, "{handle}-{index}")
        self.assertEqual(self.data, snapshot)
        self.assertEqual(preview["barcodes"], 2)
        self.assertEqual(preview["skus"], 1)
        self.assertIn({"handle": "poster", "variant": 1, "sku": "POSTER-1"}, preview["sku_samples"])
        self.assertEqual(len(preview["barcode_samples"]), 2)

    def test_apply_fixes(self):
        fixes = {"skus": {"cap": {"TS-S": "TS-S-2"}}, "option_names": {"poster": {"Sz": "Size"}}, "skip": ["mug"]}
        codes.apply_fixes(self.data, fixes)
        codes.apply_fixes(self.data, fixes)
        self.assertEqual(variants(self.data, "cap")[0]["sku"], "TS-S-2")
        self.assertEqual(variants(self.data, "tshirt-basic")[0]["sku"], "TS-S")
        self.assertEqual(by_handle(self.data, "poster")["options"][0]["name"], "Size")
        self.assertNotIn("mug", [p["handle"] for p in self.data["products"]])

    def test_fields_fix(self):
        fixes = {"skus": {"cap": {"TS-S": "CAP-1"}},
                 "fields": {"cap": {"title": "Casquette", "tags": ["été"], "status": "draft"},
                            "mug": {"description_html": "<p>Mug en céramique.</p>", "vendor": "Atelier",
                                    "product_category": "Cuisine"},
                            "gone": {"title": "Ignored"}}}
        codes.apply_fixes(self.data, fixes)
        cap, mug = by_handle(self.data, "cap"), by_handle(self.data, "mug")
        self.assertEqual((cap["title"], cap["tags"], cap["status"]), ("Casquette", ["été"], "draft"))
        self.assertEqual(cap["variants"][0]["sku"], "CAP-1")
        self.assertEqual((mug["description_html"], mug["vendor"], mug["product_category"]),
                         ("<p>Mug en céramique.</p>", "Atelier", "Cuisine"))
        with self.assertRaises(ValueError):
            codes.apply_fixes(self.data, {"fields": {"mug": {"handle": "cup"}}})

    def test_fields_fix_value_types(self):
        for value in ({"tags": "a,b"}, {"tags": ["a", 2]}, {"title": 12}, {"status": ["draft"]}):
            with self.assertRaises(ValueError, msg=value) as error:
                codes.apply_fixes(copy.deepcopy(self.data), {"fields": {"cap": value}})
            self.assertIn("cap", str(error.exception))
        codes.apply_fixes(self.data, {"fields": {"cap": {"tags": [], "vendor": None, "description_html": "<p>x</p>"}}})
        cap = by_handle(self.data, "cap")
        self.assertEqual((cap["tags"], cap["vendor"], cap["description_origin"]), ([], None, "fixed"))

    def test_carry_is_public(self):
        previous = copy.deepcopy(self.data)
        codes.fill(previous, "2", 1, "{handle}-{index}")
        fresh = sample_products()
        self.assertEqual(codes.carry(fresh, previous), 3)
        self.assertEqual(variants(fresh, "poster")[0]["sku"], "POSTER-1")
        self.assertTrue(variants(fresh, "poster")[0]["sku_generated"])

    def test_fill_carries_previous_generated_codes(self):
        previous = copy.deepcopy(self.data)
        codes.fill(previous, "2", 1, "{handle}-{index}")
        fresh = sample_products()
        summary = codes.fill(fresh, "2", 50, "{handle}-{index}", previous=previous)
        self.assertEqual(variants(fresh, "tshirt-basic")[0]["barcode"], variants(previous, "tshirt-basic")[0]["barcode"])
        self.assertTrue(variants(fresh, "tshirt-basic")[0]["barcode_generated"])
        self.assertEqual(summary["barcodes"], 0)
        self.assertEqual(summary["carried"], 3)

    def test_skip_rules(self):
        recipe = {"handle": "recipe-cake", "row": 9, "title": "Cake", "type": None, "options": [],
                  "metafields": {"custom.recipe_steps": "Mix"},
                  "variants": [{"sku": None, "sku_generated": False, "barcode": None, "price": "0",
                                "option_values": []}]}
        self.data["products"].append(recipe)
        stats = {}
        codes.apply_fixes(self.data, {"skip_rules": [{"non_sellable": True}, {"handle_pattern": "^mug$"}]}, stats)
        handles = [p["handle"] for p in self.data["products"]]
        self.assertNotIn("recipe-cake", handles)
        self.assertNotIn("mug", handles)
        self.assertEqual(stats, {'{"non_sellable": true}': 1, '{"handle_pattern": "^mug$"}': 1})

    def test_generated_sku_does_not_make_sellable(self):
        product = {"handle": "note", "metafields": {},
                   "variants": [{"sku": "NOTE-1", "sku_generated": True, "barcode": None, "price": None}]}
        self.assertTrue(codes.rule_matches(product, {"non_sellable": True}))

    def test_skip_rule_metafield_prefix(self):
        self.assertTrue(codes.rule_matches({"handle": "x", "metafields": {"custom.recipe_a": "1"}},
                                           {"metafield_prefix": "custom.recipe_"}))
        self.assertFalse(codes.rule_matches({"handle": "x", "metafields": {"custom.recipe_a": None}},
                                            {"metafield_prefix": "custom.recipe_"}))

    def test_skip_rule_conditions_are_combined(self):
        self.assertFalse(codes.rule_matches({"handle": "recipe-a", "metafields": {}},
                                            {"handle_pattern": "^recipe-", "metafield_prefix": "custom."}))

    def test_unknown_skip_rule(self):
        with self.assertRaises(ValueError):
            codes.apply_fixes(self.data, {"skip_rules": [{"vendor": "Acme"}]})

    def test_option_names_wildcard(self):
        codes.apply_fixes(self.data, {"option_names": {"*": {"Size": "Taille"}, "poster": {"Sz": "Format"}}})
        self.assertEqual(by_handle(self.data, "tshirt-basic")["options"][0]["name"], "Taille")
        self.assertEqual(by_handle(self.data, "poster")["options"][0]["name"], "Format")


class CodesCliTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.products_path = os.path.join(tmp.name, "products.json")
        self.fixes_path = os.path.join(tmp.name, "fixes.json")
        with open(self.products_path, "w", encoding="utf-8") as f:
            json.dump(sample_products(), f)

    def run_codes(self, *args):
        return subprocess.run([sys.executable, SCRIPT] + list(args), capture_output=True, text=True)

    def read(self):
        with open(self.products_path, encoding="utf-8") as f:
            return json.load(f)

    def test_fix_cli_prints_rule_counts(self):
        with open(self.fixes_path, "w") as f:
            json.dump({"skip_rules": [{"handle_pattern": "^(mug|cap)$"}]}, f)
        result = self.run_codes("fix", self.products_path, self.fixes_path)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('skipped 2 products by rule {"handle_pattern": "^(mug|cap)$"}', result.stdout)

    def test_preview_cli_leaves_the_file(self):
        before = self.read()
        result = self.run_codes("preview", self.products_path, "--prefix", "2", "--start", "1", "--sku-pattern", "{handle}-{index}")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["skus"], 1)
        self.assertEqual(self.read(), before)

    def test_fill_cli_writes_with_a_normal_mode(self):
        os.chmod(self.products_path, 0o664)
        result = self.run_codes("fill", self.products_path, "--prefix", "2", "--start", "1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["barcodes"], 2)
        self.assertTrue(variants(self.read(), "poster")[0]["barcode_generated"])
        self.assertEqual(stat.S_IMODE(os.stat(self.products_path).st_mode), 0o664)


if __name__ == "__main__":
    unittest.main()
