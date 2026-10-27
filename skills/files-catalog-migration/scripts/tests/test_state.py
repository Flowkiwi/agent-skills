import glob
import json
import os
import subprocess
import sys
import tempfile
import unittest
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import state  # noqa: E402

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "state.py")


def iri(resource, name):
    return f"/api/{resource}/{uuid.uuid5(uuid.NAMESPACE_URL, resource + name)}"


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
            {"handle": "a", "status": "created", "iri": iri("products", "a"), "error": None,
             "items": [item("product", iri("products", "a")), item("sale_offer", iri("sale_offers", "a"), module="prices")]},
            {"handle": "b", "status": "created", "iri": iri("products", "b"), "error": None,
             "items": [item("product", iri("products", "b"))]},
        ])
        flat = [h for l in state.worklists(self.state, 2, None, False, module="prices") for h in l]
        self.assertEqual(flat, ["b"])

    def test_decide_stores_value(self):
        state.decide(self.state, "status_mode", "draft", "developer")
        self.assertEqual(self.state["decisions"]["status_mode"], "draft")

    def test_merge_last_line_wins(self):
        state.merge(self.state, [
            {"handle": "a", "status": "failed", "iri": None, "items": [], "error": "boom"},
            {"handle": "a", "status": "created", "iri": iri("products", "1"), "items": [], "error": None},
        ])
        self.assertEqual(self.state["products"]["a"]["status"], "created")
        self.assertEqual(self.state["products"]["a"]["iri"], iri("products", "1"))
        self.assertIsNone(self.state["products"]["a"]["error"])

    def test_merge_no_duplicate_items(self):
        line = {"handle": "a", "status": "created", "iri": iri("products", "1"),
                "items": [item("product", iri("products", "1"))], "error": None}
        state.merge(self.state, [line])
        state.merge(self.state, [line])
        self.assertEqual(len(self.state["products"]["a"]["items"]), 1)

    def test_merge_keeps_items_of_failed_attempt(self):
        state.merge(self.state, [{"handle": "a", "status": "failed", "iri": iri("products", "1"),
                                  "items": [item("product", iri("products", "1"))], "error": "variant 422"}])
        self.assertEqual(self.state["products"]["a"]["items"][0]["iri"], iri("products", "1"))

    def test_merge_missing_line_stays_pending(self):
        state.merge(self.state, [{"handle": "a", "status": "created", "iri": iri("products", "x"), "items": [], "error": None}])
        self.assertEqual(self.state["products"]["b"]["status"], "pending")

    def test_record_shared(self):
        state.record_shared(self.state, "category:Shoes", iri("categories", "1"), "created", "taxonomy")
        self.assertEqual(self.state["shared"]["category:Shoes"],
                         {"iri": iri("categories", "1"), "status": "created", "module": "taxonomy"})

    def _populate(self):
        self.state["products"]["a"]["pilot"] = True
        state.merge(self.state, [
            {"handle": "a", "status": "created", "iri": iri("products", "a"), "error": None, "items": [
                item("product", iri("products", "a")), item("variant", iri("variants", "a")), item("sale_offer", iri("sale_offers", "a"), module="prices"),
            ]},
            {"handle": "b", "status": "created", "iri": iri("products", "b"), "error": None, "items": [
                item("product", iri("products", "b"), status="reused"), item("variant", iri("variants", "b")),
            ]},
        ])
        state.record_shared(self.state, "category:Shoes", iri("categories", "1"), "created", "taxonomy")
        state.record_shared(self.state, "supplier:Acme", iri("suppliers", "1"), "reused", "costs-suppliers")

    def test_undo_order(self):
        self._populate()
        iris = [step["iri"] for step in state.undo_plan(self.state, "all")]
        self.assertLess(iris.index(iri("sale_offers", "a")), iris.index(iri("variants", "a")))
        self.assertLess(iris.index(iri("variants", "a")), iris.index(iri("products", "a")))
        self.assertLess(iris.index(iri("products", "a")), iris.index(iri("categories", "1")))

    def test_undo_link_items_before_product(self):
        state.merge(self.state, [{"handle": "c", "status": "created", "iri": iri("products", "c"), "error": None, "items": [
            item("product", iri("products", "c")), item("channel_link", iri("products", "c") + "#channels", module="channels"),
        ]}])
        kinds = [s["kind"] for s in state.undo_plan(self.state, "all")]
        self.assertLess(kinds.index("channel_link"), kinds.index("product"))

    def test_undo_excludes_reused(self):
        self._populate()
        iris = [step["iri"] for step in state.undo_plan(self.state, "all")]
        self.assertNotIn(iri("products", "b"), iris)
        self.assertNotIn(iri("suppliers", "1"), iris)
        self.assertIn(iri("variants", "b"), iris)

    def test_undo_pilot_scope(self):
        self._populate()
        iris = {step["iri"] for step in state.undo_plan(self.state, "pilot")}
        self.assertEqual(iris, {iri("products", "a"), iri("variants", "a"), iri("sale_offers", "a")})

    def test_undo_module_scope(self):
        self._populate()
        self.assertEqual([s["iri"] for s in state.undo_plan(self.state, "module:prices")], [iri("sale_offers", "a")])
        self.assertEqual([s["iri"] for s in state.undo_plan(self.state, "module:taxonomy")], [iri("categories", "1")])

    def test_undo_skips_deleted(self):
        self._populate()
        state.mark_deleted(self.state, [iri("sale_offers", "a")])
        iris = [step["iri"] for step in state.undo_plan(self.state, "all")]
        self.assertNotIn(iri("sale_offers", "a"), iris)

    def test_mark_deleted_product_status(self):
        self._populate()
        state.mark_deleted(self.state, [iri("products", "a"), iri("categories", "1")])
        self.assertEqual(self.state["products"]["a"]["status"], "deleted")
        self.assertEqual(self.state["shared"]["category:Shoes"]["status"], "deleted")

    def test_reset_deleted_makes_products_pending_again(self):
        self._populate()
        state.mark_deleted(self.state, [iri("sale_offers", "a"), iri("variants", "a"), iri("products", "a")])
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
        state.merge(self.state, [{"handle": "a", "status": "failed", "iri": iri("products", "a"), "module": "channels",
                                  "items": [], "error": "422"}])
        self.assertEqual(self.state["products"]["a"]["status"], "created")
        self.assertEqual(self.state["products"]["a"]["module_errors"], {"channels": "422"})

    def test_module_worklist_ignores_deleted_module_items(self):
        self._populate()
        state.mark_deleted(self.state, [iri("sale_offers", "a")])
        flat = [h for l in state.worklists(self.state, 2, None, False, module="prices") for h in l]
        self.assertIn("a", flat)

    def test_merge_revives_deleted_item_with_same_iri(self):
        self._populate()
        state.mark_deleted(self.state, [iri("sale_offers", "a")])
        state.merge(self.state, [{"handle": "a", "status": "created", "iri": iri("products", "a"), "module": "prices",
                                  "items": [item("sale_offer", iri("sale_offers", "a"), module="prices")], "error": None}])
        statuses = [i["status"] for i in self.state["products"]["a"]["items"] if i["iri"] == iri("sale_offers", "a")]
        self.assertEqual(statuses, ["created"])

    def test_undo_deletes_later_shared_items_first_within_kind(self):
        state.record_shared(self.state, "category:Apparel", iri("categories", "parent"), "created", "taxonomy")
        state.record_shared(self.state, "category:Apparel > Shirts", iri("categories", "child"), "created", "taxonomy")
        iris = [s["iri"] for s in state.undo_plan(self.state, "all")]
        self.assertLess(iris.index(iri("categories", "child")), iris.index(iri("categories", "parent")))

    def test_summary(self):
        self._populate()
        self.state["products"]["c"].update(status="failed", error="422")
        summary = state.summary(self.state)
        self.assertEqual(summary["products"]["created"], 2)
        self.assertEqual(summary["products"]["failed"], 1)
        self.assertEqual(summary["items"]["variant"]["created"], 2)
        self.assertEqual(summary["failed"], [{"handle": "c", "error": "422"}])


