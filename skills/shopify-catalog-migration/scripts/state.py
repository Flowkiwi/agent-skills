"""Bookkeeping for a Shopify -> Flowkiwi migration workspace.

Usage:
  python3 state.py init <dir> --client <slug> --mcp-url <url>
  python3 state.py sync <dir>
  python3 state.py shared <dir> --key <kind:name> --iri <iri> --status created|reused --module <module>
  python3 state.py decide <dir> --key <name> --value-json '<json>'
  python3 state.py worklists <dir> --workers 4 [--limit 5] [--pilot] [--handles a,b] [--retry-failed] [--module <name>]
  python3 state.py result <results/worker-N.jsonl> --json '<line>' | --json - | --json-file <file>
  python3 state.py merge <dir>
  python3 state.py undo-plan <dir> --scope all|pilot|module:<name>|shared:<key>
  python3 state.py deleted <dir> --iris-file <file.json>
  python3 state.py reset-deleted <dir>
  python3 state.py summary <dir>
"""

import argparse
import glob
import json
import os
import re
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
ITEM_STATUSES = {"created", "reused"}
VERIFY_STATUSES = {"ok", "fixed", "mismatch"}
IRI_PATTERN = re.compile(
    r"^/api/[a-z0-9_-]+/[0-9a-fA-F-]{36}(/[a-z0-9_-]+/[0-9a-fA-F-]{36})*(#[a-z_]+(-[A-Za-z0-9_-]+)?)?$"
)


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


def _needs_module(product, module):
    """No live item of the module, or a deleted one tied to a shared element (e.g. a metafield definition)
    that has no live replacement yet."""
    items = [i for i in product["items"] if i["module"] == module]
    live = [i for i in items if i["status"] != "deleted"]
    if not live:
        return True
    live_shared = {i.get("shared") for i in live}
    return any(i["status"] == "deleted" and i.get("shared") and i["shared"] not in live_shared for i in items)


def _features(product, longest):
    features = {"multi_variant" if len(product.get("variants", [])) > 1 else "single_variant"}
    features.add("category" if product.get("product_category") or product.get("type") else "no_category")
    if any(v not in (None, "") for v in product.get("metafields", {}).values()):
        features.add("metafields")
    features.add(f"status:{product.get('status')}")
    if product["handle"] == longest:
        features.add("longest_description")
    return features


def pilot_selection(products, candidates, limit):
    """Representative pilot: greedily cover multi/single variant, with/without category, metafields,
    each source status and the longest description, then fill in order."""
    eligible = set(candidates)
    pool = [p for p in products if p["handle"] in eligible]
    longest = max(pool, key=lambda p: len(p.get("description_html") or ""), default=None)
    longest = longest["handle"] if longest and longest.get("description_html") else None
    features = {p["handle"]: _features(p, longest) for p in pool}
    wanted = ["multi_variant", "single_variant", "category", "no_category", "metafields"]
    wanted += sorted({f for fs in features.values() for f in fs if f.startswith("status:")})
    wanted.append("longest_description")
    selected, covered = [], set()
    for feature in wanted:
        if len(selected) >= limit:
            break
        if feature in covered:
            continue
        options = [h for h in features if feature in features[h] and h not in selected]
        if options:
            best = max(options, key=lambda h: (len(features[h] - covered), -options.index(h)))
            selected.append(best)
            covered |= features[best]
    for handle in candidates:
        if len(selected) >= limit:
            break
        if handle not in selected:
            selected.append(handle)
    return selected


def worklists(state, workers, limit, pilot, retry_failed=False, module=None, titles=None, handles=None,
              products=None):
    """Split handles between workers; handles sharing a title go to the same worker (orphan rule safety).
    `handles` is an explicit selection, `products` (products.json entries) enables the representative pilot."""
    if module:
        candidates = [h for h, p in state["products"].items() if p["status"] == "created" and _needs_module(p, module)]
    else:
        wanted = {"pending", "failed"} if retry_failed else {"pending"}
        candidates = [h for h, p in state["products"].items() if p["status"] in wanted]
    if pilot and limit is None:
        limit = 5
    if handles:
        eligible = set(candidates)
        for handle in handles:
            if handle not in state["products"]:
                raise ValueError(f"unknown handle '{handle}'")
            if handle not in eligible:
                status = state["products"][handle]["status"]
                hint = f"no {module} item missing" if module else "use --retry-failed for failed products"
                raise ValueError(f"handle '{handle}' is {status}, not eligible ({hint})")
        selection = list(dict.fromkeys(handles))
    elif pilot and products:
        selection = pilot_selection(products, candidates, limit)
    else:
        selection = candidates
    handles = selection[:limit] if limit is not None else selection
    if pilot:
        for product in state["products"].values():
            if product["status"] != "created":
                product["pilot"] = False
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


