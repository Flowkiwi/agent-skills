#!/usr/bin/env python3
"""Copy client files into <dir>/sources/ without touching the originals, converting old .xls/.ods to .xlsx."""
import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import time
from typing import List, Optional, Tuple

SUPPORTED = (".xlsx", ".xlsm", ".csv", ".tsv", ".txt")
CONVERT = (".xls", ".ods")
MACOS_SOFFICE = "/Applications/LibreOffice.app/Contents/MacOS/soffice"
CONVERT_TIMEOUT = 120


class IntakeError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


def find_soffice() -> Optional[str]:
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    return MACOS_SOFFICE if os.path.exists(MACOS_SOFFICE) else None


def convert_to_xlsx(path: str, outdir: str, soffice: str) -> str:
    command = [soffice, "--headless", "--convert-to", "xlsx", "--outdir", outdir, path]
    try:
        subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=CONVERT_TIMEOUT, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise IntakeError(3, "conversion of %s failed: %s" % (os.path.basename(path), error))
    result = os.path.join(outdir, os.path.splitext(os.path.basename(path))[0] + ".xlsx")
    if not os.path.exists(result):
        raise IntakeError(3, "LibreOffice did not produce %s: open the file and save it as .xlsx, then add it"
                          % os.path.basename(result))
    return result


def sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def place(sources: str, path: str, name: str, replace: bool) -> bool:
    """Copy path to sources/name. Return False when the same file is already there."""
    target = os.path.join(sources, name)
    if os.path.exists(target):
        if sha256(target) == sha256(path):
            return False
        if not replace:
            raise IntakeError(1, "%s already exists in sources with other content (use --replace for a new version)" % name)
        previous = os.path.join(sources, "previous")
        os.makedirs(previous, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        archived = os.path.join(previous, "%s-%s" % (stamp, name))
        counter = 1
        while os.path.exists(archived):
            counter += 1
            archived = os.path.join(previous, "%s-%d-%s" % (stamp, counter, name))
        shutil.move(target, archived)
    shutil.copyfile(path, target)
    return True


def add_file(directory: str, path: str, replace: bool = False, soffice: Optional[str] = None) -> List[Tuple[str, str]]:
    """Return (action, name) pairs, action being copied, already in sources or converted."""
    name = os.path.basename(path)
    extension = os.path.splitext(name)[1].lower()
    if extension not in SUPPORTED + CONVERT:
        raise IntakeError(2, "%s: unsupported format (supported: %s, and %s through LibreOffice)"
                          % (name, ", ".join(SUPPORTED), ", ".join(CONVERT)))
    if not os.path.isfile(path):
        raise IntakeError(2, "%s: file not found" % path)
    sources = os.path.join(directory, "sources")
    if extension in SUPPORTED:
        os.makedirs(sources, exist_ok=True)
        return [("copied" if place(sources, path, name, replace) else "already in sources", name)]

    soffice = soffice or find_soffice()
    if not soffice:
        raise IntakeError(3, "%s needs LibreOffice to be converted and it is not installed: open the file and "
                          "save it as .xlsx, then add it" % name)
    os.makedirs(sources, exist_ok=True)
    converted_name = os.path.splitext(name)[0] + ".xlsx"
    copied = place(sources, path, name, replace)
    results = [("copied" if copied else "already in sources", name)]
    if not copied and os.path.exists(os.path.join(sources, converted_name)):
        return results + [("already in sources", converted_name)]
    with tempfile.TemporaryDirectory() as outdir:
        converted = convert_to_xlsx(os.path.join(sources, name), outdir, soffice)
        place(sources, converted, converted_name, replace)
    return results + [("converted", converted_name)]


def add(directory: str, path: str, replace: bool = False, soffice: Optional[str] = None) -> List[str]:
    return [name for action, name in add_file(directory, path, replace, soffice) if action != "already in sources"]


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    add_parser = commands.add_parser("add", help="copy files into <dir>/sources/")
    add_parser.add_argument("dir")
    add_parser.add_argument("files", nargs="+")
    add_parser.add_argument("--replace", action="store_true", help="accept a new version of a file already in sources")
    args = parser.parse_args(argv)

    exit_code = 0
    for path in args.files:
        try:
            for action, name in add_file(args.dir, path, args.replace):
                print("%s %s" % (action, name))
        except IntakeError as error:
            print("error: %s" % error, file=sys.stderr)
            exit_code = exit_code or error.code
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
