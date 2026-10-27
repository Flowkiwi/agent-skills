#!/usr/bin/env python3
"""Text cleaning for catalog values: office and typographic characters become plain web text.

Usage: clean.py table      print the cleaning rules as an aligned table
"""
import functools
import json
import os
import re
import sys
import unicodedata
from typing import Callable, Dict, Iterable, List, Optional, Tuple

CUSTOM = "custom"
GUILLEMET_SPACES = "[    ]*"

RULES = [
    ("nfc", "unicode normalization (accents composed)", "NFC"),
    ("newline", "\\r\\n, \\r, U+2028, U+2029, U+0085", "\\n"),
    ("quote", "“ ” „ « » (spaces inside the guillemets are swallowed)", '"'),
    ("apostrophe", "’ ‘ ‚ ′", "'"),
    ("ellipsis", "…", "..."),
    ("dash", "— –", "-"),
    ("nbsp", "no-break, thin and other unicode spaces", "space"),
    ("invisible", "zero-width, soft hyphen, BOM, control characters", "(removed)"),
    ("spaces", "inner tab or run of 2+ spaces", "one space"),
    ("trim", "leading and trailing spaces and tabs of each line and of the value", "(removed)"),
]
RULE_IDS = [rule[0] for rule in RULES]
VALID_IDS = RULE_IDS + [CUSTOM]

NEWLINE_RE = re.compile("\r\n|[\r  \u0085]")
GUILLEMET_OPEN_RE = re.compile("«" + GUILLEMET_SPACES)
GUILLEMET_CLOSE_RE = re.compile(GUILLEMET_SPACES + "»")
QUOTE_RE = re.compile("[“”„]")
APOSTROPHE_RE = re.compile("[’‘‚′]")
ELLIPSIS_RE = re.compile("…")
DASH_RE = re.compile("[—–]")
INNER_RUN_RE = re.compile(r"(?<=[^ \t\n])[ \t]+(?=[^ \t\n])")


@functools.lru_cache(maxsize=None)
def _class(kind: str) -> "re.Pattern":
    if kind == "nbsp":
        chars = [c for c in map(chr, range(0x3100)) if unicodedata.category(c) == "Zs" and c != " "]
    else:
        chars = [c for c in map(chr, range(0x110000))
                 if unicodedata.category(c) in ("Cf", "Cc") and c not in "\n\t"]
    return re.compile("[" + "".join(re.escape(c) for c in chars) + "]")


def _sub(pattern, repl: str, text: str) -> Tuple[str, int]:
    return pattern.subn(repl, text)


def _quote(text: str) -> Tuple[str, int]:
    text, a = GUILLEMET_OPEN_RE.subn('"', text)
    text, b = GUILLEMET_CLOSE_RE.subn('"', text)
    text, c = QUOTE_RE.subn('"', text)
    return text, a + b + c


def _spaces(text: str) -> Tuple[str, int]:
    found = [0]

    def collapse(match):
        run = match.group(0)
        if run == " ":
            return run
        found[0] += 1
        return " "

    return INNER_RUN_RE.sub(collapse, text), found[0]


def _trim(text: str) -> Tuple[str, int]:
    count = 0
    lines = text.split("\n")
    for i, line in enumerate(lines):
        stripped = line.strip(" \t")
        if stripped != line:
            count += 1
            lines[i] = stripped
    text = "\n".join(lines)
    edged = text.strip("\n")
    if edged != text:
        count += 1
    return edged, count


STEPS = {
    "newline": lambda t: _sub(NEWLINE_RE, "\n", t),
    "quote": _quote,
    "apostrophe": lambda t: _sub(APOSTROPHE_RE, "'", t),
    "ellipsis": lambda t: _sub(ELLIPSIS_RE, "...", t),
    "dash": lambda t: _sub(DASH_RE, "-", t),
    "nbsp": lambda t: _sub(_class("nbsp"), " ", t),
    "invisible": lambda t: _sub(_class("invisible"), "", t),
    "spaces": _spaces,
    "trim": _trim,
}  # type: Dict[str, Callable[[str], Tuple[str, int]]]


def clean_text(value: Optional[str], counts: Optional[Dict[str, int]] = None,
               disabled: Iterable[str] = (), replacements: Optional[Dict[str, str]] = None) -> Optional[str]:
    if value is None:
        return None
    disabled = set(disabled)

    def record(rule_id: str, n: int) -> None:
        if n and counts is not None:
            counts[rule_id] = counts.get(rule_id, 0) + n

    text = value
    if "nfc" not in disabled:
        normalized = unicodedata.normalize("NFC", text)
        if normalized != text:
            record("nfc", 1)
            text = normalized
    if replacements and CUSTOM not in disabled:
        total = 0
        for old in sorted(replacements, key=len, reverse=True):
            total += text.count(old)
            text = text.replace(old, replacements[old])
        record(CUSTOM, total)
    for rule_id in RULE_IDS[1:]:
        if rule_id in disabled:
            continue
        text, n = STEPS[rule_id](text)
        record(rule_id, n)
    return text


class Cleaner:
    def __init__(self, config: Optional[dict] = None) -> None:
        config = config or {}
        self.disabled = tuple(config.get("disabled", ()))
        self.replacements = dict(config.get("replacements", {}))
        self.counts = {}  # type: Dict[str, int]

    def text(self, value: Optional[str]) -> Optional[str]:
        return clean_text(value, self.counts, self.disabled, self.replacements)


def load_config(directory: str) -> dict:
    path = os.path.join(directory, "mappings", "cleaning.json")
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        config = json.load(f)
    unknown = [r for r in config.get("disabled", []) if r not in VALID_IDS]
    if unknown:
        raise ValueError("unknown cleaning rule(s) in %s: %s (valid: %s)" % (path, ", ".join(unknown), ", ".join(VALID_IDS)))
    replacements = config.get("replacements", {})
    if not isinstance(replacements, dict) or not all(
            k and isinstance(k, str) and isinstance(v, str) for k, v in replacements.items()):
        raise ValueError("replacements in %s must map non-empty strings to strings" % path)
    return config


def table() -> str:
    rows = [("rule", "matches", "becomes")] + [(r[0], r[1], r[2]) for r in RULES]
    widths = [max(len(row[i]) for row in rows) for i in range(3)]

    def line(row):
        return "| " + " | ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)) + " |"

    out = [line(rows[0]), "|" + "|".join("-" * (w + 2) for w in widths) + "|"]
    out += [line(row) for row in rows[1:]]
    return "\n".join(out)


def main(argv: List[str]) -> int:
    if argv[1:] == ["table"]:
        print(table())
        return 0
    print(__doc__.strip(), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
