"""Bookkeeping for a Shopify -> Flowkiwi migration workspace.

Usage:
  python3 state.py init <dir> --client <slug> --mcp-url <url>
  python3 state.py sync <dir>
  python3 state.py shared <dir> --key <kind:name> --iri <iri> --status created|reused --module <module>
  python3 state.py decide <dir> --key <name> --value-json '<json>'
  python3 state.py worklists <dir> --workers 4 [--limit 5] [--pilot] [--retry-failed] [--module <name>]
  python3 state.py result <results/worker-N.jsonl> --json '<line>'
  python3 state.py merge <dir>
  python3 state.py undo-plan <dir> --scope all|pilot|module:<name>
  python3 state.py deleted <dir> --iris-file <file.json>
  python3 state.py reset-deleted <dir>
  python3 state.py summary <dir>
"""

import argparse
import glob
import json
import os
import sys
import tempfile
from datetime import datetime, timezone

KIND_ORDER = [
    "sale_offer", "purchase_offer", "metafield", "variant", "option_value", "product_option", "product",
    "collection", "attribute_option", "attribute_definition", "category", "supplier", "tax",
    "metafield_definition", "channel", "country",
]
RESULT_KEYS = {"handle", "status", "iri", "items", "error"}
RESULT_STATUSES = {"created", "failed"}


def _new_product():
    return {"status": "pending", "iri": None, "pilot": False, "error": None, "items": []}


def init(client, mcp_url, products):
    return {
        "version": 1,
        "client": client,
        "mcp_url": mcp_url,
        "started_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "decisions": {},
        "shared": {},
        "products": {p["handle"]: _new_product() for p in products["products"]},
    }


def sync_products(state, products):
    handles = {p["handle"] for p in products["products"]}
    for handle in handles:
        product = state["products"].setdefault(handle, _new_product())
        if product["status"] == "skipped":
            product["status"] = "pending"
    for handle, product in state["products"].items():
        if handle not in handles and product["status"] == "pending":
            product["status"] = "skipped"
    return state


def record_shared(state, key, iri, status, module):
    state["shared"][key] = {"iri": iri, "status": status, "module": module}
    return state


def decide(state, key, value):
    state["decisions"][key] = value
    return state


def worklists(state, workers, limit, pilot, retry_failed=False, module=None, titles=None):
    """Split handles between workers; handles sharing a title go to the same worker (orphan rule safety)."""
    if module:
        handles = [
            h for h, p in state["products"].items()
            if p["status"] == "created"
            and not any(i["module"] == module and i["status"] != "deleted" for i in p["items"])
        ]
    else:
        wanted = {"pending", "failed"} if retry_failed else {"pending"}
        handles = [h for h, p in state["products"].items() if p["status"] in wanted]
    if limit is not None:
        handles = handles[:limit]
    if pilot:
        for h in handles:
            state["products"][h]["pilot"] = True
    groups = {}
    for h in handles:
        title = ((titles or {}).get(h) or h).strip().lower()
        groups.setdefault(title, []).append(h)
    lists = [[] for _ in range(max(workers, 1))]
    for i, group in enumerate(groups.values()):
        lists[i % len(lists)].extend(group)
    return [l for l in lists if l]


def validate_result(line):
    missing = RESULT_KEYS - set(line)
    if missing:
        raise ValueError(f"Result line is missing keys: {sorted(missing)}")
    if line["status"] not in RESULT_STATUSES:
        raise ValueError(f"Result status must be one of {sorted(RESULT_STATUSES)}")
    for item in line["items"]:
        if not {"kind", "iri", "status", "module"} <= set(item):
            raise ValueError("Each item needs kind, iri, status and module")


def append_result(path, line):
    validate_result(line)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")


