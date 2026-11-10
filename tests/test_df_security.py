"""Maintainer R2 security fixes: df_nav isolation, scrubbed env, df_init path refuse."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from support import ROOT, IsolatedHomeTest

DF_PKG = ROOT / "vendor" / "dev_framework"


def _git_init(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "--allow-empty", "-qm", "i"], cwd=repo, check=True)


class DfNavIsolationTests(IsolatedHomeTest):
    def test_malicious_target_navigate_does_not_run(self):
        sys.path.insert(0, str(ROOT / "vendor"))
        import dev_framework as df

        repo = Path(tempfile.mkdtemp(prefix="df-nav-evil-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(repo, ignore_errors=True))
        marker = repo / "PWNED_MARKER"
        dfw = repo / ".devframework"
        dfw.mkdir()
        (dfw / "check.py").write_text("print('check')\n", encoding="utf-8")
        (dfw / "evil_sibling.py").write_text(
            "from pathlib import Path\n"
            f"Path({str(marker)!r}).write_text('pwned', encoding='utf-8')\n",
            encoding="utf-8",
        )
        (dfw / "navigate.py").write_text(
            "import evil_sibling  # noqa: F401 — side effect writes marker\n"
            "print('MALICIOUS_NAV_RAN')\n",
            encoding="utf-8",
        )
        (repo / "PROJECT.md").write_text("# Evil\n", encoding="utf-8")

        out = df.df_nav({"target": str(repo), "command": "brief"})
        self.assertFalse(marker.exists(), f"marker written — target navigate executed:\n{out}")
        self.assertNotIn("MALICIOUS_NAV_RAN", out)


class ScrubbedEnvTests(IsolatedHomeTest):
    def test_api_key_absent_in_child_env(self):
        sys.path.insert(0, str(ROOT / "vendor"))
        import dev_framework as df

        captured = {}

        def fake_run(argv, cwd=None, capture_output=None, text=None, encoding=None,
                     errors=None, timeout=None, env=None):
            captured["env"] = dict(env or {})

            class R:
                returncode = 0
                stdout = "INIT OK fake"
                stderr = ""

            return R()

        with mock.patch.object(df.subprocess, "run", side_effect=fake_run):
            with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test-should-not-leak", "PATH": os.environ.get("PATH", "")}):
                # Call _run directly so we do not need a real install target.
                code, _out = df._run([sys.executable, "-c", "print('ok')"], DF_PKG, 30)

        self.assertEqual(code, 0)
        self.assertIsNotNone(captured.get("env"))
        self.assertNotIn("OPENAI_API_KEY", captured["env"])
        for key in captured["env"]:
            self.assertFalse(key.endswith("_API_KEY"), f"leaked {key}")
        self.assertIn("PATH", captured["env"])
        self.assertEqual(captured["env"].get("PYTHONDONTWRITEBYTECODE"), "1")


def _safe_mkdtemp(prefix: str) -> Path:
    """Temp dir outside Hermes homes (resolved /tmp on POSIX; default on Windows)."""
    root = str(Path("/tmp").resolve()) if os.name != "nt" else None
    return Path(tempfile.mkdtemp(prefix=prefix, dir=root))


class DfInitTargetGuardTests(IsolatedHomeTest):
    def _df(self):
        sys.path.insert(0, str(ROOT / "vendor"))
        import dev_framework as df
        return df

    def test_refuse_hermes_home(self):
        df = self._df()
        home = Path(os.environ["HERMES_HOME"]).resolve()
        with mock.patch.object(df, "_run", return_value=(0, "should-not-run")):
            out = df.df_init({"target": str(home), "kind": "explore", "name": "x"})
        self.assertTrue(out.startswith("ERROR:"), out)
        self.assertIn("Hermes home", out)

    def test_refuse_hermes_home_subtree(self):
        df = self._df()
        home = Path(os.environ["HERMES_HOME"]).resolve()
        sub = home / "plugins" / "nope"
        sub.mkdir(parents=True, exist_ok=True)
        with mock.patch.object(df, "_run", return_value=(0, "should-not-run")):
            out = df.df_init({"target": str(sub), "kind": "explore", "name": "x"})
        self.assertTrue(out.startswith("ERROR:"), out)
        self.assertIn("Hermes home", out)

    def test_refuse_protected_dot_folders(self):
        df = self._df()
        # FAKE HOME only — never touch the real ~/.ssh / ~/.aws / …
        fake_home = _safe_mkdtemp("df-prot-home-")
        self.addCleanup(lambda: __import__("shutil").rmtree(fake_home, ignore_errors=True))
        home_env = {"HOME": str(fake_home), "USERPROFILE": str(fake_home)}
        for name in (".ssh", ".aws", ".config", ".gnupg", ".kube", ".docker"):
            target = fake_home / name
            target.mkdir(parents=True, exist_ok=True)
            with mock.patch.object(df, "_run", return_value=(0, "should-not-run")):
                with mock.patch.dict(os.environ, home_env, clear=False):
                    with mock.patch.object(Path, "home", return_value=fake_home):
                        with mock.patch.object(df, "_user_home", return_value=fake_home):
                            out = df.df_init({"target": str(target), "kind": "explore", "name": "x"})
            self.assertTrue(out.startswith("ERROR:"), f"{name}: {out}")
            self.assertIn("protected", out.lower())

    def test_refuse_home_itself_and_filesystem_root(self):
        df = self._df()
        fake_home = _safe_mkdtemp("df-home-root-")
        self.addCleanup(lambda: __import__("shutil").rmtree(fake_home, ignore_errors=True))
        with mock.patch.object(df, "_run", return_value=(0, "should-not-run")):
            with mock.patch.object(df, "_user_home", return_value=fake_home):
                out_home = df.df_init({"target": str(fake_home), "kind": "explore", "name": "x"})
                out_root = df.df_init({"target": "/", "kind": "explore", "name": "x"})
        self.assertTrue(out_home.startswith("ERROR:"), out_home)
        self.assertIn("$HOME", out_home)
        self.assertTrue(out_root.startswith("ERROR:"), out_root)
        self.assertIn("filesystem root", out_root.lower())

    def test_allow_git_work_tree(self):
        df = self._df()
        repo = _safe_mkdtemp("df-init-ok-")
        self.addCleanup(lambda: __import__("shutil").rmtree(repo, ignore_errors=True))
        _git_init(repo)
        # Stub installer so we only test the guard.
        with mock.patch.object(df, "_run", return_value=(0, "Installed.\nDone.")):
            out = df.df_init({"target": str(repo), "kind": "explore", "name": "okproj"})
        self.assertIn("INIT OK", out, out)


class AcceptanceSessionCwdTests(IsolatedHomeTest):
    def test_no_cwd_uses_resolve_agent_cwd(self):
        from acceptance import working_tree_hash

        repo = Path(tempfile.mkdtemp(prefix="vibe-cwd-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(repo, ignore_errors=True))
        _git_init(repo)
        (repo / "f.txt").write_text("a\n", encoding="utf-8")

        with mock.patch("acceptance.resolve_session_cwd", return_value=str(repo)):
            h1 = working_tree_hash(None)
            h2 = working_tree_hash(str(repo))
        self.assertEqual(h1, h2)


if __name__ == "__main__":
    unittest.main()
