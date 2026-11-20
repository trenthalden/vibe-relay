"""Gate journal — one JSON line per decision; rotate at configured size (DB-8)."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional


def append_journal(
    path: str,
    record: Mapping[str, Any],
    *,
    rotate_bytes: int,
    ts: Optional[str] = None,
) -> None:
    """Append one sorted-key JSON line. ``ts`` is UTC ISO-8601 (only time in the record).

    Journal failures must be raised to the caller; they must not alter the gate decision.
    """
    payload = dict(record)
    if ts is None:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload["ts"] = ts
    line = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    line_bytes = (line + "\n").encode("utf-8")

    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)

    # cost: one append per gate run (≤ 5,000/day); rotate at most once when size exceeds cap.
    if os.path.isfile(path) and os.path.getsize(path) + len(line_bytes) > rotate_bytes:
        _rotate(path)

    with open(path, "ab") as fh:
        fh.write(line_bytes)


def _rotate(path: str) -> None:
    rotated = path + ".1"
    if os.path.isfile(rotated):
        os.remove(rotated)
    os.replace(path, rotated)
