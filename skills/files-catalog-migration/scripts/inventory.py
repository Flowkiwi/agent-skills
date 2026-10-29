#!/usr/bin/env python3
"""Inventory the files of <dir>/sources/: sheets, header rows, columns with fill rate, samples and defect flags.

Usage: inventory.py <dir> [--samples 5]    writes <dir>/inventory.json and prints the flagged columns
       inventory.py <dir> --row <n> [--row <m> ...] [--file <name>] [--sheet <name>] [--columns <c1,c2,...>]
                                       prints the cleaned cells of sheet row n (inventory.json is not rewritten)
"""
import argparse
import datetime
import json
import os
import re
import sys
import tempfile
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from decimal import Decimal
from typing import Dict, List, Optional, Sequence

from clean import clean_text
from intake import sha256
from sources import CSV_EXTENSIONS, XLSX_EXTENSIONS, Table, column_index, column_letter, read_source

VERSION = 1
CODE_RE = re.compile(r"^[a-z][a-z0-9_.\-]*$")
BRACKETS_RE = re.compile(r"\[[^\]]*\]|\([^)]*\)")
WORD_RE = re.compile(r"[^\W_]+")
NUMBER_RE = re.compile(r"^-?\d+([.,]\d+)?$")
DIGIT_SPACE_RE = re.compile("(?<=\\d)[   ](?=\\d)")
ISO_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})(T\d{2}:\d{2}(:\d{2})?)?$")
HTML_RE = re.compile(r"<[a-zA-Z][^>]*>")
STOPWORDS = frozenset("du de des la le les d l en au aux produit product the of".split())
INVARIABLE = frozenset("poids corps temps pays mois fois bois frais gris dessous dessus".split())
YES = frozenset("oui yes true vrai y 1 x".split())
NO = frozenset("non no false faux n 0".split())
BOOLEAN = YES | NO
URL_WORDS = frozenset("pdf url lien link visuel image photo".split())


def strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def fold(text: str) -> str:
    return strip_accents(text.casefold())


def matches_code(value: Optional[str]) -> bool:
    return bool(CODE_RE.match(value))


def detect_header_rows(rows: Sequence[Sequence[Optional[str]]]) -> int:
    if len(rows) < 2:
        return 1
    first = [v.strip() for v in rows[0] if v and v.strip()]
    second = [v.strip() for v in rows[1] if v and v.strip()]
    if not second:
        return 1
    second_rate = sum(map(matches_code, second)) / len(second)
    first_rate = sum(map(matches_code, first)) / len(first) if first else 0
    return 2 if second_rate >= 0.8 and first_rate < 0.5 else 1


def label_tokens(label: Optional[str]) -> frozenset:
    text = strip_accents(BRACKETS_RE.sub(" ", label or "").lower())
    tokens = set()
    for token in WORD_RE.findall(text):
        if token in STOPWORDS:
            continue
        plural = len(token) > 3 and token.endswith("s") and token not in INVARIABLE
        tokens.add(token[:-1] if plural else token)
    return frozenset(tokens)


def parse_number(text: Optional[str]) -> Optional[Decimal]:
    if text is None:
        return None
    text = DIGIT_SPACE_RE.sub("", text.strip())
    if not NUMBER_RE.match(text):
        return None
    return Decimal(text.replace(",", "."))


def is_iso_date(text: str) -> bool:
    match = ISO_DATE_RE.match(text)
    if not match:
        return False
    try:
        datetime.datetime.strptime(match.group(1), "%Y-%m-%d")
    except ValueError:
        return False
    return True


def is_url(text: str) -> bool:
    return text.startswith("http://") or text.startswith("https://")


def share(values: Sequence[str], test) -> float:
    return sum(1 for v in values if test(v)) / len(values) if values else 0.0


def column_kind(values: Sequence[Optional[str]]) -> str:
    values = [v for v in values if v]
    if not values:
        return "empty"
    if all(v.casefold() in BOOLEAN for v in values):
        return "boolean"
    if share(values, is_iso_date) >= 0.9:
        return "date"
    if share(values, lambda v: parse_number(v) is not None) >= 0.9:
        return "number"
    if share(values, is_url) >= 0.5:
        return "url"
    if any(HTML_RE.search(v) for v in values):
        return "html"
    return "text"


