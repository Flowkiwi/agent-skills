import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import clean  # noqa: E402


class CleanTextTest(unittest.TestCase):
    def check(self, raw, expected, counts):
        found = {}
        self.assertEqual(clean.clean_text(raw, found), expected)
        self.assertEqual(found, counts)

    def test_guillemets_swallow_inner_no_break_spaces(self):
        self.check("Fonction « Stop Eject »", 'Fonction "Stop Eject"', {"quote": 2})
        self.check("« Oui »", '"Oui"', {"quote": 2})

    def test_curly_quotes_and_apostrophes(self):
        self.check("L’Atelier ‘a’ „b“ ”c” 5′",
                   "L'Atelier 'a' \"b\" \"c\" 5'", {"apostrophe": 4, "quote": 4})

    def test_ellipsis_dashes_spaces(self):
        self.check("Puissance…", "Puissance...", {"ellipsis": 1})
        self.check("1,7 L — sans fil – blanc", "1,7 L - sans fil - blanc", {"dash": 2})
        self.check("5 € et 1 400 W", "5 € et 1 400 W", {"nbsp": 3})
        self.check("a  b\t c", "a b c", {"spaces": 2})

    def test_invisible_newlines_trim_nfc(self):
        self.check("a​b­c﻿d\x07e", "abcde", {"invisible": 4})
        self.check("a\r\nb\rc d", "a\nb\nc\nd", {"newline": 3})
        self.check("  a \n b  ", "a\nb", {"trim": 2})
        self.check("crème", "crème", {"nfc": 1})

    def test_kept_characters_and_html(self):
        self.check("Inox® 18/10™ ©2024 90°C éàç", "Inox® 18/10™ ©2024 90°C éàç", {})
        self.check("<p>Prix&nbsp;:  5</p>", "<p>Prix&nbsp;: 5</p>", {"spaces": 1})
        self.check('Fonction "stop eject', 'Fonction "stop eject', {})

    def test_empty_and_none(self):
        self.assertIsNone(clean.clean_text(None))
        self.assertEqual(clean.clean_text("   "), "")

    def test_idempotent(self):
        raw = "  « L’inox » — 1 400… "
        once = clean.clean_text(raw)
        self.assertEqual(clean.clean_text(once), once)
        self.assertEqual(once, "\"L'inox\" - 1 400...")

    def test_config(self):
        self.assertEqual(clean.clean_text("a — b", disabled=("dash",)), "a — b")
        counts = {}
        self.assertEqual(clean.clean_text("20×30", counts, replacements={"×": "x"}), "20x30")
        self.assertEqual(counts, {"custom": 1})

    def test_cleaner_accumulates_counts(self):
        cleaner = clean.Cleaner()
        cleaner.text("a…")
        cleaner.text("b…")
        self.assertEqual(cleaner.counts, {"ellipsis": 2})

    def test_cleaner_uses_config(self):
        cleaner = clean.Cleaner({"disabled": ["dash"], "replacements": {"×": "x"}})
        self.assertEqual(cleaner.text("2×3 — 4"), "2x3 — 4")
        self.assertEqual(cleaner.counts, {"custom": 1})

    def test_table_lists_every_rule_aligned(self):
        lines = clean.table().split("\n")
        self.assertEqual(len(lines), len(clean.RULES) + 2)
        self.assertEqual(len({len(line) for line in lines}), 1)
        self.assertEqual([r[0] for r in clean.RULES][:2], ["nfc", "newline"])


class LoadConfigTest(unittest.TestCase):
    def tmp_with(self, content):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        os.makedirs(os.path.join(tmp.name, "mappings"))
        with open(os.path.join(tmp.name, "mappings", "cleaning.json"), "w", encoding="utf-8") as f:
            f.write(content)
        return tmp.name

    def test_missing_file_gives_empty_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(clean.load_config(tmp), {})

    def test_reads_config(self):
        config = clean.load_config(self.tmp_with('{"disabled": ["dash", "custom"], "replacements": {"×": "x"}}'))
        self.assertEqual(config["disabled"], ["dash", "custom"])

    def test_load_config_rejects_unknown_rule(self):
        with self.assertRaises(ValueError):
            clean.load_config(self.tmp_with('{"disabled": ["nope"]}'))


if __name__ == "__main__":
    unittest.main()
