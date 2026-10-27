"""Dry-run summary of a files -> Flowkiwi migration workspace, before anything is created.

Usage:
  python3 dryrun.py <dir>

Reads products.json, state.json (optional), mappings/*.json and concepts/*.json. Exit code 1 when blocking
warnings remain (list_in_single_field, missing_sku and too_long in a concept file included), when products.json
and state.json disagree (a created product whose handle is gone, handles not synced), when payload.py would fail
for a product to create, when a concept file was extracted from another products.json or mapping (its provenance
block: run extract.py <dir> <concept> again), when a concept file differs from its decision in state.json
(warnings aside: run state.py decide again), or when the status_mode / data_language decisions are missing or
invalid.

In a later phase (state phase > 1) a `Changes (phase <n>):` section summarizes diff.json and plan_shared.py
--changes, and the dry-run also blocks while diff.json is missing, from another phase or computed from another
products.json or other decisions, while a changed or removed product has no snapshot read in this phase
(tmp/remote/<handle>.json) or the conflicts were not computed from the current snapshots, while payload.py fails
for a product of diff.json, and while the removed products, the rekeyed products or the destructive shared changes
lack their decision of the phase (removed_action, confirmed_rekey, confirmed_changes). A created handle gone from
products.json is then a removed product (flagged by state.py sync), not a drift.
"""

import glob
import json
import os
import shlex
import sys
from typing import List, Tuple

import catalog
import diff as diff_module
import payload
import plan_shared
from metafields import suggest_type
from state import forced, phase_decision

REQUIRED_DECISIONS = ("status_mode", "data_language")
STATUS_MODES = ("draft", "active", "source")
MAX_CANDIDATES = 3
MAX_PAYLOAD_ERRORS = 10
MAX_NAMES = 20
MAX_CHANGED = 10
SPEC_RESERVED = ("origin", "reason", "question")
CONCEPTS = ("taxonomy", "collections", "metafields", "metaobjects", "prices")
CONCEPT_BLOCKING = ("list_in_single_field", "missing_sku", "too_long")


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
    """(products to create, number already created) by the state status of their handle. Skipped, deleted and
    archived handles are never assigned to a creation worker."""
    statuses = {handle: entry.get("status") for handle, entry in ((state or {}).get("products") or {}).items()}
    pending, created = [], 0
    for product in data["products"]:
        status = statuses.get(product["handle"], "pending")
        if status == "created":
            created += 1
        elif status not in ("skipped", "deleted", "archived"):
            pending.append(product)
    return pending, created


def is_live(entry):
    return entry.get("status") == "created" or any(i.get("status") == "created" for i in entry.get("items") or [])


def state_drift(data, state):
    """Blocking lines when products.json and state.json disagree on the handles. In a later phase a created or
    archived handle gone from products.json is a removed product once state.py sync flagged it."""
    if not state:
        return ["  state.json missing: run state.py init"]
    entries = state.get("products") or {}
    handles = {p["handle"] for p in data["products"]}
    gone = sorted(h for h, entry in entries.items() if h not in handles and is_live(entry))
    flagged = []
    if state.get("phase", 1) > 1:
        removed = [h for h in gone if entries[h].get("status") in ("created", "archived")]
        gone = [h for h in gone if h not in removed]
        flagged = [h for h in removed if not entries[h].get("removed")]
    unsynced = sorted([h for h in handles if h not in entries or entries[h].get("status") == "skipped"]
                      + [h for h, entry in entries.items() if h not in handles and not is_live(entry)
                         and entry.get("status") in ("pending", "failed")] + flagged)
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


def provenance_problems(directory: str, files, data: dict) -> List[str]:
    """A concept file must come from the current products.json and mappings/<concept>.json."""
    lines = []
    for concept, content in files:
        mapping_path = os.path.join(directory, "mappings", concept + ".json")
        expected = catalog.concept_provenance(data, concept, mapping_path)
        found = content.get("provenance") or {}
        command = "re-run extract.py %s %s" % (shlex.quote(directory), concept)
        if found.get("products") != expected["products"]:
            lines.append("  concept %s was extracted from another products.json: %s" % (concept, command))
        elif found.get("mapping") != expected["mapping"]:
            lines.append("  concept %s was extracted from another mappings/%s.json: %s" % (concept, concept, command))
    return lines


