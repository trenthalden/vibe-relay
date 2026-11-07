"""Test helpers: isolated HERMES_HOME + stub quota snapshots."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, List, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
VENDOR = ROOT / "vendor"
if str(VENDOR) not in sys.path:
    sys.path.insert(0, str(VENDOR))


@dataclass
class FakeWindow:
    label: str
    used_percent: Optional[float] = None
    reset_at: Optional[datetime] = None
    detail: Optional[str] = None
    scope: str = "account"


@dataclass
class FakeSnapshot:
    provider: str = "openai-codex"
    source: str = "test"
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


def make_snapshot(used_percent: float, *, hours_to_reset: float = 2.0, provider: str = "openai-codex") -> FakeSnapshot:
    reset = datetime.now(timezone.utc) + timedelta(hours=hours_to_reset)
    return FakeSnapshot(
        provider=provider,
        windows=(FakeWindow(label="Session", used_percent=used_percent, reset_at=reset),),
    )


class IsolatedHomeTest(unittest.TestCase):
    def setUp(self):
        self._home = Path(tempfile.mkdtemp(prefix="vibe-relay-home-"))
        self._prev_home = os.environ.get("HERMES_HOME")
        os.environ["HERMES_HOME"] = str(self._home)
        # Reset plugin state module caches by rewriting empty state
        from state import save_state
        save_state({})

    def tearDown(self):
        if self._prev_home is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = self._prev_home
        import shutil
        shutil.rmtree(self._home, ignore_errors=True)