def line(handle, status="created", items=(), **extra):
    data = {"handle": handle, "status": status, "iri": iri("products", handle) if status == "created" else None,
            "items": list(items), "error": None if status == "created" else "boom"}
    data.update(extra)
    return data


def entry(source, result, number=1):
    return {"file": source, "line": number, "result": result}


class ValidateResultTest(unittest.TestCase):
    def test_valid_line(self):
        state.validate_result(line("a", items=[item("product", iri("products", "a")),
                                               item("channel_link", iri("products", "a") + "#channels")]))

    def test_nested_iri_is_valid(self):
        nested = iri("products", "a") + "/options/" + str(uuid.uuid5(uuid.NAMESPACE_URL, "o"))
        state.validate_result(line("a", items=[item("product_option", nested)]))

    def test_translation_link_with_locale(self):
        state.validate_result(line("a", items=[
            item("translation_link", iri("products", "a") + "#translations-de-DE", module="translations")]))

    def test_rejects_non_uuid_iri(self):
        with self.assertRaises(ValueError):
            state.validate_result(line("a", items=[item("product", "/api/products/1")]))

    def test_rejects_retyped_iri(self):
        with self.assertRaises(ValueError):
            state.validate_result(line("a", items=[item("variant", "api/variants/" + "0" * 36)]))

    def test_created_requires_iri(self):
        with self.assertRaises(ValueError):
            state.validate_result(dict(line("a"), iri=None))

    def test_failed_allows_null_iri(self):
        state.validate_result(line("a", status="failed"))

    def test_relation_suffix_only_for_links(self):
        with self.assertRaises(ValueError):
            state.validate_result(line("a", items=[item("product", iri("products", "a") + "#channels")]))
        with self.assertRaises(ValueError):
            state.validate_result(line("a", items=[item("channel_link", iri("products", "a"))]))

    def test_verify_key(self):
        state.validate_result(line("a", verify="fixed"))
        with self.assertRaises(ValueError):
            state.validate_result(line("a", verify="maybe"))

    def test_item_status(self):
        with self.assertRaises(ValueError):
            state.validate_result(line("a", items=[item("product", iri("products", "a"), status="deleted")]))