def _check_iri(iri, link=False):
    """IRIs are /api/<resource>/<uuid>[/<sub>/<uuid>]; only *_link items carry a #relation suffix."""
    if not isinstance(iri, str) or not IRI_PATTERN.match(iri):
        raise ValueError(f"Invalid IRI {iri!r}: expected /api/<resource>/<uuid> exactly as returned by the API")
    if link != ("#" in iri):
        raise ValueError(f"IRI {iri!r}: a #relation suffix is required for *_link items and forbidden otherwise")


def validate_result(line):
    if not isinstance(line, dict):
        raise ValueError("Result line must be a JSON object")
    missing = RESULT_KEYS - set(line)
    if missing:
        raise ValueError(f"Result line is missing keys: {sorted(missing)}")
    if not isinstance(line["handle"], str) or not line["handle"]:
        raise ValueError("handle must be a non-empty string")
    if line["status"] not in RESULT_STATUSES:
        raise ValueError(f"Result status must be one of {sorted(RESULT_STATUSES)}")
    if line["status"] == "created" and not line["iri"]:
        raise ValueError("iri is required when status is created")
    if line["iri"] is not None:
        _check_iri(line["iri"])
    if line.get("verify") is not None and line["verify"] not in VERIFY_STATUSES:
        raise ValueError(f"verify must be one of {sorted(VERIFY_STATUSES)}")
    if not isinstance(line["items"], list):
        raise ValueError("items must be a list")
    for item in line["items"]:
        if not isinstance(item, dict) or not {"kind", "iri", "status", "module"} <= set(item):
            raise ValueError("Each item needs kind, iri, status and module")
        if item["status"] not in ITEM_STATUSES:
            raise ValueError(f"Item status must be one of {sorted(ITEM_STATUSES)}")
        _check_iri(item["iri"], link=str(item["kind"]).endswith("_link"))
        if item.get("shared") is not None and (not isinstance(item["shared"], str) or ":" not in item["shared"]):
            raise ValueError("Item shared must be a shared key like metafield_definition:custom.material")


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
        if line.get("verify"):
            product["verify"] = line["verify"]
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


def check_scope(scope):
    kind, _, name = scope.partition(":")
    if scope in ("all", "pilot") or (kind in ("module", "shared") and name):
        return scope
    raise ValueError(f"Unknown undo scope '{scope}': use all, pilot, module:<name> or shared:<key>")


def undo_plan(state, scope):
    check_scope(scope)
    steps = []
    for handle, product in state["products"].items():
        if scope == "pilot" and not product["pilot"]:
            continue
        for item in product["items"]:
            steps.append({"kind": item["kind"], "iri": item["iri"], "owner": handle, "status": item["status"],
                          "module": item["module"], "shared": item.get("shared")})
    if scope != "pilot":
        for key, shared in state["shared"].items():
            steps.append({"kind": key.split(":", 1)[0], "iri": shared["iri"], "owner": key,
                          "status": shared["status"], "module": shared["module"], "shared": key})
    if scope.startswith("module:"):
        module = scope.split(":", 1)[1]
        steps = [s for s in steps if s["module"] == module]
    if scope.startswith("shared:"):
        key = scope.split(":", 1)[1]
        steps = [s for s in steps if s["shared"] == key]
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
    verify = {}
    for product in state["products"].values():
        if product.get("verify"):
            verify[product["verify"]] = verify.get(product["verify"], 0) + 1
    mismatch = [h for h, p in state["products"].items() if p.get("verify") == "mismatch"]
    return {"products": products, "items": items, "failed": failed, "verify": verify, "verify_mismatch": mismatch}


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


