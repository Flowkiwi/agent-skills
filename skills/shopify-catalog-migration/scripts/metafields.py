"""Metafield helpers: propose definitions from the product CSV columns, compare them with declared ones.

Usage:
  python3 metafields.py infer <products.json> [--output <metafield-definitions.csv>]
  python3 metafields.py diff <products.json> <metafield-definitions.csv>

infer prints, per metafield column, its label, value count and a suggested Flowkiwi type; --output writes
them as owner,namespace_key,name,type (Flowkiwi type names). diff lists declared product definitions
without a column in the export, and columns without a definition.
"""

import argparse
import csv
import json
import os
import re
import sys
import tempfile

INTEGER = re.compile(r"^-?\d+$")
DECIMAL = re.compile(r"^-?\d+[.,]\d+$")
HTML = re.compile(r"<[a-zA-Z][^>]*>")
DEFINITION_COLUMNS = ("owner", "namespace_key", "name", "type")
SINGLE_LINE_MAX = 255


def _json(value):
    try:
        return json.loads(value)
    except ValueError:
        return None


def suggest_type(values):
    """Return (Flowkiwi type, note or None) for the raw CSV values of one metafield."""
    if not values:
        return "single_line_text_field", "no value in the export"
    parsed = [_json(v) for v in values]
    if all(isinstance(p, list) for p in parsed):
        if all(isinstance(x, str) for p in parsed for x in p):
            return "list__single_line_text_field", None
        return "json", None
    if all(isinstance(p, (dict, list)) for p in parsed):
        if all(isinstance(p, dict) and p.get("type") == "root" for p in parsed):
            return "multi_line_text_field", "Shopify rich text JSON: convert to text, or keep as json (ask)"
        return "json", None
    if all(INTEGER.match(v) for v in values):
        return "number_integer", None
    if all(INTEGER.match(v) or DECIMAL.match(v) for v in values):
        return "number_decimal", None
    if any(HTML.search(v) for v in values):
        return "multi_line_text_field", "HTML values (rich text), kept as text"
    if any("\n" in v for v in values):
        return "multi_line_text_field", None
    if all(v.lower() in ("true", "false") for v in values):
        return "single_line_text_field", "boolean values: Flowkiwi has no boolean type"
    if max(len(v) for v in values) <= SINGLE_LINE_MAX:
        return "single_line_text_field", None
    return "multi_line_text_field", None


def _columns(data):
    """namespace.key -> label, from the export columns (or the values of an older products.json)."""
    labels = dict(data.get("metafield_columns") or {})
    for product in data["products"]:
        for key in product.get("metafields", {}):
            labels.setdefault(key, key)
    return labels


def infer(data):
    result = []
    for key, label in sorted(_columns(data).items()):
        values = [
            p["metafields"][key] for p in data["products"]
            if p.get("metafields", {}).get(key) not in (None, "")
        ]
        kind, note = suggest_type(values)
        if key.startswith("shopify."):
            note = "; ".join(n for n in (note, "Shopify standard metafield, often better as taxonomy") if n)
        result.append({"namespace_key": key, "name": label, "values": len(values), "type": kind, "note": note})
    return result


def read_definitions(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        missing = [c for c in DEFINITION_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"{path}: missing columns {missing}, expected {','.join(DEFINITION_COLUMNS)}")
        return [{c: (row.get(c) or "").strip() for c in DEFINITION_COLUMNS} for row in reader]


def diff(data, definitions):
    columns = _columns(data)
    products = [d for d in definitions if d["owner"].lower().startswith("product")]
    declared = {d["namespace_key"] for d in products}
    return {
        "definitions_without_column": [d for d in products if d["namespace_key"] not in columns],
        "columns_without_definition": [
            {"namespace_key": key, "name": label} for key, label in sorted(columns.items()) if key not in declared
        ],
        "other_owner_definitions": [d for d in definitions if not d["owner"].lower().startswith("product")],
    }


def write_definitions(path, inferred):
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(DEFINITION_COLUMNS)
        for entry in inferred:
            writer.writerow(["product", entry["namespace_key"], entry["name"], entry["type"]])
    os.replace(tmp, path)


def _read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main(argv):
    parser = argparse.ArgumentParser(description="Metafield definitions helpers")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("infer")
    p.add_argument("products")
    p.add_argument("--output")
    p = sub.add_parser("diff")
    p.add_argument("products")
    p.add_argument("definitions")
    args = parser.parse_args(argv[1:])

    try:
        data = _read_json(args.products)
        if args.command == "infer":
            inferred = infer(data)
            print(json.dumps(inferred, ensure_ascii=False, indent=2))
            if args.output:
                write_definitions(args.output, inferred)
                print(f"{len(inferred)} definitions written to {args.output} (Flowkiwi type names)")
            return 0
        result = diff(data, read_definitions(args.definitions))
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["definitions_without_column"]:
        print("The Shopify product CSV omits some metafield types (json among others): get those values another "
              "way (bulk editor, app export) or accept they are not migrated.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