class CheckResultsTest(unittest.TestCase):
    def test_rejects_handle_outside_worklist(self):
        accepted, rejected, _ = state.check_results(
            [entry("worker-1", line("a")), entry("worker-1", line("b"))], {"worker-1": {"a"}})
        self.assertEqual([r["handle"] for r in accepted], ["a"])
        self.assertEqual(rejected[0]["result"]["handle"], "b")
        self.assertIn("worklists/worker-1.json", rejected[0]["reason"])

    def test_no_worklist_no_check(self):
        accepted, rejected, _ = state.check_results([entry("worker-2", line("b"))], {"worker-1": {"a"}})
        self.assertEqual(len(accepted), 1)
        self.assertEqual(rejected, [])

    def test_rejects_unknown_handle(self):
        accepted, rejected, _ = state.check_results([entry("worker-1", line("zz"))], known={"a"})
        self.assertEqual(accepted, [])
        self.assertIn("unknown handle", rejected[0]["reason"])

    def test_same_handle_in_two_files_keeps_created(self):
        accepted, rejected, notes = state.check_results(
            [entry("worker-1", line("a", status="failed")), entry("worker-2", line("a"))])
        self.assertEqual([r["status"] for r in accepted], ["created"])
        self.assertEqual(rejected[0]["file"], "worker-1")
        self.assertTrue(notes)

    def test_conflicting_created_iris_reject_all(self):
        other = dict(line("a"), iri=iri("products", "other"))
        accepted, rejected, notes = state.check_results([entry("worker-1", line("a")), entry("worker-2", other)])
        self.assertEqual(accepted, [])
        self.assertEqual(len(rejected), 2)
        self.assertIn("conflicting", notes[0])

    def test_retries_in_same_file_are_kept(self):
        accepted, rejected, notes = state.check_results(
            [entry("worker-1", line("a", status="failed")), entry("worker-1", line("a"), 2)])
        self.assertEqual(len(accepted), 2)
        self.assertEqual((rejected, notes), ([], []))


class SummaryVerifyTest(unittest.TestCase):
    def test_verify_counts(self):
        st = state.init("acme", "u", products("a", "b", "c"))
        state.merge(st, [line("a", verify="ok"), line("b", verify="mismatch"), line("c")])
        summary = state.summary(st)
        self.assertEqual(summary["verify"], {"ok": 1, "mismatch": 1})
        self.assertEqual(summary["verify_mismatch"], ["b"])


