import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import prompts  # noqa: E402
from phase_helpers import (created_line, make_products, make_state, read_json, run_cli,  # noqa: E402
                           write_workspace)

SKILL_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PREFIX = "mcp__claude_ai_Flowkiwi_-_UAT-1__"


class PromptsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = os.path.realpath(tmp.name)
        self.state = make_state(make_products())
        self.state["tools_prefix"] = PREFIX
        write_workspace(self.dir, make_products(), self.state)
        result = run_cli("worklists", self.dir, "--workers", "2")
        self.assertEqual(result.returncode, 0, result.stderr)

    def edit_state(self, change):
        path = os.path.join(self.dir, "state.json")
        st = read_json(path)
        change(st)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(st, f)

    def set_status(self, status, handles=None):
        def change(st):
            for handle, product in st["products"].items():
                if handles is None or handle in handles:
                    product["status"] = status
        self.edit_state(change)

    def fails(self, *args):
        result = run_cli(self.dir, *args, script="prompts.py")
        self.assertEqual(result.returncode, 1, result.stdout)
        return result.stderr

    def generate(self, *args):
        result = run_cli(self.dir, *args, script="prompts.py")
        self.assertEqual(result.returncode, 0, result.stderr)
        path = result.stdout.strip()
        with open(path, encoding="utf-8") as f:
            return path, f.read()

    def assert_common(self, text):
        self.assertNotIn("{{", text)
        self.assertNotIn("$SKILL", text)
        self.assertNotIn("$S/", text)
        self.assertIn("MCP rules:\n- Server:", text)
        self.assertIn("- Tools:", text)
        self.assertIn("use only the tools whose name starts with `%s` (server https://mcp.example.com)" % PREFIX, text)
        self.assertIn("If no such tool is available, or tools of more than one server start with this prefix, stop and report it", text)
        self.assertIn("- Schema refusal:", text)
        self.assertIn(os.path.join(SKILL_DIR, "scripts"), text)

    def assert_product_worker(self, text, n):
        self.assert_common(text)
        self.assertIn("Workspace: %s " % self.dir, text)
        self.assertIn(os.path.join(self.dir, "worklists", "worker-%d.json" % n), text)
        self.assertIn(os.path.join(self.dir, "results", "worker-%d.jsonl" % n), text)
        self.assertIn(os.path.join(self.dir, "tmp", "worker-%d" % n) + "/line.json", text)
        self.assertIn(os.path.join(self.dir, "products.json"), text)
        self.assertIn(os.path.join(self.dir, "state.json"), text)
        self.assertIn(self.state["started_at"], text)
        self.assertIn("**Created vs reused.**", text)

    def test_worker(self):
        path, text = self.generate("worker", "--worker", "2")
        self.assertEqual(path, os.path.join(self.dir, "tmp", "prompts", "worker-2.md"))
        self.assert_product_worker(text, 2)
        self.assertIn("## Map\n", text)
        self.assertIn("| `products.json`", text)
        self.assertIn("| `handle`                   | marker metafield `import.key` on the product", text)
        self.assertIn("## Per product\n", text)
        self.assertIn("13. **Result**", text)
        self.assertIn("python3 %s/scripts/payload.py %s <handle>" % (SKILL_DIR, self.dir), text)
        self.assertLess(text.index("## Map"), text.index("## Per product"))
        self.assertNotIn("Module-only run", text)

    def test_worker_paths_are_the_ones_state_py_reads(self):
        _, text = self.generate("worker", "--worker", "1")
        worklist = os.path.join(self.dir, "worklists", "worker-1.json")
        handles = read_json(worklist)
        self.assertTrue(handles)
        result_path = os.path.join(self.dir, "results", "worker-1.jsonl")
        line = json.dumps(created_line(handles[0]))
        self.assertEqual(run_cli("result", result_path, "--json", line).returncode, 0)
        merged = run_cli("merge", self.dir)
        self.assertEqual(merged.returncode, 0, merged.stderr)
        self.assertIn("1 result lines merged", merged.stdout)
        self.assertIn(result_path, text)

    def test_module_only(self):
        self.set_status("created")
        path, text = self.generate("worker", "--worker", "1", "--module", "taxonomy")
        self.assertEqual(path, os.path.join(self.dir, "tmp", "prompts", "worker-1-module-taxonomy.md"))
        self.assert_product_worker(text, 1)
        self.assertIn("Module-only run for the concept taxonomy:", text)
        self.assertIn('"module": "taxonomy"', text)
        self.assertNotIn("<concept>", text)
        self.assertIn("payload.py %s <handle> --step taxonomy" % self.dir, text)
        self.assertLess(text.index("Module-only run"), text.index("**Created vs reused.**"))
        self.assertLess(text.index("**Created vs reused.**"), text.rindex("--step taxonomy"))
        self.assertNotIn("13. **Result**", text)

    def test_module_only_metaobjects(self):
        self.set_status("created")
        _, text = self.generate("worker", "--worker", "1", "--module", "metaobjects")
        self.assertIn("Module-only run for the concept metaobjects:", text)
        self.assertIn("payload.py %s <handle> --step metafields, never without --step" % self.dir, text)
        self.assertNotIn("<step>", text)

    def test_module_only_overrides_the_base_payload_rules(self):
        self.set_status("created")
        for module in prompts.MODULES:
            _, text = self.generate("worker", "--worker", "1", "--module", module)
            step = "metafields" if module == "metaobjects" else module
            self.assertIn("It replaces two rules above. The payload command: build every MCP argument from "
                          "python3 %s/scripts/payload.py %s <handle> --step %s, never without --step."
                          % (SKILL_DIR, self.dir, step), text)
            self.assertIn("Never modify or delete: on the products of your worklist (created by this migration), "
                          "you only write the fields and items that payload prints", text)

    def test_module_only_never_rebuilds_worklists(self):
        self.set_status("created")
        for module in prompts.MODULES:
            _, text = self.generate("worker", "--worker", "1", "--module", module)
            self.assertNotIn("state.py worklists", text, module)

    def test_update(self):
        self.set_status("created")
        path, text = self.generate("worker", "--worker", "1", "--update")
        self.assertEqual(path, os.path.join(self.dir, "tmp", "prompts", "worker-1-update.md"))
        self.assert_product_worker(text, 1)
        self.assertIn("Update run (later phase):", text)
        self.assertIn("payload.py %s <handle> --update" % self.dir, text)
        self.assertIn("**Plan.**", text)
        self.assertIn('**`"action": "delete"`**', text)
        self.assertLess(text.index("Update run"), text.index("**Created vs reused.**"))
        self.assertLess(text.index("**Created vs reused.**"), text.index("**Plan.**"))
        self.assertNotIn("**Worklists.**", text)
        self.assertNotIn("state.py worklists", text)
        self.assertIn("It replaces six rules above.", text)

    def test_shared(self):
        path, text = self.generate("shared")
        self.assertEqual(path, os.path.join(self.dir, "tmp", "prompts", "shared.md"))
        self.assert_common(text)
        self.assertIn("python3 %s/scripts/plan_shared.py %s --ready" % (SKILL_DIR, self.dir), text)
        self.assertIn(os.path.join(self.dir, "tmp", "shared") + "/shared.json", text)
        self.assertIn("not tried again", text)
        self.assertIn("(`mkdir -p %s` first)" % os.path.join(self.dir, "tmp", "shared"), text)

    def test_python_command(self):
        _, text = self.generate("worker", "--worker", "1", "--python", "py -3")
        self.assertIn("py -3 %s/scripts/payload.py" % SKILL_DIR, text)
        self.assertNotIn("python3 ", text)

    def test_missing_tools_prefix_or_server(self):
        self.edit_state(lambda st: st.pop("tools_prefix"))
        command = "python3 %s/scripts/state.py set-mcp %s --tools-prefix <prefix>" % (SKILL_DIR, self.dir)
        for args in (("worker", "--worker", "1"), ("shared",), ("rules",)):
            stderr = self.fails(*args)
            self.assertIn("no tools_prefix", stderr)
            self.assertIn(command, stderr)
        self.edit_state(lambda st: st.update(tools_prefix=PREFIX, mcp_url=None))
        stderr = self.fails("shared")
        self.assertIn("no mcp_url", stderr)
        self.assertIn(command + " --mcp-url <url>", stderr)
        self.assertFalse(os.path.isdir(os.path.join(self.dir, "tmp", "prompts")))

    def test_rules(self):
        path, text = self.generate("rules")
        self.assertEqual(path, os.path.join(self.dir, "tmp", "prompts", "mcp-rules.md"))
        self.assertTrue(text.startswith("MCP rules:\n- Server:"))
        self.assertIn("starts with `%s`" % PREFIX, text)
        self.assertNotIn("{{", text)

    def test_tool_names_in_the_intro_use_the_prefix(self):
        _, text = self.generate("worker", "--worker", "1")
        self.assertIn("`%slist_products`" % PREFIX, text)
        _, text = self.generate("shared")
        self.assertIn("`%s`" % PREFIX, text.split("\n", 1)[0])

    def test_worker_refuses_a_worklist_already_created(self):
        handle = read_json(os.path.join(self.dir, "worklists", "worker-1.json"))[0]
        self.set_status("created", [handle])
        stderr = self.fails("worker", "--worker", "1")
        self.assertIn("worklists/worker-1.json: %s is created, not pending or failed" % handle, stderr)
        self.assertIn("state.py worklists", stderr)
        self.set_status("failed", [handle])
        self.generate("worker", "--worker", "1")

    def test_module_and_update_refuse_a_worklist_not_created(self):
        handle = read_json(os.path.join(self.dir, "worklists", "worker-1.json"))[0]
        stderr = self.fails("worker", "--worker", "1", "--module", "taxonomy")
        self.assertIn("worklists/worker-1.json: %s is pending, not created" % handle, stderr)
        self.assertIn("state.py worklists %s --workers <n> --module taxonomy" % self.dir, stderr)
        stderr = self.fails("worker", "--worker", "1", "--update")
        self.assertIn("worklists/worker-1.json: %s is pending, not created or archived" % handle, stderr)
        self.assertIn("state.py worklists %s --workers <n> --update" % self.dir, stderr)
        self.set_status("archived")
        self.fails("worker", "--worker", "1", "--module", "taxonomy")
        self.generate("worker", "--worker", "1", "--update")

    def test_unknown_handle_in_worklist(self):
        self.edit_state(lambda st: st["products"].clear())
        self.assertIn("is not in", self.fails("worker", "--worker", "1"))

    def test_missing_worklist(self):
        result = run_cli(self.dir, "worker", "--worker", "9", script="prompts.py")
        self.assertEqual(result.returncode, 1)
        self.assertIn("worklists/worker-9.json", result.stderr)
        self.assertIn("state.py worklists", result.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.dir, "tmp", "prompts", "worker-9.md")))

    def test_unknown_module(self):
        result = run_cli(self.dir, "worker", "--worker", "1", "--module", "images", script="prompts.py")
        self.assertEqual(result.returncode, 1)
        self.assertIn("unknown module 'images'", result.stderr)
        self.assertFalse(os.path.isdir(os.path.join(self.dir, "tmp", "prompts")))

    def test_module_and_update_exclusive(self):
        result = run_cli(self.dir, "worker", "--worker", "1", "--module", "prices", "--update", script="prompts.py")
        self.assertNotEqual(result.returncode, 0)

    def test_missing_heading(self):
        skill = os.path.join(self.dir, "skill")
        shutil.copytree(os.path.join(SKILL_DIR, "references"), os.path.join(skill, "references"))
        create = os.path.join(skill, "references", "create.md")
        with open(create, encoding="utf-8") as f:
            text = f.read()
        with open(create, "w", encoding="utf-8") as f:
            f.write(text.replace("## Worker MCP rules", "## MCP rules of the workers"))
        with self.assertRaisesRegex(prompts.PromptError, "create.md: no section '## Worker MCP rules'"):
            prompts.worker_prompt(self.dir, 1, skill=skill)

    def test_unfilled_placeholder(self):
        with self.assertRaisesRegex(prompts.PromptError, r"unfilled placeholder \{\{OTHER\}\}"):
            prompts.fill("{{DIR}} and {{OTHER}}", {"DIR": "/tmp/x"})

    def test_placeholder_in_inserted_text_is_not_filled_again(self):
        self.assertEqual(prompts.fill("{{A}}-{{B}}", {"A": "{{B}}x", "B": "b"}, check=False), "{{B}}x-b")


if __name__ == "__main__":
    unittest.main()
