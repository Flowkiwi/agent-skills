import contextlib
import copy
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import extract  # noqa: E402
import mapping  # noqa: E402
import metaobjects  # noqa: E402
from concept_helpers import ConceptTestCase, fixture  # noqa: E402

FIXTURE = fixture("mapping-metaobjects.json")
DECISION = {"reference_validation": True,
            "definitions": [{"type": "marque", "name": "Marque", "displayNameField": "nom", "status": "ACTIVE",
                             "fields": [{"key": "nom", "name": "Nom", "type": "single_line_text_field",
                                         "required": True}],
                             "reference": {"metafield": "custom.marque", "name": "Marque"}}],
            "entries": [{"definition": "marque", "handle": "toastine", "fields": {"nom": "Toastine"}}],
            "values": {"2226": {"marque": "toastine"}},
            "warnings": []}


def set_path(node, path, value):
    target = node
    for step in path[:-1]:
        target = target[step]
    target[path[-1]] = value
    return node


def with_origin_definition(spec):
    """Adds a pays definition referenced by a field of marque (a pays is defined before the marque)."""
    spec = copy.deepcopy(spec)
    pays = {"type": "pays", "name": "Pays", "display_name_field": "nom", "status": "DRAFT",
            "fields": [{"key": "nom", "name": "Nom", "type": "single_line_text_field",
                        "value": {"from": ["main:coloris"]}}]}
    spec["definitions"].insert(0, pays)
    spec["definitions"][1]["fields"].append({"key": "pays", "name": "Pays", "type": "metaobject_reference",
                                             "target": "pays", "value": {"from": ["main:coloris"]}})
    return spec


def with_matiere(spec, required, matiere_map):
    """Adds a matiere field to marque; the map blanks some values (demo first rows: 2226 Inox, 2227 Acier
    inoxydable, 2228 inox, 1004 Plastique, nokey-0001 Inox, 2230 Inox)."""
    spec = copy.deepcopy(spec)
    spec["definitions"][0]["fields"].append({"key": "matiere", "name": "Matière", "type": "single_line_text_field",
                                             "required": required,
                                             "value": {"from": ["main:matiere"], "map": matiere_map}})
    return spec


