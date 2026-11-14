"""Executor-lane enforcement: parent writes blocked when routing says executor."""
from __future__ import annotations

import unittest
from unittest import mock

from support import IsolatedHomeTest, make_snapshot


def _delegation(**own):
    return {
        "backend": "vibe-relay",
        "backends": {
            "vibe-relay": {
                "self_remaining_threshold": 51,
                "executor_provider": "anthropic",
                "executor_model": "claude-haiku-4-5",
                **own,
            }
        },
    }


class ExecutorLaneEnforceTests(IsolatedHomeTest):
    def setUp(self):
        super().setUp()
        from executor_lane import clear_child_registry

        clear_child_registry()

    def tearDown(self):
        from executor_lane import clear_child_registry

        clear_child_registry()
        super().tearDown()

    def _usage(self, used: float):
        from quota import get_quota

        snap = make_snapshot(used)

        def fetch(_p):
            return snap

        return get_quota(provider="anthropic", fetch=fetch, force=True)

    def test_parent_patch_blocked_in_executor_mode(self):
        """RED on 0.2.3 (parent could patch) → GREEN: block directive."""
        from executor_lane import pre_tool_call_directive

        usage = self._usage(70.0)  # 30% remaining ≤ 51
        d = pre_tool_call_directive(
            tool_name="patch",
            args={"path": "textutil/slugify.py", "old_string": "a", "new_string": "b"},
            task_id="parent-session",
            session_id="parent-session",
            delegation=_delegation(policy="auto"),
            usage=usage,
        )
        self.assertIsInstance(d, dict)
        self.assertEqual(d.get("action"), "block")
        self.assertIn("vibe-relay: quota is low", d.get("message", ""))
        self.assertIn("delegate_task", d.get("message", ""))
        self.assertIn("30% left", d.get("message", ""))

    def test_parent_write_file_and_sed_blocked(self):
        from executor_lane import pre_tool_call_directive

        usage = self._usage(70.0)
        for tool, args in (
            ("write_file", {"path": "a.py", "content": "x"}),
            ("terminal", {"command": "sed -i 's/a/b/' textutil/slugify.py"}),
            ("terminal", {"command": "python -c 'print(1)' > out.txt"}),
            ("shell", {"command": "echo hi | tee notes.txt"}),
        ):
            with self.subTest(tool=tool, cmd=args.get("command")):
                d = pre_tool_call_directive(
                    tool_name=tool,
                    args=args,
                    session_id="parent",
                    task_id="parent",
                    delegation=_delegation(policy="auto"),
                    usage=usage,
                )
                self.assertEqual(d and d.get("action"), "block")

    def test_child_allowed_even_in_executor_mode(self):
        from executor_lane import note_child_start, pre_tool_call_directive

        usage = self._usage(70.0)
        note_child_start(child_session_id="child-sess", child_subagent_id="sub-1")
        d = pre_tool_call_directive(
            tool_name="patch",
            args={"path": "textutil/slugify.py"},
            session_id="child-sess",
            task_id="sub-1",
            delegation=_delegation(policy="executor-always"),
            usage=usage,
        )
        self.assertIsNone(d)

    def test_parent_lane_allows_writes(self):
        from executor_lane import pre_tool_call_directive

        usage = self._usage(20.0)  # 80% remaining → self
        d = pre_tool_call_directive(
            tool_name="patch",
            args={"path": "a.py"},
            session_id="parent",
            task_id="parent",
            delegation=_delegation(policy="auto"),
            usage=usage,
        )
        self.assertIsNone(d)

    def test_inactive_backend_allows_writes(self):
        from executor_lane import pre_tool_call_directive

        usage = self._usage(70.0)
        inactive = _delegation(policy="executor-always")
        inactive["backend"] = "default"
        d = pre_tool_call_directive(
            tool_name="patch",
            args={"path": "a.py"},
            session_id="parent",
            task_id="parent",
            delegation=inactive,
            usage=usage,
        )
        self.assertIsNone(d)

    def test_enforce_switch_off_allows_writes(self):
        from executor_lane import pre_tool_call_directive

        usage = self._usage(70.0)
        d = pre_tool_call_directive(
            tool_name="write_file",
            args={"path": "a.py", "content": "x"},
            session_id="parent",
            task_id="parent",
            delegation=_delegation(policy="executor-always", enforce_executor_lane=False),
            usage=usage,
        )
        self.assertIsNone(d)

    def test_reads_and_gate_commands_allowed(self):
        from executor_lane import pre_tool_call_directive

        usage = self._usage(70.0)
        cases = [
            ("read_file", {"path": "textutil/slugify.py"}),
            ("search_files", {"pattern": "slugify"}),
            ("terminal", {"command": "python -m unittest discover -s tests -v"}),
            ("terminal", {"command": "pytest -q"}),
            ("terminal", {"command": "git status && git diff"}),
            ("delegate_task", {"goal": "fix", "allowed": "textutil/**", "gate": "pytest -q"}),
            ("vibe_accept", {"allowed": "textutil/**"}),
        ]
        for tool, args in cases:
            with self.subTest(tool=tool):
                d = pre_tool_call_directive(
                    tool_name=tool,
                    args=args,
                    session_id="parent",
                    task_id="parent",
                    delegation=_delegation(policy="executor-always"),
                    usage=usage,
                )
                self.assertIsNone(d)

    def test_executor_always_blocks_even_with_high_remaining(self):
        from executor_lane import pre_tool_call_directive

        usage = self._usage(10.0)  # 90% remaining
        d = pre_tool_call_directive(
            tool_name="patch",
            args={"path": "a.py"},
            session_id="parent",
            task_id="parent",
            delegation=_delegation(policy="executor-always"),
            usage=usage,
        )
        self.assertEqual(d and d.get("action"), "block")

    def test_child_env_marker_allows(self):
        from executor_lane import pre_tool_call_directive

        usage = self._usage(70.0)
        with mock.patch.dict("os.environ", {"HERMES_DELEGATED_CHILD_CONTEXT": "1"}):
            d = pre_tool_call_directive(
                tool_name="patch",
                args={"path": "a.py"},
                session_id="unknown",
                task_id="unknown",
                delegation=_delegation(policy="executor-always"),
                usage=usage,
            )
        self.assertIsNone(d)

    def test_readonly_config_load_is_used_for_delegation(self):
        from executor_lane import load_delegation_config
        from hermes_cli import config

        expected = {"backend": "vibe-relay", "backends": {"vibe-relay": {"policy": "auto"}}}
        with mock.patch.object(config, "load_config_readonly", return_value={"delegation": expected}):
            self.assertEqual(load_delegation_config(), expected)


if __name__ == "__main__":
    unittest.main()
