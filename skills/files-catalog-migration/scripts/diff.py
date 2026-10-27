"""Difference between the structured files and what the last import wrote, for a later phase.

Usage:
  python3 diff.py <dir>

Reads products.json and state.json, writes <dir>/diff.json and prints the counts, then one line per changed
handle with its changed paths. The payload written for each product (state `written`, recorded by
state.py merge) is compared with payload.py's payload now, `<key IRI>` placeholders for shared elements not
recorded yet (a new shared element is a real change).

diff.json:
  phase             the state phase the difference was computed for
  new               handles of products.json never created
  changed           {"<handle>": compare(written, now)} for created (or archived) handles still in products.json
  unchanged         count of those with no difference
  removed           created handles gone from products.json (archive or delete: decision removed_action)
  already_archived  archived handles still gone (selected again only when removed_action is delete)
  rekeyed           [{"removed", "new": [handles], "skus": [skus], "changed"?: [handles]}]: a removed (or already
                    archived) handle whose written SKUs are now held by other products (a grouping change):
                    new handles, and in `changed` the created ones that gain those SKUs (variants.added)
  removal           {"<removed or already archived handle>": "delete_rekeyed" | "removed_action"}: a rekeyed
                    handle is always deleted, only after decision confirmed_rekey of the phase and before the
                    creation of its new handles; the others follow decision removed_action
  moved             [{"sku", "from", "to": [handles]}]: a SKU leaving a product still in products.json for
                    another one; the variant of `from` must be deleted before the creation in `to`
  unmanaged         changed or removed handles whose product item is reused: never updated, archived or deleted
  unmanaged_items   {"<handle>": [paths]}: changes touching a reused item of the product, left out of `changed`
  no_written        created handles without a written snapshot: cannot be compared
  errors            {"<handle>": "<message>"}: payload.py cannot build the product now
  conflicts         filled by the conflicts step
  missing_snapshots filled by the conflicts step
Deleted handles never appear.
"""

import json
import os
import sys
import tempfile

import payload
import state  # noqa: F401  (phase decisions of the conflicts step)
import verify

PRODUCT_FIELDS = ("title", "description", "tags", "status", "category", "attributes", "collections")
LIVE = ("created", "archived")


def _sorted_list(values):
    return sorted(values or [], key=lambda v: json.dumps(v, sort_keys=True, ensure_ascii=False))


def _field(body, name):
    value = body.get(name)
    if name in ("attributes", "collections"):
        return _sorted_list(value)
    return value


def _iri(value):
    return value["@id"] if isinstance(value, dict) and "@id" in value else value


def _view(name, value):
    """Normalized product field, shared by every comparison: our own writes read back compare equal."""
    value = _iri(value)
    if name == "description":
        return verify.normalize(value)
    if name == "tags":
        tags = value.split(",") if isinstance(value, str) else (value or [])
        return sorted(t.strip() for t in tags if t and t.strip())
    if name == "attributes":
        pairs = [(_iri(a.get("definition")), _iri(a["option"]) if "option" in a else a.get("value"))
                 for a in value or []]
        return sorted(pairs, key=lambda pair: json.dumps(pair, sort_keys=True, ensure_ascii=False))
    if name == "collections":
        return sorted(_iri(c) for c in value or [])
    return value


def _product_changes(old, new):
    old_body = (old.get("product") or {}).get("body") or {}
    new_body = (new.get("product") or {}).get("body") or {}
    changes = {}
    for name in PRODUCT_FIELDS:
        if _view(name, old_body.get(name)) != _view(name, new_body.get(name)):
            changes[name] = [_field(old_body, name), _field(new_body, name)]
    return changes


def _option_changes(old, new):
    before = {o["title"]: [v["value"] for v in o["values"]] for o in old.get("options") or []}
    after = {o["title"]: [v["value"] for v in o["values"]] for o in new.get("options") or []}
    changes = {
        "added": [t for t in after if t not in before],
        "removed": [t for t in before if t not in after],
        "values_added": {}, "values_removed": {},
    }
    for title in after:
        if title not in before:
            continue
        added = [v for v in after[title] if v not in before[title]]
        removed = [v for v in before[title] if v not in after[title]]
        if added:
            changes["values_added"][title] = added
        if removed:
            changes["values_removed"][title] = removed
    return {k: v for k, v in changes.items() if v}