def merge(state, lines):
    """Apply worker result lines. A line with a "module" key comes from a module-only run:
    it adds items and records a per-module error, without touching the product status."""
    for line in lines:
        product = state["products"].setdefault(line["handle"], _new_product())
        if line.get("module"):
            errors = product.setdefault("module_errors", {})
            if line["status"] == "failed":
                errors[line["module"]] = line["error"]
            else:
                errors.pop(line["module"], None)
        else:
            product["status"] = line["status"]
            product["iri"] = line["iri"] or product["iri"]
            product["error"] = line["error"]
        known = {i["iri"]: i for i in product["items"]}
        for item in line["items"]:
            existing = known.get(item["iri"])
            if existing is None:
                product["items"].append(item)
                known[item["iri"]] = item
            elif existing["status"] == "deleted":
                existing.update(item)
    return state


def _kind_rank(kind):
    if kind.endswith("_link"):
        return KIND_ORDER.index("product") - 0.5
    return KIND_ORDER.index(kind) if kind in KIND_ORDER else len(KIND_ORDER)


def undo_plan(state, scope):
    steps = []
    for handle, product in state["products"].items():
        if scope == "pilot" and not product["pilot"]:
            continue
        for item in product["items"]:
            steps.append({"kind": item["kind"], "iri": item["iri"], "owner": handle,
                          "status": item["status"], "module": item["module"]})
    if scope != "pilot":
        for key, shared in state["shared"].items():
            steps.append({"kind": key.split(":", 1)[0], "iri": shared["iri"], "owner": key,
                          "status": shared["status"], "module": shared["module"]})
    if scope.startswith("module:"):
        module = scope.split(":", 1)[1]
        steps = [s for s in steps if s["module"] == module]
    steps = [s for s in steps if s["status"] == "created"]
    order = {id(s): i for i, s in enumerate(steps)}
    steps.sort(key=lambda s: (_kind_rank(s["kind"]), -order[id(s)]))
    return [{"kind": s["kind"], "iri": s["iri"], "owner": s["owner"]} for s in steps]


def mark_deleted(state, iris):
    iris = set(iris)
    for product in state["products"].values():
        for item in product["items"]:
            if item["iri"] in iris:
                item["status"] = "deleted"
                if item["kind"] == "product":
                    product["status"] = "deleted"
    for shared in state["shared"].values():
        if shared["iri"] in iris:
            shared["status"] = "deleted"
    return state


def reset_deleted(state):
    for product in state["products"].values():
        if product["status"] == "deleted":
            product.update(_new_product())
    return state


def summary(state):
    products = {}
    items = {}
    for product in state["products"].values():
        products[product["status"]] = products.get(product["status"], 0) + 1
        for item in product["items"]:
            counts = items.setdefault(item["kind"], {})
            counts[item["status"]] = counts.get(item["status"], 0) + 1
    for key, shared in state["shared"].items():
        counts = items.setdefault(key.split(":", 1)[0], {})
        counts[shared["status"]] = counts.get(shared["status"], 0) + 1
    failed = [{"handle": h, "error": p["error"]} for h, p in state["products"].items() if p["status"] == "failed"]
    return {"products": products, "items": items, "failed": failed}


def _read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _write_json(path, data):
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _read_results(directory):
    lines, malformed = [], []
    for path in sorted(glob.glob(os.path.join(directory, "results", "*.jsonl"))):
        with open(path, encoding="utf-8") as f:
            for number, raw in enumerate(f, start=1):
                if not raw.strip():
                    continue
                try:
                    line = json.loads(raw)
                    validate_result(line)
                except (ValueError, TypeError, AttributeError) as error:
                    malformed.append(f"{os.path.basename(path)}:{number}: {error}")
                    continue
                lines.append(line)
    return lines, malformed


def _merge_results(directory, state):
    lines, malformed = _read_results(directory)
    merge(state, lines)
    for entry in malformed:
        print(f"skipped malformed result line {entry}")
    return len(lines)


