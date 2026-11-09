"""Stop-at-threshold: block + one auto-park cron; lifts after reset; children unaffected.

RED on 0.2.4 (should_park computed but no caller acted) → GREEN.
"""
from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from support import IsolatedHomeTest, make_snapshot


def _delegation(**own):
    return {
        "backend": "vibe-relay",
        "backends": {
            "vibe-relay": {
                "self_remaining_threshold": 51,
                "stop_used_percent": 90,
                "park_buffer_seconds": 120,
                "executor_provider": "anthropic",
                "executor_model": "claude-haiku-4-5",
                **own,
            }
        },
    }


class StopParkEnforceTests(IsolatedHomeTest):
    def setUp(self):
        super().setUp()
        from executor_lane import clear_child_registry

        clear_child_registry()

    def tearDown(self):
        from executor_lane import clear_child_registry

        clear_child_registry()
        super().tearDown()

    def _usage(self, used: float, *, hours_to_reset: float = 2.0):
        from quota import get_quota

        snap = make_snapshot(used, hours_to_reset=hours_to_reset)

        def fetch(_p):
            return snap

        return get_quota(provider="anthropic", fetch=fetch, force=True)

    def test_parent_blocked_at_and_above_stop(self):
        """RED on 0.2.4 (no caller of should_park) → GREEN: block + stop message."""
        from state import save_state
        from stop_park import pre_tool_call_directive

        for used in (90.0, 95.0):
            with self.subTest(used=used):
                save_state({})
                repo = Path(tempfile.mkdtemp(prefix="vibe-stop-"))
                self.addCleanup(lambda p=repo: __import__("shutil").rmtree(p, ignore_errors=True))
                calls = []

                def dispatch(tool, args):
                    calls.append((tool, dict(args)))
                    return '{"success": true, "job_id": "cron-stop-1"}'

                usage = self._usage(used)
                d = pre_tool_call_directive(
                    tool_name="delegate_task",
                    args={"goal": "fix", "allowed": "textutil/**", "gate": "pytest -q"},
                    session_id="parent",
                    task_id="parent",
                    delegation=_delegation(),
                    usage=usage,
                    dispatch=dispatch,
                    repo=str(repo),
                )
                self.assertIsInstance(d, dict)
                self.assertEqual(d.get("action"), "block")
                msg = d.get("message") or ""
                self.assertIn("vibe-relay: quota used", msg)
                self.assertIn("≥ stop 90%", msg)
                self.assertIn("work parked", msg)
                self.assertIn("resume scheduled at", msg)
                self.assertEqual(len(calls), 1)

    def test_one_cron_on_repeated_blocks(self):
        from state import load_state
        from stop_park import pre_tool_call_directive

        repo = Path(tempfile.mkdtemp(prefix="vibe-stop-once-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(repo, ignore_errors=True))
        calls = []

        def dispatch(tool, args):
            calls.append((tool, dict(args)))
            return f'{{"success": true, "job_id": "job-{len(calls)}"}}'

        usage = self._usage(92.0)
        for i in range(3):
            d = pre_tool_call_directive(
                tool_name="patch" if i % 2 == 0 else "delegate_task",
                args={"path": "a.py"} if i % 2 == 0 else {"goal": "x"},
                session_id="parent",
                task_id="parent",
                delegation=_delegation(),
                usage=usage,
                dispatch=dispatch,
                repo=str(repo),
            )
            self.assertEqual(d and d.get("action"), "block")

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "cronjob_manage")
        self.assertEqual(calls[0][1].get("repeat"), 1)
        pending = load_state().get("pending_park") or {}
        self.assertTrue(pending.get("auto"))
        self.assertEqual(pending.get("job_id"), "job-1")
        self.assertTrue((repo / "docs" / "vibe-park.md").is_file())

    def test_lifts_after_reset_or_below_stop(self):
        from stop_park import pre_tool_call_directive

        # Below stop → allow
        usage_low = self._usage(80.0)
        d = pre_tool_call_directive(
            tool_name="delegate_task",
            args={"goal": "x"},
            session_id="parent",
            task_id="parent",
            delegation=_delegation(stop_used_percent=90),
            usage=usage_low,
            repo=".",
        )
        self.assertIsNone(d)

        # Wall-clock past reset_at → lift even if used% still looks high
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        usage_past = {
            "provider": "anthropic",
            "available": True,
            "windows": [
                {"label": "Session", "used_percent": 95.0, "reset_at": past, "scope": "account"}
            ],
            "exhausted_until": past,
        }
        d2 = pre_tool_call_directive(
            tool_name="delegate_task",
            args={"goal": "x"},
            session_id="parent",
            task_id="parent",
            delegation=_delegation(),
            usage=usage_past,
            repo=".",
        )
        self.assertIsNone(d2)

    def test_children_unaffected(self):
        from executor_lane import note_child_start
        from stop_park import pre_tool_call_directive

        usage = self._usage(95.0)
        note_child_start(child_session_id="child-1", child_subagent_id="sub-1")
        d = pre_tool_call_directive(
            tool_name="delegate_task",
            args={"goal": "nested"},
            session_id="child-1",
            task_id="sub-1",
            delegation=_delegation(),
            usage=usage,
            repo=".",
        )
        self.assertIsNone(d)

        with mock.patch.dict("os.environ", {"HERMES_DELEGATED_CHILD_CONTEXT": "1"}):
            d2 = pre_tool_call_directive(
                tool_name="patch",
                args={"path": "a.py"},
                session_id="unknown",
                task_id="unknown",
                delegation=_delegation(),
                usage=usage,
                repo=".",
            )
        self.assertIsNone(d2)

    def test_inactive_backend_unaffected(self):
        from stop_park import pre_tool_call_directive

        usage = self._usage(95.0)
        inactive = _delegation()
        inactive["backend"] = "default"
        d = pre_tool_call_directive(
            tool_name="delegate_task",
            args={"goal": "x"},
            session_id="parent",
            task_id="parent",
            delegation=inactive,
            usage=usage,
            repo=".",
        )
        self.assertIsNone(d)

    def test_reads_allowed_while_parked(self):
        from stop_park import pre_tool_call_directive

        usage = self._usage(95.0)
        for tool, args in (
            ("read_file", {"path": "a.py"}),
            ("search_files", {"pattern": "x"}),
            ("terminal", {"command": "python -m unittest discover -s tests -v"}),
            ("terminal", {"command": "python .devframework/check.py finish"}),
            ("vibe_accept", {"allowed": "textutil/**"}),
        ):
            with self.subTest(tool=tool):
                d = pre_tool_call_directive(
                    tool_name=tool,
                    args=args,
                    session_id="parent",
                    task_id="parent",
                    delegation=_delegation(),
                    usage=usage,
                    repo=".",
                )
                self.assertIsNone(d)

    def test_context_states_parked(self):
        from stop_park import parked_context_lines

        usage = self._usage(93.0)
        lines = parked_context_lines(
            usage=usage,
            settings=_delegation()["backends"]["vibe-relay"],
        )
        blob = "\n".join(lines)
        self.assertIn("parked until", blob)
        self.assertIn("do not start new work", blob)

    def test_write_file_and_delegate_blocked_together(self):
        from stop_park import pre_tool_call_directive

        repo = Path(tempfile.mkdtemp(prefix="vibe-stop-w-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(repo, ignore_errors=True))
        calls = []

        def dispatch(tool, args):
            calls.append(tool)
            return '{"success": true, "job_id": "j1"}'

        usage = self._usage(91.0)
        for tool, args in (
            ("write_file", {"path": "a.py", "content": "x"}),
            ("terminal", {"command": "sed -i 's/a/b/' x.py"}),
            ("delegate_task", {"goal": "fix"}),
        ):
            d = pre_tool_call_directive(
                tool_name=tool,
                args=args,
                session_id="parent",
                task_id="parent",
                delegation=_delegation(),
                usage=usage,
                dispatch=dispatch,
                repo=str(repo),
            )
            self.assertEqual(d and d.get("action"), "block")
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    import unittest

    unittest.main()
