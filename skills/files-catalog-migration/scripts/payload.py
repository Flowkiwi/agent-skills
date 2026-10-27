"""Print the exact MCP call bodies of one product, from products.json and state.json.

The import.key marker (shared `metafield_definition:import.key`) carries the product handle as its value.

Usage:
  python3 payload.py <dir> <handle> [--step product|options|variants|metafields|taxonomy|all] [--dry-run]

Workers copy every value verbatim from this output. Invisible or special whitespace characters
(no-break spaces, zero-width characters, soft hyphens, control characters...) are printed as
\\uXXXX escapes: keep them as is in the JSON arguments of the MCP calls.
--dry-run prints `<key IRI>` placeholders for shared elements not recorded yet instead of failing.

Module fields come from machine-readable decisions: `taxonomy` as written by taxonomy.py build
(category and attributes, IRIs from shared `category:<path>`, `attribute_definition:<code>`,
`attribute_option:<code>:<value>`), and `metafields` as
{"definitions": {"<namespace.key>": {"type": "<flowkiwi type>", "name": "...", "transform": null,
"source": "<products.json metafield key, when renamed>"}}}
with transform null (value as is), list_json, list, number, json or strip_html.
"""

import argparse
import html
import json
import os
import re
import sys
import unicodedata

STATUSES = {"active": "ACTIVE", "draft": "DRAFT", "archived": "ARCHIVED"}
MARKER_KEY = "metafield_definition:import.key"
STEPS = ("product", "options", "variants", "metafields", "taxonomy", "all")
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


def product_body(product, state, placeholders=False):
    body = {"title": product["title"]}
    if product.get("description_html"):
        body["description"] = product["description_html"]
    if product.get("tags"):
        body["tags"] = ",".join(product["tags"])
    body["status"] = status_iri(product, state.get("decisions", {}))
    body.update(taxonomy_fields(product, state, placeholders))
    return body


def options(product):
    return [
        {"title": option["name"], "position": index,
         "values": [{"value": value, "position": i} for i, value in enumerate(option["values"])]}
        for index, option in enumerate(product["options"])
    ]


def variants(product):
    return [
        {"sku": v["sku"], "barcode": v["barcode"], "option_values": list(v["option_values"])}
        for v in product["variants"]
    ]


def _list_values(value):
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


def metafields(product, state, placeholders=False):
    decision = _module_decision(state.get("decisions", {}), "metafields", "definitions")
    if decision is None:
        return []
    result = []
    for key, definition in sorted(decision["definitions"].items()):
        definition = definition or {}
        value = product.get("metafields", {}).get(definition.get("source") or key)
        if key == "import.key" or value in (None, ""):
            continue
        shared_key = f"metafield_definition:{key}"
        body = {"definition": shared_iri(state, shared_key, placeholders),
                "value": transform_value(value, definition.get("transform"))}
        result.append({"key": key, "shared": shared_key, "body": body})
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
    if step == "taxonomy":
        payload["taxonomy"] = taxonomy_fields(product, state, placeholders)
    return payload


def _read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main(argv):
    parser = argparse.ArgumentParser(description="Print the MCP call bodies of one product")
    parser.add_argument("dir")
    parser.add_argument("handle")
    parser.add_argument("--step", choices=STEPS, default="all")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv[1:])

    try:
        products = _read_json(os.path.join(args.dir, "products.json"))
        state = _read_json(os.path.join(args.dir, "state.json"))
        payload = build(products, state, args.handle, args.step, args.dry_run)
    except (OSError, ValueError, PayloadError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(dumps(payload))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
