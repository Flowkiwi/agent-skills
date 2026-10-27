import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import clean  # noqa: E402
import mapping  # noqa: E402
from demo_files import write_demo_csv, write_demo_xlsx  # noqa: E402
from sources import Table  # noqa: E402

MAIN_AND_PRICES = {
    "version": 1, "concept": "products",
    "sources": {"main": {"file": "demo-catalogue.xlsx", "sheet": "Catalogue"},
                "prices": {"file": "demo-tarifs.csv", "join": {"column": "prices:SKU", "to": "main:sku_marchand"}}},
    "key": {"from": ["main:sku_marchand"]},
}


def make_workspace(test):
    tmp = tempfile.TemporaryDirectory()
    test.addCleanup(tmp.cleanup)
    sources = os.path.join(tmp.name, "sources")
    os.makedirs(sources)
    write_demo_xlsx(os.path.join(sources, "demo-catalogue.xlsx"))
    write_demo_csv(os.path.join(sources, "demo-tarifs.csv"))
    return tmp.name


class MappingTest(unittest.TestCase):
    def setUp(self):
        self.dir = make_workspace(self)
        self.join_warnings, self.warnings = [], []
        self.cleaner = clean.Cleaner()
        self.sources = mapping.load_sources(self.dir, MAIN_AND_PRICES, self.cleaner)
        self.rows = mapping.rows(self.sources, MAIN_AND_PRICES, self.join_warnings)

    def ev(self, spec, index=0):
        return mapping.evaluate(spec, self.rows[index], self.sources, self.warnings, "test")

    def codes(self):
        return [w["code"] for w in self.warnings]

    def test_references(self):
        self.assertEqual(self.ev({"from": ["main:accroche"]}), "Grille-pain 2 fentes Inox 1400W | TOASTINE")
        self.assertEqual(self.ev({"from": ["main:Descriptif court"]}), "Grille-pain 2 fentes Inox 1400W | TOASTINE")
        self.assertEqual(self.ev({"from": ["main:@M"]}), "argenté")
        with self.assertRaises(mapping.MappingError):
            self.ev({"from": ["main:nope"]})

    def test_priority_and_conflicts(self):
        spec = {"from": ["main:dim_poids_net", "main:poids_net"], "transform": ["decimal"]}
        self.assertEqual(self.ev(spec, 0), "1.8")
        self.assertEqual(self.warnings, [])                  # 1.8 vs 1,8: same number
        self.assertEqual(self.ev(spec, 1), "2.6")
        self.assertEqual(self.codes(), ["conflict"])
        self.assertIn("poids_net", self.warnings[0]["message"])
        self.assertEqual(self.ev(dict(spec, conflicts="ignore"), 1), "2.6")

    def test_join(self):
        price = {"from": ["prices:Prix public TTC", "main:prix"], "transform": ["decimal"]}
        self.assertEqual(self.ev(price, 0), "39.90")
        self.assertEqual(self.ev(price, 3), "27.90")
        self.assertEqual(self.codes().count("conflict"), 1)   # 27,90 vs 29,90
        join_codes = [w["code"] for w in self.join_warnings]
        self.assertEqual(join_codes.count("join_unmatched"), 1)            # 9999
        self.assertEqual(join_codes.count("join_missing"), 2)              # 1005, 1006

    def test_transforms(self):
        self.assertEqual(self.ev({"from": ["main:decongelation"], "transform": [{"boolean": ["Oui", "Non"]}]}, 1), "Non")
        self.assertEqual(self.ev({"from": ["main:dim_poids_brut"], "transform": ["decimal", {"scale_above": [100, 1000]}]}, 1), "1.964")
        self.assertEqual(self.ev({"from": ["main:matiere"], "map": {"inox": "Inox"}}, 2), "Inox")
        self.assertEqual(self.ev({"from": ["main:matiere"], "map": {"INOX": "Acier inoxydable"}, "map_ignore_case": True}, 0), "Acier inoxydable")
        self.assertEqual(self.ev({"from": ["main:accroche"], "transform": [{"regex": "^(.+?)(?: - (?:Noir|Rouge))?$"}]}, 5), "Grille-pain Duo")
        self.assertEqual(self.ev({"from": ["main:visuel_princ", "main:visuel_sec_9"], "pick": "all"}, 0), ["https://example.com/img/1001.jpg"])
        self.assertEqual(self.ev({"value": "x"}), "x")
        self.assertIsNone(self.ev({"from": ["main:prix"], "transform": ["integer"]}, 0))
        self.assertIn("not_a_number", self.codes())

    def test_text_to_html(self):
        self.assertEqual(mapping.apply_transform("a & b\nc\n\nd", "text_to_html"), "<p>a &amp; b<br>c</p><p>d</p>")

    def test_load_mapping_rejects_bad_origin(self):
        with self.assertRaises(mapping.MappingError):
            mapping.validate_mapping(dict(MAIN_AND_PRICES, key={"from": ["main:ean"], "origin": "guess"}))

    def test_rows_and_join_warnings(self):
        self.assertEqual([r.row for r in self.rows], [3, 4, 5, 6, 7, 8, 9])
        self.assertIsNone(self.rows[5].value("prices", 1))
        unmatched = [w for w in self.join_warnings if w["code"] == "join_unmatched"][0]
        self.assertEqual(unmatched["row"], 5)
        self.assertIn("9999", unmatched["message"])
        missing = [w for w in self.join_warnings if w["code"] == "join_missing"]
        self.assertEqual([w["row"] for w in missing], [8, 9])
        for warning in self.join_warnings:
            self.assertEqual(sorted(warning), ["code", "field", "handle", "message", "row", "stage"])
            self.assertIsNone(warning["field"])
            self.assertIsNone(warning["handle"])
            self.assertEqual(warning["stage"], "extract")

    def test_warning_shape(self):
        mapping.evaluate({"from": ["main:prix"], "transform": ["integer"]}, self.rows[0], self.sources,
                         self.warnings, "price", handle="1001")
        self.assertEqual(self.warnings, [{"row": 3, "handle": "1001", "code": "not_a_number", "field": "price",
                                          "message": self.warnings[0]["message"], "stage": "extract"}])

    def test_pick_join_and_split(self):
        spec = {"from": ["main:marque", "main:matiere"], "pick": "join", "sep": " / "}
        self.assertEqual(self.ev(spec, 0), "TOASTINE / Inox")
        self.assertEqual(self.ev({"from": ["main:marque", "main:coloris"], "pick": "join"}, 1), "Toastine\nNoir")
        self.assertEqual(self.ev({"from": ["main:categorie"], "transform": [{"split": "/"}]}, 0),
                         ["Catalogue", "PEM", "Grille-pain"])
        self.assertEqual(self.ev({"from": ["main:marque", "main:categorie"], "pick": "all",
                                  "transform": [{"split": "/"}]}, 0),
                         ["TOASTINE", "Catalogue", "PEM", "Grille-pain"])

    def test_pick_all_deduplicates(self):
        self.assertEqual(self.ev({"from": ["main:sku_marchand", "prices:SKU"], "pick": "all"}, 0), ["1001"])

    def test_map_null_target_and_default(self):
        spec = {"from": ["main:notice"], "map": {"Voir notice": None}, "default": "aucune"}
        self.assertEqual(self.ev(spec, 1), "aucune")
        self.assertEqual(self.ev(spec, 2), "aucune")
        self.assertEqual(self.ev({"from": ["main:notice"], "map": {"Voir notice": None}}, 1), None)
        self.assertEqual(self.ev({"from": ["main:coloris", "main:notice"], "pick": "all",
                                  "map": {"Voir notice": None}}, 1), ["Noir"])

    def test_conflict_after_map(self):
        spec = {"from": ["main:matiere", "main:coloris"], "map": {"Noir": "Inox"}}
        self.assertEqual(self.ev(spec, 4), "Inox")
        self.assertEqual(self.warnings, [])

    def test_cells_cleaned_once_and_only_when_read(self):
        self.assertEqual(self.cleaner.counts, {})
        spec = {"from": ["main:accroche"]}
        self.assertEqual(self.ev(spec, 1), 'Grille-pain 4 fentes "Stop Eject"')
        after_first = dict(self.cleaner.counts)
        self.assertEqual(after_first.get("quote"), 2)
        self.ev(spec, 1)
        self.assertEqual(self.cleaner.counts, after_first)

    def test_unknown_source_in_reference(self):
        with self.assertRaises(mapping.MappingError):
            self.ev({"from": ["other:sku"]})


