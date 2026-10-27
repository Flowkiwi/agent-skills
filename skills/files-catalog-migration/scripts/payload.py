"""Print the exact MCP call bodies of one product, from products.json and state.json.

The import.key marker (shared `metafield_definition:import.key`) carries the product handle as its value.

Usage:
  python3 payload.py <dir> <handle> [--step product|options|variants|metafields|taxonomy|collections|prices|all]
                    [--dry-run]
  python3 payload.py <dir> <handle> --update

Workers copy every value verbatim from this output. Invisible or special whitespace characters
(no-break spaces, zero-width characters, soft hyphens, control characters...) are printed as
\\uXXXX escapes: keep them as is in the JSON arguments of the MCP calls.
--dry-run prints `<key IRI>` placeholders for shared elements not recorded yet instead of failing.

Module fields come from machine-readable decisions: `taxonomy` as written by taxonomy.py build
(category and attributes, IRIs from shared `category:<path>`, `attribute_definition:<code>`,
`attribute_option:<code>:<value>`), `collections` as written by collections_concept.py build
(membership, IRIs from shared `collection:<path>`; --step collections prints the IRIs of the product's
collections, to send with its existing ones), and `metafields` as
{"definitions": {"<namespace.key>": {"type": "<flowkiwi type>", "name": "...", "transform": null,
"source": "<products.json metafield key, when renamed>"}}}
with transform null (value as is), list_json, list, number, json or strip_html. The reference metafields of
`metaobjects` (as written by metaobjects.py build) follow in the --step metafields output: definition IRI from shared
`metafield_definition:<namespace.key>`, value the IRI of shared `metaobject:<type>:<entry handle>`.
`prices` as written by prices.py build: one sale offer per variant and country, tax from the country or shared
`tax:<code>:<rate>`, variant `<variant IRI of <sku>>` replaced by the worker with the IRI of that variant.

--update (a later phase, reads diff.json too) prints the operations of one product of diff.json:
- changed: {"handle", "action": "update", "iri", "product": {"body": <changed fields, new values, null when
  cleared; attributes and collections whole>} | null, "options": {"create", "values_create", "values_delete",
  "delete"}, "variants": {"delete", "update", "create"}, "metafields": {"delete", "update", "create"},
  "prices": {"delete", "update", "create"}}, empty groups left out. Applied in this order: product, options
  create and values_create, variants delete, update, create, options values_delete and delete, metafields
  delete, update, create, prices delete (an offer gone with its deleted variant is already deleted), update,
  create. Existing children are the `created` items of the state by `ref`; a reused child is never written.
- removed: {"handle", "action": "archive", "iri", "product": {"body": {"status": ARCHIVED}}} or
  {"handle", "action": "delete", "steps": <undo plan of product:<handle>>}, from decision removed_action of
  the phase; a rekeyed product (diff.json removal delete_rekeyed) is always deleted, after confirmed_rekey, and a
  blocked rekey (removal archive_blocked) is always archived, whatever removed_action says.
Refused when diff.json was computed from another products.json or other decisions (its `source`), while diff.json
lists products to write and `diff.py conflicts` has not run since the last diff.py (conflicts_checked), and for a
handle without snapshot (missing_snapshots).
A product modified in Flowkiwi (diff.json conflicts) needs decision force_update of the phase. A new variant whose
SKU is still a created variant of another product (diff.json moved, rekeyed, two products swapping SKUs) is left
out of the creations with its prices and listed in "deferred": [{"sku", "held_by"}]; once the deletion in
`held_by` is merged, print the plan again: children already deleted or recorded are left out, so it only holds
what is still to do (and the field updates, sent again as they are). A product never deletes a SKU (rekeyed
deletion, `from` side of a moved SKU) while a product receiving it has an unforced conflict, nor a SKU of diff.json
blocked_moves (no receiver can create it): that variant, its prices and the option values it uses stay.
"""

import argparse
import html
import json
import os
import re
import sys
import unicodedata

import state as state_module

