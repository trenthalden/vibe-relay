"""Dispatch ledger — append-only JSONL with fsync (DB-5, FR-017).

Events: ``dispatched``, ``ended``, ``reconciled``. No secrets in records (SEC-2).
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Iterator, List, Mapping, Optional


def ledger_path(data_dir: str) -> str:
    return os.path.join(data_dir, "ledger.jsonl")


def sequence_number(path: str) -> int:
    """Number of existing lines — used as the dispatch_id sequence input."""
    if not os.path.isfile(path):
        return 0
    n = 0
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                n += 1
    return n


def append_event(path: str, event: Mapping[str, Any]) -> None:
    """Append one sorted-key JSON line and fsync (DB-5)."""
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    line = json.dumps(dict(event), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload = (line + "\n").encode("utf-8")
    # cost: one append per dispatch/end/reconcile; ≤ 5,000 dispatches/day → ≤ 15k lines/day.
    with open(path, "ab") as fh:
        fh.write(payload)
        fh.flush()
        os.fsync(fh.fileno())


def read_events(path: str) -> List[Dict[str, Any]]:
    if not os.path.isfile(path):
        return []
    out: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            text = line.strip()
            if not text:
                continue
            try:
                item = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                out.append(item)
    return out


def open_dispatches(path: str) -> List[Dict[str, Any]]:
    """Return dispatched events that have no later ended/reconciled for the same id."""
    events = read_events(path)
    ended_ids = set()
    for ev in events:
        if ev.get("event") in ("ended", "reconciled"):
            eid = str(ev.get("id") or "")
            if eid:
                ended_ids.add(eid)
    open_ones: List[Dict[str, Any]] = []
    for ev in events:
        if ev.get("event") != "dispatched":
            continue
        eid = str(ev.get("id") or "")
        if eid and eid not in ended_ids:
            open_ones.append(ev)
    return open_ones


def iter_events(path: str) -> Iterator[Dict[str, Any]]:
    for ev in read_events(path):
        yield ev


def write_exit_json(path: str, payload: Mapping[str, Any]) -> None:
    """Atomic write via temp + rename (DB-5)."""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    raw = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    data = (raw + "\n").encode("utf-8")
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def read_exit_json(run_dir: str) -> Optional[Dict[str, Any]]:
    path = os.path.join(run_dir, "exit.json")
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as fh:
        try:
            data = json.load(fh)
        except json.JSONDecodeError:
            return None
    return data if isinstance(data, dict) else None


def pid_alive(pid: Optional[int]) -> bool:
    if pid is None:
        return False
    try:
        pid_i = int(pid)
    except (TypeError, ValueError):
        return False
    if pid_i <= 0:
        return False
    try:
        os.kill(pid_i, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True
