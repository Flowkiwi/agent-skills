"""Consolidation helpers for products.json: fixes, barcodes (EAN-13) and SKUs.

Usage:
  python3 codes.py fix <products.json> <fixes.json>
  python3 codes.py preview <products.json> --prefix 2 --start 1 [--sku-pattern "{handle}-{index}"]
  python3 codes.py fill <products.json> --prefix 2 --start 1 [--sku-pattern "{handle}-{index}"] [--previous <old products.json>]

fixes.json: {"skus": {"<handle>": {"<old sku>": "<new sku>"}},
             "option_names": {"<handle> or *": {"<old name>": "<new name>"}},
             "fields": {"<handle>": {"title" | "description_html" | "vendor" | "tags" |
                                     "product_category" | "status": <value>}},
             "skip": ["<handle>", ...],
             "skip_rules": [{"non_sellable": true}, {"metafield_prefix": "custom.recipe_"},
                            {"handle_pattern": "^recipe-"}],
             "generated_keys": [...]}
products.json is built from the source files by extract.py, which applies these fixes itself and
carries the generated codes of the previous products.json; `fix` is for a one-off check.
A product matching any skip rule is skipped (all conditions of one rule must match).
`fields` replace product values after the renames: `tags` a list of strings, the others a string or null; a
`description_html` fix sets `description_origin` to "fixed" (null when the fix is null), never regenerated.
`generated_keys` is kept by extract.py.
Generated SKUs are uppercased.
"""

import argparse
import copy
import json
import os
import re
import sys
import tempfile

from catalog import is_non_sellable

RULE_KEYS = {"non_sellable", "metafield_prefix", "handle_pattern"}
FIELD_KEYS = ("title", "description_html", "vendor", "tags", "product_category", "status")
DEFAULT_MODE = 0o644


def ean13_check_digit(first12):
    total = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(first12))
    return str((10 - total % 10) % 10)


def _barcode(prefix, sequence):
    body = prefix + str(sequence).zfill(12 - len(prefix))
    if len(body) != 12:
        raise ValueError(f"Sequence {sequence} does not fit after prefix '{prefix}'")
    return body + ean13_check_digit(body)


def _generate(prefix, start, count, used):
    """Return (barcodes, next_sequence)."""
    result = []
    sequence = start
    while len(result) < count:
        barcode = _barcode(prefix, sequence)
        sequence += 1
        if barcode not in used:
            result.append(barcode)
    return result, sequence


def generate_barcodes(prefix, start, count, used):
    return _generate(prefix, start, count, used)[0]


def rule_matches(product, rule):
    unknown = set(rule) - RULE_KEYS
    if unknown or not rule:
        raise ValueError(f"Unknown skip rule {json.dumps(rule)}: use {sorted(RULE_KEYS)}")
    if "non_sellable" in rule and is_non_sellable(product) != bool(rule["non_sellable"]):
        return False
    if "metafield_prefix" in rule and not any(
        key.startswith(rule["metafield_prefix"]) and value not in (None, "")
        for key, value in product.get("metafields", {}).items()
    ):
        return False
    if "handle_pattern" in rule and not re.search(rule["handle_pattern"], product["handle"]):
        return False
    return True


def apply_fixes(data, fixes, stats=None):
    """Apply fixes.json; `stats` (a dict, optional) receives the number of products skipped per rule."""
    stats = {} if stats is None else stats
    for handle, values in fixes.get("fields", {}).items():
        unknown = sorted(set(values) - set(FIELD_KEYS))
        if unknown:
            raise ValueError(f"Unknown field(s) {unknown} in fields fix of '{handle}': use {list(FIELD_KEYS)}")
        for field, value in values.items():
            if field == "tags":
                if not isinstance(value, list) or not all(isinstance(tag, str) for tag in value):
                    raise ValueError(f"fields fix of '{handle}': tags must be a list of strings, got {value!r}")
            elif value is not None and not isinstance(value, str):
                raise ValueError(f"fields fix of '{handle}': {field} must be a string or null, got {value!r}")
    skip = set(fixes.get("skip", []))
    rules = fixes.get("skip_rules", [])
    kept = []
    for product in data["products"]:
        if product["handle"] in skip:
            stats["skip"] = stats.get("skip", 0) + 1
            continue
        matched = next((rule for rule in rules if rule_matches(product, rule)), None)
        if matched is not None:
            label = json.dumps(matched, sort_keys=True)
            stats[label] = stats.get(label, 0) + 1
            continue
        kept.append(product)
    data["products"] = kept
    for product in data["products"]:
        sku_renames = fixes.get("skus", {}).get(product["handle"], {})
        for variant in product["variants"]:
            if variant["sku"] in sku_renames:
                variant["sku"] = sku_renames[variant["sku"]]
        name_renames = dict(fixes.get("option_names", {}).get("*", {}))
        name_renames.update(fixes.get("option_names", {}).get(product["handle"], {}))
        for option in product["options"]:
            if option["name"] in name_renames:
                option["name"] = name_renames[option["name"]]
        values = fixes.get("fields", {}).get(product["handle"], {})
        product.update(copy.deepcopy(values))
        if "description_html" in values:
            product["description_origin"] = "fixed" if values["description_html"] else None
    return data


