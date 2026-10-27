#!/usr/bin/env python3
"""Extract a concept from the source files with its mapping (see references/mapping.md).

Usage: extract.py <dir> products

products: reads mappings/products.json, mappings/cleaning.json, fixes.json, state.json and the previous
products.json when present; writes products.json (always regenerated from the source files) and
fixes.json (only to append new generated keys). Prints the counts, the cleaning counts and the warnings.
"""
import json
import os
import re
import sys
import unicodedata
from decimal import Decimal, InvalidOperation
from typing import Dict, List, Optional, Tuple

import catalog
import clean
import codes
import describe
import mapping
from intake import sha256
from mapping import MappingError, evaluate, is_spec, warn

CONCEPTS = ("products",)
PRODUCT_FIELDS = ("title", "description_html", "vendor", "tags", "status", "product_category", "images")
LIST_FIELDS = ("tags", "images")
VARIANT_FIELDS = ("sku", "barcode", "price", "compare_at_price")
CLEANED_FIELDS = ("title", "description_html", "description_candidate", "description_source", "vendor",
                  "product_category", "status")
GENERATED_KEY = "nokey-%04d"
GENERATED_KEY_RE = re.compile(r"^nokey-(\d+)$")
SUSPECT_FACTOR = 100
SUSPECT_MIN_VALUES = 3


def slugify(value: str) -> str:
    text = unicodedata.normalize("NFKD", value)
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def scalar(value):
    if isinstance(value, list):
        value = value[0] if value else None
    return None if value == "" else value


def normalize(field: str, value):
    if field in LIST_FIELDS:
        if value is None or value == "":
            return []
        return list(value) if isinstance(value, list) else [value]
    return scalar(value)


def usable_key(value) -> Optional[str]:
    value = scalar(value)
    if value is None:
        return None
    value = str(value)
    return value if slugify(value) else None


def identity_spec(spec: dict) -> dict:
    # key and group.key list fallbacks between different identifiers: no conflict unless asked for
    spec = dict(spec)
    spec.setdefault("conflicts", "ignore")
    return spec


def is_numeric(spec: dict) -> bool:
    names = [step if isinstance(step, str) else next(iter(step)) for step in spec.get("transform", [])]
    return ("decimal" in names or "integer" in names) and "scale_above" not in names


def dedupe(values):
    seen = []
    for value in values:
        if value not in seen:
            seen.append(value)
    return seen


def list_source(spec: dict) -> Optional[str]:
    if spec.get("pick") == "all":
        return "pick: all"
    if any(isinstance(step, dict) and "split" in step for step in spec.get("transform", [])):
        return "a split transform"
    if isinstance(spec.get("value"), list):
        return "a list value"
    return None


def check_single(spec: dict, where: str) -> None:
    found = list_source(spec)
    if found:
        raise MappingError("%s: %s gives a list but this field holds a single value (use pick: first or join)" % (
            where, found))


def check_products_mapping(m: dict) -> None:
    if m.get("concept") != "products":
        raise MappingError("concept must be 'products', got %r" % (m.get("concept"),))
    if not is_spec(m.get("key")):
        raise MappingError("key: a field spec is required (the client reference column(s) identifying a row)")
    check_single(m["key"], "key")
    group = m.get("group")
    if group is not None and not (isinstance(group, dict) and is_spec(group.get("key"))):
        raise MappingError("group: null or {\"key\": <field spec>}")
    if group is not None:
        check_single(group["key"], "group.key")
    for section, fields in (("product", PRODUCT_FIELDS), ("variant", VARIANT_FIELDS)):
        specs = m.get(section) or {}
        if not isinstance(specs, dict):
            raise MappingError("%s: an object of field specs" % section)
        for name, spec in specs.items():
            if name not in fields:
                raise MappingError("%s.%s: unknown field (valid: %s)" % (section, name, ", ".join(fields)))
            if not is_spec(spec):
                raise MappingError("%s.%s: a field spec is required" % (section, name))
            if name not in LIST_FIELDS:
                check_single(spec, "%s.%s" % (section, name))
            if "generate" in spec:
                if (section, name) != ("product", "description_html"):
                    raise MappingError("%s.%s: generate is only valid on product.description_html" % (section, name))
                describe.check_generate(spec["generate"], list(m["sources"]), "%s.%s.generate" % (section, name))
    options = m.get("options") or []
    if not isinstance(options, list) or not all(
            isinstance(o, dict) and isinstance(o.get("name"), str) and o["name"] and is_spec(o.get("value"))
            for o in options):
        raise MappingError("options: a list of {\"name\": \"<option name>\", \"value\": <field spec>}")
    for i, option in enumerate(options):
        check_single(option["value"], "options[%d].value" % i)


