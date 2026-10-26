import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import check_skills  # noqa: E402

SKILL = """---
name: {name}
description: {description}
---

# Title
"""


class CheckSkillsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.add_skill("alpha")
        self.write_marketplace([self.entry("alpha")])
        self.write_readme(["alpha"])

    def tearDown(self):
        self.tmp.cleanup()

    def add_skill(self, folder, name=None, description="Use when testing."):
        path = os.path.join(self.root, "skills", folder)
        os.makedirs(path)
        with open(os.path.join(path, "SKILL.md"), "w") as f:
            f.write(SKILL.format(name=name or folder, description=description))

    def entry(self, name, skills=None, source="./", strict=False):
        return {"name": name, "source": source, "strict": strict, "skills": skills or [f"./skills/{name}"]}

    def write_marketplace(self, plugins):
        os.makedirs(os.path.join(self.root, ".claude-plugin"), exist_ok=True)
        with open(os.path.join(self.root, ".claude-plugin", "marketplace.json"), "w") as f:
            json.dump({"name": "flowkiwi", "owner": {"name": "Flowkiwi"}, "plugins": plugins}, f)

    def write_readme(self, names):
        with open(os.path.join(self.root, "README.md"), "w") as f:
            f.write("\n".join(f"| [`{n}`](skills/{n}) | x |" for n in names))

    def test_valid_repository_has_no_errors(self):
        self.assertEqual(check_skills.check(self.root), [])

    def test_name_must_match_folder(self):
        self.add_skill("beta", name="other")
        self.write_marketplace([self.entry("alpha"), self.entry("beta")])
        self.write_readme(["alpha", "beta"])
        self.assertTrue(any("beta" in e and "name" in e for e in check_skills.check(self.root)))

    def test_description_required(self):
        self.add_skill("beta", description="")
        self.write_marketplace([self.entry("alpha"), self.entry("beta")])
        self.write_readme(["alpha", "beta"])
        self.assertTrue(any("beta" in e and "description" in e for e in check_skills.check(self.root)))

    def test_skill_without_marketplace_entry(self):
        self.add_skill("beta")
        self.write_readme(["alpha", "beta"])
        self.assertTrue(any("beta" in e and "marketplace" in e for e in check_skills.check(self.root)))

    def test_marketplace_entry_must_load_only_its_skill(self):
        self.write_marketplace([self.entry("alpha", skills=["./skills"])])
        self.assertTrue(check_skills.check(self.root))

    def test_marketplace_entry_must_not_be_strict(self):
        self.write_marketplace([self.entry("alpha", strict=True)])
        self.assertTrue(any("strict" in e for e in check_skills.check(self.root)))

    def test_marketplace_path_must_exist(self):
        self.write_marketplace([self.entry("alpha"), self.entry("ghost")])
        self.assertTrue(any("ghost" in e for e in check_skills.check(self.root)))

    def test_empty_repository_without_marketplace_is_valid(self):
        with tempfile.TemporaryDirectory() as empty:
            self.assertEqual(check_skills.check(empty), [])

    def test_skills_require_a_marketplace(self):
        os.remove(os.path.join(self.root, ".claude-plugin", "marketplace.json"))
        self.assertTrue(any("marketplace" in e for e in check_skills.check(self.root)))

    def test_skill_missing_from_readme(self):
        self.write_readme([])
        self.assertTrue(any("README" in e for e in check_skills.check(self.root)))



class CheckPathsTest(unittest.TestCase):
    def test_allowed_paths(self):
        files = [
            "README.md", "LICENSE", ".gitignore",
            ".claude-plugin/marketplace.json",
            ".github/workflows/ci.yml", ".github/dependabot.yml",
            "scripts/check_skills.py",
            "skills/alpha/SKILL.md",
            "skills/alpha/references/core.md",
            "skills/alpha/scripts/tool.py",
            "skills/alpha/scripts/tests/fixtures/products.csv",
            "skills/alpha/assets/schema.json.gz",
        ]
        self.assertEqual(check_skills.check_paths(files), [])

    def test_rejects_paths_outside_the_allowlist(self):
        files = [
            "migrations/acme/state.json",
            "exports.csv",
            "skills/alpha/exports/products.csv",
            "skills/alpha/notes.md",
            ".claude-plugin/plugin.json",
        ]
        errors = check_skills.check_paths(files)
        self.assertEqual(len(errors), len(files))
        for path in files:
            self.assertTrue(any(path in e for e in errors), path)


if __name__ == "__main__":
    unittest.main()
