"""Shared helpers of the later-phase tests: two demo products, their state and the created result lines."""
import json
import os
import subprocess
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import state  # noqa: E402

SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
MARKER_IRI = "/api/metafield_definitions/00000000-0000-4000-8000-000000000001"


def make_products():
    """2226: one variant (SKU 1001); 2230: two variants (SKUs 1005, 1006) on option Couleur."""
    return {"products": [
        {"key": "2226", "handle": "2226", "title": "Grille-pain", "description_html": "<p>Inox</p>",
         "tags": ["cuisine", "petit-electromenager"], "status": None, "options": [],
         "variants": [{"sku": "1001", "barcode": "3601234560012", "option_values": []}],
         "metafields": {}},
        {"key": "2230", "handle": "2230", "title": "Grille-pain Duo", "description_html": "", "tags": [],
         "status": None, "options": [{"name": "Couleur", "values": ["Noir", "Rouge"]}],
         "variants": [{"sku": "1005", "barcode": "3601234560050", "option_values": ["Noir"]},
                      {"sku": "1006", "barcode": "3601234560067", "option_values": ["Rouge"]}],
         "metafields": {}},
    ], "warnings": []}


def make_state(products, mode="draft"):
    """Initial state of the products, status_mode recorded and the import.key marker created."""
    st = state.init("demo", "https://mcp.example.com", products)
    state.decide(st, "status_mode", mode, "developer")
    state.record_shared(st, "metafield_definition:import.key", MARKER_IRI, "created", "core")
    return st


def product_iri(handle):
    return "/api/products/%s" % uuid.uuid5(uuid.NAMESPACE_URL, "product:" + handle)


def variant_iri(handle, sku):
    return "%s/variants/%s" % (product_iri(handle), uuid.uuid5(uuid.NAMESPACE_URL, "variant:%s:%s" % (handle, sku)))


def created_line(handle, products=None):
    """A valid created result line: the product item, then one variant item per variant (ref = its SKU)."""
    products = products or make_products()
    product = next(p for p in products["products"] if p["handle"] == handle)
    items = [{"kind": "product", "iri": product_iri(handle), "status": "created", "module": "core"}]
    items += [{"kind": "variant", "iri": variant_iri(handle, v["sku"]), "status": "created", "module": "core",
               "ref": v["sku"]} for v in product["variants"]]
    return {"handle": handle, "status": "created", "iri": product_iri(handle), "error": None, "items": items}


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_workspace(directory, products, st):
    write_json(os.path.join(directory, "products.json"), products)
    write_json(os.path.join(directory, "state.json"), st)


def run_cli(*args, script="state.py"):
    """Runs a skill script as the agent does (a subprocess)."""
    return subprocess.run([sys.executable, os.path.join(SCRIPTS, script)] + list(args),
                          capture_output=True, text=True)
