"""Night-shift: plan files, hooks, arm wakes, report, /night command."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from support import FakeSnapshot, FakeWindow, IsolatedHomeTest
from tool_args import wrap_tool_handler


def _git_init(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=str(path), check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "night-test@example.com"],
        cwd=str(path),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Night Test"],
        cwd=str(path),
        check=True,
        capture_output=True,
    )
    (path / "README.md").write_text("night test repo\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=str(path), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=str(path),
        check=True,
        capture_output=True,
    )
    return path.resolve()


def _usage_snap(
    *,
    session_used: float = 20.0,
    weekly_used: float = 30.0,
    hours_to_session_reset: float = 2.0,
    provider: str = "anthropic",
    include_weekly: bool = True,
    session_reset_at: datetime | None = None,
):
    reset = session_reset_at or (
        datetime.now(timezone.utc) + timedelta(hours=hours_to_session_reset)
    )
    windows = [FakeWindow(label="Session", used_percent=session_used, reset_at=reset)]
    if include_weekly:
        week_reset = datetime.now(timezone.utc) + timedelta(days=3)
        windows.append(
            FakeWindow(label="Weekly", used_percent=weekly_used, reset_at=week_reset)
        )
    return FakeSnapshot(provider=provider, windows=tuple(windows))


def _cron_dispatch(jobs: dict, calls: list):
    def dispatch(tool, args):
        assert tool == "cronjob_manage"
        action = args.get("action")
        if action == "list":
            return json.dumps({"jobs": [{"job_id": j} for j in jobs]})
        if action == "create":
            calls.append(dict(args))
            jid = f"job-{len(calls)}"
            jobs[jid] = args
            return json.dumps({"success": True, "job_id": jid})
        if action == "remove":
            jobs.pop(args.get("job_id"), None)
            return json.dumps({"success": True})
        return "{}"

    return dispatch


class NightShiftTests(IsolatedHomeTest):
    def setUp(self):
        super().setUp()
        self.repo = _git_init(Path(tempfile.mkdtemp(prefix="vibe-night-repo-")))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.repo, ignore_errors=True))
        self.clock = datetime(2026, 11, 14, 22, 0, 0).astimezone()
        self.cwd = lambda: str(self.repo)
        self.provider = lambda: ("anthropic", "claude-sonnet-4")

    def _start(self, **kwargs):
        from night import start_night

        args = {
            "hard_stop": "08:00",
            "reserve_percent": 10,
            "stop_margin_minutes": 30,
            "parent_active_minutes": 40,
            "title": "demo",
            "owner_instructions": "PRIVATE_INSTR_SENTINEL keep going",
            "lanes": [
                {"name": "c2", "worker": "cursor", "owns": "night.py"},
            ],
            "quota_provider": "anthropic",
            "now": self.clock,
            "cwd_resolver": self.cwd,
            "provider_reader": self.provider,
        }
        args.update(kwargs)
        return start_night(**args)

    def test_registered_subagent_stop_hook_does_not_persist_child_summary(self):
        from state import load_state
        import __init__ as plugin

        class Context:
            def __init__(self):
                self.hooks = {}
            def register_hook(self, name, callback):
                self.hooks[name] = callback
            def __getattr__(self, _name):
                return lambda *args, **kwargs: None

        ctx = Context()
        plugin.register(ctx)
        ctx.hooks["subagent_stop"](
            child_session_id="child-1",
            child_status="completed",
            child_summary="PRIVATE_CHILD_SUMMARY",
        )
        stored = json.dumps(load_state())
        self.assertNotIn("PRIVATE_CHILD_SUMMARY", stored)
        self.assertEqual(load_state()["last_subagent"], {
            "child_session_id": "child-1", "child_status": "completed"
        })

    def test_queue_changes_are_timeline_events_without_portion_text(self):
        from night import load_night_json, log_night

        out = self._start()
        night_dir = Path(out["dir"])
        for status in ("queued", "dispatched", "accepted"):
            log_night(
                kind="queue", id="q1", lane="c2",
                portion="PRIVATE_PORTION_TIMELINE", status=status, now=self.clock,
            )
        plan = load_night_json(night_dir)
        events = plan["events"]
        self.assertEqual(len(events), 3)
        self.assertEqual([e["at"] for e in events], [self.clock.isoformat(timespec="seconds")] * 3)
        self.assertTrue(all("q1" in e["text"] for e in events))
        self.assertNotIn("PRIVATE_PORTION_TIMELINE", json.dumps(events))
        rendered = (night_dir / "NIGHT-PLAN.md").read_text(encoding="utf-8")
        timeline = rendered.split("## Timeline", 1)[1].split("## Wakes", 1)[0]
        self.assertEqual(timeline.count("q1"), 3)
        self.assertNotIn("PRIVATE_PORTION_TIMELINE", timeline)

    def test_start_layout_and_self_ignore(self):
        from night import start_night
        from state import load_state

        out = self._start()
        night_dir = Path(out["dir"])
        self.assertTrue((night_dir / "night.json").is_file())
        self.assertTrue((night_dir / "NIGHT-PLAN.md").is_file())
        self.assertTrue((night_dir / "COMMON.md").is_file())
        gitignore = self.repo / ".vibe-relay" / ".gitignore"
        self.assertEqual(gitignore.read_text(encoding="utf-8").strip(), "*")
        porcelain = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(self.repo),
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(porcelain.stdout.strip(), "")
        meta = load_state().get("night") or {}
        self.assertTrue(meta.get("active"))
        self.assertEqual(meta.get("id"), out["id"])
        blob = json.dumps(meta)
        self.assertNotIn("PRIVATE_INSTR_SENTINEL", blob)

    def test_start_refusals(self):
        from night import start_night, validate_user_id

        # TMPDIR may sit inside this worktree's git root — use an absolute non-git dir.
        nogit = Path(tempfile.mkdtemp(prefix="vibe-night-nogit-", dir="/tmp"))
        self.addCleanup(lambda: __import__("shutil").rmtree(nogit, ignore_errors=True))
        with self.assertRaises(ValueError) as ctx:
            start_night(
                hard_stop="08:00",
                now=self.clock,
                cwd_resolver=lambda: str(nogit),
                provider_reader=self.provider,
            )
        self.assertIn("git", str(ctx.exception).lower())

        with self.assertRaises(ValueError):
            validate_user_id("..", label="night id")
        with self.assertRaises(ValueError):
            validate_user_id("a/b", label="night id")

        # Hermes-home root refusal
        home = Path(os.environ["HERMES_HOME"]).resolve()
        home_repo = _git_init(home / "fake-project")
        with mock.patch("night.hermes_home_root", return_value=home):
            with self.assertRaises(ValueError) as ctx2:
                start_night(
                    hard_stop="08:00",
                    now=self.clock,
                    cwd_resolver=lambda: str(home_repo),
                    provider_reader=self.provider,
                )
            self.assertIn("Hermes home", str(ctx2.exception))

        self._start()
        with self.assertRaises(ValueError) as ctx3:
            self._start()
        self.assertIn("active night", str(ctx3.exception).lower())

    def test_log_render_and_state_privacy(self):
        from night import log_night, load_night_json
        from state import load_state

        out = self._start()
        night_dir = Path(out["dir"])
        log_night(
            kind="decision",
            choice="take lane c2",
            doubt="PRIVATE_DECISION_SENTINEL",
            why_reversible="can reverse",
            now=self.clock + timedelta(minutes=1),
        )
        log_night(
            kind="queue",
            id="q1",
            lane="c2",
            portion="PRIVATE_PORTION_SENTINEL implement night",
            status="queued",
            evidence="none yet",
            now=self.clock + timedelta(minutes=2),
        )
        log_night(
            kind="event",
            text="worker started",
            now=self.clock + timedelta(minutes=3),
        )
        plan = load_night_json(night_dir)
        rendered = (night_dir / "NIGHT-PLAN.md").read_text(encoding="utf-8")
        self.assertIn("PRIVATE_PORTION_SENTINEL", rendered)
        self.assertIn("PRIVATE_DECISION_SENTINEL", rendered)
        self.assertEqual(plan["queue"][0]["status"], "queued")
        meta = json.dumps(load_state().get("night") or {})
        self.assertNotIn("PRIVATE_INSTR_SENTINEL", meta)
        self.assertNotIn("PRIVATE_PORTION_SENTINEL", meta)
        self.assertNotIn("PRIVATE_DECISION_SENTINEL", meta)

        log_night(
            kind="queue",
            id="q1",
            status="dispatched",
            evidence="running",
            now=self.clock + timedelta(minutes=4),
        )
        plan2 = load_night_json(night_dir)
        self.assertEqual(plan2["queue"][0]["status"], "dispatched")
        self.assertIn("PRIVATE_PORTION_SENTINEL", plan2["queue"][0]["portion"])

    def test_hard_stop_and_reserve_blocks(self):
        from night import pre_tool_call_directive
        from quota import get_quota

        out = self._start(stop_margin_minutes=30)
        # hard_stop next 08:00 from 22:00 → 08:00 next day; dispatch_stop = 07:30
        before = self.clock + timedelta(hours=1)  # 23:00 — still open
        d = pre_tool_call_directive(
            tool_name="delegate_task",
            args={"goal": "x"},
            now=before,
            usage={"windows": [{"label": "Session", "used_percent": 20.0}]},
        )
        self.assertIsNone(d)
        self.assertIsNone(
            pre_tool_call_directive(
                tool_name="vibe_night_status",
                args={},
                now=before + timedelta(hours=12),
            )
        )
        self.assertIsNone(
            pre_tool_call_directive(
                tool_name="read_file",
                args={"path": "a.py"},
                now=before + timedelta(hours=12),
            )
        )

        after = datetime(2026, 11, 15, 7, 35, 0).astimezone()
        blocked = pre_tool_call_directive(
            tool_name="delegate_task",
            args={"goal": "x"},
            now=after,
            usage={"windows": [{"label": "Session", "used_percent": 20.0}]},
        )
        self.assertIsInstance(blocked, dict)
        self.assertEqual(blocked.get("action"), "block")
        self.assertIn("dispatch window closed", blocked.get("message", ""))
        self.assertIn("not stopped", blocked.get("message", ""))

        # Reserve block while window still open
        snap = _usage_snap(session_used=95.0, weekly_used=40.0)
        get_quota(provider="anthropic", fetch=lambda _p: snap, force=True)
        mid = self.clock + timedelta(hours=1)
        reserved = pre_tool_call_directive(
            tool_name="delegate_task",
            args={"goal": "y"},
            now=mid,
            fetch_usage=lambda _p: snap,
        )
        self.assertEqual(reserved and reserved.get("action"), "block")
        self.assertIn("reserve", (reserved or {}).get("message", ""))

        # No night → no block
        from state import update_state

        update_state(night={"active": False})
        self.assertIsNone(
            pre_tool_call_directive(
                tool_name="delegate_task",
                args={"goal": "z"},
                now=after,
                usage={"windows": [{"label": "Session", "used_percent": 99.0}]},
            )
        )
        _ = out

    def test_arm_refusals_and_success(self):
        from night import arm_night
        from quota import get_quota
        from state import load_state

        self._start()
        snap = _usage_snap(session_used=20.0, weekly_used=30.0, hours_to_session_reset=3.0)
        get_quota(provider="anthropic", fetch=lambda _p: snap, force=True)

        # Cron refusal
        with mock.patch("night.in_cron_session", return_value=True):
            with self.assertRaises(ValueError) as ctx:
                arm_night(
                    dispatch=lambda t, a: "{}",
                    fetch_usage=lambda _p: snap,
                    provider_reader=self.provider,
                    now=self.clock,
                )
            self.assertIn("cron", str(ctx.exception).lower())

        # Unknown quota (clear cache so quota.py cannot prefer a prior good snapshot)
        from state import save_state

        save_state({})
        self._start()
        empty = FakeSnapshot(unavailable_reason="no auth", windows=())
        with self.assertRaises(ValueError) as ctx2:
            arm_night(
                dispatch=lambda t, a: "{}",
                fetch_usage=lambda _p: empty,
                provider_reader=self.provider,
                now=self.clock,
            )
        self.assertIn("unknown", str(ctx2.exception).lower())

        # Weekly ≤ reserve
        low = _usage_snap(weekly_used=95.0)
        with self.assertRaises(ValueError) as ctx3:
            arm_night(
                dispatch=lambda t, a: "{}",
                fetch_usage=lambda _p: low,
                provider_reader=self.provider,
                now=self.clock,
            )
        self.assertIn("weekly", str(ctx3.exception).lower())

        # Provider mismatch
        with self.assertRaises(ValueError) as ctx4:
            arm_night(
                dispatch=lambda t, a: "{}",
                fetch_usage=lambda _p: snap,
                provider_reader=lambda: ("openai-codex", "gpt-5"),
                now=self.clock,
            )
        self.assertIn("hermes cron edit", str(ctx4.exception))
        self.assertIn("allow_provider_mismatch", str(ctx4.exception))

        calls = []
        jobs = {}
        dispatch = _cron_dispatch(jobs, calls)

        # Session reset ~3h out; checkpoint at +90m — all three defaults spaced.
        result = arm_night(
            dispatch=dispatch,
            fetch_usage=lambda _p: snap,
            provider_reader=self.provider,
            now=self.clock,
        )
        self.assertTrue(result.get("ok"))
        self.assertEqual(len(calls), 3)
        names = [str(c.get("name", "")) for c in calls]
        self.assertTrue(any("checkpoint" in n for n in names))
        self.assertTrue(any("quota_reset" in n for n in names))
        self.assertTrue(any("report" in n for n in names))
        for c in calls:
            self.assertEqual(c.get("repeat"), 1)
            self.assertEqual(c.get("skills"), ["vibe-relay:night-shift"])
            self.assertEqual(c.get("workdir"), str(self.repo))
            self.assertNotIn("model", c)
            self.assertNotIn("provider", c)
            self.assertNotIn("deliver", c)
            self.assertNotIn("pinned", c)
            self.assertTrue(str(c.get("name", "")).startswith("vibe-night "))

        # Second arm → no duplicate creates (3 boundaries kept)
        n_calls = len(calls)
        result2 = arm_night(
            dispatch=dispatch,
            fetch_usage=lambda _p: snap,
            provider_reader=self.provider,
            now=self.clock,
        )
        self.assertEqual(len(calls), n_calls)
        self.assertEqual(len(result2["jobs"]), 3)
        self.assertTrue(all(j.get("kept") or j.get("verified") for j in result2["jobs"]))

        # quota_reset after dispatch_stop skipped
        from night import start_night
        from state import update_state

        update_state(night=None)
        late_clock = datetime(2026, 11, 15, 7, 0, 0).astimezone()
        # hard_stop 08:00 same day; dispatch_stop 07:30; session reset after that
        start_night(
            hard_stop="08:00",
            stop_margin_minutes=30,
            reserve_percent=10,
            quota_provider="anthropic",
            now=late_clock,
            cwd_resolver=self.cwd,
            provider_reader=self.provider,
            replace=True,
        )
        late_reset = late_clock + timedelta(hours=2)  # 09:00 > dispatch_stop 07:30
        late_snap = FakeSnapshot(
            provider="anthropic",
            windows=(
                FakeWindow(label="Session", used_percent=20.0, reset_at=late_reset),
                FakeWindow(
                    label="Weekly",
                    used_percent=30.0,
                    reset_at=late_clock + timedelta(days=3),
                ),
            ),
        )
        calls.clear()
        jobs.clear()
        out = arm_night(
            boundaries=["quota_reset", "report"],
            dispatch=dispatch,
            fetch_usage=lambda _p: late_snap,
            provider_reader=self.provider,
            now=late_clock,
        )
        reset_creates = [c for c in calls if "quota_reset" in str(c.get("name"))]
        self.assertEqual(reset_creates, [])
        self.assertTrue(any("dispatch_stop" in s.get("reason", "") for s in out.get("skipped") or []))
        report_creates = [c for c in calls if "report" in str(c.get("name"))]
        self.assertEqual(len(report_creates), 1)

        meta = load_state().get("night") or {}
        self.assertTrue(meta.get("jobs"))

    def test_post_tool_call_stamp_only_orchestrator_tools_refresh_in_cron(self):
        from night import post_tool_call_stamp
        from state import load_state

        self._start()
        initial = load_state()["night"]["last_parent_update"]
        later = self.clock + timedelta(minutes=2)
        with mock.patch("night._in_cron_guard", return_value=True):
            for tool_name in ("skill_view", "read_file"):
                post_tool_call_stamp(tool_name=tool_name, now=later)
                self.assertEqual(load_state()["night"]["last_parent_update"], initial)
            post_tool_call_stamp(tool_name="vibe_night_log", now=later)
        stamped = load_state()["night"]
        self.assertEqual(stamped["last_parent_update"], later.isoformat(timespec="seconds"))
        self.assertEqual(stamped["last_parent_source"], "cron")

        outside = later + timedelta(minutes=2)
        with mock.patch("night._in_cron_guard", return_value=False):
            post_tool_call_stamp(tool_name="read_file", now=outside)
        stamped = load_state()["night"]
        self.assertEqual(stamped["last_parent_update"], outside.isoformat(timespec="seconds"))
        self.assertEqual(stamped["last_parent_source"], "session")

    def test_status_parent_active_and_no_stamp_refresh(self):
        from night import post_tool_call_stamp, status_night
        from state import load_state

        self._start()
        # Fresh parent stamp from start
        st = status_night(now=self.clock + timedelta(minutes=5))
        self.assertTrue(st["parent_active"])
        stamp_before = load_state()["night"]["last_parent_update"]
        # status must not refresh
        status_night(now=self.clock + timedelta(minutes=10))
        self.assertEqual(load_state()["night"]["last_parent_update"], stamp_before)

        # Stale parent
        st2 = status_night(now=self.clock + timedelta(minutes=50))
        self.assertFalse(st2["parent_active"])

        # post_tool_call refreshes (throttle)
        post_tool_call_stamp(tool_name="delegate_task", now=self.clock + timedelta(minutes=51))
        self.assertNotEqual(load_state()["night"]["last_parent_update"], stamp_before)
        mid = load_state()["night"]["last_parent_update"]
        post_tool_call_stamp(tool_name="patch", now=self.clock + timedelta(minutes=51, seconds=30))
        self.assertEqual(load_state()["night"]["last_parent_update"], mid)
        # vibe_night_status does not stamp
        post_tool_call_stamp(
            tool_name="vibe_night_status",
            now=self.clock + timedelta(minutes=55),
        )
        self.assertEqual(load_state()["night"]["last_parent_update"], mid)

    def test_report_shows_each_wakes_observed_outcome(self):
        from night import load_night_json, report_night, save_night_plan

        out = self._start()
        night_dir = Path(out["dir"])
        plan = load_night_json(night_dir)
        plan["jobs"] = [
            {"boundary": "checkpoint", "job_id": "ran-ok", "run_at": "2026-11-14T23:30:00+02:00"},
            {"boundary": "quota_reset", "job_id": "ran-error", "run_at": "2026-11-15T01:00:00+02:00"},
            {"boundary": "report", "job_id": "scheduled", "run_at": "2026-11-15T07:30:00+02:00"},
            {"boundary": "missing", "job_id": "gone", "run_at": "2026-11-15T02:00:00+02:00"},
        ]
        save_night_plan(night_dir, plan)
        live_jobs = [
            {"job_id": "ran-ok", "next_run_at": None, "last_run_at": "2026-11-14T23:30:05+02:00", "last_status": "ok", "last_error": None, "state": "completed", "enabled": False},
            {"job_id": "ran-error", "next_run_at": None, "last_run_at": "2026-11-15T01:00:03+02:00", "last_status": "error", "last_error": "deadline exceeded while checking", "state": "completed", "enabled": False},
            {"job_id": "scheduled", "next_run_at": "2026-11-15T07:30:00+02:00", "last_run_at": None, "last_status": None, "last_error": None, "state": "scheduled", "enabled": True},
        ]
        dispatch_calls = []

        def dispatch(tool, args):
            self.assertEqual(tool, "cronjob_manage")
            self.assertEqual(args, {"action": "list"})
            dispatch_calls.append(args)
            return json.dumps({"jobs": live_jobs})

        result = report_night(
            now=self.clock + timedelta(hours=1),
            fetch_usage=lambda _p: _usage_snap(),
            dispatch=dispatch,
        )
        body = Path(result["path"]).read_text(encoding="utf-8")
        self.assertIn(
            "- checkpoint: job_id=ran-ok planned=2026-11-14T23:30:00+02:00 — "
            "ran 2026-11-14T23:30:05+02:00 (ok)",
            body,
        )
        self.assertIn(
            "- quota_reset: job_id=ran-error planned=2026-11-15T01:00:00+02:00 — "
            "ran 2026-11-15T01:00:03+02:00 (error); error: deadline exceeded while checking",
            body,
        )
        self.assertIn(
            "- report: job_id=scheduled planned=2026-11-15T07:30:00+02:00 — "
            "scheduled, next 2026-11-15T07:30:00+02:00",
            body,
        )
        self.assertIn(
            "- missing: job_id=gone planned=2026-11-15T02:00:00+02:00 — "
            "not observed (job no longer listed)",
            body,
        )
        self.assertEqual(len(dispatch_calls), 1)

        for unavailable_dispatch in (
            lambda _tool, _args: "not-json",
            lambda _tool, _args: (_ for _ in ()).throw(RuntimeError("cron unavailable")),
        ):
            unavailable_result = report_night(
                now=self.clock + timedelta(hours=1),
                fetch_usage=lambda _p: _usage_snap(),
                dispatch=unavailable_dispatch,
            )
            unavailable_body = Path(unavailable_result["path"]).read_text(encoding="utf-8")
            self.assertEqual(
                unavailable_body.count("not observed (cron list unavailable)"), 4
            )

    def test_report_not_done_section(self):
        from night import log_night, report_night
        from state import update_state

        out = self._start()
        log_night(
            kind="queue",
            id="a1",
            lane="c2",
            portion="done work",
            status="accepted",
            evidence="diff ok",
            now=self.clock,
        )
        log_night(
            kind="queue",
            id="a2",
            lane="c2",
            portion="still open",
            status="queued",
            evidence="",
            now=self.clock,
        )
        log_night(
            kind="queue",
            id="a3",
            lane="c2",
            portion="blocked",
            status="blocked",
            evidence="waiting",
            now=self.clock,
        )
        update_state(last_gate={"passed": True, "tree_hash": "abc123", "command": "pytest"})
        snap = _usage_snap()
        result = report_night(
            now=self.clock + timedelta(hours=1),
            fetch_usage=lambda _p: snap,
            dispatch=lambda t, a: json.dumps({"jobs": []}),
        )
        body = Path(result["path"]).read_text(encoding="utf-8")
        self.assertIn("Not done / not verified", body)
        self.assertIn("a2", body)
        self.assertIn("a3", body)
        # accepted item should not appear under not-done list as the only focus —
        # it may appear in the queue table; ensure not-done section lists a2/a3
        not_done = body.split("## Not done / not verified", 1)[1]
        self.assertIn("a2", not_done)
        self.assertIn("a3", not_done)
        self.assertNotIn("a1", not_done.split("## Quota", 1)[0])
        self.assertIn("passed=True", body)
        self.assertIn("abc123", body)
        self.assertIn(out["id"], body)

    def test_night_stop_removes_jobs(self):
        from night import arm_night, night_command
        from quota import get_quota

        self._start()
        snap = _usage_snap(hours_to_session_reset=3.0)
        get_quota(provider="anthropic", fetch=lambda _p: snap, force=True)
        jobs = {}
        calls = []
        dispatch = _cron_dispatch(jobs, calls)

        arm_night(
            dispatch=dispatch,
            fetch_usage=lambda _p: snap,
            provider_reader=self.provider,
            now=self.clock,
        )
        self.assertEqual(len(jobs), 3)
        out = night_command(
            "stop",
            dispatch=dispatch,
            fetch_usage=lambda _p: snap,
            now=self.clock + timedelta(hours=1),
        )
        data = json.loads(out)
        self.assertTrue(data.get("ok"))
        self.assertEqual(jobs, {})
        self.assertEqual(len(data.get("removed_jobs") or []), 3)

    def test_arm_session_only_and_checkpoint_skips(self):
        """C2c: optional weekly; checkpoint defaults/skips; session-reserve skip."""
        from night import arm_night, start_night
        from quota import get_quota
        from state import update_state

        self._start()
        session_only = _usage_snap(
            session_used=20.0,
            hours_to_session_reset=3.0,
            include_weekly=False,
        )
        get_quota(provider="anthropic", fetch=lambda _p: session_only, force=True)
        calls = []
        jobs = {}
        dispatch = _cron_dispatch(jobs, calls)

        # Arm succeeds with only a session window → jobs created
        out = arm_night(
            dispatch=dispatch,
            fetch_usage=lambda _p: session_only,
            provider_reader=self.provider,
            now=self.clock,
        )
        self.assertTrue(out.get("ok"))
        self.assertGreaterEqual(len(out.get("jobs") or []), 1)
        self.assertEqual(len(calls), 3)

        # Session remaining ≤ reserve: pre-reset wakes skipped; quota_reset (at reset) kept.
        # report_lead=120 → report 06:00; session reset 06:30 → both checkpoint+report before reset;
        # quota_reset ~06:33 still before dispatch_stop 07:30.
        update_state(night=None)
        self._start(replace=True)
        reset_at = self.clock.astimezone(timezone.utc) + timedelta(hours=8, minutes=30)
        low_session = _usage_snap(
            session_used=95.0,
            weekly_used=30.0,
            session_reset_at=reset_at,
        )
        calls.clear()
        jobs.clear()
        out2 = arm_night(
            report_lead_minutes=120,
            dispatch=dispatch,
            fetch_usage=lambda _p: low_session,
            provider_reader=self.provider,
            now=self.clock,
        )
        self.assertTrue(out2.get("ok"))
        skipped = out2.get("skipped") or []
        skip_boundaries = {s.get("boundary") for s in skipped}
        self.assertIn("checkpoint", skip_boundaries)
        self.assertIn("report", skip_boundaries)
        self.assertTrue(
            any("reserve" in (s.get("reason") or "") for s in skipped if s.get("boundary") == "checkpoint")
        )
        created = [c.get("name", "") for c in calls]
        self.assertTrue(any("quota_reset" in n for n in created))
        self.assertFalse(any("checkpoint" in n for n in created))
        self.assertFalse(any("report" in n for n in created))

        # Checkpoint within 30 min of quota_reset → skipped; others created
        update_state(night=None)
        self._start(replace=True)
        # started 22:00; checkpoint default +90m = 23:30; put session reset ~23:27 (+ buffer 3 → 23:30)
        near_reset = (self.clock + timedelta(minutes=87)).astimezone(timezone.utc)
        near_snap = _usage_snap(
            session_used=20.0,
            weekly_used=30.0,
            session_reset_at=near_reset,
        )
        calls.clear()
        jobs.clear()
        out3 = arm_night(
            dispatch=dispatch,
            fetch_usage=lambda _p: near_snap,
            provider_reader=self.provider,
            now=self.clock,
        )
        self.assertTrue(
            any(
                s.get("boundary") == "checkpoint" and "quota_reset" in (s.get("reason") or "")
                for s in (out3.get("skipped") or [])
            )
        )
        names3 = [c.get("name", "") for c in calls]
        self.assertFalse(any("checkpoint" in n for n in names3))
        self.assertTrue(any("quota_reset" in n for n in names3))
        self.assertTrue(any("report" in n for n in names3))

        # Checkpoint already past (< now + 5 min)
        update_state(night=None)
        self._start(replace=True)
        calls.clear()
        jobs.clear()
        past_snap = _usage_snap(hours_to_session_reset=5.0)
        out4 = arm_night(
            checkpoint_after_minutes=0,
            dispatch=dispatch,
            fetch_usage=lambda _p: past_snap,
            provider_reader=self.provider,
            now=self.clock,
        )
        self.assertTrue(
            any(
                s.get("boundary") == "checkpoint" and "past" in (s.get("reason") or "")
                for s in (out4.get("skipped") or [])
            )
        )

        # Checkpoint at/after dispatch_stop
        update_state(night=None)
        late = datetime(2026, 11, 15, 6, 0, 0).astimezone()
        start_night(
            hard_stop="08:00",
            stop_margin_minutes=30,
            reserve_percent=10,
            quota_provider="anthropic",
            now=late,
            cwd_resolver=self.cwd,
            provider_reader=self.provider,
            replace=True,
        )
        # dispatch_stop 07:30; checkpoint +90m from 06:00 = 07:30 → at dispatch_stop
        late_snap = _usage_snap(
            hours_to_session_reset=0.5,
            session_reset_at=(late + timedelta(minutes=20)).astimezone(timezone.utc),
        )
        calls.clear()
        jobs.clear()
        out5 = arm_night(
            checkpoint_after_minutes=90,
            dispatch=dispatch,
            fetch_usage=lambda _p: late_snap,
            provider_reader=self.provider,
            now=late,
        )
        self.assertTrue(
            any(
                s.get("boundary") == "checkpoint"
                and "dispatch_stop" in (s.get("reason") or "")
                for s in (out5.get("skipped") or [])
            )
        )

    def test_handlers_calling_conventions(self):
        from night import vibe_night_start, vibe_night_status
        from tool_args import wrap_tool_handler

        handler = wrap_tool_handler(vibe_night_start)
        raw = handler(
            {
                "hard_stop": "09:00",
                "title": "via dict",
                "quota_provider": "anthropic",
                "replace": True,
            },
            task_id="x",
            cwd_resolver=self.cwd,
            provider_reader=self.provider,
            now=self.clock,
        )
        self.assertTrue(json.loads(raw).get("ok"))

        handler2 = wrap_tool_handler(vibe_night_status)
        out = handler2('{"unused": true}', task_id="x", now=self.clock)
        data = json.loads(out)
        self.assertTrue(data.get("active"))

    def test_continuation_prompt_report_always_reports_before_exit(self):
        from night import continuation_prompt

        report = continuation_prompt(night_id="n1", repo_root="/repo", boundary="report")
        checkpoint = continuation_prompt(night_id="n1", repo_root="/repo", boundary="checkpoint")

        self.assertIn("vibe_night_report", report)
        self.assertNotIn("parent active — exiting", report)
        self.assertIn("parent active — exiting", checkpoint)


if __name__ == "__main__":
    import unittest

    unittest.main()
