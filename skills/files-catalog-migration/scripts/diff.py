"""Difference between the structured files and what the last import wrote, for a later phase.

Usage:
  python3 diff.py <dir>
  python3 diff.py conflicts <dir>

Reads products.json and state.json, writes <dir>/diff.json and prints the counts, then one line per changed
handle with its changed paths. The payload written for each product (state `written`, recorded by
state.py merge) is compared with payload.py's payload now, `<key IRI>` placeholders for shared elements not
recorded yet (a new shared element is a real change).

`conflicts` compares, for each handle of `changed` and `removed`, what the last import wrote with the
Flowkiwi read-back snapshot <dir>/tmp/remote/<handle>.json (`{"product": <get_product response>,
"variants": [<list_product_variants items>], "metafields": [<list_metafields items>]}`), fills `conflicts`
and `missing_snapshots` in diff.json and prints them. A field absent from the snapshot is not compared.

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
                    handle is always deleted, only after decision confirmed_rekey of the phase, before the update
                    of its `changed` handles and before the creation of its new handles; the others (blocked
                    rekeys included) follow decision removed_action
  blocked_rekeys    {"<removed handle>": "<reason>"}: a rekey into a product this migration cannot update
                    (unmanaged, payload error or no written snapshot): not deleted, its SKUs would be lost
  moved             [{"sku", "from", "to": [handles]}]: a SKU leaving a product still in products.json for
                    another one; the variant of `from` must be deleted before the creation in `to`
  unmanaged         changed or removed handles whose product item is reused: never updated, archived or deleted
  unmanaged_items   {"<handle>": [paths]}: changes touching a reused item of the product, left out of `changed`
  no_written        created handles without a written snapshot: cannot be compared
  errors            {"<handle>": "<message>"}: payload.py cannot build the product now
  conflicts         {"<handle>": {"<path>": {"written", "flowkiwi"}}}: modified in Flowkiwi since the last import
                    (filled by the conflicts step; never updated, archived or deleted unless forced)
  missing_snapshots handles of changed or removed without a snapshot (filled by the conflicts step)
Deleted handles never appear.
"""

import json
import os
import sys
import tempfile

import payload
import state
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
        pairs = [(_iri(a.get("definition")), _iri(a["option"]) if a.get("option") is not None else a.get("value"))
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
              "blocked_rekeys": {}, "moved": [], "no_written": [], "errors": {}, "conflicts": {},
              "missing_snapshots": []}

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
    not_updatable = {h: "unmanaged" for h in result["unmanaged"]}
    not_updatable.update({h: "payload error" for h in result["errors"]})
    not_updatable.update({h: "no written snapshot" for h in result["no_written"]})
    for handle in gone:
        skus = [s for s in _written_skus(entries[handle]) if s in holders]
        if skus:
            held = {h for s in skus for h in holders[s]}
            entry = {"removed": handle, "new": [h for h in result["new"] if h in held], "skus": skus}
            changed = [h for h in present if h in held and h not in result["new"]]
            if changed:
                entry["changed"] = changed
            result["rekeyed"].append(entry)
            stuck = [f"{h} ({not_updatable[h]})" for h in changed if h in not_updatable]
            if stuck:
                # deleting it would remove the SKUs from Flowkiwi while the target never receives them
                result["blocked_rekeys"][handle] = ("rekey into a product this migration cannot update: "
                                                    + ", ".join(stuck))
                result["removal"][handle] = "removed_action"
            else:
                result["removal"][handle] = "delete_rekeyed"
        else:
            result["removal"][handle] = "removed_action"
    result["moved"] = [{"sku": sku, "from": handle, "to": holders[sku]}
                       for handle, sku in departures if sku in holders]
    return result


def _plain(value):
    if isinstance(value, dict):
        return value["@id"] if "@id" in value else {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


def _numbers(value, nested=False):
    """Numbers compare as numbers: 1.8, "1.8" and 1.80 are one value, 2 and 2.0 too."""
    if nested and isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            parsed = None
        if isinstance(parsed, (int, float)) and not isinstance(parsed, bool):
            value = parsed
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, list):
        return [_numbers(v, True) for v in value]
    if isinstance(value, dict):
        return {k: _numbers(v, True) for k, v in value.items()}
    return value


def _json_text(value):
    """A metafield value as JSON text: a string holding JSON is parsed first, IRIs as objects become IRIs."""
    value = _plain(value)
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            pass
    return json.dumps(_numbers(value), sort_keys=True, ensure_ascii=False)


def _variants_view(variants):
    return {v["sku"]: v.get("barcode") or None for v in variants or [] if isinstance(v, dict) and v.get("sku")}


