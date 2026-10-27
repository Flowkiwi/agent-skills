import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import diff  # noqa: E402
import state  # noqa: E402
from phase_helpers import (MARKER_IRI, created_line, make_products, make_state, read_json, run_cli,  # noqa: E402
                           write_json, write_workspace)


def run(script, *args):
    return run_cli(*args, script=script)


def remote_of(written):
    """A Flowkiwi read-back of what was written: IRIs as objects, tags reordered, description with \\r\\n."""
    body = dict(written["product"]["body"])
    body["status"] = {"@id": body["status"]}
    body["tags"] = ",".join(reversed(body.get("tags", "").split(",")))
    body["description"] = body.get("description", "").replace("\n", "\r\n") + "\n"
    body["category"] = None
    body["collections"] = []
    variants = [{"@id": "/api/variants/x", "sku": v["sku"], "barcode": v["barcode"]} for v in written["variants"]]
    marker = written["marker"]["body"]
    metafields = [{"definition": {"@id": marker["definition"]}, "value": marker["value"]}]
    return {"product": body, "variants": variants, "metafields": metafields}


class ConflictsTest(unittest.TestCase):
    def snapshot(self, **product):
        base = {"title": "Grille-pain", "description": "<p>A</p>\r\n<p>B</p>\n", "tags": "b,a",
                "status": {"@id": "/api/statuses/DRAFT"}, "category": None}
        base.update(product)
        return {"product": base, "variants": [{"sku": "1001", "barcode": "3601234560012"}], "metafields": []}

    def written(self):
        return {"product": {"body": {"title": "Grille-pain", "description": "<p>A</p>\n<p>B</p>", "tags": "a,b",
                                     "status": "/api/statuses/DRAFT"}},
                "variants": [{"sku": "1001", "barcode": "3601234560012", "option_values": []}], "metafields": []}

    def test_own_writes_are_not_conflicts(self):
        self.assertEqual(diff.conflicts(diff.written_view(self.written()), diff.remote_view(self.snapshot())), {})

    def test_edit_in_flowkiwi_is_a_conflict(self):
        found = diff.conflicts(diff.written_view(self.written()), diff.remote_view(self.snapshot(title="Grille-pain promo")))
        self.assertEqual(found, {"title": {"written": "Grille-pain", "flowkiwi": "Grille-pain promo"}})

    def test_barcode_edit(self):
        snap = self.snapshot(); snap["variants"][0]["barcode"] = "3601234569999"
        self.assertIn("variants.1001", diff.conflicts(diff.written_view(self.written()), diff.remote_view(snap)))

    def test_forced_is_phase_scoped(self):
        st = {"phase": 2, "decisions": {"force_update": {"phase": 1, "handles": "all"}}}
        self.assertFalse(diff.forced(st, "2226"))
        st["decisions"]["force_update"] = {"phase": 2, "handles": ["2226"]}
        self.assertTrue(diff.forced(st, "2226"))

    def test_absent_fields_in_snapshot_are_not_compared(self):
        snap = {"product": {"title": "Grille-pain"}}
        self.assertEqual(diff.conflicts(diff.written_view(self.written()), diff.remote_view(snap)), {})
        self.assertNotIn("description", diff.remote_view(snap))
        self.assertNotIn("variants", diff.remote_view(snap))

    def test_iris_as_objects_and_sorted_lists(self):
        written = self.written()
        written["product"]["body"].update({
            "category": "/api/categories/c1",
            "attributes": [{"definition": "/api/attribute_definitions/d2", "value": "Inox"},
                           {"definition": "/api/attribute_definitions/d1", "option": "/api/attribute_options/o1"}],
            "collections": ["/api/collections/b", "/api/collections/a"]})
        snap = self.snapshot(
            category={"@id": "/api/categories/c1", "name": "Cuisine"},
            attributes=[{"@id": "/api/product_attributes/1", "definition": {"@id": "/api/attribute_definitions/d1"},
                         "option": {"@id": "/api/attribute_options/o1"}},
                        {"definition": "/api/attribute_definitions/d2", "value": "Inox"}],
            collections=[{"@id": "/api/collections/a"}, {"@id": "/api/collections/b"}])
        self.assertEqual(diff.conflicts(diff.written_view(written), diff.remote_view(snap)), {})

    def test_category_set_in_flowkiwi_is_a_conflict(self):
        found = diff.conflicts(diff.written_view(self.written()),
                               diff.remote_view(self.snapshot(category={"@id": "/api/categories/c9"})))
        self.assertEqual(found, {"category": {"written": None, "flowkiwi": "/api/categories/c9"}})

    def test_metafield_values_compare_as_json(self):
        written = self.written()
        written["metafields"] = [
            {"key": "custom.weight", "body": {"definition": "/api/metafield_definitions/w", "value": 1.8}},
            {"key": "custom.materials", "body": {"definition": "/api/metafield_definitions/m",
                                                 "value": '["Inox", "Bois"]'}},
            {"key": "custom.size", "body": {"definition": "/api/metafield_definitions/s", "value": {"b": 1, "a": 2}}},
        ]
        snap = self.snapshot()
        snap["metafields"] = [
            {"@id": "/api/metafields/1", "definition": {"@id": "/api/metafield_definitions/w"}, "value": "1.80"},
            {"definition": "/api/metafield_definitions/m", "value": ["Inox", "Bois"]},
            {"definition": "/api/metafield_definitions/s", "value": '{"a": 2, "b": 1}'},
        ]
        self.assertEqual(diff.conflicts(diff.written_view(written), diff.remote_view(snap)), {})
        snap["metafields"][0]["value"] = 2
        found = diff.conflicts(diff.written_view(written), diff.remote_view(snap))
        self.assertEqual(found, {"metafields./api/metafield_definitions/w": {"written": "1.8", "flowkiwi": "2"}})

    def test_reference_metafield_value_as_object(self):
        written = self.written()
        written["metafields"] = [{"key": "custom.brand", "body": {"definition": "/api/metafield_definitions/b",
                                                                  "value": "/api/metaobjects/e1"}}]
        snap = self.snapshot()
        snap["metafields"] = [{"definition": {"@id": "/api/metafield_definitions/b"},
                               "value": {"@id": "/api/metaobjects/e1", "handle": "brioche-co"}}]
        self.assertEqual(diff.conflicts(diff.written_view(written), diff.remote_view(snap)), {})

    def test_variant_or_metafield_gone_or_added_in_flowkiwi(self):
        snap = self.snapshot()
        snap["variants"] = [{"sku": "1001-X", "barcode": "3601234560999"}]
        snap["metafields"] = [{"definition": "/api/metafield_definitions/n", "value": "Note"}]
        found = diff.conflicts(diff.written_view(self.written()), diff.remote_view(snap))
        self.assertEqual(found, {
            "variants.1001": {"written": "3601234560012", "flowkiwi": None},
            "variants.1001-X": {"written": None, "flowkiwi": "3601234560999"},
            "metafields./api/metafield_definitions/n": {"written": None, "flowkiwi": '"Note"'},
        })

    def test_marker_is_part_of_what_was_written(self):
        written = self.written()
        written["marker"] = {"shared": "metafield_definition:import.key",
                             "body": {"definition": MARKER_IRI, "value": "2226"}}
        snap = self.snapshot()
        snap["metafields"] = [{"definition": {"@id": MARKER_IRI}, "value": "2226"}]
        self.assertEqual(diff.conflicts(diff.written_view(written), diff.remote_view(snap)), {})


class ConflictsCliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.addCleanup(self.tmp.cleanup)
        self.products = make_products()
        self.st = make_state(self.products)
        state.merge(self.st, [created_line("2226"), created_line("2230")], self.products)
        self.written = {h: self.st["products"][h]["written"]["payload"] for h in ("2226", "2230")}
        self.products["products"][0]["title"] = "Grille-pain Toastine"
        self.products["products"][1]["title"] = "Grille-pain Duo Toastine"
        write_workspace(self.dir, self.products, self.st)
        self.diff_path = os.path.join(self.dir, "diff.json")
        result = run("diff.py", self.dir)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sorted(read_json(self.diff_path)["changed"]), ["2226", "2230"])
        write_json(os.path.join(self.dir, "tmp", "remote", "2226.json"), remote_of(self.written["2226"]))

    def test_cli_marks_missing_snapshots(self):
        run("diff.py", "conflicts", self.dir)
        with open(self.diff_path, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["missing_snapshots"], ["2230"])

    def test_cli_fills_conflicts_and_prints_them(self):
        snap = remote_of(self.written["2226"])
        snap["product"]["title"] = "Grille-pain promo"
        write_json(os.path.join(self.dir, "tmp", "remote", "2226.json"), snap)
        result = run("diff.py", "conflicts", self.dir)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = read_json(self.diff_path)
        self.assertEqual(data["conflicts"],
                         {"2226": {"title": {"written": "Grille-pain", "flowkiwi": "Grille-pain promo"}}})
        self.assertEqual(list(data["changed"]), ["2226", "2230"])
        self.assertIn("2226: title", result.stdout)
        self.assertIn("missing snapshots: 2230", result.stdout)

    def test_cli_own_writes_read_back_are_not_conflicts(self):
        write_json(os.path.join(self.dir, "tmp", "remote", "2230.json"), remote_of(self.written["2230"]))
        result = run("diff.py", "conflicts", self.dir)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = read_json(self.diff_path)
        self.assertEqual((data["conflicts"], data["missing_snapshots"]), ({}, []))

    def test_cli_covers_removed_handles(self):
        self.products["products"] = self.products["products"][:1]
        write_workspace(self.dir, self.products, self.st)
        run("diff.py", self.dir)
        snap = remote_of(self.written["2230"])
        snap["variants"][0]["barcode"] = "3601234569999"
        write_json(os.path.join(self.dir, "tmp", "remote", "2230.json"), snap)
        run("diff.py", "conflicts", self.dir)
        self.assertEqual(list(read_json(self.diff_path)["conflicts"]), ["2230"])

    def test_cli_shows_forced_handles(self):
        snap = remote_of(self.written["2226"])
        snap["product"]["title"] = "Grille-pain promo"
        write_json(os.path.join(self.dir, "tmp", "remote", "2226.json"), snap)
        state.decide(self.st, "force_update", {"phase": 1, "handles": ["2226"]}, "developer")
        write_workspace(self.dir, self.products, self.st)
        result = run("diff.py", "conflicts", self.dir)
        self.assertIn("2226 (forced): title", result.stdout)

    def test_cli_rejects_a_diff_of_another_phase(self):
        state.start_phase(self.st)
        write_workspace(self.dir, self.products, self.st)
        result = run("diff.py", "conflicts", self.dir)
        self.assertEqual(result.returncode, 1)
        self.assertIn("diff.json is from phase 1, run diff.py again", result.stderr)

    def test_cli_invalid_snapshot(self):
        with open(os.path.join(self.dir, "tmp", "remote", "2226.json"), "w", encoding="utf-8") as f:
            f.write("{not json")
        result = run("diff.py", "conflicts", self.dir)
        self.assertEqual(result.returncode, 1)
        self.assertIn("2226.json", result.stderr)

    def test_cli_usage(self):
        self.assertEqual(run("diff.py", "conflicts").returncode, 2)


if __name__ == "__main__":
    unittest.main()
