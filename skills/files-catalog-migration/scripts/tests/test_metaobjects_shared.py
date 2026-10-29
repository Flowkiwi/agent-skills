import copy
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import payload  # noqa: E402
import plan_shared  # noqa: E402
import state  # noqa: E402
from concept_helpers import ConceptTestCase, fake_iri, fixture  # noqa: E402

FIXTURE = fixture("mapping-metaobjects.json")
REFERENCE_VALIDATION = "/api/metafield_definition_validation_types/metaobject_definition"


def with_pays(spec):
    """A pays definition (entries from the coloris column) referenced by a pays field of marque."""
    spec = copy.deepcopy(spec)
    spec["definitions"].insert(0, {"type": "pays", "name": "Pays", "display_name_field": "nom", "status": "DRAFT",
                                   "fields": [{"key": "nom", "name": "Nom", "type": "single_line_text_field",
                                               "value": {"from": ["main:coloris"]}}]})
    spec["definitions"][1]["fields"].append({"key": "pays", "name": "Pays", "type": "metaobject_reference",
                                             "target": "pays", "value": {"from": ["main:coloris"]}})
    return spec


def add_created_product(st, handle, reference=True):
    """Marks a product created with its product item and, when asked, its reference metafield item."""
    product = st["products"][handle]
    product["status"] = "created"
    product["iri"] = fake_iri("product:" + handle)
    product["items"].append({"kind": "product", "iri": product["iri"], "status": "created", "module": "core"})
    if reference:
        product["items"].append({"kind": "metafield", "iri": fake_iri("metafield:" + handle), "status": "created",
                                 "module": "metaobjects", "ref": "custom.marque",
                                 "shared": "metafield_definition:custom.marque"})
    return st