def decide_command(directory: str, concept: str, meta: dict) -> str:
    """The state.py decide command recording the concept file again, with the origin and reason it had."""
    words = ["state.py", "decide", directory, "--key", concept, "--value-file",
             os.path.join(directory, "concepts", concept + ".json"), "--origin", meta.get("origin") or "developer"]
    if meta.get("reason"):
        words += ["--reason", meta["reason"]]
    return " ".join(shlex.quote(word) for word in words)


def stale_decisions(files, decisions, directory: str = "<dir>", meta: dict = None) -> List[str]:
    """A concept file must be the recorded decision; both are compared without their warnings."""
    meta = meta or {}
    return ["  stale decision: %s (run %s)" % (concept, decide_command(directory, concept, meta.get(concept) or {}))
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


def capped(items: List[str]) -> List[str]:
    more = len(items) - MAX_NAMES
    return items[:MAX_NAMES] + (["... and %d more" % more] if more > 0 else [])


def named(title: str, items: List[str]) -> List[str]:
    return ["    %s:" % title] + ["      " + item for item in capped(items)] if items else []


def name_lines(concept: str, data: dict) -> List[str]:
    """The names the migration will write, in the data language, for the developer to review."""
    if concept == "taxonomy":
        attributes = []
        for a in data.get("attributes") or []:
            options = capped([o["value"] for o in a.get("options") or []])
            attributes.append("%s '%s' (%s)%s" % (a["code"], a["name"], a.get("type", "list"),
                                                  ": " + ", ".join(options) if options else ""))
        return (named("category paths", [c["path"] for c in data.get("categories") or []])
                + named("attributes", attributes))
    if concept == "collections":
        return named("collection paths", [c["path"] for c in data.get("collections") or []])
    if concept == "metaobjects":
        definitions, display = [], {}
        for d in data.get("definitions") or []:
            display[d["type"]] = d.get("displayNameField")
            fields = ", ".join("%s '%s' (%s)" % (f["key"], f["name"], f["type"]) for f in d.get("fields") or [])
            reference = d.get("reference")
            definitions.append("%s '%s': fields %s%s" % (d["type"], d["name"], fields, "; reference %s '%s'" % (
                reference["metafield"], reference["name"]) if reference else ""))
        entries = ["%s %s '%s'" % (e["definition"], e["handle"], e["fields"].get(display.get(e["definition"]), ""))
                   for e in data.get("entries") or []]
        return named("definitions", definitions) + named("entries", entries)
    if concept == "prices":
        return named("taxes", ["%s: %s %s%% (%s)" % (c.get("code"), c.get("tax_name") or c.get("tax"), c.get("rate"),
                                                    "to create" if c.get("tax") is None else "existing")
                               for c in data.get("countries") or []])
    return []


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
        offers, max(0, pairs - offers), ", ".join(c.get("code") or "?" for c in countries) or "none")]


def concepts_section(files, products) -> List[str]:
    if not files:
        return ["Concepts: none"]
    lines = ["Concepts:"]
    for concept, data in files:
        lines += concept_lines(concept, data, products) + name_lines(concept, data)
        if data.get("warnings"):
            lines += indented(catalog.format_warnings(data["warnings"]), "    ")
    return lines


def read_diff(directory: str):
    path = os.path.join(directory, "diff.json")
    return read_json(path) if os.path.exists(path) else None


def removed_action(state: dict):
    decision = phase_decision(state, "removed_action")
    action = decision.get("action") if isinstance(decision, dict) else None
    return action if action in ("archive", "delete") else None


def snapshot_handles(diff_data: dict, state: dict) -> List[str]:
    """The handles the update run may write: each needs a snapshot of Flowkiwi read in this phase."""
    handles = list(diff_data.get("changed") or {}) + list(diff_data.get("removed") or [])
    if removed_action(state) == "delete":
        handles += list(diff_data.get("already_archived") or [])
    return sorted(set(handles))


