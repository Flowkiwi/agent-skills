"""Validate products.json (pivot format of the files migration) and list blocking warnings.

Usage:
  python3 catalog.py --validate <products.json>
  python3 catalog.py --check <products.json> [--state <state.json>]
"""

import argparse
import json
import os
import sys
import tempfile

KNOWN_STATUSES = ("active", "draft", "archived")
MIN_OPTION_NAME_LENGTH = 3
LIMITS = {"title": 255, "handle": 255}
BLOCKING = (
    "duplicate_key", "duplicate_handle", "missing_title", "too_long", "duplicate_sku",
    "missing_sku", "missing_barcode", "short_option_name", "duplicate_option_values", "missing_option_value",
    "list_in_single_field",
)
WARNING_ORDER = ("non_sellable",)
MAX_LISTED_HANDLES = 10


def _is_zero_or_empty(price):
    if price in (None, ""):
        return True
    try:
        return float(price) == 0
    except ValueError:
        return False


def is_non_sellable(product):
    """No priced variant and no SKU from the sources (generated ones do not count):
    usually content rows, samples or notes stored as products."""
    variants = product["variants"]
    return (
        all(_is_zero_or_empty(v.get("price")) for v in variants)
        and not any(v.get("sku") and not v.get("sku_generated") for v in variants)
    )


def _warn(warnings, row, handle, code, message, field=None):
    warning = {"row": row, "handle": handle, "code": code, "message": message, "stage": "validate"}
    if field:
        warning["field"] = field
    warnings.append(warning)


def _handles_text(handles):
    listed = ", ".join(handles[:MAX_LISTED_HANDLES])
    return listed + (f" and {len(handles) - MAX_LISTED_HANDLES} more" if len(handles) > MAX_LISTED_HANDLES else "")


def _option_name_case(data):
    forms = {}
    for product in data["products"]:
        for option in product.get("options") or []:
            users = forms.setdefault(option["name"].lower(), {}).setdefault(option["name"], [])
            if product["handle"] not in users:
                users.append(product["handle"])
    warnings = []
    rows = {p["handle"]: p["row"] for p in data["products"]}
    for variants in forms.values():
        if len(variants) < 2:
            continue
        majority = max(variants, key=lambda name: len(variants[name]))
        counts = ", ".join(f"'{name}': {len(handles)}" for name, handles in variants.items())
        for name, handles in variants.items():
            if name == majority:
                continue
            _warn(
                warnings, rows[handles[0]], handles[0], "option_name_case",
                f"Option name '{name}' differs only by case from '{majority}', use '{majority}' "
                f"(products per form: {counts}; '{name}' in: {_handles_text(handles)})",
            )
    return warnings


def validate(data):
    """Recompute the validate warnings from the products."""
    warnings = []
    seen_skus = {}
    for product in data["products"]:
        handle, row = product["handle"], product["row"]
        if not (product.get("title") or "").strip():
            _warn(warnings, row, handle, "missing_title", "Product has no title", "title")
        for field, limit in LIMITS.items():
            value = product.get(field)
            if isinstance(value, str) and len(value) > limit:
                _warn(warnings, row, handle, "too_long",
                      f"{field} is too long ({len(value)} > {limit})", field)
        if is_non_sellable(product):
            _warn(warnings, row, handle, "non_sellable",
                  "No variant price and no SKU from the sources: probably not a product (note, sample)")
        status = product.get("status")
        if status is not None and str(status).lower() not in KNOWN_STATUSES:
            _warn(warnings, row, handle, "unknown_status",
                  f"Status '{status}' is not one of {', '.join(KNOWN_STATUSES)}", "status")
        for option in product.get("options") or []:
            if len(option["name"]) < MIN_OPTION_NAME_LENGTH:
                _warn(warnings, row, handle, "short_option_name",
                      f"Option name '{option['name']}' is shorter than {MIN_OPTION_NAME_LENGTH} characters")
        seen_values = set()
        for index, variant in enumerate(product["variants"], start=1):
            values = tuple(variant.get("option_values") or ())
            if values in seen_values:
                _warn(warnings, row, handle, "duplicate_option_values",
                      f"Variant {index} has the same option values as another variant: {list(values)}")
            seen_values.add(values)
            sku = variant.get("sku")
            if not sku:
                _warn(warnings, row, handle, "missing_sku", f"Variant {index} has no SKU")
            elif sku in seen_skus:
                _warn(warnings, row, handle, "duplicate_sku", f"SKU '{sku}' already used by '{seen_skus[sku]}'")
            else:
                seen_skus[sku] = handle
            if not variant.get("barcode"):
                _warn(warnings, row, handle, "missing_barcode", f"Variant {index} has no barcode")
    return warnings + _option_name_case(data)


def refresh(data):
    """Keep the extract warnings, replace the validate ones."""
    kept = [w for w in data.get("warnings", []) if w.get("stage") == "extract"]
    data["warnings"] = kept + validate(data)
    return data


def blocking(data, decisions=None):
    """Blocking warnings; with status_mode source, an empty status is one too unless status_map[""] maps it."""
    decisions = decisions or {}
    status_source = decisions.get("status_mode") == "source"
    status_map = decisions.get("status_map") or {}
    statuses = {}
    for product in data["products"]:
        statuses[(product["handle"], product["row"])] = product.get("status")
    found = []
    for warning in data.get("warnings", []):
        code = warning["code"]
        if code in BLOCKING:
            found.append(warning)
        elif code == "unknown_status" and status_source:
            status = statuses.get((warning["handle"], warning["row"]))
            if str(status).lower() not in status_map:
                found.append(warning)
    if status_source and "" not in status_map:
        for product in data["products"]:
            if not str(product.get("status") or "").strip():
                _warn(found, product["row"], product["handle"], "unknown_status",
                      "No source status: map the empty value in the mapping or record status_map[\"\"]", "status")
    return found


def format_warnings(warnings):
    by_code = {}
    for warning in warnings:
        by_code.setdefault(warning["code"], []).append(warning)
    codes = [c for c in WARNING_ORDER if c in by_code] + [c for c in by_code if c not in WARNING_ORDER]
    lines = []
    for code in codes:
        items = by_code[code]
        lines.append(f"{code} ({len(items)})")
        for w in items:
            lines.append(f"  row {w['row']} {w['handle']}: {w['message']}")
        if code == "non_sellable":
            lines.append("  hint: these products also produce missing_sku (and often missing_barcode) warnings; "
                         "skipping them with a skip_rules fix clears those too")
    return "\n".join(lines)


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _write(path, data):
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".products-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def main(argv):
    parser = argparse.ArgumentParser(description="Validate products.json")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--validate", metavar="PRODUCTS_JSON")
    group.add_argument("--check", metavar="PRODUCTS_JSON")
    parser.add_argument("--state", metavar="STATE_JSON")
    args = parser.parse_args(argv[1:])

    path = args.validate or args.check
    data = refresh(_load(path))
    _write(path, data)

    if args.validate:
        variants = sum(len(p["variants"]) for p in data["products"])
        print(f"{len(data['products'])} products, {variants} variants, {len(data['warnings'])} warnings")
        if data["warnings"]:
            print(format_warnings(data["warnings"]))
        return 0

    decisions = _load(args.state).get("decisions") if args.state else None
    found = blocking(data, decisions)
    if found:
        print(f"{len(found)} blocking warnings")
        print(format_warnings(found))
        return 1
    print("no blocking warnings")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
