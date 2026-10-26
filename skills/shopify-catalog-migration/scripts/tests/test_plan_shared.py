import json
import os
import subprocess
import sys
import tempfile
import unittest
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import plan_shared  # noqa: E402
import state  # noqa: E402
import taxonomy  # noqa: E402

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "plan_shared.py")


def iri(resource, name):
    return f"/api/{resource}/{uuid.uuid5(uuid.NAMESPACE_URL, resource + name)}"


def make_state():
    products = [
        {"handle": "vanilla", "product_category": "Drinks > Syrups", "tags": ["format:big"]},
        {"handle": "espresso", "product_category": "Drinks > Coffee", "tags": ["format:small"]},
    ]
    st = state.init("acme", "u", {"products": products})
    state.decide(st, "taxonomy", taxonomy.build(products, "product_category"))
    state.decide(st, "metafields", {"definitions": {"custom.ingredients": {"type": "multi_line_text_field"}}})
    return st


class PlanSharedTest(unittest.TestCase):
    def test_order_and_placeholders(self):
        entries = plan_shared.plan(make_state(), {"custom.ingredients": "Ingredients"})
        self.assertEqual([e["key"] for e in entries], [
            "metafield_definition:shopify.handle", "category:Drinks", "category:Drinks > Coffee",
            "category:Drinks > Syrups", "attribute_definition:format", "attribute_option:format:Big",
            "attribute_option:format:Small", "metafield_definition:custom.ingredients",
        ])
        coffee = entries[2]
        self.assertEqual(coffee["body"], {"name": "Coffee", "parent": "<category:Drinks IRI>",
                                          "status": "/api/statuses/ACTIVE"})
        self.assertEqual(coffee["depends_on"], ["category:Drinks"])
        self.assertEqual(entries[5]["path"], {"definitionId": "<attribute_definition:format IRI>"})
        self.assertEqual(entries[-1]["body"]["name"], "Ingredients")
        self.assertEqual(entries[-1]["body"]["type"], "/api/metafield_definition_types/multi_line_text_field")

    def test_recorded_keys_are_skipped_and_resolved(self):
        st = make_state()
        state.record_shared(st, "category:Drinks", iri("categories", "drinks"), "created", "taxonomy")
        state.record_shared(st, "metafield_definition:shopify.handle", iri("metafield_definitions", "m"), "reused",
                            "core")
        entries = plan_shared.plan(st)
        keys = [e["key"] for e in entries]
        self.assertNotIn("category:Drinks", keys)
        self.assertNotIn("metafield_definition:shopify.handle", keys)
        self.assertEqual(entries[0]["body"]["parent"], iri("categories", "drinks"))
        self.assertEqual(entries[0]["depends_on"], [])
        self.assertEqual(entries[-1]["body"]["name"], "custom.ingredients")

    def test_deleted_keys_are_planned_again(self):
        st = make_state()
        state.record_shared(st, "category:Drinks", iri("categories", "drinks"), "created", "taxonomy")
        state.mark_deleted(st, [iri("categories", "drinks")])
        self.assertIn("category:Drinks", [e["key"] for e in plan_shared.plan(st)])

    def test_cli_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "state.json"), "w", encoding="utf-8") as f:
                json.dump(make_state(), f)
            result = subprocess.run([sys.executable, SCRIPT, tmp, "--ready"], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual([e["key"] for e in json.loads(result.stdout)], [
                "metafield_definition:shopify.handle", "category:Drinks", "metafield_definition:custom.ingredients"])


if __name__ == "__main__":
    unittest.main()
