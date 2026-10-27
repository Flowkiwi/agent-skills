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
import taxonomy  # noqa: E402
from concept_helpers import ConceptTestCase, fixture  # noqa: E402
from demo_files import write_demo_csv  # noqa: E402

FIXTURE = fixture("mapping-taxonomy.json")


def categories(**changes):
    return dict(FIXTURE, categories=dict(FIXTURE["categories"], **changes))


class TaxonomyCategoriesTest(ConceptTestCase):
    def test_category_path(self):
        self.assertEqual(taxonomy.category_path("Catalogue/PEM/Grille-pain", "/", drop_levels=1), "PEM > Grille-pain")
        self.assertEqual(taxonomy.category_path("A/B/C", "/", depth=2), "A > B")
        self.assertIsNone(taxonomy.category_path("A/B", "/", path_map={"A > B": None}))
        self.assertEqual(taxonomy.category_path("A/B", "/", renames={"A > B": "Bee"}), "A > Bee")
        self.assertEqual(taxonomy.category_path(" A / /B ", "/"), "A > B")
        self.assertEqual(taxonomy.category_path("A/B", None), "A/B")
        self.assertIsNone(taxonomy.category_path("Catalogue", "/", drop_levels=1))
        self.assertEqual(taxonomy.category_path("A/B", "/", path_map={"A > B": "X > Y"}, renames={"X": "Ex"}),
                         "Ex > Y")

    def test_attribute_code_strips_accents(self):
        self.assertEqual(taxonomy.attribute_code("Matière"), "matiere")
        self.assertEqual(taxonomy.attribute_code(" Décongélation rapide "), "decongelation-rapide")

    def test_path_tree(self):
        nodes, warnings = taxonomy.path_tree(["Maison > Cuisine", "Bar", "Maison > Bain > Spa", "Maison > Cuisine"],
                                             3, "short_category_name", "categories")
        self.assertEqual([n["path"] for n in nodes],
                         ["Bar", "Maison", "Maison > Bain", "Maison > Bain > Spa", "Maison > Cuisine"])
        self.assertEqual(nodes[3], {"path": "Maison > Bain > Spa", "name": "Spa", "parent": "Maison > Bain"})
        self.assertEqual(warnings, [])
        _, warnings = taxonomy.path_tree(["PE > Four"], 3, "short_collection_name", "collections")
        self.assertEqual([(w["code"], w["field"], w["stage"]) for w in warnings],
                         [("short_collection_name", "collections", "extract")])
        self.assertIn("'PE'", warnings[0]["message"])

    def test_demo_tree(self):
        t = self.extract_with(FIXTURE)
        self.assertEqual([c["path"] for c in t["categories"]],
                         ["Petit électroménager", "Petit électroménager > Bouilloire", "Petit électroménager > Grille-pain"])
        self.assertEqual(t["products"]["1004"], "Petit électroménager > Bouilloire")
        self.assertEqual(t["products"]["2230"], "Petit électroménager > Grille-pain")
        self.assertEqual(t["uncategorized"], [])
        self.assertEqual(list(t), ["source", "category_status", "categories", "products", "attributes",
                                   "uncategorized", "warnings"])
        self.assertEqual((t["source"], t["category_status"]), ("mappings/taxonomy.json", "/api/statuses/ACTIVE"))
        self.assertEqual(t["categories"][1], {"path": "Petit électroménager > Bouilloire", "name": "Bouilloire",
                                              "parent": "Petit électroménager"})
        self.assertEqual(t["warnings"], [])

    def test_agent_proposal_without_column(self):
        spec = {"value": None, "separator": None, "assign": {"2226": "Grille-pain", "2230": "Grille-pain", "nope": "X"},
                "status": "/api/statuses/ACTIVE", "origin": "agent", "reason": "Aucune colonne catégorie."}
        t = self.extract_with(dict(FIXTURE, categories=spec))
        self.assertEqual(t["categories"], [{"path": "Grille-pain", "name": "Grille-pain", "parent": None}])
        self.assertEqual(sorted(t["uncategorized"]), ["1004", "2227", "2228", "nokey-0001"])
        self.assertIn("unknown_handle", [w["code"] for w in t["warnings"]])

    def test_assign_overrides_the_column_and_short_names_warn(self):
        t = self.extract_with(categories(assign={"1004": "Petit électroménager > Bouilloire > Sans fil",
                                                 "2228": "Petit électroménager>GP"}))
        self.assertEqual(t["products"]["1004"], "Petit électroménager > Bouilloire > Sans fil")
        self.assertEqual(t["products"]["2228"], "Petit électroménager > GP")
        self.assertEqual([(w["code"], w["field"]) for w in t["warnings"]], [("short_category_name", "categories")])

    def test_rename_and_null_map_target(self):
        t = self.extract_with(categories(
            map={"PEM > Grille-pain": "Petit électroménager > Grille-pain", "PEM > Bouilloire": None},
            rename={"Petit électroménager": "Électroménager"}))
        self.assertEqual([c["path"] for c in t["categories"]], ["Électroménager", "Électroménager > Grille-pain"])
        self.assertEqual(t["uncategorized"], ["1004"])

    def test_variants_with_different_categories_warn(self):
        t = self.extract_with(categories(value={"from": ["main:coloris"]}, separator=None, drop_levels=0, map={}))
        self.assertEqual(t["products"]["2230"], "Noir")
        conflicts = [w for w in t["warnings"] if w["code"] == "group_conflict"]
        self.assertEqual([(w["handle"], w["row"]) for w in conflicts], [("2230", 9)])

    def test_mapping_errors(self):
        bad = [categories(seperator="/"), categories(drop_levels=-1), categories(depth=0),
               categories(value={"from": ["main:nope"]}), categories(value={"from": ["other:categorie"]}),
               categories(value={"from": ["main:categorie"], "transfrom": ["decimal"]}),
               categories(assign={"2226": ""}), categories(status="ACTIVE"), dict(FIXTURE, attributes={}),
               dict(FIXTURE, categories=None)]
        for m in bad:
            with self.subTest(m=m), self.assertRaises(mapping.MappingError):
                self.extract_with(m)
        with open(os.path.join(self.dir, "mappings", "taxonomy.json"), "w", encoding="utf-8") as f:
            json.dump(dict(FIXTURE, concept="products"), f)
        with self.assertRaisesRegex(mapping.MappingError, "concept must be 'taxonomy'"):
            extract.main_concept(self.dir, "taxonomy")

    def test_cli_prints_the_preview(self):
        self.write_mapping(FIXTURE)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = extract.main(["extract.py", self.dir, "taxonomy"])
        self.assertEqual(code, 0)
        lines = out.getvalue().splitlines()
        self.assertIn("Petit électroménager (6)", lines)
        self.assertIn("  Grille-pain (5)", lines)
        self.assertIn("  Bouilloire (1)", lines)
        self.assertEqual(lines[-1], "3 categories")

    def test_preview_lists_uncategorized_and_warnings(self):
        spec = {"value": None, "separator": None, "assign": {"2226": "Grille-pain", "nope": "X"}}
        text = taxonomy.format_preview(self.extract_with(dict(FIXTURE, categories=spec)))
        self.assertIn("Without category: 5 products (2227, 2228, 1004, nokey-0001, 2230)", text)
        self.assertIn("unknown_handle: ", text)

    def test_plan_shared_reads_it(self):
        t = self.extract_with(FIXTURE)
        st = self.state_with_decision("taxonomy", t)
        keys = [e["key"] for e in plan_shared.plan(st)]
        self.assertEqual(keys[0], payload.MARKER_KEY)
        self.assertEqual([k for k in keys if k.startswith("category:")], ["category:" + c["path"] for c in t["categories"]])
        self.assertEqual(plan_shared.plan(st)[2]["spec"], {"name": "Bouilloire", "parent": "Petit électroménager"})

    def test_payload_category(self):
        st = self.record_all_shared(self.state_with_decision("taxonomy", self.extract_with(FIXTURE)))
        product = next(p for p in self.products()["products"] if p["handle"] == "1004")
        iri = st["shared"]["category:Petit électroménager > Bouilloire"]["iri"]
        self.assertEqual(payload.product_body(product, st)["category"], iri)


class ProductRowsTest(ConceptTestCase):
    def test_rows_per_product(self):
        data, rows, sources, warnings = extract.product_rows(self.dir)
        self.assertEqual(list(rows), [p["handle"] for p in data["products"]])
        self.assertEqual([r.row for r in rows["2230"]], [8, 9])
        self.assertEqual(rows["2226"][0].source_row("prices"), 2)
        self.assertEqual(set(sources), {"main", "prices"})
        self.assertEqual(sorted({w["code"] for w in warnings}), ["join_missing", "join_unmatched"])

    def test_changed_sources_must_be_extracted_again(self):
        path = os.path.join(self.dir, "sources", "demo-tarifs.csv")
        self.addCleanup(write_demo_csv, path)
        with open(path, "ab") as f:
            f.write(b"\r\n")
        with self.assertRaisesRegex(mapping.MappingError, "extract.py <dir> products"):
            extract.product_rows(self.dir)

    def test_missing_concept_mapping(self):
        with self.assertRaisesRegex(mapping.MappingError, "taxonomy.json not found"):
            extract.main_concept(self.dir, "taxonomy")


if __name__ == "__main__":
    unittest.main()
