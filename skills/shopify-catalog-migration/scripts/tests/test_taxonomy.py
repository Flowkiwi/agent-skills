import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import taxonomy  # noqa: E402

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "taxonomy.py")
ROOT = "Food, Beverages & Tobacco > Beverages"


def product(handle, category=None, type_=None, tags=()):
    return {"handle": handle, "product_category": category, "type": type_, "tags": list(tags)}


PRODUCTS = [
    product("vanilla", f"{ROOT} > Syrups", "Syrup", ["format:gros-format", "format:petit-format", "summer"]),
    product("caramel", f"{ROOT} > Syrups", "Syrup", ["format:petit-format"]),
    product("espresso", f"{ROOT} > Coffee > Beans", "Coffee", ["format:Big Bag"]),
    product("gift-card", None, "Syrups"),
    product("mystery", None, None, ["format:petit-format"]),
]


class TargetPathTest(unittest.TestCase):
    def test_trimming_steps(self):
        p = PRODUCTS[2]
        self.assertEqual(taxonomy.target_path(p, "product_category"), f"{ROOT} > Coffee > Beans")
        self.assertEqual(taxonomy.target_path(p, "product_category", drop_root=True), "Beverages > Coffee > Beans")
        self.assertEqual(taxonomy.target_path(p, "product_category", drop_root=True, depth=2), "Beverages > Coffee")
        self.assertEqual(taxonomy.target_path(p, "product_category", drop_root=True, depth=2,
                                              renames={"Beverages": "Drinks"}), "Drinks > Coffee")

    def test_type_source_is_flat_and_map_applies(self):
        self.assertEqual(taxonomy.target_path(PRODUCTS[0], "type"), "Syrup")
        self.assertEqual(taxonomy.target_path(PRODUCTS[0], "type", mapping={"Syrup": "Drinks > Syrups"}),
                         "Drinks > Syrups")
        self.assertIsNone(taxonomy.target_path(PRODUCTS[0], "type", mapping={"Syrup": None}))


class BuildTest(unittest.TestCase):
    def setUp(self):
        self.result = taxonomy.build(PRODUCTS, "product_category", drop_root=True)

    def test_categories_parents_first(self):
        self.assertEqual(self.result["categories"], [
            {"path": "Beverages", "name": "Beverages", "parent": None},
            {"path": "Beverages > Coffee", "name": "Coffee", "parent": "Beverages"},
            {"path": "Beverages > Coffee > Beans", "name": "Beans", "parent": "Beverages > Coffee"},
            {"path": "Beverages > Syrups", "name": "Syrups", "parent": "Beverages"},
        ])
        self.assertEqual(self.result["products"]["vanilla"], "Beverages > Syrups")
        self.assertEqual(self.result["uncategorized"], ["gift-card", "mystery"])

    def test_tag_attributes(self):
        (definition,) = self.result["attributes"]
        self.assertEqual(definition["code"], "format")
        self.assertEqual(definition["name"], "Format")
        self.assertTrue(definition["multiple"])
        self.assertEqual(definition["categories"], ["Beverages"])
        self.assertEqual([o["value"] for o in definition["options"]], ["Gros format", "Petit format", "Big Bag"])
        self.assertEqual(definition["values"]["vanilla"], ["Gros format", "Petit format"])
        self.assertNotIn("mystery", definition["values"])
        self.assertTrue(any("mystery" in w for w in self.result["warnings"]))

    def test_single_value_attribute_and_filters(self):
        result = taxonomy.build(PRODUCTS[1:3], "product_category", drop_root=True, depth=2)
        self.assertFalse(result["attributes"][0]["multiple"])
        self.assertEqual(taxonomy.build(PRODUCTS, "type", attributes=False)["attributes"], [])
        self.assertEqual(taxonomy.build(PRODUCTS, "type", attribute_keys={"color"})["attributes"], [])

    def test_option_label(self):
        self.assertEqual(taxonomy.option_label("gros-format"), "Gros format")
        self.assertEqual(taxonomy.option_label("red"), "Red")
        self.assertEqual(taxonomy.option_label("Big Bag"), "Big Bag")

    def test_short_names_warned(self):
        result = taxonomy.build([product("a", "Toys > TV")], "product_category")
        self.assertTrue(any("'Toys > TV'" in w for w in result["warnings"]))

    def test_preview_counts_and_suggestions(self):
        text = taxonomy.format_preview(PRODUCTS, self.result)
        self.assertIn("Beverages (3)", text)
        self.assertIn("    Beans (1)", text)
        self.assertIn("gift-card: Beverages > Syrups (from type)", text)
        self.assertTrue(text.endswith("4 categories"))


class TaxonomyCliTest(unittest.TestCase):
    def test_preview_and_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            products_path = os.path.join(tmp, "products.json")
            output = os.path.join(tmp, "taxonomy.json")
            with open(products_path, "w", encoding="utf-8") as f:
                json.dump({"products": PRODUCTS}, f)
            args = [products_path, "--source", "product_category", "--drop-root", "--depth", "2",
                    "--rename", "Beverages > Syrups=Sirops"]
            preview = subprocess.run([sys.executable, SCRIPT, "preview", *args], capture_output=True, text=True)
            self.assertEqual(preview.returncode, 0, preview.stderr)
            self.assertIn("  Sirops (2)", preview.stdout)
            self.assertIn("3 categories", preview.stdout)
            build = subprocess.run([sys.executable, SCRIPT, "build", *args, "--output", output],
                                   capture_output=True, text=True)
            self.assertEqual(build.returncode, 0, build.stderr)
            with open(output, encoding="utf-8") as f:
                self.assertEqual(json.load(f)["products"]["vanilla"], "Beverages > Sirops")
            bad = subprocess.run([sys.executable, SCRIPT, "preview", products_path, "--source", "type",
                                  "--rename", "nope"], capture_output=True, text=True)
            self.assertEqual(bad.returncode, 1)


if __name__ == "__main__":
    unittest.main()