class ColumnTest(unittest.TestCase):
    def source(self, rows, header_rows=1):
        self.cleaner = clean.Cleaner()
        return mapping.LoadedSource("x", Table("t.csv", "csv", rows), header_rows, self.cleaner)

    def test_ambiguous_label_suggests_letters(self):
        source = self.source([["Prix", "Prix", "EAN"], ["1", "2", "3"]])
        with self.assertRaises(mapping.MappingError) as ctx:
            source.column("Prix")
        self.assertIn("@A", str(ctx.exception))
        self.assertIn("@B", str(ctx.exception))
        self.assertEqual(source.column("@B"), 1)
        self.assertEqual(source.column("EAN"), 2)

    def test_code_before_label_and_headers_not_counted(self):
        source = self.source([["prix", "Prix TTC"], ["tarif", "prix"], ["1", "2"]], header_rows=2)
        self.assertEqual(source.column("prix"), 1)
        self.assertEqual(source.column("Prix TTC"), 1)
        self.assertEqual(self.cleaner.counts, {})

    def test_letter_out_of_range(self):
        with self.assertRaises(mapping.MappingError):
            self.source([["a", "b"], ["1", "2"]]).column("@Z")


class TransformTest(unittest.TestCase):
    def test_decimal_and_integer(self):
        self.assertEqual(mapping.apply_transform("1 299,90 €", "decimal"), "1299.90")
        self.assertEqual(mapping.apply_transform("20 %", "decimal"), "20")
        self.assertEqual(mapping.apply_transform("12 EUR", "decimal"), "12")
        self.assertEqual(mapping.apply_transform("6.0", "integer"), "6")
        warnings = []
        self.assertIsNone(mapping.apply_transform("6.5", "integer", warnings, "qty"))
        self.assertIsNone(mapping.apply_transform("1.234,5", "decimal", warnings, "qty"))
        self.assertEqual([w["code"] for w in warnings], ["not_a_number", "not_a_number"])
        self.assertEqual(warnings[0]["field"], "qty")

    def test_boolean(self):
        step = {"boolean": ["yes", "no"]}
        self.assertEqual(mapping.apply_transform("X", step), "yes")
        self.assertEqual(mapping.apply_transform("Faux", step), "no")
        warnings = []
        self.assertIsNone(mapping.apply_transform("peut-être", step, warnings))
        self.assertEqual(warnings[0]["code"], "not_a_boolean")

    def test_scale_above(self):
        warnings = []
        self.assertEqual(mapping.apply_transform("1500", {"scale_above": [100, 1000]}, warnings), "1.5")
        self.assertEqual(mapping.apply_transform("2000", {"scale_above": [100, 1000]}), "2")
        self.assertEqual(mapping.apply_transform("2.10", {"scale_above": [100, 1000]}), "2.10")
        self.assertEqual([w["code"] for w in warnings], ["scaled"])

    def test_regex(self):
        self.assertEqual(mapping.apply_transform("Ref 22/6", {"regex": r"\d+/\d+"}), "22/6")
        warnings = []
        self.assertIsNone(mapping.apply_transform("abc", {"regex": r"(\d+)"}, warnings))
        self.assertEqual(warnings[0]["code"], "no_match")

    def test_strip_html(self):
        html = "<p>Grille-pain&nbsp;2 fentes.<br/>Mati&egrave;re : Inox.</p><ul><li> A </li><li>B</li></ul>"
        self.assertEqual(mapping.apply_transform(html, "strip_html"),
                         "Grille-pain 2 fentes.\nMatière : Inox.\nA\nB")

    def test_text_to_html_escapes_and_trims(self):
        self.assertEqual(mapping.apply_transform("<b>\n\n\n x \n", "text_to_html"), "<p>&lt;b&gt;</p><p>x</p>")


