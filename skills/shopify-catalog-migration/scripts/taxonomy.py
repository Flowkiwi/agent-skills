"""Preview and build the category tree and tag attributes of the taxonomy module.

Usage:
  python3 taxonomy.py preview <products.json> --source product_category|type [options]
  python3 taxonomy.py build <products.json> --source product_category|type [options] --output taxonomy.json

Options (applied in this order):
  --map mapping.json          {"<source value>": "<target path>" or null}, applied to the raw source value
  --drop-root                 remove the first level of every path
  --depth N                   keep the first N levels
  --rename 'Old path=New name' (repeatable) rename the node at that path (path after the steps above)
  --attribute <tag key>       (repeatable) only these tag keys become attributes; default: every key:value tag key
  --no-attributes             no attribute from tags

preview prints the tree with product counts, products without category with a suggestion (type or tags),
attribute candidates and the category count to use in the dry-run. build writes the machine-readable
decision, recorded with: state.py decide <dir> --key taxonomy --value-file taxonomy.json
"""

import argparse
import json
import os
import re
import sys
import tempfile

SEP = " > "
SLUG = re.compile(r"^[a-z0-9]+([-_][a-z0-9]+)*$")
MIN_NAME_LENGTH = 3


def split_path(value):
    return [s.strip() for s in (value or "").split(">") if s.strip()]


def target_path(product, source, mapping=None, drop_root=False, depth=None, renames=None):
    raw = product.get(source)
    if mapping and raw in mapping:
        segments = split_path(mapping[raw])
    elif source == "product_category":
        segments = split_path(raw)
    else:
        segments = [raw.strip()] if raw and raw.strip() else []
    if drop_root:
        segments = segments[1:]
    if depth is not None:
        segments = segments[:depth]
    renamed = list(segments)
    for i in range(len(segments)):
        new_name = (renames or {}).get(SEP.join(segments[:i + 1]))
        if new_name:
            renamed[i] = new_name
    return SEP.join(renamed) or None


def option_label(raw):
    """Readable label for slug-like tag values: gros-format -> Gros format."""
    if not SLUG.match(raw):
        return raw
    text = re.sub(r"[-_]", " ", raw)
    return text[0].upper() + text[1:]


def attribute_code(key):
    return re.sub(r"[^a-z0-9_-]+", "-", key.strip().lower()).strip("-")[:64]


def tag_attributes(product):
    values = {}
    for tag in product.get("tags", []):
        key, sep, value = tag.partition(":")
        if sep and key.strip() and value.strip():
            values.setdefault(key.strip(), [])
            if value.strip() not in values[key.strip()]:
                values[key.strip()].append(value.strip())
    return values


def _common_ancestor(paths):
    split = [p.split(SEP) for p in paths]
    common = []
    for parts in zip(*split):
        if len(set(parts)) != 1:
            break
        common.append(parts[0])
    if common:
        return [SEP.join(common)]
    return sorted({parts[0] for parts in split})


def build(products, source, mapping=None, drop_root=False, depth=None, renames=None, attribute_keys=None,
          attributes=True):
    assignments, uncategorized, warnings = {}, [], []
    for product in products:
        path = target_path(product, source, mapping, drop_root, depth, renames)
        if path:
            assignments[product["handle"]] = path
        else:
            uncategorized.append(product["handle"])

    categories, seen = [], set()
    for path in sorted(set(assignments.values()), key=lambda p: p.split(SEP)):
        parts = path.split(SEP)
        for i in range(1, len(parts) + 1):
            node = SEP.join(parts[:i])
            if node in seen:
                continue
            seen.add(node)
            categories.append({"path": node, "name": parts[i - 1], "parent": SEP.join(parts[:i - 1]) or None})
            if len(parts[i - 1]) < MIN_NAME_LENGTH:
                warnings.append(f"category '{node}': name shorter than {MIN_NAME_LENGTH} characters, rename it")

    definitions, dropped = {}, {}
    for product in products if attributes else []:
        for key, raws in tag_attributes(product).items():
            if attribute_keys and key not in attribute_keys:
                continue
            code = attribute_code(key)
            if product["handle"] not in assignments:
                dropped.setdefault(code, []).append(product["handle"])
                continue
            definition = definitions.setdefault(code, {
                "code": code, "name": option_label(key), "type": "list", "multiple": False,
                "categories": [], "options": [], "values": {}, "source": f"tags {key}:<value>",
            })
            labels = []
            for raw in raws:
                label = option_label(raw)
                if label not in [o["value"] for o in definition["options"]]:
                    definition["options"].append({"value": label, "source": raw})
                if label not in labels:
                    labels.append(label)
            if len(labels) > 1:
                definition["multiple"] = True
            definition["values"][product["handle"]] = labels
    for definition in definitions.values():
        paths = {assignments[h] for h in definition["values"]}
        definition["categories"] = _common_ancestor(sorted(paths)) if paths else []
        if not 2 <= len(definition["code"]) <= 64:
            warnings.append(f"attribute '{definition['code']}': code must be 2-64 characters")
    for code, handles in dropped.items():
        warnings.append(f"attribute '{code}': {len(handles)} products without category lose their value "
                        f"({', '.join(handles[:5])}{', ...' if len(handles) > 5 else ''})")

    return {
        "source": source, "categories": categories, "products": assignments,
        "attributes": list(definitions.values()), "uncategorized": uncategorized, "warnings": warnings,
    }


