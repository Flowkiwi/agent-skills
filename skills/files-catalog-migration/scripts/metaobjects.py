"""Metaobjects concept: "cms" data (brand, designer, origin...) as metaobject definitions, one entry per distinct
value, linked to the products by a reference metafield.

Used by `extract.py <dir> metaobjects` with mappings/metaobjects.json; writes concepts/metaobjects.json in the
decision format, recorded with:
state.py decide <dir> --key metaobjects --value-file concepts/metaobjects.json
The values of a product come from its first row. Entry handle = extract.slugify(<entry_key value>).
"""
import re
from typing import Dict, List

import catalog
import mapping
import metafields
from mapping import MappingError, evaluate, is_spec, warn

TYPE_PATTERN = r"^[a-z][a-z0-9_]*$"
TYPE_LENGTH = (2, 64)
FIELD_KEY_PATTERN = r"^[a-z][a-z0-9_]*$"
HANDLE_PATTERN = r"^[a-z0-9][a-z0-9_-]*$"
HANDLE_MAX = 255
STATUSES = ("DRAFT", "ACTIVE")
REFERENCE_TYPES = ("metaobject_reference", "list__metaobject_reference")
FIELD_TYPES = metafields.METAFIELD_TYPES + REFERENCE_TYPES
KEYS = frozenset(("version", "concept", "reference_validation", "definitions"))
DEFINITION_KEYS = frozenset(("type", "name", "display_name_field", "entry_key", "status", "fields", "reference"))
FIELD_KEYS = frozenset(("key", "name", "type", "required", "target", "value"))
REFERENCE_KEYS = frozenset(("metafield", "name"))


def _check_keys(node, allowed, where: str) -> None:
    if not isinstance(node, dict):
        raise MappingError("%s: an object with %s" % (where, ", ".join(sorted(allowed))))
    unknown = sorted(set(node) - allowed - mapping.DOC_KEYS)
    if unknown:
        raise MappingError("%s: unknown key(s) %s (valid: %s)" % (
            where, ", ".join(unknown), ", ".join(sorted(allowed | mapping.DOC_KEYS))))


