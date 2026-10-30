import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import clean  # noqa: E402
import client_review  # noqa: E402
from concept_helpers import read_json, run, write_workspace  # noqa: E402

CONCEPTS = ("taxonomy", "metafields", "metaobjects", "prices")
TYPOGRAPHIC = "’ …«»—"


class ClientReviewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        """The demo workspace with plan 1 and plan 2 fixtures extracted, codes filled, data_language fr."""
        tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        cls.template = os.path.join(tmp.name, "demo")
        d = cls.template
        os.makedirs(d)
        write_workspace(d, CONCEPTS)
        run("inventory.py", d)
        run("extract.py", d, "all")
        run("codes.py", "fill", os.path.join(d, "products.json"), "--prefix", "2", "--start", "1",
            "--sku-pattern", "{handle}-{index}")
        run("extract.py", d, "prices")
        run("state.py", "init", d, "--client", "demo", "--mcp-url", "https://demo.invalid/api/mcp")
        run("state.py", "decide", d, "--key", "data_language", "--value-json", '"fr"', "--origin", "developer")
        run("state.py", "decide", d, "--key", "status_mode", "--value-json", '"draft"', "--origin", "developer")
        for concept in CONCEPTS:
            run("state.py", "decide", d, "--key", concept, "--value-file",
                os.path.join(d, "concepts", concept + ".json"), "--origin", "developer")

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = os.path.join(tmp.name, "demo")
        shutil.copytree(self.template, self.dir)
        self.text = client_review.render(self.dir)

    def edit_mapping(self, concept, change):
        path = os.path.join(self.dir, "mappings", concept + ".json")
        m = read_json(path)
        change(m)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(m, f, ensure_ascii=False)

    def decide(self, key, value, origin="developer", reason=None):
        args = ["state.py", "decide", self.dir, "--key", key, "--value-json", json.dumps(value), "--origin", origin]
        run(*(args + (["--reason", reason] if reason else [])))

    def test_sections_and_content(self):
        self.assertTrue(self.text.startswith("# Catalogue demo : points à valider\n"))
        self.assertLess(self.text.index("## Produits et variantes"), self.text.index("## Fiches liées"))
        self.assertIn("- Les lignes de même référence commerciale sont les coloris d'un même modèle. "
                      "(colonne \"Référence Commerciale\", \"Sku Marchand\")", self.text)
        self.assertIn("1 produit sans référence a reçu une référence provisoire : Toaster sans référence", self.text)
        self.assertIn("Colonnes vides : Visuel secondaire 9", self.text)
        self.assertIn("2 codes EAN et 2 SKU provisoires ont été générés", self.text)
        self.assertIn("1 variante sans prix : NOKEY-0001-1", self.text)

    def test_layout(self):
        products = self.text[self.text.index("## Produits et variantes"):self.text.index("## Fiches liées")]
        self.assertEqual(products.splitlines()[:3], ["## Produits et variantes", "", "### Choix faits pour vous"])
        self.assertIn("\n### Données manquantes\n\n- 1 produit sans référence", products)
        self.assertIn("- La colonne Marque devient une fiche par marque reliée aux produits. (colonne \"Marque\")",
                      self.text)
        self.assertIn("## Prix de vente\n\n### Données manquantes\n\n- 1 variante sans prix : NOKEY-0001-1",
                      self.text)
        for absent in ("## Catégories et caractéristiques", "## Collections", "## Champs complémentaires",
                       "## Autres choix", "### Questions"):
            self.assertNotIn(absent, self.text)

    def test_client_ready(self):
        self.assertNotIn("/api/", self.text)
        self.assertNotIn("main:", self.text)
        self.assertEqual(clean.clean_text(self.text), self.text)
        self.assertEqual(client_review.render(self.dir), self.text)

    def test_no_typographic_character(self):
        self.edit_mapping("products", lambda m: m["group"].update(
            reason="Les lignes d’une même référence sont des coloris… « Duo » "
                   "— un modèle."))
        text = client_review.render(self.dir)
        self.assertIn("- Les lignes d'une même référence sont des coloris... \"Duo\" - un modèle.", text)
        for char in TYPOGRAPHIC:
            self.assertNotIn(char, text)

    def test_question_disappears_once_answered(self):
        self.edit_mapping("products", lambda m: m["group"].update(question="Les coloris sont-ils bien des variantes ?"))
        self.assertIn("- Les coloris sont-ils bien des variantes ?", client_review.render(self.dir))
        self.edit_mapping("products", lambda m: m["group"].update(origin="client"))
        text = client_review.render(self.dir)
        self.assertNotIn("Les coloris sont-ils", text)
        self.assertNotIn("Les lignes de même référence", text)

    def test_question_without_agent_origin(self):
        self.edit_mapping("taxonomy", lambda m: m["attributes"][0].update(
            question="La matière Inox est-elle bien de l'acier inoxydable ?"))
        text = client_review.render(self.dir)
        self.assertIn("## Catégories et caractéristiques\n\n### Questions\n\n"
                      "- La matière Inox est-elle bien de l'acier inoxydable ?", text)

    def test_agent_decision(self):
        self.decide("codes", {"prefix": "2"}, "agent", "Codes EAN internes en attendant ceux du client.")
        self.decide("prices", read_json(os.path.join(self.dir, "concepts", "prices.json")), "agent",
                    "Prix TTC de la colonne Prix public.")
        text = client_review.render(self.dir)
        self.assertIn("## Autres choix\n\n### Choix faits pour vous\n\n- Codes EAN internes en attendant ceux du client.",
                      text)
        self.assertIn("## Prix de vente\n\n### Choix faits pour vous\n\n- Prix TTC de la colonne Prix public.\n", text)

    def test_generated_descriptions(self):
        self.decide("description_generation", {"approved": True})
        run("extract.py", self.dir, "products")
        generated = sum(1 for p in read_json(os.path.join(self.dir, "products.json"))["products"]
                        if p.get("description_origin") == "generated")
        self.assertEqual(generated, 3)
        self.assertIn("- 3 descriptions ont été rédigées à partir des caractéristiques du fichier",
                      client_review.render(self.dir))

    def test_counted_labels(self):
        labels = client_review.LABELS["fr"]
        self.assertEqual(client_review.counted(labels, "missing_price", 2, skus="A, B"), "2 variantes sans prix : A, B")
        self.assertEqual(client_review.counted(labels, "missing_price", 1, skus="A"), "1 variante sans prix : A")
        self.assertEqual(client_review.counted(labels, "generated_codes", 1, barcodes=1, skus=3),
                         "1 code EAN et 3 SKU provisoires ont été générés")
        no_singular = {k: v for k, v in labels.items() if not k.endswith("_one")}
        self.assertEqual(client_review.counted(no_singular, "missing_price", 1, skus="A"), "1 variantes sans prix : A")

    def test_same_keys_in_every_language(self):
        fr, en = client_review.LABELS["fr"], client_review.LABELS["en"]
        self.assertEqual(set(fr), set(en))
        self.assertEqual(set(fr["concepts"]), set(en["concepts"]))

    def test_unknown_language(self):
        self.decide("data_language", "nl")
        with self.assertRaises(client_review.ReviewError):
            client_review.render(self.dir)

    def test_cli(self):
        result = run("client_review.py", self.dir)
        self.assertEqual(result.stdout.strip(), "0 questions, 3 choices, 4 missing data items")
        with open(os.path.join(self.dir, "client-review.md"), encoding="utf-8") as f:
            self.assertEqual(f.read(), self.text + "\n")

    def test_cli_labels(self):
        self.decide("data_language", "nl")
        result = run("client_review.py", self.dir, check=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn("no labels for 'nl': pass --labels", result.stderr)
        path = os.path.join(self.dir, "tmp-labels.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(dict(client_review.LABELS["en"], title="Catalogus {client}"), f)
        run("client_review.py", self.dir, "--labels", path)
        with open(os.path.join(self.dir, "client-review.md"), encoding="utf-8") as f:
            self.assertTrue(f.read().startswith("# Catalogus demo\n"))
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"title": "Catalogus {client}"}, f)
        result = run("client_review.py", self.dir, "--labels", path, check=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn("labels file misses", result.stderr)


if __name__ == "__main__":
    unittest.main()
