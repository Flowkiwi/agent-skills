import json
import os
import sys
import tempfile
import unittest
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import diff  # noqa: E402
import payload  # noqa: E402
import state  # noqa: E402
from phase_helpers import (created_line, make_products, make_state, product_iri, run_cli,  # noqa: E402
                           variant_iri, write_workspace)


def child_iri(handle, kind, ref):
    return "%s/%ss/%s" % (product_iri(handle), kind, uuid.uuid5(uuid.NAMESPACE_URL, "%s:%s:%s" % (handle, kind, ref)))


def item(handle, kind, ref, module="core", status="created"):
    return {"kind": kind, "iri": child_iri(handle, kind, ref), "status": status, "module": module, "ref": ref}


def product(products, handle):
    return next(p for p in products["products"] if p["handle"] == handle)


class UpdatePlanTest(unittest.TestCase):
    def setUp(self):
        self.products = make_products()             # 2226 (SKU 1001), 2230 (SKUs 1005, 1006)
        self.st = make_state(self.products)
        state.merge(self.st, [created_line("2226"), created_line("2230")], self.products)
        state.start_phase(self.st)
        p = product(self.products, "2226")
        p["title"] = "Grille-pain Toastine"
        p["variants"].append(dict(p["variants"][0], sku="1001-B", barcode="3601234560999"))
        self.refresh()

    def refresh(self):
        self.diff = diff.diff(self.st, self.products)

    def drop_variant(self, handle, sku):
        p = product(self.products, handle)
        p["variants"] = [v for v in p["variants"] if v["sku"] != sku]
        self.refresh()

    def variant_iri(self, handle, sku):
        return variant_iri(handle, sku)

    def test_title_and_new_variant(self):
        plan = payload.update_plan(self.products, self.st, self.diff, "2226")
        self.assertEqual(plan["action"], "update")
        self.assertEqual(plan["product"]["body"], {"title": "Grille-pain Toastine"})
        self.assertEqual([v["sku"] for v in plan["variants"]["create"]], ["1001-B"])
        self.assertNotIn("metafields", plan)

    def test_removed_variant_uses_item_ref(self):
        self.drop_variant("2230", "1006")
        plan = payload.update_plan(self.products, self.st, self.diff, "2230")
        self.assertEqual(plan["variants"]["delete"], [{"iri": self.variant_iri("2230", "1006"), "sku": "1006"}])

    def test_conflict_needs_force(self):
        self.diff["conflicts"] = {"2226": {"title": {"written": "a", "flowkiwi": "b"}}}
        with self.assertRaisesRegex(payload.PayloadError, "modified in Flowkiwi"):
            payload.update_plan(self.products, self.st, self.diff, "2226")
        state.decide(self.st, "force_update", {"phase": 2, "handles": ["2226"]}, "developer")
        self.assertEqual(payload.update_plan(self.products, self.st, self.diff, "2226")["action"], "update")

    def test_removed_action(self):
        self.diff["removed"] = ["2230"]
        with self.assertRaisesRegex(payload.PayloadError, "removed_action for phase 2"):
            payload.update_plan(self.products, self.st, self.diff, "2230")
        state.decide(self.st, "removed_action", {"phase": 1, "action": "archive"}, "developer")
        with self.assertRaises(payload.PayloadError):
            payload.update_plan(self.products, self.st, self.diff, "2230")
        state.decide(self.st, "removed_action", {"phase": 2, "action": "archive"}, "developer")
        self.assertEqual(payload.update_plan(self.products, self.st, self.diff, "2230")["product"]["body"],
                         {"status": "/api/statuses/ARCHIVED"})
        state.decide(self.st, "removed_action", {"phase": 2, "action": "delete"}, "developer")
        steps = payload.update_plan(self.products, self.st, self.diff, "2230")["steps"]
        self.assertEqual([s["kind"] for s in steps][-1], "product")


