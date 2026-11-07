"""Park creates exactly one job with correct schedule (stubbed dispatch)."""
from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from support import IsolatedHomeTest, make_snapshot


class ParkTests(IsolatedHomeTest):
    def test_park_one_job_at_reset_plus_buffer(self):
        from park import park, schedule_iso
        from quota import get_quota
        from state import load_state

        reset = datetime.now(timezone.utc) + timedelta(hours=3)
        snap = make_snapshot(92.0)
        # Override window reset
        from support import FakeSnapshot, FakeWindow
        snap = FakeSnapshot(windows=(FakeWindow(label="Session", used_percent=92.0, reset_at=reset),))

        def fetch(_p):
            return snap

        get_quota(provider="openai-codex", fetch=fetch, force=True)

        calls = []

        def dispatch(tool, args):
            calls.append((tool, dict(args)))
            return '{"success": true, "job_id": "job-1"}'

        repo = Path(tempfile.mkdtemp(prefix="vibe-park-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(repo, ignore_errors=True))
        (repo / "docs").mkdir()

        buf = 120
        expected = schedule_iso(reset.isoformat(), buf)
        out = park(repo=str(repo), note="pause here", dispatch=dispatch, buffer_seconds=buf)
        self.assertIn("PARKED", out)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "cronjob_manage")
        self.assertEqual(calls[0][1]["action"], "create")
        self.assertEqual(calls[0][1]["schedule"], expected)
        self.assertEqual(calls[0][1].get("repeat"), 1)
        handoff = repo / "docs" / "vibe-park.md"
        self.assertTrue(handoff.is_file())
        self.assertIn("pause here", handoff.read_text(encoding="utf-8"))

        # Second park must not create another job
        out2 = park(repo=str(repo), note="again", dispatch=dispatch, buffer_seconds=buf)
        self.assertIn("SKIPPED", out2)
        self.assertEqual(len(calls), 1)
        pending = load_state().get("pending_park")
        self.assertEqual(pending.get("job_id"), "job-1")
