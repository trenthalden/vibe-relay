"""DEFECT 1 (R6): handlers must tolerate string args and non-dict last_gate.

Live: vibe_accept crashed with ``AttributeError: 'str' object has no attribute 'get'``
because ``provider.route_task`` stored the gate *command string* under ``last_gate``
(overwriting the PASS record dict). Also covers JSON-string args from deferred
``tool_call`` bridges.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

from support import IsolatedHomeTest


class LastGateStringRegressionTests(IsolatedHomeTest):
    def _repo(self) -> Path:
        repo = Path(tempfile.mkdtemp(prefix="vibe-gate-str-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(repo, ignore_errors=True))
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        (repo / "textutil").mkdir()
        (repo / "textutil" / "slugify.py").write_text("def slugify(v): return v\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "i"],
            cwd=repo,
            check=True,
        )
        (repo / "textutil" / "slugify.py").write_text(
            'def slugify(v):\n    """doc"""\n    return v\n',
            encoding="utf-8",
        )
        return repo

    def test_vibe_accept_survives_last_gate_command_string(self):
        """RED on 0.3.0 (gate.get on str) → GREEN: treat non-dict last_gate as missing."""
        from acceptance import vibe_accept
        from state import save_state

        repo = self._repo()
        # Exact live shape from vrdemo state.json after delegate_task:
        save_state(
            {
                "last_allowed": "textutil/**",
                "last_gate": (
                    "grep -q 'doc' textutil/slugify.py && echo PASS || echo FAIL"
                ),
            }
        )
        out = vibe_accept({"allowed": "textutil/**", "cwd": str(repo)})
        # Must not raise; without a PASS record → REJECT (not TOOL_ERROR).
        self.assertTrue(out.startswith("REJECT"), out)
        self.assertIn("no fresh gate PASS", out)

    def test_vibe_accept_json_string_args(self):
        from acceptance import vibe_accept
        from state import save_state

        repo = self._repo()
        save_state({"last_allowed": "textutil/**", "last_gate": {}})
        payload = json.dumps({"allowed": "textutil/**", "cwd": str(repo)})
        out = vibe_accept(payload)  # type: ignore[arg-type]
        self.assertTrue(out.startswith("REJECT") or out.startswith("ACCEPT"), out)

    def test_provider_does_not_overwrite_last_gate_with_string(self):
        from provider import VibeRelayProvider
        from state import load_state, save_state
        from support import make_snapshot
        from quota import get_quota

        save_state(
            {
                "last_gate": {
                    "passed": True,
                    "tree_hash": "abc",
                    "command": "python -m unittest",
                }
            }
        )

        def fetch(_p):
            return make_snapshot(40.0)

        get_quota(provider="anthropic", fetch=fetch, force=True)
        provider = VibeRelayProvider()
        provider.route_task(
            {
                "goal": "PRIVATE_PROVIDER_GOAL_SENTINEL",
                "allowed": "textutil/**",
                "gate": "PRIVATE_PROVIDER_GATE_SENTINEL",
                "lane": "executor",
            },
            {
                "backends": {
                    "vibe-relay": {
                        "policy": "executor-always",
                        "executor_provider": "anthropic",
                        "executor_model": "claude-haiku-4-5",
                    }
                }
            },
        )
        state = load_state()
        self.assertIsInstance(state.get("last_gate"), dict)
        self.assertEqual(state["last_gate"].get("passed"), True)
        self.assertIsNone(state.get("last_gate_command"))
        self.assertNotIn("gate", state.get("last_route", {}))
        self.assertNotIn("goal", state.get("last_route", {}))
        self.assertNotIn("PRIVATE_PROVIDER_GOAL_SENTINEL", str(state))
        self.assertNotIn("PRIVATE_PROVIDER_GATE_SENTINEL", str(state))


class DfHandlerStringArgsTests(IsolatedHomeTest):
    def test_df_check_accepts_json_string_args(self):
        import dev_framework as df

        repo = Path(tempfile.mkdtemp(prefix="vibe-df-str-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(repo, ignore_errors=True))
        # No .devframework → ERROR string, but must not AttributeError on args.get
        out = df.df_check(json.dumps({"target": str(repo), "mode": "doctor"}))
        self.assertIn("ERROR", out)

    def test_df_nav_accepts_json_string_args(self):
        import dev_framework as df

        repo = Path(tempfile.mkdtemp(prefix="vibe-df-nav-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(repo, ignore_errors=True))
        out = df.df_nav(json.dumps({"target": str(repo), "command": "brief"}))
        self.assertIn("ERROR", out)

    def test_df_init_accepts_json_string_args(self):
        import dev_framework as df

        # Missing target path → still must coerce and refuse cleanly
        out = df.df_init('{"target": "/nonexistent/vibe-df-init-xyz", "kind": "tool"}')
        self.assertTrue(isinstance(out, str) and out)


class CoerceHelperTests(IsolatedHomeTest):
    def test_coerce_tool_args_shapes(self):
        from tool_args import as_dict, coerce_tool_args

        self.assertEqual(coerce_tool_args(None), {})
        self.assertEqual(coerce_tool_args({"a": 1})["a"], 1)
        self.assertEqual(coerce_tool_args('{"allowed": "x/**"}')["allowed"], "x/**")
        self.assertEqual(coerce_tool_args("not-json"), {})
        self.assertEqual(as_dict("gate-command-string"), {})
        self.assertEqual(as_dict({"passed": True})["passed"], True)


if __name__ == "__main__":
    import unittest

    unittest.main()
