import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import diff  # noqa: E402
import payload  # noqa: E402
import state  # noqa: E402
from phase_helpers import (created_line, make_products, make_state, read_json, run_cli,  # noqa: E402
                           write_workspace)


class DiffTest(unittest.TestCase):
    def setUp(self):
        self.products = make_products()             # 2226 (SKU 1001), 2230 (SKUs 1005, 1006)
        self.st = make_state(self.products)
        state.merge(self.st, [created_line("2226"), created_line("2230")], self.products)

    def test_unchanged(self):
        self.assertEqual(diff.diff(self.st, self.products)["unchanged"], 2)

    def test_field_variant_and_metafield_changes(self):
        p = self.products["products"][0]
        p["title"] = "Grille-pain Toastine"
        p["variants"].append(dict(p["variants"][0], sku="1001-B", barcode="3601234560999"))
        d = diff.diff(self.st, self.products)["changed"]["2226"]
        self.assertEqual(d["product"]["title"][1], "Grille-pain Toastine")
        self.assertEqual(d["variants"], {"added": ["1001-B"]})

    def test_grouping_change_is_rekeyed(self):
        duo = self.products["products"][1]
        split = [dict(duo, handle=v["sku"], variants=[v], options=[]) for v in duo["variants"]]
        self.products["products"] = [self.products["products"][0]] + split
        state.sync_products(self.st, self.products)
        d = diff.diff(self.st, self.products)
        self.assertEqual((d["new"], d["removed"]), (["1005", "1006"], ["2230"]))
        self.assertEqual(d["rekeyed"], [{"removed": "2230", "new": ["1005", "1006"], "skus": ["1005", "1006"]}])
        self.assertEqual(d["removal"], {"2230": "delete_rekeyed"})

    def test_reused_product_is_unmanaged(self):
        self.st["products"]["2226"]["items"][0]["status"] = "reused"
        self.products["products"][0]["title"] = "X"
        d = diff.diff(self.st, self.products)
        self.assertEqual((d["unmanaged"], list(d["changed"])), (["2226"], []))

    def test_removed_without_rekey_waits_for_removed_action(self):
        self.products["products"] = self.products["products"][:1]
        d = diff.diff(self.st, self.products)
        self.assertEqual((d["removed"], d["rekeyed"], d["removal"]), (["2230"], [], {"2230": "removed_action"}))

    def test_removed_found_without_sync(self):
        self.products["products"] = self.products["products"][:1]
        self.assertEqual(diff.diff(self.st, self.products)["removed"], ["2230"])

    def test_archived_still_absent_is_already_archived(self):
        gone = {"products": self.products["products"][:1]}
        state.sync_products(self.st, gone)
        state.merge(self.st, [dict(created_line("2230"), status="archived", items=[])], gone)
        d = diff.diff(self.st, gone)
        self.assertEqual((d["removed"], d["already_archived"]), ([], ["2230"]))

    def test_deleted_never_appears(self):
        self.st["products"]["2230"]["status"] = "deleted"
        self.products["products"] = self.products["products"][:1]
        d = diff.diff(self.st, self.products)
        self.assertEqual((d["removed"], d["already_archived"], d["new"]), ([], [], []))

    def test_reused_removed_is_unmanaged(self):
        self.st["products"]["2230"]["items"][0]["status"] = "reused"
        self.products["products"] = self.products["products"][:1]
        d = diff.diff(self.st, self.products)
        self.assertEqual((d["removed"], d["unmanaged"]), ([], ["2230"]))

    def test_reused_child_is_never_targeted(self):
        self.st["products"]["2230"]["items"][2]["status"] = "reused"    # variant 1006
        duo = self.products["products"][1]
        duo["variants"][1]["barcode"] = "3601234560999"
        duo["title"] = "Grille-pain Duo noir et rouge"
        d = diff.diff(self.st, self.products)
        self.assertEqual(d["changed"]["2230"], {"product": {"title": ["Grille-pain Duo", "Grille-pain Duo noir et rouge"]}})
        self.assertEqual(d["unmanaged_items"], {"2230": ["variants.changed.1006.barcode"]})
        self.assertEqual(d["unmanaged"], [])

    def test_regrouping_into_existing_product_is_rekeyed(self):
        single, duo = self.products["products"]
        single["variants"] += duo["variants"]
        self.products["products"] = [single]
        d = diff.diff(self.st, self.products)
        self.assertEqual(d["removed"], ["2230"])
        self.assertEqual(d["rekeyed"], [{"removed": "2230", "new": [], "skus": ["1005", "1006"], "changed": ["2226"]}])
        self.assertEqual(d["removal"], {"2230": "delete_rekeyed"})
        self.assertEqual(d["changed"]["2226"]["variants"], {"added": ["1005", "1006"]})

    def regroup_into_2226(self):
        single, duo = self.products["products"]
        single["variants"] += duo["variants"]
        self.products["products"] = [single]

    def test_rekey_into_unmanaged_product_is_blocked(self):
        self.st["products"]["2226"]["items"][0]["status"] = "reused"
        self.regroup_into_2226()
        d = diff.diff(self.st, self.products)
        self.assertEqual(d["unmanaged"], ["2226"])
        self.assertEqual(d["rekeyed"], [{"removed": "2230", "new": [], "skus": ["1005", "1006"], "changed": ["2226"]}])
        self.assertEqual(d["removal"], {"2230": "removed_action"})
        self.assertEqual(d["blocked_rekeys"],
                         {"2230": "rekey into a product this migration cannot update: 2226 (unmanaged)"})
        self.assertIn("blocked rekey 2230: rekey into a product this migration cannot update: 2226 (unmanaged)",
                      diff.report(d))

    def test_rekey_into_product_with_payload_error_is_blocked(self):
        self.regroup_into_2226()
        self.products["products"][0]["variants"][1]["option_values"] = [None]
        d = diff.diff(self.st, self.products)
        self.assertEqual(list(d["errors"]), ["2226"])
        self.assertEqual(d["removal"], {"2230": "removed_action"})
        self.assertEqual(d["blocked_rekeys"],
                         {"2230": "rekey into a product this migration cannot update: 2226 (payload error)"})

    def test_rekey_into_product_without_written_snapshot_is_blocked(self):
        del self.st["products"]["2226"]["written"]
        self.regroup_into_2226()
        d = diff.diff(self.st, self.products)
        self.assertEqual(d["removal"], {"2230": "removed_action"})
        self.assertEqual(d["blocked_rekeys"],
                         {"2230": "rekey into a product this migration cannot update: 2226 (no written snapshot)"})

    def test_rekey_into_new_products_is_not_blocked(self):
        duo = self.products["products"][1]
        self.products["products"][1] = dict(duo, handle="1005", key="1005")
        d = diff.diff(self.st, self.products)
        self.assertEqual((d["removal"], d["blocked_rekeys"]), ({"2230": "delete_rekeyed"}, {}))

    def test_sku_moving_between_present_products(self):
        single, duo = self.products["products"]
        single["variants"].append(duo["variants"].pop())
        d = diff.diff(self.st, self.products)
        self.assertEqual((d["removed"], d["rekeyed"]), ([], []))
        self.assertEqual(d["moved"], [{"sku": "1006", "from": "2230", "to": ["2226"]}])

    def test_sku_moving_to_a_new_product(self):
        duo = self.products["products"][1]
        moved = duo["variants"].pop()
        self.products["products"].append(dict(duo, handle="1006", key="1006", variants=[moved], options=[]))
        d = diff.diff(self.st, self.products)
        self.assertEqual((d["new"], d["moved"]), (["1006"], [{"sku": "1006", "from": "2230", "to": ["1006"]}]))

    def test_archived_handle_regrouped_is_rekeyed(self):
        gone = {"products": self.products["products"][:1]}
        state.sync_products(self.st, gone)
        state.merge(self.st, [dict(created_line("2230"), status="archived", items=[])], gone)
        duo = self.products["products"][1]
        regrouped = {"products": gone["products"] + [dict(duo, handle="1005", key="1005", variants=duo["variants"][:1],
                                                          options=[])]}
        d = diff.diff(self.st, regrouped)
        self.assertEqual((d["removed"], d["already_archived"]), ([], ["2230"]))
        self.assertEqual(d["rekeyed"], [{"removed": "2230", "new": ["1005"], "skus": ["1005"]}])
        self.assertEqual(d["removal"], {"2230": "delete_rekeyed"})

    def test_payload_error_is_reported(self):
        self.products["products"][1]["variants"][0]["option_values"] = [None]
        d = diff.diff(self.st, self.products)
        self.assertEqual(list(d["errors"]), ["2230"])
        self.assertIn("missing_option_value", d["errors"]["2230"])
        self.assertEqual((list(d["changed"]), d["unchanged"]), ([], 1))

    def test_no_written_snapshot_is_not_compared(self):
        del self.st["products"]["2226"]["written"]
        self.products["products"][0]["title"] = "X"
        d = diff.diff(self.st, self.products)
        self.assertEqual((d["no_written"], list(d["changed"]), d["unchanged"]), (["2226"], [], 1))

    def test_tag_order_is_not_a_change(self):
        self.products["products"][0]["tags"] = ["petit-electromenager", " cuisine"]
        self.assertEqual(diff.diff(self.st, self.products)["unchanged"], 2)

    def test_new_handle_and_paths(self):
        extra = dict(self.products["products"][0], handle="2240", key="2240",
                     variants=[{"sku": "1010", "barcode": "3601234560104", "option_values": []}])
        self.products["products"].append(extra)
        self.products["products"][0]["title"] = "Grille-pain Toastine"
        d = diff.diff(self.st, self.products)
        self.assertEqual((d["new"], d["phase"], d["conflicts"], d["missing_snapshots"]), (["2240"], 1, {}, []))
        self.assertEqual(diff.change_paths(d["changed"]["2226"]), ["product.title"])

    def test_new_shared_element_is_a_change(self):
        decision = {"products": {"2226": "Cuisine"}, "attributes": []}
        self.st["decisions"]["taxonomy"] = decision
        d = diff.diff(self.st, self.products)["changed"]["2226"]
        self.assertEqual(d["product"]["category"], [None, "<category:Cuisine IRI>"])