def suggestions(products, taxonomy):
    """Suggested category for each product without one: its type, or a tag, matching a category name."""
    by_name = {}
    for category in taxonomy["categories"]:
        by_name.setdefault(category["name"].lower(), category["path"])
    result = {}
    for product in products:
        if product["handle"] not in taxonomy["uncategorized"]:
            continue
        product_type = (product.get("type") or "").strip()
        tag = next((t for t in product.get("tags", []) if t.lower() in by_name), None)
        if product_type and product_type.lower() in by_name:
            result[product["handle"]] = f"{by_name[product_type.lower()]} (from type)"
        elif tag:
            result[product["handle"]] = f"{by_name[tag.lower()]} (from tag '{tag}')"
        elif product_type and taxonomy["source"] != "type":
            result[product["handle"]] = f"new category '{product_type}' (from type)"
        else:
            result[product["handle"]] = None
    return result


def format_preview(products, taxonomy):
    counts = {}
    for path in taxonomy["products"].values():
        parts = path.split(SEP)
        for i in range(1, len(parts) + 1):
            node = SEP.join(parts[:i])
            counts[node] = counts.get(node, 0) + 1
    lines = [f"Category tree (source: {taxonomy['source']}), products per node including descendants:"]
    for category in taxonomy["categories"]:
        indent = "  " * category["path"].count(SEP)
        lines.append(f"{indent}{category['name']} ({counts.get(category['path'], 0)})")
    lines.append(f"Without category: {len(taxonomy['uncategorized'])} products")
    for handle, suggestion in suggestions(products, taxonomy).items():
        lines.append(f"  {handle}: {suggestion or 'no suggestion'}")
    if taxonomy["attributes"]:
        lines.append("Attribute candidates (tags key:value):")
        for d in taxonomy["attributes"]:
            options = ", ".join(
                o["value"] if o["value"] == o["source"] else f"{o['value']} (from {o['source']})" for o in d["options"]
            )
            lines.append(f"  {d['code']} '{d['name']}': {len(d['values'])} products, "
                         f"{'multiple' if d['multiple'] else 'single'} value, on {d['categories']}, options: {options}")
    for warning in taxonomy["warnings"]:
        lines.append(f"warning: {warning}")
    lines.append(f"{len(taxonomy['categories'])} categories")
    return "\n".join(lines)


def _read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _write_json(path, data):
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def main(argv):
    parser = argparse.ArgumentParser(description="Taxonomy module helpers")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("preview", "build"):
        p = sub.add_parser(name)
        p.add_argument("products")
        p.add_argument("--source", required=True, choices=["product_category", "type"])
        p.add_argument("--map")
        p.add_argument("--drop-root", action="store_true")
        p.add_argument("--depth", type=int)
        p.add_argument("--rename", action="append", default=[])
        p.add_argument("--attribute", action="append", default=[])
        p.add_argument("--no-attributes", action="store_true")
        if name == "build":
            p.add_argument("--output", required=True)
    args = parser.parse_args(argv[1:])

    renames = {}
    for rename in args.rename:
        old, sep, new = rename.partition("=")
        if not sep or not old.strip() or not new.strip():
            print(f"error: --rename expects 'Old path=New name', got '{rename}'", file=sys.stderr)
            return 1
        renames[SEP.join(split_path(old))] = new.strip()
    try:
        products = _read_json(args.products)["products"]
        mapping = _read_json(args.map) if args.map else None
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    taxonomy = build(products, args.source, mapping, args.drop_root, args.depth, renames,
                     set(args.attribute) or None, not args.no_attributes)
    if args.command == "preview":
        print(format_preview(products, taxonomy))
        return 0
    _write_json(args.output, taxonomy)
    print(f"{len(taxonomy['categories'])} categories, {len(taxonomy['products'])} products with a category, "
          f"{len(taxonomy['attributes'])} attributes, written to {args.output}")
    for warning in taxonomy["warnings"]:
        print(f"warning: {warning}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