def snapshot_problems(directory: str, diff_data: dict, state: dict) -> List[str]:
    started = diff_module.phase_started(state)
    compared = diff_data.get("snapshots") or {}
    missing, stale = [], []
    for handle in snapshot_handles(diff_data, state):
        path = os.path.join(directory, "tmp", "remote", handle + ".json")
        if not os.path.exists(path) or (started is not None and os.path.getmtime(path) < started):
            missing.append(handle)
        elif compared.get(handle) != os.path.getmtime(path):
            stale.append(handle)
    command = "diff.py conflicts %s" % shlex.quote(directory)
    lines = []
    if missing:
        lines.append("  snapshots missing: %s (read in this phase into tmp/remote/<handle>.json, then run %s)" % (
            catalog._handles_text(missing), command))
    if stale:
        lines.append("  conflicts not computed from the current snapshots: %s (run %s)" % (
            catalog._handles_text(stale), command))
    return lines


def diff_problems(directory: str, diff_data, state: dict, products: dict) -> List[str]:
    """diff.json must exist, be of the current phase and come from the current products and decisions."""
    phase = state.get("phase", 1)
    command = "run diff.py %s" % shlex.quote(directory)
    if diff_data is None:
        return ["  diff.json is missing: %s" % command]
    if diff_data.get("phase") != phase:
        return ["  diff.json is from phase %s: %s" % (diff_data.get("phase"), command)]
    if diff_data.get("source") != diff_module.source_fingerprint(state, products):
        return ["  diff.json was computed from another products.json or other decisions: %s, then %s" % (
            command, "diff.py conflicts %s" % shlex.quote(directory))]
    return []


def decision_problems(diff_data: dict, state: dict) -> List[str]:
    phase = state.get("phase", 1)
    removal = diff_data.get("removal") or {}
    lines = []
    waiting = [h for h in diff_data.get("removed") or [] if removal.get(h, "removed_action") == "removed_action"]
    if waiting and removed_action(state) is None:
        lines.append('  removed products without decision removed_action {"phase": %d, "action": "archive" or '
                     '"delete"}: %s' % (phase, catalog._handles_text(waiting)))
    if "delete_rekeyed" in removal.values() and phase_decision(state, "confirmed_rekey") is None:
        lines.append('  rekeyed products not confirmed: decision confirmed_rekey {"phase": %d}' % phase)
    return lines


def diff_errors(diff_data: dict) -> List[str]:
    errors = ["    %s: %s" % item for item in (diff_data.get("errors") or {}).items()]
    if not errors:
        return []
    more = len(errors) - MAX_PAYLOAD_ERRORS
    return (["  payload.py fails for %d products of diff.json:" % len(errors)] + errors[:MAX_PAYLOAD_ERRORS]
            + (["    and %d more" % more] if more > 0 else []))


def shared_changes(state: dict, data: dict) -> Tuple[List[str], List[str]]:
    """(lines of the Changes section, blocking lines) of plan_shared.py --changes."""
    try:
        found = plan_shared.changes(state, data.get("metafield_columns"))
    except payload.PayloadError as error:
        return [], ["  plan_shared.py --changes fails: %s" % error]
    waiting = plan_shared.unconfirmed(state, found)
    destructive = found["destructive"]
    lines = ["  shared changes: %d destructive (confirmed %d), %d updates" % (
        len(destructive), len(destructive) - len(waiting), len(found["updates"]))]
    lines += ["    %s: %s (products: %s)" % (c["key"], c["reason"], catalog._handles_text(c["products"]) or "none")
              for c in destructive]
    lines += ["    update %s: %s" % (u["key"], u["operation"]) for u in found["updates"]]
    if found["unmanaged"]:
        lines.append("    unmanaged (reused, never modified): %s" % ", ".join(found["unmanaged"]))
    if found["unknown"]:
        lines.append("    unknown (recorded without spec, not compared): %s" % ", ".join(found["unknown"]))
    blocking = ["  destructive shared changes not confirmed: %s" % ", ".join(waiting)] if waiting else []
    return lines, blocking


