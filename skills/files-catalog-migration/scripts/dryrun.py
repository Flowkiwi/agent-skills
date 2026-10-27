"""Dry-run summary of a files -> Flowkiwi migration workspace, before anything is created.

Usage:
  python3 dryrun.py <dir>

Reads products.json, state.json (optional) and mappings/*.json. Exit code 1 when blocking warnings remain, when
products.json and state.json disagree (a created product whose handle is gone, handles not synced), when
payload.py would fail for a product to create, or when the status_mode / data_language decisions are missing
or invalid.
"""

import glob
import json
import os
import sys
from typing import List, Tuple

import catalog
import payload

REQUIRED_DECISIONS = ("status_mode", "data_language")
STATUS_MODES = ("draft", "active", "source")
MAX_CANDIDATES = 3
MAX_PAYLOAD_ERRORS = 10
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


def split_products(data, state):
    """(products to create, number already created) by the state status of their handle."""
    statuses = {handle: entry.get("status") for handle, entry in ((state or {}).get("products") or {}).items()}
    pending, created = [], 0
    for product in data["products"]:
        status = statuses.get(product["handle"], "pending")
        if status == "created":
            created += 1
        elif status != "skipped":
            pending.append(product)
    return pending, created


def is_live(entry):
    return entry.get("status") == "created" or any(i.get("status") == "created" for i in entry.get("items") or [])


def state_drift(data, state):
    """Blocking lines when products.json and state.json disagree on the handles."""
    if not state:
        return ["  state.json missing: run state.py init"]
    entries = state.get("products") or {}
    handles = {p["handle"] for p in data["products"]}
    gone = sorted(h for h, entry in entries.items() if h not in handles and is_live(entry))
    unsynced = sorted([h for h in handles if h not in entries or entries[h].get("status") == "skipped"]
                      + [h for h, entry in entries.items() if h not in handles and not is_live(entry)
                         and entry.get("status") in ("pending", "failed")])
    lines = []
    if gone:
        lines.append("  identity changed or product removed: %s (created in Flowkiwi, no longer in products.json: "
                     "undo it first or restore its key)" % catalog._handles_text(gone))
    if unsynced:
        lines.append("  products.json and state.json differ, run state.py sync: %s" % catalog._handles_text(unsynced))
    return lines


def payload_errors(products, state):
    """Builds the payload of every product to create, as the workers will (placeholders for shared IRIs)."""
    errors = []
    for product in products:
        try:
            payload.build({"products": [product]}, state, product["handle"], placeholders=True)
        except payload.PayloadError as error:
            errors.append("    %s: %s" % (product["handle"], error))
    if not errors:
        return []
    more = len(errors) - MAX_PAYLOAD_ERRORS
    return (["  payload.py fails for %d products:" % len(errors)] + errors[:MAX_PAYLOAD_ERRORS]
            + (["    and %d more" % more] if more > 0 else []))


def to_create(data, state):
    pending, created = split_products(data, state)
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
    problems = state_drift(data, state)
    mode = decisions.get("status_mode")
    if mode and mode not in STATUS_MODES:
        problems.append("  status_mode %r is not one of %s" % (mode, ", ".join(STATUS_MODES)))
    elif mode:
        problems += payload_errors(split_products(data, state)[0], state)
    variants = [v for p in data["products"] for v in p["variants"]]
    images = sum(len(p.get("images") or []) for p in data["products"])

    lines = ["Data language: %s" % (decisions.get("data_language") or "(missing)"),
             "Status: %s" % (decisions.get("status_mode") or "(missing)")]
    lines += to_create(data, state)
    if blocking or problems:
        lines.append("Blocking:")
        if blocking:
            lines.append(catalog.format_warnings(blocking))
        lines += problems
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
    return "\n".join(lines), 1 if blocking or problems or missing else 0


def main(argv):
    if len(argv) != 2:
        print("usage: dryrun.py <dir>", file=sys.stderr)
        return 2
    text, code = report(argv[1])
    print(text)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