def assign_generated_keys(keyless: List[Tuple[int, Optional[str]]], titles, entries: List[dict],
                          source_id: str) -> Tuple[Dict[int, str], List[dict]]:
    """Generated keys of the rows without key: an entry of fixes.json is reused for the row with its
    title (the same row first), or for its row when its title is in no current row; else a new key."""
    candidates = [e for e in entries if isinstance(e, dict) and e.get("source") == source_id]
    taken = set()
    assigned = {}  # type: Dict[int, str]

    def claim(matches):
        for row, title in keyless:
            if row in assigned:
                continue
            for i, entry in enumerate(candidates):
                if i not in taken and matches(entry, row, title):
                    taken.add(i)
                    assigned[row] = entry["key"]
                    break

    claim(lambda e, row, title: e.get("title") == title and e.get("row") == row)
    claim(lambda e, row, title: e.get("title") == title)
    claim(lambda e, row, title: e.get("row") == row and e.get("title") not in titles)
    numbers = [int(match.group(1)) for match in (GENERATED_KEY_RE.match(str(e.get("key", ""))) for e in entries
                                                 if isinstance(e, dict)) if match]
    number = max(numbers, default=0)
    new = []
    for row, title in keyless:
        if row not in assigned:
            number += 1
            assigned[row] = GENERATED_KEY % number
            new.append({"source": source_id, "row": row, "title": title, "key": assigned[row]})
    return assigned, new


