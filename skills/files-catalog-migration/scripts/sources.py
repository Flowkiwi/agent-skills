#!/usr/bin/env python3
"""Tabular source readers: xlsx (stdlib zip + XML) and csv, returning raw cell text.

Values are never cleaned here: a cell holds exactly what the client file stores, None when empty.
"""
import codecs
import csv
import datetime
import io
import math
import os
import posixpath
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from decimal import Decimal
from typing import Dict, List, Optional

XLSX_EXTENSIONS = (".xlsx", ".xlsm")
CSV_EXTENSIONS = (".csv", ".tsv", ".txt")
DELIMITERS = [";", "\t", ",", "|"]
DATE_FORMAT_IDS = set(range(14, 23)) | {45, 46, 47}
CELL_REF_RE = re.compile(r"^\$?([A-Za-z]{1,3})\$?(\d*)$")
INTEGER_RE = re.compile(r"^[+-]?\d+$")
XSTRING_ESCAPE_RE = re.compile(r"_x([0-9A-Fa-f]{4})_")
FORMAT_LITERAL_RE = re.compile(r'"[^"]*"|\[[^\]]*\]|\\.|[_*].')

csv.field_size_limit(min(sys.maxsize, 2 ** 31 - 1))  # long HTML descriptions


class Table:
    def __init__(self, file, name, rows, merged=None, hidden=False):
        self.file = file  # type: str
        self.name = name  # type: str
        self.rows = rows  # type: List[List[Optional[str]]]
        self.merged = merged or []  # type: List[str]
        self.hidden = hidden  # type: bool

    def __repr__(self):
        return "Table(%r, %r, %d rows)" % (self.file, self.name, len(self.rows))


def column_letter(index):
    letters = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        letters = chr(65 + rest) + letters
    return letters


def column_index(letter):
    index = 0
    for char in letter.upper():
        index = index * 26 + ord(char) - 64
    return index - 1


def excel_number_text(raw):
    raw = raw.strip()
    if INTEGER_RE.match(raw):
        return raw
    try:
        number = float(raw)
    except ValueError:
        return raw
    if math.isnan(number) or math.isinf(number):
        return raw
    text = format(Decimal(repr(number)), "f")
    return text[:-2] if text.endswith(".0") else text


def excel_date(serial, date1904=False):
    base = datetime.datetime(1904, 1, 1) if date1904 else datetime.datetime(1899, 12, 30)
    days = math.floor(serial)
    seconds = int(round((serial - days) * 86400))
    if seconds >= 86400:
        days, seconds = days + 1, 0
    moment = base + datetime.timedelta(days=days, seconds=seconds)
    return moment.strftime("%Y-%m-%dT%H:%M:%S") if seconds else moment.strftime("%Y-%m-%d")


def is_date_format(code):
    bare = FORMAT_LITERAL_RE.sub("", code).lower()
    return any(char in bare for char in "dmy")


def local(tag):
    return tag.rsplit("}", 1)[-1]


def children(element, name):
    return [child for child in element if local(child.tag) == name]


def attribute(element, name):
    """Attribute by local name, whatever its namespace (r:id in transitional or strict OOXML)."""
    for key, value in element.attrib.items():
        if local(key) == name:
            return value
    return None


def unescape_xstring(text):
    return XSTRING_ESCAPE_RE.sub(lambda m: chr(int(m.group(1), 16)), text)


def string_item_text(element):
    """Text of an si or is element: direct t plus the t of each rich run r; rPh (phonetic) ignored."""
    parts = []
    for child in element:
        name = local(child.tag)
        if name == "t":
            parts.append(child.text or "")
        elif name == "r":
            parts.extend(t.text or "" for t in children(child, "t"))
    return unescape_xstring("".join(parts))


