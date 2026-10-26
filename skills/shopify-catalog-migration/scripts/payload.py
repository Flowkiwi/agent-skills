"""Print the exact MCP call bodies of one product, from products.json and state.json.

Usage:
  python3 payload.py <dir> <handle> [--step product|options|variants|metafields|all] [--dry-run]

Workers copy every value verbatim from this output. Invisible or special whitespace characters
(no-break spaces, zero-width characters, soft hyphens, control characters...) are printed as
\\uXXXX escapes: keep them as is in the JSON arguments of the MCP calls.
--dry-run prints `<key IRI>` placeholders for shared elements not recorded yet instead of failing.
"""

import argparse
import json
import os
import sys
import unicodedata

STATUSES = {"active": "ACTIVE", "draft": "DRAFT", "archived": "ARCHIVED"}
MARKER_KEY = "metafield_definition:shopify.handle"
STEPS = ("product", "options", "variants", "metafields", "all")
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
    status = product.get("status")
    code = STATUSES.get(status)
    if code is None:
        raise PayloadError(f"Shopify status '{status}' has no mapping")
    return f"/api/statuses/{code}"


def product_body(product, state):
    body = {"title": product["title"]}
    if product.get("description_html"):
        body["description"] = product["description_html"]
    if product.get("tags"):
        body["tags"] = ",".join(product["tags"])
    body["status"] = status_iri(product, state.get("decisions", {}))
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


def metafields(product, state, placeholders=False):
    result = []
    for key, value in sorted(product.get("metafields", {}).items()):
        shared_key = f"metafield_definition:{key}"
        entry = state.get("shared", {}).get(shared_key)
        if key == "shopify.handle" or value in (None, "") or not entry or entry.get("status") == "deleted":
            continue
        result.append({"key": key, "shared": shared_key,
                       "body": {"definition": shared_iri(state, shared_key, placeholders), "value": value}})
    return result


def build(products, state, handle, step="all", placeholders=False):
    product = next((p for p in products["products"] if p["handle"] == handle), None)
    if product is None:
        raise PayloadError(f"handle '{handle}' is not in products.json")
    payload = {"handle": handle}
    if step in ("product", "all"):
        payload["product"] = {"body": product_body(product, state)}
        payload["marker"] = {"shared": MARKER_KEY,
                             "body": {"definition": shared_iri(state, MARKER_KEY, placeholders), "value": handle}}
    if step in ("options", "all"):
        payload["options"] = options(product)
    if step in ("variants", "all"):
        payload["variants"] = variants(product)
    if step in ("metafields", "all"):
        payload["metafields"] = metafields(product, state, placeholders)
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