STATUSES = {"active": "ACTIVE", "draft": "DRAFT", "archived": "ARCHIVED"}
MARKER_KEY = "metafield_definition:import.key"
STEPS = ("product", "options", "variants", "metafields", "taxonomy", "collections", "prices", "all")
TRANSFORMS = (None, "list_json", "list", "number", "json", "strip_html")
ESCAPED_CATEGORIES = {"Cc", "Cf", "Zs", "Zl", "Zp"}


class PayloadError(Exception):
    pass


def is_special_char(char):
    """Invisible or non-ASCII whitespace characters a model may silently drop or replace."""
    return char not in " \t\n\r" and unicodedata.category(char) in ESCAPED_CATEGORIES


def escape_special(text):
    return "".join(json.dumps(c)[1:-1] if is_special_char(c) else c for c in text)


def dumps(data):
    """JSON with non-ASCII letters kept literal and special characters escaped.
    json.dumps already escapes control characters inside strings, and outside strings the JSON text
    only holds ASCII spaces and newlines, so replacing the remaining ones is safe."""
    return escape_special(json.dumps(data, ensure_ascii=False, indent=2))


def shared_iri(state, key, placeholders=False):
    entry = state.get("shared", {}).get(key)
    if entry and entry.get("iri") and entry.get("status") in ("created", "reused"):
        return entry["iri"]
    if placeholders:
        return f"<{key} IRI>"
    raise PayloadError(f"shared element '{key}' is not recorded in state.json: create it, then state.py shared")


def status_iri(product, decisions):
    mode = decisions.get("status_mode")
    if mode is None:
        raise PayloadError("decision 'status_mode' is missing: run state.py decide --key status_mode")
    if mode == "draft":
        return "/api/statuses/DRAFT"
    if mode == "active":
        return "/api/statuses/ACTIVE"
    if mode != "source":
        raise PayloadError(f"unknown status_mode '{mode}': use draft, active or source")
    status = product.get("status")
    key = str(status).lower() if status else ""
    code = decisions.get("status_map", {}).get(key) or STATUSES.get(key)
    if code is None:
        raise PayloadError(f"source status '{status}' has no mapping: record it in decision 'status_map'")
    return code if code.startswith("/api/statuses/") else f"/api/statuses/{code.upper()}"


def _module_decision(decisions, module, required_key):
    """The decision of a module handled by payload.py, or None when the module is not used."""
    value = decisions.get(module)
    if value is None:
        if module in decisions.get("modules", []):
            raise PayloadError(f"decision '{module}' is missing")
        return None
    if not isinstance(value, dict) or required_key not in value:
        raise PayloadError(f"decision '{module}' has no '{required_key}': record it in the format of references")
    return value


def taxonomy_fields(product, state, placeholders=False):
    taxonomy = _module_decision(state.get("decisions", {}), "taxonomy", "products")
    if taxonomy is None:
        return {}
    handle = product["handle"]
    fields = {}
    path = taxonomy["products"].get(handle)
    if path:
        fields["category"] = shared_iri(state, f"category:{path}", placeholders)
    attributes = []
    for definition in taxonomy.get("attributes", []):
        values = definition.get("values", {}).get(handle) or []
        if not values:
            continue
        code = definition["code"]
        definition_iri = shared_iri(state, f"attribute_definition:{code}", placeholders)
        for value in values:
            if definition.get("type", "list") == "list":
                option = shared_iri(state, f"attribute_option:{code}:{value}", placeholders)
                attributes.append({"definition": definition_iri, "option": option})
            else:
                attributes.append({"definition": definition_iri, "value": value})
    if attributes:
        fields["attributes"] = attributes
    return fields


def collection_iris(product, state, placeholders=False):
    collections = _module_decision(state.get("decisions", {}), "collections", "membership")
    if collections is None:
        return []
    paths = collections["membership"].get(product["handle"]) or []
    return [shared_iri(state, f"collection:{path}", placeholders) for path in paths]


