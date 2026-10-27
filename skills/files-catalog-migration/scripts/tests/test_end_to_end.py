import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))

from concept_helpers import FIXTURES, PRODUCTS_FIXTURE as FIXTURE_MAPPING, run  # noqa: E402
from demo_files import write_demo_csv, write_demo_xlsx  # noqa: E402

CONCEPTS = ("taxonomy", "metafields", "metaobjects", "prices")


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

    def test_every_concept_to_shared_plan_and_payload(self):
        d = self.dir
        run("intake.py", "add", d, self.demo_xlsx, self.demo_csv)
        run("inventory.py", d)
        os.makedirs(os.path.join(d, "mappings"), exist_ok=True)
        shutil.copy(FIXTURE_MAPPING, os.path.join(d, "mappings", "products.json"))
        for concept in CONCEPTS:
            shutil.copy(os.path.join(FIXTURES, "mapping-%s.json" % concept), os.path.join(d, "mappings", concept + ".json"))
        self.assertIn("== metaobjects ==", run("extract.py", d, "all").stdout)
        run("codes.py", "fill", os.path.join(d, "products.json"), "--prefix", "2", "--start", "1",
            "--sku-pattern", "{handle}-{index}")
        run("extract.py", d, "prices")
        run("state.py", "init", d, "--client", "demo", "--mcp-url", "https://demo.invalid/api/mcp")
        run("state.py", "decide", d, "--key", "data_language", "--value-json", '"fr"', "--origin", "developer")
        run("state.py", "decide", d, "--key", "status_mode", "--value-json", '"draft"', "--origin", "developer")
        for concept in CONCEPTS:
            run("state.py", "decide", d, "--key", concept, "--value-file", os.path.join(d, "concepts", concept + ".json"),
                "--origin", "developer")
        self.assertEqual(run("dryrun.py", d, check=False).returncode, 0)
        plan = json.loads(run("plan_shared.py", d).stdout)
        self.assertEqual([e["key"] for e in plan], [
            "metafield_definition:import.key", "category:Petit électroménager",
            "category:Petit électroménager > Bouilloire", "category:Petit électroménager > Grille-pain",
            "attribute_definition:matiere", "attribute_option:matiere:Acier inoxydable",
            "attribute_option:matiere:Plastique", "metaobject_definition:marque", "metaobject:marque:toastine",
            "metaobject:marque:l-atelier-pain", "metaobject:marque:brioche-co", "metafield_definition:custom.marque",
            "metafield_definition:custom.poids_net", "tax:FR:20"])
        out = json.loads(run("payload.py", d, "2226", "--dry-run").stdout)
        body = out["product"]["body"]
        self.assertEqual(body["category"], "<category:Petit électroménager > Grille-pain IRI>")
        self.assertEqual(body["attributes"], [{"definition": "<attribute_definition:matiere IRI>",
                                               "option": "<attribute_option:matiere:Acier inoxydable IRI>"}])
        self.assertIn({"key": "custom.marque", "shared": "metafield_definition:custom.marque",
                       "body": {"definition": "<metafield_definition:custom.marque IRI>",
                                "value": "<metaobject:marque:toastine IRI>"}}, out["metafields"])
        self.assertEqual(len(out["prices"]), 1)
        self.assertEqual(out["prices"][0]["body"]["tax"], "<tax:FR:20 IRI>")


if __name__ == "__main__":
    unittest.main()
