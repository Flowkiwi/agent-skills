"""Check a value read back from Flowkiwi against products.json, and pick the products to check.

Usage:
  python3 verify.py <dir> <handle> --field description|title --readback-file <file>
  python3 verify.py sample <dir> [--rate 0.1] [--output <file>]

The read-back file holds the value as returned by the MCP, preferably as a JSON string literal
(quotes included, escapes kept), else as raw text. Exit code 0 on match, 1 on mismatch, 2 on error.
`sample` lists a deterministic ~rate share of the handles, plus every handle whose description holds
special whitespace or non-ASCII punctuation, plus the pilot products.
"""

import argparse
import hashlib
import json
import os
import sys
import tempfile
import unicodedata

from payload import is_special_char

FIELDS = {"description": "description_html", "title": "title"}
CONTEXT = 15


def normalize(text):
    text = (text or "").replace("\r\n", "\n")
    return unicodedata.normalize("NFC", text).strip(" \t\r\n")


def read_readback(path):
    with open(path, encoding="utf-8") as f:
        content = f.read()
    stripped = content.strip()
    if stripped.startswith('"'):
        try:
            value = json.loads(stripped)
        except ValueError:
            return content
        if isinstance(value, str):
            return value
    return content


def _sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _context(text, position):
    return json.dumps(text[max(position - CONTEXT, 0):position + CONTEXT])


def compare(expected, actual):
    expected, actual = normalize(expected), normalize(actual)
    report = {
        "match": expected == actual,
        "expected_length": len(expected), "actual_length": len(actual),
        "expected_sha256": _sha256(expected), "actual_sha256": _sha256(actual),
    }
    if not report["match"]:
        position = next(
            (i for i, (a, b) in enumerate(zip(expected, actual)) if a != b), min(len(expected), len(actual))
        )
        report.update(position=position, expected_context=_context(expected, position),
                      actual_context=_context(actual, position))
    return report


def needs_check(product):
    text = product.get("description_html") or ""
    return any(
        is_special_char(c) or (ord(c) > 127 and unicodedata.category(c).startswith("P")) for c in text
    )


def in_rate(handle, rate):
    return int(hashlib.sha256(handle.encode("utf-8")).hexdigest()[:8], 16) / 0xFFFFFFFF < rate


def sample(products, rate, pilots=()):
    pilots = set(pilots)
    return [
        p["handle"] for p in products["products"]
        if p["handle"] in pilots or in_rate(p["handle"], rate) or needs_check(p)
    ]


def _read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _write_json(path, data):
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def main_sample(argv):
    parser = argparse.ArgumentParser(prog="verify.py sample", description="Handles to verify after creation")
    parser.add_argument("dir")
    parser.add_argument("--rate", type=float, default=0.1)
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    products = _read_json(os.path.join(args.dir, "products.json"))
    state_path = os.path.join(args.dir, "state.json")
    pilots = []
    if os.path.exists(state_path):
        pilots = [h for h, p in _read_json(state_path)["products"].items() if p.get("pilot")]
    handles = sample(products, args.rate, pilots)
    if args.output:
        _write_json(args.output, handles)
        print(f"{len(handles)} of {len(products['products'])} handles to verify, written to {args.output}")
    else:
        print(json.dumps(handles, ensure_ascii=False, indent=2))
    return 0


def main_check(argv):
    parser = argparse.ArgumentParser(prog="verify.py", description="Compare a read-back value with products.json")
    parser.add_argument("dir")
    parser.add_argument("handle")
    parser.add_argument("--field", choices=sorted(FIELDS), default="description")
    parser.add_argument("--readback-file", required=True)
    args = parser.parse_args(argv)
    try:
        products = _read_json(os.path.join(args.dir, "products.json"))
        actual = read_readback(args.readback_file)
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    product = next((p for p in products["products"] if p["handle"] == args.handle), None)
    if product is None:
        print(f"error: handle '{args.handle}' is not in products.json", file=sys.stderr)
        return 2
    report = compare(product.get(FIELDS[args.field]), actual)
    print(json.dumps(report, indent=2))
    return 0 if report["match"] else 1


def main(argv):
    if len(argv) > 1 and argv[1] == "sample":
        return main_sample(argv[2:])
    return main_check(argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv))
