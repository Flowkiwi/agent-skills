"""Taxonomy concept: the category tree from a category column (or a proposed one) and attributes.

Used by `extract.py <dir> taxonomy` with mappings/taxonomy.json (see references/taxonomy.md); writes
concepts/taxonomy.json in the decision format read by plan_shared.py and payload.py, recorded with:
state.py decide <dir> --key taxonomy --value-file concepts/taxonomy.json
"""
import re
import unicodedata
from typing import Dict, Iterable, List, Optional, Tuple

import mapping
from mapping import MappingError, evaluate, is_spec, warn

SEP = " > "
MIN_NAME_LENGTH = 3
SOURCE = "mappings/taxonomy.json"
DEFAULT_STATUS = "/api/statuses/ACTIVE"
CATEGORY_KEYS = frozenset(("value", "separator", "drop_levels", "depth", "map", "rename", "assign", "status"))
PREVIEW_HANDLES = 10


def split_path(value):
    return [s.strip() for s in (value or "").split(">") if s.strip()]


def attribute_code(key):
    text = unicodedata.normalize("NFKD", key)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9_-]+", "-", text.strip().lower()).strip("-")[:64]


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


def rename_nodes(segments, renames):
    renamed = list(segments)
    for i in range(len(segments)):
        new_name = (renames or {}).get(SEP.join(segments[:i + 1]))
        if new_name:
            renamed[i] = new_name
    return renamed


def category_path(raw, separator, drop_levels=0, depth=None, path_map=None, renames=None) -> Optional[str]:
    """Split on separator (no separator: one level), drop the first levels, keep `depth` levels, join with
    ' > ', then path_map on the joined path (a null target: no category), then renames per node."""
    if raw is None:
        return None
    parts = raw.split(separator) if separator else [raw]
    segments = [s.strip() for s in parts if s.strip()][drop_levels:]
    if depth is not None:
        segments = segments[:depth]
    path = SEP.join(segments)
    if path and path_map and path in path_map:
        if path_map[path] is None:
            return None
        segments = split_path(path_map[path])
    return SEP.join(rename_nodes(segments, renames)) or None


def path_tree(paths: Iterable[str], min_length: int, code: str, field: str) -> Tuple[List[dict], List[dict]]:
    """Every node of the given ' > ' paths, sorted by segments so parents come first, and a `code` warning
    for each node name shorter than min_length. Shared by categories and collections."""
    nodes, warnings, seen = [], [], set()
    for path in sorted(set(paths), key=lambda p: p.split(SEP)):
        parts = path.split(SEP)
        for i in range(1, len(parts) + 1):
            node = SEP.join(parts[:i])
            if node in seen:
                continue
            seen.add(node)
            nodes.append({"path": node, "name": parts[i - 1], "parent": SEP.join(parts[:i - 1]) or None})
            if len(parts[i - 1]) < min_length:
                warn(warnings, None, None, code, field,
                     "'%s': name shorter than %d characters, rename it" % (node, min_length))
    return nodes, warnings


def _string_map(value, where, nullable):
    if not isinstance(value, dict) or not all(
            isinstance(k, str) and k.strip() and ((v is None and nullable) or (isinstance(v, str) and v.strip()))
            for k, v in value.items()):
        raise MappingError("%s: an object of non-empty strings%s" % (where, " (or null targets)" if nullable else ""))


def check_mapping(m: dict) -> dict:
    """Checks the taxonomy keys of the mapping and returns its field specs only, for mapping.validate_mapping
    (the `map` of categories is a path table, not a field spec map)."""
    if m.get("concept") != "taxonomy":
        raise MappingError("concept must be 'taxonomy', got %r" % (m.get("concept"),))
    unknown = sorted(set(m) - {"version", "concept", "categories", "attributes"})
    if unknown:
        raise MappingError("unknown key(s) %s (valid: categories, attributes)" % ", ".join(unknown))
    c = m.get("categories")
    if not isinstance(c, dict):
        raise MappingError("categories: an object with value, separator, drop_levels, depth, map, rename, assign, status")
    unknown = sorted(set(c) - CATEGORY_KEYS - mapping.DOC_KEYS)
    if unknown:
        raise MappingError("categories: unknown key(s) %s (valid: %s)" % (
            ", ".join(unknown), ", ".join(sorted(CATEGORY_KEYS | mapping.DOC_KEYS))))
    if c.get("value") is not None and not is_spec(c["value"]):
        raise MappingError("categories.value: a field spec ({\"from\": [...]}) or null")
    separator = c.get("separator")
    if separator is not None and (not isinstance(separator, str) or separator == ""):
        raise MappingError("categories.separator: a non-empty string or null")
    drop = c.get("drop_levels", 0)
    if isinstance(drop, bool) or not isinstance(drop, int) or drop < 0:
        raise MappingError("categories.drop_levels: an integer >= 0")
    depth = c.get("depth")
    if depth is not None and (isinstance(depth, bool) or not isinstance(depth, int) or depth < 1):
        raise MappingError("categories.depth: an integer >= 1 or null")
    for key, nullable in (("map", True), ("rename", False), ("assign", False)):
        if c.get(key) is not None:
            _string_map(c[key], "categories." + key, nullable)
    status = c.get("status", DEFAULT_STATUS)
    if not isinstance(status, str) or not status.startswith("/api/statuses/"):
        raise MappingError("categories.status: a status IRI such as %s" % DEFAULT_STATUS)
    if not isinstance(m.get("attributes", []), list):
        raise MappingError("attributes: a list")
    specs = {k: v for k, v in c.items() if k == "value" or k in mapping.DOC_KEYS}
    return {"version": m.get("version"), "concept": "taxonomy", "categories": specs,
            "attributes": m.get("attributes", [])}