class ValidateTest(unittest.TestCase):
    def assertRejected(self, **changes):
        with self.assertRaises(mapping.MappingError):
            mapping.validate_mapping(dict(MAIN_AND_PRICES, **changes))

    def test_valid_mapping(self):
        self.assertIs(mapping.validate_mapping(MAIN_AND_PRICES), MAIN_AND_PRICES)

    def test_rejections(self):
        self.assertRejected(version=2)
        self.assertRejected(concept="")
        self.assertRejected(sources={"a": {"file": "a.csv"}, "b": {"file": "b.csv"}})
        self.assertRejected(key={"from": ["main:ean"], "transform": ["tranform"]})
        self.assertRejected(key={"from": ["main:ean"], "transform": [{"scale_above": 100}]})
        self.assertRejected(key={"from": ["main:ean"], "transform": [{"regex": "("}]})
        self.assertRejected(key={"from": ["ean"]})
        self.assertRejected(key={"from": ["nowhere:ean"]})
        self.assertRejected(key={"from": ["main:ean"], "pick": "last"})
        self.assertRejected(key={"from": ["main:ean"], "value": "x"})
        self.assertRejected(group={"key": {"from": ["main:ref_com"]}, "origin": "guess"})

    def test_options_entries_hold_specs(self):
        options = [{"name": "Couleur", "value": {"from": ["main:coloris"], "transform": ["nope"]}}]
        self.assertRejected(options=options)
        options[0]["value"]["transform"] = []
        mapping.validate_mapping(dict(MAIN_AND_PRICES, options=options))

    def test_load_mapping_reads_utf8(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "products.json")
        data = dict(MAIN_AND_PRICES, key={"from": ["main:ean"], "origin": "agent", "reason": "Référence"})
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        self.assertEqual(mapping.load_mapping(path)["key"]["reason"], "Référence")


if __name__ == "__main__":
    unittest.main()
