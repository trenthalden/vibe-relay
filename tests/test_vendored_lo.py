"""Vendored lean-orchestration pin match + its unittest suite still passes."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

from support import ROOT

LO = ROOT / "vendor" / "lean_orchestration"
PIN = ROOT / "vendor" / "lean_orchestration.SOURCE.json"
DF_PIN = ROOT / "vendor" / "dev_framework.SOURCE.json"
DF = ROOT / "vendor" / "dev_framework"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _tree_files(root: Path) -> dict:
    out = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        out[path.relative_to(root).as_posix()] = _sha256(path)
    return out


def _assert_pin_matches(test: unittest.TestCase, root: Path, pin_path: Path) -> None:
    pin = json.loads(pin_path.read_text(encoding="utf-8"))
    expected = pin.get("files") or {}
    actual = _tree_files(root)
    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    changed = sorted(k for k in set(expected) & set(actual) if expected[k] != actual[k])
    if missing or extra or changed:
        test.fail(
            "pin mismatch for %s: missing=%s extra=%s changed=%s"
            % (pin_path.name, missing, extra, changed)
        )


class VendoredLoPinTests(unittest.TestCase):
    def test_lean_orchestration_pin_matches_tree(self):
        self.assertTrue(PIN.is_file(), "missing %s" % PIN)
        _assert_pin_matches(self, LO, PIN)

    def test_dev_framework_pin_matches_tree(self):
        self.assertTrue(DF_PIN.is_file(), "missing %s" % DF_PIN)
        _assert_pin_matches(self, DF, DF_PIN)


class VendoredLoSuite(unittest.TestCase):
    def test_lo_unittest_suite(self):
        env = {k: v for k, v in os.environ.items()}
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        # KE-2026-11-20-TMPDIR-NESTED-PROJECT: keep fixture temps outside DF projects.
        if "TMPDIR" not in env or not env["TMPDIR"]:
            env["TMPDIR"] = "/tmp"
        # Do not inherit the parent's Hermes PYTHONPATH — it can shadow lo_core during
        # LO's own scratch-copy RED subprocesses.
        env["PYTHONPATH"] = str(LO)
        run = subprocess.run(
            [sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-v"],
            cwd=str(LO),
            capture_output=True,
            text=True,
            timeout=300,
            env=env,
        )
        combined = (run.stdout or "") + "\n" + (run.stderr or "")
        if run.returncode != 0:
            self.fail(combined)
        self.assertIn("OK", combined)
        # The vendored suite grows with each refresh; pin "ran and passed", not a count.
        ran = re.search(r"Ran (\d+) tests", combined)
        self.assertIsNotNone(ran, combined[-2000:])
        self.assertGreater(int(ran.group(1)), 0)
        self.assertNotIn("skipped=", combined)


if __name__ == "__main__":
    unittest.main()
