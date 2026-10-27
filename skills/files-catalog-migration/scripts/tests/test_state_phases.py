import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import dryrun  # noqa: E402
import state  # noqa: E402
from phase_helpers import (created_line, make_products, make_state, product_iri, read_json, run_cli,  # noqa: E402
                           variant_iri, write_json, write_workspace)


class PhasesTest(unittest.TestCase):
    def setUp(self):
        self.products = make_products()
        self.st = make_state(self.products)

    def test_init_records_phase_one(self):
        self.assertEqual(self.st["phase"], 1)
        self.assertEqual(self.st["phases"], [{"n": 1, "started_at": self.st["started_at"]}])

    def test_merge_records_written(self):
        state.merge(self.st, [created_line("2226")], self.products)
        written = self.st["products"]["2226"]["written"]
        self.assertEqual(written["phase"], 1)
        self.assertEqual(written["payload"]["product"]["body"]["title"], self.products["products"][0]["title"])

    def test_phase_start_and_update_line(self):
        state.merge(self.st, [created_line("2226")], self.products)
        self.assertEqual(state.start_phase(self.st), 2)
        self.products["products"][0]["title"] = "Nouveau titre"
        variant = self.st["products"]["2226"]["items"][1]["iri"]
        state.merge(self.st, [dict(created_line("2226"), status="updated", items=[], deleted=[variant])], self.products)
        p = self.st["products"]["2226"]
        self.assertEqual((p["status"], p["updated_phase"], p["written"]["phase"]), ("created", 2, 2))
        self.assertEqual(p["written"]["payload"]["product"]["body"]["title"], "Nouveau titre")
        self.assertEqual(p["items"][1]["status"], "deleted")

    def test_removed_flag_and_archived(self):
        state.merge(self.st, [created_line("2226")], self.products)
        state.sync_products(self.st, {"products": [p for p in self.products["products"] if p["handle"] != "2226"]})
        self.assertTrue(self.st["products"]["2226"]["removed"])
        state.merge(self.st, [dict(created_line("2226"), status="archived", items=[])], self.products)
        self.assertEqual(self.st["products"]["2226"]["status"], "archived")

    def test_archived_snapshot_keeps_written_with_status(self):
        state.merge(self.st, [created_line("2226")], self.products)
        before = self.st["products"]["2226"]["written"]["payload"]
        state.start_phase(self.st)
        gone = {"products": [p for p in self.products["products"] if p["handle"] != "2226"]}
        state.merge(self.st, [dict(created_line("2226"), status="archived", items=[])], gone)
        written = self.st["products"]["2226"]["written"]
        self.assertEqual(written["phase"], 2)
        self.assertEqual(written["payload"]["product"]["body"]["status"], "/api/statuses/ARCHIVED")
        self.assertEqual(before["product"]["body"]["status"], "/api/statuses/DRAFT")
        self.assertEqual(written["payload"]["variants"], before["variants"])

    def test_product_scope(self):
        state.merge(self.st, [created_line("2226"), created_line("2230")], self.products)
        owners = {s["owner"] for s in state.undo_plan(self.st, "product:2226")}
        self.assertEqual(owners, {"2226"})

    def test_phase_refused_with_unmerged_results(self):
        with tempfile.TemporaryDirectory() as directory:
            write_workspace(directory, self.products, self.st)
            write_json(os.path.join(directory, "results", "worker-1.jsonl"), created_line("2226"))
            result = run_cli("phase", directory, "--start")
            self.assertEqual(result.returncode, 1)
            self.assertIn("run state.py merge first", result.stderr)
            self.assertEqual(read_json(os.path.join(directory, "state.json"))["phase"], 1)