def _variant_changes(old, new):
    before = {v["sku"]: v for v in old.get("variants") or []}
    after = {v["sku"]: v for v in new.get("variants") or []}
    changes = {
        "added": [s for s in after if s not in before],
        "removed": [s for s in before if s not in after],
        "changed": {},
    }
    for sku in after:
        if sku not in before:
            continue
        fields = {name: [before[sku].get(name), after[sku].get(name)] for name in ("barcode", "option_values")
                  if before[sku].get(name) != after[sku].get(name)}
        if fields:
            changes["changed"][sku] = fields
    return {k: v for k, v in changes.items() if v}


def _keyed_changes(old, new, key, value):
    before = {key(e): e for e in old or []}
    after = {key(e): e for e in new or []}
    changes = {
        "added": [k for k in after if k not in before],
        "removed": [k for k in before if k not in after],
        "changed": {k: [value(before[k]), value(after[k])] for k in after
                    if k in before and value(before[k]) != value(after[k])},
    }
    return {k: v for k, v in changes.items() if v}


def compare(old, new):
    """Changes from `old` to `new`, two payload.build outputs; empty when equal."""
    result = {
        "product": _product_changes(old, new),
        "options": _option_changes(old, new),
        "variants": _variant_changes(old, new),
        "metafields": _keyed_changes(old.get("metafields"), new.get("metafields"),
                                     lambda e: e["key"], lambda e: e["body"].get("value")),
        "prices": _keyed_changes(old.get("prices"), new.get("prices"),
                                 lambda e: f"{e['sku']}:{e['country']}", lambda e: e["body"]),
    }
    return {k: v for k, v in result.items() if v}


def change_paths(change):
    """Readable paths of a compare() result: product.<field>, <group>.<added|removed|...>.<id>."""
    paths = [f"product.{name}" for name in change.get("product", {})]
    for group in ("options", "variants", "metafields", "prices"):
        for kind, entries in change.get(group, {}).items():
            if group == "variants" and kind == "changed":
                paths += [f"variants.changed.{sku}.{name}" for sku, fields in entries.items() for name in fields]
            else:
                paths += [f"{group}.{kind}.{entry}" for entry in entries]
    return paths


def _reused_refs(product):
    return {(i["kind"], i["ref"]) for i in product["items"] if i.get("status") == "reused" and i.get("ref")}


def _strip_reused(change, product):
    """Leave out of the change every path touching a reused item of the product; returns those paths."""
    reused = _reused_refs(product)
    if not reused:
        return change, []
    change = json.loads(json.dumps(change))
    unmanaged = []

    def keep(kind, ref, path):
        if (kind, ref) in reused:
            unmanaged.append(path)
            return False
        return True

    options = change.get("options", {})
    options["removed"] = [t for t in options.get("removed", [])
                          if keep("product_option", t, f"options.removed.{t}")]
    for title, values in list(options.get("values_removed", {}).items()):
        values = [v for v in values if keep("option_value", f"{title}:{v}", f"options.values_removed.{title}.{v}")]
        options["values_removed"][title] = values
        if not values:
            del options["values_removed"][title]
    variants = change.get("variants", {})
    variants["removed"] = [s for s in variants.get("removed", []) if keep("variant", s, f"variants.removed.{s}")]
    for sku, fields in list(variants.get("changed", {}).items()):
        if ("variant", sku) in reused:
            unmanaged += [f"variants.changed.{sku}.{name}" for name in fields]
            del variants["changed"][sku]
    for group, kind in (("metafields", "metafield"), ("prices", "sale_offer")):
        entries = change.get(group, {})
        entries["removed"] = [k for k in entries.get("removed", []) if keep(kind, k, f"{group}.removed.{k}")]
        for key in list(entries.get("changed", {})):
            if not keep(kind, key, f"{group}.changed.{key}"):
                del entries["changed"][key]
    for group in ("options", "variants", "metafields", "prices"):
        if group in change:
            change[group] = {k: v for k, v in change[group].items() if v}
    return {k: v for k, v in change.items() if v}, unmanaged


def _reused_product(product):
    return any(i["kind"] == "product" and i.get("status") == "reused" for i in product["items"])


def _written_skus(product):
    written = (product.get("written") or {}).get("payload")
    if written:
        return [v["sku"] for v in written.get("variants") or []]
    return [i["ref"] for i in product["items"] if i["kind"] == "variant" and i.get("ref")]


