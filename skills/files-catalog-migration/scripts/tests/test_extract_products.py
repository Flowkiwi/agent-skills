import contextlib
import copy
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import catalog  # noqa: E402
import clean  # noqa: E402
import extract  # noqa: E402
from demo_files import DEMO_CODES, DEMO_LABELS, DEMO_ROWS, write_demo_csv, write_demo_xlsx, write_xlsx  # noqa: E402

SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
CODES = os.path.join(SCRIPTS, "codes.py")
EXTRACT = os.path.join(SCRIPTS, "extract.py")
FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "mapping-products.json")
FIRST_DEMO_ROW = 3


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class ExtractProductsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        os.makedirs(os.path.join(self.dir, "sources"))
        os.makedirs(os.path.join(self.dir, "mappings"))
        write_demo_xlsx(os.path.join(self.dir, "sources", "demo-catalogue.xlsx"))
        write_demo_csv(os.path.join(self.dir, "sources", "demo-tarifs.csv"))
        shutil.copy(FIXTURE, os.path.join(self.dir, "mappings", "products.json"))
        self.products_path = os.path.join(self.dir, "products.json")
        self.data = self.run_extract()

    def run_extract(self):
        return extract.main_products(self.dir)

    def by_handle(self):
        return {p["handle"]: p for p in self.data["products"]}

    def write_fixes(self, fixes):
        with open(os.path.join(self.dir, "fixes.json"), "w", encoding="utf-8") as f:
            json.dump(fixes, f, ensure_ascii=False)

    def rewrite_demo(self, swap_rows=None, retitle=None):
        rows = copy.deepcopy(DEMO_ROWS)
        if swap_rows:
            a, b = (n - FIRST_DEMO_ROW for n in swap_rows)
            rows[a], rows[b] = rows[b], rows[a]
        for number, title in (retitle or {}).items():
            rows[number - FIRST_DEMO_ROW][DEMO_CODES.index("accroche")] = title
        write_xlsx(os.path.join(self.dir, "sources", "demo-catalogue.xlsx"), [
            {"name": "Catalogue", "rows": [DEMO_LABELS, DEMO_CODES] + rows, "no_refs_rows": [9]},
            {"name": "Notes", "rows": [["Export du 12/03 - demo"]], "merged": ["A1:C1"]},
        ])

    def extract_rows(self, table, price_column=False):
        with open(os.path.join(self.dir, "sources", "rows.csv"), "w", encoding="utf-8", newline="") as f:
            f.write("Ref;Valeur\r\n" + "".join("%s;%s\r\n" % tuple(row) for row in table))
        second = {"from": ["main:Valeur"]}
        mapping = {"version": 1, "concept": "products", "sources": {"main": {"file": "rows.csv", "header_rows": 1}},
                   "key": {"from": ["main:Ref"]}, "group": None, "product": {}, "variant": {}}
        if price_column:
            mapping["variant"]["price"] = dict(second, transform=["decimal"])
        else:
            mapping["product"]["title"] = second
        return extract.extract_products(self.dir, mapping, {}, clean.Cleaner())

    def test_handles_and_grouping(self):
        self.assertEqual([p["handle"] for p in self.data["products"]], ["2226", "2227", "2228", "1004", "nokey-0001", "2230"])
        duo = self.by_handle()["2230"]
        self.assertEqual(duo["title"], "Grille-pain Duo")
        self.assertEqual(duo["options"], [{"name": "Couleur", "values": ["Noir", "Rouge"]}])
        self.assertEqual([v["sku"] for v in duo["variants"]], ["1005", "1006"])
        self.assertEqual(self.by_handle()["2226"]["options"], [])

    def test_product_entry(self):
        duo = self.by_handle()["2230"]
        self.assertEqual(duo, {
            "handle": "2230", "key": "2230", "row": 8, "rows": {"main": [8, 9]}, "title": "Grille-pain Duo",
            "description_html": "<p>Grille-pain Duo, deux coloris.</p>", "description_origin": "source",
            "vendor": "TOASTINE", "type": None, "product_category": "Catalogue/PEM/Grille-pain", "tags": [],
            "status": None, "published": None, "options": [{"name": "Couleur", "values": ["Noir", "Rouge"]}],
            "variants": [
                {"sku": "1005", "sku_generated": False, "barcode": "3601234560050", "barcode_generated": False,
                 "option_values": ["Noir"], "price": "49.90", "compare_at_price": None, "cost": None, "grams": None,
                 "taxable": None, "market_prices": {}},
                {"sku": "1006", "sku_generated": False, "barcode": "3601234560067", "barcode_generated": False,
                 "option_values": ["Rouge"], "price": "49.90", "compare_at_price": None, "cost": None, "grams": None,
                 "taxable": None, "market_prices": {}}],
            "images": ["https://example.com/img/1005.jpg"], "metafields": {}})
        first = self.by_handle()["2226"]
        self.assertEqual((first["key"], first["rows"]), ("2226", {"main": [3], "prices": [2]}))
        self.assertEqual(first["variants"][0]["compare_at_price"], "49.90")
        self.assertEqual(self.by_handle()["2228"]["variants"][0]["sku"], None)
        self.assertEqual(self.by_handle()["2228"]["variants"][0]["barcode"], None)

    def test_top_level(self):
        self.assertEqual(list(self.data), ["version", "products", "warnings", "cleaning", "sources", "metafield_columns"])
        self.assertEqual(self.data["version"], 1)
        self.assertEqual(self.data["metafield_columns"], {})
        main = self.data["sources"]["main"]
        self.assertEqual((main["file"], main["sheet"], len(main["sha256"])), ("demo-catalogue.xlsx", "Catalogue", 64))
        self.assertEqual(self.data["sources"]["prices"]["sheet"], "csv")
        self.assertEqual(read_json(self.products_path), self.data)

    def test_cleaned_values_and_counts(self):
        p = self.by_handle()
        self.assertEqual(p["2227"]["title"], 'Grille-pain 4 fentes "Stop Eject"')
        self.assertEqual(p["2228"]["title"], "Grille-pain vintage crème")
        self.assertEqual(p["2228"]["vendor"], "L'Atelier Pain")
        self.assertEqual(p["1004"]["title"], "Bouilloire 1,7 L - sans fil")
        self.assertEqual(p["1004"]["description_html"], "<p>Bouilloire sans fil 1,7 L.</p>")
        self.assertEqual(p["1004"]["variants"][0]["price"], "27.90")
        for rule in ("quote", "apostrophe", "dash", "nbsp", "invisible", "spaces", "trim"):
            self.assertGreater(self.data["cleaning"].get(rule, 0), 0, rule)

    def test_warnings(self):
        codes = {w["code"] for w in self.data["warnings"]}
        self.assertTrue({"generated_key", "conflict", "missing_sku", "missing_barcode", "join_unmatched"} <= codes)

    def test_identity_specs_ignore_conflicts(self):
        identity = [w for w in self.data["warnings"] if w["code"] == "conflict" and w["field"] in ("key", "group.key")]
        self.assertEqual(identity, [])
        price = [w for w in self.data["warnings"] if w["code"] == "conflict"]
        self.assertEqual([(w["field"], w["row"], w["handle"], w["stage"]) for w in price], [("variant.price", 6, "1004", "extract")])

    def test_identity_is_stable(self):
        fixes = read_json(os.path.join(self.dir, "fixes.json"))
        self.assertEqual(fixes["generated_keys"], [{"source": "main", "row": 7, "title": "Toaster sans référence", "key": "nokey-0001"}])
        self.rewrite_demo(swap_rows=(3, 4), retitle={3: "Grille-pain 2 fentes Inox 1400 W"})
        again = self.run_extract()
        self.assertEqual(sorted(p["handle"] for p in again["products"]), sorted(["2226", "2227", "2228", "1004", "nokey-0001", "2230"]))
        self.assertEqual(len(read_json(os.path.join(self.dir, "fixes.json"))["generated_keys"]), 1)

    def test_generated_key_follows_its_row_when_retitled(self):
        self.rewrite_demo(retitle={7: "Toaster inconnu"})
        again = {p["title"]: p["handle"] for p in self.run_extract()["products"]}
        self.assertEqual(again["Toaster inconnu"], "nokey-0001")
        self.assertEqual(len(read_json(os.path.join(self.dir, "fixes.json"))["generated_keys"]), 1)

    def test_generated_key_follows_its_title_when_moved(self):
        self.rewrite_demo(swap_rows=(7, 8))
        again = {p["handle"]: p for p in self.run_extract()["products"]}
        self.assertEqual((again["nokey-0001"]["row"], again["nokey-0001"]["title"]), (8, "Toaster sans référence"))

    def test_new_keyless_row_gets_next_key(self):
        self.write_fixes({"generated_keys": [{"source": "main", "row": 12, "title": "Autre", "key": "nokey-0007"},
                                             {"source": "main", "row": 2, "title": "Ancien", "key": "nokey-0003"}]})
        again = self.run_extract()
        self.assertIn("nokey-0008", [p["handle"] for p in again["products"]])
        keys = read_json(os.path.join(self.dir, "fixes.json"))["generated_keys"]
        self.assertEqual([k["key"] for k in keys], ["nokey-0007", "nokey-0003", "nokey-0008"])

    def test_generated_codes_survive_re_extract(self):
        subprocess.run([sys.executable, CODES, "fill", self.products_path, "--prefix", "2", "--start", "1",
                        "--sku-pattern", "{handle}-{index}"], check=True, stdout=subprocess.DEVNULL)
        again = self.run_extract()
        v = {p["handle"]: p["variants"][0] for p in again["products"]}
        self.assertEqual((v["2228"]["sku"], v["2228"]["barcode"]), ("2228-1", "2000000000015"))
        self.assertEqual((v["nokey-0001"]["sku"], v["nokey-0001"]["barcode"]), ("NOKEY-0001-1", "2000000000022"))
        self.assertTrue(v["2228"]["sku_generated"] and v["2228"]["barcode_generated"])

    def test_slug_collision_is_blocking(self):
        data = self.extract_rows([["A b", "T1"], ["a-b", "T2"]])
        self.assertEqual([p["handle"] for p in data["products"]], ["a-b"])
        self.assertIn("duplicate_handle", [w["code"] for w in catalog.blocking(data)])

    def test_duplicate_key_without_grouping(self):
        data = self.extract_rows([["A1", "T1"], ["A1", "T2"]])
        self.assertEqual([(p["handle"], p["title"]) for p in data["products"]], [("a1", "T1")])
        duplicate = [w for w in data["warnings"] if w["code"] == "duplicate_key"]
        self.assertEqual([(w["row"], w["stage"]) for w in duplicate], [(3, "extract")])
        self.assertIn(duplicate[0], catalog.blocking(data))

    def test_suspect_unit(self):
        data = self.extract_rows([["a", "2.1"], ["b", "1964"], ["c", "2.0"]], price_column=True)
        self.assertEqual([w["code"] for w in data["warnings"] if w["code"] == "suspect_unit"], ["suspect_unit"])

    def test_group_conflict(self):
        rows = copy.deepcopy(DEMO_ROWS)
        rows[8 - FIRST_DEMO_ROW][DEMO_CODES.index("marque")] = "Toastine Pro"
        write_xlsx(os.path.join(self.dir, "sources", "demo-catalogue.xlsx"),
                   [{"name": "Catalogue", "rows": [DEMO_LABELS, DEMO_CODES] + rows}])
        again = self.run_extract()
        conflicts = [w for w in again["warnings"] if w["code"] == "group_conflict"]
        self.assertEqual([(w["row"], w["handle"], w["field"]) for w in conflicts], [(9, "2230", "product.vendor")])
        self.assertEqual({p["handle"]: p for p in again["products"]}["2230"]["vendor"], "Toastine Pro")

    def test_fields_fix(self):
        self.write_fixes({"fields": {"2226": {"title": "Grille-pain Toastine"}}})
        self.assertEqual({p["handle"]: p for p in self.run_extract()["products"]}["2226"]["title"], "Grille-pain Toastine")

    def test_files_keep_a_normal_mode(self):
        for name in ("products.json", "fixes.json"):
            self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.dir, name)).st_mode), 0o644, name)

    def test_cli(self):
        result = subprocess.run([sys.executable, EXTRACT, self.dir, "products"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("6 products, 7 variants", result.stdout)
        self.assertIn("quote: ", result.stdout)
        self.assertIn("generated_key (1)", result.stdout)
        result = subprocess.run([sys.executable, EXTRACT, self.dir, "taxonomy"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)

    def test_mapping_error_is_reported(self):
        path = os.path.join(self.dir, "mappings", "products.json")
        mapping = read_json(path)
        mapping["key"] = {"from": ["main:nope"]}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(mapping, f)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(extract.main(["extract.py", self.dir, "products"]), 1)
        self.assertIn("nope", err.getvalue())


class SlugifyTest(unittest.TestCase):
    def test_slugify(self):
        self.assertEqual(extract.slugify("REF 22/6"), "ref-22-6")
        self.assertEqual(extract.slugify("  Crème brûlée -- XL "), "creme-brulee-xl")
        self.assertEqual(extract.slugify("2230"), "2230")
        self.assertEqual(extract.slugify("///"), "")


if __name__ == "__main__":
    unittest.main()
