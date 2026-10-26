import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import codes  # noqa: E402
import shopify_csv  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "products.csv")


def variants(data, handle):
    return next(p for p in data["products"] if p["handle"] == handle)["variants"]


class CodesTest(unittest.TestCase):
    def setUp(self):
        self.data = shopify_csv.parse(FIXTURE)

    def test_check_digit_known_value(self):
        self.assertEqual(codes.ean13_check_digit("400638133393"), "1")

    def test_generate_prefix_and_length(self):
        generated = codes.generate_barcodes("2", 1, 2, set())
        self.assertEqual(len(generated), 2)
        for barcode in generated:
            self.assertEqual(len(barcode), 13)
            self.assertTrue(barcode.startswith("2"))
        self.assertEqual(generated[0], "200000000001" + codes.ean13_check_digit("200000000001"))

    def test_generate_skips_used(self):
        first = codes.generate_barcodes("2", 1, 1, set())[0]
        generated = codes.generate_barcodes("2", 1, 1, {first})
        self.assertNotEqual(generated[0], first)

    def test_fill_only_missing(self):
        codes.fill(self.data, "2", 1, None)
        tshirt = variants(self.data, "tshirt-basic")
        self.assertTrue(tshirt[0]["barcode"].startswith("2"))
        self.assertTrue(tshirt[0]["barcode_generated"])
        self.assertEqual(tshirt[1]["barcode"], "4006381333931")
        self.assertFalse(tshirt[1]["barcode_generated"])

    def test_fill_never_reuses_existing_barcodes(self):
        taken = "200000000001" + codes.ean13_check_digit("200000000001")
        variants(self.data, "mug")[0]["barcode"] = taken
        codes.fill(self.data, "2", 1, None)
        all_barcodes = [v["barcode"] for p in self.data["products"] for v in p["variants"]]
        self.assertEqual(len(all_barcodes), len(set(all_barcodes)))

    def test_fill_sku_pattern(self):
        codes.fill(self.data, "2", 1, "{handle}-{index}")
        poster = variants(self.data, "poster")[0]
        self.assertEqual(poster["sku"], "POSTER-1")
        self.assertTrue(poster["sku_generated"])

    def test_fill_without_sku_pattern_leaves_sku_null(self):
        codes.fill(self.data, "2", 1, None)
        self.assertIsNone(variants(self.data, "poster")[0]["sku"])

    def test_fill_summary(self):
        summary = codes.fill(self.data, "2", 1, "{handle}-{index}")
        self.assertEqual(summary["barcodes"], 2)
        self.assertEqual(summary["skus"], 1)
        self.assertEqual(summary["next_barcode_start"], 3)

    def test_fill_is_idempotent(self):
        codes.fill(self.data, "2", 1, "{handle}-{index}")
        snapshot = copy.deepcopy(self.data)
        summary = codes.fill(self.data, "2", 1, "{handle}-{index}")
        self.assertEqual(summary["barcodes"], 0)
        self.assertEqual(summary["skus"], 0)
        self.assertEqual(self.data, snapshot)

    def test_preview_does_not_modify_and_lists_samples(self):
        snapshot = copy.deepcopy(self.data)
        preview = codes.preview(self.data, "2", 1, "{handle}-{index}")
        self.assertEqual(self.data, snapshot)
        self.assertEqual(preview["barcodes"], 2)
        self.assertEqual(preview["skus"], 1)
        self.assertIn({"handle": "poster", "variant": 1, "sku": "POSTER-1"}, preview["sku_samples"])
        self.assertEqual(len(preview["barcode_samples"]), 2)

    def test_apply_fixes(self):
        fixes = {"skus": {"cap": {"TS-S": "TS-S-2"}}, "option_names": {"poster": {"Sz": "Size"}}, "skip": ["mug"]}
        codes.apply_fixes(self.data, fixes)
        codes.apply_fixes(self.data, fixes)
        self.assertEqual(variants(self.data, "cap")[0]["sku"], "TS-S-2")
        self.assertEqual(variants(self.data, "tshirt-basic")[0]["sku"], "TS-S")
        poster = next(p for p in self.data["products"] if p["handle"] == "poster")
        self.assertEqual(poster["options"][0]["name"], "Size")
        self.assertNotIn("mug", [p["handle"] for p in self.data["products"]])

    def test_fill_carries_previous_generated_codes(self):
        previous = copy.deepcopy(self.data)
        codes.fill(previous, "2", 1, "{handle}-{index}")
        fresh = shopify_csv.parse(FIXTURE)
        summary = codes.fill(fresh, "2", 50, "{handle}-{index}", previous=previous)
        self.assertEqual(variants(fresh, "tshirt-basic")[0]["barcode"], variants(previous, "tshirt-basic")[0]["barcode"])
        self.assertTrue(variants(fresh, "tshirt-basic")[0]["barcode_generated"])
        self.assertEqual(summary["barcodes"], 0)
        self.assertEqual(summary["carried"], 3)


if __name__ == "__main__":
    unittest.main()
