import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import verify  # noqa: E402

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "verify.py")


def products(**descriptions):
    return {"products": [{"handle": h, "title": h.title(), "description_html": d} for h, d in descriptions.items()]}


class CompareTest(unittest.TestCase):
    def test_match_ignores_line_endings_and_outer_whitespace(self):
        self.assertTrue(verify.compare("<p>A</p>\n<p>B</p>", "<p>A</p>\r\n<p>B</p>\n")["match"])

    def test_detects_replaced_no_break_space(self):
        report = verify.compare("Prix : 5 €", "Prix : 5 €")
        self.assertFalse(report["match"])
        self.assertEqual(report["position"], 4)
        self.assertIn("\\u00a0", report["expected_context"])
        self.assertNotEqual(report["expected_sha256"], report["actual_sha256"])

    def test_detects_truncation(self):
        report = verify.compare("abcdef", "abc")
        self.assertEqual((report["position"], report["expected_length"], report["actual_length"]), (3, 6, 3))

    def test_null_expected_matches_empty(self):
        self.assertTrue(verify.compare(None, "\n")["match"])


class ReadbackTest(unittest.TestCase):
    def test_json_string_literal_is_decoded(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "readback.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write('"Prix\\u00a0: 5"\n')
            self.assertEqual(verify.read_readback(path), "Prix : 5")

    def test_raw_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "readback.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write('<p class="x">Hello</p>\n')
            self.assertEqual(verify.read_readback(path), '<p class="x">Hello</p>\n')


class SampleTest(unittest.TestCase):
    def test_special_descriptions_always_sampled(self):
        data = products(plain="<p>Plain</p>", nbsp="5 cl", quote="It’s", accent="Crème")
        handles = verify.sample(data, 0)
        self.assertEqual(handles, ["nbsp", "quote"])

    def test_rate_is_deterministic_and_roughly_respected(self):
        data = products(**{f"p{i}": "x" for i in range(1000)})
        first = verify.sample(data, 0.1)
        self.assertEqual(first, verify.sample(data, 0.1))
        self.assertTrue(50 < len(first) < 150, len(first))
        self.assertEqual(len(verify.sample(data, 1)), 1000)

    def test_pilots_are_sampled(self):
        self.assertEqual(verify.sample(products(a="x", b="y"), 0, pilots=["b"]), ["b"])


class VerifyCliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        with open(os.path.join(self.dir, "products.json"), "w", encoding="utf-8") as f:
            json.dump(products(mug="Mug 50 cl", cap="Cap"), f, ensure_ascii=False)
        self.readback = os.path.join(self.dir, "readback.txt")

    def tearDown(self):
        self.tmp.cleanup()

    def run_check(self, content):
        with open(self.readback, "w", encoding="utf-8") as f:
            f.write(content)
        return subprocess.run([sys.executable, SCRIPT, self.dir, "mug", "--field", "description",
                               "--readback-file", self.readback], capture_output=True, text=True)

    def test_exit_codes(self):
        self.assertEqual(self.run_check('"Mug\\u00a050\\u00a0cl"\n').returncode, 0)
        mismatch = self.run_check("Mug 50 cl\n")
        self.assertEqual(mismatch.returncode, 1)
        self.assertEqual(json.loads(mismatch.stdout)["position"], 3)

    def test_sample_output_file(self):
        output = os.path.join(self.dir, "verify-sample.json")
        result = subprocess.run([sys.executable, SCRIPT, "sample", self.dir, "--rate", "0", "--output", output],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        with open(output, encoding="utf-8") as f:
            self.assertEqual(json.load(f), ["mug"])


if __name__ == "__main__":
    unittest.main()