def _final(cleaner, path):
    """Map targets, renames and assign paths are written text: cleaned like the cells."""
    if path is None:
        return None
    return SEP.join(n for n in (cleaner.final(s) for s in path.split(SEP)) if n) or None


def column_path(handle, rows, c, path_map, renames, sources, warnings, cleaner):
    """The category of a product from its rows: the first row giving one, a later different one warns."""
    kept = kept_row = None
    for row in rows:
        value = evaluate(c["value"], row, sources, warnings, "categories.value", handle)
        if isinstance(value, list):
            if len(value) > 1:
                warn(warnings, row.row, handle, "list_in_single_field", "categories.value",
                     "%d values %r for the category (first one kept)" % (len(value), value))
            value = value[0] if value else None
        path = _final(cleaner, category_path(None if value is None else str(value), c.get("separator"),
                                             c.get("drop_levels", 0), c.get("depth"), path_map, renames))
        if path is None:
            continue
        if kept is None:
            kept, kept_row = path, row.row
        elif path != kept:
            warn(warnings, row.row, handle, "group_conflict", "categories.value",
                 "row %d gives category %r, row %d (kept) gives %r" % (row.row, path, kept_row, kept))
    return kept


def build(products: List[dict], rows: Dict[str, list], spec: dict, sources: dict, warnings: List[dict]) -> dict:
    """concepts/taxonomy.json from the products of products.json, their rows and the taxonomy mapping."""
    c = spec["categories"]
    cleaner = next(iter(sources.values())).cleaner
    path_map = {SEP.join(split_path(k)): v for k, v in (c.get("map") or {}).items()}
    renames = {SEP.join(split_path(k)): v.strip() for k, v in (c.get("rename") or {}).items()}
    assign = c.get("assign") or {}
    handles = [p["handle"] for p in products]
    assignments = {}  # type: Dict[str, str]
    uncategorized = []  # type: List[str]
    for product in products:
        handle = product["handle"]
        if handle in assign:
            path = _final(cleaner, SEP.join(rename_nodes(split_path(assign[handle]), renames)) or None)
        elif c.get("value") is not None:
            path = column_path(handle, rows.get(handle, []), c, path_map, renames, sources, warnings, cleaner)
        else:
            path = None
        if path:
            assignments[handle] = path
        else:
            uncategorized.append(handle)
    for handle in assign:
        if handle not in handles:
            warn(warnings, None, handle, "unknown_handle", "categories.assign",
                 "assign names %s, which is not a product of products.json" % handle)
    categories, tree_warnings = path_tree(assignments.values(), MIN_NAME_LENGTH, "short_category_name", "categories")
    warnings.extend(tree_warnings)
    return {
        "source": SOURCE, "category_status": c.get("status", DEFAULT_STATUS), "categories": categories,
        "products": assignments, "attributes": [], "uncategorized": uncategorized, "warnings": warnings,
    }


def format_preview(taxonomy: dict) -> str:
    counts = {}  # type: Dict[str, int]
    for path in taxonomy["products"].values():
        parts = path.split(SEP)
        for i in range(1, len(parts) + 1):
            node = SEP.join(parts[:i])
            counts[node] = counts.get(node, 0) + 1
    lines = ["Category tree (source: %s), products per node including descendants:" % taxonomy.get("source", SOURCE)]
    for category in taxonomy["categories"]:
        indent = "  " * category["path"].count(SEP)
        lines.append("%s%s (%d)" % (indent, category["name"], counts.get(category["path"], 0)))
    uncategorized = taxonomy["uncategorized"]
    line = "Without category: %d products" % len(uncategorized)
    if uncategorized:
        line += " (%s%s)" % (", ".join(uncategorized[:PREVIEW_HANDLES]),
                             ", ..." if len(uncategorized) > PREVIEW_HANDLES else "")
    lines.append(line)
    if taxonomy.get("attributes"):
        lines.append("Attributes:")
        for d in taxonomy["attributes"]:
            options = ", ".join(o["value"] if o.get("source") in (None, o["value"])
                                else "%s (from %s)" % (o["value"], o["source"]) for o in d.get("options", []))
            lines.append("  %s '%s' (%s): %d products, %s value, on %s%s" % (
                d["code"], d["name"], d.get("type", "list"), len(d.get("values", {})),
                "multiple" if d.get("multiple") else "single", d.get("categories", []),
                ", options: " + options if options else ""))
    for warning in taxonomy.get("warnings", []):
        lines.append("%s: %s" % (warning["code"], warning["message"]))
    lines.append("%d categories" % len(taxonomy["categories"]))
    return "\n".join(lines)