class PhaseDetailsTest(unittest.TestCase):
    def setUp(self):
        self.products = make_products()
        self.st = make_state(self.products)

    def without(self, handle):
        return {"products": [p for p in self.products["products"] if p["handle"] != handle]}

    def test_start_phase_appends_phases(self):
        state.start_phase(self.st)
        self.assertEqual(state.start_phase(self.st), 3)
        self.assertEqual([p["n"] for p in self.st["phases"]], [1, 2, 3])
        self.assertEqual(state.summary(self.st)["phase"], 3)

    def test_written_needs_products(self):
        state.merge(self.st, [created_line("2226")])
        self.assertNotIn("written", self.st["products"]["2226"])

    def test_payload_error_keeps_written(self):
        state.merge(self.st, [created_line("2226")], self.products)
        before = self.st["products"]["2226"]["written"]
        del self.st["decisions"]["status_mode"]
        state.start_phase(self.st)
        err = io.StringIO()
        with redirect_stderr(err):
            state.merge(self.st, [dict(created_line("2226"), status="updated", items=[])], self.products)
        self.assertEqual(self.st["products"]["2226"]["written"], before)
        self.assertEqual(self.st["products"]["2226"]["updated_phase"], 2)
        self.assertIn("warning: no written snapshot for 2226: decision 'status_mode' is missing", err.getvalue())

    def test_failed_line_writes_no_snapshot(self):
        state.merge(self.st, [dict(created_line("2226"), status="failed", error="422")], self.products)
        self.assertNotIn("written", self.st["products"]["2226"])

    def test_failed_update_keeps_created(self):
        state.merge(self.st, [created_line("2226")], self.products)
        state.start_phase(self.st)
        state.merge(self.st, [dict(created_line("2226"), status="failed", items=[], error="variant 422")], self.products)
        p = self.st["products"]["2226"]
        self.assertEqual((p["status"], p["error"], p["written"]["phase"]), ("created", "variant 422", 1))

    def test_removed_flag_cleared_when_back(self):
        state.merge(self.st, [created_line("2226")], self.products)
        state.sync_products(self.st, self.without("2226"))
        state.sync_products(self.st, self.products)
        self.assertNotIn("removed", self.st["products"]["2226"])

    def test_archived_stays_flagged_and_pending_is_skipped(self):
        state.merge(self.st, [dict(created_line("2226"), status="archived", items=[])], self.products)
        state.sync_products(self.st, {"products": []})
        self.assertTrue(self.st["products"]["2226"]["removed"])
        self.assertEqual(self.st["products"]["2226"]["status"], "archived")
        self.assertEqual(self.st["products"]["2230"]["status"], "skipped")
        self.assertNotIn("removed", self.st["products"]["2230"])
        self.assertEqual(state.summary(self.st)["removed"], 1)

    def test_deleted_handle_is_never_flagged_removed(self):
        state.merge(self.st, [created_line("2226")], self.products)
        state.sync_products(self.st, self.without("2226"))
        state.mark_deleted(self.st, [s["iri"] for s in state.undo_plan(self.st, "product:2226")])
        self.assertEqual(self.st["products"]["2226"]["status"], "deleted")
        self.assertNotIn("removed", self.st["products"]["2226"])
        state.sync_products(self.st, self.without("2226"))
        self.assertNotIn("removed", self.st["products"]["2226"])
        self.assertEqual(state.summary(self.st)["removed"], 0)

    def test_product_scope_children_first_and_created_only(self):
        line = created_line("2230")
        line["items"][1]["status"] = "reused"
        state.merge(self.st, [line, created_line("2226")], self.products)
        steps = state.undo_plan(self.st, "product:2230")
        self.assertEqual([s["iri"] for s in steps], [variant_iri("2230", "1006"), product_iri("2230")])
        self.assertNotIn("metafield_definition", {s["kind"] for s in steps})

    def test_product_scope_errors(self):
        self.assertEqual(state.check_scope("product:2226"), "product:2226")
        with self.assertRaisesRegex(ValueError, "unknown handle 'nope'"):
            state.undo_plan(self.st, "product:nope")
        with self.assertRaisesRegex(ValueError, "product:<handle>"):
            state.check_scope("product:")

    def test_phase_decision_is_phase_scoped(self):
        state.start_phase(self.st)
        state.decide(self.st, "removed_action", {"phase": 1, "action": "archive"}, "developer")
        self.assertIsNone(state.phase_decision(self.st, "removed_action"))
        state.decide(self.st, "removed_action", {"phase": 2, "action": "archive"}, "developer")
        self.assertEqual(state.phase_decision(self.st, "removed_action"), {"phase": 2, "action": "archive"})
        state.start_phase(self.st)
        self.assertIsNone(state.phase_decision(self.st, "removed_action"))
        self.assertIsNone(state.phase_decision(self.st, "missing"))

    def test_forced(self):
        st = {"phase": 2, "decisions": {"force_update": {"phase": 1, "handles": "all"}}}
        self.assertFalse(state.forced(st, "2226"))
        st["decisions"]["force_update"] = {"phase": 2, "handles": ["2226"]}
        self.assertTrue(state.forced(st, "2226"))
        self.assertFalse(state.forced(st, "2230"))
        st["decisions"]["force_update"] = {"phase": 2, "handles": "all"}
        self.assertTrue(state.forced(st, "2230"))

    def test_dryrun_does_not_count_deleted_as_to_create(self):
        state.merge(self.st, [created_line("2226")], self.products)
        state.mark_deleted(self.st, [product_iri("2226")])
        pending, created = dryrun.split_products(self.products, self.st)
        self.assertEqual(([p["handle"] for p in pending], created), (["2230"], 0))