def _archive_results(directory):
    """Move merged result files out of results/ so they are never merged twice."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    archive = os.path.join(directory, "results", "merged")
    for path in glob.glob(os.path.join(directory, "results", "*.jsonl")):
        os.makedirs(archive, exist_ok=True)
        os.replace(path, os.path.join(archive, f"{stamp}-{os.path.basename(path)}"))


def main(argv):
    parser = argparse.ArgumentParser(description="Migration state bookkeeping")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init")
    p.add_argument("dir")
    p.add_argument("--client", required=True)
    p.add_argument("--mcp-url", required=True)

    sub.add_parser("sync").add_argument("dir")

    p = sub.add_parser("shared")
    p.add_argument("dir")
    p.add_argument("--key", required=True)
    p.add_argument("--iri", required=True)
    p.add_argument("--status", required=True, choices=["created", "reused"])
    p.add_argument("--module", required=True)

    p = sub.add_parser("decide")
    p.add_argument("dir")
    p.add_argument("--key", required=True)
    p.add_argument("--value-json", required=True)

    p = sub.add_parser("worklists")
    p.add_argument("dir")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--limit", type=int)
    p.add_argument("--pilot", action="store_true")
    p.add_argument("--retry-failed", action="store_true")
    p.add_argument("--module")

    p = sub.add_parser("result")
    p.add_argument("file")
    p.add_argument("--json", required=True)

    sub.add_parser("merge").add_argument("dir")

    p = sub.add_parser("undo-plan")
    p.add_argument("dir")
    p.add_argument("--scope", required=True)

    p = sub.add_parser("deleted")
    p.add_argument("dir")
    p.add_argument("--iris-file", required=True)

    sub.add_parser("reset-deleted").add_argument("dir")
    sub.add_parser("summary").add_argument("dir")

    args = parser.parse_args(argv[1:])

    if args.command == "result":
        try:
            append_result(args.file, json.loads(args.json))
        except ValueError as error:
            print(f"error: {error}", file=sys.stderr)
            return 1
        return 0

    state_path = os.path.join(args.dir, "state.json")

    if args.command == "init":
        if os.path.exists(state_path):
            print(f"error: {state_path} already exists, resume instead", file=sys.stderr)
            return 1
        state = init(args.client, args.mcp_url, _read_json(os.path.join(args.dir, "products.json")))
        _write_json(state_path, state)
        print(f"{len(state['products'])} products pending")
        return 0

    state = _read_json(state_path)

    if args.command == "sync":
        before = len(state["products"])
        sync_products(state, _read_json(os.path.join(args.dir, "products.json")))
        print(f"{len(state['products']) - before} new products added")
    elif args.command == "shared":
        record_shared(state, args.key, args.iri, args.status, args.module)
    elif args.command == "decide":
        decide(state, args.key, json.loads(args.value_json))
    elif args.command == "worklists":
        merged = _merge_results(args.dir, state)
        _write_json(state_path, state)
        _archive_results(args.dir)
        for path in glob.glob(os.path.join(args.dir, "worklists", "*.json")):
            os.remove(path)
        products_path = os.path.join(args.dir, "products.json")
        titles = {}
        if os.path.exists(products_path):
            titles = {p["handle"]: p.get("title") for p in _read_json(products_path)["products"]}
        lists = worklists(state, args.workers, args.limit, args.pilot, args.retry_failed, args.module, titles)
        for i, handles in enumerate(lists, start=1):
            _write_json(os.path.join(args.dir, "worklists", f"worker-{i}.json"), handles)
        print(json.dumps({"merged_before": merged, "workers": len(lists), "products": sum(len(l) for l in lists)}))
    elif args.command == "merge":
        print(f"{_merge_results(args.dir, state)} result lines merged")
        _write_json(state_path, state)
        _archive_results(args.dir)
        return 0
    elif args.command == "undo-plan":
        print(json.dumps(undo_plan(state, args.scope), indent=2))
        return 0
    elif args.command == "deleted":
        mark_deleted(state, _read_json(args.iris_file))
    elif args.command == "reset-deleted":
        reset_deleted(state)
    elif args.command == "summary":
        print(json.dumps(summary(state), indent=2))
        return 0

    _write_json(state_path, state)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
