#!/usr/bin/env python3
"""Declarative mappings: which source columns give each field, and how their values are converted.

A mapping is a JSON file (`mappings/<concept>.json`, see references/mapping.md). This module checks it,
loads its sources, joins the secondary sources to the main one and evaluates field specs row by row.
"""
import html
import json
import os
import re
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

import clean
from inventory import detect_header_rows
from sources import column_index, column_letter, read_source

ORIGINS = ("developer", "agent", "client")
TRANSFORMS = ("decimal", "integer", "boolean", "split", "scale_above", "regex", "text_to_html", "strip_html")
ARGUMENT_TRANSFORMS = ("boolean", "split", "scale_above", "regex")
PICKS = ("first", "all", "join")
CONFLICTS = ("warn", "ignore")
STAGE = "extract"

NUMBER_RE = re.compile(r"^-?\d+(\.\d+)?$")
LETTERS_RE = re.compile(r"^[A-Za-z]{1,3}$")
NUMBER_SUFFIXES = ("€", "eur", "%")
YES = frozenset("oui yes y true vrai 1 x".split())
NO = frozenset("non no n false faux 0".split())


class MappingError(Exception):
    pass


def warn(warnings, row, handle, code, field, message):
    if warnings is not None:
        warnings.append({"row": row, "handle": handle, "code": code, "field": field, "message": message,
                         "stage": STAGE})


def split_ref(ref: Any) -> Tuple[str, str]:
    if not isinstance(ref, str) or ":" not in ref:
        raise MappingError("column reference %r must be '<source id>:<column>'" % (ref,))
    source_id, column = ref.split(":", 1)
    if not source_id or not column.strip():
        raise MappingError("column reference %r must be '<source id>:<column>'" % (ref,))
    return source_id, column


# --- validation ---------------------------------------------------------------------------------

def is_spec(node: Any) -> bool:
    return isinstance(node, dict) and ("from" in node or ("value" in node and not isinstance(node["value"], dict)))


def check_ref(ref: Any, source_ids, where: str) -> str:
    try:
        source_id, _ = split_ref(ref)
    except MappingError as error:
        raise MappingError("%s: %s" % (where, error))
    if source_id not in source_ids:
        raise MappingError("%s: unknown source '%s' in %r (sources: %s)" % (where, source_id, ref, ", ".join(source_ids)))
    return source_id


def check_step(step: Any, where: str) -> None:
    if isinstance(step, str):
        if step not in TRANSFORMS:
            raise MappingError("%s: unknown transform '%s' (valid: %s)" % (where, step, ", ".join(TRANSFORMS)))
        if step in ARGUMENT_TRANSFORMS:
            raise MappingError("%s: transform '%s' needs an argument: {\"%s\": ...}" % (where, step, step))
        return
    if not isinstance(step, dict) or len(step) != 1:
        raise MappingError("%s: a transform step is a name or a one-key object, got %r" % (where, step))
    name, arg = next(iter(step.items()))
    if name not in TRANSFORMS:
        raise MappingError("%s: unknown transform '%s' (valid: %s)" % (where, name, ", ".join(TRANSFORMS)))
    if name == "boolean":
        ok = isinstance(arg, list) and len(arg) == 2 and all(isinstance(a, str) for a in arg)
    elif name == "split":
        ok = isinstance(arg, str) and arg != ""
    elif name == "scale_above":
        ok = (isinstance(arg, list) and len(arg) == 2
              and all(isinstance(a, (int, float)) and not isinstance(a, bool) for a in arg) and arg[1] != 0)
    elif name == "regex":
        ok = isinstance(arg, str)
        if ok:
            try:
                re.compile(arg)
            except re.error as error:
                raise MappingError("%s: invalid regex %r: %s" % (where, arg, error))
    else:
        ok = arg is None
    if not ok:
        expected = {"boolean": '["<yes>", "<no>"]', "split": '"<separator>"', "scale_above": "[threshold, divisor]",
                    "regex": '"<pattern>"'}.get(name, "no argument")
        raise MappingError("%s: bad argument %r for transform '%s' (expected %s)" % (where, arg, name, expected))


