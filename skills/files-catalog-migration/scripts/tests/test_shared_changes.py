import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import extract  # noqa: E402
import plan_shared  # noqa: E402
import state  # noqa: E402
from concept_helpers import SCRIPTS, ConceptTestCase, fake_iri, fixture  # noqa: E402

CONCEPTS = ("taxonomy", "metafields", "metaobjects")


def add_created_product(st, handle, shared=(), written=None):
    """Marks a product created with its product item, one metafield item per shared key, and its snapshot."""
    product = st["products"][handle]
    product["status"] = "created"
    product["iri"] = fake_iri("product:" + handle)
    product["items"].append({"kind": "product", "iri": product["iri"], "status": "created", "module": "core"})
    for key in shared:
        product["items"].append({"kind": "metafield", "iri": fake_iri("metafield:%s:%s" % (handle, key)),
                                 "status": "created", "module": "metafields", "ref": key.split(":", 1)[1],
                                 "shared": key})
    if written is not None:
        product["written"] = {"phase": 1, "payload": written}
    return st


class SharedChangesTest(ConceptTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.concepts = {}
        for concept in CONCEPTS:
            with open(os.path.join(cls.dir, "mappings", concept + ".json"), "w", encoding="utf-8") as f:
                json.dump(fixture("mapping-%s.json" % concept), f, ensure_ascii=False)
            cls.concepts[concept] = extract.main_concept(cls.dir, concept)

    def setUp(self):
        self.st = self.state_with_decision("taxonomy", copy.deepcopy(self.concepts["taxonomy"]))
        for concept in ("metafields", "metaobjects"):
            state.decide(self.st, concept, copy.deepcopy(self.concepts[concept]), "developer")
        self.record_all_shared(self.st)
        add_created_product(self.st, "2226", shared=["metafield_definition:custom.poids_net"])
        self.st["phase"] = 2

    def rename_category(self, old, new):
        taxonomy = self.st["decisions"]["taxonomy"]
        for category in taxonomy["categories"]:
            if category["path"] == old:
                category["path"], category["name"] = new, new.split(" > ")[-1]
        taxonomy["products"] = {h: (new if p == old else p) for h, p in taxonomy["products"].items()}
        for definition in taxonomy["attributes"]:
            definition["categories"] = [new if p == old else p for p in definition.get("categories", [])]

    def add_metaobject_field(self, key, kind, target=None):
        field = {"key": key, "name": key.capitalize(), "type": kind, "value": {"from": ["main:coloris"]}}
        if target:
            field["target"] = target
        definitions = self.st["decisions"]["metaobjects"]["definitions"]
        next(d for d in definitions if d["type"] == "marque")["fields"].append(field)

    def decide(self, key, value):
        state.decide(self.st, key, value, "developer")

    def cli_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "state.json"), "w", encoding="utf-8") as f:
                json.dump(self.st, f, ensure_ascii=False)
            return subprocess.run([sys.executable, os.path.join(SCRIPTS, "plan_shared.py"), tmp, "--changes"],
                                  capture_output=True, text=True)

    def test_no_change(self):
        self.assertEqual(plan_shared.changes(self.st)["destructive"], [])

    def test_metafield_type_change(self):
        self.st["decisions"]["metafields"]["definitions"]["custom.poids_net"]["type"] = "single_line_text_field"
        found = plan_shared.changes(self.st)["destructive"]
        self.assertEqual(found, [{"key": "metafield_definition:custom.poids_net",
                                  "reason": "type number_decimal -> single_line_text_field", "products": ["2226"]}])

    def test_category_removed_and_attribute_type(self):
        self.rename_category("Petit électroménager > Bouilloire", "Petit électroménager > Bouilloires")
        self.st["decisions"]["taxonomy"]["attributes"][0]["type"] = "string"
        keys = {c["key"]: c["reason"] for c in plan_shared.changes(self.st)["destructive"]}
        self.assertEqual(keys["category:Petit électroménager > Bouilloire"], "removed")
        self.assertEqual(keys["attribute_definition:matiere"], "type list -> string")

    def test_additive_changes_are_updates(self):
        self.st["decisions"]["taxonomy"]["attributes"][0]["options"].append({"value": "Bambou", "source": "Bambou"})
        self.st["decisions"]["taxonomy"]["attributes"][0]["name"] = "Matière du corps"
        self.add_metaobject_field("pays", "single_line_text_field")
        result = plan_shared.changes(self.st)
        self.assertEqual(result["destructive"], [])
        self.assertEqual(sorted(u["key"] for u in result["updates"]), ["attribute_definition:matiere", "metaobject_definition:marque"])
        self.assertIn("attribute_option:matiere:Bambou", [e["key"] for e in plan_shared.plan(self.st)])

    def test_removed_metaobject_field_is_destructive(self):
        self.st["decisions"]["metaobjects"]["definitions"][0]["fields"] = []
        self.assertEqual(plan_shared.changes(self.st)["destructive"][0]["key"], "metaobject_definition:marque")

    def test_confirmation_is_phase_scoped(self):
        self.st["decisions"]["metafields"]["definitions"]["custom.poids_net"]["type"] = "json"
        self.assertEqual(self.cli_changes().returncode, 1)
        self.decide("confirmed_changes", {"phase": 2, "keys": ["metafield_definition:custom.poids_net"]})
        self.assertEqual(self.cli_changes().returncode, 0)

    def test_confirmation_of_an_earlier_phase_does_not_count(self):
        self.st["decisions"]["metafields"]["definitions"]["custom.poids_net"]["type"] = "json"
        self.decide("confirmed_changes", {"phase": 2, "keys": ["metafield_definition:custom.poids_net"]})
        state.start_phase(self.st)
        result = self.cli_changes()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)["destructive"][0]["key"], "metafield_definition:custom.poids_net")

    def test_update_entries(self):
        self.st["decisions"]["taxonomy"]["attributes"][0]["name"] = "Matière du corps"
        self.add_metaobject_field("pays", "single_line_text_field")
        updates = {u["key"]: u for u in plan_shared.changes(self.st)["updates"]}
        attribute = updates["attribute_definition:matiere"]
        self.assertEqual(attribute["operation"], "update_attribute_definition")
        self.assertEqual(attribute["iri"], fake_iri("attribute_definition:matiere"))
        self.assertEqual(attribute["body"], {"name": "Matière du corps"})
        self.assertEqual(attribute["module"], "taxonomy")
        self.assertEqual(attribute["spec"], {"type": "list", "multiple": False, "name": "Matière du corps"})
        marque = updates["metaobject_definition:marque"]
        self.assertEqual(marque["operation"], "update_metaobject_definition")
        self.assertEqual([f["key"] for f in marque["body"]["fieldDefinitions"]], ["nom", "pays"])
        self.assertEqual(list(marque["body"]), ["fieldDefinitions"])

    def test_metafield_rename_and_metaobject_entry_change(self):
        self.st["decisions"]["metafields"]["definitions"]["custom.poids_net"]["name"] = "Poids net"
        entry = self.st["decisions"]["metaobjects"]["entries"][0]
        entry["fields"]["nom"] = entry["fields"]["nom"] + " SA"
        updates = {u["key"]: u for u in plan_shared.changes(self.st)["updates"]}
        self.assertEqual(updates["metafield_definition:custom.poids_net"]["body"], {"name": "Poids net"})
        self.assertEqual(updates["metafield_definition:custom.poids_net"]["operation"], "update_metafield_definition")
        changed = updates["metaobject:marque:%s" % entry["handle"]]
        self.assertEqual(changed["operation"], "update_metaobject")
        self.assertEqual(changed["body"], {"fields": [{"key": "nom", "value": entry["fields"]["nom"]}]})

    def test_retargeted_reference_field_is_destructive(self):
        definitions = self.st["decisions"]["metaobjects"]["definitions"]
        for kind in ("pays", "region"):
            definitions.insert(0, {"type": kind, "name": kind.capitalize(), "displayNameField": "nom",
                                   "status": "ACTIVE", "fields": [{"key": "nom", "name": "Nom",
                                                                   "type": "single_line_text_field"}]})
        self.add_metaobject_field("origine", "metaobject_reference", target="pays")
        self.record_all_shared(self.st)
        marque = next(e for e in plan_shared.plan(self.st, include_recorded=True)
                      if e["key"] == "metaobject_definition:marque")
        self.assertEqual(marque["spec"]["targets"], {"origine": "pays"})
        state.record_shared(self.st, marque["key"], fake_iri(marque["key"]), "created", "metaobjects", marque["spec"])
        definitions[-1]["fields"][-1]["target"] = "region"
        found = {c["key"]: c["reason"] for c in plan_shared.changes(self.st)["destructive"]}
        self.assertEqual(found, {"metaobject_definition:marque": "field origine target pays -> region"})

    def test_collection_rename_is_a_removed_path(self):
        self.st["decisions"]["collections"] = {"collections": [{"path": "Promo", "name": "Promo", "parent": None}]}
        self.record_all_shared(self.st)
        self.st["decisions"]["collections"]["collections"] = [{"path": "Promos", "name": "Promos", "parent": None}]
        result = plan_shared.changes(self.st)
        self.assertEqual([c["key"] for c in result["destructive"]], ["collection:Promo"])
        self.assertEqual(result["updates"], [])

    def test_products_from_written_snapshot(self):
        category = "category:Petit électroménager > Bouilloire"
        add_created_product(self.st, "2227", written={"product": {"body": {"category": fake_iri(category)}}})
        self.rename_category("Petit électroménager > Bouilloire", "Petit électroménager > Bouilloires")
        found = {c["key"]: c["products"] for c in plan_shared.changes(self.st)["destructive"]}
        self.assertEqual(found[category], ["2227"])

    def test_reused_and_unknown(self):
        state.record_shared(self.st, "metafield_definition:custom.poids_net", fake_iri("x"), "reused", "metafields",
                            {"type": "number_decimal", "name": "Poids net (kg)"})
        state.record_shared(self.st, "metafield_definition:custom.marque", fake_iri("y"), "created", "metaobjects")
        state.record_shared(self.st, "country:FR", fake_iri("country:FR"), "created", "prices")
        self.st["decisions"]["metafields"]["definitions"]["custom.poids_net"]["type"] = "json"
        result = plan_shared.changes(self.st)
        self.assertEqual(result["destructive"], [])
        self.assertEqual(result["unmanaged"], ["metafield_definition:custom.poids_net"])
        self.assertEqual(result["unknown"], ["metafield_definition:custom.marque"])
        self.assertEqual(self.cli_changes().returncode, 0)

    def test_plan_include_recorded(self):
        keys = [e["key"] for e in plan_shared.plan(self.st, include_recorded=True)]
        self.assertIn("attribute_definition:matiere", keys)
        self.assertEqual(plan_shared.plan(self.st), [])


if __name__ == "__main__":
    unittest.main()