def catalog_product(handle, variants=1, category=None, metafields=None, status="active", description=""):
    return {"handle": handle, "title": handle, "product_category": category, "type": None, "status": status,
            "description_html": description, "metafields": metafields or {},
            "variants": [{"sku": f"{handle}-{i}"} for i in range(variants)]}


class PilotAndSelectionTest(unittest.TestCase):
    def setUp(self):
        self.catalog = [
            catalog_product("plain-1"), catalog_product("plain-2"), catalog_product("plain-3"),
            catalog_product("sizes", variants=3, category="Apparel"),
            catalog_product("syrup", metafields={"custom.ingredients": "Sugar"}),
            catalog_product("old", status="archived"),
            catalog_product("story", description="x" * 500),
            catalog_product("plain-4"),
        ]
        self.state = state.init("acme", "u", {"products": self.catalog})

    def test_representative_pilot_covers_features(self):
        handles = [p["handle"] for p in self.catalog]
        selected = state.pilot_selection(self.catalog, handles, 5)
        self.assertEqual(len(selected), 5)
        for handle in ("sizes", "syrup", "old", "story"):
            self.assertIn(handle, selected)

    def test_pilot_fills_in_order_and_respects_candidates(self):
        selected = state.pilot_selection(self.catalog, ["plain-1", "plain-2", "plain-3"], 5)
        self.assertEqual(selected, ["plain-1", "plain-2", "plain-3"])

    def test_worklists_pilot_uses_products_and_default_limit(self):
        lists = state.worklists(self.state, 1, None, True, products=self.catalog)
        flat = lists[0]
        self.assertEqual(len(flat), 5)
        self.assertIn("sizes", flat)
        self.assertEqual({h for h, p in self.state["products"].items() if p["pilot"]}, set(flat))

    def test_new_pilot_clears_flags_of_products_not_created(self):
        state.worklists(self.state, 1, None, True, handles=["plain-1", "plain-2"])
        self.state["products"]["plain-1"]["status"] = "created"
        state.worklists(self.state, 1, None, True, handles=["plain-3"])
        pilots = {h for h, p in self.state["products"].items() if p["pilot"]}
        self.assertEqual(pilots, {"plain-1", "plain-3"})

    def test_explicit_handles(self):
        lists = state.worklists(self.state, 2, None, False, handles=["old", "sizes", "old"])
        self.assertEqual(sorted(h for l in lists for h in l), ["old", "sizes"])

    def test_explicit_handles_errors(self):
        with self.assertRaises(ValueError):
            state.worklists(self.state, 1, None, False, handles=["nope"])
        self.state["products"]["old"]["status"] = "failed"
        with self.assertRaises(ValueError):
            state.worklists(self.state, 1, None, False, handles=["old"])
        self.assertEqual(state.worklists(self.state, 1, None, False, retry_failed=True, handles=["old"]), [["old"]])


