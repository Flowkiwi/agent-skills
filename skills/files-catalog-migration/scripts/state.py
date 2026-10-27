"""Bookkeeping for a files -> Flowkiwi migration workspace.

Usage:
  python3 state.py init <dir> --client <slug> --mcp-url <url>
  python3 state.py sync <dir>
  python3 state.py shared <dir> --key <kind:name> --iri <iri> --status created|reused --module <module> [--spec-json '<json>']
  python3 state.py shared <dir> --file <shared.json>   # [{"key", "iri", "status", "module", "spec"?}, ...]
  python3 state.py decide <dir> --key <name> --value-json '<json>' | --value-file <file.json>
                          --origin developer|agent|client [--reason "<text in the data language>"]
  python3 state.py worklists <dir> --workers 4 [--limit 5] [--pilot] [--handles a,b] [--retry-failed] [--module <name>]
  python3 state.py worklists <dir> --workers 4 --update [--retry-failed] [--handles a,b]   # reads diff.json
  python3 state.py result <results/worker-N.jsonl> --json '<line>' | --json - | --json-file <file>
  python3 state.py merge <dir>
  python3 state.py phase <dir> [--start]
  python3 state.py undo-plan <dir> --scope all|pilot|module:<name>|shared:<key>|product:<handle>
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
    "collection", "metaobject", "attribute_option", "attribute_definition", "category", "supplier", "tax",
    "metafield_definition", "metaobject_definition", "channel", "country",
]
ORIGINS = ("developer", "agent", "client")
RESULT_KEYS = {"handle", "status", "iri", "items", "error"}
RESULT_STATUSES = {"created", "failed", "updated", "archived"}
ITEM_STATUSES = {"created", "reused"}
VERIFY_STATUSES = {"ok", "fixed", "mismatch"}
IRI_PATTERN = re.compile(
    r"^/api/[a-z0-9_-]+/[0-9a-fA-F-]{36}(/[a-z0-9_-]+/[0-9a-fA-F-]{36})*(#[a-z_]+(-[A-Za-z0-9_-]+)?)?$"
)


def _new_product():
    return {"status": "pending", "iri": None, "pilot": False, "error": None, "items": []}


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def init(client, mcp_url, products):
    started_at = _now()
    return {
        "version": 1,
        "source": "files",
        "phase": 1,
        "phases": [{"n": 1, "started_at": started_at}],
        "client": client,
        "mcp_url": mcp_url,
        "started_at": started_at,
        "decisions": {},
        "decision_meta": {},
        "shared": {},
        "products": {p["handle"]: _new_product() for p in products["products"]},
    }


def _has_created_items(product):
    return any(i.get("status") == "created" for i in product["items"])


def sync_products(state, products):
    """New handles become pending, skipped ones that reappear too; handles gone from products.json are
    skipped when pending, or failed without any created item (a live product must be undone instead).
    A created or archived handle gone from products.json is flagged `removed` (cleared when it comes back);
    a deleted one never is."""
    handles = {p["handle"] for p in products["products"]}
    for handle in handles:
        product = state["products"].setdefault(handle, _new_product())
        product.pop("removed", None)
        if product["status"] == "skipped":
            product["status"] = "pending"
    for handle, product in state["products"].items():
        if handle in handles:
            continue
        if product["status"] in ("created", "archived"):
            product["removed"] = True
        elif product["status"] == "pending" or (product["status"] == "failed" and not _has_created_items(product)):
            product["status"] = "skipped"
    return state


def start_phase(state):
    """Start the next phase: decisions recorded from now on belong to it."""
    phases = state.setdefault("phases", [{"n": state.get("phase", 1), "started_at": state.get("started_at")}])
    state["phase"] = state.get("phase", 1) + 1
    phases.append({"n": state["phase"], "started_at": _now()})
    return state["phase"]


def phase_decision(state, key):
    """The decision value when it was taken for the current phase, else None: the `phase` of its
    decision_meta and, for a value carrying one, its own `phase` must equal the state phase."""
    if key not in state.get("decisions", {}):
        return None
    value = state["decisions"][key]
    current = state.get("phase", 1)
    meta = state.get("decision_meta", {}).get(key)
    if meta is not None and meta.get("phase", 1) != current:
        return None
    if isinstance(value, dict) and "phase" in value and value["phase"] != current:
        return None
    return value


def forced(state, handle):
    """True when decision force_update of the current phase names the handle (or is "all")."""
    value = phase_decision(state, "force_update")
    if not isinstance(value, dict):
        return False
    handles = value.get("handles")
    return handles == "all" or (isinstance(handles, list) and handle in handles)


def record_shared(state, key, iri, status, module, spec=None):
    entry = {"iri": iri, "status": status, "module": module}
    if spec is not None:
        if not isinstance(spec, dict):
            raise ValueError("Shared spec must be a JSON object")
        entry["spec"] = spec
    state["shared"][key] = entry
    return state


def record_shared_entries(state, entries):
    """Record many shared elements at once; every entry is checked before any is recorded."""
    if not isinstance(entries, list):
        raise ValueError("The shared file must hold a JSON array")
    for entry in entries:
        if not isinstance(entry, dict) or not {"key", "iri", "status", "module"} <= set(entry):
            raise ValueError(f"Each shared entry needs key, iri, status and module: {entry!r}")
        if entry["status"] not in ITEM_STATUSES or ":" not in str(entry["key"]) or not entry["iri"]:
            raise ValueError(f"Invalid shared entry {entry!r}: status created|reused, key <kind>:<name>, iri")
        if entry.get("spec") is not None and not isinstance(entry["spec"], dict):
            raise ValueError(f"Invalid shared entry {entry!r}: spec must be a JSON object")
    for entry in entries:
        record_shared(state, entry["key"], entry["iri"], entry["status"], entry["module"], entry.get("spec"))
    return state


def decide(state, key, value, origin, reason=None):
    if origin not in ORIGINS:
        raise ValueError(f"Unknown decision origin {origin!r}: use one of {', '.join(ORIGINS)}")
    state["decisions"][key] = value
    state.setdefault("decision_meta", {})[key] = {
        "origin": origin,
        "reason": reason,
        "phase": state.get("phase", 1),
        "at": _now(),
    }
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
    if product.get("description_origin") == "generated":
        features.add("generated_description")
    features.add(f"status:{product.get('status')}")
    if product["handle"] == longest:
        features.add("longest_description")
    return features


def pilot_selection(products, candidates, limit):
    """Representative pilot: greedily cover multi/single variant, with/without category, metafields,
    generated description, each source status and the longest description, then fill in order."""
    eligible = set(candidates)
    pool = [p for p in products if p["handle"] in eligible]
    longest = max(pool, key=lambda p: len(p.get("description_html") or ""), default=None)
    longest = longest["handle"] if longest and longest.get("description_html") else None
    features = {p["handle"]: _features(p, longest) for p in pool}
    wanted = ["multi_variant", "single_variant", "category", "no_category", "metafields", "generated_description"]
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


PLACEHOLDER = re.compile(r"^<(.+) IRI>$")


def _resolved(value, shared):
    """A diff.json value with its `<key IRI>` placeholders replaced by the shared IRIs recorded since."""
    if isinstance(value, str):
        match = PLACEHOLDER.match(value)
        entry = shared.get(match.group(1)) if match else None
        return entry["iri"] if entry and entry.get("iri") else value
    if isinstance(value, list):
        return [_resolved(v, shared) for v in value]
    if isinstance(value, dict):
        return {k: _resolved(v, shared) for k, v in value.items()}
    return value


def _change_applied(written, change, shared):
    """True when the written snapshot already holds every change of a diff.json `changed` entry: an update
    merged since diff.py ran (a deferred variant is left out of the snapshot, so it is still to do)."""
    import diff  # function-level: diff imports state

    change = _resolved(change, shared)
    body = (written.get("product") or {}).get("body") or {}
    if any(diff._view(name, body.get(name)) != diff._view(name, new)
           for name, (_, new) in change.get("product", {}).items()):
        return False
    now = {
        "options": {o["title"]: [v["value"] for v in o["values"]] for o in written.get("options") or []},
        "variants": {v["sku"]: v for v in written.get("variants") or []},
        "metafields": {e["key"]: e["body"].get("value") for e in written.get("metafields") or []},
        "prices": {f"{e['sku']}:{e['country']}": e["body"] for e in written.get("prices") or []},
    }
    for group, entries in now.items():
        part = change.get(group, {})
        if any(k not in entries for k in part.get("added", [])) or any(k in entries for k in part.get("removed", [])):
            return False
    options = change.get("options", {})
    if any(v not in now["options"].get(t, []) for t, values in options.get("values_added", {}).items() for v in values):
        return False
    if any(v in now["options"].get(t, []) for t, values in options.get("values_removed", {}).items() for v in values):
        return False
    for sku, fields in change.get("variants", {}).get("changed", {}).items():
        if any(now["variants"].get(sku, {}).get(name) != new for name, (_, new) in fields.items()):
            return False
    return all(now[group].get(key) == new for group in ("metafields", "prices")
               for key, (_, new) in change.get(group, {}).get("changed", {}).items())


def _gives_away(product, skus):
    """SKUs of `skus` that are still a created variant of the product."""
    return [s for s in skus if any(i["kind"] == "variant" and i.get("ref") == s and i.get("status") == "created"
                                   for i in product["items"])]


def update_selection(state, diff_data, retry_failed=False, skipped=None):
    """The next pass of an update run from diff.json: ("deletions", handles) while rekeyed products are to delete
    or products still hold a SKU moving to another one (they are merged before the receivers are updated or
    created), else ("updates", handles): the changed handles, then the removed ones. Also returns the handles
    whose action is a deletion. Never selected (in `skipped` with the reason): unmanaged handles, unforced
    conflicts, a deletion whose receiver has an unforced conflict, a failed update without retry_failed, a
    product waiting for a SKU still held by another one; handles already done in this phase are left out."""
    import payload  # function-level: payload imports state

    phase = state.get("phase", 1)
    if diff_data is None:
        raise ValueError("--update needs diff.json: run diff.py <dir> first")
    if diff_data.get("phase") != phase:
        raise ValueError(f"diff.json is from phase {diff_data.get('phase')}, run diff.py again")
    skipped = {} if skipped is None else skipped
    entries = state["products"]
    unmanaged = set(diff_data.get("unmanaged") or [])
    conflicts = diff_data.get("conflicts") or {}
    removal = diff_data.get("removal") or {}
    changed = diff_data.get("changed") or {}

    def conflicted(handle):
        return handle in conflicts and not forced(state, handle)

    def eligible(handle):
        product = entries.get(handle)
        if product is None or product["status"] == "deleted":
            return False
        if handle in unmanaged:
            skipped[handle] = "unmanaged (reused): never written"
        elif conflicted(handle):
            skipped[handle] = ("modified in Flowkiwi since the last import (diff.json conflicts): "
                               "force_update to update it")
        elif product.get("error") and not retry_failed:
            skipped[handle] = f"failed: {product['error']} (--retry-failed to try again)"
        else:
            return True
        return False

    def receivers_conflicted(handle, receivers):
        stuck = [r for r in receivers if conflicted(r)]
        if stuck:
            skipped[handle] = (f"gives its SKUs to {', '.join(stuck)}, modified in Flowkiwi since the last import: "
                               "force_update or resolve the conflict first")
        return bool(stuck)

    first, updates, deletes = [], [], set()
    moved = {}
    for entry in diff_data.get("moved") or []:
        moved.setdefault(entry["from"], []).append(entry)
    for handle, change in changed.items():
        if not eligible(handle):
            continue
        product = entries[handle]
        giving = [m for m in moved.get(handle, []) if _gives_away(product, [m["sku"]])]
        if giving:
            if not receivers_conflicted(handle, [r for m in giving for r in m.get("to") or []]):
                first.append(handle)
            continue
        if product.get("updated_phase") == phase:
            written = (product.get("written") or {}).get("payload") or {}
            if _change_applied(written, change, state.get("shared", {})):
                continue
            held = [(sku, payload._held_elsewhere(state, handle, sku))
                    for sku in change.get("variants", {}).get("added", [])]
            held = [f"SKU {sku} held by {other}" for sku, other in held if other]
            if held:
                skipped[handle] = f"waits for {', '.join(held)} (deferred creation)"
                continue
        updates.append(handle)

    action = None
    for handle in list(diff_data.get("removed") or []) + list(diff_data.get("already_archived") or []):
        if not eligible(handle):
            continue
        status = entries[handle]["status"]
        kind = removal.get(handle, "removed_action")
        if kind == "delete_rekeyed":
            receivers = [r for e in diff_data.get("rekeyed") or [] if e.get("removed") == handle
                         for r in e.get("changed") or []]
            if receivers_conflicted(handle, receivers):
                continue
            if phase_decision(state, "confirmed_rekey") is None:
                raise ValueError(f"rekeyed products need decision confirmed_rekey for phase {phase}")
            first.append(handle)
            deletes.add(handle)
        elif kind == "archive_blocked":
            if status == "created":
                updates.append(handle)
        else:
            decision = phase_decision(state, "removed_action")
            action = decision.get("action") if isinstance(decision, dict) else None
            if action not in ("archive", "delete"):
                if status != "created":
                    continue
                raise ValueError(f"removed products need decision removed_action for phase {phase}")
            if status == "created" or action == "delete":
                updates.append(handle)
                if action == "delete":
                    deletes.add(handle)
    if first:
        return "deletions", first, deletes
    return "updates", updates, deletes


def _creation_holders(state, candidates, products):
    """In a later phase, a product to create whose SKU is still a created variant of another product waits for
    the update run that deletes it (rekeyed or moved SKUs)."""
    import payload  # function-level: payload imports state

    waiting = {}
    for p in products:
        if p["handle"] not in candidates:
            continue
        held = [(v["sku"], payload._held_elsewhere(state, p["handle"], v["sku"])) for v in p.get("variants") or []]
        held = [f"SKU {sku} held by {other}" for sku, other in held if other]
        if held:
            waiting[p["handle"]] = f"waits for {', '.join(held)}: run state.py worklists --update first"
    return waiting


def worklists(state, workers, limit, pilot, retry_failed=False, module=None, titles=None, handles=None,
              products=None, update=False, diff_data=None, report=None):
    """Split handles between workers; handles sharing a title go to the same worker (orphan rule safety).
    `handles` is an explicit selection, `products` (products.json entries) enables the representative pilot.
    `update` selects the next pass of an update run from `diff_data` (update_selection); its deletions all go
    to one worker (each `state.py deleted` writes the state). `report` (a dict) receives `skipped`
    ({handle: reason}) and, for an update run, `pass`."""
    skipped = {}
    deletes = set()
    if update:
        if pilot or module:
            raise ValueError("--pilot and --module do not apply to --update")
        name, candidates, deletes = update_selection(state, diff_data, retry_failed, skipped)
        if report is not None:
            report["pass"] = name
    elif module:
        candidates = [h for h, p in state["products"].items() if p["status"] == "created" and _needs_module(p, module)]
    else:
        wanted = {"pending", "failed"} if retry_failed else {"pending"}
        candidates = [h for h, p in state["products"].items() if p["status"] in wanted]
        if products and state.get("phase", 1) > 1:
            skipped.update(_creation_holders(state, set(candidates), products))
            candidates = [h for h in candidates if h not in skipped]
    if report is not None:
        report["skipped"] = skipped
    if pilot and limit is None:
        limit = 5
    if handles:
        eligible = set(candidates)
        for handle in handles:
            if handle not in state["products"]:
                raise ValueError(f"unknown handle '{handle}'")
            if handle not in eligible:
                status = state["products"][handle]["status"]
                if handle in skipped:
                    hint = skipped[handle]
                elif update:
                    hint = "nothing to do in this pass of the update run"
                else:
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
        if h in deletes:
            groups.setdefault(None, []).append(h)
    for h in handles:
        if h not in deletes:
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
    if line["status"] != "failed" and not line["iri"]:
        raise ValueError(f"iri is required when status is {line['status']}")
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
        if item.get("ref") is not None and (not isinstance(item["ref"], str) or not item["ref"]):
            raise ValueError("Item ref must be a non-empty string (SKU, option title, <option>:<value> or namespace.key)")
    if line.get("deleted") is not None:
        if not isinstance(line["deleted"], list):
            raise ValueError("deleted must be a list of the IRIs deleted by the run")
        for iri in line["deleted"]:
            _check_iri(iri, link=isinstance(iri, str) and "#" in iri)


def append_result(path, line):
    validate_result(line)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")


def _module_part(payload, written, products, state, handle, module):
    """Refresh in `written` (a copy of the previous payload) only the part a module-only run wrote."""
    product = next((p for p in products.get("products", []) if p["handle"] == handle), None)
    if product is None:
        raise payload.PayloadError(f"handle '{handle}' is not in products.json")
    if module == "taxonomy":
        body = written["product"]["body"]
        body.pop("category", None)
        body.pop("attributes", None)
        body.update(payload.taxonomy_fields(product, state))
    elif module == "collections":
        body = written["product"]["body"]
        body.pop("collections", None)
        iris = payload.collection_iris(product, state)
        if iris:
            body["collections"] = iris
    elif module in ("metafields", "metaobjects"):
        def is_reference(entry):
            return state.get("shared", {}).get(entry.get("shared") or "", {}).get("module") == "metaobjects"

        fresh = payload.metafields(product, state)
        kept = [e for e in written.get("metafields", []) if is_reference(e) == (module == "metafields")]
        part = [e for e in fresh if is_reference(e) == (module == "metaobjects")]
        regular, references = (part, kept) if module == "metafields" else (kept, part)
        written["metafields"] = regular + references
    elif module == "prices":
        written["prices"] = payload.sale_offers(product, state)
    return written


def _keep_reused(built, previous, product):
    """An update run never writes a reused child: its entries of the snapshot stay as previously written
    (gone from the payload now included), so the next diff still lists them unmanaged."""
    reused = {(i["kind"], i["ref"]) for i in product["items"] if i.get("status") == "reused" and i.get("ref")}
    if not reused:
        return built

    def keep(kind, new, old, key):
        before = {key(e): e for e in old or []}
        after = {key(e) for e in new or []}
        result = [before.get(key(e), e) if (kind, key(e)) in reused else e for e in new or []]
        return result + [e for k, e in before.items() if (kind, k) in reused and k not in after]

    for name, kind, key in (("variants", "variant", lambda e: e["sku"]),
                            ("metafields", "metafield", lambda e: e["key"]),
                            ("prices", "sale_offer", lambda e: f"{e['sku']}:{e['country']}")):
        if name in built:
            built[name] = keep(kind, built[name], previous.get(name), key)
    if "options" in built:
        old_options = {o["title"]: o for o in previous.get("options") or []}
        options = keep("product_option", built["options"], previous.get("options"), lambda o: o["title"])
        for option in options:
            title = option["title"]
            if ("product_option", title) not in reused or title not in old_options:
                old_values = (old_options.get(title) or {}).get("values")
                option["values"] = keep("option_value", option["values"], old_values,
                                        lambda v, t=title: f"{t}:{v['value']}")
        built["options"] = options
    return built


def _leave_out_unwritten(built, previous, product):
    """An update run may defer the creation of a variant (its SKU still held by another product): a variant with
    no live item and not written before was not written, nor its prices and the option values only it uses, so
    the next diff still finds them to create."""
    live = {(i["kind"], i["ref"]) for i in product["items"]
            if i.get("status") in ("created", "reused") and i.get("ref")}
    before = {v["sku"] for v in previous.get("variants") or []}
    unwritten = {v["sku"] for v in built.get("variants") or []
                 if v["sku"] not in before and ("variant", v["sku"]) not in live}
    if not unwritten:
        return built
    built["variants"] = [v for v in built["variants"] if v["sku"] not in unwritten]
    if "prices" in built:
        built["prices"] = [p for p in built["prices"] if p["sku"] not in unwritten]
    old_values = {(o["title"], v["value"]) for o in previous.get("options") or [] for v in o["values"]}
    for index, option in enumerate(built.get("options") or []):
        used = {v["option_values"][index] for v in built["variants"] if index < len(v["option_values"])}
        option["values"] = [v for v in option["values"]
                            if v["value"] in used or (option["title"], v["value"]) in old_values
                            or ("option_value", f"{option['title']}:{v['value']}") in live]
    return built


def _snapshot(state, products, handle, line):
    """Record what was written for the product: its payload as payload.py prints it now. An archive run only
    wrote the status: the previous snapshot is kept with status ARCHIVED (the handle is gone from products.json).
    A module-only run only refreshes its part of the previous snapshot (none recorded: none written). An update
    run keeps the previous entries of the reused children it never wrote and leaves out its deferred variants."""
    import payload  # function-level: payload imports state

    product = state["products"][handle]
    previous = (product.get("written") or {}).get("payload")
    if line["status"] == "archived" or line.get("module"):
        if previous is None:
            return
        written = json.loads(json.dumps(previous))
    if line["status"] == "archived":
        written.get("product", {}).get("body", {})["status"] = "/api/statuses/ARCHIVED"
        product["written"] = {"phase": state.get("phase", 1), "payload": written}
        return
    try:
        if line.get("module"):
            built = _module_part(payload, written, products, state, handle, line["module"])
        else:
            built = payload.build(products, state, handle)
            if line["status"] == "updated" and previous is not None:
                built = _leave_out_unwritten(_keep_reused(built, previous, product), previous, product)
    except (payload.PayloadError, KeyError, TypeError, ValueError) as error:
        print(f"warning: no written snapshot for {handle}: {error}", file=sys.stderr)
        return
    product["written"] = {"phase": state.get("phase", 1), "payload": built}


def merge(state, lines, products=None):
    """Apply worker result lines. A line with a "module" key comes from a module-only run:
    it adds items and records a per-module error, without touching the product status.
    An item tied to a recorded shared element takes that element's module (a reference metafield: metaobjects).
    `updated` keeps the product created (updated_phase), `archived` archives it, a failed line on a product
    already created or archived (an update run) only records the error. `deleted` IRIs mark those items deleted.
    With `products` (products.json), every non-failed line records the `written` snapshot of the product."""
    for line in lines:
        for item in line["items"]:
            shared = state.get("shared", {}).get(item.get("shared") or "")
            if shared and shared.get("module"):
                item["module"] = shared["module"]
        product = state["products"].setdefault(line["handle"], _new_product())
        if line.get("module"):
            errors = product.setdefault("module_errors", {})
            if line["status"] == "failed":
                errors[line["module"]] = line["error"]
            else:
                errors.pop(line["module"], None)
        else:
            live = product["status"] in ("created", "archived")
            if line["status"] == "updated":
                product["status"] = "created"
                product["updated_phase"] = state.get("phase", 1)
            elif not (line["status"] == "failed" and live):
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
        _mark_items_deleted(product, set(line.get("deleted") or []))
        if products is not None and line["status"] != "failed" and product["status"] != "deleted":
            _snapshot(state, products, line["handle"], line)
    return state


def _kind_rank(kind):
    if kind.endswith("_link"):
        return KIND_ORDER.index("product") - 0.5
    return KIND_ORDER.index(kind) if kind in KIND_ORDER else len(KIND_ORDER)


MARKER_KEY = "metafield_definition:import.key"


def check_scope(scope):
    kind, _, name = scope.partition(":")
    if kind == "shared" and name == MARKER_KEY:
        raise ValueError(f"Undo scope '{scope}' would delete the marker definition and every import.key marker: "
                         "no product could be found again by marker. To remove everything, use all or module:core")
    if scope in ("all", "pilot") or (kind in ("module", "shared", "product") and name):
        return scope
    raise ValueError(f"Unknown undo scope '{scope}': use all, pilot, module:<name>, shared:<key> or product:<handle>")


def undo_plan(state, scope):
    check_scope(scope)
    kind, _, name = scope.partition(":")
    if kind == "product" and name not in state["products"]:
        raise ValueError(f"unknown handle '{name}'")
    steps = []
    for handle, product in state["products"].items():
        if scope == "pilot" and not product["pilot"]:
            continue
        if kind == "product" and handle != name:
            continue
        for item in product["items"]:
            steps.append({"kind": item["kind"], "iri": item["iri"], "owner": handle, "status": item["status"],
                          "module": item["module"], "shared": item.get("shared")})
    if scope != "pilot" and kind != "product":
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


def _mark_items_deleted(product, iris):
    """A deleted product item deletes the product, which is never flagged removed again."""
    for item in product["items"]:
        if item["iri"] in iris:
            item["status"] = "deleted"
            if item["kind"] == "product":
                product["status"] = "deleted"
                product.pop("removed", None)


def mark_deleted(state, iris):
    iris = set(iris)
    for product in state["products"].values():
        _mark_items_deleted(product, iris)
    for shared in state["shared"].values():
        if shared["iri"] in iris:
            shared["status"] = "deleted"
    return state


def reset_deleted(state):
    for product in state["products"].values():
        if product["status"] == "deleted":
            for key in ("written", "updated_phase", "removed"):
                product.pop(key, None)
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
    decisions = {origin: 0 for origin in ORIGINS}
    for meta in state.get("decision_meta", {}).values():
        if meta.get("origin") in decisions:
            decisions[meta["origin"]] += 1
    removed = sum(1 for p in state["products"].values() if p.get("removed"))
    return {"phase": state.get("phase", 1), "products": products, "items": items, "failed": failed,
            "verify": verify, "verify_mismatch": mismatch, "decisions": decisions, "removed": removed}


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


def _has_lines(path):
    with open(path, encoding="utf-8") as f:
        return any(raw.strip() for raw in f)


def _read_worklists(directory):
    worklists = {}
    for path in glob.glob(os.path.join(directory, "worklists", "worker-*.json")):
        worklists[os.path.basename(path)[:-len(".json")]] = set(_read_json(path))
    return worklists


def _merge_results(directory, state):
    """Merge result files, using the worklists present now (before any are replaced)."""
    entries, malformed = _read_results(directory)
    lines, rejected, notes = check_results(entries, _read_worklists(directory), set(state["products"]))
    products_path = os.path.join(directory, "products.json")
    merge(state, lines, _read_json(products_path) if os.path.exists(products_path) else None)
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
    p.add_argument("--file")
    p.add_argument("--key")
    p.add_argument("--iri")
    p.add_argument("--status", choices=["created", "reused"])
    p.add_argument("--module")
    p.add_argument("--spec-json")

    p = sub.add_parser("decide")
    p.add_argument("dir")
    p.add_argument("--key", required=True)
    value = p.add_mutually_exclusive_group(required=True)
    value.add_argument("--value-json")
    value.add_argument("--value-file")
    p.add_argument("--origin", required=True, choices=ORIGINS)
    p.add_argument("--reason")

    p = sub.add_parser("worklists")
    p.add_argument("dir")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--limit", type=int)
    p.add_argument("--pilot", action="store_true")
    p.add_argument("--retry-failed", action="store_true")
    p.add_argument("--module")
    p.add_argument("--handles", help="comma-separated explicit selection")
    p.add_argument("--update", action="store_true", help="next pass of the update run of diff.json")

    p = sub.add_parser("result")
    p.add_argument("file")
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument("--json", help="the line, or - to read it from stdin")
    source.add_argument("--json-file")

    sub.add_parser("merge").add_argument("dir")

    p = sub.add_parser("phase")
    p.add_argument("dir")
    p.add_argument("--start", action="store_true", help="start the next phase")

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
        single = [args.key, args.iri, args.status, args.module]
        try:
            if args.file:
                if any(single):
                    raise ValueError("use either --file or --key/--iri/--status/--module")
                record_shared_entries(state, _read_json(args.file))
            elif all(single):
                record_shared(state, *single, spec=json.loads(args.spec_json) if args.spec_json else None)
            else:
                raise ValueError("--key, --iri, --status and --module are required without --file")
        except (OSError, ValueError) as error:
            print(f"error: {error}", file=sys.stderr)
            return 1
    elif args.command == "decide":
        try:
            value = _read_json(args.value_file) if args.value_file else json.loads(args.value_json)
            decide(state, args.key, value, args.origin, args.reason)
        except (OSError, ValueError) as error:
            print(f"error: {error}", file=sys.stderr)
            return 1
    elif args.command == "worklists":
        merged = _merge_results(args.dir, state)
        _write_json(state_path, state)
        _archive_results(args.dir)
        products_path = os.path.join(args.dir, "products.json")
        products = _read_json(products_path)["products"] if os.path.exists(products_path) else []
        titles = {p["handle"]: p.get("title") for p in products}
        selection = [h.strip() for h in args.handles.split(",") if h.strip()] if args.handles else None
        report = {}
        try:
            diff_data = None
            diff_path = os.path.join(args.dir, "diff.json")
            if args.update and os.path.exists(diff_path):
                diff_data = _read_json(diff_path)
            lists = worklists(state, args.workers, args.limit, args.pilot, args.retry_failed, args.module, titles,
                              selection, products, args.update, diff_data, report)
        except ValueError as error:
            print(f"error: {error}", file=sys.stderr)
            return 1
        for path in glob.glob(os.path.join(args.dir, "worklists", "*.json")):
            os.remove(path)
        for i, handles in enumerate(lists, start=1):
            _write_json(os.path.join(args.dir, "worklists", f"worker-{i}.json"), handles)
        output = {"merged_before": merged, "workers": len(lists), "products": sum(len(l) for l in lists)}
        if args.update:
            output["pass"] = report["pass"]
        if report["skipped"]:
            output["skipped"] = report["skipped"]
        print(json.dumps(output, ensure_ascii=False))
    elif args.command == "merge":
        print(f"{_merge_results(args.dir, state)} result lines merged")
        _write_json(state_path, state)
        _archive_results(args.dir)
        return 0
    elif args.command == "phase":
        if not args.start:
            print(state.get("phase", 1))
            return 0
        unmerged = [path for path in _result_files(args.dir) if _has_lines(path)]
        if unmerged:
            names = ", ".join("results/" + os.path.basename(path) for path in unmerged)
            print(f"error: {names} not merged: run state.py merge first", file=sys.stderr)
            return 1
        print(start_phase(state))
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
