import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import diff  # noqa: E402
import payload  # noqa: E402
import state  # noqa: E402
from phase_helpers import (checked, created_line, make_products, make_state, product_iri, read_json,  # noqa: E402
                           run_cli, variant_iri, write_json, write_workspace)


def product(products, handle):
    return next(p for p in products["products"] if p["handle"] == handle)


def updated_line(handle, items=(), deleted=()):
    return {"handle": handle, "status": "updated", "iri": product_iri(handle), "error": None,
            "items": list(items), "deleted": list(deleted)}


class UpdateWorklistsTest(unittest.TestCase):
    def created_state(self, handles, phase=2):
        st = state.init("demo", "https://mcp.example.com", {"products": [{"handle": h} for h in handles]})
        for handle in handles:
            st["products"][handle].update(status="created", iri=product_iri(handle), items=[
                {"kind": "product", "iri": product_iri(handle), "status": "created", "module": "core"}])
        while st["phase"] < phase:
            state.start_phase(st)
        return st

    def test_selection(self):
        d = checked({"phase": 2, "changed": {"2226": {}, "2227": {}}, "removed": ["2230"], "unmanaged": [],
             "conflicts": {"2227": {"title": {}}}, "new": [], "rekeyed": []})
        st = self.created_state(["2226", "2227", "2230"], phase=2)
        with self.assertRaisesRegex(ValueError, "removed_action"):
            state.worklists(st, 1, None, False, update=True, diff_data=d)
        state.decide(st, "removed_action", {"phase": 2, "action": "archive"}, "developer")
        self.assertEqual(state.worklists(st, 1, None, False, update=True, diff_data=d), [["2226", "2230"]])
        state.decide(st, "force_update", {"phase": 2, "handles": ["2227"]}, "developer")
        self.assertEqual(state.worklists(st, 1, None, False, update=True, diff_data=d), [["2226", "2227", "2230"]])

    def test_needs_the_conflicts_step(self):
        st = self.created_state(["2226", "2227"])
        d = {"phase": 2, "changed": {"2226": {}, "2227": {}}, "removed": [], "conflicts": {},
             "conflicts_checked": False}
        with self.assertRaisesRegex(ValueError, "no conflicts computed since diff.py ran"):
            state.worklists(st, 1, None, False, update=True, diff_data=d)
        d.update(conflicts_checked=True, missing_snapshots=["2227"])
        report = {}
        self.assertEqual(state.worklists(st, 1, None, False, update=True, diff_data=d, report=report), [["2226"]])
        self.assertIn("no Flowkiwi snapshot read in this phase", report["skipped"]["2227"])
        empty = {"phase": 2, "changed": {}, "removed": [], "conflicts_checked": False}
        self.assertEqual(state.worklists(st, 1, None, False, update=True, diff_data=empty), [])

    def test_stale_diff(self):
        st = self.created_state(["2226"], phase=3)
        with self.assertRaisesRegex(ValueError, "phase 2"):
            state.worklists(st, 1, None, False, update=True, diff_data={"phase": 2, "changed": {}, "removed": []})

    def test_needs_diff(self):
        st = self.created_state(["2226"])
        with self.assertRaisesRegex(ValueError, "run diff.py"):
            state.worklists(st, 1, None, False, update=True)

    def test_skipped_are_reported(self):
        d = checked({"phase": 2, "changed": {"2226": {}, "2227": {}}, "removed": ["2230"], "unmanaged": ["2230"],
             "conflicts": {"2227": {"title": {}}}})
        st = self.created_state(["2226", "2227", "2230"])
        report = {}
        self.assertEqual(state.worklists(st, 4, None, False, update=True, diff_data=d, report=report), [["2226"]])
        self.assertEqual(report["pass"], "updates")
        self.assertEqual(sorted(report["skipped"]), ["2227", "2230"])
        self.assertIn("force_update", report["skipped"]["2227"])
        self.assertIn("unmanaged", report["skipped"]["2230"])

    def test_removed_with_conflicts_needs_force(self):
        d = checked({"phase": 2, "changed": {}, "removed": ["2230"], "conflicts": {"2230": {"title": {}}}})
        st = self.created_state(["2230"])
        state.decide(st, "removed_action", {"phase": 2, "action": "delete"}, "developer")
        self.assertEqual(state.worklists(st, 1, None, False, update=True, diff_data=d), [])
        state.decide(st, "force_update", {"phase": 2, "handles": "all"}, "developer")
        self.assertEqual(state.worklists(st, 1, None, False, update=True, diff_data=d), [["2230"]])

    def test_removed_action_of_an_earlier_phase_is_refused(self):
        d = checked({"phase": 2, "changed": {}, "removed": ["2230"]})
        st = self.created_state(["2230"], phase=1)
        state.decide(st, "removed_action", {"phase": 1, "action": "archive"}, "developer")
        state.start_phase(st)
        with self.assertRaisesRegex(ValueError, "removed_action for phase 2"):
            state.worklists(st, 1, None, False, update=True, diff_data=d)

    def test_already_archived_only_for_delete_and_blocked_rekey_archived(self):
        d = checked({"phase": 2, "changed": {}, "removed": ["2231"], "already_archived": ["2230"],
             "removal": {"2230": "removed_action", "2231": "archive_blocked"}})
        st = self.created_state(["2230", "2231"])
        st["products"]["2230"]["status"] = "archived"
        self.assertEqual(state.worklists(st, 1, None, False, update=True, diff_data=d), [["2231"]])
        state.decide(st, "removed_action", {"phase": 2, "action": "archive"}, "developer")
        self.assertEqual(state.worklists(st, 1, None, False, update=True, diff_data=d), [["2231"]])
        state.decide(st, "removed_action", {"phase": 2, "action": "delete"}, "developer")
        self.assertEqual(state.worklists(st, 1, None, False, update=True, diff_data=d), [["2230", "2231"]])
        st["products"]["2231"]["status"] = "archived"
        st["products"]["2230"]["status"] = "deleted"
        self.assertEqual(state.worklists(st, 1, None, False, update=True, diff_data=d), [])

    def test_deletions_go_to_one_worker(self):
        d = checked({"phase": 2, "changed": {"2226": {}, "2227": {}}, "removed": ["2230", "2231"]})
        st = self.created_state(["2226", "2227", "2230", "2231"])
        state.decide(st, "removed_action", {"phase": 2, "action": "delete"}, "developer")
        self.assertEqual(state.worklists(st, 3, None, False, update=True, diff_data=d),
                         [["2230", "2231"], ["2226"], ["2227"]])

    def test_failed_update_needs_retry_failed(self):
        d = checked({"phase": 2, "changed": {"2226": {}}, "removed": []})
        st = self.created_state(["2226"])
        st["products"]["2226"]["error"] = "update_product: 422"
        report = {}
        self.assertEqual(state.worklists(st, 1, None, False, update=True, diff_data=d, report=report), [])
        self.assertIn("--retry-failed", report["skipped"]["2226"])
        self.assertEqual(state.worklists(st, 1, None, False, True, update=True, diff_data=d), [["2226"]])

    def test_update_refuses_pilot_and_module(self):
        st = self.created_state(["2226"])
        d = checked({"phase": 2, "changed": {"2226": {}}, "removed": []})
        with self.assertRaisesRegex(ValueError, "--update"):
            state.worklists(st, 1, None, True, update=True, diff_data=d)
        with self.assertRaisesRegex(ValueError, "--update"):
            state.worklists(st, 1, None, False, module="prices", update=True, diff_data=d)