class SharedScopeTest(unittest.TestCase):
    KEY = "metafield_definition:custom.ingredients"

    def setUp(self):
        self.state = state.init("acme", "u", products("a", "b"))
        self.definition = iri("metafield_definitions", "ingredients")
        state.record_shared(self.state, self.KEY, self.definition, "created", "metafields")
        state.record_shared(self.state, "metafield_definition:custom.volume", iri("metafield_definitions", "volume"),
                            "created", "metafields")
        for handle in ("a", "b"):
            state.merge(self.state, [{"handle": handle, "status": "created", "iri": iri("products", handle),
                                      "error": None, "items": [
                item("product", iri("products", handle)),
                dict(item("metafield", iri("metafields", handle + "i"), module="metafields"), shared=self.KEY),
                dict(item("metafield", iri("metafields", handle + "v"), module="metafields"),
                     shared="metafield_definition:custom.volume"),
            ]}])

    def test_undo_plan_shared_scope(self):
        plan = state.undo_plan(self.state, "shared:" + self.KEY)
        self.assertEqual([s["iri"] for s in plan],
                         [iri("metafields", "bi"), iri("metafields", "ai"), self.definition])

    def test_unknown_scope(self):
        for scope in ("everything", "shared:", "module:"):
            with self.assertRaises(ValueError):
                state.undo_plan(self.state, scope)

    def test_marker_scope_is_refused(self):
        with self.assertRaises(ValueError) as error:
            state.undo_plan(self.state, "shared:metafield_definition:import.key")
        self.assertIn("use all or module:core", str(error.exception))
        self.assertEqual(state.check_scope("module:core"), "module:core")

    def test_shared_key_validation(self):
        state.validate_result(line("a", items=[dict(item("metafield", iri("metafields", "x")), shared=self.KEY)]))
        with self.assertRaises(ValueError):
            state.validate_result(line("a", items=[dict(item("metafield", iri("metafields", "x")), shared="oops")]))

    def test_redo_after_shared_undo(self):
        self.assertEqual(state.worklists(self.state, 1, None, False, module="metafields"), [])
        plan = state.undo_plan(self.state, "shared:" + self.KEY)
        state.mark_deleted(self.state, [s["iri"] for s in plan])
        self.assertEqual(self.state["shared"][self.KEY]["status"], "deleted")
        self.assertEqual(self.state["products"]["a"]["status"], "created")
        flat = sorted(h for l in state.worklists(self.state, 1, None, False, module="metafields") for h in l)
        self.assertEqual(flat, ["a", "b"])
        new_definition = iri("metafield_definitions", "ingredients-2")
        state.record_shared(self.state, self.KEY, new_definition, "created", "metafields")
        self.assertEqual(self.state["shared"][self.KEY]["iri"], new_definition)
        state.merge(self.state, [{"handle": "a", "status": "created", "iri": iri("products", "a"), "error": None,
                                  "module": "metafields", "items": [
            dict(item("metafield", iri("metafields", "a2"), module="metafields"), shared=self.KEY)]}])
        flat = [h for l in state.worklists(self.state, 1, None, False, module="metafields") for h in l]
        self.assertEqual(flat, ["b"])


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
        line = {"handle": "a", "status": "created", "iri": iri("products", "a"), "items": [], "error": None}
        self.assertEqual(run_cli("result", result_file, "--json", json.dumps(line)).returncode, 0)
        merged = run_cli("merge", self.dir)
        self.assertEqual(merged.returncode, 0, merged.stderr)
        with open(os.path.join(self.dir, "state.json")) as f:
            self.assertEqual(json.load(f)["products"]["a"]["status"], "created")

    def test_result_from_file_and_stdin(self):
        result_file = os.path.join(self.dir, "results", "worker-1.jsonl")
        line_file = os.path.join(self.dir, "line.json")
        with open(line_file, "w", encoding="utf-8") as f:
            json.dump(line("a", error=None, items=[]), f)
            f.write("\n")
        self.assertEqual(run_cli("result", result_file, "--json-file", line_file).returncode, 0)
        piped = subprocess.run([sys.executable, SCRIPT, "result", result_file, "--json", "-"],
                               input=json.dumps(line("b")), capture_output=True, text=True)
        self.assertEqual(piped.returncode, 0, piped.stderr)
        with open(result_file, encoding="utf-8") as f:
            self.assertEqual([json.loads(l)["handle"] for l in f], ["a", "b"])
        both = run_cli("result", result_file, "--json", "{}", "--json-file", line_file)
        self.assertNotEqual(both.returncode, 0)

    def test_merge_writes_rejected_lines(self):
        run_cli("init", self.dir, "--client", "acme", "--mcp-url", "u")
        run_cli("worklists", self.dir, "--workers", "2")
        with open(os.path.join(self.dir, "worklists", "worker-1.json")) as f:
            mine = json.load(f)[0]
        other = "b" if mine == "a" else "a"
        result_file = os.path.join(self.dir, "results", "worker-1.jsonl")
        run_cli("result", result_file, "--json", json.dumps(line(mine)))
        run_cli("result", result_file, "--json", json.dumps(line(other)))
        merged = run_cli("merge", self.dir)
        self.assertEqual(merged.returncode, 0, merged.stderr)
        self.assertIn("rejected", merged.stdout)
        rejected = glob.glob(os.path.join(self.dir, "results", "rejected-*.jsonl"))
        self.assertEqual(len(rejected), 1)
        with open(rejected[0], encoding="utf-8") as f:
            self.assertEqual(json.loads(f.readline())["result"]["handle"], other)
        with open(os.path.join(self.dir, "state.json")) as f:
            products_state = json.load(f)["products"]
        self.assertEqual(products_state[mine]["status"], "created")
        self.assertEqual(products_state[other]["status"], "pending")
        self.assertIn("0 result lines merged", run_cli("merge", self.dir).stdout)

    def test_worklists_handles_error_keeps_worklists(self):
        run_cli("init", self.dir, "--client", "acme", "--mcp-url", "u")
        run_cli("worklists", self.dir, "--workers", "1")
        bad = run_cli("worklists", self.dir, "--handles", "a,zz", "--pilot")
        self.assertEqual(bad.returncode, 1)
        self.assertIn("zz", bad.stderr)
        self.assertTrue(os.path.exists(os.path.join(self.dir, "worklists", "worker-1.json")))
        good = run_cli("worklists", self.dir, "--handles", "b", "--pilot")
        self.assertEqual(good.returncode, 0, good.stderr)
        with open(os.path.join(self.dir, "worklists", "worker-1.json")) as f:
            self.assertEqual(json.load(f), ["b"])

    def test_shared_file_and_decide_value_file(self):
        run_cli("init", self.dir, "--client", "acme", "--mcp-url", "u")
        shared_file = os.path.join(self.dir, "shared.json")
        entries = [{"key": "category:Drinks", "iri": iri("categories", "d"), "status": "created", "module": "taxonomy"},
                   {"key": "supplier:Acme", "iri": iri("suppliers", "a"), "status": "reused", "module": "costs"}]
        with open(shared_file, "w") as f:
            json.dump(entries, f)
        self.assertEqual(run_cli("shared", self.dir, "--file", shared_file).returncode, 0)
        with open(shared_file, "w") as f:
            json.dump([{"key": "category:Bad", "iri": iri("categories", "b"), "status": "deleted", "module": "x"}], f)
        self.assertEqual(run_cli("shared", self.dir, "--file", shared_file).returncode, 1)
        self.assertEqual(run_cli("shared", self.dir, "--key", "category:X").returncode, 1)
        value_file = os.path.join(self.dir, "taxonomy.json")
        with open(value_file, "w") as f:
            json.dump({"categories": [], "products": {}}, f)
        self.assertEqual(run_cli("decide", self.dir, "--key", "taxonomy", "--value-file", value_file, "--origin", "developer").returncode, 0)
        with open(os.path.join(self.dir, "state.json")) as f:
            saved = json.load(f)
        self.assertEqual(sorted(saved["shared"]), ["category:Drinks", "supplier:Acme"])
        self.assertEqual(saved["decisions"]["taxonomy"], {"categories": [], "products": {}})

    def test_result_rejects_missing_keys(self):
        result_file = os.path.join(self.dir, "results", "worker-1.jsonl")
        self.assertNotEqual(run_cli("result", result_file, "--json", '{"handle": "a"}').returncode, 0)

    def test_merge_does_not_replay_merged_results(self):
        run_cli("init", self.dir, "--client", "acme", "--mcp-url", "u")
        result_file = os.path.join(self.dir, "results", "worker-1.jsonl")
        line = {"handle": "a", "status": "created", "iri": iri("products", "a"), "items": [item("product", iri("products", "a"))], "error": None}
        run_cli("result", result_file, "--json", json.dumps(line))
        run_cli("merge", self.dir)
        iris_file = os.path.join(self.dir, "deleted.json")
        with open(iris_file, "w") as f:
            json.dump([iri("products", "a")], f)
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
            f.write(json.dumps({"handle": "b", "status": "created", "iri": iri("products", "b"), "items": [], "error": None}) + "\n")
        merged = run_cli("merge", self.dir)
        self.assertEqual(merged.returncode, 0)
        self.assertIn("malformed", merged.stdout + merged.stderr)
        with open(os.path.join(self.dir, "state.json")) as f:
            self.assertEqual(json.load(f)["products"]["b"]["status"], "created")