def diff(state_data, products):
    present = [p["handle"] for p in products["products"]]
    present_set = set(present)
    entries = state_data.get("products", {})
    result = {"phase": state_data.get("phase", 1), "new": [], "changed": {}, "unchanged": 0, "removed": [],
              "already_archived": [], "rekeyed": [], "removal": {}, "unmanaged": [], "unmanaged_items": {},
              "moved": [], "no_written": [], "errors": {}, "conflicts": {}, "missing_snapshots": []}

    for handle in present:
        product = entries.get(handle)
        status = product["status"] if product else "pending"
        if status == "deleted":
            continue
        if status not in LIVE:
            result["new"].append(handle)
            continue
        written = (product.get("written") or {}).get("payload")
        if not written:
            result["no_written"].append(handle)
            continue
        try:
            now = payload.build(products, state_data, handle, placeholders=True)
        except payload.PayloadError as error:
            result["errors"][handle] = str(error)
            continue
        change = compare(written, now)
        if change and _reused_product(product):
            result["unmanaged"].append(handle)
            continue
        change, unmanaged = _strip_reused(change, product)
        if unmanaged:
            result["unmanaged_items"][handle] = unmanaged
        if change:
            result["changed"][handle] = change
        elif not unmanaged:
            result["unchanged"] += 1

    gone = []
    for handle, product in entries.items():
        if handle in present_set or product["status"] not in LIVE:
            continue
        if _reused_product(product):
            result["unmanaged"].append(handle)
            continue
        result["removed" if product["status"] == "created" else "already_archived"].append(handle)
        gone.append(handle)

    # SKUs a present product holds now and did not hold when written (every SKU of a new handle)
    holders, departures = {}, []
    for p in products["products"]:
        product = entries.get(p["handle"])
        if product and product["status"] == "deleted":
            continue
        before = _written_skus(product) if product and product["status"] in LIVE else []
        now = [v["sku"] for v in p.get("variants") or []]
        for sku in now:
            if sku not in before:
                holders.setdefault(sku, []).append(p["handle"])
        departures += [(p["handle"], sku) for sku in before if sku not in now]
    for handle in gone:
        skus = [s for s in _written_skus(entries[handle]) if s in holders]
        if skus:
            held = {h for s in skus for h in holders[s]}
            entry = {"removed": handle, "new": [h for h in result["new"] if h in held], "skus": skus}
            changed = [h for h in present if h in held and h not in result["new"]]
            if changed:
                entry["changed"] = changed
            result["rekeyed"].append(entry)
            result["removal"][handle] = "delete_rekeyed"
        else:
            result["removal"][handle] = "removed_action"
    result["moved"] = [{"sku": sku, "from": handle, "to": holders[sku]}
                       for handle, sku in departures if sku in holders]
    return result


def _read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _write_json(path, data):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(path)), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def report(result):
    lines = [
        f"phase {result['phase']}: new {len(result['new'])}, changed {len(result['changed'])}, "
        f"unchanged {result['unchanged']}, removed {len(result['removed'])}, "
        f"already archived {len(result['already_archived'])}, rekeyed {len(result['rekeyed'])}, "
        f"unmanaged {len(result['unmanaged'])}"
    ]
    lines += [f"  {handle}: {', '.join(change_paths(change))}" for handle, change in result["changed"].items()]
    for entry in result["rekeyed"]:
        targets = entry["new"] + entry.get("changed", [])
        lines.append(f"rekeyed {entry['removed']} -> {', '.join(targets)} (SKU {', '.join(entry['skus'])})")
    for entry in result["moved"]:
        lines.append(f"moved SKU {entry['sku']}: {entry['from']} -> {', '.join(entry['to'])}")
    if result["unmanaged"]:
        lines.append(f"unmanaged (reused, never written): {', '.join(result['unmanaged'])}")
    for handle, paths in result["unmanaged_items"].items():
        lines.append(f"unmanaged items of {handle} (reused, never written): {', '.join(paths)}")
    if result["no_written"]:
        lines.append(f"no written snapshot, not compared: {', '.join(result['no_written'])}")
    for handle, message in result["errors"].items():
        lines.append(f"error: {handle}: {message}")
    return "\n".join(lines)


def main(argv):
    args = argv[1:]
    if len(args) != 1 or args[0].startswith("-"):
        print("usage: diff.py <dir>", file=sys.stderr)
        return 2
    directory = args[0]
    try:
        products = _read_json(os.path.join(directory, "products.json"))
        state_data = _read_json(os.path.join(directory, "state.json"))
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    result = diff(state_data, products)
    _write_json(os.path.join(directory, "diff.json"), result)
    print(report(result))
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