def changes_section(diff_data, state: dict) -> List[str]:
    lines = ["Changes (phase %d):" % state.get("phase", 1)]
    if diff_data is None or diff_data.get("phase") != state.get("phase", 1):
        return lines + ["  diff.json missing or from another phase"]
    changed = diff_data.get("changed") or {}
    lines.append("  new: %d" % len(diff_data.get("new") or []))
    lines.append("  changed: %d" % len(changed))
    lines += ["    %s: %s" % (handle, ", ".join(diff_module.change_paths(change)))
              for handle, change in list(changed.items())[:MAX_CHANGED]]
    if len(changed) > MAX_CHANGED:
        lines.append("    and %d more" % (len(changed) - MAX_CHANGED))
    for entry in diff_data.get("moved") or []:
        lines.append("    moved SKU %s: %s -> %s" % (entry["sku"], entry["from"], ", ".join(entry["to"])))
    for entry in diff_data.get("blocked_moves") or []:
        lines.append("    blocked move SKU %s: %s -> %s: %s (kept on %s, never deleted)" % (
            entry["sku"], entry["from"], ", ".join(entry["to"]), entry["reason"], entry["from"]))
    removed = diff_data.get("removed") or []
    lines.append("  removed: %d (action: %s)" % (len(removed), removed_action(state) or "not decided"))
    if removed:
        lines.append("    " + catalog._handles_text(removed))
    archived = diff_data.get("already_archived") or []
    if archived:
        lines.append("  already archived: %d (written again only when the action is delete)" % len(archived))
    rekeyed = diff_data.get("rekeyed") or []
    lines.append("  rekeyed: %d (%s)" % (len(rekeyed), "confirmed" if phase_decision(state, "confirmed_rekey")
                                          is not None else "not confirmed"))
    for entry in rekeyed:
        targets = entry.get("new", []) + entry.get("changed", [])
        lines.append("    %s -> %s (SKU %s)" % (entry["removed"], ", ".join(targets), ", ".join(entry["skus"])))
    for handle, reason in (diff_data.get("blocked_rekeys") or {}).items():
        lines.append("    blocked %s: %s (archived, never deleted)" % (handle, reason))
    conflicts = diff_data.get("conflicts") or {}
    lines.append("  modified in Flowkiwi: %d (forced: %d)" % (
        len(conflicts), sum(1 for handle in conflicts if forced(state, handle))))
    lines += ["    %s%s: %s" % (handle, " (forced)" if forced(state, handle) else "", ", ".join(paths))
              for handle, paths in conflicts.items()]
    unmanaged = diff_data.get("unmanaged") or []
    lines.append("  unmanaged: %d" % len(unmanaged))
    if unmanaged:
        lines.append("    %s (reused, never written)" % catalog._handles_text(unmanaged))
    lines += ["    %s, reused items never written: %s" % (handle, ", ".join(paths))
              for handle, paths in (diff_data.get("unmanaged_items") or {}).items()]
    if diff_data.get("no_written"):
        lines.append("  not compared (no written snapshot): %s" % catalog._handles_text(diff_data["no_written"]))
    return lines


def phase_checks(directory: str, state: dict, products: dict) -> Tuple[List[str], List[str]]:
    """(the Changes section, blocking lines) of a later phase."""
    diff_data = read_diff(directory)
    problems = diff_problems(directory, diff_data, state, products)
    lines = changes_section(diff_data, state)
    if diff_data is not None and diff_data.get("phase") == state.get("phase", 1):
        problems += diff_errors(diff_data)
        problems += snapshot_problems(directory, diff_data, state)
        problems += decision_problems(diff_data, state)
    shared_lines, shared_blocking = shared_changes(state, products)
    return lines + shared_lines, problems + shared_blocking


def report(directory: str) -> Tuple[str, int]:
    raw = read_json(os.path.join(directory, "products.json"))
    data = catalog.refresh(json.loads(json.dumps(raw)))
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
    workspace = os.path.abspath(directory)
    problems += provenance_problems(workspace, files, data)
    problems += stale_decisions(files, decisions, workspace, state.get("decision_meta"))
    problems += concept_blocking(files)
    changes = []  # type: List[str]
    if state.get("phase", 1) > 1:
        changes, phase_problems = phase_checks(workspace, state, raw)
        problems += phase_problems
    variants = [v for p in data["products"] for v in p["variants"]]
    images = sum(len(p.get("images") or []) for p in data["products"])

    lines = ["Data language: %s" % (decisions.get("data_language") or "(missing)"),
             "Status: %s" % (decisions.get("status_mode") or "(missing)")]
    lines += to_create(data, state)
    lines += changes
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
