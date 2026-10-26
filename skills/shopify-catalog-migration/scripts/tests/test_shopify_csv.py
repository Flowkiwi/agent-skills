import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import shopify_csv  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "products.csv")


class ShopifyCsvTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = shopify_csv.parse(FIXTURE)
        cls.by_handle = {p["handle"]: p for p in cls.data["products"]}

    def test_groups_rows_by_handle(self):
        self.assertEqual(
            [p["handle"] for p in self.data["products"]],
            ["tshirt-basic", "mug", "poster", "cap"],
        )

    def test_image_only_row_adds_image_not_variant(self):
        tshirt = self.by_handle["tshirt-basic"]
        self.assertEqual(len(tshirt["variants"]), 2)
        self.assertIn("https://cdn.shopify.com/ts-3.jpg", tshirt["images"])

    def test_multiline_body_preserved(self):
        self.assertIn("\n", self.by_handle["tshirt-basic"]["description_html"])

    def test_default_title_gives_no_options(self):
        mug = self.by_handle["mug"]
        self.assertEqual(mug["options"], [])
        self.assertEqual(len(mug["variants"]), 1)
        self.assertEqual(mug["variants"][0]["option_values"], [])

    def test_options_and_values(self):
        tshirt = self.by_handle["tshirt-basic"]
        self.assertEqual(tshirt["options"], [{"name": "Size", "values": ["S", "M"]}])
        self.assertEqual([v["option_values"] for v in tshirt["variants"]], [["S"], ["M"]])

    def test_tags_split_and_trimmed(self):
        self.assertEqual(self.by_handle["tshirt-basic"]["tags"], ["cotton", "summer"])

    def test_status_lowercased(self):
        self.assertEqual(self.by_handle["mug"]["status"], "draft")

    def test_market_prices(self):
        variant = self.by_handle["tshirt-basic"]["variants"][0]
        self.assertEqual(variant["market_prices"]["France"]["price"], "19.90")

    def test_metafield_column_key(self):
        self.assertEqual(self.by_handle["tshirt-basic"]["metafields"], {"custom.material": "Cotton"})

    def test_empty_values_are_null(self):
        variant = self.by_handle["tshirt-basic"]["variants"][0]
        self.assertIsNone(variant["barcode"])
        self.assertIsNone(variant["compare_at_price"])

    def test_variant_fields(self):
        variant = self.by_handle["tshirt-basic"]["variants"][0]
        self.assertEqual(variant["sku"], "TS-S")
        self.assertEqual(variant["grams"], 200)
        self.assertEqual(variant["cost"], "6.00")
        self.assertTrue(variant["taxable"])
        self.assertFalse(variant["sku_generated"])
        self.assertFalse(variant["barcode_generated"])

    def test_published_flag(self):
        self.assertFalse(self.by_handle["poster"]["published"])

    def test_row_is_first_line_of_record(self):
        self.assertEqual(self.by_handle["tshirt-basic"]["row"], 2)
        self.assertEqual(self.by_handle["mug"]["row"], 6)

    def test_validate_recomputes_warnings_after_fixes(self):
        data = shopify_csv.parse(FIXTURE)
        cap = next(p for p in data["products"] if p["handle"] == "cap")
        cap["variants"][0]["sku"] = "CAP-1"
        warnings = shopify_csv.validate(data)
        self.assertNotIn("duplicate_sku", {w["code"] for w in warnings})
        self.assertIn("missing_sku", {w["code"] for w in warnings})

    def test_cli_prints_warnings_grouped_by_code(self):
        import subprocess, tempfile
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "products.json")
            script = os.path.join(os.path.dirname(__file__), "..", "shopify_csv.py")
            result = subprocess.run([sys.executable, script, FIXTURE, out], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("duplicate_sku (1)", result.stdout)
            self.assertIn("cap", result.stdout)
            again = subprocess.run([sys.executable, script, FIXTURE, out], capture_output=True, text=True)
            self.assertNotEqual(again.returncode, 0)

    def test_warnings(self):
        codes = {(w["code"], w["handle"]) for w in self.data["warnings"]}
        self.assertIn(("missing_barcode", "tshirt-basic"), codes)
        self.assertIn(("missing_sku", "poster"), codes)
        self.assertIn(("short_option_name", "poster"), codes)
        self.assertIn(("duplicate_sku", "cap"), codes)

    def test_metafield_column_labels(self):
        self.assertEqual(self.data["metafield_columns"], {"custom.material": "Material"})


def product(handle, price="0", sku=None, type_=None, status="active", options=(), row=2, **extra):
    data = {"handle": handle, "row": row, "title": handle, "type": type_, "status": status,
            "options": [{"name": n, "values": ["x"]} for n in options],
            "variants": [{"sku": sku, "sku_generated": False, "barcode": "1", "price": price,
                          "option_values": ["x"] * len(options)}], "metafields": {}}
    data.update(extra)
    return data


def codes_of(products, code):
    return [w for w in shopify_csv.validate({"products": products}) if w["code"] == code]


class ValidateWarningsTest(unittest.TestCase):
    def test_non_sellable(self):
        products = [product("recipe", price="0.00"), product("empty", price=None), product("sold", price="5"),
                    product("typed", type_="Syrup"), product("skued", sku="A-1")]
        self.assertEqual([w["handle"] for w in codes_of(products, "non_sellable")], ["recipe", "empty"])

    def test_generated_sku_does_not_make_sellable(self):
        recipe = product("recipe")
        recipe["variants"][0].update(sku="RECIPE-1", sku_generated=True)
        self.assertTrue(shopify_csv.is_non_sellable(recipe))

    def test_non_sellable_listed_once_per_product(self):
        recipe = product("recipe")
        recipe["variants"].append(dict(recipe["variants"][0]))
        self.assertEqual(len(codes_of([recipe], "non_sellable")), 1)

    def test_unknown_status(self):
        warnings = codes_of([product("a", status="unlisted"), product("b", status="draft"),
                             product("c", status=None)], "unknown_status")
        self.assertEqual([w["handle"] for w in warnings], ["a"])
        self.assertIn("unlisted", warnings[0]["message"])

    def test_option_name_case(self):
        products = [product("a", options=["Size"]), product("b", options=["Size"]),
                    product("c", options=["size"], row=9), product("d", options=["SIZE"]),
                    product("e", options=["Color"])]
        warnings = codes_of(products, "option_name_case")
        self.assertEqual([(w["handle"], w["row"]) for w in warnings], [("c", 9), ("d", 2)])
        self.assertIn("use 'Size'", warnings[0]["message"])
        self.assertIn("'Size': 2", warnings[0]["message"])

    def test_format_warnings_puts_non_sellable_first_with_hint(self):
        warnings = shopify_csv.validate({"products": [product("recipe")]})
        text = shopify_csv.format_warnings(warnings)
        self.assertTrue(text.startswith("non_sellable (1)"))
        self.assertIn("missing_sku", text.split("\n")[2])


if __name__ == "__main__":
    unittest.main()
