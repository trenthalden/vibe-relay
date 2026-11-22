"""Dispatch ledger — append-only JSONL with fsync (DB-5, FR-017).

Events: ``dispatched``, ``ended``, ``reconciled``, ``child_started``. No secrets in records (SEC-2).
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os

_IS_WINDOWS = os.name == "nt"
try:
    import psutil
except ImportError:
    psutil = None
import subprocess
from typing import Any, Dict, Iterator, List, Mapping, Optional


def ledger_path(data_dir: str) -> str:
    return os.path.join(data_dir, "ledger.jsonl")


def sequence_number(path: str) -> int:
    """Number of existing lines — used as the dispatch_id sequence input."""
    if not os.path.isfile(path):
        return 0
    n = 0
    with open(path, "r", encoding="utf-8-sig") as fh:
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
    with open(path, "r", encoding="utf-8-sig") as fh:
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
    with open(path, "r", encoding="utf-8-sig") as fh:
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
    if psutil is not None:
        return psutil.pid_exists(pid_i)
    if _IS_WINDOWS:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.GetExitCodeProcess.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = kernel.OpenProcess(0x1000, False, pid_i)
        if not handle:
            return ctypes.get_last_error() == 5  # Access denied still denotes a process.
        try:
            code = wintypes.DWORD()
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid_i, 0)  # windows-footgun: ok — POSIX-only fallback after Windows return.
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def process_start_token(pid: Optional[int]) -> Optional[str]:
    """Opaque OS start token for ``pid``; None if unreadable.

    Paired with ``pid_alive`` so child reconcile can tell a live host from a recycled
    PID after the dispatching process exits (FR-012). CLI supervise liveness uses
    ``pid_alive`` alone; children also seal the start token at dispatch.
    """
    if pid is None:
        return None
    try:
        pid_i = int(pid)
    except (TypeError, ValueError):
        return None
    if pid_i <= 0:
        return None
    if _IS_WINDOWS:
        try:
            if psutil is not None:
                return "%.6f" % psutil.Process(pid_i).create_time()
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
            kernel.GetProcessTimes.restype = wintypes.BOOL
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel.CloseHandle.restype = wintypes.BOOL
            handle = kernel.OpenProcess(0x1000, False, pid_i)
            if not handle:
                return None
            try:
                created, exited, kernel_time, user_time = (wintypes.FILETIME() for _ in range(4))
                if not kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel_time), ctypes.byref(user_time)):
                    return None
                return str((created.dwHighDateTime << 32) | created.dwLowDateTime)
            finally:
                kernel.CloseHandle(handle)
        except Exception:
            # An inaccessible/exited process has no readable start token.
            return None
    try:
        # cost: one ps per child dispatch + one per reconcile of each open child.
        out = subprocess.check_output(
            ["ps", "-p", str(pid_i), "-o", "lstart="],
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired):
        return None
    text = out.decode("utf-8", "replace").strip()
    return text or None


def host_process_alive(host_pid: Optional[int], host_started: Optional[str]) -> bool:
    """True when the dispatching host is still the same process (pid + start token).

    Missing ``host_pid`` → False (legacy child rows end as ``host_restarted``).
    Alive pid whose sealed start token no longer matches → False (PID reuse).
    """
    if host_pid is None:
        return False
    try:
        pid_i = int(host_pid)
    except (TypeError, ValueError):
        return False
    if not pid_alive(pid_i):
        return False
    sealed_s = str(host_started) if host_started is not None else ""
    if sealed_s == "":
        return True
    current = process_start_token(pid_i)
    if current is None:
        # kill(0) succeeded but start unreadable — keep open (avoid false host_restarted).
        return True
    return current == sealed_s