class UpdatePlanDetailsTest(unittest.TestCase):
    def setUp(self):
        self.products = make_products()
        self.st = make_state(self.products)

    def create(self, handle, *extra):
        line = created_line(handle, self.products)
        line["items"] += list(extra)
        state.merge(self.st, [line], self.products)

    def plan(self, handle):
        return payload.update_plan(self.products, self.st, diff.diff(self.st, self.products), handle)

    def test_not_in_diff_and_unmanaged(self):
        self.create("2226")
        state.start_phase(self.st)
        with self.assertRaisesRegex(payload.PayloadError, r"^'2226' is not in diff.json changed or removed$"):
            self.plan("2226")
        self.st["products"]["2226"]["items"][0]["status"] = "reused"
        product(self.products, "2226")["title"] = "X"
        with self.assertRaisesRegex(payload.PayloadError, r"^'2226' is unmanaged \(reused\): never updated$"):
            self.plan("2226")

    def test_stale_diff(self):
        self.create("2226")
        d = diff.diff(self.st, self.products)
        state.start_phase(self.st)
        with self.assertRaisesRegex(payload.PayloadError, "diff.json is from phase 1, run diff.py again"):
            payload.update_plan(self.products, self.st, d, "2226")

    def test_options_values_and_variant_changes(self):
        self.create("2230", item("2230", "product_option", "Couleur"), item("2230", "option_value", "Couleur:Noir"),
                    item("2230", "option_value", "Couleur:Rouge"))
        state.start_phase(self.st)
        duo = product(self.products, "2230")
        duo["options"] = [{"name": "Couleur", "values": ["Noir", "Bleu"]}, {"name": "Taille", "values": ["S"]}]
        duo["variants"] = [dict(duo["variants"][0], barcode="3601234560999", option_values=["Noir", "S"]),
                           {"sku": "1007", "barcode": "3601234560074", "option_values": ["Bleu", "S"]}]
        plan = self.plan("2230")
        self.assertIsNone(plan["product"])
        self.assertEqual(plan["options"]["create"],
                         [{"title": "Taille", "position": 1, "values": [{"value": "S", "position": 0}]}])
        self.assertEqual(plan["options"]["values_create"],
                         [{"option": "Couleur", "option_iri": child_iri("2230", "product_option", "Couleur"),
                           "value": "Bleu", "position": 1}])
        self.assertEqual(plan["options"]["values_delete"],
                         [{"iri": child_iri("2230", "option_value", "Couleur:Rouge"), "option": "Couleur",
                           "value": "Rouge"}])
        self.assertEqual(plan["variants"]["delete"], [{"iri": variant_iri("2230", "1006"), "sku": "1006"}])
        self.assertEqual(plan["variants"]["update"],
                         [{"iri": variant_iri("2230", "1005"), "sku": "1005", "body": {"barcode": "3601234560999"},
                           "option_values": ["Noir", "S"]}])
        self.assertEqual([v["sku"] for v in plan["variants"]["create"]], ["1007"])

    def test_removed_option_deletes_it_and_its_values(self):
        self.create("2230", item("2230", "product_option", "Couleur"), item("2230", "option_value", "Couleur:Noir"),
                    item("2230", "option_value", "Couleur:Rouge"))
        state.start_phase(self.st)
        duo = product(self.products, "2230")
        duo["options"] = []
        duo["variants"] = [dict(duo["variants"][0], option_values=[])]
        plan = self.plan("2230")
        self.assertEqual(plan["options"]["delete"],
                         [{"iri": child_iri("2230", "product_option", "Couleur"), "title": "Couleur"}])
        self.assertEqual([v["value"] for v in plan["options"]["values_delete"]], ["Noir", "Rouge"])
        self.assertEqual(plan["variants"]["update"][0]["option_values"], [])

    def test_value_still_used_is_kept(self):
        self.create("2230", item("2230", "product_option", "Couleur"), item("2230", "option_value", "Couleur:Rouge"))
        state.start_phase(self.st)
        duo = product(self.products, "2230")
        duo["options"] = [{"name": "Couleur", "values": ["Noir"]}]
        plan = self.plan("2230")
        self.assertNotIn("values_delete", plan.get("options", {}))

    def test_missing_item_ref(self):
        self.create("2230")
        state.start_phase(self.st)
        product(self.products, "2230")["options"][0]["values"].append("Bleu")
        with self.assertRaisesRegex(payload.PayloadError,
                                    r"^no product_option item with ref 'Couleur' for '2230': look it up with list_\* "
                                    r"and record it$"):
            self.plan("2230")

    def test_cleared_fields(self):
        self.create("2226")
        state.start_phase(self.st)
        p = product(self.products, "2226")
        p["description_html"], p["tags"] = "", []
        self.assertEqual(self.plan("2226")["product"]["body"], {"description": None, "tags": None})

    def metafields_decision(self, value):
        state.record_shared(self.st, "metafield_definition:custom.puissance",
                            "/api/metafield_definitions/00000000-0000-4000-8000-000000000002", "created", "metafields")
        state.record_shared(self.st, "metafield_definition:custom.garantie",
                            "/api/metafield_definitions/00000000-0000-4000-8000-000000000003", "created", "metafields")
        definitions = {"custom.puissance": {"type": "single_line_text_field", "transform": None},
                       "custom.garantie": {"type": "single_line_text_field", "transform": None}}
        state.decide(self.st, "metafields", {"definitions": definitions, "values": {"2226": value}}, "developer")

    def test_metafields(self):
        self.metafields_decision({"custom.puissance": "800 W"})
        self.create("2226", item("2226", "metafield", "custom.puissance", "metafields"))
        state.start_phase(self.st)
        self.metafields_decision({"custom.puissance": "900 W", "custom.garantie": "2 ans"})
        plan = self.plan("2226")
        self.assertEqual(plan["metafields"]["update"],
                         [{"iri": child_iri("2226", "metafield", "custom.puissance"), "key": "custom.puissance",
                           "body": {"value": "900 W"}}])
        self.assertEqual([e["key"] for e in plan["metafields"]["create"]], ["custom.garantie"])
        self.metafields_decision({})
        self.assertEqual(self.plan("2226")["metafields"],
                         {"delete": [{"iri": child_iri("2226", "metafield", "custom.puissance"),
                                      "key": "custom.puissance"}]})

    def prices_decision(self, offers):
        countries = [{"code": "FR", "country": "/api/countries/00000000-0000-4000-8000-000000000010",
                      "tax": "/api/taxes/00000000-0000-4000-8000-000000000011", "currency": "EUR"},
                     {"code": "BE", "country": "/api/countries/00000000-0000-4000-8000-000000000012",
                      "tax": "/api/taxes/00000000-0000-4000-8000-000000000013", "currency": "EUR"}]
        state.decide(self.st, "prices", {"offers": {"2226": offers}, "countries": countries}, "developer")

    def test_prices(self):
        self.prices_decision({"1001": {"FR": {"taxExcludedPrice": "20.00"}, "BE": {"taxExcludedPrice": "21.00"}}})
        self.create("2226", item("2226", "sale_offer", "1001:FR", "prices"),
                    item("2226", "sale_offer", "1001:BE", "prices"))
        state.start_phase(self.st)
        self.prices_decision({"1001": {"FR": {"taxExcludedPrice": "25.00"}}})
        plan = self.plan("2226")
        self.assertEqual(plan["prices"]["update"],
                         [{"iri": child_iri("2226", "sale_offer", "1001:FR"), "ref": "1001:FR",
                           "body": {"taxExcludedPrice": {"amount": "25.00", "currency": "EUR"}}}])
        self.assertEqual(plan["prices"]["delete"],
                         [{"iri": child_iri("2226", "sale_offer", "1001:BE"), "ref": "1001:BE"}])

    def test_reused_child_is_never_targeted(self):
        self.create("2230")
        self.st["products"]["2230"]["items"][2]["status"] = "reused"    # variant 1006
        state.start_phase(self.st)
        duo = product(self.products, "2230")
        duo["title"] = "Duo"
        duo["variants"][1]["barcode"] = "3601234560999"
        plan = self.plan("2230")
        self.assertEqual(plan["product"]["body"], {"title": "Duo"})
        self.assertNotIn("variants", plan)

    def test_moved_sku_is_deferred_until_its_deletion(self):
        self.create("2226")
        self.create("2230")
        state.start_phase(self.st)
        duo = product(self.products, "2230")
        moved = duo["variants"].pop()
        product(self.products, "2226")["title"] = "Grille-pain Toastine"
        product(self.products, "2226")["variants"].append(dict(moved, option_values=[]))
        d = diff.diff(self.st, self.products)
        plan = payload.update_plan(self.products, self.st, d, "2226")
        self.assertEqual(plan["deferred"], [{"sku": "1006", "held_by": "2230"}])
        self.assertEqual(plan["product"]["body"], {"title": "Grille-pain Toastine"})
        self.assertNotIn("variants", plan)
        delete = payload.update_plan(self.products, self.st, d, "2230")["variants"]["delete"]
        state.mark_deleted(self.st, [v["iri"] for v in delete])
        plan = payload.update_plan(self.products, self.st, d, "2226")
        self.assertEqual([v["sku"] for v in plan["variants"]["create"]], ["1006"])
        self.assertNotIn("deferred", plan)

    def test_swapped_skus(self):
        self.create("2226")
        self.create("2230")
        state.start_phase(self.st)
        toaster, duo = product(self.products, "2226"), product(self.products, "2230")
        one, six = toaster["variants"][0], duo["variants"][1]
        toaster["variants"] = [dict(six, option_values=[])]
        duo["variants"] = [duo["variants"][0], dict(one, option_values=["Rouge"])]
        d = diff.diff(self.st, self.products)
        self.assertEqual(sorted(m["sku"] for m in d["moved"]), ["1001", "1006"])
        first = payload.update_plan(self.products, self.st, d, "2226")
        second = payload.update_plan(self.products, self.st, d, "2230")
        self.assertEqual((first["deferred"], second["deferred"]),
                         ([{"sku": "1006", "held_by": "2230"}], [{"sku": "1001", "held_by": "2226"}]))
        self.assertEqual(first["variants"], {"delete": [{"iri": variant_iri("2226", "1001"), "sku": "1001"}]})
        self.assertEqual(second["variants"], {"delete": [{"iri": variant_iri("2230", "1006"), "sku": "1006"}]})
        state.mark_deleted(self.st, [variant_iri("2226", "1001"), variant_iri("2230", "1006")])
        first = payload.update_plan(self.products, self.st, d, "2226")
        second = payload.update_plan(self.products, self.st, d, "2230")
        self.assertEqual((first["variants"].keys(), second["variants"].keys()), ({"create"}, {"create"}))
        self.assertEqual(([v["sku"] for v in first["variants"]["create"]],
                          [v["sku"] for v in second["variants"]["create"]]), (["1006"], ["1001"]))
        self.assertNotIn("deferred", first)

    def updated_line(self, handle, items=(), deleted=()):
        return {"handle": handle, "status": "updated", "iri": product_iri(handle), "error": None,
                "items": list(items), "deleted": list(deleted)}

    def test_swap_deferred_creates_stay_pending_after_pass_one(self):
        self.create("2226")
        self.create("2230")
        state.start_phase(self.st)
        toaster, duo = product(self.products, "2226"), product(self.products, "2230")
        one, six = toaster["variants"][0], duo["variants"][1]
        toaster["variants"] = [dict(six, option_values=[])]
        duo["variants"] = [duo["variants"][0], dict(one, option_values=["Rouge"])]
        d = diff.diff(self.st, self.products)
        lines = []
        for handle in ("2226", "2230"):
            plan = payload.update_plan(self.products, self.st, d, handle)
            lines.append(self.updated_line(handle, deleted=[v["iri"] for v in plan["variants"]["delete"]]))
        state.merge(self.st, lines, self.products)
        d = diff.diff(self.st, self.products)
        self.assertEqual((d["changed"]["2226"]["variants"], d["changed"]["2230"]["variants"]),
                         ({"added": ["1006"]}, {"added": ["1001"]}))
        plan = payload.update_plan(self.products, self.st, d, "2226")
        self.assertEqual(([v["sku"] for v in plan["variants"]["create"]], "deferred" in plan), (["1006"], False))
        state.merge(self.st, [self.updated_line("2226", [item("2226", "variant", "1006")])], self.products)
        self.assertNotIn("2226", diff.diff(self.st, self.products)["changed"])

    def rekey_into_2226(self):
        self.create("2226")
        self.create("2230")
        state.start_phase(self.st)
        duo = product(self.products, "2230")
        product(self.products, "2226")["variants"] += [dict(v, option_values=[]) for v in duo["variants"]]
        self.products["products"] = [product(self.products, "2226")]
        d = diff.diff(self.st, self.products)
        self.assertEqual((d["removal"], d["rekeyed"][0]["changed"]), ({"2230": "delete_rekeyed"}, ["2226"]))
        return d

    def test_rekeyed_deletion_waits_for_a_conflicted_target(self):
        d = self.rekey_into_2226()
        d["conflicts"] = {"2226": {"title": {"written": "a", "flowkiwi": "b"}}}
        state.decide(self.st, "confirmed_rekey", {"phase": 2}, "developer")
        with self.assertRaisesRegex(payload.PayloadError, "'2230' cannot delete SKU 1005, 1006 yet: '2226' receives "
                                                          "it .* force_update or resolve the conflict first"):
            payload.update_plan(self.products, self.st, d, "2230")
        state.decide(self.st, "force_update", {"phase": 2, "handles": ["2226"]}, "developer")
        self.assertEqual(payload.update_plan(self.products, self.st, d, "2230")["action"], "delete")

    def test_moved_deletion_waits_for_a_conflicted_receiver(self):
        self.create("2226")
        self.create("2230")
        state.start_phase(self.st)
        moved = product(self.products, "2230")["variants"].pop()
        product(self.products, "2226")["variants"].append(dict(moved, option_values=[]))
        d = diff.diff(self.st, self.products)
        d["conflicts"] = {"2226": {"title": {"written": "a", "flowkiwi": "b"}}}
        with self.assertRaisesRegex(payload.PayloadError, "'2230' cannot delete SKU 1006 yet: '2226' receives it"):
            payload.update_plan(self.products, self.st, d, "2230")
        state.decide(self.st, "force_update", {"phase": 2, "handles": ["2226"]}, "developer")
        plan = payload.update_plan(self.products, self.st, d, "2230")
        self.assertEqual(plan["variants"]["delete"], [{"iri": variant_iri("2230", "1006"), "sku": "1006"}])

    def test_deferred_sku_keeps_its_prices_for_later(self):
        countries = [{"code": "FR", "country": "/api/countries/00000000-0000-4000-8000-000000000010",
                      "tax": "/api/taxes/00000000-0000-4000-8000-000000000011", "currency": "EUR"}]
        offers = {"2226": {"1001": {"FR": {"taxExcludedPrice": "20.00"}}},
                  "2230": {"1005": {"FR": {"taxExcludedPrice": "30.00"}},
                           "1006": {"FR": {"taxExcludedPrice": "31.00"}}}}
        state.decide(self.st, "prices", {"offers": offers, "countries": countries}, "developer")
        self.create("2226", item("2226", "sale_offer", "1001:FR", "prices"))
        self.create("2230", item("2230", "sale_offer", "1005:FR", "prices"),
                    item("2230", "sale_offer", "1006:FR", "prices"))
        state.start_phase(self.st)
        duo = product(self.products, "2230")
        product(self.products, "2226")["variants"].append(dict(duo["variants"].pop(), option_values=[]))
        offers = {"2226": {"1001": {"FR": {"taxExcludedPrice": "20.00"}},
                           "1006": {"FR": {"taxExcludedPrice": "31.00"}}},
                  "2230": {"1005": {"FR": {"taxExcludedPrice": "30.00"}}}}
        state.decide(self.st, "prices", {"offers": offers, "countries": countries}, "developer")
        plan = self.plan("2226")
        self.assertEqual(plan["deferred"], [{"sku": "1006", "held_by": "2230"}])
        self.assertNotIn("prices", plan)

    def test_stale_diff_in_the_same_phase(self):
        self.create("2226")
        state.start_phase(self.st)
        toaster = product(self.products, "2226")
        toaster["variants"].append(dict(toaster["variants"][0], sku="1009", barcode="3601234560098"))
        d = diff.diff(self.st, self.products)
        toaster["variants"].pop()
        with self.assertRaisesRegex(payload.PayloadError, "variant '1009' of '2226' .* run diff.py again"):
            payload.update_plan(self.products, self.st, d, "2226")

    def test_printed_again_after_a_partial_run(self):
        self.create("2230", item("2230", "product_option", "Couleur"), item("2230", "option_value", "Couleur:Rouge"))
        state.start_phase(self.st)
        duo = product(self.products, "2230")
        duo["options"] = [{"name": "Couleur", "values": ["Noir", "Bleu"]}]
        duo["variants"] = [duo["variants"][0], {"sku": "1007", "barcode": "3601234560074", "option_values": ["Bleu"]}]
        d = diff.diff(self.st, self.products)
        state.mark_deleted(self.st, [variant_iri("2230", "1006"), child_iri("2230", "option_value", "Couleur:Rouge")])
        line = {"handle": "2230", "status": "updated", "iri": product_iri("2230"), "error": None,
                "items": [item("2230", "option_value", "Couleur:Bleu"), item("2230", "variant", "1007")]}
        state.merge(self.st, [line])
        self.assertEqual({k: v for k, v in payload.update_plan(self.products, self.st, d, "2230").items()
                          if k not in ("handle", "action", "iri")}, {"product": None})

    def test_rekeyed_removal_needs_confirmed_rekey(self):
        self.create("2226")
        self.create("2230")
        state.start_phase(self.st)
        duo = product(self.products, "2230")
        self.products["products"] = [product(self.products, "2226")] + [
            dict(duo, key=v["sku"], handle=v["sku"], variants=[dict(v, option_values=[])], options=[])
            for v in duo["variants"]]
        state.sync_products(self.st, self.products)
        state.decide(self.st, "removed_action", {"phase": 2, "action": "archive"}, "developer")
        with self.assertRaisesRegex(payload.PayloadError, "confirmed_rekey for phase 2"):
            self.plan("2230")
        state.decide(self.st, "confirmed_rekey", {"phase": 2}, "developer")
        plan = self.plan("2230")
        self.assertEqual((plan["action"], plan["steps"][-1]["kind"]), ("delete", "product"))

    def test_blocked_rekey_is_archived_never_deleted(self):
        self.create("2226")
        self.create("2230")
        state.start_phase(self.st)
        self.st["products"]["2226"]["items"][0]["status"] = "reused"
        duo = product(self.products, "2230")
        product(self.products, "2226")["variants"] += [dict(v, option_values=[]) for v in duo["variants"]]
        self.products["products"] = [product(self.products, "2226")]
        d = diff.diff(self.st, self.products)
        self.assertEqual(d["removal"], {"2230": "archive_blocked"})
        archive = {"handle": "2230", "action": "archive", "iri": product_iri("2230"),
                   "product": {"body": {"status": "/api/statuses/ARCHIVED"}}}
        self.assertEqual(payload.update_plan(self.products, self.st, d, "2230"), archive)
        state.decide(self.st, "removed_action", {"phase": 2, "action": "delete"}, "developer")
        state.decide(self.st, "confirmed_rekey", {"phase": 2}, "developer")
        self.assertEqual(payload.update_plan(self.products, self.st, d, "2230"), archive)

    def test_already_archived_selected_only_for_delete(self):
        self.create("2230")
        gone = {"products": [product(self.products, "2226")], "warnings": []}
        state.sync_products(self.st, gone)
        state.merge(self.st, [dict(created_line("2230"), status="archived", items=[])], gone)
        state.start_phase(self.st)
        self.products = gone
        state.decide(self.st, "removed_action", {"phase": 2, "action": "archive"}, "developer")
        with self.assertRaisesRegex(payload.PayloadError, "'2230' is already archived"):
            self.plan("2230")
        state.decide(self.st, "removed_action", {"phase": 2, "action": "delete"}, "developer")
        self.assertEqual(self.plan("2230")["action"], "delete")


