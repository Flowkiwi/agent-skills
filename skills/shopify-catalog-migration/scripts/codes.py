"""Consolidation helpers for products.json: fixes, barcodes (EAN-13) and SKUs.

Usage:
  python3 codes.py fix <products.json> <fixes.json>
  python3 codes.py preview <products.json> --prefix 2 --start 1 [--sku-pattern "{handle}-{index}"]
  python3 codes.py fill <products.json> --prefix 2 --start 1 [--sku-pattern "{handle}-{index}"] [--previous <old products.json>]

fixes.json: {"skus": {"<handle>": {"<old sku>": "<new sku>"}},
             "option_names": {"<handle>": {"<old name>": "<new name>"}},
             "skip": ["<handle>", ...]}
Generated SKUs are uppercased.
"""

import argparse
import copy
import json
import sys


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


def apply_fixes(data, fixes):
    skip = set(fixes.get("skip", []))
    data["products"] = [p for p in data["products"] if p["handle"] not in skip]
    for product in data["products"]:
        sku_renames = fixes.get("skus", {}).get(product["handle"], {})
        for variant in product["variants"]:
            if variant["sku"] in sku_renames:
                variant["sku"] = sku_renames[variant["sku"]]
        name_renames = fixes.get("option_names", {}).get(product["handle"], {})
        for option in product["options"]:
            if option["name"] in name_renames:
                option["name"] = name_renames[option["name"]]
    return data


def _variant_key(product, variant):
    return product["handle"], tuple(variant["option_values"])


def _carry(data, previous):
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
    carried = _carry(data, previous) if previous else 0
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


def _write(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


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
        apply_fixes(data, _read(args.fixes))
        _write(args.products, data)
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
    _write(args.products, data)
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