def product_body(product, state, placeholders=False):
    body = {"title": product["title"]}
    if product.get("description_html"):
        body["description"] = product["description_html"]
    if product.get("tags"):
        body["tags"] = ",".join(product["tags"])
    body["status"] = status_iri(product, state.get("decisions", {}))
    body.update(taxonomy_fields(product, state, placeholders))
    iris = collection_iris(product, state, placeholders)
    if iris:
        body["collections"] = iris
    return body


def options(product):
    return [
        {"title": option["name"], "position": index,
         "values": [{"value": value, "position": i} for i, value in enumerate(option["values"])]}
        for index, option in enumerate(product["options"])
    ]


def variants(product):
    names = [option["name"] for option in product.get("options") or []]
    for index, v in enumerate(product["variants"], start=1):
        missing = [names[i] if i < len(names) else str(i + 1)
                   for i, value in enumerate(v["option_values"]) if value in (None, "")]
        if missing:
            raise PayloadError(f"variant {index} has no value for option(s) {', '.join(missing)} "
                               "(missing_option_value): settle it in the mapping")
    return [
        {"sku": v["sku"], "barcode": v["barcode"], "option_values": list(v["option_values"])}
        for v in product["variants"]
    ]


def _list_values(value):
    if isinstance(value, list):
        return [str(v) for v in value]
    parsed = None
    try:
        parsed = json.loads(value)
    except ValueError:
        pass
    if isinstance(parsed, list):
        return [str(v) for v in parsed]
    return [v.strip() for v in re.split(r"[;\n]", value) if v.strip()]


def transform_value(value, transform):
    if transform not in TRANSFORMS:
        raise PayloadError(f"unknown metafield transform '{transform}', use one of {TRANSFORMS}")
    if isinstance(value, list) and transform not in (None, "list_json", "list"):
        raise PayloadError(f"transform '{transform}' does not apply to the list value {value!r}")
    if transform == "list_json":
        return json.dumps(_list_values(value), ensure_ascii=False)
    if transform == "list":
        return _list_values(value)
    if transform == "number":
        number = value.strip().replace(",", ".")
        try:
            return int(number) if re.match(r"^-?\d+$", number) else float(number)
        except ValueError:
            raise PayloadError(f"value {value!r} is not a number")
    if transform == "json":
        try:
            return json.loads(value)
        except ValueError:
            raise PayloadError(f"value {value!r} is not valid JSON")
    if transform == "strip_html":
        text = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</h[1-6]>", "\n", value)
        text = html.unescape(re.sub(r"<[^>]+>", "", text))
        return "\n".join(line.strip() for line in text.split("\n") if line.strip())
    return value


def reference_metafields(product, state, placeholders=False):
    decision = _module_decision(state.get("decisions", {}), "metaobjects", "definitions")
    if decision is None:
        return []
    result = []
    entries = (decision.get("values") or {}).get(product["handle"]) or {}
    for definition in decision["definitions"]:
        reference, entry = definition.get("reference"), entries.get(definition["type"])
        if not reference or not entry:
            continue
        key = reference["metafield"]
        shared_key = f"metafield_definition:{key}"
        body = {"definition": shared_iri(state, shared_key, placeholders),
                "value": shared_iri(state, f"metaobject:{definition['type']}:{entry}", placeholders)}
        result.append({"key": key, "shared": shared_key, "body": body})
    return result


def metafields(product, state, placeholders=False):
    decision = _module_decision(state.get("decisions", {}), "metafields", "definitions") or {"definitions": {}}
    result = []
    values = decision.get("values")
    for key, definition in sorted(decision["definitions"].items()):
        definition = definition or {}
        if values is not None:
            value = (values.get(product["handle"]) or {}).get(key)
        else:
            value = product.get("metafields", {}).get(definition.get("source") or key)
        if key == "import.key" or value in (None, "", []):
            continue
        shared_key = f"metafield_definition:{key}"
        body = {"definition": shared_iri(state, shared_key, placeholders),
                "value": transform_value(value, definition.get("transform"))}
        result.append({"key": key, "shared": shared_key, "body": body})
    return result + reference_metafields(product, state, placeholders)


