"""Metafield helpers: the Flowkiwi metafield types and the type suggested for a set of values (no CLI)."""
import json
import re

METAFIELD_TYPES = ("single_line_text_field", "multi_line_text_field", "number_integer", "number_decimal", "json",
                   "list__single_line_text_field", "list__number_integer", "list__number_decimal")
INTEGER = re.compile(r"^-?\d+$")
DECIMAL = re.compile(r"^-?\d+[.,]\d+$")
HTML = re.compile(r"<[a-zA-Z][^>]*>")
SINGLE_LINE_MAX = 255


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
