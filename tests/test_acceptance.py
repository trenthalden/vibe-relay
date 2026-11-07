"""Gate capture, pre_verify, vibe_accept path matrix."""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from support import IsolatedHomeTest


class GateCaptureTests(IsolatedHomeTest):
    def test_capture_and_pre_verify(self):
        from acceptance import capture_terminal_gate, fresh_pass_for_tree, pre_verify_directive, working_tree_hash
        from state import update_state

        repo = Path(tempfile.mkdtemp(prefix="vibe-accept-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(repo, ignore_errors=True))
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        (repo / "a.py").write_text("x=1\n", encoding="utf-8")
        subprocess.run(["git", "add", "a.py"], cwd=repo, check=True)
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "i"], cwd=repo, check=True)

        # No changes → pre_verify silent
        self.assertIsNone(pre_verify_directive(changed_paths_arg=[], cwd=str(repo)))

        (repo / "a.py").write_text("x=2\n", encoding="utf-8")
        tree = working_tree_hash(str(repo))
        out = "FINISH PASSED: 1 commands; 1 tests, 0 skipped\n"
        rec = capture_terminal_gate(args={"command": "python -m check finish", "cwd": str(repo)}, result=out, cwd=str(repo))
        self.assertTrue(rec["passed"])
        self.assertEqual(rec["tree_hash"], tree)
        self.assertTrue(fresh_pass_for_tree(str(repo)))
        self.assertIsNone(pre_verify_directive(changed_paths_arg=["a.py"], cwd=str(repo)))

        # Stale after further edit
        (repo / "a.py").write_text("x=3\n", encoding="utf-8")
        directive = pre_verify_directive(changed_paths_arg=["a.py"], cwd=str(repo))
        self.assertEqual(directive["action"], "continue")
        self.assertIn("run the gate", directive["message"])


class VibeAcceptMatrixTests(IsolatedHomeTest):
    def _repo(self):
        repo = Path(tempfile.mkdtemp(prefix="vibe-matrix-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(repo, ignore_errors=True))
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        (repo / "src").mkdir()
        (repo / "src" / "ok.py").write_text("ok\n", encoding="utf-8")
        (repo / "outside.py").write_text("no\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "i"], cwd=repo, check=True)
        return repo

    def _pass_gate(self, repo: Path):
        from acceptance import capture_terminal_gate
        capture_terminal_gate(
            args={"command": "gate", "cwd": str(repo)},
            result="FINISH PASSED: 1 commands; 1 tests, 0 skipped\n",
            cwd=str(repo),
        )

    def test_inside_allowed_accept(self):
        from acceptance import vibe_accept
        repo = self._repo()
        (repo / "src" / "ok.py").write_text("ok2\n", encoding="utf-8")
        self._pass_gate(repo)
        out = vibe_accept({"allowed": "src/**", "cwd": str(repo)})
        self.assertTrue(out.startswith("ACCEPT"), out)

    def test_outside_allowed_reject(self):
        from acceptance import vibe_accept
        repo = self._repo()
        (repo / "outside.py").write_text("changed\n", encoding="utf-8")
        self._pass_gate(repo)
        out = vibe_accept({"allowed": "src/**", "cwd": str(repo)})
        self.assertTrue(out.startswith("REJECT"), out)
        self.assertIn("OUT:", out)

    def test_untracked_outside_reject(self):
        from acceptance import vibe_accept
        repo = self._repo()
        (repo / "leak.txt").write_text("x\n", encoding="utf-8")
        self._pass_gate(repo)
        out = vibe_accept({"allowed": "src/**", "cwd": str(repo)})
        self.assertTrue(out.startswith("REJECT"), out)
        self.assertIn("untracked", out)

    def test_deleted_inside_ok_with_gate(self):
        from acceptance import vibe_accept
        repo = self._repo()
        (repo / "src" / "ok.py").unlink()
        self._pass_gate(repo)
        out = vibe_accept({"allowed": "src/**", "cwd": str(repo)})
        self.assertTrue(out.startswith("ACCEPT"), out)
        self.assertIn("deleted", out)

    def test_no_gate_reject(self):
        from acceptance import vibe_accept
        repo = self._repo()
        (repo / "src" / "ok.py").write_text("ok3\n", encoding="utf-8")
        out = vibe_accept({"allowed": "src/**", "cwd": str(repo)})
        self.assertTrue(out.startswith("REJECT"), out)
        self.assertIn("gate", out.lower())


if __name__ == "__main__":
    unittest.main()