class ProductExtraction:
    def __init__(self, m, sources, warnings):
        self.mapping = m
        self.sources = sources
        self.warnings = warnings
        self.numeric = {}  # type: Dict[str, List[Tuple[int, str, str]]]

    def field(self, spec, row, name, handle, record=True):
        value = evaluate(spec, row, self.sources, self.warnings if record else [], name, handle)
        if record and is_numeric(spec) and isinstance(value, str):
            self.numeric.setdefault(name, []).append((row.row, handle, value))
        return value

    def single(self, value, row, handle, name):
        """A single-value field never silently keeps the first item of a list (a map target can be one)."""
        if isinstance(value, list) and len(value) > 1:
            warn(self.warnings, row.row, handle, "list_in_single_field", name,
                 "%d values %r for a single-value field (first one kept): give it a single value in the mapping" % (
                     len(value), value))
        return scalar(value)

    def value(self, field, spec, row, name, handle, record=True):
        value = self.field(spec, row, name, handle, record)
        return normalize(field, value) if field in LIST_FIELDS else self.single(value, row, handle, name)

    def identities(self, rows, fixes):
        group = self.mapping.get("group")
        group_spec = identity_spec(group["key"]) if group else None
        key_spec = identity_spec(self.mapping["key"])
        title_spec = (self.mapping.get("product") or {}).get("title")
        idents, titles = [], []
        for row in rows:
            ident = None
            if group_spec:
                ident = usable_key(self.single(evaluate(group_spec, row, self.sources, self.warnings, "group.key"),
                                               row, None, "group.key"))
            if ident is None:
                ident = usable_key(self.single(evaluate(key_spec, row, self.sources, self.warnings, "key"),
                                               row, None, "key"))
            idents.append(ident)
            titles.append(scalar(evaluate(title_spec, row, self.sources, [], "product.title")) if title_spec else None)
        keyless = [(row.row, title) for row, ident, title in zip(rows, idents, titles) if ident is None]
        keyed = [(row, ident, title) for row, ident, title in zip(rows, idents, titles) if ident is not None]
        entries = fixes.get("generated_keys") or []
        source_id = mapping.main_source_id(self.mapping)
        assigned, new = assign_generated_keys(keyless, set(titles), entries, source_id)
        self.superseded(entries, source_id, set(assigned.values()), keyed, set(titles))
        new_keys = {entry["key"] for entry in new}
        for i, row in enumerate(rows):
            if idents[i] is None:
                key = idents[i] = assigned[row.row]
                warn(self.warnings, row.row, slugify(key), "generated_key", "key",
                     "no key in this row: generated key %s (%s)" % (
                         key, "new, written to fixes.json" if key in new_keys else "kept from fixes.json"))
        return idents, new

    def superseded(self, entries, source_id, used, keyed, titles):
        """Warns when an unused generated key of fixes.json matches a row that now has a real key."""
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("source") != source_id or entry.get("key") in used:
                continue
            title = entry.get("title")
            match = None
            if title is not None:
                match = next((k for k in keyed if k[2] == title and k[0].row == entry.get("row")), None) or next(
                    (k for k in keyed if k[2] == title), None)
            if match is None and title not in titles:
                match = next((k for k in keyed if k[0].row == entry.get("row")), None)
            if match is None:
                continue
            row, ident, _ = match
            warn(self.warnings, row.row, slugify(ident), "generated_key_superseded", "key",
                 "the row of generated key %s (row %s, title %r) now has the key %s: product %s replaces %s, "
                 "undo %s first if it was created" % (entry.get("key"), entry.get("row"), title, ident,
                                                       slugify(ident), entry.get("key"), entry.get("key")))

    def groups(self, rows, idents):
        grouping = bool(self.mapping.get("group"))
        groups = {}  # type: Dict[str, list]
        owners = {}  # type: Dict[str, str]
        for row, ident in zip(rows, idents):
            handle = slugify(ident)
            if ident in groups:
                if grouping:
                    groups[ident].append(row)
                else:
                    warn(self.warnings, row.row, handle, "duplicate_key", "key",
                         "key %s already used by row %d, row dropped" % (ident, groups[ident][0].row))
                continue
            if handle in owners:
                owner = owners[handle]
                warn(self.warnings, row.row, handle, "duplicate_handle", "key",
                     "key %s gives handle %s, already used by key %s (row %d), row dropped" % (
                         ident, handle, owner, groups[owner][0].row))
                continue
            owners[handle] = ident
            groups[ident] = [row]
        return groups

    def source_rows(self, group_rows):
        result = {}
        for source_id in self.mapping["sources"]:
            numbers = dedupe([r.source_row(source_id) for r in group_rows if r.source_row(source_id) is not None])
            if numbers:
                result[source_id] = numbers
        return result

    def product_values(self, group_rows, handle):
        specs = self.mapping.get("product") or {}
        values = {}
        first = group_rows[0]
        for field in PRODUCT_FIELDS:
            spec = specs.get(field)
            if spec is None:
                values[field] = normalize(field, None)
                continue
            name = "product." + field
            values[field] = self.value(field, spec, first, name, handle)
            for row in group_rows[1:]:
                other = self.value(field, spec, row, name, handle, record=False)
                if field == "images":
                    values[field] = dedupe(values[field] + other)
                elif other not in (None, []) and other != values[field]:
                    warn(self.warnings, row.row, handle, "group_conflict", name,
                         "row %d gives %r, row %d (kept) gives %r" % (row.row, other, first.row, values[field]))
        return values

    def options(self, group_rows, handle):
        options, per_row = [], [[] for _ in group_rows]
        if len(group_rows) < 2:
            return options, per_row
        for option in self.mapping.get("options") or []:
            name = "options." + option["name"]
            found = [self.single(self.field(option["value"], row, name, handle), row, handle, name)
                     for row in group_rows]
            if all(value is None for value in found):
                continue
            for row, value in zip(group_rows, found):
                if value is None:
                    warn(self.warnings, row.row, handle, "missing_option_value", name,
                         "no %s value for this variant" % option["name"])
            options.append({"name": option["name"], "values": dedupe([v for v in found if v is not None])})
            for values, value in zip(per_row, found):
                values.append(value)
        return options, per_row

    def variant(self, row, handle, option_values):
        specs = self.mapping.get("variant") or {}
        values = {field: self.value(field, specs[field], row, "variant." + field, handle) if field in specs else None
                  for field in VARIANT_FIELDS}
        return {"sku": values["sku"], "sku_generated": False, "barcode": values["barcode"],
                "barcode_generated": False, "option_values": option_values, "price": values["price"],
                "compare_at_price": values["compare_at_price"], "cost": None, "grams": None, "taxable": None,
                "market_prices": {}}

    def product(self, ident, group_rows):
        handle = slugify(ident)
        values = self.product_values(group_rows, handle)
        options, per_row = self.options(group_rows, handle)
        return {
            "handle": handle, "key": ident, "row": group_rows[0].row, "rows": self.source_rows(group_rows),
            "title": values["title"], "description_html": values["description_html"],
            "description_origin": "source" if values["description_html"] else None,
            "vendor": values["vendor"], "type": None, "product_category": values["product_category"],
            "tags": values["tags"], "status": values["status"], "published": None, "options": options,
            "variants": [self.variant(row, handle, option_values) for row, option_values in zip(group_rows, per_row)],
            "images": values["images"], "metafields": {},
        }

    def suspect_units(self):
        for name, items in self.numeric.items():
            numbers = []
            for row, handle, value in items:
                try:
                    numbers.append((row, handle, value, Decimal(value)))
                except InvalidOperation:
                    continue
            positive = sorted(n for _, _, _, n in numbers if n > 0)
            if len(positive) < SUSPECT_MIN_VALUES:
                continue
            middle = len(positive) // 2
            median = positive[middle] if len(positive) % 2 else (positive[middle - 1] + positive[middle]) / 2
            for row, handle, value, number in numbers:
                if number > SUSPECT_FACTOR * median:
                    warn(self.warnings, row, handle, "suspect_unit", name,
                         "%s is more than %d times the median %s of %s: another unit? (scale_above)" % (
                             value, SUSPECT_FACTOR, mapping.plain_decimal(median), name))