class UpdatePassesTest(unittest.TestCase):
    """Real diffs of the demo products: deletions first, then updates, deferred creations, then creations."""

    def setUp(self):
        self.products = make_products()             # 2226 (SKU 1001), 2230 (SKUs 1005, 1006)
        self.st = make_state(self.products)
        state.merge(self.st, [created_line("2226"), created_line("2230")], self.products)
        state.start_phase(self.st)

    def lists(self, report=None, **kwargs):
        return state.worklists(self.st, 4, None, False, update=True, diff_data=self.diff, report=report, **kwargs)

    def test_rekey_deletion_first(self):
        duo = product(self.products, "2230")
        toaster = product(self.products, "2226")
        toaster["title"] = "Grille-pain Toastine"
        toaster["variants"] += [dict(v, option_values=[]) for v in duo["variants"]]
        self.products["products"] = [toaster]
        state.sync_products(self.st, self.products)
        self.diff = checked(diff.diff(self.st, self.products))
        self.assertEqual(self.diff["removal"], {"2230": "delete_rekeyed"})
        with self.assertRaisesRegex(ValueError, "confirmed_rekey for phase 2"):
            self.lists()
        state.decide(self.st, "confirmed_rekey", {"phase": 2}, "developer")
        report = {}
        self.assertEqual(self.lists(report), [["2230"]])
        self.assertEqual(report["pass"], "deletions")
        steps = payload.update_plan(self.products, self.st, self.diff, "2230")["steps"]
        state.mark_deleted(self.st, [s["iri"] for s in steps])
        report = {}
        self.assertEqual(self.lists(report), [["2226"]])
        self.assertEqual(report["pass"], "updates")
        plan = payload.update_plan(self.products, self.st, self.diff, "2226")
        self.assertEqual([v["sku"] for v in plan["variants"]["create"]], ["1005", "1006"])
        state.merge(self.st, [updated_line("2226", [
            {"kind": "variant", "iri": variant_iri("2226", sku), "status": "created", "module": "core", "ref": sku}
            for sku in ("1005", "1006")])], self.products)
        self.assertEqual(self.lists(), [])

    def test_rekey_deletion_waits_for_a_conflicted_receiver(self):
        duo = product(self.products, "2230")
        toaster = product(self.products, "2226")
        toaster["variants"] += [dict(v, option_values=[]) for v in duo["variants"]]
        self.products["products"] = [toaster]
        self.diff = checked(diff.diff(self.st, self.products))
        self.diff["conflicts"] = {"2226": {"title": {"written": "a", "flowkiwi": "b"}}}
        state.decide(self.st, "confirmed_rekey", {"phase": 2}, "developer")
        report = {}
        self.assertEqual(self.lists(report), [])
        self.assertIn("2226", report["skipped"]["2230"])
        self.assertIn("force_update", report["skipped"]["2226"])

    def test_moved_sku_deletion_first_then_deferred_creation(self):
        moved = product(self.products, "2230")["variants"].pop()
        toaster = product(self.products, "2226")
        toaster["title"] = "Grille-pain Toastine"
        toaster["variants"].append(dict(moved, option_values=[]))
        self.diff = checked(diff.diff(self.st, self.products))
        report = {}
        self.assertEqual(self.lists(report), [["2230"]])
        self.assertEqual(report["pass"], "deletions")
        delete = payload.update_plan(self.products, self.st, self.diff, "2230")["variants"]["delete"]
        state.merge(self.st, [updated_line("2230", deleted=[v["iri"] for v in delete])], self.products)
        self.assertEqual(self.lists(report), [["2226"]])
        self.assertEqual(report["pass"], "updates")

    def test_swapped_skus_second_pass_for_deferred_creations(self):
        toaster, duo = product(self.products, "2226"), product(self.products, "2230")
        one, six = toaster["variants"][0], duo["variants"][1]
        toaster["variants"] = [dict(six, option_values=[])]
        duo["variants"] = [duo["variants"][0], dict(one, option_values=["Rouge"])]
        self.diff = checked(diff.diff(self.st, self.products))
        report = {}
        self.assertEqual(sorted(h for l in self.lists(report) for h in l), ["2226", "2230"])
        self.assertEqual(report["pass"], "deletions")
        # 2226 is merged first: 2230 still holds 1006, so 2226 waits; 2230 is still to do
        plan = payload.update_plan(self.products, self.st, self.diff, "2226")
        self.assertEqual(plan["deferred"], [{"sku": "1006", "held_by": "2230"}])
        state.merge(self.st, [updated_line("2226", deleted=[v["iri"] for v in plan["variants"]["delete"]])],
                    self.products)
        report = {}
        self.assertEqual(self.lists(report), [["2230"]])
        self.assertEqual(report["pass"], "deletions")
        self.assertIn("1006", report["skipped"]["2226"])
        plan = payload.update_plan(self.products, self.st, self.diff, "2230")
        state.merge(self.st, [updated_line("2230", deleted=[v["iri"] for v in plan["variants"]["delete"]])],
                    self.products)
        lists = self.lists(report)
        self.assertEqual((sorted(h for l in lists for h in l), report["pass"]), (["2226", "2230"], "updates"))
        for handle, sku in (("2226", "1006"), ("2230", "1001")):
            plan = payload.update_plan(self.products, self.st, self.diff, handle)
            self.assertEqual([v["sku"] for v in plan["variants"]["create"]], [sku])
            state.merge(self.st, [updated_line(handle, [{"kind": "variant", "iri": variant_iri(handle, sku),
                                                         "status": "created", "module": "core", "ref": sku}])],
                        self.products)
        self.assertEqual(self.lists(), [])

    def test_done_updates_are_not_selected_again_with_the_same_diff(self):
        product(self.products, "2226")["title"] = "Grille-pain Toastine"
        self.diff = checked(diff.diff(self.st, self.products))
        self.assertEqual(self.lists(), [["2226"]])
        state.merge(self.st, [updated_line("2226")], self.products)
        self.assertEqual(self.lists(), [])
        product(self.products, "2226")["title"] = "Grille-pain Toastine Inox"
        self.diff = checked(diff.diff(self.st, self.products))
        self.assertEqual(self.lists(), [["2226"]])

    def test_placeholders_of_shared_elements_created_since(self):
        state.decide(self.st, "collections", {"membership": {"2226": ["Cuisine"]}}, "developer")
        self.diff = checked(diff.diff(self.st, self.products))
        self.assertEqual(self.diff["changed"]["2226"]["product"]["collections"], [[], ["<collection:Cuisine IRI>"]])
        state.record_shared(self.st, "collection:Cuisine", "/api/collections/00000000-0000-4000-8000-000000000020",
                            "created", "collections")
        self.assertEqual(self.lists(), [["2226"]])
        state.merge(self.st, [updated_line("2226")], self.products)
        self.assertEqual(self.lists(), [])

    def move_1006_to_2226(self):
        moved = product(self.products, "2230")["variants"].pop()
        product(self.products, "2226")["variants"].append(dict(moved, option_values=[]))

    def test_move_from_an_unmanaged_product_is_skipped_not_retried(self):
        self.st["products"]["2230"]["items"][0]["status"] = "reused"
        self.move_1006_to_2226()
        product(self.products, "2226")["title"] = "Grille-pain Toastine"
        self.diff = checked(diff.diff(self.st, self.products))
        self.assertEqual((self.diff["unmanaged"], self.diff["moved"]),
                         (["2230"], [{"sku": "1006", "from": "2230", "to": ["2226"]}]))
        for _ in range(2):
            report = {}
            self.assertEqual(self.lists(report), [])
            self.assertIn("SKU 1006 held by unmanaged 2230", report["skipped"]["2226"])

    def test_move_from_a_reused_variant_is_skipped(self):
        self.st["products"]["2230"]["items"][2]["status"] = "reused"    # variant 1006
        self.move_1006_to_2226()
        self.diff = checked(diff.diff(self.st, self.products))
        report = {}
        self.assertEqual(self.lists(report), [])
        self.assertIn("SKU 1006 held by 2230 as a reused variant", report["skipped"]["2226"])

    def test_already_archived_rekeyed_product_is_deleted_first(self):
        duo = product(self.products, "2230")
        gone = {"products": [product(self.products, "2226")], "warnings": []}
        state.sync_products(self.st, gone)
        state.merge(self.st, [dict(created_line("2230"), status="archived", items=[])], gone)
        state.start_phase(self.st)
        self.products["products"] = [product(self.products, "2226")] + [
            dict(duo, key=v["sku"], handle=v["sku"], variants=[dict(v, option_values=[])], options=[])
            for v in duo["variants"]]
        state.sync_products(self.st, self.products)
        self.diff = checked(diff.diff(self.st, self.products))
        self.assertEqual((self.diff["already_archived"], self.diff["removal"]), (["2230"], {"2230": "delete_rekeyed"}))
        with self.assertRaisesRegex(ValueError, "confirmed_rekey for phase 3"):
            self.lists()
        state.decide(self.st, "confirmed_rekey", {"phase": 3}, "developer")
        report = {}
        self.assertEqual((self.lists(report), report["pass"]), ([["2230"]], "deletions"))
        state.mark_deleted(self.st, [s["iri"] for s in state.undo_plan(self.st, "product:2230")])
        self.assertEqual(self.lists(), [])

    def test_creations_wait_for_the_sku_holder(self):
        duo = product(self.products, "2230")
        self.products["products"] = [product(self.products, "2226")] + [
            dict(duo, key=v["sku"], handle=v["sku"], variants=[dict(v, option_values=[])], options=[])
            for v in duo["variants"]]
        state.sync_products(self.st, self.products)
        report = {}
        lists = state.worklists(self.st, 4, None, False, products=self.products["products"], report=report)
        self.assertEqual(lists, [])
        self.assertEqual(sorted(report["skipped"]), ["1005", "1006"])
        self.assertIn("held by 2230", report["skipped"]["1005"])
        state.mark_deleted(self.st, [s["iri"] for s in state.undo_plan(self.st, "product:2230")])
        lists = state.worklists(self.st, 4, None, False, products=self.products["products"])
        self.assertEqual(sorted(h for l in lists for h in l), ["1005", "1006"])


