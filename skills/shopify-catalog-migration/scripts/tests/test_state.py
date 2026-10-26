import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import state  # noqa: E402

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "state.py")


def products(*handles):
    return {"products": [{"handle": h} for h in handles], "warnings": []}


def item(kind, iri, status="created", module="core"):
    return {"kind": kind, "iri": iri, "status": status, "module": module}


def run_cli(*args):
    return subprocess.run([sys.executable, SCRIPT, *args], capture_output=True, text=True)


class StateFunctionsTest(unittest.TestCase):
    def setUp(self):
        self.state = state.init("acme", "https://mcp", products("a", "b", "c", "d", "e"))

    def test_init_all_pending(self):
        self.assertEqual({p["status"] for p in self.state["products"].values()}, {"pending"})
        self.assertEqual(len(self.state["products"]), 5)
        self.assertEqual(self.state["client"], "acme")

    def test_sync_keeps_existing_status(self):
        self.state["products"]["a"]["status"] = "created"
        state.sync_products(self.state, products("a", "f"))
        self.assertEqual(self.state["products"]["a"]["status"], "created")
        self.assertEqual(self.state["products"]["f"]["status"], "pending")

    def test_worklists_round_robin(self):
        lists = state.worklists(self.state, 2, None, False)
        self.assertEqual(sorted(len(l) for l in lists), [2, 3])
        flat = [h for l in lists for h in l]
        self.assertEqual(sorted(flat), ["a", "b", "c", "d", "e"])

    def test_worklists_pilot_limit(self):
        lists = state.worklists(self.state, 4, 3, True)
        self.assertEqual(sum(len(l) for l in lists), 3)
        pilots = [h for h, p in self.state["products"].items() if p["pilot"]]
        self.assertEqual(len(pilots), 3)

    def test_worklists_skip_created(self):
        self.state["products"]["a"]["status"] = "created"
        self.state["products"]["b"]["status"] = "failed"
        flat = [h for l in state.worklists(self.state, 2, None, False) for h in l]
        self.assertNotIn("a", flat)
        self.assertNotIn("b", flat)

    def test_worklists_retry_failed(self):
        self.state["products"]["b"]["status"] = "failed"
        flat = [h for l in state.worklists(self.state, 2, None, False, retry_failed=True) for h in l]
        self.assertIn("b", flat)

    def test_worklists_module_targets_created_products_without_module_items(self):
        state.merge(self.state, [
            {"handle": "a", "status": "created", "iri": "/p/a", "error": None,
             "items": [item("product", "/p/a"), item("sale_offer", "/so/a", module="prices")]},
            {"handle": "b", "status": "created", "iri": "/p/b", "error": None,
             "items": [item("product", "/p/b")]},
        ])
        flat = [h for l in state.worklists(self.state, 2, None, False, module="prices") for h in l]
        self.assertEqual(flat, ["b"])

    def test_decide_stores_value(self):
        state.decide(self.state, "status_mode", "draft")
        self.assertEqual(self.state["decisions"]["status_mode"], "draft")

    def test_merge_last_line_wins(self):
        state.merge(self.state, [
            {"handle": "a", "status": "failed", "iri": None, "items": [], "error": "boom"},
            {"handle": "a", "status": "created", "iri": "/api/products/1", "items": [], "error": None},
        ])
        self.assertEqual(self.state["products"]["a"]["status"], "created")
        self.assertEqual(self.state["products"]["a"]["iri"], "/api/products/1")
        self.assertIsNone(self.state["products"]["a"]["error"])

    def test_merge_no_duplicate_items(self):
        line = {"handle": "a", "status": "created", "iri": "/api/products/1",
                "items": [item("product", "/api/products/1")], "error": None}
        state.merge(self.state, [line])
        state.merge(self.state, [line])
        self.assertEqual(len(self.state["products"]["a"]["items"]), 1)

    def test_merge_keeps_items_of_failed_attempt(self):
        state.merge(self.state, [{"handle": "a", "status": "failed", "iri": "/api/products/1",
                                  "items": [item("product", "/api/products/1")], "error": "variant 422"}])
        self.assertEqual(self.state["products"]["a"]["items"][0]["iri"], "/api/products/1")

    def test_merge_missing_line_stays_pending(self):
        state.merge(self.state, [{"handle": "a", "status": "created", "iri": "/x", "items": [], "error": None}])
        self.assertEqual(self.state["products"]["b"]["status"], "pending")

    def test_record_shared(self):
        state.record_shared(self.state, "category:Shoes", "/api/categories/1", "created", "taxonomy")
        self.assertEqual(self.state["shared"]["category:Shoes"],
                         {"iri": "/api/categories/1", "status": "created", "module": "taxonomy"})

    def _populate(self):
        self.state["products"]["a"]["pilot"] = True
        state.merge(self.state, [
            {"handle": "a", "status": "created", "iri": "/p/a", "error": None, "items": [
                item("product", "/p/a"), item("variant", "/v/a"), item("sale_offer", "/so/a", module="prices"),
            ]},
            {"handle": "b", "status": "created", "iri": "/p/b", "error": None, "items": [
                item("product", "/p/b", status="reused"), item("variant", "/v/b"),
            ]},
        ])
        state.record_shared(self.state, "category:Shoes", "/c/1", "created", "taxonomy")
        state.record_shared(self.state, "supplier:Acme", "/s/1", "reused", "costs-suppliers")

    def test_undo_order(self):
        self._populate()
        iris = [step["iri"] for step in state.undo_plan(self.state, "all")]
        self.assertLess(iris.index("/so/a"), iris.index("/v/a"))
        self.assertLess(iris.index("/v/a"), iris.index("/p/a"))
        self.assertLess(iris.index("/p/a"), iris.index("/c/1"))

    def test_undo_link_items_before_product(self):
        state.merge(self.state, [{"handle": "c", "status": "created", "iri": "/p/c", "error": None, "items": [
            item("product", "/p/c"), item("channel_link", "/p/c#channels", module="channels"),
        ]}])
        kinds = [s["kind"] for s in state.undo_plan(self.state, "all")]
        self.assertLess(kinds.index("channel_link"), kinds.index("product"))

    def test_undo_excludes_reused(self):
        self._populate()
        iris = [step["iri"] for step in state.undo_plan(self.state, "all")]
        self.assertNotIn("/p/b", iris)
        self.assertNotIn("/s/1", iris)
        self.assertIn("/v/b", iris)

    def test_undo_pilot_scope(self):
        self._populate()
        iris = {step["iri"] for step in state.undo_plan(self.state, "pilot")}
        self.assertEqual(iris, {"/p/a", "/v/a", "/so/a"})

    def test_undo_module_scope(self):
        self._populate()
        self.assertEqual([s["iri"] for s in state.undo_plan(self.state, "module:prices")], ["/so/a"])
        self.assertEqual([s["iri"] for s in state.undo_plan(self.state, "module:taxonomy")], ["/c/1"])

    def test_undo_skips_deleted(self):
        self._populate()
        state.mark_deleted(self.state, ["/so/a"])
        iris = [step["iri"] for step in state.undo_plan(self.state, "all")]
        self.assertNotIn("/so/a", iris)

    def test_mark_deleted_product_status(self):
        self._populate()
        state.mark_deleted(self.state, ["/p/a", "/c/1"])
        self.assertEqual(self.state["products"]["a"]["status"], "deleted")
        self.assertEqual(self.state["shared"]["category:Shoes"]["status"], "deleted")

    def test_reset_deleted_makes_products_pending_again(self):
        self._populate()
        state.mark_deleted(self.state, ["/so/a", "/v/a", "/p/a"])
        state.reset_deleted(self.state)
        product = self.state["products"]["a"]
        self.assertEqual(product["status"], "pending")
        self.assertIsNone(product["iri"])
        self.assertFalse(product["pilot"])
        self.assertEqual(product["items"], [])
        self.assertEqual(self.state["products"]["b"]["status"], "created")

    def test_sync_marks_removed_handles_skipped(self):
        state.sync_products(self.state, products("a", "b", "c", "d"))
        self.assertEqual(self.state["products"]["e"]["status"], "skipped")
        flat = [h for l in state.worklists(self.state, 2, None, False) for h in l]
        self.assertNotIn("e", flat)
        state.sync_products(self.state, products("a", "b", "c", "d", "e"))
        self.assertEqual(self.state["products"]["e"]["status"], "pending")

    def test_worklists_same_title_same_worker(self):
        titles = {"a": "Mug", "b": "Cap", "c": "mug ", "d": "Hat", "e": "Bag"}
        lists = state.worklists(self.state, 4, None, False, titles=titles)
        worker_of = {h: i for i, l in enumerate(lists) for h in l}
        self.assertEqual(worker_of["a"], worker_of["c"])

    def test_module_result_does_not_change_product_status(self):
        self._populate()
        state.merge(self.state, [{"handle": "a", "status": "failed", "iri": "/p/a", "module": "channels",
                                  "items": [], "error": "422"}])
        self.assertEqual(self.state["products"]["a"]["status"], "created")
        self.assertEqual(self.state["products"]["a"]["module_errors"], {"channels": "422"})

    def test_module_worklist_ignores_deleted_module_items(self):
        self._populate()
        state.mark_deleted(self.state, ["/so/a"])
        flat = [h for l in state.worklists(self.state, 2, None, False, module="prices") for h in l]
        self.assertIn("a", flat)

    def test_merge_revives_deleted_item_with_same_iri(self):
        self._populate()
        state.mark_deleted(self.state, ["/so/a"])
        state.merge(self.state, [{"handle": "a", "status": "created", "iri": "/p/a", "module": "prices",
                                  "items": [item("sale_offer", "/so/a", module="prices")], "error": None}])
        statuses = [i["status"] for i in self.state["products"]["a"]["items"] if i["iri"] == "/so/a"]
        self.assertEqual(statuses, ["created"])

    def test_undo_deletes_later_shared_items_first_within_kind(self):
        state.record_shared(self.state, "category:Apparel", "/c/parent", "created", "taxonomy")
        state.record_shared(self.state, "category:Apparel > Shirts", "/c/child", "created", "taxonomy")
        iris = [s["iri"] for s in state.undo_plan(self.state, "all")]
        self.assertLess(iris.index("/c/child"), iris.index("/c/parent"))

    def test_summary(self):
        self._populate()
        self.state["products"]["c"].update(status="failed", error="422")
        summary = state.summary(self.state)
        self.assertEqual(summary["products"]["created"], 2)
        self.assertEqual(summary["products"]["failed"], 1)
        self.assertEqual(summary["items"]["variant"]["created"], 2)
        self.assertEqual(summary["failed"], [{"handle": "c", "error": "422"}])


class StateCliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        with open(os.path.join(self.dir, "products.json"), "w") as f:
            json.dump(products("a", "b"), f)

    def tearDown(self):
        self.tmp.cleanup()

    def test_init_refuses_overwrite(self):
        self.assertEqual(run_cli("init", self.dir, "--client", "acme", "--mcp-url", "u").returncode, 0)
        second = run_cli("init", self.dir, "--client", "acme", "--mcp-url", "u")
        self.assertNotEqual(second.returncode, 0)

    def test_worklists_result_merge_cycle(self):
        run_cli("init", self.dir, "--client", "acme", "--mcp-url", "u")
        self.assertEqual(run_cli("worklists", self.dir, "--workers", "2").returncode, 0)
        with open(os.path.join(self.dir, "worklists", "worker-1.json")) as f:
            self.assertEqual(len(json.load(f)), 1)
        result_file = os.path.join(self.dir, "results", "worker-1.jsonl")
        line = {"handle": "a", "status": "created", "iri": "/p/a", "items": [], "error": None}
        self.assertEqual(run_cli("result", result_file, "--json", json.dumps(line)).returncode, 0)
        merged = run_cli("merge", self.dir)
        self.assertEqual(merged.returncode, 0, merged.stderr)
        with open(os.path.join(self.dir, "state.json")) as f:
            self.assertEqual(json.load(f)["products"]["a"]["status"], "created")

    def test_result_rejects_missing_keys(self):
        result_file = os.path.join(self.dir, "results", "worker-1.jsonl")
        self.assertNotEqual(run_cli("result", result_file, "--json", '{"handle": "a"}').returncode, 0)

    def test_merge_does_not_replay_merged_results(self):
        run_cli("init", self.dir, "--client", "acme", "--mcp-url", "u")
        result_file = os.path.join(self.dir, "results", "worker-1.jsonl")
        line = {"handle": "a", "status": "created", "iri": "/p/a", "items": [item("product", "/p/a")], "error": None}
        run_cli("result", result_file, "--json", json.dumps(line))
        run_cli("merge", self.dir)
        iris_file = os.path.join(self.dir, "deleted.json")
        with open(iris_file, "w") as f:
            json.dump(["/p/a"], f)
        run_cli("deleted", self.dir, "--iris-file", iris_file)
        run_cli("reset-deleted", self.dir)
        run_cli("merge", self.dir)
        with open(os.path.join(self.dir, "state.json")) as f:
            self.assertEqual(json.load(f)["products"]["a"]["status"], "pending")

    def test_merge_skips_malformed_line(self):
        run_cli("init", self.dir, "--client", "acme", "--mcp-url", "u")
        os.makedirs(os.path.join(self.dir, "results"))
        with open(os.path.join(self.dir, "results", "worker-1.jsonl"), "w") as f:
            f.write("{not json\n")
            f.write(json.dumps({"handle": "b", "status": "created", "iri": "/p/b", "items": [], "error": None}) + "\n")
        merged = run_cli("merge", self.dir)
        self.assertEqual(merged.returncode, 0)
        self.assertIn("malformed", merged.stdout + merged.stderr)
        with open(os.path.join(self.dir, "state.json")) as f:
            self.assertEqual(json.load(f)["products"]["b"]["status"], "created")


if __name__ == "__main__":
    unittest.main()
