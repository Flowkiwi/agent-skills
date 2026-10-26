"""Check that every skill is consistent with the marketplace and the README,
and that the repository only tracks files under the allowed paths.

Usage: python3 scripts/check_skills.py [repo_root]
Exits 1 and lists the problems when something is inconsistent.
"""

import glob
import json
import os
import re
import subprocess
import sys

# Keep in sync with the "Allowed paths" section of the README.
ALLOWED_PATHS = [
    r"skills/[^/]+/SKILL\.md",
    r"skills/[^/]+/(references|scripts|assets)/.+",
    r"\.claude-plugin/marketplace\.json",
    r"\.github/.+",
    r"scripts/.+",
    r"README\.md",
    r"LICENSE",
    r"\.gitignore",
]


def frontmatter(path):
    """Top-level `key: value` pairs of a SKILL.md frontmatter (nested keys are ignored)."""
    with open(path, encoding="utf-8") as f:
        lines = f.read().split("\n")
    if not lines or lines[0].strip() != "---":
        return None
    fields = {}
    for line in lines[1:]:
        if line.strip() == "---":
            return fields
        if line and not line[0].isspace() and ":" in line:
            key, value = line.split(":", 1)
            fields[key.strip()] = value.strip().strip("\"'")
    return None


def check(root):
    errors = []
    skill_dirs = sorted(
        os.path.basename(os.path.dirname(p)) for p in glob.glob(os.path.join(root, "skills", "*", "SKILL.md"))
    )

    for folder in skill_dirs:
        fields = frontmatter(os.path.join(root, "skills", folder, "SKILL.md"))
        if fields is None:
            errors.append(f"skills/{folder}/SKILL.md: missing or unterminated frontmatter")
            continue
        if fields.get("name") != folder:
            errors.append(f"skills/{folder}/SKILL.md: name '{fields.get('name')}' must equal the folder name")
        if not fields.get("description"):
            errors.append(f"skills/{folder}/SKILL.md: description is required")

    marketplace_path = os.path.join(root, ".claude-plugin", "marketplace.json")
    try:
        with open(marketplace_path, encoding="utf-8") as f:
            plugins = json.load(f).get("plugins", [])
    except (OSError, ValueError) as error:
        return errors + [f".claude-plugin/marketplace.json: {error}"]

    entries = {}
    for plugin in plugins:
        name = plugin.get("name")
        entries[name] = plugin
        expected = [f"./skills/{name}"]
        if plugin.get("source") != "./":
            errors.append(f"marketplace plugin '{name}': source must be './'")
        if plugin.get("strict") is not False:
            errors.append(f"marketplace plugin '{name}': strict must be false")
        if plugin.get("skills") != expected:
            errors.append(f"marketplace plugin '{name}': skills must be {expected} so it loads only this skill")
        if name not in skill_dirs:
            errors.append(f"marketplace plugin '{name}': skills/{name}/SKILL.md does not exist")

    readme_path = os.path.join(root, "README.md")
    readme = open(readme_path, encoding="utf-8").read() if os.path.exists(readme_path) else ""
    for folder in skill_dirs:
        if folder not in entries:
            errors.append(f"skills/{folder}: no plugin entry in the marketplace")
        if f"(skills/{folder})" not in readme:
            errors.append(f"skills/{folder}: missing from the README skills table")

    return errors


def check_paths(files):
    return [
        f"{path}: not under an allowed path (see the README)"
        for path in files
        if not any(re.fullmatch(pattern, path) for pattern in ALLOWED_PATHS)
    ]


def tracked_files(root):
    output = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True, check=True).stdout
    return [line for line in output.split("\n") if line]


def main(argv):
    root = argv[1] if len(argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    errors = check(root) + check_paths(tracked_files(root))
    for error in errors:
        print(f"error: {error}")
    if not errors:
        print("All skills are consistent")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