def sale_offers(product, state, placeholders=False):
    decision = _module_decision(state.get("decisions", {}), "prices", "offers")
    if decision is None:
        return []
    handle = product["handle"]
    offers = decision["offers"].get(handle) or {}
    skus = [v["sku"] for v in product["variants"]]
    unknown = [sku for sku in offers if sku not in skus]
    if unknown:
        raise PayloadError(f"decision 'prices' has offers for SKU {', '.join(unknown)} of {handle}, not in "
                           "products.json: extract the prices again and record the decision")
    result = []
    for sku in skus:
        for country in decision["countries"]:
            code = country["code"]
            offer = (offers.get(sku) or {}).get(code)
            if offer is None:
                continue
            discount = offer.get("discountTaxExcludedPrice")
            body = {"variant": f"<variant IRI of {sku}>", "country": country["country"],
                    "tax": country.get("tax") or shared_iri(state, f"tax:{code}:{country['rate']}", placeholders),
                    "status": decision.get("status") or "/api/statuses/ACTIVE",
                    "taxExcludedPrice": {"amount": offer["taxExcludedPrice"], "currency": country["currency"]},
                    "discountTaxExcludedPrice": {"amount": discount, "currency": country["currency"]}
                    if discount is not None else None}
            if decision.get("channels"):
                body["channels"] = list(decision["channels"])
            result.append({"sku": sku, "country": code, "body": body})
    return result


def build(products, state, handle, step="all", placeholders=False):
    product = next((p for p in products["products"] if p["handle"] == handle), None)
    if product is None:
        raise PayloadError(f"handle '{handle}' is not in products.json")
    payload = {"handle": handle}
    if step in ("product", "all"):
        payload["product"] = {"body": product_body(product, state, placeholders)}
        payload["marker"] = {"shared": MARKER_KEY,
                             "body": {"definition": shared_iri(state, MARKER_KEY, placeholders), "value": handle}}
    if step in ("options", "all"):
        payload["options"] = options(product)
    if step in ("variants", "all"):
        payload["variants"] = variants(product)
    if step in ("metafields", "all"):
        payload["metafields"] = metafields(product, state, placeholders)
    if step in ("prices", "all"):
        payload["prices"] = sale_offers(product, state, placeholders)
    if step == "taxonomy":
        payload["taxonomy"] = taxonomy_fields(product, state, placeholders)
    if step == "collections":
        payload["collections"] = collection_iris(product, state, placeholders)
    return payload

ARCHIVED = "/api/statuses/ARCHIVED"


class _Children:
    """The child items of a product in the state, by kind and ref. A plan can be printed again after a partial
    run: a child already deleted is not deleted again, a child already recorded is not created again."""

    def __init__(self, handle, product):
        self.handle = handle
        self.items = {}
        self.deleted = set()
        for item in product["items"]:
            if not item.get("ref"):
                continue
            if item.get("status") in ("created", "reused"):
                self.items.setdefault((item["kind"], item["ref"]), item)
            elif item.get("status") == "deleted":
                self.deleted.add((item["kind"], item["ref"]))

    def _item(self, kind, ref):
        item = self.items.get((kind, ref))
        if item is None:
            raise PayloadError(f"no {kind} item with ref '{ref}' for '{self.handle}': look it up with list_* "
                               "and record it")
        return item

    def target(self, kind, ref):
        """IRI of a child to update or delete; None when it is reused (never written) or already deleted."""
        if (kind, ref) not in self.items and (kind, ref) in self.deleted:
            return None
        item = self._item(kind, ref)
        return item["iri"] if item["status"] == "created" else None

    def parent(self, kind, ref):
        """IRI of a child a new child is created under."""
        return self._item(kind, ref)["iri"]

    def exists(self, kind, ref):
        return (kind, ref) in self.items

    def created(self, kind, prefix):
        return [(ref, item["iri"]) for (k, ref), item in self.items.items()
                if k == kind and ref.startswith(prefix) and item["status"] == "created"]