def _variant_key(product, variant):
    return product["handle"], tuple(variant["option_values"])


def carry(data, previous):
    """Copy the generated barcodes and SKUs of `previous` onto the same variants (handle and option
    values) when they are still missing; returns the number of codes carried."""
    generated = {}
    for product in previous["products"]:
        for variant in product["variants"]:
            generated[_variant_key(product, variant)] = variant
    carried = 0
    for product in data["products"]:
        for variant in product["variants"]:
            old = generated.get(_variant_key(product, variant))
            if old is None:
                continue
            for field in ("barcode", "sku"):
                if not variant[field] and old[f"{field}_generated"]:
                    variant[field] = old[field]
                    variant[f"{field}_generated"] = True
                    carried += 1
    return carried


def fill(data, barcode_prefix, barcode_start, sku_pattern, previous=None):
    carried = carry(data, previous) if previous else 0
    all_variants = [(p, i, v) for p in data["products"] for i, v in enumerate(p["variants"], start=1)]
    used = {v["barcode"] for _, _, v in all_variants if v["barcode"]}
    if previous:
        used |= {v["barcode"] for p in previous["products"] for v in p["variants"] if v["barcode"]}
    missing = [v for _, _, v in all_variants if not v["barcode"]]

    barcodes, next_start = _generate(barcode_prefix, barcode_start, len(missing), used)
    for variant, barcode in zip(missing, barcodes):
        variant["barcode"] = barcode
        variant["barcode_generated"] = True

    skus = 0
    if sku_pattern:
        used_skus = {v["sku"] for _, _, v in all_variants if v["sku"]}
        for product, index, variant in all_variants:
            if variant["sku"]:
                continue
            sku = sku_pattern.format(handle=product["handle"], index=index).upper()
            if sku in used_skus:
                raise ValueError(f"Generated SKU '{sku}' already exists, choose another pattern")
            variant["sku"] = sku
            variant["sku_generated"] = True
            used_skus.add(sku)
            skus += 1

    return {"barcodes": len(barcodes), "skus": skus, "carried": carried, "next_barcode_start": next_start}


def preview(data, barcode_prefix, barcode_start, sku_pattern, previous=None):
    trial = copy.deepcopy(data)
    before = {
        (p["handle"], i): (v["barcode"], v["sku"])
        for p in trial["products"] for i, v in enumerate(p["variants"], start=1)
    }
    summary = fill(trial, barcode_prefix, barcode_start, sku_pattern, copy.deepcopy(previous) if previous else None)
    barcode_samples, sku_samples = [], []
    for product in trial["products"]:
        for index, variant in enumerate(product["variants"], start=1):
            old_barcode, old_sku = before[(product["handle"], index)]
            if variant["barcode"] != old_barcode:
                barcode_samples.append({"handle": product["handle"], "variant": index, "barcode": variant["barcode"]})
            if variant["sku"] != old_sku:
                sku_samples.append({"handle": product["handle"], "variant": index, "sku": variant["sku"]})
    return {**summary, "barcode_samples": barcode_samples, "sku_samples": sku_samples}


def _read(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path, data):
    """Atomic write (temp file + rename) keeping the mode of the replaced file, 0644 for a new one."""
    directory = os.path.dirname(os.path.abspath(path))
    mode = os.stat(path).st_mode & 0o7777 if os.path.exists(path) else DEFAULT_MODE
    fd, tmp = tempfile.mkstemp(dir=directory, prefix="." + os.path.basename(path) + "-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def main(argv):
    parser = argparse.ArgumentParser(description="Consolidation helpers for products.json")
    sub = parser.add_subparsers(dest="command", required=True)

    fix_cmd = sub.add_parser("fix")
    fix_cmd.add_argument("products")
    fix_cmd.add_argument("fixes")

    for name in ("preview", "fill"):
        cmd = sub.add_parser(name)
        cmd.add_argument("products")
        cmd.add_argument("--prefix", default="2")
        cmd.add_argument("--start", type=int, default=1)
        cmd.add_argument("--sku-pattern")
        cmd.add_argument("--previous")

    args = parser.parse_args(argv[1:])
    data = _read(args.products)

    if args.command == "fix":
        stats = {}
        try:
            apply_fixes(data, _read(args.fixes), stats)
        except (ValueError, re.error) as error:
            print(f"error: {error}", file=sys.stderr)
            return 1
        write_json(args.products, data)
        for label, count in stats.items():
            print(f"skipped {count} products by {'skip list' if label == 'skip' else 'rule ' + label}")
        print(f"{len(data['products'])} products after fixes")
        return 0

    previous = _read(args.previous) if args.previous else None
    try:
        if args.command == "preview":
            print(json.dumps(preview(data, args.prefix, args.start, args.sku_pattern, previous), indent=2))
            return 0
        summary = fill(data, args.prefix, args.start, args.sku_pattern, previous)
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    write_json(args.products, data)
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