class ResultLineTest(unittest.TestCase):
    def test_updated_and_archived_need_iri(self):
        for status in ("updated", "archived"):
            with self.assertRaisesRegex(ValueError, "iri is required"):
                state.validate_result(dict(created_line("2226"), status=status, iri=None))
            state.validate_result(dict(created_line("2226"), status=status))

    def test_deleted_iris_are_checked(self):
        state.validate_result(dict(created_line("2226"), deleted=[variant_iri("2226", "1001")]))
        with self.assertRaisesRegex(ValueError, "Invalid IRI"):
            state.validate_result(dict(created_line("2226"), deleted=["/api/variants/42"]))
        with self.assertRaisesRegex(ValueError, "deleted must be a list"):
            state.validate_result(dict(created_line("2226"), deleted=variant_iri("2226", "1001")))


class PhaseCliTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        self.products = make_products()
        write_workspace(self.dir, self.products, make_state(self.products))

    def test_merge_then_start(self):
        result_file = os.path.join(self.dir, "results", "worker-1.jsonl")
        write_json(result_file, created_line("2226"))
        self.assertEqual(run_cli("merge", self.dir).returncode, 0)
        st = read_json(os.path.join(self.dir, "state.json"))
        self.assertEqual(st["products"]["2226"]["written"]["payload"]["variants"][0]["sku"], "1001")
        started = run_cli("phase", self.dir, "--start")
        self.assertEqual((started.returncode, started.stdout.strip()), (0, "2"))
        st = read_json(os.path.join(self.dir, "state.json"))
        self.assertEqual((st["phase"], [p["n"] for p in st["phases"]]), (2, [1, 2]))
        self.assertEqual(run_cli("phase", self.dir).stdout.strip(), "2")

    def test_merge_warns_without_snapshot(self):
        st = make_state(self.products)
        del st["decisions"]["status_mode"]
        write_workspace(self.dir, self.products, st)
        write_json(os.path.join(self.dir, "results", "worker-1.jsonl"), created_line("2226"))
        merged = run_cli("merge", self.dir)
        self.assertEqual(merged.returncode, 0)
        self.assertIn("warning: no written snapshot for 2226: decision 'status_mode' is missing", merged.stderr)
        self.assertEqual(read_json(os.path.join(self.dir, "state.json"))["products"]["2226"]["status"], "created")


if __name__ == "__main__":
    unittest.main()