if __name__ == "__main__":
    unittest.main()


class OriginTest(unittest.TestCase):
    def test_decide_records_origin(self):
        st = state.init("acme", "u", products("a"))
        state.decide(st, "status_mode", "draft", "developer")
        state.decide(st, "codes", {"prefix": "2"}, "agent", reason="Aucun EAN fourni pour 2 produits.")
        self.assertEqual(st["source"], "files")
        self.assertEqual(st["decisions"]["status_mode"], "draft")
        self.assertEqual(st["decision_meta"]["codes"]["origin"], "agent")
        self.assertEqual(st["decision_meta"]["codes"]["phase"], 1)
        self.assertRegex(st["decision_meta"]["codes"]["at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertEqual(state.summary(st)["decisions"], {"developer": 1, "agent": 1, "client": 0})
        with self.assertRaises(ValueError):
            state.decide(st, "x", 1, "guess")
        self.assertNotIn("x", st["decisions"])

    def test_cli_requires_origin(self):
        with tempfile.TemporaryDirectory() as directory:
            with open(os.path.join(directory, "products.json"), "w") as f:
                json.dump(products("a", "b"), f)
            self.assertEqual(run_cli("init", directory, "--client", "acme", "--mcp-url", "u").returncode, 0)
            self.assertNotEqual(run_cli("decide", directory, "--key", "k", "--value-json", "1").returncode, 0)
            self.assertNotEqual(
                run_cli("decide", directory, "--key", "k", "--value-json", "1", "--origin", "guess").returncode, 0)
            done = run_cli("decide", directory, "--key", "k", "--value-json", "1", "--origin", "client",
                           "--reason", "Choix du client.")
            self.assertEqual(done.returncode, 0, done.stderr)
            with open(os.path.join(directory, "state.json")) as f:
                saved = json.load(f)
            self.assertEqual(saved["decisions"]["k"], 1)
            self.assertEqual(saved["decision_meta"]["k"]["origin"], "client")
            self.assertEqual(saved["decision_meta"]["k"]["reason"], "Choix du client.")

    def test_cli_shared_spec(self):
        with tempfile.TemporaryDirectory() as directory:
            with open(os.path.join(directory, "products.json"), "w") as f:
                json.dump(products("a"), f)
            run_cli("init", directory, "--client", "acme", "--mcp-url", "u")
            target = iri("metafield_definitions", "m")
            done = run_cli("shared", directory, "--key", "metafield_definition:import.key", "--iri", target,
                           "--status", "created", "--module", "core", "--spec-json", '{"type": "number_integer"}')
            self.assertEqual(done.returncode, 0, done.stderr)
            bad = run_cli("shared", directory, "--key", "metafield_definition:import.other", "--iri", target,
                          "--status", "created", "--module", "core", "--spec-json", "[1]")
            self.assertEqual(bad.returncode, 1)
            with open(os.path.join(directory, "state.json")) as f:
                saved = json.load(f)
            self.assertEqual(saved["shared"]["metafield_definition:import.key"]["spec"], {"type": "number_integer"})
            self.assertNotIn("metafield_definition:import.other", saved["shared"])

    def test_shared_spec_and_item_ref(self):
        st = state.init("acme", "u", products("a"))
        state.record_shared_entries(st, [{"key": "metafield_definition:import.key",
                                          "iri": iri("metafield_definitions", "m"), "status": "created",
                                          "module": "core", "spec": {"type": "single_line_text_field"}}])
        self.assertEqual(st["shared"]["metafield_definition:import.key"]["spec"], {"type": "single_line_text_field"})
        state.record_shared(st, "supplier:Acme", iri("suppliers", "a"), "created", "costs")
        self.assertNotIn("spec", st["shared"]["supplier:Acme"])
        with self.assertRaises(ValueError):
            state.record_shared_entries(st, [{"key": "supplier:B", "iri": iri("suppliers", "b"), "status": "created",
                                              "module": "costs", "spec": "text"}])
        line = {"handle": "a", "status": "created", "iri": iri("products", "a"), "error": None,
                "items": [dict(item("variant", iri("products", "a") + "/variants/" + str(uuid.uuid4())), ref="1001")]}
        state.validate_result(line)
        line["items"][0]["ref"] = ""
        with self.assertRaises(ValueError):
            state.validate_result(line)

    def test_pilot_covers_generated_description(self):
        prods = [{"handle": "a", "variants": [{}], "description_origin": "source"},
                 {"handle": "b", "variants": [{}], "description_origin": "generated"}]
        self.assertEqual(state.pilot_selection(prods, ["a", "b"], 1), ["b"])