def distinct(values: Sequence[Optional[str]]) -> List[str]:
    seen = []  # type: List[str]
    known = set()
    for value in values:
        if value is not None and value not in known:
            known.add(value)
            seen.append(value)
    return seen


def same_answer(a: str, b: str) -> bool:
    x, y = parse_number(a), parse_number(b)
    if x is not None and y is not None:
        return x == y
    return a.casefold() == b.casefold()


def suspect_scale(values: Sequence[str]) -> List[str]:
    numbers = [(v, parse_number(v)) for v in values]
    positive = sorted(n for _, n in numbers if n is not None and n > 0)
    if len(positive) < 3:
        return []
    middle = len(positive) // 2
    median = positive[middle] if len(positive) % 2 else (positive[middle - 1] + positive[middle]) / 2
    return distinct([v for v, n in numbers if n is not None and n > 100 * median])


def boolean_variants(values: Sequence[str]) -> List[str]:
    spellings = distinct(values)
    yes = [v for v in spellings if v.casefold() in YES]
    no = [v for v in spellings if v.casefold() in NO]
    return sorted(spellings) if len(yes) > 1 or len(no) > 1 else []


def value_variants(values: Sequence[str]) -> List[List[str]]:
    groups = {}  # type: Dict[str, List[str]]
    for value in distinct(values):
        groups.setdefault(fold(value), []).append(value)
    return [sorted(group) for group in groups.values() if len(group) > 1]


def label_expects_url(label: Optional[str]) -> bool:
    for word in WORD_RE.findall(fold(label or "")):
        if word in URL_WORDS or (word.endswith("s") and word[:-1] in URL_WORDS):
            return True
    return False


def column_flags(index: int, columns: List[dict], values: List[List[Optional[str]]]) -> List[dict]:
    column, own = columns[index], values[index]
    filled = [v for v in own if v is not None]
    flags = []  # type: List[dict]
    if not filled:
        flags.append({"flag": "empty"})
    if len(filled) >= 2 and len(distinct(filled)) == 1:
        flags.append({"flag": "constant", "value": filled[0]})
    for other in range(index):
        pairs = [(a, b) for a, b in zip(values[other], own) if a is not None and b is not None]
        if pairs and all(a == b for a, b in pairs):
            flags.append({"flag": "same_values", "with": columns[other]["letter"]})
            break
    tokens = label_tokens(column["label"])
    for other in range(index):
        if tokens and label_tokens(columns[other]["label"]) == tokens:
            conflicts = sum(1 for a, b in zip(values[other], own)
                            if a is not None and b is not None and not same_answer(a, b))
            flags.append({"flag": "similar_label", "with": columns[other]["letter"], "conflicts": conflicts})
            break
    if filled and label_expects_url(column["label"]):
        matching = share(filled, is_url)
        if matching < 0.5:
            flags.append({"flag": "label_mismatch", "expected": "url", "matching": round(matching, 2)})
    kind = column["kind"]
    if kind == "number":
        suspects = suspect_scale(filled)
        if suspects:
            flags.append({"flag": "suspect_scale", "values": suspects})
    # a yes/no column may also hold a few free texts ("NON", "non", "<accessory name>")
    answers = [v for v in filled if v.casefold() in BOOLEAN]
    spellings = boolean_variants(answers) if answers and len(answers) * 2 >= len(filled) else []
    if spellings:
        flags.append({"flag": "boolean_variants", "values": spellings})
    if kind != "boolean" and filled:
        groups = value_variants([v for v in filled if not spellings or v.casefold() not in BOOLEAN])
        if groups:
            flags.append({"flag": "value_variants", "groups": groups})
    return flags


def cleaned(value: Optional[str]) -> Optional[str]:
    return clean_text(value) or None