def check_results(entries, worklists=None, known=None):
    """Filter valid result entries [{"file", "line", "result"}] before merging.

    A line from worker-<n> whose handle is not in worklists["worker-<n>"] (when given) is rejected, as is
    a handle unknown to the state. A handle found in several result files: when exactly one product IRI
    was created, the files that created it are kept and the others rejected; conflicting created IRIs
    reject every line of that handle. Returns (accepted results, rejected entries with a reason, notes)."""
    worklists = worklists or {}
    accepted, rejected, notes = [], [], []
    for entry in entries:
        handle, source = entry["result"]["handle"], entry["file"]
        if known is not None and handle not in known:
            rejected.append(dict(entry, reason=f"unknown handle '{handle}'"))
        elif source in worklists and handle not in worklists[source]:
            rejected.append(dict(entry, reason=f"handle '{handle}' is not in worklists/{source}.json"))
        else:
            accepted.append(entry)

    groups = {}
    for entry in accepted:
        groups.setdefault((entry["result"]["handle"], entry["result"].get("module")), []).append(entry)
    dropped = set()
    for (handle, module), group in groups.items():
        sources = sorted({e["file"] for e in group})
        if len(sources) < 2:
            continue
        label = handle + (f" (module {module})" if module else "")
        created = [e for e in group if e["result"]["status"] == "created"]
        iris = {e["result"]["iri"] for e in created}
        if len(iris) > 1:
            reason = f"conflicting created IRIs for '{label}' in {', '.join(sources)}"
            losers = group
        elif created:
            winners = {e["file"] for e in created}
            reason = f"'{label}' was created by {', '.join(sorted(winners))}"
            losers = [e for e in group if e["file"] not in winners]
        else:
            notes.append(f"'{label}' failed in several result files: {', '.join(sources)}")
            continue
        notes.append(reason)
        for entry in losers:
            dropped.add(id(entry))
            rejected.append(dict(entry, reason=reason))
    return [e["result"] for e in accepted if id(e) not in dropped], rejected, notes


def _result_files(directory):
    return [
        path for path in sorted(glob.glob(os.path.join(directory, "results", "*.jsonl")))
        if not os.path.basename(path).startswith("rejected-")
    ]


def _read_results(directory):
    entries, malformed = [], []
    for path in _result_files(directory):
        source = os.path.basename(path)[:-len(".jsonl")]
        with open(path, encoding="utf-8") as f:
            for number, raw in enumerate(f, start=1):
                if not raw.strip():
                    continue
                try:
                    line = json.loads(raw)
                    validate_result(line)
                except (ValueError, TypeError, AttributeError) as error:
                    malformed.append({"file": source, "line": number, "reason": f"malformed: {error}",
                                      "raw": raw.rstrip("\n")})
                    continue
                entries.append({"file": source, "line": number, "result": line})
    return entries, malformed


def _read_worklists(directory):
    worklists = {}
    for path in glob.glob(os.path.join(directory, "worklists", "worker-*.json")):
        worklists[os.path.basename(path)[:-len(".json")]] = set(_read_json(path))
    return worklists


def _merge_results(directory, state):
    """Merge result files, using the worklists present now (before any are replaced)."""
    entries, malformed = _read_results(directory)
    lines, rejected, notes = check_results(entries, _read_worklists(directory), set(state["products"]))
    merge(state, lines)
    rejected = malformed + rejected
    for note in notes:
        print(f"note: {note}")
    if rejected:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
        path = os.path.join(directory, "results", f"rejected-{stamp}.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            for entry in rejected:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        for entry in rejected:
            print(f"rejected result line {entry['file']}:{entry['line']}: {entry['reason']}")
        print(f"{len(rejected)} rejected lines written to {path}")
    return len(lines)


def _archive_results(directory):
    """Move merged result files out of results/ so they are never merged twice."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    archive = os.path.join(directory, "results", "merged")
    for path in _result_files(directory):
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
    p.add_argument("--handles", help="comma-separated explicit selection")

    p = sub.add_parser("result")
    p.add_argument("file")
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument("--json", help="the line, or - to read it from stdin")
    source.add_argument("--json-file")

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
            if args.json_file:
                with open(args.json_file, encoding="utf-8") as f:
                    raw = f.read()
            else:
                raw = sys.stdin.read() if args.json == "-" else args.json
            append_result(args.file, json.loads(raw))
        except (OSError, ValueError) as error:
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
        products_path = os.path.join(args.dir, "products.json")
        products = _read_json(products_path)["products"] if os.path.exists(products_path) else []
        titles = {p["handle"]: p.get("title") for p in products}
        selection = [h.strip() for h in args.handles.split(",") if h.strip()] if args.handles else None
        try:
            lists = worklists(state, args.workers, args.limit, args.pilot, args.retry_failed, args.module, titles,
                              selection, products)
        except ValueError as error:
            print(f"error: {error}", file=sys.stderr)
            return 1
        for path in glob.glob(os.path.join(args.dir, "worklists", "*.json")):
            os.remove(path)
        for i, handles in enumerate(lists, start=1):
            _write_json(os.path.join(args.dir, "worklists", f"worker-{i}.json"), handles)
        print(json.dumps({"merged_before": merged, "workers": len(lists), "products": sum(len(l) for l in lists)}))
    elif args.command == "merge":
        print(f"{_merge_results(args.dir, state)} result lines merged")
        _write_json(state_path, state)
        _archive_results(args.dir)
        return 0
    elif args.command == "undo-plan":
        try:
            print(json.dumps(undo_plan(state, args.scope), indent=2))
        except ValueError as error:
            print(f"error: {error}", file=sys.stderr)
            return 1
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
