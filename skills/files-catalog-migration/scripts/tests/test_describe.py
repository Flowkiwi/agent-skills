import copy
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import describe  # noqa: E402
import extract  # noqa: E402
from demo_files import write_demo_csv, write_demo_xlsx  # noqa: E402
from mapping import MappingError  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "mapping-products.json")
CANDIDATE_2228 = "<p>Grille-pain vintage crème</p><ul><li>Matière : Inox</li><li>Coloris : Crème</li></ul>"


class DescribeTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        os.makedirs(os.path.join(self.dir, "sources"))
        os.makedirs(os.path.join(self.dir, "mappings"))
        write_demo_xlsx(os.path.join(self.dir, "sources", "demo-catalogue.xlsx"))
        write_demo_csv(os.path.join(self.dir, "sources", "demo-tarifs.csv"))
        shutil.copy(FIXTURE, os.path.join(self.dir, "mappings", "products.json"))

    def extract(self):
        self.data = extract.main_products(self.dir)
        return {p["handle"]: p for p in self.data["products"]}

    def write_state_decision(self, key, value):
        with open(os.path.join(self.dir, "state.json"), "w", encoding="utf-8") as f:
            json.dump({"decisions": {key: value}}, f)

    def edit_generate(self, **changes):
        path = os.path.join(self.dir, "mappings", "products.json")
        with open(path, encoding="utf-8") as f:
            m = json.load(f)
        generate = m["product"]["description_html"]["generate"]
        for key, value in changes.items():
            if value is None:
                generate.pop(key, None)
            else:
                generate[key] = value
        with open(path, "w", encoding="utf-8") as f:
            json.dump(m, f, ensure_ascii=False)

    def test_is_poor(self):
        self.assertTrue(describe.is_poor(None, "T"))
        self.assertTrue(describe.is_poor("<p>Grille-pain vintage crème</p>", "Grille-pain vintage crème"))
        self.assertTrue(describe.is_poor("<p>Court.</p>", "T", min_length=20))
        self.assertFalse(describe.is_poor("<p>Grille-pain 2 fentes.<br/>Matière : Inox.</p>", "T", min_length=20))

    def test_is_poor_edges(self):
        self.assertTrue(describe.is_poor("<p> </p>", "T"))
        self.assertTrue(describe.is_poor("<p>GRILLE-PAIN  Vintage</p>", "grille-pain vintage", min_length=5))
        self.assertFalse(describe.is_poor("<p>Assez long pour passer.</p>", None, min_length=10))
        self.assertEqual(describe.plain_text("<p>A  b</p><p>c&amp;d</p>"), "A b c&d")
        self.assertEqual(describe.DEFAULT_MIN_LENGTH, 80)

    def test_generate_states_only_given_values(self):
        html = describe.generate("Grille-pain & co", [("Matière", "Inox", ""), ("Puissance", "1400", " W"), ("Coloris", None, "")], " : ")
        self.assertEqual(html, "<p>Grille-pain &amp; co</p><ul><li>Matière : Inox</li><li>Puissance : 1400 W</li></ul>")
        self.assertIsNone(describe.generate("T", [("Matière", "", "")]))

    def test_generate_escapes_values_and_skips_empty_intro(self):
        self.assertEqual(describe.generate("", [("A", "<b>x</b>", "")]), "<ul><li>A: &lt;b&gt;x&lt;/b&gt;</li></ul>")

    def test_extract_keeps_poor_without_approval(self):
        p = self.extract()["2228"]
        self.assertEqual(p["description_html"], "Grille-pain vintage crème")
        self.assertEqual(p["description_candidate"], CANDIDATE_2228)
        self.assertEqual(p["description_origin"], "source")
        self.assertNotIn("description_source", p)

    def test_poor_description_warning_is_not_blocking(self):
        self.extract()
        poor = [w for w in self.data["warnings"] if w["code"] == "poor_description"]
        self.assertEqual(sorted(w["handle"] for w in poor), ["2227", "2228", "nokey-0001"])
        self.assertEqual({w["stage"] for w in poor}, {"extract"})
        self.assertEqual(poor[0]["field"], "product.description_html")
        self.assertNotIn("poor_description", [w["code"] for w in extract.catalog.blocking(self.data)])

    def test_extract_uses_generated_after_approval(self):
        self.write_state_decision("description_generation", {"approved": True})
        p = self.extract()["2228"]
        self.assertTrue(p["description_html"].startswith("<p>Grille-pain vintage"))
        self.assertEqual(p["description_html"], CANDIDATE_2228)
        self.assertEqual((p["description_origin"], p["description_source"]), ("generated", "Grille-pain vintage crème"))
        self.assertNotIn("description_candidate", p)
        self.assertEqual(self.extract()["2226"]["description_origin"], "source")
        self.assertNotIn("poor_description", [w["code"] for w in self.data["warnings"]])

    def test_missing_description_is_generated_too(self):
        self.write_state_decision("description_generation", {"approved": True})
        p = self.extract()["2227"]
        self.assertEqual(p["description_html"],
                         '<p>Grille-pain 4 fentes "Stop Eject"</p><ul><li>Matière : Acier inoxydable</li><li>Coloris : Noir</li></ul>')
        self.assertEqual((p["description_origin"], p["description_source"]), ("generated", None))

    def test_other_decision_value_is_not_an_approval(self):
        self.write_state_decision("description_generation", {"approved": False})
        self.assertEqual(self.extract()["2228"]["description_html"], "Grille-pain vintage crème")

    def test_features_use_cleaning_and_field_keys(self):
        self.edit_generate(intro="{vendor} - {title}", features=[
            {"label": "Poids", "from": ["main:poids_net"], "transform": ["decimal"], "suffix": " kg"},
            {"label": "Matière", "from": ["main:matiere"], "map": {"inox": "Inox"}}])
        p = self.extract()["2228"]
        self.assertEqual(p["description_candidate"],
                         "<p>L'Atelier Pain - Grille-pain vintage crème</p><ul><li>Poids : 1.5 kg</li><li>Matière : Inox</li></ul>")

    def test_no_feature_value_means_no_candidate(self):
        self.edit_generate(features=[{"label": "Notice", "from": ["main:notice"]}])
        p = self.extract()["2228"]
        self.assertNotIn("description_candidate", p)
        self.assertNotIn("2228", [w["handle"] for w in self.data["warnings"] if w["code"] == "poor_description"])

    def write_fixes(self, fixes):
        with open(os.path.join(self.dir, "fixes.json"), "w", encoding="utf-8") as f:
            json.dump(fixes, f, ensure_ascii=False)

    def test_intro_uses_the_fixed_title(self):
        self.write_fixes({"fields": {"2227": {"title": "Grille-pain 4 fentes Stop Eject"}}})
        self.assertEqual(self.extract()["2227"]["description_candidate"],
                         "<p>Grille-pain 4 fentes Stop Eject</p><ul><li>Matière : Acier inoxydable</li>"
                         "<li>Coloris : Noir</li></ul>")

    def test_empty_intro_placeholder_means_no_candidate(self):
        self.edit_generate(intro="{title} de la marque {vendor}.")
        self.write_fixes({"fields": {"2228": {"vendor": None}}})
        p = self.extract()
        self.assertNotIn("description_candidate", p["2228"])
        self.assertEqual(p["2228"]["description_html"], "Grille-pain vintage crème")
        self.assertTrue(p["2227"]["description_candidate"].startswith(
            '<p>Grille-pain 4 fentes "Stop Eject" de la marque Toastine.</p>'))
        self.assertEqual(describe.render_intro("{title} de {vendor}", {"title": "T", "vendor": " "}), None)

    def test_without_generate_nothing_changes(self):
        path = os.path.join(self.dir, "mappings", "products.json")
        with open(path, encoding="utf-8") as f:
            m = json.load(f)
        del m["product"]["description_html"]["generate"]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(m, f)
        p = self.extract()["2228"]
        self.assertNotIn("description_candidate", p)
        self.assertNotIn("poor_description", [w["code"] for w in self.data["warnings"]])

    def test_mapping_validation(self):
        with open(FIXTURE, encoding="utf-8") as f:
            base = json.load(f)
        self.assertIsNone(extract.check_products_mapping(base))
        cases = {
            "intro placeholder": lambda g: g.update(intro="{price}"),
            "when": lambda g: g.update(when="always"),
            "min_length": lambda g: g.update(min_length="20"),
            "features": lambda g: g.update(features=[]),
            "label": lambda g: g.update(features=[{"from": ["main:matiere"]}]),
            "unknown source": lambda g: g.update(features=[{"label": "X", "from": ["nope:matiere"]}]),
            "unknown key": lambda g: g.update(extra=1),
            "origin": lambda g: g.update(origin="robot"),
        }
        for name, change in cases.items():
            m = copy.deepcopy(base)
            change(m["product"]["description_html"]["generate"])
            with self.assertRaises(MappingError, msg=name):
                extract.check_products_mapping(m)
        m = copy.deepcopy(base)
        m["product"]["title"]["generate"] = m["product"]["description_html"]["generate"]
        with self.assertRaises(MappingError):
            extract.check_products_mapping(m)


if __name__ == "__main__":
    unittest.main()
