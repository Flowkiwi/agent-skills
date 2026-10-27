"""Ordered plan of the shared elements to create, from the decisions in state.json.

Usage:
  python3 plan_shared.py <dir> [--ready]

Covers the core import.key marker definition, then (when decided) categories parents first, attribute definitions,
attribute options, collections parents first, metafield definitions. Keys already recorded `created` or `reused`
in state.json are skipped. Every entry carries a `spec` (what was asked), recorded with the IRI to detect later drift.
A body referencing a shared element not recorded yet holds a `<key IRI>` placeholder and lists it
in `depends_on`; --ready prints only the entries whose dependencies are all recorded. Other modules'
shared elements (countries, taxes, suppliers, channels) follow their references.
"""

import argparse
import json
import os
import sys

from payload import MARKER_KEY, PayloadError, dumps, shared_iri

OWNER_TYPE = "/api/metafield_owner_types/products"
TYPE_PREFIX = "/api/metafield_definition_types/"
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
    if not _recorded(state, key):
        depends_on.append(key)
    return shared_iri(state, key, placeholders=True)


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


def plan(state, metafield_columns=None):
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

    metafields = decisions.get("metafields")
    if isinstance(metafields, dict) and "definitions" in metafields:
        for namespace_key, definition in sorted(metafields["definitions"].items()):
            if namespace_key == "import.key":
                continue
            definition = definition or {}
            source = definition.get("source") or namespace_key
            name = definition.get("name") or (metafield_columns or {}).get(source) or namespace_key
            if not definition.get("type"):
                raise PayloadError(f"metafield definition '{namespace_key}' has no type in decision 'metafields'")
            add(f"metafield_definition:{namespace_key}", "metafields", "create_metafield_definition",
                f"list_metafield_definitions: namespace.key {namespace_key}, owner type products",
                _metafield_definition(namespace_key, name, definition["type"]),
                {"type": definition["type"], "name": name})

    return [e for e in entries if not _recorded(state, e["key"])]


def _read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main(argv):
    parser = argparse.ArgumentParser(description="Ordered plan of the shared elements to create")
    parser.add_argument("dir")
    parser.add_argument("--ready", action="store_true", help="only entries whose dependencies are recorded")
    args = parser.parse_args(argv[1:])
    try:
        state = _read_json(os.path.join(args.dir, "state.json"))
        products_path = os.path.join(args.dir, "products.json")
        columns = _read_json(products_path).get("metafield_columns") if os.path.exists(products_path) else None
        entries = plan(state, columns)
    except (OSError, ValueError, PayloadError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    if args.ready:
        entries = [e for e in entries if not e["depends_on"]]
    print(dumps(entries))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
