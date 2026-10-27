"""Shared helpers of the concept tests: one demo workspace per test class, extracted products."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import extract  # noqa: E402
import plan_shared  # noqa: E402
import state  # noqa: E402
from demo_files import write_demo_csv, write_demo_xlsx  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
PRODUCTS_FIXTURE = os.path.join(FIXTURES, "mapping-products.json")
SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def fixture(name):
    return read_json(os.path.join(FIXTURES, name))


def run(script, *args, check=True):
    """Runs a skill script as the agent does (a subprocess, without -I)."""
    result = subprocess.run([sys.executable, os.path.join(SCRIPTS, script)] + list(args),
                            capture_output=True, text=True)
    if check and result.returncode != 0:
        raise AssertionError("%s %s failed (%d):\n%s%s" % (
            script, " ".join(args), result.returncode, result.stdout, result.stderr))
    return result


def write_workspace(directory, mappings=()):
    """The demo workspace: sources, mappings/products.json and the named fixture mappings (concepts)."""
    os.makedirs(os.path.join(directory, "sources"))
    os.makedirs(os.path.join(directory, "mappings"))
    write_demo_xlsx(os.path.join(directory, "sources", "demo-catalogue.xlsx"))
    write_demo_csv(os.path.join(directory, "sources", "demo-tarifs.csv"))
    shutil.copy(PRODUCTS_FIXTURE, os.path.join(directory, "mappings", "products.json"))
    for concept in mappings:
        shutil.copy(os.path.join(FIXTURES, "mapping-%s.json" % concept),
                    os.path.join(directory, "mappings", concept + ".json"))


def fake_iri(key):
    kind = key.split(":", 1)[0]
    return "/api/%ss/%s" % (kind, uuid.uuid5(uuid.NAMESPACE_URL, key))


class ConceptTestCase(unittest.TestCase):
    """setUpClass builds the demo workspace (sources, mappings/products.json) and extracts the products."""

    @classmethod
    def setUpClass(cls):
        tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        cls.dir = tmp.name
        write_workspace(cls.dir)
        extract.main_products(cls.dir)

    def products(self):
        return read_json(os.path.join(self.dir, "products.json"))

    def write_mapping(self, m):
        with open(os.path.join(self.dir, "mappings", m["concept"] + ".json"), "w", encoding="utf-8") as f:
            json.dump(m, f, ensure_ascii=False)

    def extract_with(self, m):
        """Writes mappings/<concept>.json, extracts the concept and returns concepts/<concept>.json."""
        self.write_mapping(m)
        result = extract.main_concept(self.dir, m["concept"])
        self.assertEqual(read_json(os.path.join(self.dir, "concepts", m["concept"] + ".json")), result)
        return result

    def state_with_decision(self, key, value, origin="developer"):
        """State of the demo products with status_mode draft, data_language fr and the decision; no shared
        element is recorded (the import.key marker stays first in the plan)."""
        st = state.init("demo", "https://mcp.example.com", self.products())
        state.decide(st, "status_mode", "draft", "developer")
        state.decide(st, "data_language", "fr", "developer")
        state.decide(st, key, value, origin)
        return st

    def record_all_shared(self, st):
        """Records every entry of plan_shared.plan as created, dependencies first, with fake IRIs."""
        entries = plan_shared.plan(st)
        while entries:
            ready = [e for e in entries if not e["depends_on"]]
            self.assertTrue(ready, "shared entries wait on each other: %r" % [e["key"] for e in entries])
            for entry in ready:
                state.record_shared(st, entry["key"], fake_iri(entry["key"]), "created", entry["module"], entry["spec"])
            entries = plan_shared.plan(st)
        return st