def check_spec(spec: dict, source_ids, where: str) -> None:
    if "from" in spec and "value" in spec:
        raise MappingError("%s: a field spec has 'from' or 'value', not both" % where)
    if "from" in spec:
        refs = spec["from"]
        if not isinstance(refs, list) or not refs:
            raise MappingError("%s: 'from' must be a non-empty list of column references" % where)
        for ref in refs:
            check_ref(ref, source_ids, where)
    if spec.get("pick", "first") not in PICKS:
        raise MappingError("%s: pick must be one of %s" % (where, ", ".join(PICKS)))
    if spec.get("conflicts", "warn") not in CONFLICTS:
        raise MappingError("%s: conflicts must be one of %s" % (where, ", ".join(CONFLICTS)))
    if "sep" in spec and not isinstance(spec["sep"], str):
        raise MappingError("%s: sep must be a string" % where)
    if "map" in spec and not isinstance(spec["map"], dict):
        raise MappingError("%s: map must be an object" % where)
    steps = spec.get("transform", [])
    if not isinstance(steps, list):
        raise MappingError("%s: transform must be a list of steps" % where)
    for step in steps:
        check_step(step, where)


def walk(node: Any, source_ids, where: str) -> None:
    if isinstance(node, dict):
        if "origin" in node and node["origin"] not in ORIGINS:
            raise MappingError("%s: origin must be one of %s, got %r" % (where, ", ".join(ORIGINS), node["origin"]))
        for key in ("reason", "question"):
            if key in node and node[key] is not None and not isinstance(node[key], str):
                raise MappingError("%s: %s must be a string" % (where, key))
        if is_spec(node):
            check_spec(node, source_ids, where)
            return
        for key, value in node.items():
            walk(value, source_ids, "%s.%s" % (where, key) if where else key)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            walk(value, source_ids, "%s[%d]" % (where, i))


def main_source_id(mapping: dict) -> str:
    return [sid for sid, source in mapping["sources"].items() if "join" not in source][0]


def validate_mapping(mapping: dict) -> dict:
    if not isinstance(mapping, dict) or mapping.get("version") != 1:
        raise MappingError("mapping version must be 1")
    if not isinstance(mapping.get("concept"), str) or not mapping["concept"]:
        raise MappingError("mapping concept missing")
    sources = mapping.get("sources")
    if not isinstance(sources, dict) or not sources:
        raise MappingError("mapping sources missing")
    mains = [sid for sid, source in sources.items() if isinstance(source, dict) and "join" not in source]
    if len(mains) != 1:
        raise MappingError("exactly one source without 'join' (the main source) is required, found %d" % len(mains))
    source_ids = list(sources)
    for sid, source in sources.items():
        where = "sources.%s" % sid
        if ":" in sid or not sid:
            raise MappingError("%s: a source id cannot be empty or hold ':'" % where)
        if not isinstance(source, dict) or not isinstance(source.get("file"), str) or not source["file"]:
            raise MappingError("%s: 'file' missing" % where)
        if "sheet" in source and not isinstance(source["sheet"], str):
            raise MappingError("%s: sheet must be a string" % where)
        header_rows = source.get("header_rows")
        if header_rows is not None and (isinstance(header_rows, bool) or not isinstance(header_rows, int)
                                        or header_rows < 0):
            raise MappingError("%s: header_rows must be an integer >= 0" % where)
        if "join" in source:
            join = source["join"]
            if not isinstance(join, dict):
                raise MappingError("%s: join must be {\"column\", \"to\"}" % where)
            if check_ref(join.get("column"), source_ids, where + ".join.column") != sid:
                raise MappingError("%s: join.column must be a column of '%s'" % (where, sid))
            if check_ref(join.get("to"), source_ids, where + ".join.to") != mains[0]:
                raise MappingError("%s: join.to must be a column of the main source '%s'" % (where, mains[0]))
    walk({k: v for k, v in mapping.items() if k not in ("version", "concept", "sources")}, source_ids, "")
    return mapping