def _metafields_view(entries):
    return {_iri(e.get("definition")): _json_text(e.get("value")) for e in entries if isinstance(e, dict)}


def written_view(written):
    """What the last import wrote (a payload.build output); fields the payload leaves out when empty are empty."""
    body = (written.get("product") or {}).get("body") or {}
    view = {name: _view(name, body.get(name)) for name in PRODUCT_FIELDS}
    if "variants" in written:
        view["variants"] = _variants_view(written["variants"])
    entries = [written["marker"]] if written.get("marker") else []
    entries += written.get("metafields") or []
    view["metafields"] = _metafields_view([e.get("body") or {} for e in entries])
    return view


def remote_view(snapshot):
    """The Flowkiwi read-back snapshot in the shape of written_view; a key absent from it stays absent."""
    product = snapshot.get("product") or {}
    view = {name: _view(name, product[name]) for name in PRODUCT_FIELDS if name in product}
    if "variants" in snapshot:
        view["variants"] = _variants_view(snapshot["variants"])
    if "metafields" in snapshot:
        view["metafields"] = _metafields_view(snapshot["metafields"] or [])
    return view


def conflicts(written, remote):
    """{"<path>": {"written", "flowkiwi"}} for the keys of both views that differ; in the variant and metafield
    maps an entry on one side only is a difference too (null on the other side)."""
    found = {}
    for name, value in written.items():
        if name not in remote:
            continue
        if name in ("variants", "metafields"):
            other = remote[name]
            for key in list(value) + [k for k in other if k not in value]:
                if key not in value or key not in other or value[key] != other[key]:
                    found[f"{name}.{key}"] = {"written": value.get(key), "flowkiwi": other.get(key)}
        elif value != remote[name]:
            found[name] = {"written": value, "flowkiwi": remote[name]}
    return found


forced = state.forced


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
    for handle, reason in result["blocked_rekeys"].items():
        lines.append(f"blocked rekey {handle}: {reason}")
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


def conflicts_report(diff_data, state_data, not_compared):
    found, missing = diff_data["conflicts"], diff_data["missing_snapshots"]
    lines = [f"conflicts: {len(found)} modified in Flowkiwi since the last import, {len(missing)} snapshot(s) missing"]
    for handle, paths in found.items():
        label = f"{handle} (forced)" if forced(state_data, handle) else handle
        lines.append(f"  {label}: {', '.join(paths)}")
    if missing:
        lines.append(f"missing snapshots: {', '.join(missing)}")
    if not_compared:
        lines.append(f"no written snapshot, not compared: {', '.join(not_compared)}")
    return "\n".join(lines)


def conflicts_main(args):
    if len(args) != 1 or args[0].startswith("-"):
        print("usage: diff.py conflicts <dir>", file=sys.stderr)
        return 2
    directory = args[0]
    diff_path = os.path.join(directory, "diff.json")
    try:
        diff_data = _read_json(diff_path)
        state_data = _read_json(os.path.join(directory, "state.json"))
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    if diff_data.get("phase") != state_data.get("phase", 1):
        print(f"error: diff.json is from phase {diff_data.get('phase')}, run diff.py again", file=sys.stderr)
        return 1
    found, missing, not_compared = {}, [], []
    for handle in list(diff_data.get("changed") or {}) + list(diff_data.get("removed") or []):
        path = os.path.join(directory, "tmp", "remote", f"{handle}.json")
        if not os.path.exists(path):
            missing.append(handle)
            continue
        try:
            snapshot = _read_json(path)
        except (OSError, ValueError) as error:
            print(f"error: {path}: {error}", file=sys.stderr)
            return 1
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("product"), dict):
            print(f"error: {path}: expected {{\"product\": {{...}}, \"variants\": [...], \"metafields\": [...]}}",
                  file=sys.stderr)
            return 1
        written = ((state_data.get("products", {}).get(handle) or {}).get("written") or {}).get("payload")
        if not written:
            not_compared.append(handle)
            continue
        changes = conflicts(written_view(written), remote_view(snapshot))
        if changes:
            found[handle] = changes
    diff_data["conflicts"], diff_data["missing_snapshots"] = found, missing
    _write_json(diff_path, diff_data)
    print(conflicts_report(diff_data, state_data, not_compared))
    return 0


def main(argv):
    args = argv[1:]
    if args and args[0] == "conflicts":
        return conflicts_main(args[1:])
    if len(args) != 1 or args[0].startswith("-"):
        print("usage: diff.py <dir> | diff.py conflicts <dir>", file=sys.stderr)
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
