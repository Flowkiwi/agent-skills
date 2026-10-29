import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import columns  # noqa: E402
import inventory  # noqa: E402
from demo_files import write_demo_csv, write_demo_xlsx  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "mapping-products.json")


def make_workspace(test):
    tmp = tempfile.TemporaryDirectory()
    test.addCleanup(tmp.cleanup)
    directory = tmp.name
    os.makedirs(os.path.join(directory, "sources"))
    os.makedirs(os.path.join(directory, "mappings"))
    write_demo_xlsx(os.path.join(directory, "sources", "demo-catalogue.xlsx"))
    write_demo_csv(os.path.join(directory, "sources", "demo-tarifs.csv"))
    shutil.copy(FIXTURE, os.path.join(directory, "mappings", "products.json"))
    inventory.write_inventory(directory, inventory.inventory(directory))
    return directory


class ColumnsTest(unittest.TestCase):
    def setUp(self):
        self.dir = make_workspace(self)
        self.entries = columns.propose(self.dir)
        self.by_letter = {e["letter"]: e for e in self.entries if e["column"].startswith("main:")}

    def role(self, letter):
        e = self.by_letter[letter]
        return e["role"], e["type"]

    def test_triage_of_demo_columns(self):
        self.assertEqual(self.role("A"), ("core", None))
        self.assertEqual(self.role("G"), ("metafield", "number_decimal"))
        self.assertEqual(self.role("H")[0], "ignore")
        self.assertIn("similar to G (1 conflicts)", self.by_letter["H"]["reason"])
        self.assertEqual(self.role("I"), ("metafield", "number_decimal"))
        self.assertIn("suspect scale", self.by_letter["I"]["reason"])
        self.assertEqual(self.role("J")[0], "ignore")
        self.assertEqual(self.role("K"), ("attribute", "list"))
        self.assertEqual(self.role("L"), ("attribute", "list"))
        self.assertEqual(self.role("Q"), ("ignore", None))
        self.assertEqual(self.role("R"), ("metafield", "single_line_text_field"))
        self.assertEqual(self.by_letter["K"]["column"], "main:matiere")

    def test_join_columns_are_core_and_every_source_is_listed(self):
        prices = {e["column"]: e for e in self.entries if e["column"].startswith("prices:")}
        self.assertEqual(prices["prices:SKU"]["role"], "core")
        self.assertEqual(prices["prices:SKU"]["reason"], "used by products")
        self.assertEqual(prices["prices:Prix public TTC"]["role"], "core")
        self.assertEqual(len(self.by_letter), 18)

    def test_entry_shape_and_rule_reasons(self):
        self.assertEqual(set(self.by_letter["A"]), {"column", "letter", "label", "role", "type", "reason"})
        self.assertEqual(self.by_letter["A"]["reason"], "used by products")
        self.assertEqual(self.by_letter["Q"]["reason"], "empty")
        self.assertEqual(self.by_letter["H"]["reason"],
                         'similar to G (1 conflicts): merge into one "from", winner first')

    def test_missing_inventory_is_an_error(self):
        os.remove(os.path.join(self.dir, "inventory.json"))
        with self.assertRaises(ValueError):
            columns.propose(self.dir)

    def test_cli_writes_the_proposal_under_tmp_and_prints_a_table(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(columns.main(["columns.py", "propose", self.dir]), 0)
        path = os.path.join(self.dir, "tmp", "columns-proposal.json")
        with open(path, encoding="utf-8") as f:
            self.assertEqual(json.load(f), self.entries)
        self.assertFalse(os.path.exists(os.path.join(self.dir, "concepts")))
        lines = out.getvalue().splitlines()
        self.assertTrue(lines[0].startswith("| column "))
        self.assertEqual(len({len(line) for line in lines}), 1)
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(columns.main(["columns.py", "propose"]), 2)
        self.assertIn("Usage: columns.py propose <dir>", err.getvalue())


def column(**changes):
    base = {"letter": "B", "kind": "text", "flags": [], "fill_rate": 1.0, "max_length": 10, "distinct": 3,
            "filled": 10}
    base.update(changes)
    return base


SAME_AS_A = [{"flag": "same_values", "with": "A"}]
URL_LABEL = [{"flag": "label_mismatch", "expected": "url", "matching": 0.0}]


class TriageRulesTest(unittest.TestCase):
    CASES = [  # (rule, column, sheet columns, expected)
        ("2 empty", column(flags=[{"flag": "empty"}], fill_rate=0.0), None, ("ignore", None, "empty")),
        ("2 sparse", column(fill_rate=0.1), None, ("ignore", None, "sparse")),
        ("3 duplicate", column(flags=SAME_AS_A), None, ("ignore", None, "duplicate of A")),
        ("3 constant column", column(flags=[{"flag": "constant", "value": "Inox"}], distinct=1), None,
         ("ignore", None, "constant: Inox")),
        ("3 constant column that also copies another", column(flags=SAME_AS_A + [{"flag": "constant", "value": "1"}],
                                                              distinct=1), None, ("ignore", None, "constant: 1")),
        ("3 same values, one distinct value", column(flags=SAME_AS_A, distinct=1), None, ("attribute", "list", "1 distinct values")),
        ("3 two yes/no columns", column(flags=SAME_AS_A, kind="boolean", distinct=2),
         [column(letter="A", kind="boolean")], ("attribute", "list", "yes/no values")),
        ("3 yes/no copy of a text column", column(flags=SAME_AS_A, kind="boolean", distinct=2),
         [column(letter="A")], ("ignore", None, "duplicate of A")),
        ("5 long text under a url label", column(flags=URL_LABEL, max_length=300), None,
         ("description", None, "long text under a label that says url")),
        ("5 short text under a url label", column(flags=URL_LABEL), None,
         ("ignore", None, "label says url, content is text")),
        ("6 urls", column(kind="url"), None, ("ignore", None, "media: URLs listed in the final report")),
        ("7 html", column(kind="html"), None, ("description", None, "html")),
        ("7 long text", column(max_length=300), None, ("description", None, "long text")),
        ("10 few distinct values", column(distinct=6, filled=8), None, ("attribute", "list", "6 distinct values")),
        ("10 half of the filled cells", column(distinct=10, filled=20), None, ("attribute", "list", "10 distinct values")),
        ("11 too many for few cells", column(distinct=7, filled=10), None, ("attribute", "string", "free text")),
        ("11 over 20 distinct values", column(distinct=21, filled=100), None, ("attribute", "string", "free text")),
    ]

    def test_rules(self):
        for rule, col, sheet, expected in self.CASES:
            with self.subTest(rule=rule):
                self.assertEqual(columns.triage(col, ["v"], sheet), expected)


def pair(*specs):
    """Sheet columns A, B...: (fill_rate, label, flags) each."""
    return [column(letter=chr(65 + i), fill_rate=rate, label=label, flags=flags)
            for i, (rate, label, flags) in enumerate(specs)]


class DuplicateWinnerTest(unittest.TestCase):
    def triage_all(self, sheet):
        return [columns.triage(c, ["v"], sheet) for c in sheet]

    def test_the_better_filled_column_wins_whatever_its_position(self):
        sheet = pair((0.78, "Hauteur du produit", []),
                     (0.97, "[Dimensions] Hauteur produit (cm)", [{"flag": "same_values", "with": "A"}]))
        a, b = self.triage_all(sheet)
        self.assertEqual(a, ("ignore", None, "duplicate of B"))
        self.assertEqual(b, ("attribute", "list", "3 distinct values, winner over A"))

    def test_the_earlier_wins_a_better_fill_rate_and_gets_the_suffix(self):
        sheet = pair((0.9, "Hauteur", []), (0.8, "Hauteur (cm)", [{"flag": "same_values", "with": "A"}]))
        a, b = self.triage_all(sheet)
        self.assertEqual(a, ("attribute", "list", "3 distinct values, winner over B"))
        self.assertEqual(b, ("ignore", None, "duplicate of A"))

    def test_equal_fill_rate_prefers_the_explicit_unit(self):
        for label in ("Hauteur (cm)", "Poids [kg]", "Puissance (W)", "Debit (m3/h)"):
            with self.subTest(label=label):
                sheet = pair((0.9, "Mesure", []), (0.9, label, [{"flag": "same_values", "with": "A"}]))
                self.assertEqual(self.triage_all(sheet)[0], ("ignore", None, "duplicate of B"))

    def test_a_category_in_brackets_is_not_a_unit(self):
        sheet = pair((0.9, "Hauteur", []), (0.9, "[Dimensions] Hauteur", [{"flag": "same_values", "with": "A"}]))
        self.assertEqual(self.triage_all(sheet)[1], ("ignore", None, "duplicate of A"))

    def test_equal_everything_keeps_the_earlier_column(self):
        sheet = pair((0.9, "Hauteur (cm)", []), (0.9, "Hauteur (mm)", [{"flag": "same_values", "with": "A"}]))
        a, b = self.triage_all(sheet)
        self.assertEqual(a[2], "3 distinct values, winner over B")
        self.assertEqual(b, ("ignore", None, "duplicate of A"))

    def test_similar_label_names_the_winner_and_keeps_the_conflicts(self):
        similar = [{"flag": "similar_label", "with": "A", "conflicts": 2}]
        sheet = pair((0.5, "Poids net", []), (0.9, "Poids net (kg)", similar))
        a, b = self.triage_all(sheet)
        self.assertEqual(a, ("ignore", None, 'similar to B (2 conflicts): merge into one "from", winner first'))
        self.assertEqual(b[2], "3 distinct values, winner over A")

    def test_a_partner_that_is_ignored_anyway_does_not_take_part(self):
        sheet = pair((0.1, "Poids", []), (0.9, "Poids (kg)", [{"flag": "same_values", "with": "A"}]))
        self.assertEqual(self.triage_all(sheet)[1][2], "3 distinct values")

    def test_two_losers_of_one_winner(self):
        sheet = pair((0.9, "Hauteur", []), (0.5, "Haut", [{"flag": "same_values", "with": "A"}]),
                     (0.4, "Haut. (cm)", [{"flag": "same_values", "with": "A"}]))
        a, b, c = self.triage_all(sheet)
        self.assertEqual(a[2], "3 distinct values, winner over B, C")
        self.assertEqual((b[2], c[2]), ("duplicate of A", "duplicate of A"))


class FormatTableTest(unittest.TestCase):
    def entry(self, letter, role, label="Label", reason="r", kind=None):
        return {"column": "main:" + letter.lower(), "letter": letter, "label": label, "role": role, "type": kind,
                "reason": reason}

    def test_sorted_by_role_then_letter_and_cut(self):
        entries = [self.entry("A", "ignore"), self.entry("B", "core", label="L" * 100),
                   self.entry("C", "attribute", kind="list__single_line_text_field"), self.entry("D", "core"),
                   self.entry("AA", "metafield", reason="x" * 80)]
        lines = columns.format_table(entries).splitlines()
        self.assertEqual([line.split("|")[1].strip() for line in lines[2:]],
                         ["main:b", "main:d", "main:c", "main:aa", "main:a"])
        self.assertEqual(len({len(line) for line in lines}), 1)
        self.assertIn(" " + "L" * 37 + "... |", lines[2])
        self.assertIn(" " + "x" * 57 + "... |", lines[5])
        self.assertIn(" list__single_line_text_field |", lines[4])
        self.assertEqual(entries[1]["label"], "L" * 100)


if __name__ == "__main__":
    unittest.main()