def clean_products(products: List[dict], cleaner: clean.Cleaner) -> None:
    """Cleans again every text value that reaches Flowkiwi: map targets, transform results, fixes and generated
    descriptions were written after the cells were cleaned. Cleaning is idempotent: cell values do not change."""
    def text(value):
        return (cleaner.final(value) or None) if isinstance(value, str) else value

    def texts(values):
        return dedupe([v for v in (text(value) for value in values) if v is not None])

    for product in products:
        for field in CLEANED_FIELDS:
            if field in product:
                product[field] = text(product[field])
        for field in LIST_FIELDS:
            product[field] = texts(product.get(field) or [])
        for option in product.get("options") or []:
            option["name"] = text(option["name"]) or ""
            option["values"] = texts(option["values"])
        for variant in product["variants"]:
            for field in VARIANT_FIELDS:
                variant[field] = text(variant[field])
            variant["option_values"] = [text(value) for value in variant["option_values"]]


def extract_products(directory: str, m: dict, fixes: dict, cleaner: clean.Cleaner, state: Optional[dict] = None,
                     previous: Optional[dict] = None) -> dict:
    """products.json data from the source files. New generated keys are appended to fixes["generated_keys"]."""
    mapping.validate_mapping(m)
    check_products_mapping(m)
    sources = mapping.load_sources(directory, m, cleaner)
    warnings = []  # type: List[dict]
    rows = mapping.rows(sources, m, warnings)
    extraction = ProductExtraction(m, sources, warnings)
    idents, new_keys = extraction.identities(rows, fixes)
    if new_keys:
        fixes.setdefault("generated_keys", []).extend(new_keys)
    groups = extraction.groups(rows, idents)
    products = [extraction.product(ident, group_rows) for ident, group_rows in groups.items()]
    extraction.suspect_units()

    data = {
        "version": 1, "products": products, "warnings": warnings, "cleaning": {},
        "sources": {source_id: {"file": m["sources"][source_id]["file"], "sheet": source.table.name,
                                 "sha256": sha256(source.path)}
                    for source_id, source in sources.items()},
        "metafield_columns": {},
    }
    # fields fixes first: a fixed title or vendor is the one a generated description states
    codes.apply_fixes(data, fixes)
    clean_products(data["products"], cleaner)
    decision = ((state or {}).get("decisions") or {}).get("description_generation")
    approved = isinstance(decision, dict) and decision.get("approved") is True
    description_spec = (m.get("product") or {}).get("description_html")
    first_rows = {slugify(ident): group_rows[0] for ident, group_rows in groups.items()}
    for product in data["products"]:
        describe.apply(product, description_spec, first_rows[product["handle"]], sources, warnings, approved)
    clean_products(data["products"], cleaner)
    data["cleaning"] = {rule: cleaner.counts[rule] for rule in clean.VALID_IDS if rule in cleaner.counts}
    if previous:
        codes.carry(data, previous)
    return catalog.refresh(data)


