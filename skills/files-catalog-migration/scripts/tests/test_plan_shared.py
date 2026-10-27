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

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "plan_shared.py")


def iri(resource, name):
    return f"/api/{resource}/{uuid.uuid5(uuid.NAMESPACE_URL, resource + name)}"


def make_products():
    return {"products": [
        {"key": "2226", "handle": "2226", "title": "Grille-pain", "options": [], "variants": []},
        {"key": "2230", "handle": "2230", "title": "Cafetiere",
         "options": [{"name": "Couleur", "values": ["Noir", "Rouge"]}], "variants": []},
    ]}


def make_state():
    st = state.init("acme", "u", make_products())
    state.decide(st, "taxonomy", {
        "categories": [{"path": "Drinks", "name": "Drinks", "parent": None},
                       {"path": "Drinks > Coffee", "name": "Coffee", "parent": "Drinks"},
                       {"path": "Drinks > Syrups", "name": "Syrups", "parent": "Drinks"}],
        "products": {"2226": "Drinks > Syrups", "2230": "Drinks > Coffee"},
        "attributes": [{"code": "format", "name": "Format", "type": "list", "multiple": False,
                        "categories": ["Drinks > Coffee"], "options": [{"value": "Big"}, {"value": "Small"}],
                        "values": {}}]}, "developer")
    state.decide(st, "metafields", {"definitions": {"custom.ingredients": {"type": "multi_line_text_field"}}},
                 "developer")
    return st


class PlanSharedTest(unittest.TestCase):
    def test_marker_entry(self):
        st = state.init("acme", "u", make_products())
        state.decide(st, "data_language", "fr", "developer")
        entry = plan_shared.plan(st)[0]
        self.assertEqual(entry["key"], "metafield_definition:import.key")
        self.assertEqual((entry["body"]["namespace"], entry["body"]["key"], entry["body"]["name"]),
                         ("import", "key", "Clé d'import"))
        self.assertEqual(entry["lookup"], "list_metafield_definitions: namespace import, key key, owner type products")
        self.assertEqual(entry["spec"], {"type": "single_line_text_field", "name": "Clé d'import"})
        state.record_shared(st, entry["key"], iri("metafield_definitions", "m"), "created", "core", entry["spec"])
        self.assertEqual(plan_shared.plan(st), [])

    def test_order_placeholders_and_specs(self):
        entries = plan_shared.plan(make_state(), {"custom.ingredients": "Ingredients"})
        self.assertEqual([e["key"] for e in entries], [
            "metafield_definition:import.key", "category:Drinks", "category:Drinks > Coffee",
            "category:Drinks > Syrups", "attribute_definition:format", "attribute_option:format:Big",
            "attribute_option:format:Small", "metafield_definition:custom.ingredients",
        ])
        coffee = entries[2]
        self.assertEqual(coffee["body"], {"name": "Coffee", "parent": "<category:Drinks IRI>",
                                          "status": "/api/statuses/ACTIVE"})
        self.assertEqual(coffee["depends_on"], ["category:Drinks"])
        self.assertEqual(coffee["spec"], {"name": "Coffee", "parent": "Drinks"})
        self.assertEqual(entries[1]["spec"], {"name": "Drinks", "parent": None})
        self.assertEqual(entries[4]["spec"], {"type": "list", "multiple": False, "name": "Format"})
        self.assertEqual(entries[5]["spec"], {"value": "Big"})
        self.assertEqual(entries[5]["path"], {"definitionId": "<attribute_definition:format IRI>"})
        self.assertEqual(entries[-1]["body"]["name"], "Ingredients")
        self.assertEqual(entries[-1]["spec"], {"type": "multi_line_text_field", "name": "Ingredients"})
        self.assertEqual(entries[-1]["body"]["type"], "/api/metafield_definition_types/multi_line_text_field")

    def test_recorded_keys_are_skipped_and_resolved(self):
        st = make_state()
        state.record_shared(st, "category:Drinks", iri("categories", "drinks"), "created", "taxonomy")
        state.record_shared(st, "metafield_definition:import.key", iri("metafield_definitions", "m"), "reused",
                            "core")
        entries = plan_shared.plan(st)
        keys = [e["key"] for e in entries]
        self.assertNotIn("category:Drinks", keys)
        self.assertNotIn("metafield_definition:import.key", keys)
        self.assertEqual(entries[0]["body"]["parent"], iri("categories", "drinks"))
        self.assertEqual(entries[0]["depends_on"], [])
        self.assertEqual(entries[-1]["body"]["name"], "custom.ingredients")

    def test_deleted_keys_are_planned_again(self):
        st = make_state()
        state.record_shared(st, "category:Drinks", iri("categories", "drinks"), "created", "taxonomy")
        state.mark_deleted(st, [iri("categories", "drinks")])
        self.assertIn("category:Drinks", [e["key"] for e in plan_shared.plan(st)])

    def test_marker_follows_data_language(self):
        st = make_state()
        self.assertEqual(plan_shared.plan(st)[0]["body"]["name"], "Import key")
        state.decide(st, "data_language", "fr-FR", "developer")
        marker = plan_shared.plan(st)[0]["body"]
        self.assertEqual(marker["name"], "Clé d'import")
        self.assertEqual(marker["description"], "Marqueur technique posé par la migration de fichiers")
        state.decide(st, "marker_labels", {"name": "Importsleutel", "description": "Technisch kenmerk"}, "developer")
        self.assertEqual(plan_shared.plan(st)[0]["body"]["name"], "Importsleutel")

    def test_cli_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "state.json"), "w", encoding="utf-8") as f:
                json.dump(make_state(), f)
            result = subprocess.run([sys.executable, SCRIPT, tmp, "--ready"], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual([e["key"] for e in json.loads(result.stdout)], [
                "metafield_definition:import.key", "category:Drinks", "metafield_definition:custom.ingredients"])


if __name__ == "__main__":
    unittest.main()
