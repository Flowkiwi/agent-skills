"""Metafields: the Flowkiwi metafield types, the type suggested for a set of values and the metafields concept.

The concept is used by `extract.py <dir> metafields` with mappings/metafields.json (see references/metafields.md);
it writes concepts/metafields.json in the decision format read by plan_shared.py and payload.py, recorded with:
state.py decide <dir> --key metafields --value-file concepts/metafields.json
This module imports no other skill module than catalog and mapping (extract.py imports it).
"""
import json
import re
from typing import Dict, List, Optional

import catalog
import mapping
from mapping import MappingError, evaluate, is_spec, warn

METAFIELD_TYPES = ("single_line_text_field", "multi_line_text_field", "number_integer", "number_decimal", "json",
                   "list__single_line_text_field", "list__number_integer", "list__number_decimal")
INTEGER = re.compile(r"^-?\d+$")
DECIMAL = re.compile(r"^-?\d+[.,]\d+$")
HTML = re.compile(r"<[a-zA-Z][^>]*>")
SINGLE_LINE_MAX = 255
NAME_PART = re.compile(r"^[a-z][a-z0-9_]{2,}$")
MARKER = "import.key"
MIN_NAME_LENGTH = 3
TRANSFORMS = (None, "list_json", "list", "number", "json")
LIST_TRANSFORMS = (None, "list_json", "list")
KEYS = frozenset(("version", "concept", "definitions"))
DEFINITION_KEYS = frozenset(("name", "type", "value", "transform"))
INTEGER_VALUE = re.compile(r"^-?\d+$")
NUMBER_VALUE = re.compile(r"^-?\d+(\.\d+)?$")
PREVIEW_SAMPLE = 40


def _json(value):
    try:
        return json.loads(value)
    except ValueError:
        return None


def suggest_type(values):
    """Return (Flowkiwi type, note or None) for the values of one metafield."""
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


def is_list_type(kind: str) -> bool:
    return kind.startswith("list__")


def default_transform(kind: str) -> Optional[str]:
    if is_list_type(kind):
        return "list_json"
    return "number" if kind.startswith("number_") else None


def check_mapping(m: dict) -> dict:
    """Checks the metafields keys of the mapping and returns its field specs only, for mapping.validate_mapping."""
    if m.get("concept") != "metafields":
        raise MappingError("concept must be 'metafields', got %r" % (m.get("concept"),))
    unknown = sorted(set(m) - KEYS - mapping.DOC_KEYS)
    if unknown:
        raise MappingError("unknown key(s) %s (valid: definitions)" % ", ".join(unknown))
    definitions = m.get("definitions")
    if not isinstance(definitions, dict):
        raise MappingError("definitions: an object {\"<namespace>.<key>\": {\"name\", \"type\", \"value\"}}")
    specs = {}  # type: Dict[str, dict]
    for key, d in definitions.items():
        where = "definitions.%s" % key
        parts = key.split(".")
        if key == MARKER:
            raise MappingError("%s: import.key is the reserved marker of the migration" % where)
        if len(parts) != 2 or not all(NAME_PART.match(p) for p in parts):
            raise MappingError("%s: <namespace>.<key> with a namespace and a key of 3+ characters, lowercase "
                               "letters, digits or _, starting with a letter" % where)
        if not isinstance(d, dict):
            raise MappingError("%s: an object with name, type, value" % where)
        unknown = sorted(set(d) - DEFINITION_KEYS - mapping.DOC_KEYS)
        if unknown:
            raise MappingError("%s: unknown key(s) %s (valid: %s)" % (
                where, ", ".join(unknown), ", ".join(sorted(DEFINITION_KEYS | mapping.DOC_KEYS))))
        if not isinstance(d.get("name"), str) or len(d["name"].strip()) < MIN_NAME_LENGTH:
            raise MappingError("%s.name: a string of 3+ characters (the name in the data language)" % where)
        if d.get("type") not in METAFIELD_TYPES:
            raise MappingError("%s.type: one of %s" % (where, ", ".join(METAFIELD_TYPES)))
        if not is_spec(d.get("value")):
            raise MappingError("%s.value: a field spec ({\"from\": [...]})" % where)
        allowed = LIST_TRANSFORMS if is_list_type(d["type"]) else TRANSFORMS
        if d.get("transform") not in allowed:
            raise MappingError("%s.transform: one of %s for type %s" % (
                where, ", ".join("null" if t is None else t for t in allowed), d["type"]))
        specs[key] = {k: v for k, v in d.items() if k == "value" or k in mapping.DOC_KEYS}
    doc = {k: v for k, v in m.items() if k in mapping.DOC_KEYS}
    return dict(doc, version=m.get("version"), concept="metafields", definitions=specs)


