"""DEFECT 2 (R6) + R7: stop+park with public fetch only + 15 s negative TTL.

R6: empty ``fetch_account_usage`` must not poison the plugin cache for 180 s.
R7: drop private ``agent.account_usage_cache.cached_account_usage``; keep only
public ``fetch_account_usage(..., read_only=True)``. Empty snapshot → retry
after 15 s; real snapshot → stop fires.
"""
from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from support import IsolatedHomeTest, FakeSnapshot, FakeWindow


def _delegation(**own):
    return {
        "backend": "vibe-relay",
        "backends": {
            "vibe-relay": {
                "self_remaining_threshold": 51,
                "stop_used_percent": 50,
                "park_buffer_seconds": 120,
                "executor_provider": "anthropic",
                "executor_model": "claude-haiku-4-5",
                "policy": "auto",
                **own,
            }
        },
    }


class StopQuotaFreshnessTests(IsolatedHomeTest):
    def setUp(self):
        super().setUp()
        from executor_lane import clear_child_registry

        clear_child_registry()

    def tearDown(self):
        from executor_lane import clear_child_registry

        clear_child_registry()
        super().tearDown()

    def test_empty_snapshot_retries_after_negative_ttl_then_stop_fires(self):
        """Empty plugin cache → short TTL → later public fetch with windows → block."""
        from state import save_state
        from stop_park import pre_tool_call_directive
        import quota as quota_mod
        from quota import NEGATIVE_CACHE_TTL_SECONDS

        repo = Path(tempfile.mkdtemp(prefix="vibe-stop-fresh-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(repo, ignore_errors=True))

        save_state({})
        reset = datetime.now(timezone.utc) + timedelta(hours=4)
        real_snap = FakeSnapshot(
            provider="anthropic",
            windows=(
                FakeWindow("Current session", 65.0, reset),
                FakeWindow("Current week", 71.0, reset),
            ),
        )
        calls = {"n": 0}

        def fetch(_provider):
            calls["n"] += 1
            if calls["n"] == 1:
                return None
            return real_snap

        t0 = 1_000_000.0
        empty = quota_mod.get_quota(provider="anthropic", fetch=fetch, now=t0, force=True)
        self.assertFalse(quota_mod._usable_usage(empty))
        self.assertEqual(calls["n"], 1)

        # Within negative TTL → still empty, no refetch.
        again = quota_mod.get_quota(
            provider="anthropic", fetch=fetch, now=t0 + 1, force=False
        )
        self.assertTrue(again.get("from_cache"))
        self.assertEqual(calls["n"], 1)

        # Past negative TTL → public fetch returns real windows → stop fires.
        usage = quota_mod.get_quota(
            provider="anthropic",
            fetch=fetch,
            now=t0 + NEGATIVE_CACHE_TTL_SECONDS + 1,
            force=False,
        )
        self.assertEqual(calls["n"], 2)
        self.assertEqual(max(w["used_percent"] for w in usage["windows"]), 71.0)

        dispatch_calls = []

        def dispatch(tool, args):
            dispatch_calls.append((tool, dict(args)))
            return '{"success": true, "job_id": "cron-r7-1"}'

        d = pre_tool_call_directive(
            tool_name="delegate_task",
            args={"goal": "add docstring", "allowed": "textutil/**", "gate": "true"},
            session_id="parent",
            task_id="parent",
            delegation=_delegation(stop_used_percent=50),
            usage=usage,
            dispatch=dispatch,
            repo=str(repo),
        )

        self.assertIsInstance(d, dict)
        self.assertEqual(d.get("action"), "block")
        self.assertIn("quota used 71%", d.get("message") or "")
        self.assertIn("≥ stop 50%", d.get("message") or "")
        self.assertEqual(len(dispatch_calls), 1)
        self.assertTrue((repo / "docs" / "vibe-park.md").is_file())

    def test_negative_cache_does_not_poison_for_full_ttl(self):
        """Empty fetch must not lock out refetch for CACHE_TTL_SECONDS."""
        from state import save_state
        import quota as quota_mod
        from quota import NEGATIVE_CACHE_TTL_SECONDS

        save_state({})
        calls = []

        def empty_fetch(_p):
            calls.append("empty")
            return None

        t0 = 2_000_000.0
        a = quota_mod.get_quota(provider="anthropic", fetch=empty_fetch, now=t0, force=True)
        self.assertFalse(a.get("available"))
        b = quota_mod.get_quota(
            provider="anthropic", fetch=empty_fetch, now=t0 + 1, force=False
        )
        self.assertTrue(b.get("from_cache"))
        self.assertEqual(len(calls), 1)
        c = quota_mod.get_quota(
            provider="anthropic",
            fetch=empty_fetch,
            now=t0 + NEGATIVE_CACHE_TTL_SECONDS + 1,
            force=False,
        )
        self.assertFalse(c.get("from_cache"))
        self.assertEqual(len(calls), 2)

    def test_no_private_account_usage_cache_import(self):
        """R7: quota.py must not import the private cache helper."""
        import ast
        from pathlib import Path

        import quota as quota_mod

        tree = ast.parse(Path(quota_mod.__file__).read_text(encoding="utf-8"))
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                imported.append(mod)
                imported.extend(f"{mod}.{a.name}" for a in node.names)
        joined = " ".join(imported)
        self.assertNotIn("account_usage_cache", joined)
        self.assertNotIn("cached_account_usage", joined)
        self.assertIn("agent.account_usage", joined)


    def test_executor_lane_still_fires_when_quota_empty_but_stop_needs_used(self):
        """Empty used% → executor-lane yes, stop no (without a usable fetch)."""
        from stop_park import pre_tool_call_directive
        from executor_lane import pre_tool_call_directive as lane_directive

        empty = {
            "provider": "anthropic",
            "available": False,
            "unavailable_reason": "no snapshot",
            "windows": [],
        }
        d_stop = pre_tool_call_directive(
            tool_name="delegate_task",
            args={"goal": "x"},
            session_id="parent",
            task_id="parent",
            delegation=_delegation(stop_used_percent=50),
            usage=empty,
            repo=".",
        )
        self.assertIsNone(d_stop)

        d_lane = lane_directive(
            tool_name="patch",
            args={"path": "a.py"},
            session_id="parent",
            task_id="parent",
            delegation=_delegation(),
            usage=empty,
        )
        self.assertEqual(d_lane and d_lane.get("action"), "block")
        self.assertIn("?% left", d_lane.get("message") or "")


if __name__ == "__main__":
    import unittest

    unittest.main()
