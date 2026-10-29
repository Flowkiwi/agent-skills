import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import inventory  # noqa: E402
from demo_files import write_demo_csv, write_demo_xlsx  # noqa: E402
from sources import Table  # noqa: E402


def make_table(rows):
    return Table("test.csv", "csv", rows)


def make_workspace(test):
    tmp = tempfile.TemporaryDirectory()
    test.addCleanup(tmp.cleanup)
    directory = tmp.name
    sources = os.path.join(directory, "sources")
    os.makedirs(os.path.join(sources, "previous"))
    write_demo_xlsx(os.path.join(sources, "demo-catalogue.xlsx"))
    write_demo_csv(os.path.join(sources, "demo-tarifs.csv"))
    write_demo_csv(os.path.join(sources, "previous", "20260101-000000-demo-tarifs.csv"))
    write_demo_csv(os.path.join(sources, ".hidden.csv"))
    with open(os.path.join(sources, "demo-catalogue.xls"), "wb") as f:
        f.write(b"original xls kept next to its conversion")
    return directory


class InventoryTest(unittest.TestCase):
    def setUp(self):
        self.dir = make_workspace(self)
        self.inv = inventory.inventory(self.dir)
        sheet = self.inv["files"][0]["sheets"][0]
        self.sheet, self.cols = sheet, {c["letter"]: c for c in sheet["columns"]}

    def flags(self, letter):
        return {f["flag"]: f for f in self.cols[letter]["flags"]}

    def test_headers_and_counts(self):
        self.assertEqual(self.sheet["header_rows"], 2)
        self.assertEqual(self.sheet["rows"], 7)
        self.assertEqual(self.cols["A"]["code"], "sku_marchand")
        self.assertEqual(self.cols["D"]["samples"][1], 'Grille-pain 4 fentes "Stop Eject"')
        self.assertEqual(self.inv["files"][0]["sheets"][1]["merged"], ["A1:C1"])

    def test_defects_of_the_sample_file_are_flagged(self):
        self.assertIn("empty", self.flags("Q"))
        self.assertEqual(self.flags("H")["similar_label"], {"flag": "similar_label", "with": "G", "conflicts": 1})
        self.assertEqual(self.flags("J")["label_mismatch"]["matching"], 0.33)
        self.assertEqual(self.flags("I")["suspect_scale"]["values"], ["1964"])
        self.assertEqual(self.flags("L")["boolean_variants"]["values"], ["NON", "OUI", "non"])
        self.assertEqual(self.flags("K")["value_variants"]["groups"], [["Inox", "inox"]])
        self.assertEqual(self.cols["R"]["kind"], "date")
        self.assertEqual(self.cols["E"]["kind"], "html")

    def test_same_values(self):
        table = make_table([["a", "b"], ["1", "1"], ["2", "2"], [None, "3"]])
        cols = inventory.sheet_inventory(table, header_rows=1)["columns"]
        self.assertIn({"flag": "same_values", "with": "A"}, cols[1]["flags"])

    def test_csv_single_header(self):
        tarifs = self.inv["files"][1]["sheets"][0]
        self.assertEqual((tarifs["header_rows"], tarifs["rows"]), (1, 4))
        self.assertEqual(tarifs["columns"][1]["label"], "Prix public TTC")

    def test_label_tokens(self):
        self.assertEqual(inventory.label_tokens("[Dimensions] Poids Net (kg)"), frozenset({"poids", "net"}))
        self.assertEqual(inventory.label_tokens("Poids net du produit"), frozenset({"poids", "net"}))
        self.assertEqual(inventory.label_tokens("[Sécurité] Pieds antidérapant"),
                         inventory.label_tokens("Pieds antidérapants"))

    def test_only_supported_current_sources_are_listed(self):
        self.assertEqual([f["file"] for f in self.inv["files"]], ["demo-catalogue.xlsx", "demo-tarifs.csv"])
        self.assertEqual(len(self.inv["files"][0]["sha256"]), 64)

    def test_column_and_key_order(self):
        self.assertEqual(list(self.inv), ["version", "files"])
        self.assertEqual(list(self.inv["files"][0]), ["file", "sha256", "sheets"])
        self.assertEqual(list(self.sheet), ["sheet", "hidden", "header_rows", "rows", "merged", "columns"])
        self.assertEqual(self.cols["A"], {
            "letter": "A", "label": "Sku Marchand", "code": "sku_marchand", "filled": 5, "fill_rate": 0.71,
            "distinct": 5, "max_length": 4, "kind": "number", "samples": ["1001", "1002", "1004", "1005", "1006"],
            "flags": []})
        self.assertEqual(list(self.cols["A"]), ["letter", "label", "code", "filled", "fill_rate", "distinct",
                                                "max_length", "kind", "samples", "flags"])

    def test_empty_column_has_only_the_empty_flag(self):
        self.assertEqual(self.cols["Q"]["flags"], [{"flag": "empty"}])
        self.assertEqual((self.cols["Q"]["filled"], self.cols["Q"]["kind"]), (0, "empty"))

    def test_samples_limit(self):
        inv = inventory.inventory(self.dir, samples=2)
        self.assertEqual(inv["files"][0]["sheets"][0]["columns"][0]["samples"], ["1001", "1002"])

    def test_cli_writes_json_and_prints_flagged_columns(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = inventory.main([self.dir, "--samples", "3"])
        self.assertEqual(code, 0)
        with open(os.path.join(self.dir, "inventory.json"), encoding="utf-8") as f:
            text = f.read()
        self.assertIn('"Référence Commerciale"', text)
        written = json.loads(text)
        self.assertEqual(len(written["files"][0]["sheets"][0]["columns"][0]["samples"]), 3)
        printed = out.getvalue()
        self.assertIn("demo-catalogue.xlsx / Catalogue: 7 rows, 18 columns, header rows 2, merged none", printed)
        self.assertIn("demo-catalogue.xlsx / Notes: 0 rows, 1 columns, header rows 1, merged A1:C1", printed)
        self.assertIn("Q Visuel secondaire 9 [visuel_sec_9]: empty, filled 0.0, flags empty", printed)
        self.assertNotIn("A Sku Marchand", printed)
        self.assertEqual(os.listdir(self.dir).count("inventory.json"), 1)
        self.assertEqual([n for n in os.listdir(self.dir) if n.endswith(".tmp")], [])


class ConstantFlagTest(unittest.TestCase):
    def flags(self, rows):
        return inventory.sheet_inventory(make_table(rows), header_rows=1)["columns"][0]["flags"]

    def test_one_distinct_value_in_at_least_two_cells(self):
        self.assertEqual(self.flags([["a"], ["Inox"], ["Inox"], [None]]), [{"flag": "constant", "value": "Inox"}])

    def test_one_filled_cell_is_not_constant(self):
        self.assertEqual(self.flags([["a"], ["Inox"], [None]]), [])

    def test_an_empty_column_is_not_constant(self):
        self.assertEqual(self.flags([["a"], [None], [None]]), [{"flag": "empty"}])

    def test_two_values_are_not_constant(self):
        self.assertEqual(self.flags([["a"], ["Inox"], ["inox"]])[0]["flag"], "value_variants")

    def test_format_inventory_shows_the_value(self):
        inv = {"files": [{"file": "f.csv", "sheets": [inventory.sheet_inventory(
            make_table([["a", "b"], ["Inox", "1"], ["Inox", "2"]]), header_rows=1)]}]}
        self.assertIn("A a: text, filled 1.0, flags constant Inox", inventory.format_inventory(inv))


class RowReaderTest(unittest.TestCase):
    def setUp(self):
        self.dir = make_workspace(self)

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = inventory.main([self.dir] + list(args))
        return code, out.getvalue(), err.getvalue()

    def test_prints_one_line_per_column_of_the_sheet_row(self):
        code, out, _ = self.run_cli("--row", "3")
        lines = out.splitlines()
        self.assertEqual(code, 0)
        self.assertEqual(lines[0], "demo-catalogue.xlsx / Catalogue row 3:")
        self.assertEqual(len(lines), 1 + 18)
        self.assertEqual(lines[1], "A Sku Marchand [sku_marchand]: 1001")
        self.assertEqual(lines[4], "D Descriptif court [accroche]: Grille-pain 2 fentes Inox 1400W | TOASTINE")
        self.assertEqual(lines[8], "H [Dimensions] Poids Net (kg) [dim_poids_net]: 1.8")
        self.assertTrue(any(line.endswith(": (empty)") for line in lines))

    def test_columns_by_code_label_or_letter(self):
        code, out, _ = self.run_cli("--row", "3", "--columns", "sku_marchand,Descriptif court,@G")
        self.assertEqual(code, 0)
        self.assertEqual(out.splitlines()[1:], [
            "A Sku Marchand [sku_marchand]: 1001",
            "D Descriptif court [accroche]: Grille-pain 2 fentes Inox 1400W | TOASTINE",
            "G Poids net du produit [poids_net]: 1,8"])

    def test_several_rows_and_the_second_file_and_sheet(self):
        code, out, _ = self.run_cli("--row", "2", "--row", "3", "--file", "demo-tarifs.csv")
        self.assertEqual(code, 0)
        self.assertEqual([line for line in out.splitlines() if line.endswith(":")],
                         ["demo-tarifs.csv / csv row 2:", "demo-tarifs.csv / csv row 3:"])
        code, out, _ = self.run_cli("--row", "1", "--sheet", "Notes")
        self.assertEqual((code, out.splitlines()[0]), (0, "demo-catalogue.xlsx / Notes row 1:"))

    def test_does_not_write_inventory_json(self):
        self.run_cli("--row", "3")
        self.assertFalse(os.path.exists(os.path.join(self.dir, "inventory.json")))

    def test_errors_exit_1_without_a_trace(self):
        for args in (["--row", "99"], ["--row", "0"], ["--row", "3", "--columns", "nope"],
                     ["--row", "3", "--columns", "@ZZ"], ["--row", "3", "--file", "nope.csv"],
                     ["--row", "3", "--sheet", "nope"]):
            with self.subTest(args=args):
                code, out, err = self.run_cli(*args)
                self.assertEqual((code, out), (1, ""))
                self.assertTrue(err.startswith("error: "))

    def test_options_without_row_are_refused(self):
        code, _, err = self.run_cli("--columns", "A")
        self.assertEqual(code, 2)
        self.assertIn("need --row", err)


class HelpersTest(unittest.TestCase):
    def test_detect_header_rows(self):
        self.assertEqual(inventory.detect_header_rows([["Nom", "Prix"], ["nom", "prix_ttc"], ["a", "1"]]), 2)
        self.assertEqual(inventory.detect_header_rows([["nom", "prix"], ["nom", "prix_ttc"]]), 1)
        self.assertEqual(inventory.detect_header_rows([["Nom", "Prix"], ["Grille-pain", "39,90"]]), 1)
        self.assertEqual(inventory.detect_header_rows([["Nom"]]), 1)

    def test_parse_number(self):
        self.assertEqual(inventory.parse_number("1,8"), Decimal("1.8"))
        self.assertEqual(inventory.parse_number("-12.50"), Decimal("-12.50"))
        self.assertEqual(inventory.parse_number("1 400"), Decimal("1400"))
        self.assertIsNone(inventory.parse_number("39,90 €"))
        self.assertIsNone(inventory.parse_number("1.000,50"))
        self.assertIsNone(inventory.parse_number("abc"))

    def test_column_kind(self):
        self.assertEqual(inventory.column_kind([]), "empty")
        self.assertEqual(inventory.column_kind(["Oui", "non", "X"]), "boolean")
        self.assertEqual(inventory.column_kind(["2024-03-15", "2023-01-01T10:00:00"]), "date")
        self.assertEqual(inventory.column_kind(["1,5", "2"]), "number")
        self.assertEqual(inventory.column_kind(["https://a.example/x.pdf", "voir"]), "url")
        self.assertEqual(inventory.column_kind(["plain", "<b>bold</b>"]), "html")
        self.assertEqual(inventory.column_kind(["a < b", "plain"]), "text")

    def test_label_mismatch_needs_filled_values(self):
        table = make_table([["Lien fiche", "Photos"], [None, "voir catalogue"], [None, "https://a.example/1.jpg"]])
        cols = inventory.sheet_inventory(table, header_rows=1)["columns"]
        self.assertEqual(cols[0]["flags"], [{"flag": "empty"}])
        self.assertEqual(cols[1]["flags"], [])

    def test_mostly_yes_no_column_flags_boolean_variants(self):
        table = make_table([["Accessoires"], ["OUI"], ["non"], ["NON"], ["Pince à toasts"], ["pince à toasts"],
                            ["OUI"]])
        column = inventory.sheet_inventory(table, header_rows=1)["columns"][0]
        flags = {f["flag"]: f for f in column["flags"]}
        self.assertEqual(column["kind"], "text")
        self.assertEqual(flags["boolean_variants"]["values"], ["NON", "OUI", "non"])
        self.assertEqual(flags["value_variants"]["groups"], [["Pince à toasts", "pince à toasts"]])

    def test_text_column_with_few_yes_no_values_has_no_boolean_variants(self):
        table = make_table([["Matière"], ["Inox"], ["inox"], ["non"], ["NON"], ["Plastique"]])
        column = inventory.sheet_inventory(table, header_rows=1)["columns"][0]
        flags = {f["flag"]: f for f in column["flags"]}
        self.assertNotIn("boolean_variants", flags)
        self.assertEqual(flags["value_variants"]["groups"], [["Inox", "inox"], ["NON", "non"]])

    def test_similar_label_conflicts_compare_numbers(self):
        table = make_table([["Poids (kg)", "Poids"], ["1,50", "1.5"], ["Bleu", "bleu"], ["2", "3"]])
        cols = inventory.sheet_inventory(table, header_rows=1)["columns"]
        self.assertIn({"flag": "similar_label", "with": "A", "conflicts": 1}, cols[1]["flags"])