class MetaobjectsSharedTest(ConceptTestCase):
    def setUp(self):
        self.st = self.state_with_decision("metaobjects", self.extract_with(FIXTURE))

    def test_plan_order_and_bodies(self):
        entries = plan_shared.plan(self.st)
        keys = [e["key"] for e in entries]
        self.assertEqual(keys, ["metafield_definition:import.key", "metaobject_definition:marque", "metaobject:marque:toastine",
                                "metaobject:marque:l-atelier-pain", "metaobject:marque:brioche-co", "metafield_definition:custom.marque"])
        self.assertEqual(entries[2]["body"], {"definition": "<metaobject_definition:marque IRI>", "handle": "toastine",
                                              "fields": [{"key": "nom", "value": "Toastine"}], "status": "ACTIVE"})
        self.assertEqual(entries[5]["body"]["validations"][0]["value"], "<metaobject_definition:marque IRI>")
        self.assertEqual(entries[5]["depends_on"], ["metaobject_definition:marque"])
        self.assertNotIn("locale", json.dumps(entries))

    def test_definition_and_entry_entries(self):
        entries = {e["key"]: e for e in plan_shared.plan(self.st)}
        self.assertEqual({e["module"] for k, e in entries.items() if k != "metafield_definition:import.key"},
                         {"metaobjects"})
        definition, entry = entries["metaobject_definition:marque"], entries["metaobject:marque:l-atelier-pain"]
        reference = entries["metafield_definition:custom.marque"]
        self.assertEqual(definition["operation"], "create_metaobject_definition")
        self.assertEqual(definition["lookup"], "list_metaobject_definitions: type 'marque'")
        self.assertEqual(definition["body"], {
            "type": "marque", "name": "Marque", "displayNameField": "nom",
            "fieldDefinitions": [{"key": "nom", "name": "Nom", "type": "/api/metafield_definition_types/single_line_text_field",
                                  "required": True}]})
        self.assertEqual(definition["spec"], {"name": "Marque", "fields": {"nom": "single_line_text_field"}})
        self.assertEqual(definition["depends_on"], [])
        self.assertEqual(entry["operation"], "create_metaobject")
        self.assertEqual(entry["lookup"], "list_metaobjects: definition <metaobject_definition:marque IRI>, "
                                          "handle 'l-atelier-pain'")
        self.assertEqual(entry["spec"], {"fields": {"nom": "L'Atelier Pain"}})
        self.assertEqual(entry["depends_on"], ["metaobject_definition:marque"])
        self.assertEqual(reference["operation"], "create_metafield_definition")
        self.assertEqual(reference["body"], {
            "namespace": "custom", "key": "marque", "name": "Marque", "ownerType": "/api/metafield_owner_types/products",
            "type": "/api/metafield_definition_types/metaobject_reference",
            "validations": [{"name": REFERENCE_VALIDATION, "value": "<metaobject_definition:marque IRI>"}]})
        self.assertEqual(reference["spec"], {"type": "metaobject_reference", "name": "Marque"})

    def test_recorded_definition_is_resolved(self):
        state.record_shared(self.st, "metaobject_definition:marque", fake_iri("metaobject_definition:marque"),
                            "created", "metaobjects")
        entries = plan_shared.plan(self.st)
        self.assertNotIn("metaobject_definition:marque", [e["key"] for e in entries])
        self.assertEqual(entries[1]["body"]["definition"], fake_iri("metaobject_definition:marque"))
        self.assertEqual(entries[1]["depends_on"], [])
        self.assertEqual(entries[-1]["depends_on"], [])

    def test_without_validation(self):
        self.st["decisions"]["metaobjects"]["reference_validation"] = False
        self.assertNotIn("validations", plan_shared.plan(self.st)[-1]["body"])
        self.assertEqual(plan_shared.plan(self.st)[-1]["depends_on"], [])

    def test_reference_fields_between_definitions(self):
        st = self.state_with_decision("metaobjects", self.extract_with(with_pays(FIXTURE)))
        entries = {e["key"]: e for e in plan_shared.plan(st)}
        keys = [k for k in entries if k.startswith("metaobject")]
        self.assertEqual(keys[:3], ["metaobject_definition:pays", "metaobject_definition:marque", "metaobject:pays:argente"])
        self.assertLess(keys.index("metaobject:pays:blanc"), keys.index("metaobject:marque:toastine"))
        marque = entries["metaobject_definition:marque"]
        self.assertEqual(marque["body"]["fieldDefinitions"][1], {
            "key": "pays", "name": "Pays", "type": "/api/metafield_definition_types/metaobject_reference",
            "validations": [{"name": REFERENCE_VALIDATION, "value": "<metaobject_definition:pays IRI>"}]})
        self.assertEqual(marque["depends_on"], ["metaobject_definition:pays"])
        self.assertEqual(marque["spec"]["fields"], {"nom": "single_line_text_field", "pays": "metaobject_reference"})
        toastine = entries["metaobject:marque:toastine"]
        self.assertEqual(toastine["body"]["fields"], [{"key": "nom", "value": "Toastine"},
                                                      {"key": "pays", "value": "<metaobject:pays:argente IRI>"}])
        self.assertEqual(toastine["depends_on"], ["metaobject_definition:marque", "metaobject:pays:argente"])
        self.assertEqual(entries["metaobject:pays:noir"]["body"]["status"], "DRAFT")

    def test_list_reference_value_is_a_json_array_string(self):
        spec = with_pays(FIXTURE)
        field = spec["definitions"][1]["fields"][1]
        field["type"] = "list__metaobject_reference"
        field["value"] = {"from": ["main:coloris"], "pick": "all"}
        st = self.state_with_decision("metaobjects", self.extract_with(spec))
        state.record_shared(st, "metaobject:pays:argente", fake_iri("metaobject:pays:argente"), "created", "metaobjects")
        toastine = [e for e in plan_shared.plan(st) if e["key"] == "metaobject:marque:toastine"][0]
        self.assertEqual(json.loads(toastine["body"]["fields"][1]["value"]), [fake_iri("metaobject:pays:argente")])

    def test_metafields_and_reference_on_the_same_key(self):
        state.decide(self.st, "metafields", {"definitions": {"custom.marque": {
            "type": "single_line_text_field", "name": "Marque", "transform": None}}}, "developer")
        with self.assertRaises(payload.PayloadError):
            plan_shared.plan(self.st)

    def test_product_reference(self):
        out = payload.build(self.products(), self.st, "1004", placeholders=True)["metafields"]
        self.assertEqual(out, [{"key": "custom.marque", "shared": "metafield_definition:custom.marque",
                                "body": {"definition": "<metafield_definition:custom.marque IRI>",
                                         "value": "<metaobject:marque:brioche-co UUID>"}}])

    def test_product_reference_is_the_bare_entry_uuid(self):
        self.record_all_shared(self.st)
        (entry,) = payload.build(self.products(), self.st, "1004")["metafields"]
        self.assertEqual(entry["body"], {"definition": fake_iri("metafield_definition:custom.marque"),
                                         "value": fake_iri("metaobject:marque:brioche-co").rsplit("/", 1)[1]})

    def test_product_reference_needs_an_iri_ending_with_a_uuid(self):
        self.record_all_shared(self.st)
        self.st["shared"]["metaobject:marque:brioche-co"]["iri"] = "/api/metaobjects/brioche-co"
        for placeholders in (False, True):
            with self.assertRaises(payload.PayloadError):
                payload.build(self.products(), self.st, "1004", placeholders=placeholders)

    def test_reference_validation_is_false_by_default(self):
        spec = copy.deepcopy(FIXTURE)
        del spec["reference_validation"]
        st = self.state_with_decision("metaobjects", self.extract_with(spec))
        self.assertIs(st["decisions"]["metaobjects"]["reference_validation"], False)
        self.assertNotIn("validations", plan_shared.plan(st)[-1]["body"])
        del st["decisions"]["metaobjects"]["reference_validation"]
        self.assertNotIn("validations", plan_shared.plan(st)[-1]["body"])

    def test_reference_fields_inside_definitions_keep_their_validation(self):
        spec = with_pays(FIXTURE)
        del spec["reference_validation"]
        st = self.state_with_decision("metaobjects", self.extract_with(spec))
        marque = next(e for e in plan_shared.plan(st) if e["key"] == "metaobject_definition:marque")
        self.assertEqual(marque["body"]["fieldDefinitions"][1]["validations"],
                         [{"name": REFERENCE_VALIDATION, "value": "<metaobject_definition:pays IRI>"}])

    def test_product_reference_after_metafields(self):
        state.decide(self.st, "metafields", {"definitions": {"custom.poids": {
            "type": "single_line_text_field", "name": "Poids", "transform": None}},
            "values": {"1004": {"custom.poids": "1,1"}}}, "developer")
        out = payload.build(self.products(), self.st, "1004", placeholders=True)["metafields"]
        self.assertEqual([e["key"] for e in out], ["custom.poids", "custom.marque"])

    def test_product_reference_needs_recorded_entries(self):
        with self.assertRaises(payload.PayloadError):
            payload.build(self.products(), self.st, "1004")

    def test_undo_order(self):
        self.record_all_shared(self.st)
        add_created_product(self.st, "1004")
        kinds = [s["kind"] for s in state.undo_plan(self.st, "all")]
        self.assertEqual(kinds, ["metafield", "product", "metaobject", "metaobject", "metaobject",
                                 "metafield_definition", "metafield_definition", "metaobject_definition"])
        entries = [s["owner"] for s in state.undo_plan(self.st, "all") if s["kind"] == "metaobject"]
        self.assertEqual(entries[-1], "metaobject:marque:toastine")

    def test_undo_deletes_referencing_entries_before_their_targets(self):
        st = self.state_with_decision("metaobjects", self.extract_with(with_pays(FIXTURE)))
        self.record_all_shared(st)
        owners = [s["owner"] for s in state.undo_plan(st, "all")]
        self.assertLess(owners.index("metaobject:marque:toastine"), owners.index("metaobject:pays:argente"))
        self.assertLess(owners.index("metaobject_definition:marque"), owners.index("metaobject_definition:pays"))
        self.assertLess(owners.index("metafield_definition:custom.marque"), owners.index("metaobject_definition:pays"))

    def test_undo_module_metaobjects(self):
        self.record_all_shared(self.st)
        add_created_product(self.st, "1004")
        steps = state.undo_plan(self.st, "module:metaobjects")
        self.assertEqual([s["kind"] for s in steps], ["metafield", "metaobject", "metaobject", "metaobject",
                                                      "metafield_definition", "metaobject_definition"])
        self.assertNotIn("metafield_definition:import.key", [s["owner"] for s in steps])

    def test_worklists_module_selects_products_without_reference(self):
        self.record_all_shared(self.st)
        add_created_product(self.st, "1004", reference=False)
        add_created_product(self.st, "2226")
        self.assertEqual(state.worklists(self.st, 1, None, False, module="metaobjects"), [["1004"]])


if __name__ == "__main__":
    unittest.main()
