"""Respect cron.allow_agent_scheduling when parking from a cron session (R7).

Auto-park via ``ctx.dispatch_tool`` would bypass core's cron toolset denylist
(``HERMES_CRON_SESSION`` + ``_resolve_cron_disabled_toolsets``). Skip scheduling
unless the gate is on; still block; message must say no resume timer was set
because this is a scheduled run.
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from support import IsolatedHomeTest, FakeSnapshot, FakeWindow, make_snapshot


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


class CronParkGuardTests(IsolatedHomeTest):
    def setUp(self):
        super().setUp()
        from executor_lane import clear_child_registry

        clear_child_registry()
        self._prev_cron = os.environ.get("HERMES_CRON_SESSION")
        os.environ.pop("HERMES_CRON_SESSION", None)

    def tearDown(self):
        from executor_lane import clear_child_registry

        clear_child_registry()
        if self._prev_cron is None:
            os.environ.pop("HERMES_CRON_SESSION", None)
        else:
            os.environ["HERMES_CRON_SESSION"] = self._prev_cron
        super().tearDown()

    def test_cron_scheduling_blocked_reads_env_and_gate(self):
        """Wiring: ``HERMES_CRON_SESSION`` env + ``agent_scheduling_allowed`` gate."""
        from park import cron_scheduling_blocked, in_cron_session

        self.assertFalse(in_cron_session())
        self.assertFalse(cron_scheduling_blocked())
        os.environ["HERMES_CRON_SESSION"] = "1"
        self.assertTrue(in_cron_session())
        with mock.patch("park.agent_scheduling_allowed", return_value=False):
            self.assertTrue(cron_scheduling_blocked())
        with mock.patch("park.agent_scheduling_allowed", return_value=True):
            self.assertFalse(cron_scheduling_blocked())

    def _seed_usage(self, used: float = 71.0):
        from quota import get_quota

        reset = datetime.now(timezone.utc) + timedelta(hours=3)
        snap = FakeSnapshot(
            provider="anthropic",
            windows=(FakeWindow(label="Session", used_percent=used, reset_at=reset),),
        )

        def fetch(_p):
            return snap

        return get_quota(provider="anthropic", fetch=fetch, force=True), reset

    def test_auto_park_skips_cron_in_cron_session_without_gate(self):
        from park import CRON_NO_RESUME_TIMER
        from state import load_state
        from stop_park import pre_tool_call_directive

        os.environ["HERMES_CRON_SESSION"] = "1"
        usage, _reset = self._seed_usage()
        repo = Path(tempfile.mkdtemp(prefix="vibe-cron-guard-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(repo, ignore_errors=True))
        calls = []

        def dispatch(tool, args):
            calls.append((tool, dict(args)))
            return '{"success": true, "job_id": "should-not-run"}'

        # Freeze reload so the cron_scheduling_blocked patch sticks (production
        # reload is for stale gateway modules; unit tests patch the live module).
        with mock.patch("importlib.reload", side_effect=lambda m: m):
            with mock.patch("park.cron_scheduling_blocked", return_value=True):
                d = pre_tool_call_directive(
                    tool_name="delegate_task",
                    args={"goal": "x"},
                    session_id="parent",
                    task_id="parent",
                    delegation=_delegation(stop_used_percent=50),
                    usage=usage,
                    dispatch=dispatch,
                    repo=str(repo),
                )

        self.assertIsInstance(d, dict)
        self.assertEqual(d.get("action"), "block")
        msg = d.get("message") or ""
        self.assertIn(CRON_NO_RESUME_TIMER, msg)
        self.assertNotIn("resume scheduled at", msg)
        self.assertEqual(calls, [], "must not dispatch cronjob_manage inside cron session")
        pending = load_state().get("pending_park") or {}
        self.assertEqual(pending.get("cron_skipped"), "scheduled_run")
        self.assertIsNone(pending.get("job_id"))
        self.assertTrue((repo / "docs" / "vibe-park.md").is_file())

    def test_auto_park_schedules_when_allow_agent_scheduling_true(self):
        from stop_park import pre_tool_call_directive

        os.environ["HERMES_CRON_SESSION"] = "1"
        usage, _reset = self._seed_usage()
        repo = Path(tempfile.mkdtemp(prefix="vibe-cron-allow-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(repo, ignore_errors=True))
        calls = []

        def dispatch(tool, args):
            calls.append((tool, dict(args)))
            return '{"success": true, "job_id": "cron-allowed-1"}'

        with mock.patch("importlib.reload", side_effect=lambda m: m):
            with mock.patch("park.cron_scheduling_blocked", return_value=False):
                d = pre_tool_call_directive(
                    tool_name="delegate_task",
                    args={"goal": "x"},
                    session_id="parent",
                    task_id="parent",
                    delegation=_delegation(stop_used_percent=50),
                    usage=usage,
                    dispatch=dispatch,
                    repo=str(repo),
                )

        self.assertEqual(d.get("action"), "block")
        self.assertIn("resume scheduled at", d.get("message") or "")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "cronjob_manage")

    def test_manual_vibe_park_skips_cron_in_cron_session(self):
        from park import CRON_NO_RESUME_TIMER, park, vibe_command
        from quota import get_quota

        os.environ["HERMES_CRON_SESSION"] = "1"
        snap = make_snapshot(92.0)

        def fetch(_p):
            return snap

        get_quota(provider="openai-codex", fetch=fetch, force=True)
        repo = Path(tempfile.mkdtemp(prefix="vibe-manual-cron-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(repo, ignore_errors=True))
        (repo / "docs").mkdir()
        calls = []

        def dispatch(tool, args):
            calls.append((tool, dict(args)))
            return '{"success": true, "job_id": "nope"}'

        with mock.patch("park.cron_scheduling_blocked", return_value=True):
            out = park(repo=str(repo), note="manual", dispatch=dispatch)
            out2 = vibe_command(f"park {repo} --note manual2", dispatch=dispatch)

        self.assertIn(CRON_NO_RESUME_TIMER, out)
        self.assertIn(CRON_NO_RESUME_TIMER, out2)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    import unittest

    unittest.main()
