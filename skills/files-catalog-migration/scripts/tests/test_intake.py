import glob
import hashlib
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import intake
from demo_files import write_demo_xlsx

REAL_FIND_SOFFICE = intake.find_soffice
INTAKE = os.path.join(os.path.dirname(__file__), "..", "intake.py")
FAKE_SOFFICE = """#!/usr/bin/env python3
import os
import sys

outdir = sys.argv[sys.argv.index("--outdir") + 1]
stem = os.path.splitext(os.path.basename(sys.argv[-1]))[0]
with open(os.path.join(outdir, stem + ".xlsx"), "wb") as f:
    f.write(b"converted")
"""


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


class IntakeTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root)
        self.dir = os.path.join(self.root, "workspace")
        os.makedirs(self.dir)
        self.xlsx = self.write("demo-catalogue.xlsx", b"")
        write_demo_xlsx(self.xlsx)
        patcher = mock.patch.object(intake, "find_soffice", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, name, content, folder="incoming"):
        folder_path = os.path.join(self.root, folder)
        os.makedirs(folder_path, exist_ok=True)
        path = os.path.join(folder_path, name)
        with open(path, "wb") as f:
            f.write(content)
        return path

    def fake_soffice(self):
        path = os.path.join(self.root, "fake-soffice")
        with open(path, "w") as f:
            f.write(FAKE_SOFFICE)
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
        return path

    def test_copy_and_idempotent(self):
        self.assertEqual(intake.add(self.dir, self.xlsx), ["demo-catalogue.xlsx"])
        self.assertEqual(intake.add(self.dir, self.xlsx), [])
        self.assertEqual(sha(self.xlsx), sha(os.path.join(self.dir, "sources", "demo-catalogue.xlsx")))

    def test_clash_and_replace(self):
        intake.add(self.dir, self.xlsx)
        other = self.write("demo-catalogue.xlsx", b"other", folder="other")
        with self.assertRaises(intake.IntakeError) as error:
            intake.add(self.dir, other)
        self.assertEqual(error.exception.code, 1)
        intake.add(self.dir, other, replace=True)
        self.assertEqual(len(glob.glob(os.path.join(self.dir, "sources", "previous", "*-demo-catalogue.xlsx"))), 1)
        self.assertEqual(sha(other), sha(os.path.join(self.dir, "sources", "demo-catalogue.xlsx")))

    def test_xls_without_libreoffice(self):
        xls = self.write("old.xls", b"\xd0\xcf\x11\xe0")
        with self.assertRaises(intake.IntakeError) as error:
            intake.add(self.dir, xls, soffice=None)
        self.assertEqual(error.exception.code, 3)
        self.assertIn("save it as .xlsx", str(error.exception))

    def test_xls_conversion(self):
        fake = self.fake_soffice()
        xls = self.write("old.xls", b"x")
        self.assertEqual(intake.add(self.dir, xls, soffice=fake), ["old.xls", "old.xlsx"])
        self.assertTrue(os.path.exists(os.path.join(self.dir, "sources", "old.xlsx")))
        self.assertNotIn("old.xlsx", os.listdir(os.path.dirname(xls)))

    def test_unsupported(self):
        with self.assertRaises(intake.IntakeError) as error:
            intake.add(self.dir, self.write("fiche.pdf", b"%PDF"))
        self.assertEqual(error.exception.code, 2)

    def test_missing_file(self):
        with self.assertRaises(intake.IntakeError) as error:
            intake.add(self.dir, os.path.join(self.root, "nope.xlsx"))
        self.assertEqual(error.exception.code, 2)

    def test_find_soffice(self):
        mac = "/Applications/LibreOffice.app/Contents/MacOS/soffice"
        with mock.patch.object(intake.shutil, "which", side_effect=lambda name: "/usr/bin/" + name if name == "libreoffice" else None):
            self.assertEqual(REAL_FIND_SOFFICE(), "/usr/bin/libreoffice")
        with mock.patch.object(intake.shutil, "which", return_value=None), \
                mock.patch.object(intake.os.path, "exists", side_effect=lambda path: path == mac):
            self.assertEqual(REAL_FIND_SOFFICE(), mac)
        with mock.patch.object(intake.shutil, "which", return_value=None), \
                mock.patch.object(intake.os.path, "exists", return_value=False):
            self.assertIsNone(REAL_FIND_SOFFICE())

    def test_conversion_failure(self):
        failing = os.path.join(self.root, "failing-soffice")
        with open(failing, "w") as f:
            f.write("#!/usr/bin/env python3\n")
        os.chmod(failing, 0o755)
        with self.assertRaises(intake.IntakeError) as error:
            intake.add(self.dir, self.write("old.xls", b"x"), soffice=failing)
        self.assertEqual(error.exception.code, 3)

    def test_xls_again_keeps_converted_file(self):
        fake = self.fake_soffice()
        xls = self.write("old.xls", b"x")
        intake.add(self.dir, xls, soffice=fake)
        self.assertEqual(intake.add(self.dir, xls, soffice=fake), [])

    def test_cli_exit_codes(self):
        def run(*args):
            return subprocess.run([sys.executable, INTAKE] + list(args), stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, universal_newlines=True)

        first = run("add", self.dir, self.xlsx)
        self.assertEqual((first.returncode, first.stdout.strip()), (0, "copied demo-catalogue.xlsx"))
        again = run("add", self.dir, self.xlsx)
        self.assertEqual((again.returncode, again.stdout.strip()), (0, "already in sources demo-catalogue.xlsx"))
        other = self.write("demo-catalogue.xlsx", b"other", folder="other")
        self.assertEqual(run("add", self.dir, other).returncode, 1)
        self.assertEqual(run("add", self.dir, other, "--replace").returncode, 0)
        pdf = run("add", self.dir, self.write("fiche.pdf", b"%PDF"))
        self.assertEqual(pdf.returncode, 2)
        self.assertIn("fiche.pdf", pdf.stderr)


if __name__ == "__main__":
    unittest.main()