class XlsxPackage:
    def __init__(self, zf):
        self.zf = zf
        self.names = {name.lower(): name for name in zf.namelist()}

    def part_name(self, name):
        return name if name in self.zf.NameToInfo else self.names.get(name.lower())

    def has(self, name):
        return self.part_name(name) is not None

    def open(self, name):
        return self.zf.open(self.part_name(name))

    def parse(self, name):
        with self.open(name) as f:
            return ET.parse(f).getroot()

    def relationships(self, part):
        """{id: (type, resolved target)} of a part, from its _rels/<name>.rels."""
        folder, base = posixpath.split(part)
        rels_name = posixpath.join(folder, "_rels", base + ".rels")
        rels = {}
        if not self.has(rels_name):
            return rels
        for rel in self.parse(rels_name):
            if local(rel.tag) != "Relationship" or rel.get("TargetMode") == "External":
                continue
            target = rel.get("Target", "").replace("\\", "/")
            if target.startswith("/"):
                resolved = target.lstrip("/")
            else:
                resolved = posixpath.normpath(posixpath.join(folder, target))
            rels[rel.get("Id")] = (rel.get("Type", ""), resolved)
        return rels


def workbook_part(package):
    for rel_type, target in package.relationships("").values():
        if rel_type.endswith("/officeDocument") and package.has(target):
            return target
    return "xl/workbook.xml"


def read_shared_strings(package, name):
    strings = []  # type: List[str]
    if not name or not package.has(name):
        return strings
    with package.open(name) as f:
        for _, element in ET.iterparse(f):
            if local(element.tag) == "si":
                strings.append(string_item_text(element))
                element.clear()
    return strings


def read_date_styles(package, name):
    """Indexes of the cellXfs entries whose number format displays a date."""
    if not name or not package.has(name):
        return set()
    root = package.parse(name)
    custom = {}
    for numfmts in children(root, "numFmts"):
        for numfmt in children(numfmts, "numFmt"):
            custom[numfmt.get("numFmtId")] = numfmt.get("formatCode", "")
    dates = set()
    for cellxfs in children(root, "cellXfs"):
        for index, xf in enumerate(children(cellxfs, "xf")):
            fmt_id = xf.get("numFmtId", "0")
            if fmt_id in custom:
                is_date = is_date_format(custom[fmt_id])
            else:
                is_date = fmt_id.isdigit() and int(fmt_id) in DATE_FORMAT_IDS
            if is_date:
                dates.add(index)
    return dates


def cell_value(cell, strings, date_styles, date1904):
    kind = cell.get("t", "n")
    if kind == "inlineStr":
        boxes = children(cell, "is")
        return (string_item_text(boxes[0]) if boxes else "") or None
    values = children(cell, "v")
    raw = values[0].text if values else None
    if raw is None or raw == "":
        return None
    if kind == "s":
        try:
            return strings[int(raw)] or None
        except (ValueError, IndexError):
            raise ValueError("shared string %r out of range in cell %s" % (raw, cell.get("r", "?")))
    if kind == "b":
        return "TRUE" if raw.strip() in ("1", "true") else "FALSE"
    if kind == "e":
        return None
    if kind in ("str", "d"):
        return unescape_xstring(raw) if kind == "str" else raw
    style = cell.get("s")
    if style is not None and style.isdigit() and int(style) in date_styles:
        try:
            return excel_date(float(raw), date1904)
        except (ValueError, OverflowError):
            return raw
    return excel_number_text(raw)


def read_worksheet(package, name, strings, date_styles, date1904):
    """({row index: {column index: value}}, merged refs), 0-based indexes."""
    cells = {}  # type: Dict[int, Dict[int, str]]
    merged = []
    row_index = -1
    with package.open(name) as f:
        for _, element in ET.iterparse(f):
            tag = local(element.tag)
            if tag == "mergeCell":
                merged.append(element.get("ref"))
            if tag != "row":
                continue
            row_cells = children(element, "c")
            ref = element.get("r")
            if ref and ref.isdigit():
                row_index = int(ref) - 1
            else:
                first = CELL_REF_RE.match(row_cells[0].get("r", "")) if row_cells else None
                row_index = int(first.group(2)) - 1 if first and first.group(2) else row_index + 1
            col = -1
            for cell in row_cells:
                match = CELL_REF_RE.match(cell.get("r", ""))
                col = column_index(match.group(1)) if match else col + 1
                value = cell_value(cell, strings, date_styles, date1904)
                if value is not None:
                    cells.setdefault(row_index, {})[col] = value
            element.clear()
    return cells, [ref for ref in merged if ref]