class MetaobjectsTest(ConceptTestCase):
    def extract_with(self, m):
        result = super().extract_with(m)
        return result, [w["code"] for w in result["warnings"]]

    def test_entries_and_values(self):
        m, codes = self.extract_with(FIXTURE)
        self.assertEqual([e["handle"] for e in m["entries"]], ["toastine", "l-atelier-pain", "brioche-co"])
        self.assertEqual(m["entries"][1]["fields"], {"nom": "L'Atelier Pain"})
        self.assertEqual(m["values"]["1004"], {"marque": "brioche-co"})
        self.assertEqual(m["values"]["nokey-0001"], {"marque": "toastine"})
        self.assertEqual(codes, [])

    def test_decision_format(self):
        m, _ = self.extract_with(FIXTURE)
        self.assertEqual(m["definitions"], DECISION["definitions"])
        self.assertEqual(m["reference_validation"], True)
        self.assertEqual(m["entries"][0], DECISION["entries"][0])
        self.assertEqual(m["values"]["2226"], DECISION["values"]["2226"])
        self.assertEqual(sorted(m), sorted(list(DECISION) + ["provenance"]))
        self.assertEqual(len(m["values"]), 6)

    def test_case_variant_without_map_conflicts(self):
        spec = copy.deepcopy(FIXTURE)
        spec["definitions"][0]["fields"][0]["value"] = {"from": ["main:marque"]}
        _, codes = self.extract_with(spec)
        self.assertEqual(codes, ["entry_conflict"])     # TOASTINE vs Toastine on handle toastine

    def test_conflict_keeps_the_first_value(self):
        spec = copy.deepcopy(FIXTURE)
        spec["definitions"][0]["fields"][0]["value"] = {"from": ["main:marque"]}
        m, _ = self.extract_with(spec)
        self.assertEqual(m["entries"][0], {"definition": "marque", "handle": "toastine", "fields": {"nom": "TOASTINE"}})
        self.assertEqual(m["values"]["2227"], {"marque": "toastine"})

    def test_identifiers_are_validated(self):
        for path, bad in ((("definitions", 0, "type"), "marque-produit"), (("definitions", 0, "type"), "m"),
                          (("definitions", 0, "fields", 0, "key"), "Nom"), (("definitions", 0, "status"), "PUBLISHED"),
                          (("definitions", 0, "entry_key"), "titre")):
            spec = set_path(copy.deepcopy(FIXTURE), path, bad)
            with self.assertRaises(mapping.MappingError):
                metaobjects.validate(spec)

    def test_other_rules_are_validated(self):
        for path, bad in ((("definitions", 0, "type"), "m" * 65), (("definitions", 0, "display_name_field"), "titre"),
                          (("definitions", 0, "fields", 0, "type"), "boolean"),
                          (("definitions", 0, "fields", 0, "type"), "metaobject_reference"),
                          (("definitions", 0, "reference", "metafield"), "custom-marque"),
                          (("definitions", 0, "reference", "metafield"), "import.key"),
                          (("definitions", 0, "locale"), "fr"), (("reference_validation",), "yes")):
            spec = set_path(copy.deepcopy(FIXTURE), path, bad)
            with self.assertRaises(mapping.MappingError, msg=repr((path, bad))):
                metaobjects.validate(spec)
        metaobjects.validate(copy.deepcopy(FIXTURE))
        metaobjects.validate(with_origin_definition(FIXTURE))

    def test_reference_target_defined_earlier(self):
        spec = with_origin_definition(FIXTURE)
        spec["definitions"].reverse()
        with self.assertRaises(mapping.MappingError):
            metaobjects.validate(spec)

    def test_reference_field_points_at_target_entries(self):
        m, codes = self.extract_with(with_origin_definition(FIXTURE))
        self.assertEqual([(e["definition"], e["handle"]) for e in m["entries"]][:4], [
            ("pays", "argente"), ("pays", "noir"), ("pays", "creme"), ("pays", "blanc")])
        toastine = [e for e in m["entries"] if e["definition"] == "marque"][0]
        self.assertEqual(toastine["fields"], {"nom": "Toastine", "pays": "argente"})
        self.assertEqual(m["values"]["2226"], {"marque": "toastine"})
        self.assertNotIn("pays", m["values"]["2226"])
        self.assertIn("entry_conflict", codes)          # toastine: argente first, then noir

    def test_unknown_target_entry(self):
        spec = with_origin_definition(FIXTURE)
        spec["definitions"][0]["fields"][0]["value"] = {"from": ["main:coloris"], "map": {"Blanc": None}}
        m, codes = self.extract_with(spec)
        # 2227 toastine: pays noir vs argente (first); 1004 Brioche & Co: Blanc has no pays entry;
        # 2230: pays noir (first row, kept) vs rouge
        self.assertEqual(codes, ["group_conflict", "entry_conflict", "unknown_entry"])
        self.assertEqual(m["warnings"][0]["handle"], "2230")
        self.assertEqual(m["warnings"][2]["handle"], "1004")
        brioche = [e for e in m["entries"] if e["handle"] == "brioche-co"][0]
        self.assertEqual(brioche["fields"], {"nom": "Brioche & Co"})

    def toastine(self, m):
        return [e for e in m["entries"] if e["handle"] == "toastine"][0]

    def test_sparse_fields_are_merged_in_both_row_orders(self):
        for required in (False, True):
            # empty for 2226 (first toastine product), filled for 2227
            m, codes = self.extract_with(with_matiere(FIXTURE, required, {"Inox": None}))
            self.assertEqual(codes, [], required)
            self.assertEqual(self.toastine(m)["fields"], {"nom": "Toastine", "matiere": "Acier inoxydable"})
            # filled for 2226, empty for 2227
            m, codes = self.extract_with(with_matiere(FIXTURE, required, {"Acier inoxydable": None}))
            self.assertEqual(codes, [], required)
            self.assertEqual(self.toastine(m)["fields"], {"nom": "Toastine", "matiere": "Inox"})
            for handle in ("2226", "2227", "nokey-0001", "2230"):
                self.assertEqual(m["values"][handle], {"marque": "toastine"}, (required, handle))

    def test_missing_required_drops_the_entry_and_its_links(self):
        m, codes = self.extract_with(with_matiere(FIXTURE, True, {"Plastique": None, "Acier inoxydable": "Inox"}))
        self.assertEqual(codes, ["missing_required"])
        self.assertEqual(m["warnings"][0]["handle"], "1004")
        self.assertEqual([e["handle"] for e in m["entries"]], ["toastine", "l-atelier-pain"])
        self.assertNotIn("1004", m["values"])
        self.assertEqual(len(m["values"]), 5)

    def test_no_entry_key(self):
        spec = with_matiere(FIXTURE, False, {"Acier inoxydable": "Inox"})
        spec["definitions"][0]["fields"][0]["value"]["map"]["brioche & co"] = None
        m, codes = self.extract_with(spec)
        self.assertEqual(codes, ["no_entry_key"])
        self.assertEqual(m["warnings"][0]["handle"], "1004")
        self.assertNotIn("1004", m["values"])
        self.assertNotIn("brioche-co", [e["handle"] for e in m["entries"]])

    def test_no_handle(self):
        spec = copy.deepcopy(FIXTURE)
        spec["definitions"][0]["fields"][0]["value"]["map"]["brioche & co"] = "&&"
        m, codes = self.extract_with(spec)
        self.assertEqual(codes, ["no_handle"])
        self.assertEqual(m["warnings"][0]["handle"], "1004")
        self.assertNotIn("1004", m["values"])

    def test_several_values_for_a_single_reference(self):
        spec = with_origin_definition(FIXTURE)
        spec["definitions"][1]["fields"][1]["value"] = {"from": ["main:coloris", "main:matiere"], "pick": "all"}
        m, codes = self.extract_with(spec)
        self.assertEqual(set(codes), {"list_in_single_field", "entry_conflict", "group_conflict"})
        first = [w for w in m["warnings"] if w["code"] == "list_in_single_field"][0]
        self.assertEqual((first["handle"], first["field"]), ("2226", "metaobjects.marque.pays"))
        self.assertEqual(self.toastine(m)["fields"]["pays"], "argente")     # first value kept

    def test_number_field_with_decimal_comma(self):
        spec = copy.deepcopy(FIXTURE)
        spec["definitions"][0]["fields"].append({"key": "poids", "name": "Poids", "type": "number_decimal",
                                                 "value": {"from": ["main:poids_net"]}})
        m, _ = self.extract_with(spec)
        self.assertEqual(self.toastine(m)["fields"]["poids"], "1.8")
        brioche = [e for e in m["entries"] if e["handle"] == "brioche-co"][0]
        self.assertEqual(brioche["fields"]["poids"], "1.1")

    def test_cli_prints_the_preview(self):
        self.write_mapping(FIXTURE)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(extract.main(["extract.py", self.dir, "metaobjects"]), 0)
        self.assertIn("marque 'Marque' (custom.marque): 3 entries, 6 products", out.getvalue())


if __name__ == "__main__":
    unittest.main()
