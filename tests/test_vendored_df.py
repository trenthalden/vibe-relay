"""Vendored DEV Framework unit tests still pass; df_check returns a terminal command."""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from support import ROOT, IsolatedHomeTest

DF = ROOT / "vendor" / "dev_framework"


class VendoredDfSuite(unittest.TestCase):
    def test_df_unittest_suite(self):
        run = subprocess.run(
            [sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-v"],
            cwd=str(DF),
            capture_output=True,
            text=True,
            timeout=300,
            env={**dict(**{k: v for k, v in __import__("os").environ.items()}), "PYTHONDONTWRITEBYTECODE": "1"},
        )
        if run.returncode != 0:
            self.fail(run.stdout + "\n" + run.stderr)
        self.assertIn("OK", run.stderr + run.stdout)


class DfCheckCommandTests(IsolatedHomeTest):
    def test_df_check_returns_command_not_executing_project_json(self):
        sys.path.insert(0, str(ROOT / "vendor"))
        import dev_framework as df

        repo = Path(tempfile.mkdtemp(prefix="df-check-cmd-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(repo, ignore_errors=True))
        # Minimal .devframework so df_check resolves
        dfw = repo / ".devframework"
        dfw.mkdir()
        (dfw / "check.py").write_text("print('should-not-run')\n", encoding="utf-8")
        # Poison project.json command — must not be executed by df_check
        (dfw / "project.json").write_text(
            '{"commands": {"test": ["%s", "-c", "raise SystemExit(\'executed project.json\')"]}}' % sys.executable,
            encoding="utf-8",
        )
        out = df.df_check({"target": str(repo), "mode": "finish"})
        self.assertIn("RUN VIA TERMINAL", out)
        self.assertIn("check.py", out)
        self.assertIn("finish", out)
        self.assertNotIn("executed project.json", out)