def to_rows(cells):
    if not cells:
        return []
    width = max(max(row) for row in cells.values()) + 1
    return [[cells.get(r, {}).get(c) for c in range(width)] for r in range(max(cells) + 1)]


def read_xlsx(path):
    file = os.path.basename(path)
    with zipfile.ZipFile(path) as zf:
        package = XlsxPackage(zf)
        workbook_name = workbook_part(package)
        workbook = package.parse(workbook_name)
        rels = package.relationships(workbook_name)
        by_type = {}
        for rel_type, target in rels.values():
            by_type.setdefault(rel_type.rsplit("/", 1)[-1], target)
        strings = read_shared_strings(package, by_type.get("sharedStrings", "xl/sharedStrings.xml"))
        date_styles = read_date_styles(package, by_type.get("styles", "xl/styles.xml"))
        date1904 = any(pr.get("date1904", "").lower() in ("1", "true") for pr in children(workbook, "workbookPr"))
        tables = []
        for sheets in children(workbook, "sheets"):
            for sheet in children(sheets, "sheet"):
                rel_type, target = rels.get(attribute(sheet, "id"), ("", ""))
                if not rel_type.endswith("/worksheet") or not package.has(target):
                    continue
                cells, merged = read_worksheet(package, target, strings, date_styles, date1904)
                hidden = sheet.get("state", "visible") in ("hidden", "veryHidden")
                tables.append(Table(file, sheet.get("name", ""), to_rows(cells), merged, hidden))
        return tables


def detect_encoding(data):
    if data.startswith(codecs.BOM_UTF8):
        return "utf-8-sig"
    if data.startswith(codecs.BOM_UTF16_LE) or data.startswith(codecs.BOM_UTF16_BE):
        return "utf-16"
    for encoding in ("utf-8", "cp1252"):
        try:
            data.decode(encoding)
            return encoding
        except UnicodeDecodeError:
            pass
    return "latin-1"


def count_outside_quotes(line, char):
    count, quoted = 0, False
    for c in line:
        if c == '"':
            quoted = not quoted
        elif c == char and not quoted:
            count += 1
    return count


def detect_delimiter(text):
    lines = [line for line in text.splitlines() if line.strip()][:10]
    best, best_score = DELIMITERS[0], 0
    for candidate in DELIMITERS:
        frequency = {}  # type: Dict[int, int]
        for line in lines:
            count = count_outside_quotes(line, candidate)
            if count:
                frequency[count] = frequency.get(count, 0) + 1
        score = max(frequency.values()) if frequency else 0
        if score > best_score:
            best, best_score = candidate, score
    return best


def read_csv(path):
    with open(path, "rb") as f:
        data = f.read()
    text = data.decode(detect_encoding(data))
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=detect_delimiter(text))
    rows = [[value if value != "" else None for value in row] for row in reader]
    width = max((len(row) for row in rows), default=0)
    return Table(os.path.basename(path), "csv", [row + [None] * (width - len(row)) for row in rows])


def read_source(path):
    extension = os.path.splitext(path)[1].lower()
    if extension in XLSX_EXTENSIONS:
        return read_xlsx(path)
    if extension in CSV_EXTENSIONS:
        return [read_csv(path)]
    raise ValueError("unsupported source file %s: expected .xlsx, .xlsm, .csv, .tsv or .txt" % os.path.basename(path))