def sheet_inventory(table: Table, header_rows: Optional[int] = None, samples: int = 5) -> dict:
    rows = [[cleaned(v) for v in row] for row in table.rows]
    while rows and all(v is None for v in rows[0]):
        rows.pop(0)
    if header_rows is None:
        header_rows = detect_header_rows(rows)
    width = max((len(row) for row in rows), default=0)
    rows = [row + [None] * (width - len(row)) for row in rows]
    headers = rows[:header_rows]
    data = [row for row in rows[header_rows:] if any(v is not None for v in row)]
    values = [[row[i] for row in data] for i in range(width)]
    columns = []
    for i in range(width):
        filled = [v for v in values[i] if v is not None]
        spellings = distinct(filled)
        columns.append({
            "letter": column_letter(i),
            "label": headers[0][i] if headers else None,
            "code": headers[1][i] if len(headers) > 1 else None,
            "filled": len(filled),
            "fill_rate": round(len(filled) / len(data), 2) if data else 0.0,
            "distinct": len(spellings),
            "max_length": max((len(v) for v in filled), default=0),
            "kind": column_kind(filled),
            "samples": spellings[:samples],
        })
    for i, column in enumerate(columns):
        column["flags"] = column_flags(i, columns, values)
    return {"sheet": table.name, "hidden": table.hidden, "header_rows": header_rows, "rows": len(data),
            "merged": list(table.merged), "columns": columns}


def source_files(directory: str) -> List[str]:
    folder = os.path.join(directory, "sources")
    if not os.path.isdir(folder):
        return []
    names = []
    for name in sorted(os.listdir(folder)):
        if name.startswith(".") or name.startswith("~$") or not os.path.isfile(os.path.join(folder, name)):
            continue
        if os.path.splitext(name)[1].lower() in XLSX_EXTENSIONS + CSV_EXTENSIONS:
            names.append(os.path.join(folder, name))
    return names


def inventory(directory: str, samples: int = 5) -> dict:
    files = []
    for path in source_files(directory):
        try:
            tables = read_source(path)
        except (ValueError, KeyError, OSError, zipfile.BadZipFile, ET.ParseError) as error:
            raise ValueError("%s: cannot be read (%s)" % (os.path.basename(path), error))
        files.append({"file": os.path.basename(path), "sha256": sha256(path),
                      "sheets": [sheet_inventory(table, samples=samples) for table in tables]})
    return {"version": VERSION, "files": files}


