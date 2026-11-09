"""DEFECT 2 (R6): stop+park must work on the real chat path after state wipe.

Live failure: setup deletes ``state.json``; in-gateway ``fetch_account_usage``
sometimes returns no snapshot; plugin cached that empty result for 180s; ``used``
stayed None → ``should_park`` false → ``delegate_task`` ran despite stop=50 and
real session/week used ≥ 50. Executor-lane still blocked writes (``?% left``).

Regression: empty/poisoned plugin quota + Hermes in-process cache with real
windows → stop blocks ``delegate_task`` and auto-parks once.
"""
from __future__ import annotations

import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from unittest import mock

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


@dataclass
class _HermesWindow:
    label: str
    used_percent: float
    reset_at: datetime
    detail: Optional[str] = None
    scope: str = "account"


@dataclass
class _HermesSnap:
    provider: str = "anthropic"
    source: str = "oauth_usage_api"
    fetched_at: datetime = None  # type: ignore[assignment]
    title: str = "Account limits"
    plan: Optional[str] = None
    windows: tuple = ()
    details: tuple = ()
    unavailable_reason: Optional[str] = None
    identity: Optional[str] = None
    raw: Optional[dict] = None
    usage_window: bool = False

    def __post_init__(self):
        if self.fetched_at is None:
            self.fetched_at = datetime.now(timezone.utc)


class StopQuotaFreshnessTests(IsolatedHomeTest):
    def setUp(self):
        super().setUp()
        from executor_lane import clear_child_registry

        clear_child_registry()

    def tearDown(self):
        from executor_lane import clear_child_registry

        clear_child_registry()
        super().tearDown()

    def test_empty_plugin_cache_uses_hermes_cache_for_stop(self):
        """Real failing condition: poisoned empty quota + warm Hermes cache → block."""
        from state import save_state
        from stop_park import pre_tool_call_directive
        import quota as quota_mod

        repo = Path(tempfile.mkdtemp(prefix="vibe-stop-fresh-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(repo, ignore_errors=True))

        # Live shape after setup wipe + failed fetch (cached for 180s on 0.3.0):
        save_state(
            {
                "quota": {
                    "available": False,
                    "cache_fetched_at": 1_000_000.0,
                    "from_cache": False,
                    "provider": "anthropic",
                    "unavailable_reason": "no snapshot",
                },
                "quota_provider": "anthropic",
            }
        )

        reset = datetime.now(timezone.utc) + timedelta(hours=4)
        hermes_snap = _HermesSnap(
            windows=(
                _HermesWindow("Current session", 65.0, reset),
                _HermesWindow("Current week", 71.0, reset),
            )
        )

        def empty_fetch(_provider):
            return None

        calls = []

        def dispatch(tool, args):
            calls.append((tool, dict(args)))
            return '{"success": true, "job_id": "cron-r6-1"}'

        with mock.patch.object(quota_mod, "_hermes_cached_snapshot", return_value=hermes_snap):
            # Age within old full TTL but past negative TTL → must refresh via Hermes cache.
            usage = quota_mod.get_quota(
                provider="anthropic",
                fetch=empty_fetch,
                now=1_000_000.0 + 30,  # > NEGATIVE_CACHE_TTL (15), < 180
                force=False,
            )
            self.assertEqual(max(w["used_percent"] for w in usage["windows"]), 71.0)

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
        self.assertEqual(len(calls), 1)
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
        with mock.patch.object(quota_mod, "_hermes_cached_snapshot", return_value=None):
            a = quota_mod.get_quota(provider="anthropic", fetch=empty_fetch, now=t0, force=True)
            self.assertFalse(a.get("available"))
            # Within negative TTL → cached empty
            b = quota_mod.get_quota(
                provider="anthropic", fetch=empty_fetch, now=t0 + 1, force=False
            )
            self.assertTrue(b.get("from_cache"))
            self.assertEqual(len(calls), 1)
            # Past negative TTL → refetch even though < 180s
            c = quota_mod.get_quota(
                provider="anthropic",
                fetch=empty_fetch,
                now=t0 + NEGATIVE_CACHE_TTL_SECONDS + 1,
                force=False,
            )
            self.assertFalse(c.get("from_cache"))
            self.assertEqual(len(calls), 2)

    def test_executor_lane_still_fires_when_quota_empty_but_stop_needs_used(self):
        """Document the live split: empty used% → executor-lane yes, stop no (without fallback)."""
        from stop_park import pre_tool_call_directive
        from executor_lane import pre_tool_call_directive as lane_directive

        empty = {
            "provider": "anthropic",
            "available": False,
            "unavailable_reason": "no snapshot",
            "windows": [],
        }
        # Without usable used%, stop must not block (no false park on unknown).
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

        # Executor-lane still routes to executor when remaining unknown.
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
