"""Normalize a Shopify product CSV export into products.json.

Usage:
  python3 shopify_csv.py <input.csv> <output.json> [--force]
  python3 shopify_csv.py --validate <products.json>
"""

import argparse
import csv
import json
import os
import re
import sys

METAFIELD_COLUMN = re.compile(r"\(product\.metafields\.([^)]+)\)$")
MARKET_COLUMN = re.compile(r"^(Price|Compare At Price) / (.+)$")
VARIANT_COLUMNS = ("Variant SKU", "Variant Price", "Option1 Value")
MIN_OPTION_NAME_LENGTH = 3


def _value(row, column):
    value = (row.get(column) or "").strip()
    return value or None


def _bool(value):
    if value is None:
        return None
    return value.upper() == "TRUE"


def _int(value):
    if value is None:
        return None
    try:
        return int(float(value))
    except ValueError:
        return None


def _is_default_title(row):
    return _value(row, "Option1 Name") == "Title" and _value(row, "Option1 Value") == "Default Title"


def _start_product(row, line, metafield_columns):
    tags = _value(row, "Tags")
    status = _value(row, "Status")
    options = []
    if not _is_default_title(row):
        for i in (1, 2, 3):
            name = _value(row, f"Option{i} Name")
            if name:
                options.append({"name": name, "values": []})
    return {
        "handle": _value(row, "Handle"),
        "row": line,
        "title": _value(row, "Title"),
        "description_html": _value(row, "Body (HTML)"),
        "vendor": _value(row, "Vendor"),
        "type": _value(row, "Type"),
        "product_category": _value(row, "Product Category"),
        "tags": [t.strip() for t in tags.split(",") if t.strip()] if tags else [],
        "status": status.lower() if status else None,
        "published": _bool(_value(row, "Published")),
        "options": options,
        "variants": [],
        "images": [],
        "metafields": {
            key: _value(row, column)
            for column, key in metafield_columns.items()
            if _value(row, column) is not None
        },
    }


def _variant(row, product, market_columns):
    option_values = []
    for i, option in enumerate(product["options"], start=1):
        value = _value(row, f"Option{i} Value")
        if value is None:
            continue
        option_values.append(value)
        if value not in option["values"]:
            option["values"].append(value)

    market_prices = {}
    for column, (kind, market) in market_columns.items():
        prices = market_prices.setdefault(market, {"price": None, "compare_at_price": None})
        prices["price" if kind == "Price" else "compare_at_price"] = _value(row, column)
    market_prices = {m: p for m, p in market_prices.items() if p["price"] or p["compare_at_price"]}

    return {
        "sku": _value(row, "Variant SKU"),
        "sku_generated": False,
        "barcode": _value(row, "Variant Barcode"),
        "barcode_generated": False,
        "option_values": option_values,
        "price": _value(row, "Variant Price"),
        "compare_at_price": _value(row, "Variant Compare At Price"),
        "cost": _value(row, "Cost per item"),
        "grams": _int(_value(row, "Variant Grams")),
        "taxable": _bool(_value(row, "Variant Taxable")),
        "market_prices": market_prices,
    }


def _warn(warnings, line, handle, code, message):
    warnings.append({"row": line, "handle": handle, "code": code, "message": message})


def parse(path):
    products = []
    by_handle = {}
    warnings = []

    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        columns = reader.fieldnames or []
        metafield_columns = {
            c: m.group(1) for c in columns if (m := METAFIELD_COLUMN.search(c))
        }
        market_columns = {
            c: (m.group(1), m.group(2)) for c in columns if (m := MARKET_COLUMN.match(c))
        }

        previous_end = reader.line_num
        for row in reader:
            line = previous_end + 1
            previous_end = reader.line_num
            handle = _value(row, "Handle")
            if handle is None:
                continue

            product = by_handle.get(handle)
            if product is None:
                if _value(row, "Title") is None:
                    _warn(warnings, line, handle, "orphan_row", "Row for a handle with no title row before it")
                    continue
                product = _start_product(row, line, metafield_columns)
                by_handle[handle] = product
                products.append(product)

            image = _value(row, "Image Src")
            if image and image not in product["images"]:
                product["images"].append(image)

            if not any(_value(row, c) for c in VARIANT_COLUMNS):
                continue

            product["variants"].append(_variant(row, product, market_columns))

    data = {"products": products, "warnings": warnings}
    data["warnings"] = warnings + validate(data)
    return data


def validate(data):
    """Recompute the data warnings (everything except orphan_row) from products."""
    warnings = []
    seen_skus = {}
    for product in data["products"]:
        handle, line = product["handle"], product["row"]
        for option in product["options"]:
            if len(option["name"]) < MIN_OPTION_NAME_LENGTH:
                _warn(
                    warnings, line, handle, "short_option_name",
                    f"Option name '{option['name']}' is shorter than {MIN_OPTION_NAME_LENGTH} characters",
                )
        for index, variant in enumerate(product["variants"], start=1):
            sku = variant["sku"]
            if sku is None:
                _warn(warnings, line, handle, "missing_sku", f"Variant {index} has no SKU")
            elif sku in seen_skus:
                _warn(warnings, line, handle, "duplicate_sku", f"SKU '{sku}' already used by '{seen_skus[sku]}'")
            else:
                seen_skus[sku] = handle
            if variant["barcode"] is None:
                _warn(warnings, line, handle, "missing_barcode", f"Variant {index} has no barcode")
    return warnings


def format_warnings(warnings):
    by_code = {}
    for warning in warnings:
        by_code.setdefault(warning["code"], []).append(warning)
    lines = []
    for code, items in by_code.items():
        lines.append(f"{code} ({len(items)})")
        for w in items:
            lines.append(f"  row {w['row']} {w['handle']}: {w['message']}")
    return "\n".join(lines)


def _write(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def main(argv):
    parser = argparse.ArgumentParser(description="Normalize a Shopify product CSV export")
    parser.add_argument("--validate", metavar="PRODUCTS_JSON")
    parser.add_argument("input", nargs="?")
    parser.add_argument("output", nargs="?")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv[1:])

    if args.validate:
        with open(args.validate, encoding="utf-8") as f:
            data = json.load(f)
        data["warnings"] = validate(data)
        _write(args.validate, data)
    else:
        if not args.input or not args.output:
            parser.error("input and output are required")
        if os.path.exists(args.output) and not args.force:
            print(f"error: {args.output} exists (it may hold consolidation work), use another name or --force",
                  file=sys.stderr)
            return 1
        data = parse(args.input)
        _write(args.output, data)

    variants = sum(len(p["variants"]) for p in data["products"])
    print(f"{len(data['products'])} products, {variants} variants, {len(data['warnings'])} warnings")
    if data["warnings"]:
        print(format_warnings(data["warnings"]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