class UpdateCliTest(unittest.TestCase):
    def test_prints_the_plan(self):
        products = make_products()
        st = make_state(products)
        state.merge(st, [created_line("2226"), created_line("2230")], products)
        state.start_phase(st)
        product(products, "2230")["title"] = "Grille-pain Duo Inox"
        with tempfile.TemporaryDirectory() as directory:
            write_workspace(directory, products, st)
            result = run_cli(directory, script="diff.py")
            self.assertEqual(result.returncode, 0, result.stderr)
            result = run_cli(directory, "2230", "--update", script="payload.py")
            self.assertEqual(result.returncode, 0, result.stderr)
            plan = json.loads(result.stdout)
            self.assertEqual((plan["action"], plan["iri"]), ("update", product_iri("2230")))
            self.assertEqual(plan["product"]["body"], {"title": "Grille-pain Duo Inox"})
            result = run_cli(directory, "2226", "--update", script="payload.py")
            self.assertEqual(result.returncode, 1)
            self.assertIn("'2226' is not in diff.json changed or removed", result.stderr)
            os.remove(os.path.join(directory, "diff.json"))
            result = run_cli(directory, "2230", "--update", script="payload.py")
            self.assertEqual(result.returncode, 1)
            self.assertIn("diff.json", result.stderr)


if __name__ == "__main__":
    unittest.main()
