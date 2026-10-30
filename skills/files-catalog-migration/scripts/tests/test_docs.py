import glob
import os
import re
import unittest

SKILL_DIR = os.path.join(os.path.dirname(__file__), "..", "..")
FORBIDDEN = "—–’‘“”«»… "


def skill_texts():
    paths = [os.path.join(SKILL_DIR, "SKILL.md")] + sorted(glob.glob(os.path.join(SKILL_DIR, "references", "*.md")))
    texts = {}
    for path in paths:
        with open(path, encoding="utf-8") as f:
            texts[path] = f.read()
    return texts


class DocsTest(unittest.TestCase):
    def test_referenced_files_exist(self):
        for path, text in skill_texts().items():
            for ref in re.findall(r"references/([a-z0-9-]+\.md)", text):
                self.assertTrue(os.path.exists(os.path.join(SKILL_DIR, "references", ref)), f"{path}: {ref}")
            for script in re.findall(r"(?:\$S/|scripts/)([a-z_]+\.py)", text):
                self.assertTrue(os.path.exists(os.path.join(SKILL_DIR, "scripts", script)), f"{path}: {script}")

    def test_no_typographic_characters_in_skill_text(self):
        for path, text in skill_texts().items():
            for char in FORBIDDEN:
                self.assertNotIn(char, text, f"{path} holds U+{ord(char):04X}")

    def test_frontmatter(self):
        text = skill_texts()[os.path.join(SKILL_DIR, "SKILL.md")]
        self.assertTrue(text.startswith("---\nname: files-catalog-migration\ndescription: Use when "))
