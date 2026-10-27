import csv
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import metafields  # noqa: E402
import shopify_csv  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "products.csv")
SCRIPT = os.path.join(os.path.dirname(__file__), "..", "metafields.py")


def data(columns, *values):
    return {"metafield_columns": columns,
            "products": [{"handle": f"p{i}", "metafields": v} for i, v in enumerate(values)]}


class SuggestTypeTest(unittest.TestCase):
    def check(self, values, expected):
        self.assertEqual(metafields.suggest_type(values)[0], expected)

    def test_types(self):
        self.check(["12", "-3"], "number_integer")
        self.check(["12", "4.5"], "number_decimal")
        self.check(['["a","b"]', '["c"]'], "list__single_line_text_field")
        self.check(['{"a": 1}', "[1, 2]"], "json")
        self.check(["line 1\nline 2", "x"], "multi_line_text_field")
        self.check(["Short"], "single_line_text_field")
        self.check(["x" * 300], "multi_line_text_field")

    def test_html_and_rich_text_have_notes(self):
        kind, note = metafields.suggest_type(["<p>Hello</p>"])
        self.assertEqual(kind, "multi_line_text_field")
        self.assertIn("HTML", note)
        kind, note = metafields.suggest_type(['{"type": "root", "children": []}'])
        self.assertEqual(kind, "multi_line_text_field")
        self.assertIn("rich text", note)


class InferTest(unittest.TestCase):
    def test_infer_uses_labels_and_counts(self):
        result = metafields.infer(data(
            {"custom.ingredients": "Ingredients", "custom.volume": "Volume"},
            {"custom.ingredients": "Sugar\nWater", "custom.volume": "70"},
            {"custom.ingredients": "Sugar", "custom.volume": "100"},
            {},
        ))
        self.assertEqual(result, [
            {"namespace_key": "custom.ingredients", "name": "Ingredients", "values": 2,
             "type": "multi_line_text_field", "note": None},
            {"namespace_key": "custom.volume", "name": "Volume", "values": 2, "type": "number_integer", "note": None},
        ])

    def test_old_products_json_without_columns(self):
        old = {"products": [{"handle": "a", "metafields": {"custom.material": "Cotton"}}]}
        self.assertEqual(metafields.infer(old)[0]["name"], "custom.material")

    def test_fixture(self):
        result = metafields.infer(shopify_csv.parse(FIXTURE))
        self.assertEqual([(r["namespace_key"], r["name"], r["values"]) for r in result],
                         [("custom.material", "Material", 1)])


class DiffTest(unittest.TestCase):
    def test_diff(self):
        definitions = [
            {"owner": "product", "namespace_key": "custom.material", "name": "Material", "type": "single_line_text_field"},
            {"owner": "PRODUCT", "namespace_key": "custom.specs", "name": "Specs", "type": "json"},
            {"owner": "variant", "namespace_key": "custom.size", "name": "Size", "type": "single_line_text_field"},
        ]
        result = metafields.diff(data({"custom.material": "Material", "custom.color": "Color"}), definitions)
        self.assertEqual([d["namespace_key"] for d in result["definitions_without_column"]], ["custom.specs"])
        self.assertEqual(result["columns_without_definition"], [{"namespace_key": "custom.color", "name": "Color"}])
        self.assertEqual([d["namespace_key"] for d in result["other_owner_definitions"]], ["custom.size"])


class MetafieldsCliTest(unittest.TestCase):
    def test_infer_output_and_diff(self):
        with tempfile.TemporaryDirectory() as tmp:
            products_path = os.path.join(tmp, "products.json")
            output = os.path.join(tmp, "metafield-definitions.csv")
            with open(products_path, "w", encoding="utf-8") as f:
                json.dump(shopify_csv.parse(FIXTURE), f)
            result = subprocess.run([sys.executable, SCRIPT, "infer", products_path, "--output", output],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            with open(output, encoding="utf-8", newline="") as f:
                rows = list(csv.reader(f))
            self.assertEqual(rows, [["owner", "namespace_key", "name", "type"],
                                    ["product", "custom.material", "Material", "single_line_text_field"]])
            result = subprocess.run([sys.executable, SCRIPT, "diff", products_path, output],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["columns_without_definition"], [])


if __name__ == "__main__":
    unittest.main()