def _now(entries, key, handle, what):
    """The entry of the payload now that diff.json names; missing when products.json changed since diff.py."""
    if key not in entries:
        raise PayloadError(f"{what} '{key}' of '{handle}' in diff.json is not in the payload now: "
                           "products.json changed since, run diff.py again")
    return entries[key]


def _cleared(name):
    return [] if name in ("attributes", "collections") else None


def _product_update(change, now):
    fields = change.get("product")
    if not fields:
        return None
    body = now["product"]["body"]
    return {"body": {name: body.get(name, _cleared(name)) for name in fields}}


def kept_option_values(written, kept):
    """(option title, value) pairs used by the variants of `kept` SKUs in a written payload."""
    titles = [o["title"] for o in written.get("options") or []]
    return {(title, value) for v in written.get("variants") or [] if v["sku"] in kept
            for title, value in zip(titles, v.get("option_values") or [])}


def _options_update(change, now, children, kept_values=frozenset()):
    options = change.get("options", {})
    handle = children.handle
    current = {o["title"]: o for o in now.get("options") or []}
    used = {}
    for variant in now.get("variants") or []:
        for index, value in enumerate(variant["option_values"]):
            used.setdefault(index, set()).add(value)
    group = {"create": [_now(current, title, handle, "option") for title in options.get("added", [])
                        if not children.exists("product_option", title)],
             "values_create": [], "values_delete": [], "delete": []}
    for title, values in options.get("values_added", {}).items():
        positions = {v["value"]: v["position"] for v in _now(current, title, handle, "option")["values"]}
        todo = [value for value in values if not children.exists("option_value", f"{title}:{value}")]
        iri = children.parent("product_option", title) if todo else None
        group["values_create"] += [{"option": title, "option_iri": iri, "value": value,
                                    "position": _now(positions, value, handle, f"value of {title}")}
                                   for value in todo]
    for title, values in options.get("values_removed", {}).items():
        in_use = used.get(_now(current, title, handle, "option")["position"], set())
        for value in values:
            if value in in_use or (title, value) in kept_values:
                continue
            iri = children.target("option_value", f"{title}:{value}")
            if iri:
                group["values_delete"].append({"iri": iri, "option": title, "value": value})
    for title in options.get("removed", []):
        iri = children.target("product_option", title)
        if iri is None or any(t == title for t, _ in kept_values):
            continue
        group["values_delete"] += [{"iri": value_iri, "option": title, "value": ref.split(":", 1)[1]}
                                   for ref, value_iri in children.created("option_value", f"{title}:")]
        group["delete"].append({"iri": iri, "title": title})
    return group


def _variants_update(change, now, children, kept=frozenset()):
    variants = change.get("variants", {})
    handle = children.handle
    current = {v["sku"]: v for v in now.get("variants") or []}
    group = {"delete": [], "update": [],
             "create": [_now(current, sku, handle, "variant") for sku in variants.get("added", [])
                        if not children.exists("variant", sku)]}
    for sku in variants.get("removed", []):
        iri = children.target("variant", sku) if sku not in kept else None
        if iri:
            group["delete"].append({"iri": iri, "sku": sku})
    for sku, fields in variants.get("changed", {}).items():
        iri = children.target("variant", sku)
        if iri is None:
            continue
        variant = _now(current, sku, handle, "variant")
        entry = {"iri": iri, "sku": sku, "body": {}}
        if "barcode" in fields:
            entry["body"]["barcode"] = variant["barcode"]
        if "option_values" in fields:
            entry["option_values"] = list(variant["option_values"])
        group["update"].append(entry)
    return group


