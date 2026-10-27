"""Ordered plan of the shared elements to create, from the decisions in state.json.

Usage:
  python3 plan_shared.py <dir> [--ready]
  python3 plan_shared.py <dir> --changes

Covers the core import.key marker definition, then (when decided) categories parents first, attribute definitions,
attribute options, collections parents first, metaobject definitions, metaobject entries (definition order, then
entry order), the reference metafield definitions of the metaobjects, other metafield definitions, then the taxes
of the `prices` countries without a tax IRI (key `tax:<country code>:<rate>`). Keys already
recorded `created` or `reused` in state.json are skipped. Every entry carries a `spec` (what was asked), recorded
with the IRI to detect later drift.
A body referencing a shared element not recorded yet holds a `<key IRI>` placeholder and lists it
in `depends_on`; --ready prints only the entries whose dependencies are all recorded. Other modules'
shared elements (countries, suppliers, channels) follow their references.

--changes compares the recorded specs with the current plan (a later phase): destructive changes (with the
created products that use them), updates of elements created by this migration, `unmanaged` keys (reused,
never modified) and `unknown` ones (recorded without spec). Exit 1 while a destructive key is not in the
decision `confirmed_changes` of the current phase (`{"phase": <n>, "keys": [...]}`).
"""

import argparse
import copy
import json
import os
import sys
from decimal import ROUND_HALF_UP, Decimal

from payload import MARKER_KEY, PayloadError, dumps, shared_iri
from state import phase_decision

OWNER_TYPE = "/api/metafield_owner_types/products"
TYPE_PREFIX = "/api/metafield_definition_types/"
METAOBJECT_VALIDATION = "/api/metafield_definition_validation_types/metaobject_definition"
MARKER_LABELS = {
    "en": ("Import key", "Technical marker set by the files migration"),
    "fr": ("Clé d'import", "Marqueur technique posé par la migration de fichiers"),
    "de": ("Import-Schlüssel", "Technische Markierung der Dateimigration"),
    "es": ("Clave de importación", "Marcador técnico de la migración de archivos"),
    "it": ("Chiave di importazione", "Marcatore tecnico della migrazione di file"),
}


def _recorded(state, key):
    entry = state.get("shared", {}).get(key)
    return bool(entry and entry.get("iri") and entry.get("status") in ("created", "reused"))


def _iri(state, key, depends_on):
    if not _recorded(state, key) and key not in depends_on:
        depends_on.append(key)
    return shared_iri(state, key, placeholders=True)


def _metaobject_validation(state, kind, depends_on):
    return [{"name": METAOBJECT_VALIDATION, "value": _iri(state, f"metaobject_definition:{kind}", depends_on)}]


def _metaobject_field_value(state, field, value, depends_on):
    """Entry field value as sent: text as is, references as entry IRIs, lists as a JSON array string."""
    if field.get("target"):
        items = value if isinstance(value, list) else [value]
        value = [_iri(state, f"metaobject:{field['target']}:{item}", depends_on) for item in items]
        if field["type"] == "metaobject_reference":
            return value[0]
    if isinstance(value, list):
        return json.dumps(value, ensure_ascii=False)
    return value


def _metafield_definition(namespace_key, name, kind):
    namespace, _, key = namespace_key.partition(".")
    return {"namespace": namespace, "key": key, "name": name, "ownerType": OWNER_TYPE, "type": TYPE_PREFIX + kind}


def marker_labels(decisions):
    """Marker name and description in the data language; `marker_labels` decision for other languages."""
    custom = decisions.get("marker_labels")
    if isinstance(custom, dict) and custom.get("name"):
        return custom["name"], custom.get("description") or MARKER_LABELS["en"][1]
    language = str(decisions.get("data_language") or "en").lower().split("-")[0].split("_")[0]
    return MARKER_LABELS.get(language, MARKER_LABELS["en"])


