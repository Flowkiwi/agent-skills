"""Dry-run summary of a files -> Flowkiwi migration workspace, before anything is created.

Usage:
  python3 dryrun.py <dir>

Reads products.json, state.json (optional) and mappings/*.json. Exit code 1 when blocking warnings remain or
the status_mode / data_language decisions are missing.
"""

import glob
import json
import os
import sys
from typing import List, Tuple

import catalog

REQUIRED_DECISIONS = ("status_mode", "data_language")
MAX_CANDIDATES = 3
SPEC_RESERVED = ("origin", "reason", "question")


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def walk_agent(node, concept, path, found):
    if isinstance(node, dict):
        if node.get("origin") == "agent" and path:
            found.append({
                "concept": concept, "path": ".".join(path), "reason": node.get("reason"),
                "question": node.get("question"),
                "spec": {k: v for k, v in node.items() if k not in SPEC_RESERVED},
            })
        for key, value in node.items():
            walk_agent(value, concept, path + [str(key)], found)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            walk_agent(value, concept, path + [str(index)], found)


def agent_choices(directory: str, state: dict) -> List[dict]:
    found = []  # type: List[dict]
    for path in sorted(glob.glob(os.path.join(directory, "mappings", "*.json"))):
        mapping = read_json(path)
        concept = mapping.get("concept") or os.path.splitext(os.path.basename(path))[0]
        walk_agent(mapping, concept, [], found)
    decisions = state.get("decisions") or {}
    for key, meta in (state.get("decision_meta") or {}).items():
        if meta.get("origin") == "agent":
            found.append({"concept": "decisions", "path": key, "reason": meta.get("reason"), "question": None,
                          "spec": decisions.get(key)})
    return found


def to_create(data, state):
    statuses = {handle: entry.get("status") for handle, entry in ((state or {}).get("products") or {}).items()}
    pending, created = [], 0
    for product in data["products"]:
        status = statuses.get(product["handle"], "pending")
        if status == "created":
            created += 1
        elif status != "skipped":
            pending.append(product)
    options = sum(len(p.get("options") or []) for p in pending)
    values = sum(len(o["values"]) for p in pending for o in p.get("options") or [])
    variants = sum(len(p["variants"]) for p in pending)
    suffix = " (%d already created)" % created if created else ""
    return [
        "To create:",
        "  products: %d%s" % (len(pending), suffix),
        "  variants: %d" % variants,
        "  options: %d" % options,
        "  option values: %d" % values,
    ]


def descriptions(data):
    origins = {}
    candidates = []
    for product in data["products"]:
        origin = product.get("description_origin") or "none"
        origins[origin] = origins.get(origin, 0) + 1
        if product.get("description_candidate"):
            candidates.append(product)
    lines = ["Descriptions:"] + ["  %s: %d" % (origin, count) for origin, count in sorted(origins.items())]
    for product in candidates[:MAX_CANDIDATES]:
        lines.append("  candidate %s" % product["handle"])
        lines.append("    poor: %s" % (product.get("description_html") or "(empty)"))
        lines.append("    candidate: %s" % product["description_candidate"])
    return lines


def report(directory: str) -> Tuple[str, int]:
    data = catalog.refresh(read_json(os.path.join(directory, "products.json")))
    state_path = os.path.join(directory, "state.json")
    state = read_json(state_path) if os.path.exists(state_path) else {}
    decisions = state.get("decisions") or {}

    blocking = catalog.blocking(data, decisions)
    missing = [key for key in REQUIRED_DECISIONS if not decisions.get(key)]
    variants = [v for p in data["products"] for v in p["variants"]]
    images = sum(len(p.get("images") or []) for p in data["products"])

    lines = ["Data language: %s" % (decisions.get("data_language") or "(missing)"),
             "Status: %s" % (decisions.get("status_mode") or "(missing)")]
    lines += to_create(data, state)
    if blocking:
        lines += ["Blocking:", catalog.format_warnings(blocking)]
    else:
        lines.append("Blocking: none")
    lines.append("Cleaning:")
    lines += ["  %s: %d" % (rule, count) for rule, count in data.get("cleaning", {}).items() if count]
    lines.append("Agent choices:")
    lines += ["  [%s] %s: %s" % (c["concept"], c["path"], c["reason"] or "(no reason given)")
              for c in agent_choices(directory, state)]
    lines.append("Generated codes: %d barcodes, %d SKUs" % (
        sum(1 for v in variants if v.get("barcode_generated")), sum(1 for v in variants if v.get("sku_generated"))))
    lines += descriptions(data)
    lines += ["Not migrated:", "  %d image URLs kept in products.json" % images]
    for key in missing:
        lines.append("missing decision: %s" % key)
    return "\n".join(lines), 1 if blocking or missing else 0


def main(argv):
    if len(argv) != 2:
        print("usage: dryrun.py <dir>", file=sys.stderr)
        return 2
    text, code = report(argv[1])
    print(text)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
