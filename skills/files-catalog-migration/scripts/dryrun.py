"""Dry-run summary of a files -> Flowkiwi migration workspace, before anything is created.

Usage:
  python3 dryrun.py <dir>

Reads products.json, state.json (optional), mappings/*.json and concepts/*.json. Exit code 1 when blocking
warnings remain (list_in_single_field in a concept file included), when products.json and state.json disagree
(a created product whose handle is gone, handles not synced), when payload.py would fail for a product to create,
when a concept file differs from its decision in state.json (warnings aside: run state.py decide again), or when
the status_mode / data_language decisions are missing or invalid.
"""

import glob
import json
import os
import sys
from typing import List, Tuple

import catalog
import payload
from metafields import suggest_type

REQUIRED_DECISIONS = ("status_mode", "data_language")
STATUS_MODES = ("draft", "active", "source")
MAX_CANDIDATES = 3
MAX_PAYLOAD_ERRORS = 10
SPEC_RESERVED = ("origin", "reason", "question")
CONCEPTS = ("taxonomy", "collections", "metafields", "metaobjects", "prices")
CONCEPT_BLOCKING = ("list_in_single_field",)


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


def without_warnings(value):
    return {k: v for k, v in value.items() if k != "warnings"} if isinstance(value, dict) else value


def concept_files(directory: str) -> List[Tuple[str, dict]]:
    found = []
    for concept in CONCEPTS:
        path = os.path.join(directory, "concepts", concept + ".json")
        if os.path.exists(path):
            found.append((concept, read_json(path)))
    return found


def stale_decisions(files, decisions) -> List[str]:
    """A concept file must be the recorded decision; both are compared without their warnings."""
    return ["  stale decision: %s (run state.py decide <dir> --key %s --value-file concepts/%s.json "
            "--origin developer)" % (concept, concept, concept)
            for concept, data in files if without_warnings(decisions.get(concept)) != without_warnings(data)]


def indented(text: str, prefix: str) -> List[str]:
    return [prefix + line for line in text.splitlines()]


def concept_blocking(files) -> List[str]:
    lines = []
    for concept, data in files:
        found = [w for w in data.get("warnings") or [] if w.get("code") in CONCEPT_BLOCKING]
        if found:
            lines += ["  %s:" % concept] + indented(catalog.format_warnings(found), "    ")
    return lines


def metafield_rows(data) -> List[str]:
    values = {}  # type: dict
    for product_values in (data.get("values") or {}).values():
        for key, value in product_values.items():
            values.setdefault(key, []).append(json.dumps(value, ensure_ascii=False)
                                              if isinstance(value, list) else str(value))
    rows = [[key, d.get("name") or "", "proposed %s" % suggest_type(values.get(key, []))[0],
             "chosen %s" % d.get("type"), "%d values" % len(values.get(key, []))]
            for key, d in (data.get("definitions") or {}).items()]
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]) - 1)] if rows else []
    return ["    " + " | ".join([cell.ljust(width) for cell, width in zip(row, widths)] + row[-1:]) for row in rows]


def concept_lines(concept: str, data: dict, products: List[dict]) -> List[str]:
    if concept == "taxonomy":
        attributes = data.get("attributes") or []
        options = sum(len(a.get("options") or []) for a in attributes if a.get("type", "list") == "list")
        return ["  taxonomy: %d categories, %d attribute definitions, %d options, %d products without category" % (
            len(data.get("categories") or []), len(attributes), options, len(data.get("uncategorized") or []))]
    if concept == "collections":
        return ["  collections: %d collections, %d products in a collection" % (
            len(data.get("collections") or []), len(data.get("membership") or {}))]
    if concept == "metafields":
        return ["  metafields:"] + metafield_rows(data)
    if concept == "metaobjects":
        return ["  metaobjects: %d definitions, %d entries, %d products referenced" % (
            len(data.get("definitions") or []), len(data.get("entries") or []), len(data.get("values") or {}))]
    countries = data.get("countries") or []
    offers = sum(len(by_country) for by_sku in (data.get("offers") or {}).values() for by_country in by_sku.values())
    pairs = sum(len(p["variants"]) for p in products) * len(countries)
    return ["  prices: %d offers, %d variant-country pairs without price, countries %s" % (
        offers, pairs - offers, ", ".join(c.get("code") or "?" for c in countries) or "none")]


def concepts_section(files, products) -> List[str]:
    if not files:
        return ["Concepts: none"]
    lines = ["Concepts:"]
    for concept, data in files:
        lines += concept_lines(concept, data, products)
        if data.get("warnings"):
            lines += indented(catalog.format_warnings(data["warnings"]), "    ")
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
    files = concept_files(directory)
    problems += stale_decisions(files, decisions) + concept_blocking(files)
    variants = [v for p in data["products"] for v in p["variants"]]
    images = sum(len(p.get("images") or []) for p in data["products"])

    lines = ["Data language: %s" % (decisions.get("data_language") or "(missing)"),
             "Status: %s" % (decisions.get("status_mode") or "(missing)")]
    lines += to_create(data, state)
    lines += concepts_section(files, data["products"])
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