def plan(state, metafield_columns=None, include_recorded=False):
    decisions = state.get("decisions", {})
    entries = []

    def add(key, module, operation, lookup, body, spec, depends_on=None, path=None):
        entry = {"key": key, "module": module, "operation": operation, "lookup": lookup}
        if path is not None:
            entry["path"] = path
        entry["body"] = body
        entry["spec"] = spec
        entry["depends_on"] = depends_on or []
        entries.append(entry)

    name, description = marker_labels(decisions)
    marker = _metafield_definition("import.key", name, "single_line_text_field")
    marker["description"] = description
    add(MARKER_KEY, "core", "create_metafield_definition",
        "list_metafield_definitions: namespace import, key key, owner type products", marker,
        {"type": "single_line_text_field", "name": name})

    taxonomy = decisions.get("taxonomy")
    if isinstance(taxonomy, dict) and "categories" in taxonomy:
        status = taxonomy.get("category_status", "/api/statuses/ACTIVE")
        for category in taxonomy["categories"]:
            depends_on = []
            parent = _iri(state, f"category:{category['parent']}", depends_on) if category.get("parent") else None
            add(f"category:{category['path']}", "taxonomy", "create_category",
                f"list_categories: name '{category['name']}' with the same parent",
                {"name": category["name"], "parent": parent, "status": status},
                {"name": category["name"], "parent": category.get("parent")}, depends_on)
        for definition in taxonomy.get("attributes", []):
            code = definition["code"]
            depends_on = []
            categories = [_iri(state, f"category:{path}", depends_on) for path in definition.get("categories", [])]
            add(f"attribute_definition:{code}", "taxonomy", "create_attribute_definition",
                f"list_attribute_definitions: code '{code}'",
                {"code": code, "name": definition["name"], "type": definition.get("type", "list"),
                 "multiple": bool(definition.get("multiple")), "categories": categories},
                {"type": definition.get("type", "list"), "multiple": bool(definition.get("multiple")),
                 "name": definition["name"]}, depends_on)
            if definition.get("type", "list") != "list":
                continue
            for position, option in enumerate(definition.get("options", [])):
                depends_on = []
                definition_iri = _iri(state, f"attribute_definition:{code}", depends_on)
                add(f"attribute_option:{code}:{option['value']}", "taxonomy", "create_attribute_option",
                    f"list_attribute_options of the definition: value '{option['value']}'",
                    {"value": option["value"], "position": position}, {"value": option["value"]}, depends_on,
                    {"definitionId": definition_iri})

    collections = decisions.get("collections")
    if isinstance(collections, dict) and "collections" in collections:
        status = collections.get("status", "/api/statuses/ACTIVE")
        for collection in collections["collections"]:
            depends_on = []
            parent = _iri(state, f"collection:{collection['parent']}", depends_on) if collection.get("parent") else None
            add(f"collection:{collection['path']}", "collections", "create_collection",
                f"list_collections: name '{collection['name']}' with the same parent",
                {"name": collection["name"], "parent": parent, "status": status},
                {"name": collection["name"], "parent": collection.get("parent")}, depends_on)

    references = set()
    metaobjects = decisions.get("metaobjects")
    if isinstance(metaobjects, dict) and "definitions" in metaobjects:
        definitions = metaobjects["definitions"]
        for definition in definitions:
            kind = definition["type"]
            depends_on = []
            fields = []
            for field in definition["fields"]:
                body = {"key": field["key"], "name": field["name"], "type": TYPE_PREFIX + field["type"]}
                if field.get("required"):
                    body["required"] = True
                if field.get("target"):
                    body["validations"] = _metaobject_validation(state, field["target"], depends_on)
                fields.append(body)
            spec = {"name": definition["name"], "fields": {f["key"]: f["type"] for f in definition["fields"]}}
            targets = {f["key"]: f["target"] for f in definition["fields"] if f.get("target")}
            if targets:
                spec["targets"] = targets
            add(f"metaobject_definition:{kind}", "metaobjects", "create_metaobject_definition",
                f"list_metaobject_definitions: type '{kind}'",
                {"type": kind, "name": definition["name"], "displayNameField": definition["displayNameField"],
                 "fieldDefinitions": fields}, spec, depends_on)
        for definition in definitions:
            kind = definition["type"]
            for entry in metaobjects.get("entries", []):
                if entry["definition"] != kind:
                    continue
                depends_on = []
                definition_iri = _iri(state, f"metaobject_definition:{kind}", depends_on)
                fields = [{"key": f["key"], "value": _metaobject_field_value(state, f, entry["fields"][f["key"]],
                                                                            depends_on)}
                          for f in definition["fields"] if f["key"] in entry["fields"]]
                add(f"metaobject:{kind}:{entry['handle']}", "metaobjects", "create_metaobject",
                    f"list_metaobjects: definition {definition_iri}, handle '{entry['handle']}'",
                    {"definition": definition_iri, "handle": entry["handle"], "fields": fields,
                     "status": definition["status"]},
                    {"fields": copy.deepcopy(entry["fields"])}, depends_on)
        for definition in definitions:
            reference = definition.get("reference")
            if not reference:
                continue
            namespace_key = reference["metafield"]
            depends_on = []
            body = _metafield_definition(namespace_key, reference["name"], "metaobject_reference")
            if metaobjects.get("reference_validation", True) is not False:
                body["validations"] = _metaobject_validation(state, definition["type"], depends_on)
            add(f"metafield_definition:{namespace_key}", "metaobjects", "create_metafield_definition",
                f"list_metafield_definitions: namespace.key {namespace_key}, owner type products", body,
                {"type": "metaobject_reference", "name": reference["name"]}, depends_on)
            references.add(namespace_key)

    metafields = decisions.get("metafields")
    if isinstance(metafields, dict) and "definitions" in metafields:
        for namespace_key, definition in sorted(metafields["definitions"].items()):
            if namespace_key == "import.key":
                continue
            if namespace_key in references:
                raise PayloadError(f"metafield '{namespace_key}' is both in decision 'metafields' and the reference "
                                   "of a metaobject: rename one of them in its mapping")
            definition = definition or {}
            source = definition.get("source") or namespace_key
            name = definition.get("name") or (metafield_columns or {}).get(source) or namespace_key
            if not definition.get("type"):
                raise PayloadError(f"metafield definition '{namespace_key}' has no type in decision 'metafields'")
            add(f"metafield_definition:{namespace_key}", "metafields", "create_metafield_definition",
                f"list_metafield_definitions: namespace.key {namespace_key}, owner type products",
                _metafield_definition(namespace_key, name, definition["type"]),
                {"type": definition["type"], "name": name})

    prices = decisions.get("prices")
    if isinstance(prices, dict) and "countries" in prices:
        for country in prices["countries"]:
            if country.get("tax"):
                continue
            rate = str(country["rate"])
            api_rate = Decimal(rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            add(f"tax:{country['code']}:{rate}", "prices", "create_tax",
                f'list_taxes: country {country["country"]}, rate {rate} (the API returns "{api_rate}")',
                {"name": country["tax_name"], "rate": rate, "country": country["country"]},
                {"rate": rate, "country": country["country"]})

    if include_recorded:
        return entries
    return [e for e in entries if not _recorded(state, e["key"])]


PLANNED_KINDS = {"metafield_definition", "category", "attribute_definition", "attribute_option", "collection",
                 "metaobject_definition", "metaobject", "tax"}
RENAMED_KINDS = {"metafield_definition", "attribute_definition", "metaobject_definition"}


def _shown(value):
    return value if isinstance(value, str) else json.dumps(value)


def _compare(kind, old, new):
    """(destructive reasons, update body fields) between a recorded spec and the current one."""
    reasons, update = [], []
    for field in {"metafield_definition": ["type"], "attribute_definition": ["type", "multiple"]}.get(kind, []):
        if old.get(field) != new.get(field):
            reasons.append(f"{field} {_shown(old.get(field))} -> {_shown(new.get(field))}")
    if kind in RENAMED_KINDS and old.get("name") != new.get("name"):
        update.append("name")
    if kind == "metaobject_definition":
        old_fields, new_fields = old.get("fields") or {}, new.get("fields") or {}
        old_targets, new_targets = old.get("targets") or {}, new.get("targets") or {}
        for key, field_type in old_fields.items():
            if key not in new_fields:
                reasons.append(f"field {key} removed")
            elif new_fields[key] != field_type:
                reasons.append(f"field {key} type {field_type} -> {new_fields[key]}")
            elif key in old_targets and old_targets[key] != new_targets.get(key):
                reasons.append(f"field {key} target {old_targets[key]} -> {new_targets.get(key)}")
        if set(new_fields) - set(old_fields):
            update.append("fieldDefinitions")
    if kind == "metaobject" and old.get("fields") != new.get("fields"):
        update.append("fields")
    return reasons, update


def _products_using(state, key, iri):
    handles = []
    for handle, product in state.get("products", {}).items():
        if product.get("status") not in ("created", "archived"):
            continue
        uses = any(i.get("shared") == key and i.get("status") != "deleted" for i in product.get("items", []))
        written = (product.get("written") or {}).get("payload")
        if uses or (iri and written is not None and iri in json.dumps(written, ensure_ascii=False)):
            handles.append(handle)
    return sorted(handles)


def changes(state, metafield_columns=None):
    """Recorded shared elements against the current plan. Only elements recorded `created` are changed: a
    renamed or moved category or collection is its path removed plus a new one (created by `plan`)."""
    current = {e["key"]: e for e in plan(state, metafield_columns, include_recorded=True)}
    result = {"destructive": [], "updates": [], "unmanaged": [], "unknown": []}
    for key, recorded in state.get("shared", {}).items():
        kind = key.split(":", 1)[0]
        if kind not in PLANNED_KINDS or recorded.get("status") not in ("created", "reused"):
            continue
        entry = current.get(key)
        if entry is None:
            reasons, update = ["removed"], []
        elif recorded.get("spec") is None:
            result["unknown"].append(key)
            continue
        else:
            reasons, update = _compare(kind, recorded["spec"], entry["spec"])
        if not reasons and not update:
            continue
        if recorded["status"] == "reused":
            result["unmanaged"].append(key)
        elif reasons:
            result["destructive"].append({"key": key, "reason": "; ".join(reasons),
                                          "products": _products_using(state, key, recorded.get("iri"))})
        else:
            result["updates"].append({"key": key, "operation": f"update_{kind}", "iri": recorded["iri"],
                                      "module": entry["module"], "body": {f: entry["body"][f] for f in update},
                                      "spec": entry["spec"], "depends_on": entry["depends_on"]})
    return result


def unconfirmed(state, found):
    """Destructive keys not confirmed by the decision `confirmed_changes` of the current phase."""
    decision = phase_decision(state, "confirmed_changes")
    keys = decision.get("keys") if isinstance(decision, dict) else None
    confirmed = set(keys) if isinstance(keys, list) else set()
    return [c["key"] for c in found["destructive"] if c["key"] not in confirmed]


def _read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main(argv):
    parser = argparse.ArgumentParser(description="Ordered plan of the shared elements to create")
    parser.add_argument("dir")
    parser.add_argument("--ready", action="store_true", help="only entries whose dependencies are recorded")
    parser.add_argument("--changes", action="store_true", help="changes on the recorded shared elements")
    args = parser.parse_args(argv[1:])
    try:
        state = _read_json(os.path.join(args.dir, "state.json"))
        products_path = os.path.join(args.dir, "products.json")
        columns = _read_json(products_path).get("metafield_columns") if os.path.exists(products_path) else None
        if args.changes:
            found = changes(state, columns)
        else:
            entries = plan(state, columns)
    except (OSError, ValueError, PayloadError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    if args.changes:
        print(dumps(found))
        waiting = unconfirmed(state, found)
        if waiting:
            print(f"{len(waiting)} destructive changes not confirmed for phase {state.get('phase', 1)}: "
                  f"{', '.join(waiting)}", file=sys.stderr)
            return 1
        return 0
    if args.ready:
        entries = [e for e in entries if not e["depends_on"]]
    print(dumps(entries))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
