"""Collections concept: plain product lists from a grouping column (range, universe, season) or a proposal.

Used by `extract.py <dir> collections` with mappings/collections.json (see references/collections.md); writes
concepts/collections.json in the decision format read by plan_shared.py and payload.py, recorded with:
state.py decide <dir> --key collections --value-file concepts/collections.json
(named collections_concept so that it never shadows the standard library `collections`)
"""
from typing import Dict, List, Optional

import catalog
import mapping
from mapping import MappingError, evaluate, is_spec, warn
from taxonomy import SEP, path_tree, split_path

MIN_NAME_LENGTH = 3
DEFAULT_STATUS = "/api/statuses/ACTIVE"
KEYS = frozenset(("version", "concept", "value", "separator", "assign", "status"))


def check_mapping(m: dict) -> dict:
    """Checks the collections keys of the mapping and returns its field specs only, for mapping.validate_mapping
    (a null top-level `value` would make the whole mapping look like a field spec)."""
    if m.get("concept") != "collections":
        raise MappingError("concept must be 'collections', got %r" % (m.get("concept"),))
    unknown = sorted(set(m) - KEYS - mapping.DOC_KEYS)
    if unknown:
        raise MappingError("unknown key(s) %s (valid: %s)" % (
            ", ".join(unknown), ", ".join(sorted(KEYS - {"version", "concept"} | mapping.DOC_KEYS))))
    if m.get("value") is not None and not is_spec(m["value"]):
        raise MappingError("value: a field spec ({\"from\": [...]}) or null")
    separator = m.get("separator")
    if separator is not None and (not isinstance(separator, str) or separator == ""):
        raise MappingError("separator: a non-empty string or null")
    assign = m.get("assign") or {}
    if not isinstance(assign, dict) or not all(
            isinstance(handle, str) and handle.strip() and isinstance(paths, list)
            and all(isinstance(p, str) and split_path(p) for p in paths)
            for handle, paths in assign.items()):
        raise MappingError("assign: an object {\"<handle>\": [\"<collection path>\", ...]}")
    status = m.get("status", DEFAULT_STATUS)
    if not isinstance(status, str) or not status.startswith("/api/statuses/"):
        raise MappingError("status: a status IRI such as %s" % DEFAULT_STATUS)
    specs = {k: v for k, v in m.items() if k in mapping.DOC_KEYS}
    if m.get("value") is not None:
        specs["value"] = m["value"]
    return dict(specs, version=m.get("version"), concept="collections")


def collection_path(levels: List[str], cleaner) -> Optional[str]:
    """Levels joined with ' > '; written text (map targets, assign paths) is cleaned like the cells."""
    return SEP.join(n for n in (cleaner.final(s.strip()) for s in levels) if n) or None


def column_paths(handle, rows, m, sources, warnings, cleaner) -> List[str]:
    """The collections of a product from its rows: every value of every row, in order, without duplicates."""
    separator = m.get("separator")
    paths = []  # type: List[str]
    for row in rows:
        value = evaluate(m["value"], row, sources, warnings, "value", handle)
        for item in value if isinstance(value, list) else [value]:
            if item is None:
                continue
            raw = str(item)
            path = collection_path(raw.split(separator) if separator else [raw], cleaner)
            if path and path not in paths:
                paths.append(path)
    return paths


def build(products: List[dict], rows: Dict[str, list], m: dict, sources: dict, warnings: List[dict]) -> dict:
    """concepts/collections.json from the products of products.json, their rows and the collections mapping."""
    cleaner = next(iter(sources.values())).cleaner
    assign = m.get("assign") or {}
    handles = [p["handle"] for p in products]
    membership = {}  # type: Dict[str, List[str]]
    for handle in handles:
        paths = column_paths(handle, rows.get(handle, []), m, sources, warnings, cleaner) if m.get("value") else []
        for raw in assign.get(handle, []):
            path = collection_path(split_path(raw), cleaner)
            if path and path not in paths:
                paths.append(path)
        if paths:
            membership[handle] = paths
    for handle in assign:
        if handle not in handles:
            warn(warnings, None, handle, "unknown_handle", "assign",
                 "assign names %s, which is not a product of products.json" % handle)
    collections, tree_warnings = path_tree([p for paths in membership.values() for p in paths], MIN_NAME_LENGTH,
                                           "short_collection_name", "collections")
    warnings.extend(tree_warnings)
    return {"status": m.get("status", DEFAULT_STATUS), "collections": collections, "membership": membership,
            "warnings": warnings}


def format_preview(data: dict) -> str:
    counts = {}  # type: Dict[str, int]
    for paths in data["membership"].values():
        for path in paths:
            counts[path] = counts.get(path, 0) + 1
    lines = ["Collections, products per collection (a product is in the collections it is listed in):"]
    for collection in data["collections"]:
        indent = "  " * collection["path"].count(SEP)
        lines.append("%s%s (%d)" % (indent, collection["name"], counts.get(collection["path"], 0)))
    lines.append("Products in a collection: %d" % len(data["membership"]))
    if data.get("warnings"):
        lines += catalog.format_warnings(data["warnings"]).splitlines()
    lines.append("%d collections" % len(data["collections"]))
    return "\n".join(lines)
