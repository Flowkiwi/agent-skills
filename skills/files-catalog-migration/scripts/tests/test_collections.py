import contextlib
import io
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import extract  # noqa: E402
import mapping  # noqa: E402
import payload  # noqa: E402
import plan_shared  # noqa: E402
from concept_helpers import ConceptTestCase  # noqa: E402


class CollectionsTest(ConceptTestCase):
    MAPPING = {"version": 1, "concept": "collections", "status": "/api/statuses/ACTIVE", "separator": None,
               "value": {"from": ["main:marque"], "map": {"toastine": "Toastine"}, "map_ignore_case": True},
               "assign": {"2226": ["Petit déjeuner"]}, "origin": "agent", "reason": "Regroupement par marque proposé."}

    def mapping_with(self, **changes):
        return dict(self.MAPPING, **changes)

    def test_extract(self):
        c = self.extract_with(self.MAPPING)
        self.assertEqual([x["path"] for x in c["collections"]], ["Brioche & Co", "L'Atelier Pain", "Petit déjeuner", "Toastine"])
        self.assertEqual(c["membership"]["2226"], ["Toastine", "Petit déjeuner"])
        self.assertEqual(c["membership"]["2228"], ["L'Atelier Pain"])

    def test_plan_and_payload(self):
        st = self.state_with_decision("collections", self.extract_with(self.MAPPING))
        entries = [e for e in plan_shared.plan(st) if e["module"] == "collections"]
        self.assertEqual(entries[0]["body"], {"name": "Brioche & Co", "parent": None, "status": "/api/statuses/ACTIVE"})
        body = payload.build(self.products(), st, "2226", placeholders=True)["product"]["body"]
        self.assertEqual(body["collections"], ["<collection:Toastine IRI>", "<collection:Petit déjeuner IRI>"])

    def test_decision_format(self):
        c = self.extract_with(self.MAPPING)
        self.assertEqual(list(c), ["status", "collections", "membership", "warnings"])
        self.assertEqual(c["status"], "/api/statuses/ACTIVE")
        self.assertEqual(c["collections"][0], {"path": "Brioche & Co", "name": "Brioche & Co", "parent": None})
        self.assertEqual(list(c["membership"]), [p["handle"] for p in self.products()["products"]])
        self.assertEqual(c["warnings"], [])

    def test_separator_builds_a_tree(self):
        c = self.extract_with(self.mapping_with(
            value={"from": ["main:categorie"]}, separator="/", assign={"1004": ["Maison > Bain", "Sp"], "nope": ["X"]},
            status="/api/statuses/DRAFT"))
        self.assertEqual([x["path"] for x in c["collections"]],
                         ["Catalogue", "Catalogue > PEM", "Catalogue > PEM > Bouilloire", "Catalogue > PEM > Grille-pain",
                          "Maison", "Maison > Bain", "Sp"])
        self.assertEqual(c["collections"][5], {"path": "Maison > Bain", "name": "Bain", "parent": "Maison"})
        self.assertEqual(c["membership"]["1004"], ["Catalogue > PEM > Bouilloire", "Maison > Bain", "Sp"])
        self.assertEqual(c["status"], "/api/statuses/DRAFT")
        self.assertEqual([(w["code"], w["handle"], w["field"]) for w in c["warnings"]],
                         [("unknown_handle", "nope", "assign"), ("short_collection_name", None, "collections")])

    def test_list_value_gives_several_collections(self):
        c = self.extract_with(self.mapping_with(value={"from": ["main:categorie"], "transform": [{"split": "/"}]},
                                                assign={}))
        self.assertEqual(c["membership"]["2226"], ["Catalogue", "PEM", "Grille-pain"])
        self.assertEqual([x["parent"] for x in c["collections"]], [None] * 4)

    def test_proposal_without_column(self):
        c = self.extract_with(self.mapping_with(value=None, assign={"2226": ["Petit déjeuner", "Petit déjeuner"],
                                                                    "2230": ["Petit déjeuner"]}))
        self.assertEqual(c["collections"], [{"path": "Petit déjeuner", "name": "Petit déjeuner", "parent": None}])
        self.assertEqual(c["membership"], {"2226": ["Petit déjeuner"], "2230": ["Petit déjeuner"]})

    def test_mapping_errors(self):
        bad = [self.mapping_with(seperator="/"), self.mapping_with(separator=""),
               self.mapping_with(value={"from": ["main:nope"]}), self.mapping_with(value="Toastine"),
               self.mapping_with(value={"from": ["main:marque"], "mapp": {}}),
               self.mapping_with(assign={"2226": "Petit déjeuner"}), self.mapping_with(assign={"2226": [""]}),
               self.mapping_with(status="ACTIVE"), self.mapping_with(origin="robot")]
        for m in bad:
            with self.subTest(m=m), self.assertRaises(mapping.MappingError):
                self.extract_with(m)
        with open(os.path.join(self.dir, "mappings", "collections.json"), "w", encoding="utf-8") as f:
            json.dump(dict(self.MAPPING, concept="taxonomy"), f)
        with self.assertRaisesRegex(mapping.MappingError, "concept must be 'collections'"):
            extract.main_concept(self.dir, "collections")

    def test_plan_parents_first_with_dependencies(self):
        c = self.extract_with(self.mapping_with(value=None, assign={"2226": ["Maison > Cuisine"]}))
        st = self.state_with_decision("collections", c)
        entries = [e for e in plan_shared.plan(st) if e["module"] == "collections"]
        self.assertEqual([e["key"] for e in entries], ["collection:Maison", "collection:Maison > Cuisine"])
        child = entries[1]
        self.assertEqual((child["operation"], child["lookup"]),
                         ("create_collection", "list_collections: name 'Cuisine' with the same parent"))
        self.assertEqual(child["body"], {"name": "Cuisine", "parent": "<collection:Maison IRI>",
                                         "status": "/api/statuses/ACTIVE"})
        self.assertEqual(child["spec"], {"name": "Cuisine", "parent": "Maison"})
        self.assertEqual(child["depends_on"], ["collection:Maison"])

    def test_step_collections_and_recorded_iris(self):
        st = self.record_all_shared(self.state_with_decision("collections", self.extract_with(self.MAPPING)))
        iris = [st["shared"]["collection:Toastine"]["iri"], st["shared"]["collection:Petit déjeuner"]["iri"]]
        self.assertEqual(payload.build(self.products(), st, "2226", step="collections"),
                         {"handle": "2226", "collections": iris})
        self.assertEqual(payload.build(self.products(), st, "2226")["product"]["body"]["collections"], iris)
        self.assertIn("collections", payload.STEPS)

    def test_product_without_membership(self):
        c = self.extract_with(self.mapping_with(value=None, assign={"2226": ["Petit déjeuner"]}))
        st = self.state_with_decision("collections", c)
        body = payload.build(self.products(), st, "1004", placeholders=True)["product"]["body"]
        self.assertNotIn("collections", body)
        self.assertEqual(payload.build(self.products(), st, "1004", step="collections", placeholders=True),
                         {"handle": "1004", "collections": []})

    def test_cli_prints_the_preview(self):
        self.write_mapping(self.mapping_with(assign={"2226": ["PD"]}))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = extract.main(["extract.py", self.dir, "collections"])
        self.assertEqual(code, 0)
        lines = out.getvalue().splitlines()
        self.assertIn("Toastine (4)", lines)
        self.assertIn("Brioche & Co (1)", lines)
        self.assertTrue(any(line.startswith("short_collection_name: ") for line in lines))
        self.assertEqual(lines[-1], "4 collections")


if __name__ == "__main__":
    unittest.main()