class CompareTest(unittest.TestCase):
    def setUp(self):
        self.products = make_products()
        self.st = make_state(self.products)

    def build(self, handle="2230"):
        return payload.build(self.products, self.st, handle, placeholders=True)

    def test_equal(self):
        self.assertEqual(diff.compare(self.build(), self.build()), {})

    def test_options_and_variants(self):
        old = self.build()
        duo = self.products["products"][1]
        duo["options"] = [{"name": "Couleur", "values": ["Noir", "Bleu"]}, {"name": "Taille", "values": ["S"]}]
        duo["variants"] = [dict(duo["variants"][0], option_values=["Noir", "S"], barcode="3601234560999"),
                           {"sku": "1007", "barcode": "3601234560074", "option_values": ["Bleu", "S"]}]
        change = diff.compare(old, self.build())
        self.assertEqual(change["options"], {"added": ["Taille"], "values_added": {"Couleur": ["Bleu"]},
                                             "values_removed": {"Couleur": ["Rouge"]}})
        self.assertEqual(change["variants"], {
            "added": ["1007"], "removed": ["1006"],
            "changed": {"1005": {"barcode": ["3601234560050", "3601234560999"],
                                 "option_values": [["Noir"], ["Noir", "S"]]}}})

    def test_metafields_and_prices(self):
        self.st["decisions"]["metafields"] = {"definitions": {"custom.material": {"type": "single_line_text"},
                                                              "custom.color": {"type": "single_line_text"}},
                                              "values": {"2226": {"custom.material": "Inox", "custom.color": "Gris"}}}
        self.st["decisions"]["prices"] = {"countries": [{"code": "FR", "country": "/api/countries/FR",
                                                         "tax": "/api/taxes/1", "currency": "EUR"}],
                                          "offers": {"2226": {"1001": {"FR": {"taxExcludedPrice": "20.00"}}}}}
        old = self.build("2226")
        self.st["decisions"]["metafields"]["values"]["2226"] = {"custom.material": "Acier", "custom.weight": "2"}
        self.st["decisions"]["metafields"]["definitions"]["custom.weight"] = {"type": "number_integer"}
        self.st["decisions"]["prices"]["offers"]["2226"]["1001"]["FR"]["taxExcludedPrice"] = "25.00"
        change = diff.compare(old, self.build("2226"))
        self.assertEqual(change["metafields"], {"added": ["custom.weight"], "removed": ["custom.color"],
                                                "changed": {"custom.material": ["Inox", "Acier"]}})
        self.assertEqual(list(change["prices"]), ["changed"])
        before, after = change["prices"]["changed"]["1001:FR"]
        self.assertEqual((before["taxExcludedPrice"]["amount"], after["taxExcludedPrice"]["amount"]),
                         ("20.00", "25.00"))

    def test_attributes_and_collections_compare_sorted(self):
        old = self.build("2226")
        new = self.build("2226")
        old["product"]["body"]["collections"] = ["/api/collections/b", "/api/collections/a"]
        new["product"]["body"]["collections"] = ["/api/collections/a", "/api/collections/b"]
        old["product"]["body"]["attributes"] = [{"definition": "d2", "value": "x"}, {"definition": "d1", "option": "o"}]
        new["product"]["body"]["attributes"] = list(reversed(old["product"]["body"]["attributes"]))
        self.assertEqual(diff.compare(old, new), {})

    def test_description_whitespace_is_not_a_change(self):
        old = self.build("2226")
        new = self.build("2226")
        new["product"]["body"]["description"] = "<p>Inox</p>\r\n"
        self.assertEqual(diff.compare(old, new), {})


class DiffCliTest(unittest.TestCase):
    def test_writes_diff_json(self):
        products = make_products()
        st = make_state(products)
        state.merge(st, [created_line("2226"), created_line("2230")], products)
        products["products"][0]["title"] = "Grille-pain Toastine"
        with tempfile.TemporaryDirectory() as directory:
            write_workspace(directory, products, st)
            result = run_cli(directory, script="diff.py")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("2226: product.title", result.stdout)
            written = read_json(os.path.join(directory, "diff.json"))
        self.assertEqual((list(written["changed"]), written["unchanged"]), (["2226"], 1))

    def test_payload_error_exits_1_and_still_writes(self):
        products = make_products()
        st = make_state(products)
        state.merge(st, [created_line("2226"), created_line("2230")], products)
        products["products"][1]["variants"][0]["option_values"] = [None]
        with tempfile.TemporaryDirectory() as directory:
            write_workspace(directory, products, st)
            result = run_cli(directory, script="diff.py")
            self.assertEqual(result.returncode, 1)
            self.assertIn("error: 2230:", result.stdout)
            self.assertEqual(list(read_json(os.path.join(directory, "diff.json"))["errors"]), ["2230"])

    def test_usage_error(self):
        result = run_cli(script="diff.py")
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