def _keyed_update(change, entries, children, kind, key, field, kept=frozenset()):
    """delete / update / create of a keyed group (metafields by namespace.key, prices by <sku>:<country>); `kept`
    are the refs never deleted (the offers of a variant kept by a blocked move)."""
    current = {key(e): e for e in entries}
    handle = children.handle
    group = {"delete": [], "update": [],
             "create": [_now(current, k, handle, kind) for k in change.get("added", [])
                        if not children.exists(kind, k)]}
    for ref in change.get("removed", []):
        iri = children.target(kind, ref) if ref not in kept else None
        if iri:
            group["delete"].append({"iri": iri, field: ref})
    for ref, (old, _) in change.get("changed", {}).items():
        iri = children.target(kind, ref)
        if iri:
            new = _now(current, ref, handle, kind)["body"]
            group["update"].append({"iri": iri, field: ref, "body": _changed_body(kind, old, new)})
    return group


def _changed_body(kind, old, new):
    if kind == "metafield":
        return {"value": new.get("value")}
    return {name: value for name, value in new.items() if (old or {}).get(name) != value}


def _non_empty(group):
    group = {name: entries for name, entries in group.items() if entries}
    return group or None


def _held_elsewhere(state, handle, sku):
    for other, product in state["products"].items():
        if other == handle or product["status"] == "deleted":
            continue
        if any(i["kind"] == "variant" and i.get("ref") == sku and i.get("status") == "created"
               for i in product["items"]):
            return other
    return None


def blocked_skus(diff_data, handle):
    """SKUs of diff.json blocked_moves that `handle` keeps: never deleted, their receivers cannot create them."""
    return {m["sku"] for m in diff_data.get("blocked_moves") or [] if m.get("from") == handle}


def _changed_plan(products, state, diff_data, handle, entry):
    change = diff_data["changed"][handle]
    now = build(products, state, handle)
    children = _Children(handle, entry)
    plan = {"handle": handle, "action": "update", "iri": entry["iri"], "product": _product_update(change, now)}
    kept = blocked_skus(diff_data, handle)
    written = (entry.get("written") or {}).get("payload") or {}
    kept_offers = {ref for ref in change.get("prices", {}).get("removed", []) if ref.rsplit(":", 1)[0] in kept}
    groups = {
        "options": _options_update(change, now, children, kept_option_values(written, kept)),
        "variants": _variants_update(change, now, children, kept),
        "metafields": _keyed_update(change.get("metafields", {}), now.get("metafields") or [], children,
                                    "metafield", lambda e: e["key"], "key"),
        "prices": _keyed_update(change.get("prices", {}), now.get("prices") or [], children,
                                "sale_offer", lambda e: f"{e['sku']}:{e['country']}", "ref", kept_offers),
    }
    deleted = {v["sku"] for v in groups["variants"]["delete"]}
    for moved in diff_data.get("moved") or []:
        if moved.get("from") == handle and moved.get("sku") in deleted:
            _check_receivers(state, diff_data, handle, [moved["sku"]], moved.get("to") or [])
    # A SKU still held by another product (moved, swapped, rekeyed into this one) waits for its deletion there;
    # every other operation, this product's own deletions included, goes on.
    deferred = []
    for variant in groups["variants"]["create"]:
        other = _held_elsewhere(state, handle, variant["sku"])
        if other:
            deferred.append({"sku": variant["sku"], "held_by": other})
    if deferred:
        waiting = {d["sku"] for d in deferred}
        groups["variants"]["create"] = [v for v in groups["variants"]["create"] if v["sku"] not in waiting]
        groups["prices"]["create"] = [p for p in groups["prices"]["create"] if p["sku"] not in waiting]
    for name, group in groups.items():
        group = _non_empty(group)
        if group:
            plan[name] = group
    if deferred:
        plan["deferred"] = deferred
    return plan


def _conflicted(state, diff_data, handle):
    return handle in (diff_data.get("conflicts") or {}) and not state_module.forced(state, handle)


def _check_receivers(state, diff_data, handle, skus, receivers):
    """The SKUs `handle` gives away must reach their receivers: none may be blocked by an unforced conflict."""
    for target in receivers:
        if _conflicted(state, diff_data, target):
            raise PayloadError(f"'{handle}' cannot delete SKU {', '.join(skus)} yet: '{target}' receives it and was "
                               "modified in Flowkiwi since the last import (see diff.json conflicts): "
                               "force_update or resolve the conflict first")


