#!/usr/bin/env python3
"""Triage proposal for the columns of the mapped sources (see references/concepts.md).

Usage: columns.py propose <dir>

Reads inventory.json and mappings/products.json; writes tmp/columns-proposal.json and prints, for every
column of every mapped source, a role (core, attribute, metafield, description or ignore), a type and
the reason. The proposal is a starting point for the conversation with the user, not a decision.
"""
import json
import os
import sys
from typing import List, Optional, Set, Tuple

import clean
import codes
import mapping
from mapping import MappingError
from metafields import suggest_type
from sources import column_letter

SPARSE_BELOW = 0.2
DESCRIPTION_LENGTH = 255
LIST_MAX_DISTINCT = 20
LIST_MIN_DISTINCT = 6
PROPOSAL = os.path.join("tmp", "columns-proposal.json")


def referenced(m: dict, loaded: dict) -> Set[Tuple[str, int]]:
    """The columns the products mapping reads in its field specs, plus the join keys of its sources."""
    refs = mapping.mapping_refs({k: v for k, v in m.items() if k not in ("version", "concept", "sources")})
    for source in m["sources"].values():
        if "join" in source:
            refs += [source["join"]["column"], source["join"]["to"]]
    return {mapping.resolve(ref, loaded) for ref in refs}


def first_flag(column: dict, name: str) -> Optional[dict]:
    return next((f for f in column["flags"] if f["flag"] == name), None)


def triage(column: dict, values: List[str]) -> Tuple[str, Optional[str], str]:
    """(role, type, reason) of a column that the mapping does not read."""
    kind = column["kind"]
    if first_flag(column, "empty"):
        return "ignore", None, "empty"
    if column["fill_rate"] < SPARSE_BELOW:
        return "ignore", None, "sparse"
    same = first_flag(column, "same_values")
    if same:
        return "ignore", None, "duplicate of %s" % same["with"]
    similar = first_flag(column, "similar_label")
    if similar:
        return "ignore", None, 'similar to %s (%d conflicts): merge into one "from", winner first' % (
            similar["with"], similar["conflicts"])
    if first_flag(column, "label_mismatch"):
        if column["max_length"] > DESCRIPTION_LENGTH:
            return "description", None, "long text under a label that says url"
        return "ignore", None, "label says url, content is text"
    if kind == "url":
        return "ignore", None, "media: URLs listed in the final report"
    if kind == "html" or column["max_length"] > DESCRIPTION_LENGTH:
        return "description", None, "html" if kind == "html" else "long text"
    if kind == "boolean":
        return "attribute", "list", "yes/no values"
    if kind in ("number", "date"):
        kind_type = "single_line_text_field" if kind == "date" else suggest_type(values)[0]
        reason = "%s values" % kind + (", suspect scale" if first_flag(column, "suspect_scale") else "")
        return "metafield", kind_type, reason
    if column["distinct"] <= LIST_MAX_DISTINCT and column["distinct"] <= max(LIST_MIN_DISTINCT, column["filled"] // 2):
        return "attribute", "list", "%d distinct values" % column["distinct"]
    return "attribute", "string", "free text"


def sheet_of(inv: dict, source: "mapping.LoadedSource") -> dict:
    for file in inv["files"]:
        if file["file"] != source.table.file:
            continue
        for sheet in file["sheets"]:
            if sheet["sheet"] == source.table.name:
                return sheet
    raise ValueError("source %s: %s / %s is not in inventory.json; run inventory.py again" % (
        source.id, source.table.file, source.table.name))


def column_values(source: "mapping.LoadedSource", index: int) -> List[str]:
    cells = [source.cell(row, index) for row in source.data_rows()]
    return [c for c in cells if c is not None]


def propose(directory: str) -> List[dict]:
    inv = read_inventory(directory)
    m = mapping.load_mapping(os.path.join(directory, "mappings", "products.json"))
    loaded = mapping.load_sources(directory, m, clean.Cleaner(clean.load_config(directory)))
    used = referenced(m, loaded)
    entries = []  # type: List[dict]
    for source_id, source in loaded.items():
        for index, column in enumerate(sheet_of(inv, source)["columns"]):
            if (source_id, index) in used:
                role, kind_type, reason = "core", None, "used by products"
            else:
                role, kind_type, reason = triage(column, column_values(source, index))
            name = column["code"] or column["label"] or "@" + column_letter(index)
            entries.append({"column": "%s:%s" % (source_id, name), "letter": column["letter"],
                            "label": column["label"], "role": role, "type": kind_type, "reason": reason})
    return entries


def read_inventory(directory: str) -> dict:
    path = os.path.join(directory, "inventory.json")
    if not os.path.isfile(path):
        raise ValueError("%s not found: run inventory.py first" % path)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def format_table(entries: List[dict]) -> str:
    rows = [("column", "label", "role", "type", "reason")]
    rows += [(e["column"], e["label"] or "", e["role"], e["type"] or "-", e["reason"]) for e in entries]
    widths = [max(len(row[i]) for row in rows) for i in range(5)]

    def line(row):
        return "| " + " | ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)) + " |"

    out = [line(rows[0]), "|" + "|".join("-" * (w + 2) for w in widths) + "|"]
    return "\n".join(out + [line(row) for row in rows[1:]])


def main(argv: List[str]) -> int:
    if len(argv) != 3 or argv[1] != "propose":
        print(__doc__.strip(), file=sys.stderr)
        return 2
    directory = argv[2]
    if not os.path.isdir(directory):
        print("error: %s is not a directory" % directory, file=sys.stderr)
        return 2
    try:
        entries = propose(directory)
    except (MappingError, ValueError) as error:
        print("error: %s" % error, file=sys.stderr)
        return 1
    path = os.path.join(directory, PROPOSAL)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    codes.write_json(path, entries)
    print(format_table(entries))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
