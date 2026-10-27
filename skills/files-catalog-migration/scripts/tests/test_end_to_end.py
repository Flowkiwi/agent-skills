import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))

from demo_files import write_demo_csv, write_demo_xlsx  # noqa: E402

TESTS = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(TESTS, "..")
FIXTURE_MAPPING = os.path.join(TESTS, "fixtures", "mapping-products.json")


def run(script, *args, check=True):
    result = subprocess.run([sys.executable, os.path.join(SCRIPTS, script)] + list(args),
                            capture_output=True, text=True)
    if check and result.returncode != 0:
        raise AssertionError("%s %s failed (%d):\n%s%s" % (
            script, " ".join(args), result.returncode, result.stdout, result.stderr))
    return result


class EndToEndTest(unittest.TestCase):
    def setUp(self):
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        attachments = tempfile.TemporaryDirectory()
        self.addCleanup(attachments.cleanup)
        self.dir = workspace.name
        self.demo_xlsx = os.path.join(attachments.name, "demo-catalogue.xlsx")
        self.demo_csv = os.path.join(attachments.name, "demo-tarifs.csv")
        write_demo_xlsx(self.demo_xlsx)
        write_demo_csv(self.demo_csv)

    def test_demo_files_to_payload(self):
        d = self.dir
        run("intake.py", "add", d, self.demo_xlsx, self.demo_csv)
        run("inventory.py", d)
        os.makedirs(os.path.join(d, "mappings"), exist_ok=True)
        shutil.copy(FIXTURE_MAPPING, os.path.join(d, "mappings", "products.json"))
        run("extract.py", d, "products")
        self.assertEqual(run("catalog.py", "--check", os.path.join(d, "products.json"), check=False).returncode, 1)
        run("codes.py", "fill", os.path.join(d, "products.json"), "--prefix", "2", "--start", "1",
            "--sku-pattern", "{handle}-{index}")
        run("state.py", "init", d, "--client", "demo", "--mcp-url", "https://demo.invalid/api/mcp")
        run("state.py", "decide", d, "--key", "data_language", "--value-json", '"fr"', "--origin", "developer")
        run("state.py", "decide", d, "--key", "status_mode", "--value-json", '"draft"', "--origin", "developer")
        self.assertEqual(run("dryrun.py", d, check=False).returncode, 0)
        plan = json.loads(run("plan_shared.py", d).stdout)
        self.assertEqual([e["key"] for e in plan], ["metafield_definition:import.key"])
        out = json.loads(run("payload.py", d, "2227", "--dry-run").stdout)
        self.assertEqual(out["product"]["body"]["title"], 'Grille-pain 4 fentes "Stop Eject"')
        self.assertEqual(out["marker"]["body"]["value"], "2227")
        self.assertEqual(out["marker"]["body"]["definition"], "<metafield_definition:import.key IRI>")
        run("state.py", "worklists", d, "--workers", "1", "--pilot")
        with open(os.path.join(d, "worklists", "worker-1.json"), encoding="utf-8") as f:
            self.assertIn("2230", f.read())


if __name__ == "__main__":
    unittest.main()