def typed_value(value, kind: str, key: str, handle: str, row: int, warnings: List[dict], cleaner):
    """The value of a product for a definition: a string, or a list for the list types; None when nothing
    usable (not a number for a number type: warning, value dropped). Numbers are normalised by
    mapping.parse_number (decimal comma -> dot)."""
    field = "metafields." + key
    items = [cleaner.final(str(v)) for v in (value if isinstance(value, list) else [value])
             if v is not None and v != ""]
    items = [v for v in items if v]
    if not is_list_type(kind) and len(items) > 1:
        warn(warnings, row, handle, "list_in_single_field", field,
             "%d values %r for a single-value metafield (first one kept): give it a single value or a list type" % (
                 len(items), items))
        items = items[:1]
    base = kind[len("list__"):] if is_list_type(kind) else kind
    pattern = INTEGER_VALUE if base == "number_integer" else NUMBER_VALUE
    kept = []
    for item in items:
        if base.startswith("number_"):
            number = mapping.parse_number(item)
            if number is None or not pattern.match(number):
                warn(warnings, row, handle, "not_a_number", field, "%r is not %s, value dropped" % (
                    item, "an integer" if base == "number_integer" else "a number"))
                continue
            item = number
        if base == "single_line_text_field" and len(item) > SINGLE_LINE_MAX:
            warn(warnings, row, handle, "too_long", field, "value of %d characters (over %d), kept whole: use "
                 "multi_line_text_field" % (len(item), SINGLE_LINE_MAX))
        kept.append(item)
    if not kept:
        return None
    return kept if is_list_type(kind) else kept[0]


def build(products: List[dict], rows: Dict[str, list], m: dict, sources: dict, warnings: List[dict]) -> dict:
    """concepts/metafields.json from the products of products.json, their rows and the metafields mapping."""
    cleaner = next(iter(sources.values())).cleaner
    definitions = {}  # type: Dict[str, dict]
    values = {}  # type: Dict[str, Dict[str, object]]
    for key, d in m["definitions"].items():
        transform = d["transform"] if "transform" in d else default_transform(d["type"])
        definitions[key] = {"type": d["type"], "name": cleaner.final(d["name"]), "transform": transform}
        for product in products:
            handle = product["handle"]
            product_rows = rows.get(handle) or []
            if not product_rows:
                continue
            first = product_rows[0]
            value = typed_value(evaluate(d["value"], first, sources, warnings, "metafields." + key, handle),
                                d["type"], key, handle, first.row, warnings, cleaner)
            if value is not None:
                values.setdefault(handle, {})[key] = value
            for row in product_rows[1:]:
                other = typed_value(evaluate(d["value"], row, sources, [], "metafields." + key, handle),
                                    d["type"], key, handle, row.row, [], cleaner)
                group_conflict(warnings, row.row, handle, "metafields." + key, other, first.row, value)
    return {"definitions": definitions, "values": values, "warnings": warnings}


def group_conflict(warnings: List[dict], row: int, handle: str, field: str, value, kept_row: int, kept) -> None:
    """A later row of a grouped product giving another non-empty value than the first row (kept)."""
    if value is not None and value != kept:
        warn(warnings, row, handle, "group_conflict", field, "row %d gives %r, row %d (kept) gives %r: values come "
             "from the first row of the product" % (row, value, kept_row, kept))


def format_preview(data: dict) -> str:
    samples = {}  # type: Dict[str, List]
    for product_values in data["values"].values():
        for key, value in product_values.items():
            samples.setdefault(key, []).append(value)
    lines = ["Metafield definitions (values come from the first row of each product):"]
    for key, d in data["definitions"].items():
        found = samples.get(key, [])
        sample = ""
        if found:
            text = json.dumps(found[0], ensure_ascii=False) if isinstance(found[0], list) else str(found[0])
            sample = ", e.g. %s" % (text if len(text) <= PREVIEW_SAMPLE else text[:PREVIEW_SAMPLE - 3] + "...")
        lines.append("  %s '%s' (%s, transform %s): %d products%s" % (
            key, d["name"], d["type"], d.get("transform"), len(found), sample))
    if data.get("warnings"):
        lines += catalog.format_warnings(data["warnings"]).splitlines()
    lines.append("%d metafield definitions" % len(data["definitions"]))
    return "\n".join(lines)
