import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import dryrun  # noqa: E402
import plan_shared  # noqa: E402
import state  # noqa: E402
from concept_helpers import fake_iri, read_json, run, write_workspace  # noqa: E402
from demo_files import DEMO_CODES, DEMO_LABELS, DEMO_ROWS, write_xlsx  # noqa: E402
from phase_helpers import created_line, write_json  # noqa: E402

CONCEPTS = ("taxonomy", "metafields", "metaobjects", "prices")
TITLE_2226 = "Grille-pain 2 fentes Inox 1400W | TOASTINE"


def record_all_shared(st, columns):
    """Every plan_shared entry recorded created with its spec, dependencies first."""
    entries = plan_shared.plan(st, columns)
    while entries:
        for entry in [e for e in entries if not e["depends_on"]]:
            state.record_shared(st, entry["key"], fake_iri(entry["key"]), "created", entry["module"], entry["spec"])
        entries = plan_shared.plan(st, columns)


def remote_snapshot(written):
    """A Flowkiwi read-back of what was written: status and IRIs as objects, tags reordered."""
    body = dict(written["product"]["body"])
    body["status"] = {"@id": body["status"]}
    body["tags"] = ",".join(reversed((body.get("tags") or "").split(",")))
    variants = [{"@id": "/api/variants/x", "sku": v["sku"], "barcode": v["barcode"]} for v in written["variants"]]
    entries = ([written["marker"]] if written.get("marker") else []) + (written.get("metafields") or [])
    metafields = [{"definition": {"@id": e["body"]["definition"]}, "value": e["body"]["value"]} for e in entries]
    return {"product": body, "variants": variants, "metafields": metafields}


class DryRunPhasesTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        attachments = tempfile.TemporaryDirectory()
        self.addCleanup(attachments.cleanup)
        self.attachments = attachments.name
        write_workspace(self.dir, CONCEPTS)
        run("extract.py", self.dir, "all")
        run("codes.py", "fill", self.path("products.json"), "--prefix", "2", "--start", "1",
            "--sku-pattern", "{handle}-{index}")
        run("extract.py", self.dir, "prices")
        run("state.py", "init", self.dir, "--client", "demo", "--mcp-url", "https://demo.invalid/api/mcp")
        self.decide("data_language", "fr")
        self.decide("status_mode", "draft")
        self.record_concepts()
        products = read_json(self.path("products.json"))
        st = read_json(self.path("state.json"))
        record_all_shared(st, products.get("metafield_columns"))
        state.merge(st, [created_line(p["handle"], products) for p in products["products"]], products)
        state.start_phase(st)
        write_json(self.path("state.json"), st)

    def path(self, *parts):
        return os.path.join(self.dir, *parts)

    def decide(self, key, value):
        run("state.py", "decide", self.dir, "--key", key, "--value-json", json.dumps(value), "--origin", "developer")

    def record_concepts(self):
        """Every concept file recorded again after an extraction, with the origin it had (P3-R3)."""
        st = read_json(self.path("state.json"))
        for concept in CONCEPTS:
            origin = ((st.get("decision_meta") or {}).get(concept) or {}).get("origin") or "developer"
            run("state.py", "decide", self.dir, "--key", concept, "--value-file",
                self.path("concepts", concept + ".json"), "--origin", origin)

    def change_title_in_mapping(self, handle):
        self.assertEqual(handle, "2226")
        path = self.path("mappings", "products.json")
        m = read_json(path)
        m["product"]["title"]["map"] = {TITLE_2226: "Grille-pain 2 fentes Inox 1400W"}
        write_json(path, m)

    def remove_rows_of(self, handle):
        """A new delivery of the catalogue without the rows of the handle (column B, the commercial reference)."""
        rows = [row for row in DEMO_ROWS if row[1] != handle]
        new_file = os.path.join(self.attachments, "demo-catalogue.xlsx")
        write_xlsx(new_file, [
            {"name": "Catalogue", "rows": [DEMO_LABELS, DEMO_CODES] + rows, "merged": [], "hidden": False,
             "no_refs_rows": []},
            {"name": "Notes", "rows": [["Export du 12/03 - demo"]], "merged": ["A1:C1"], "hidden": False,
             "no_refs_rows": []},
        ])
        run("intake.py", "add", self.dir, new_file, "--replace")
        run("inventory.py", self.dir)

    def write_snapshots_matching_written(self, handles):
        st = read_json(self.path("state.json"))
        for handle in handles:
            written = st["products"][handle]["written"]["payload"]
            write_json(self.path("tmp", "remote", handle + ".json"), remote_snapshot(written))

    def regenerate(self):
        run("extract.py", self.dir, "all"); self.record_concepts(); run("state.py", "sync", self.dir)
        run("diff.py", self.dir)

    def test_blocked_until_decided(self):
        self.change_title_in_mapping("2226")
        self.remove_rows_of("2227")
        run("extract.py", self.dir, "all"); self.record_concepts(); run("state.py", "sync", self.dir); run("diff.py", self.dir)
        text, code = dryrun.report(self.dir)
        self.assertEqual(code, 1)
        self.assertIn("removed: 1 (action: not decided)", text)
        self.assertIn("snapshots missing: 2226, 2227", text)

    def test_ready_after_decisions(self):
        self.change_title_in_mapping("2226")
        self.remove_rows_of("2227")
        run("extract.py", self.dir, "all"); self.record_concepts(); run("state.py", "sync", self.dir); run("diff.py", self.dir)
        self.write_snapshots_matching_written(["2226", "2227"])
        run("diff.py", "conflicts", self.dir)
        self.decide("removed_action", {"phase": 2, "action": "archive"})
        text, code = dryrun.report(self.dir)
        self.assertEqual(code, 0, text)
        self.assertIn("    2226: product.title", text)
        self.assertIn("modified in Flowkiwi: 0 (forced: 0)", text)
        self.assertIn("shared changes: 0 destructive (confirmed 0), 0 updates", text)   # 2227 shares brand, category and options

    def test_changes_section_and_removed_flow(self):
        self.remove_rows_of("2227")
        self.regenerate()
        text, code = dryrun.report(self.dir)
        changes = text[text.index("Changes (phase 2):"):text.index("Concepts:")]
        for line in ("  new: 0", "  changed: 0", "  removed: 1 (action: not decided)", "  rekeyed: 0 (not confirmed)",
                     "  unmanaged: 0"):
            self.assertIn(line + "\n", changes)
        self.assertIn("removed products without decision removed_action", text)
        self.assertNotIn("identity changed or product removed", text)   # a later phase: the removed flow

    def test_removed_product_needs_sync(self):
        self.remove_rows_of("2227")
        run("extract.py", self.dir, "all"); self.record_concepts()
        text, code = dryrun.report(self.dir)
        self.assertEqual(code, 1)
        self.assertIn("products.json and state.json differ, run state.py sync: 2227", text)

    def test_missing_and_stale_diff(self):
        text, code = dryrun.report(self.dir)
        self.assertEqual(code, 1)
        self.assertIn("diff.json is missing", text)
        run("diff.py", self.dir)
        diff_data = read_json(self.path("diff.json"))
        diff_data["phase"] = 1
        write_json(self.path("diff.json"), diff_data)
        self.assertIn("diff.json is from phase 1", dryrun.report(self.dir)[0])
        run("diff.py", self.dir)
        self.assertEqual(dryrun.report(self.dir)[1], 0)
        self.change_title_in_mapping("2226")
        run("extract.py", self.dir, "all"); self.record_concepts()
        text, code = dryrun.report(self.dir)
        self.assertEqual(code, 1)
        self.assertIn("diff.json was computed from another products.json or other decisions", text)

    def test_conflicts_older_than_a_snapshot(self):
        self.change_title_in_mapping("2226")
        self.regenerate()
        self.write_snapshots_matching_written(["2226"])
        text, code = dryrun.report(self.dir)
        self.assertEqual(code, 1)
        self.assertIn("conflicts not computed from the current snapshots: 2226", text)
        run("diff.py", "conflicts", self.dir)
        self.assertEqual(dryrun.report(self.dir)[1], 0)
        snapshot = self.path("tmp", "remote", "2226.json")
        stat = os.stat(snapshot)
        os.utime(snapshot, (stat.st_atime, stat.st_mtime + 5))
        self.assertIn("conflicts not computed from the current snapshots: 2226", dryrun.report(self.dir)[0])

    def test_modified_in_flowkiwi_and_forced(self):
        self.change_title_in_mapping("2226")
        self.regenerate()
        self.write_snapshots_matching_written(["2226"])
        path = self.path("tmp", "remote", "2226.json")
        snapshot = read_json(path)
        snapshot["product"]["title"] = "Grille-pain promo"
        write_json(path, snapshot)
        run("diff.py", "conflicts", self.dir)
        text, code = dryrun.report(self.dir)
        self.assertEqual(code, 0, text)
        self.assertIn("  modified in Flowkiwi: 1 (forced: 0)\n    2226: title", text)
        self.decide("force_update", {"phase": 2, "handles": ["2226"]})
        self.assertIn("  modified in Flowkiwi: 1 (forced: 1)\n    2226 (forced): title", dryrun.report(self.dir)[0])

    def test_rekey_needs_confirmation(self):
        path = self.path("mappings", "products.json")
        m = read_json(path)
        m["group"]["key"]["from"] = ["main:sku_marchand", "main:ref_com"]    # one product per SKU: 2230 -> 1005, 1006
        write_json(path, m)
        self.regenerate()
        diff_data = read_json(self.path("diff.json"))
        self.assertEqual(diff_data["removal"].get("2230"), "delete_rekeyed")
        st = read_json(self.path("state.json"))
        for handle in list(diff_data["changed"]) + diff_data["removed"]:
            write_json(self.path("tmp", "remote", handle + ".json"),
                       remote_snapshot(st["products"][handle]["written"]["payload"]))
        run("diff.py", "conflicts", self.dir)
        if any(h for h in diff_data["removed"] if diff_data["removal"][h] == "removed_action"):
            self.decide("removed_action", {"phase": 2, "action": "archive"})
        text, code = dryrun.report(self.dir)
        self.assertEqual(code, 1)
        self.assertRegex(text, r"rekeyed: \d+ \(not confirmed\)")
        self.assertIn('rekeyed products not confirmed: decision confirmed_rekey {"phase": 2}', text)
        self.decide("confirmed_rekey", {"phase": 2})
        text, code = dryrun.report(self.dir)
        self.assertRegex(text, r"rekeyed: \d+ \(confirmed\)")
        self.assertNotIn("rekeyed products not confirmed", text)

    def test_destructive_shared_change_needs_confirmation(self):
        path = self.path("mappings", "metafields.json")
        m = read_json(path)
        m["definitions"]["custom.poids_net"]["type"] = "single_line_text_field"
        write_json(path, m)
        self.regenerate()
        text, code = dryrun.report(self.dir)
        self.assertEqual(code, 1)
        self.assertIn("shared changes: 1 destructive (confirmed 0), 0 updates", text)
        self.assertIn("destructive shared changes not confirmed: metafield_definition:custom.poids_net", text)
        self.decide("confirmed_changes", {"phase": 2, "keys": ["metafield_definition:custom.poids_net"]})
        text, _ = dryrun.report(self.dir)
        self.assertIn("shared changes: 1 destructive (confirmed 1), 0 updates", text)
        self.assertNotIn("destructive shared changes not confirmed", text)

    def test_first_phase_has_no_changes_section(self):
        st = read_json(self.path("state.json"))
        st["phase"] = 1
        write_json(self.path("state.json"), st)
        self.assertNotIn("Changes (phase", dryrun.report(self.dir)[0])


class PhaseLinesTest(unittest.TestCase):
    """Changes and blocking lines of a later phase from a given diff.json."""

    def setUp(self):
        self.st = state.init("demo", "https://demo.invalid/api/mcp", {"products": [{"handle": "2226"},
                                                                                {"handle": "2230"}]})
        state.start_phase(self.st)

    def test_blocked_move_is_listed(self):
        diff_data = {"phase": 2, "changed": {"2230": {"variants": {"removed": ["1006"]}}}, "unmanaged": ["2226"],
                     "blocked_moves": [{"sku": "1006", "from": "2230", "to": ["2226"],
                                        "reason": "moved to a product this migration cannot update: 2226 (unmanaged)"}]}
        self.assertIn("    blocked move SKU 1006: 2230 -> 2226: moved to a product this migration cannot update: "
                      "2226 (unmanaged) (kept on 2230, never deleted)", dryrun.changes_section(diff_data, self.st))


if __name__ == "__main__":
    unittest.main()