def _check_name(value, where: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise MappingError("%s: a non-empty string (the name in the data language)" % where)


def _check_field(f, where: str, known_types: List[str]) -> None:
    _check_keys(f, FIELD_KEYS, where)
    if not isinstance(f.get("key"), str) or not re.match(FIELD_KEY_PATTERN, f["key"]):
        raise MappingError("%s.key: lowercase letters, digits or _, starting with a letter" % where)
    _check_name(f.get("name"), where + ".name")
    if f.get("type") not in FIELD_TYPES:
        raise MappingError("%s.type: one of %s" % (where, ", ".join(FIELD_TYPES)))
    if not isinstance(f.get("required", False), bool):
        raise MappingError("%s.required: true or false" % where)
    if f["type"] in REFERENCE_TYPES:
        if f.get("target") not in known_types:
            raise MappingError("%s.target: the type of a metaobject defined earlier in definitions (found %r)" % (
                where, f.get("target")))
    elif "target" in f:
        raise MappingError("%s.target: only for the types %s" % (where, ", ".join(REFERENCE_TYPES)))
    if "value" in f and not is_spec(f["value"]):
        raise MappingError("%s.value: a field spec ({\"from\": [...]})" % where)


def _check_reference(r, where: str, metafields_used: List[str]) -> None:
    _check_keys(r, REFERENCE_KEYS, where)
    key = r.get("metafield")
    parts = key.split(".") if isinstance(key, str) else []
    if key == metafields.MARKER:
        raise MappingError("%s.metafield: import.key is the reserved marker of the migration" % where)
    if len(parts) != 2 or not all(metafields.NAME_PART.match(p) for p in parts):
        raise MappingError("%s.metafield: <namespace>.<key> with a namespace and a key of 3+ characters, "
                           "lowercase letters, digits or _, starting with a letter" % where)
    if key in metafields_used:
        raise MappingError("%s.metafield: %s is already the reference of another definition" % (where, key))
    metafields_used.append(key)
    if not isinstance(r.get("name"), str) or len(r["name"].strip()) < metafields.MIN_NAME_LENGTH:
        raise MappingError("%s.name: a string of 3+ characters (the metafield name in the data language)" % where)


def validate(m: dict) -> None:
    """Checks the metaobjects mapping (its field specs are checked by mapping.validate_mapping)."""
    if not isinstance(m, dict) or m.get("concept") != "metaobjects":
        raise MappingError("concept must be 'metaobjects', got %r" % (m.get("concept") if isinstance(m, dict) else m,))
    _check_keys(m, KEYS, "mapping")
    if not isinstance(m.get("reference_validation", False), bool):
        raise MappingError("reference_validation: true or false")
    definitions = m.get("definitions")
    if not isinstance(definitions, list) or not definitions:
        raise MappingError("definitions: a non-empty list of {\"type\", \"name\", \"display_name_field\", "
                           "\"status\", \"fields\"}")
    types = []  # type: List[str]
    references = []  # type: List[str]
    for i, d in enumerate(definitions):
        where = "definitions[%d]" % i
        _check_keys(d, DEFINITION_KEYS, where)
        kind = d.get("type")
        if (not isinstance(kind, str) or not re.match(TYPE_PATTERN, kind)
                or not TYPE_LENGTH[0] <= len(kind) <= TYPE_LENGTH[1]):
            raise MappingError("%s.type: %d-%d lowercase letters, digits or _, starting with a letter (no hyphen)" % (
                where, TYPE_LENGTH[0], TYPE_LENGTH[1]))
        if kind in types:
            raise MappingError("%s.type: %s is defined twice" % (where, kind))
        where = "definitions.%s" % kind
        _check_name(d.get("name"), where + ".name")
        if d.get("status") not in STATUSES:
            raise MappingError("%s.status: one of %s (status of the entries)" % (where, ", ".join(STATUSES)))
        fields = d.get("fields")
        if not isinstance(fields, list) or not fields:
            raise MappingError("%s.fields: a non-empty list of {\"key\", \"name\", \"type\", \"value\"}" % where)
        keys = []  # type: List[str]
        for j, f in enumerate(fields):
            _check_field(f, "%s.fields[%d]" % (where, j), types)
            if f["key"] in keys:
                raise MappingError("%s.fields: key %s is defined twice" % (where, f["key"]))
            keys.append(f["key"])
        by_key = {f["key"]: f for f in fields}
        for name in ("display_name_field", "entry_key"):
            value = d.get(name, d.get("display_name_field"))
            if value not in by_key:
                raise MappingError("%s.%s: the key of one of its fields (%s), found %r" % (
                    where, name, ", ".join(keys), value))
            if metafields.is_list_type(by_key[value]["type"]):
                raise MappingError("%s.%s: field %s has a list type" % (where, name, value))
        key_field = d.get("entry_key", d["display_name_field"])
        if "value" not in by_key[key_field]:
            raise MappingError("%s.entry_key: field %s has no value spec" % (where, key_field))
        if "reference" in d:
            _check_reference(d["reference"], where + ".reference", references)
        types.append(kind)


def check_mapping(m: dict) -> dict:
    """Checks the metaobjects mapping and returns its field specs only, for mapping.validate_mapping."""
    validate(m)
    specs = {}  # type: Dict[str, dict]
    for d in m["definitions"]:
        for f in d["fields"]:
            if "value" in f:
                specs["%s.%s" % (d["type"], f["key"])] = f["value"]
    doc = {k: v for k, v in m.items() if k in mapping.DOC_KEYS}
    return dict(doc, version=m.get("version"), concept="metaobjects", definitions=specs)


def entry_handle(value) -> str:
    import extract  # function-level: extract imports this module
    return extract.slugify(str(value))[:HANDLE_MAX].rstrip("-")


def field_value(f: dict, kind: str, row, handle: str, sources, warnings: List[dict], cleaner,
                handles: Dict[str, List[str]]):
    """The value of a field for one product row: text and numbers as metafields.typed_value, references as
    target entry handles (an unknown entry is dropped with a warning)."""
    field = "metaobjects.%s.%s" % (kind, f["key"])
    if "value" not in f:
        return None
    raw = evaluate(f["value"], row, sources, warnings, field, handle)
    if f["type"] not in REFERENCE_TYPES:
        local = []  # type: List[dict]
        value = metafields.typed_value(raw, f["type"], f["key"], handle, row.row, local, cleaner)
        for w in local:
            w["field"] = field
        warnings.extend(local)
        return value
    items = [str(v) for v in (raw if isinstance(raw, list) else [raw]) if v is not None and v != ""]
    if f["type"] == "metaobject_reference" and len(items) > 1:
        warn(warnings, row.row, handle, "list_in_single_field", field,
             "%d values %r for a single reference (first one kept): use list__metaobject_reference" % (
                 len(items), items))
        items = items[:1]
    kept = []  # type: List[str]
    for item in items:
        target = entry_handle(cleaner.final(item))
        if target not in handles.get(f["target"], []):
            warn(warnings, row.row, handle, "unknown_entry", field,
                 "%r is not an entry of %s (no product gives it), value dropped" % (item, f["target"]))
        elif target not in kept:
            kept.append(target)
    if not kept:
        return None
    return kept if f["type"] == "list__metaobject_reference" else kept[0]


def output_definition(d: dict, cleaner) -> dict:
    fields = []
    for f in d["fields"]:
        out = {"key": f["key"], "name": cleaner.final(f["name"]), "type": f["type"],
               "required": f.get("required", False)}
        if "target" in f:
            out["target"] = f["target"]
        fields.append(out)
    result = {"type": d["type"], "name": cleaner.final(d["name"]), "displayNameField": d["display_name_field"],
              "status": d["status"], "fields": fields}
    if "reference" in d:
        result["reference"] = {"metafield": d["reference"]["metafield"],
                               "name": cleaner.final(d["reference"]["name"])}
    return result


def merge_entry(entry: dict, fields: Dict[str, object], kind: str, handle: str, row: int,
                warnings: List[dict], reported: set) -> None:
    """Merges the fields of one product into its entry: the first non-empty value of each field is kept, two
    different non-empty values are an entry_conflict (reported once per value)."""
    for key, value in fields.items():
        if key not in entry["fields"]:
            entry["fields"][key] = value
            entry["origins"][key] = handle
        elif entry["fields"][key] != value and (entry["handle"], key, repr(value)) not in reported:
            reported.add((entry["handle"], key, repr(value)))
            warn(warnings, row, handle, "entry_conflict", "metaobjects.%s.%s" % (kind, key),
                 "entry %s of %s: %s = %r here, %r kept (product %s): map the variants to one spelling" % (
                     entry["handle"], kind, key, value, entry["fields"][key], entry["origins"][key]))


def build(products: List[dict], rows: Dict[str, list], m: dict, sources: dict, warnings: List[dict]) -> dict:
    """concepts/metaobjects.json from the products of products.json, their rows and the metaobjects mapping."""
    cleaner = next(iter(sources.values())).cleaner
    entries = []  # type: List[dict]
    handles = {}  # type: Dict[str, List[str]]
    values = {}  # type: Dict[str, Dict[str, str]]
    for d in m["definitions"]:
        kind = d["type"]
        key_field = d.get("entry_key", d["display_name_field"])
        merged = {}  # type: Dict[str, dict]
        links = []  # type: List[tuple]
        reported = set()
        for product in products:
            handle = product["handle"]
            product_rows = rows.get(handle) or []
            if not product_rows:
                continue
            first = product_rows[0]
            fields = {}  # type: Dict[str, object]
            for f in d["fields"]:
                value = field_value(f, kind, first, handle, sources, warnings, cleaner, handles)
                if value is not None:
                    fields[f["key"]] = value
                for row in product_rows[1:]:
                    other = field_value(f, kind, row, handle, sources, [], cleaner, handles)
                    metafields.group_conflict(warnings, row.row, handle, "metaobjects.%s.%s" % (kind, f["key"]),
                                              other, first.row, value)
            if key_field not in fields:
                if fields:
                    warn(warnings, first.row, handle, "no_entry_key", "metaobjects.%s.%s" % (kind, key_field),
                         "no %s value for this product: its other %s fields are not used" % (key_field, kind))
                continue
            entry = entry_handle(fields[key_field])
            if not entry:
                warn(warnings, first.row, handle, "no_handle", "metaobjects.%s.%s" % (kind, key_field),
                     "%r gives no entry handle (no letter or digit)" % (fields[key_field],))
                continue
            if entry not in merged:
                merged[entry] = {"handle": entry, "fields": {}, "origins": {}, "row": first.row, "product": handle}
            merge_entry(merged[entry], fields, kind, handle, first.row, warnings, reported)
            links.append((handle, entry))
        handles[kind] = []
        for entry, e in merged.items():
            missing = [f["key"] for f in d["fields"] if f.get("required") and f["key"] not in e["fields"]]
            if missing:
                linked = sum(1 for _, target in links if target == entry)
                warn(warnings, e["row"], e["product"], "missing_required", "metaobjects.%s.%s" % (kind, missing[0]),
                     "entry %s of %s has no value for the required field(s) %s in any product: entry dropped, "
                     "%d product(s) not linked" % (entry, kind, ", ".join(missing), linked))
                continue
            handles[kind].append(entry)
            entries.append({"definition": kind, "handle": entry, "fields": {
                f["key"]: e["fields"][f["key"]] for f in d["fields"] if f["key"] in e["fields"]}})
        if "reference" in d:
            for handle, entry in links:
                if entry in handles[kind]:
                    values.setdefault(handle, {})[kind] = entry
    return {"reference_validation": m.get("reference_validation", False),
            "definitions": [output_definition(d, cleaner) for d in m["definitions"]],
            "entries": entries, "values": values, "warnings": warnings}


def format_preview(data: dict) -> str:
    lines = ["Metaobject definitions (values come from the first row of each product):"]
    for d in data["definitions"]:
        entries = [e for e in data["entries"] if e["definition"] == d["type"]]
        linked = sum(1 for v in data["values"].values() if d["type"] in v)
        reference = " (%s)" % d["reference"]["metafield"] if "reference" in d else ""
        sample = ", e.g. %s" % ", ".join(e["handle"] for e in entries[:3]) if entries else ""
        lines.append("  %s '%s'%s: %d entries, %d products%s" % (
            d["type"], d["name"], reference, len(entries), linked, sample))
    if data.get("warnings"):
        lines += catalog.format_warnings(data["warnings"]).splitlines()
    lines.append("%d metaobject definitions, %d entries" % (len(data["definitions"]), len(data["entries"])))
    return "\n".join(lines)