def load_mapping(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        try:
            data = json.load(f)
        except ValueError as error:
            raise MappingError("%s is not valid JSON: %s" % (path, error))
    try:
        return validate_mapping(data)
    except MappingError as error:
        raise MappingError("%s: %s" % (os.path.basename(path), error))


# --- sources and rows ---------------------------------------------------------------------------

class LoadedSource:
    def __init__(self, source_id, table, header_rows, cleaner, path=None):
        self.id = source_id  # type: str
        self.table = table
        self.path = path  # type: Optional[str]
        self.cleaner = cleaner
        self.width = max((len(row) for row in table.rows), default=0)
        self.offset = 0
        while self.offset < len(table.rows) and self.is_empty(self.offset):
            self.offset += 1
        if header_rows is None:
            header_rows = detect_header_rows([[self.quiet(v) for v in row] for row in table.rows[self.offset:]])
        self.header_rows = header_rows  # type: int
        headers = [[self.quiet(v) for v in row] + [None] * (self.width - len(row))
                   for row in table.rows[self.offset:self.offset + header_rows]]
        self.labels = headers[0] if headers else [None] * self.width
        self.codes = headers[1] if len(headers) > 1 else [None] * self.width
        self.cache = {}  # type: Dict[Tuple[int, int], Optional[str]]
        self.columns = {}  # type: Dict[str, int]

    def quiet(self, value):
        return clean.clean_text(value, None, self.cleaner.disabled, self.cleaner.replacements) or None

    def blank(self, value):
        if value is None:
            return True
        if not self.cleaner.replacements and any(c.isalnum() for c in value):
            return False
        return self.quiet(value) is None

    def is_empty(self, index):
        return all(self.blank(v) for v in self.table.rows[index])

    def describe(self):
        return "%s (%s / %s)" % (self.id, self.table.file, self.table.name)

    def column(self, ref_column):
        if ref_column not in self.columns:
            self.columns[ref_column] = self.find_column(ref_column)
        return self.columns[ref_column]

    def find_column(self, ref_column):
        if ref_column.startswith("@"):
            letters = ref_column[1:]
            index = column_index(letters) if LETTERS_RE.match(letters) else -1
            if not 0 <= index < self.width:
                raise MappingError("no column %s in %s (last column: @%s)" % (
                    ref_column, self.describe(), column_letter(self.width - 1) if self.width else "-"))
            return index
        wanted = self.quiet(ref_column)
        for headers in (self.codes, self.labels):
            found = [i for i, header in enumerate(headers) if header is not None and header == wanted]
            if len(found) == 1:
                return found[0]
            if found:
                raise MappingError("'%s:%s' matches several columns (%s); use %s" % (
                    self.id, ref_column, ", ".join(column_letter(i) for i in found),
                    " or ".join("'%s:@%s'" % (self.id, column_letter(i)) for i in found)))
        raise MappingError("no column '%s' in %s; use a technical code, a label or '%s:@<letter>'" % (
            ref_column, self.describe(), self.id))

    def data_rows(self):
        return [i for i in range(self.offset + self.header_rows, len(self.table.rows)) if not self.is_empty(i)]

    def cell(self, index, col):
        key = (index, col)
        if key not in self.cache:
            row = self.table.rows[index]
            self.cache[key] = (self.cleaner.text(row[col]) or None) if col < len(row) else None
        return self.cache[key]


def resolve(ref: str, sources: Dict[str, LoadedSource]) -> Tuple[str, int]:
    source_id, column = split_ref(ref)
    if source_id not in sources:
        raise MappingError("unknown source '%s' in %r (sources: %s)" % (source_id, ref, ", ".join(sources)))
    return source_id, sources[source_id].column(column)


def mapping_refs(node: Any) -> List[str]:
    if isinstance(node, dict):
        if is_spec(node):
            return list(node.get("from", []))
        return [ref for value in node.values() for ref in mapping_refs(value)]
    if isinstance(node, list):
        return [ref for value in node for ref in mapping_refs(value)]
    return []


def load_sources(directory: str, mapping: dict, cleaner: clean.Cleaner) -> Dict[str, LoadedSource]:
    validate_mapping(mapping)
    loaded = {}
    for source_id, source in mapping["sources"].items():
        path = os.path.join(directory, "sources", source["file"])
        if not os.path.isfile(path):
            raise MappingError("source %s: file %s not found in sources/" % (source_id, source["file"]))
        try:
            tables = read_source(path)
        except ValueError as error:
            raise MappingError("source %s: %s" % (source_id, error))
        if not tables:
            raise MappingError("source %s: %s has no sheet" % (source_id, source["file"]))
        table = tables[0]
        if source.get("sheet") is not None:
            named = [t for t in tables if t.name == source["sheet"]]
            if not named:
                raise MappingError("source %s: no sheet '%s' in %s (sheets: %s)" % (
                    source_id, source["sheet"], source["file"], ", ".join(t.name for t in tables)))
            table = named[0]
        loaded[source_id] = LoadedSource(source_id, table, source.get("header_rows"), cleaner, path)
    for source in mapping["sources"].values():
        if "join" in source:
            resolve(source["join"]["column"], loaded)
            resolve(source["join"]["to"], loaded)
    for ref in mapping_refs({k: v for k, v in mapping.items() if k != "sources"}):
        resolve(ref, loaded)
    return loaded


class Row:
    def __init__(self, row, indexes, sources):
        self.row = row  # type: int
        self.indexes = indexes  # type: Dict[str, Optional[int]]
        self.sources = sources

    def value(self, source_id, col):
        if source_id not in self.indexes:
            raise MappingError("unknown source '%s'" % source_id)
        index = self.indexes[source_id]
        return None if index is None else self.sources[source_id].cell(index, col)

    def source_row(self, source_id):
        index = self.indexes.get(source_id)
        return None if index is None else index + 1

    def __repr__(self):
        return "Row(%d, %r)" % (self.row, self.indexes)


def rows(sources: Dict[str, LoadedSource], mapping: dict, warnings: List[dict]) -> List[Row]:
    main_id = main_source_id(mapping)
    main = sources[main_id]
    main_indexes = main.data_rows()
    result = [Row(index + 1, {main_id: index}, sources) for index in main_indexes]
    for source_id, config in mapping["sources"].items():
        if "join" not in config:
            continue
        secondary = sources[source_id]
        _, own_col = resolve(config["join"]["column"], sources)
        _, main_col = resolve(config["join"]["to"], sources)
        by_key = {}  # type: Dict[str, int]
        for index in secondary.data_rows():
            key = secondary.cell(index, own_col)
            if key is None:
                continue
            if key in by_key:
                warn(warnings, index + 1, None, "join_duplicate", None,
                     "%s: key %s on rows %d and %d of %s, row %d is used" % (
                         source_id, key, by_key[key] + 1, index + 1, secondary.table.file, by_key[key] + 1))
                continue
            by_key[key] = index
        used = set()
        for row in result:
            key = main.cell(row.indexes[main_id], main_col)
            index = by_key.get(key) if key is not None else None
            row.indexes[source_id] = index
            if index is not None:
                used.add(index)
            elif key is not None:
                warn(warnings, row.row, None, "join_missing", None,
                     "%s: no row of %s for key %s" % (source_id, secondary.table.file, key))
        for key, index in sorted(by_key.items(), key=lambda item: item[1]):
            if index not in used:
                warn(warnings, index + 1, None, "join_unmatched", None,
                     "%s: row %d of %s (key %s) matches no row of %s" % (
                         source_id, index + 1, secondary.table.file, key, main_id))
    return result


# --- transforms ---------------------------------------------------------------------------------

def parse_number(value: str) -> Optional[str]:
    text = re.sub(r"\s+", "", value)
    stripped = True
    while stripped and text:
        stripped = False
        for suffix in NUMBER_SUFFIXES:
            if text.lower().endswith(suffix):
                text, stripped = text[:-len(suffix)], True
    text = text.replace(",", ".")
    return text if NUMBER_RE.match(text) else None


def plain_decimal(number: Decimal) -> str:
    text = format(number.normalize(), "f")
    return "0" if text in ("-0", "") else text


def text_to_html(value: str) -> str:
    escaped = value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    paragraphs = []
    for block in re.split(r"\n[ \t]*\n", escaped):
        lines = [line.strip() for line in block.split("\n") if line.strip()]
        if lines:
            paragraphs.append("<p>%s</p>" % "<br>".join(lines))
    return "".join(paragraphs)


def strip_html(value: str) -> str:
    text = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</h[1-6]>", "\n", value)
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    return "\n".join(line.strip() for line in text.split("\n") if line.strip())


def apply_transform(value, step, warnings=None, field=None, row=None, handle=None):
    if isinstance(value, list):
        result = []
        for item in value:
            out = apply_transform(item, step, warnings, field, row, handle)
            result.extend(out if isinstance(out, list) else [out])
        return [item for item in result if item not in (None, "")]
    if value is None:
        return None
    name, arg = (step, None) if isinstance(step, str) else next(iter(step.items()))

    def fail(code, message):
        warn(warnings, row, handle, code, field, message)
        return None

    if name in ("decimal", "integer"):
        number = parse_number(value)
        if number is None:
            return fail("not_a_number", "%r is not a number" % value)
        if name == "integer":
            if "." in number:
                whole, fraction = number.split(".")
                if fraction.strip("0"):
                    return fail("not_a_number", "%r is not a whole number" % value)
                number = whole
            number = str(int(number))
        return number
    if name == "boolean":
        folded = value.strip().casefold()
        if folded in YES:
            return arg[0]
        if folded in NO:
            return arg[1]
        return fail("not_a_boolean", "%r is neither yes nor no" % value)
    if name == "split":
        return [part.strip() for part in value.split(arg) if part.strip()]
    if name == "scale_above":
        number = parse_number(value)
        if number is None:
            return fail("not_a_number", "%r is not a number" % value)
        threshold, divisor = (Decimal(str(a)) for a in arg)
        if Decimal(number) <= threshold:
            return value
        scaled = plain_decimal(Decimal(number) / divisor)
        warn(warnings, row, handle, "scaled", field,
             "%s above %s, divided by %s: %s" % (value, plain_decimal(threshold), plain_decimal(divisor), scaled))
        return scaled
    if name == "regex":
        match = re.search(arg, value)
        if match is None or (match.re.groups and match.group(1) is None):
            return fail("no_match", "%r does not match %s" % (value, arg))
        return match.group(1) if match.re.groups else match.group(0)
    if name == "text_to_html":
        return text_to_html(value)
    if name == "strip_html":
        return strip_html(value)
    raise MappingError("unknown transform '%s'" % name)


# --- evaluation ---------------------------------------------------------------------------------

def is_empty(value) -> bool:
    return value is None or value == "" or value == []


def dedupe(values: List[str]) -> List[str]:
    seen = []  # type: List[str]
    for value in values:
        if value not in seen:
            seen.append(value)
    return seen


def map_value(value, spec):
    table = spec.get("map")
    if not table or value is None:
        return value
    if isinstance(value, list):
        mapped = [map_value(item, spec) for item in value]
        return [item for item in mapped if item is not None]
    if spec.get("map_ignore_case"):
        folded = {key.casefold(): target for key, target in table.items()}
        key = value.casefold()
        return folded[key] if key in folded else value
    return table[value] if value in table else value


def convert(value, spec, warnings, field, row, handle):
    value = map_value(value, spec)
    for step in spec.get("transform", []):
        if is_empty(value):
            break
        value = apply_transform(value, step, warnings, field, row, handle)
    return value


def evaluate(spec: dict, row: Row, sources: Dict[str, LoadedSource], warnings: List[dict], field: str,
             handle: Optional[str] = None):
    if "value" in spec:
        return spec["value"]
    candidates = []  # type: List[Tuple[str, str]]
    for ref in spec["from"]:
        source_id, col = resolve(ref, sources)
        value = row.value(source_id, col)
        if value is not None:
            candidates.append((ref, value))
    pick = spec.get("pick", "first")
    if pick == "all":
        result = convert(dedupe([value for _, value in candidates]), spec, warnings, field, row.row, handle)
        if isinstance(result, list):
            result = dedupe(result)
    elif pick == "join":
        joined = spec.get("sep", "\n").join(value for _, value in candidates) if candidates else None
        result = convert(joined, spec, warnings, field, row.row, handle)
    else:
        result = convert(candidates[0][1], spec, warnings, field, row.row, handle) if candidates else None
        if len(candidates) > 1 and spec.get("conflicts", "warn") == "warn":
            first_ref, first_value = candidates[0]
            for ref, value in candidates[1:]:
                if convert(value, spec, [], field, row.row, handle) != result:
                    warn(warnings, row.row, handle, "conflict", field, "%s = %r (kept) but %s = %r" % (
                        first_ref, first_value, ref, value))
    return spec.get("default") if is_empty(result) else result