def read_json(path: str, default=None):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        try:
            return json.load(f)
        except ValueError as error:
            raise ValueError("%s is not valid JSON: %s" % (path, error))


def main_products(directory: str) -> dict:
    """The CLI without its output: extracts, writes products.json (and fixes.json) and returns the data."""
    mapping_path = os.path.join(directory, "mappings", "products.json")
    if not os.path.isfile(mapping_path):
        raise MappingError("%s not found: write the products mapping first" % mapping_path)
    m = mapping.load_mapping(mapping_path)
    cleaner = clean.Cleaner(clean.load_config(directory))
    fixes_path = os.path.join(directory, "fixes.json")
    products_path = os.path.join(directory, "products.json")
    fixes = read_json(fixes_path, {})
    known_keys = len(fixes.get("generated_keys") or [])
    state = read_json(os.path.join(directory, "state.json"))
    previous = read_json(products_path)
    data = extract_products(directory, m, fixes, cleaner, state, previous)
    codes.write_json(products_path, data)
    if len(fixes.get("generated_keys") or []) > known_keys:
        codes.write_json(fixes_path, fixes)
    return data


def report(data: dict, decisions: Optional[dict] = None) -> str:
    variants = sum(len(p["variants"]) for p in data["products"])
    lines = ["%d products, %d variants, %d warnings (%d blocking)" % (
        len(data["products"]), variants, len(data["warnings"]), len(catalog.blocking(data, decisions)))]
    if data["cleaning"]:
        lines.append("cleaning:")
        lines += ["  %s: %d" % (rule, count) for rule, count in data["cleaning"].items()]
    if data["warnings"]:
        lines.append(catalog.format_warnings(data["warnings"]))
    return "\n".join(lines)


def main(argv: List[str]) -> int:
    if len(argv) != 3:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    directory, concept = argv[1], argv[2]
    if concept not in CONCEPTS:
        print("error: unknown concept '%s' (available: %s)" % (concept, ", ".join(CONCEPTS)), file=sys.stderr)
        return 2
    try:
        data = main_products(directory)
        state = read_json(os.path.join(directory, "state.json")) or {}
    except (MappingError, ValueError, re.error) as error:
        print("error: %s" % error, file=sys.stderr)
        return 1
    print(report(data, state.get("decisions")))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