def _removed_plan(state, diff_data, handle, entry):
    phase = state.get("phase", 1)
    removal = (diff_data.get("removal") or {}).get(handle)
    if removal == "delete_rekeyed":
        if state_module.phase_decision(state, "confirmed_rekey") is None:
            raise PayloadError(f"rekeyed products need decision confirmed_rekey for phase {phase}")
        for rekey in diff_data.get("rekeyed") or []:
            if rekey.get("removed") == handle:
                _check_receivers(state, diff_data, handle, rekey.get("skus") or [], rekey.get("changed") or [])
        action = "delete"
    elif removal == "archive_blocked":
        action = "archive"
    else:
        decision = state_module.phase_decision(state, "removed_action")
        action = decision.get("action") if isinstance(decision, dict) else None
        if action not in ("archive", "delete"):
            raise PayloadError(f"removed products need decision removed_action for phase {phase}")
    if action == "delete":
        return {"handle": handle, "action": "delete", "steps": state_module.undo_plan(state, f"product:{handle}")}
    if handle in diff_data.get("already_archived", []):
        raise PayloadError(f"'{handle}' is already archived: nothing to write for removed_action archive")
    return {"handle": handle, "action": "archive", "iri": entry["iri"], "product": {"body": {"status": ARCHIVED}}}


def update_plan(products, state, diff_data, handle):
    """Ordered update operations of one changed or removed product of diff.json (see the module docstring)."""
    phase = state.get("phase", 1)
    if diff_data.get("phase") != phase:
        raise PayloadError(f"diff.json is from phase {diff_data.get('phase')}, run diff.py again")
    stale = state_module.diff_stale(state, diff_data, products)
    if stale:
        raise PayloadError(stale)
    unchecked = state_module.conflicts_unchecked(diff_data)
    if unchecked:
        raise PayloadError(unchecked)
    entry = state.get("products", {}).get(handle)
    reused = entry is not None and any(i["kind"] == "product" and i.get("status") == "reused" for i in entry["items"])
    if handle in diff_data.get("unmanaged", []) or reused:
        raise PayloadError(f"'{handle}' is unmanaged (reused): never updated")
    changed = handle in diff_data.get("changed", {})
    removed = handle in diff_data.get("removed", []) or handle in diff_data.get("already_archived", [])
    if entry is None or not (changed or removed):
        raise PayloadError(f"'{handle}' is not in diff.json changed or removed")
    if handle in (diff_data.get("missing_snapshots") or []):
        raise PayloadError(f"'{handle}': {state_module.MISSING_SNAPSHOT}")
    if _conflicted(state, diff_data, handle):
        raise PayloadError(f"'{handle}' was modified in Flowkiwi since the last import (see diff.json conflicts): "
                           "force it with decision force_update")
    if removed:
        return _removed_plan(state, diff_data, handle, entry)
    return _changed_plan(products, state, diff_data, handle, entry)


def _read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main(argv):
    parser = argparse.ArgumentParser(description="Print the MCP call bodies of one product")
    parser.add_argument("dir")
    parser.add_argument("handle")
    parser.add_argument("--step", choices=STEPS, default="all")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--update", action="store_true", help="operations of a product of diff.json")
    args = parser.parse_args(argv[1:])
    if args.update and (args.dry_run or args.step != "all"):
        parser.error("--update takes no --step and no --dry-run")

    try:
        products = _read_json(os.path.join(args.dir, "products.json"))
        state = _read_json(os.path.join(args.dir, "state.json"))
        if args.update:
            payload = update_plan(products, state, _read_json(os.path.join(args.dir, "diff.json")), args.handle)
        else:
            payload = build(products, state, args.handle, args.step, args.dry_run)
    except (OSError, ValueError, PayloadError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(dumps(payload))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