def write_inventory(directory: str, inv: dict) -> str:
    path = os.path.join(directory, "inventory.json")
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(inv, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    return path


def format_flag(flag: dict) -> str:
    name = flag["flag"]
    if name in ("same_values", "similar_label"):
        text = "%s with %s" % (name, flag["with"])
        return text + (" (%d conflicts)" % flag["conflicts"] if name == "similar_label" else "")
    if name == "label_mismatch":
        return "label_mismatch (expected %s, %s matching)" % (flag["expected"], flag["matching"])
    if name in ("suspect_scale", "boolean_variants"):
        return "%s %s" % (name, ", ".join(flag["values"]))
    if name == "value_variants":
        return "value_variants %s" % ", ".join("/".join(group) for group in flag["groups"])
    if name == "constant":
        return "constant %s" % flag["value"]
    return name


def format_inventory(inv: dict) -> str:
    lines = []
    for file in inv["files"]:
        for sheet in file["sheets"]:
            lines.append("%s / %s: %d rows, %d columns, header rows %d, merged %s%s" % (
                file["file"], sheet["sheet"], sheet["rows"], len(sheet["columns"]), sheet["header_rows"],
                ", ".join(sheet["merged"]) or "none", ", hidden" if sheet["hidden"] else ""))
            for column in sheet["columns"]:
                if not column["flags"]:
                    continue
                code = " [%s]" % column["code"] if column["code"] else ""
                lines.append("  %s %s%s: %s, filled %s, flags %s" % (
                    column["letter"], column["label"] or "(no label)", code, column["kind"], column["fill_rate"],
                    "; ".join(format_flag(flag) for flag in column["flags"])))
    if not lines:
        lines.append("no source file in sources/")
    return "\n".join(lines)


def pick_table(directory: str, file: Optional[str], sheet: Optional[str]) -> Table:
    paths = source_files(directory)
    if file is not None:
        paths = [p for p in paths if os.path.basename(p) == file]
        if not paths:
            raise ValueError("no file '%s' in sources/ (files: %s)" % (
                file, ", ".join(os.path.basename(p) for p in source_files(directory)) or "none"))
    if not paths:
        raise ValueError("no source file in sources/")
    path = paths[0]
    try:
        tables = read_source(path)
    except (ValueError, KeyError, OSError, zipfile.BadZipFile, ET.ParseError) as error:
        raise ValueError("%s: cannot be read (%s)" % (os.path.basename(path), error))
    if sheet is not None:
        tables = [t for t in tables if t.name == sheet]
        if not tables:
            raise ValueError("no sheet '%s' in %s" % (sheet, os.path.basename(path)))
    if not tables:
        raise ValueError("%s has no sheet" % os.path.basename(path))
    return tables[0]


def pick_columns(columns: List[dict], refs: Sequence[str]) -> List[int]:
    chosen = []  # type: List[int]
    for ref in refs:
        if ref.startswith("@"):
            index = column_index(ref[1:]) if re.match(r"^[A-Za-z]{1,3}$", ref[1:]) else -1
            if not 0 <= index < len(columns):
                raise ValueError("no column %s (last column: @%s)" % (ref, column_letter(len(columns) - 1)))
            chosen.append(index)
            continue
        wanted = clean_text(ref)
        for key in ("code", "label"):
            found = [i for i, c in enumerate(columns) if c[key] is not None and c[key] == wanted]
            if len(found) > 1:
                raise ValueError("'%s' matches several columns (%s); use @<letter>" % (
                    ref, ", ".join(columns[i]["letter"] for i in found)))
            if found:
                chosen.append(found[0])
                break
        else:
            raise ValueError("no column '%s'; use a technical code, a label or @<letter>" % ref)
    return chosen


def format_rows(table: Table, numbers: Sequence[int], refs: Sequence[str] = ()) -> str:
    columns = sheet_inventory(table)["columns"]
    indexes = pick_columns(columns, refs) if refs else list(range(len(columns)))
    lines = []  # type: List[str]
    for number in numbers:
        if not 1 <= number <= len(table.rows):
            raise ValueError("no row %d in %s / %s (rows 1 to %d)" % (number, table.file, table.name, len(table.rows)))
        row = table.rows[number - 1]
        lines.append("%s / %s row %d:" % (table.file, table.name, number))
        for i in indexes:
            column = columns[i]
            value = cleaned(row[i]) if i < len(row) else None
            lines.append("%s %s%s: %s" % (
                column["letter"], column["label"] or "(no label)", " [%s]" % column["code"] if column["code"] else "",
                value.replace("\n", "\\n") if value is not None else "(empty)"))
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("dir")
    parser.add_argument("--samples", type=int, default=5, help="distinct values kept per column (default 5)")
    parser.add_argument("--row", type=int, action="append", help="sheet row number to print (repeatable)")
    parser.add_argument("--file", help="with --row: source file name (default: the first one)")
    parser.add_argument("--sheet", help="with --row: sheet name (default: the first one)")
    parser.add_argument("--columns", help="with --row: only these columns, comma separated (code, label or @letter)")
    args = parser.parse_args(argv)
    if not os.path.isdir(args.dir):
        print("error: %s is not a directory" % args.dir, file=sys.stderr)
        return 2
    if args.row is None and (args.file or args.sheet or args.columns):
        print("error: --file, --sheet and --columns need --row", file=sys.stderr)
        return 2
    if args.row is not None:
        try:
            refs = [r.strip() for r in args.columns.split(",") if r.strip()] if args.columns else []
            print(format_rows(pick_table(args.dir, args.file, args.sheet), args.row, refs))
        except ValueError as error:
            print("error: %s" % error, file=sys.stderr)
            return 1
        return 0
    try:
        inv = inventory(args.dir, args.samples)
    except ValueError as error:
        print("error: %s" % error, file=sys.stderr)
        return 1
    write_inventory(args.dir, inv)
    print(format_inventory(inv))
    return 0


if __name__ == "__main__":
    sys.exit(main())