class UpdateWorklistsCliTest(unittest.TestCase):
    def test_prints_pass_and_skipped(self):
        products = make_products()
        st = make_state(products)
        state.merge(st, [created_line("2226"), created_line("2230")], products)
        state.start_phase(st)
        product(products, "2226")["title"] = "Grille-pain Toastine"
        product(products, "2230")["title"] = "Grille-pain Duo Inox"
        with tempfile.TemporaryDirectory() as directory:
            write_workspace(directory, products, st)
            result = run_cli(directory, script="diff.py")
            self.assertEqual(result.returncode, 0, result.stderr)
            result = run_cli("worklists", directory, "--workers", "4", "--update")
            self.assertEqual(result.returncode, 1)
            self.assertIn("no conflicts computed since diff.py ran", result.stderr)
            data = read_json(os.path.join(directory, "diff.json"))
            data["conflicts"] = {"2230": {"title": {"written": "a", "flowkiwi": "b"}}}
            write_json(os.path.join(directory, "diff.json"), checked(data))
            result = run_cli("worklists", directory, "--workers", "4", "--update")
            self.assertEqual(result.returncode, 0, result.stderr)
            output = json.loads(result.stdout)
            self.assertEqual((output["pass"], output["products"]), ("updates", 1))
            self.assertIn("modified in Flowkiwi", output["skipped"]["2230"])
            self.assertEqual(read_json(os.path.join(directory, "worklists", "worker-1.json")), ["2226"])

    def test_errors(self):
        products = make_products()
        st = make_state(products)
        state.merge(st, [created_line("2226")], products)
        state.start_phase(st)
        with tempfile.TemporaryDirectory() as directory:
            write_workspace(directory, products, st)
            result = run_cli("worklists", directory, "--update")
            self.assertEqual(result.returncode, 1)
            self.assertIn("run diff.py", result.stderr)
            write_json(os.path.join(directory, "diff.json"), {"phase": 1, "changed": {}, "removed": []})
            result = run_cli("worklists", directory, "--update")
            self.assertEqual(result.returncode, 1)
            self.assertIn("error: diff.json is from phase 1, run diff.py again", result.stderr)


if __name__ == "__main__":
    unittest.main()
