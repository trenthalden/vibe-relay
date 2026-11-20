#!/usr/bin/env python3
"""Detached supervisor — starts the executor process group, enforces wall timeout (JOB-2/3).

Reads ``<run_dir>/launch.json`` (argv, cwd, env additions, timeout_s, grace_s, receipt).
Writes ``<run_dir>/exit.json`` atomically and exits. Never uses ``shell=True`` (SEC-1).
Executor stdout+stderr append to ``<run_dir>/output.log``.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="lo_exec.supervise")
    parser.add_argument("--run-dir", required=True, help="Absolute run directory")
    args = parser.parse_args(argv)
    run_dir = os.path.abspath(args.run_dir)
    launch_path = os.path.join(run_dir, "launch.json")
    try:
        with open(launch_path, "r", encoding="utf-8") as fh:
            launch = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        _write_exit(
            run_dir,
            {
                "rc": None,
                "seconds": 0.0,
                "timed_out": False,
                "receipt_present": False,
                "ended": _local_now(),
                "error": "launch_unreadable:%s" % exc,
            },
        )
        return 1
    return run_supervisor(run_dir, launch)


def run_supervisor(run_dir: str, launch: Mapping[str, Any]) -> int:
    argv = list(launch.get("argv") or [])
    if not argv or not all(isinstance(x, str) for x in argv):
        _write_exit(
            run_dir,
            {
                "rc": None,
                "seconds": 0.0,
                "timed_out": False,
                "receipt_present": False,
                "ended": _local_now(),
                "error": "launch.argv",
            },
        )
        return 1
    cwd = str(launch.get("cwd") or run_dir)
    env_add = launch.get("env") or {}
    if not isinstance(env_add, dict):
        env_add = {}
    timeout_s = float(launch.get("timeout_s", 3600))
    grace_s = float(launch.get("grace_s", 20))
    receipt = str(launch.get("receipt") or os.path.join(run_dir, "RECEIPT.md"))

    env = os.environ.copy()
    for key in sorted(env_add.keys()):
        env[str(key)] = str(env_add[key])

    # JOB-2: explicit argv/env/cwd — never the shell's.
    # SEC-1: argv list only, never shell=True.
    start = time.monotonic()
    timed_out = False
    rc: Optional[int] = None
    log_path = os.path.join(run_dir, "output.log")
    try:
        log_fh = open(log_path, "ab")
    except OSError as exc:
        _write_exit(
            run_dir,
            {
                "rc": None,
                "seconds": 0.0,
                "timed_out": False,
                "receipt_present": False,
                "ended": _local_now(),
                "error": "output_log:%s" % exc,
            },
        )
        return 1

    try:
        try:
            # start_new_session → new process group (pgid == child pid on POSIX).
            child = _popen(argv, cwd=cwd, env=env, log_fh=log_fh)
        except OSError as exc:
            _write_exit(
                run_dir,
                {
                    "rc": None,
                    "seconds": round(time.monotonic() - start, 4),
                    "timed_out": False,
                    "receipt_present": os.path.isfile(receipt),
                    "ended": _local_now(),
                    "error": "spawn_failed:%s" % exc,
                },
            )
            return 1

        pgid = child.pid
        try:
            # JOB-1: blocking wait for the remaining wall budget — no poll loop.
            # cost: one wait syscall per dispatch (plus at most one grace wait after TERM).
            # At ≤ 5,000 dispatches/day → ≤ 5,000 waits/day; a 60-minute run is one blocked
            # wait, not 18,000 wakeups (0.2 s poll × 3600 s).
            remaining = max(0.0, timeout_s - (time.monotonic() - start))
            try:
                rc = int(child.wait(timeout=remaining))
            except subprocess.TimeoutExpired:
                timed_out = True
                _kill_group(pgid, grace_s)
                try:
                    rc_wait = child.wait(timeout=grace_s + 2)
                    rc = int(rc_wait) if rc_wait is not None else None
                except subprocess.TimeoutExpired:
                    rc = child.poll()
                    rc = int(rc) if rc is not None else None
                except Exception:  # noqa: BLE001
                    rc = child.poll()
                    rc = int(rc) if rc is not None else None
        finally:
            # Ensure we do not leave the child unreaped.
            if child.poll() is None:
                _kill_group(pgid, grace_s)
                try:
                    child.wait(timeout=grace_s + 2)
                except Exception:  # noqa: BLE001
                    pass
                if rc is None:
                    polled = child.poll()
                    rc = int(polled) if polled is not None else None
    finally:
        try:
            log_fh.close()
        except Exception:  # noqa: BLE001
            pass

    seconds = round(time.monotonic() - start, 4)
    _write_exit(
        run_dir,
        {
            "rc": rc,
            "seconds": seconds,
            "timed_out": timed_out,
            "receipt_present": os.path.isfile(receipt),
            "ended": _local_now(),
        },
    )
    return 0 if not timed_out else 124


def _popen(argv: List[str], *, cwd: str, env: Dict[str, str], log_fh):
    return subprocess.Popen(
        argv,
        cwd=cwd,
        env=env,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )


def _kill_group(pgid: int, grace_s: float) -> None:
    try:
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, OSError):
        return
    deadline = time.monotonic() + max(0.0, grace_s)
    while time.monotonic() < deadline:
        try:
            os.killpg(pgid, 0)
        except (ProcessLookupError, OSError):
            return
        time.sleep(0.1)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, OSError):
        return


def _write_exit(run_dir: str, payload: Mapping[str, Any]) -> None:
    # Inline atomic write so supervise.py stays runnable without package imports
    # when launched as ``python3 -B <plugin>/lo_exec/supervise.py``.
    path = os.path.join(run_dir, "exit.json")
    raw = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    data = (raw + "\n").encode("utf-8")
    tmp = path + ".tmp"
    os.makedirs(run_dir, exist_ok=True)
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _local_now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


if __name__ == "__main__":
    sys.exit(main())
