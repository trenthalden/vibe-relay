"""orch_dispatch core — gate → TASK.md → supervise (FR-011) or hermes_child envelope (FR-012)."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from lo_core.config import get_executor, merge_config
from lo_core.envelope import seal_context
from lo_core.gate import run_gate
from lo_exec import ledger as ledger_mod
from lo_exec.accept import capture_git_base

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUPERVISE_PY = os.path.join(PLUGIN_ROOT, "lo_exec", "supervise.py")
HERMES_CHILD = "hermes_child"


def dispatch(
    *,
    repo: str,
    brief_text: str,
    workdir: str,
    executor: str,
    data_dir: str,
    config: Optional[Mapping[str, Any]] = None,
    profile: Optional[str] = None,
    timeout_s: Optional[float] = None,
    allowed_extra: Optional[Sequence[str]] = None,
    journal_path: Optional[str] = None,
    receipt_name: Optional[str] = None,
    python_bin: str = "python3",
) -> Dict[str, Any]:
    """Run the gate and, on PASS, start a CLI executor or return a hermes_child payload.

    Never starts a process when the gate refuses (FR-011 / KE-BRIEF-RULES-SKIPPED).
    On PASS for a CLI executor the result includes ``_supervisor`` (Popen) for the
    plugin waiter thread; callers that serialise the result must strip keys starting
    with ``_``.
    """
    cfg = merge_config(config)
    if not workdir or not os.path.isabs(workdir) or not os.path.isdir(workdir):
        return {
            "ok": False,
            "decision": "ERROR",
            "reasons": ["bad_workdir"],
            "id": "",
        }
    workdir = os.path.abspath(workdir)
    data_dir = os.path.abspath(data_dir)
    os.makedirs(data_dir, exist_ok=True)

    gate = run_gate(
        repo,
        brief_text,
        allowed_extra=allowed_extra,
        config=cfg,
        journal_path=journal_path,
        profile=profile,
    )
    if gate["decision"] != "PASS":
        refused = {
            "ok": False,
            "decision": gate["decision"],
            "reasons": list(gate.get("reasons") or []),
            "id": "",
            "gate": gate,
            "profile": gate.get("profile") or (profile or "code"),
        }
        if gate.get("gate_disabled"):
            refused["gate_disabled"] = True
        return refused

    task_text = str(gate.get("task") or "")
    task_bytes = task_text.encode("utf-8")
    brief_sha = str(gate.get("brief_sha256") or "")
    task_sha = hashlib.sha256(task_bytes).hexdigest()
    profile_name = str(gate.get("profile") or profile or "code")

    if executor == HERMES_CHILD:
        payload = _hermes_child_payload(task_text)
        child_out = {
            "ok": True,
            "decision": "PASS",
            "reasons": [],
            "id": "",
            "executor": HERMES_CHILD,
            "profile": profile_name,
            "delegate_task": payload,
            "gate": gate,
            "task_sha256": task_sha,
            "brief_sha256": brief_sha,
        }
        if gate.get("gate_disabled"):
            child_out["gate_disabled"] = True
        return child_out

    try:
        ex = get_executor(cfg, executor)
    except ValueError:
        return {
            "ok": False,
            "decision": "REFUSED",
            "reasons": ["unknown_executor"],
            "id": "",
            "gate": gate,
            "profile": profile_name,
        }

    max_t = float(cfg["max_timeout_s"])
    wall = float(ex["timeout_s"])
    if timeout_s is not None:
        wall = float(timeout_s)
        if wall > max_t:
            return {
                "ok": False,
                "decision": "REFUSED",
                "reasons": ["timeout_above_max"],
                "id": "",
                "gate": gate,
                "profile": profile_name,
            }
        if wall <= 0:
            return {
                "ok": False,
                "decision": "REFUSED",
                "reasons": ["timeout_invalid"],
                "id": "",
                "gate": gate,
                "profile": profile_name,
            }

    led_path = ledger_mod.ledger_path(data_dir)
    seq = ledger_mod.sequence_number(led_path)
    dispatch_id = make_dispatch_id(task_bytes, workdir, executor, seq)
    run_dir = os.path.join(data_dir, "runs", dispatch_id)
    os.makedirs(run_dir, exist_ok=True)

    task_file = os.path.join(run_dir, "TASK.md")
    with open(task_file, "wb") as fh:
        fh.write(task_bytes)
        fh.flush()
        os.fsync(fh.fileno())

    # BRIEF.md so orch_accept can re-run the gate without the orchestrator (KE-BRIEF-RULES-SKIPPED).
    brief_file = os.path.join(run_dir, "BRIEF.md")
    with open(brief_file, "wb") as fh:
        fh.write(brief_text.encode("utf-8"))
        fh.flush()
        os.fsync(fh.fileno())

    # DB-5: capture git base atomically into the dispatched ledger event.
    base = capture_git_base(workdir)
    base_head = base.get("base_head")
    base_dirty = list(base.get("base_dirty") or [])

    receipt = os.path.join(run_dir, receipt_name or "RECEIPT.md")
    argv = _expand_argv(ex["argv"], task_file=task_file, workdir=workdir, receipt=receipt)
    launch = {
        "argv": argv,
        "cwd": workdir,
        "env": dict(ex["env"]),
        "timeout_s": wall,
        "grace_s": int(ex["grace_s"]),
        "receipt": receipt,
        "executor": executor,
        "id": dispatch_id,
    }
    launch_path = os.path.join(run_dir, "launch.json")
    _atomic_json(launch_path, launch)

    # Start detached supervisor (JOB-2); plugin does not wait on the child.
    supervisor_argv = [
        python_bin,
        "-B",
        SUPERVISE_PY,
        "--run-dir",
        run_dir,
    ]
    try:
        # SEC-1: argv list; start_new_session so supervisor survives parent.
        proc = subprocess.Popen(
            supervisor_argv,
            cwd=run_dir,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        return {
            "ok": False,
            "decision": "ERROR",
            "reasons": ["supervisor_spawn_failed:%s" % exc],
            "id": dispatch_id,
            "run_dir": run_dir,
            "gate": gate,
            "profile": profile_name,
        }

    started = time.time()
    event = {
        "event": "dispatched",
        "id": dispatch_id,
        "executor": executor,
        "workdir": workdir,
        "repo": os.path.abspath(repo),
        "brief_sha256": brief_sha,
        "task_sha256": task_sha,
        "pid": proc.pid,
        "pgid": proc.pid,
        "started": started,
        "timeout": wall,
        "run_dir": run_dir,
        "profile": profile_name,
        "seq": seq,
        "base_head": base_head,
        "base_dirty": base_dirty,
    }
    ledger_mod.append_event(led_path, event)

    out = {
        "ok": True,
        "decision": "PASS",
        "reasons": [],
        "id": dispatch_id,
        "executor": executor,
        "run_dir": run_dir,
        "pid": proc.pid,
        "pgid": proc.pid,
        "task_sha256": task_sha,
        "brief_sha256": brief_sha,
        "profile": profile_name,
        "base_head": base_head,
        "gate": gate,
        "_supervisor": proc,
    }
    if gate.get("gate_disabled"):
        out["gate_disabled"] = True
    return out


def make_dispatch_id(task_bytes: bytes, workdir: str, executor: str, seq: int) -> str:
    material = task_bytes + workdir.encode("utf-8") + executor.encode("utf-8") + str(seq).encode("utf-8")
    return hashlib.sha256(material).hexdigest()[:12]


def _hermes_child_payload(task_text: str) -> Dict[str, str]:
    goal = "Execute the sealed lean-orchestration task exactly."
    context = seal_context(goal, task_text)
    return {"goal": goal, "context": context}


def _expand_argv(template: Sequence[str], *, task_file: str, workdir: str, receipt: str) -> List[str]:
    mapping = {
        "{task_file}": task_file,
        "{workdir}": workdir,
        "{receipt}": receipt,
    }
    out: List[str] = []
    for part in template:
        s = str(part)
        for key in sorted(mapping.keys(), key=len, reverse=True):
            s = s.replace(key, mapping[key])
        out.append(s)
    return out


def _atomic_json(path: str, payload: Mapping[str, Any]) -> None:
    raw = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    data = (raw + "\n").encode("utf-8")
    tmp = path + ".tmp"
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def reconcile(data_dir: str) -> List[Dict[str, Any]]:
    """Reconcile open dispatches after restart (FR-017 / JOB-3).

    For each ``dispatched`` without ``ended``:
    - ``exit.json`` present → record ``ended``
    - supervisor pid alive → keep watching
    - neither → ``ended`` with ``rc: null, evidence: supervisor_gone``
    """
    led = ledger_mod.ledger_path(data_dir)
    actions: List[Dict[str, Any]] = []
    for ev in ledger_mod.open_dispatches(led):
        dispatch_id = str(ev.get("id") or "")
        run_dir = str(ev.get("run_dir") or os.path.join(data_dir, "runs", dispatch_id))
        exit_info = ledger_mod.read_exit_json(run_dir)
        pid = ev.get("pid")
        if exit_info is not None:
            ended = {
                "event": "ended",
                "id": dispatch_id,
                "executor": ev.get("executor"),
                "workdir": ev.get("workdir"),
                "brief_sha256": ev.get("brief_sha256"),
                "task_sha256": ev.get("task_sha256"),
                "pid": pid,
                "pgid": ev.get("pgid"),
                "started": ev.get("started"),
                "timeout": ev.get("timeout"),
                "run_dir": run_dir,
                "rc": exit_info.get("rc"),
                "seconds": exit_info.get("seconds"),
                "timed_out": exit_info.get("timed_out"),
                "receipt_present": exit_info.get("receipt_present"),
                "ended": exit_info.get("ended"),
                "evidence": "exit_json",
            }
            ledger_mod.append_event(led, ended)
            actions.append(ended)
            continue
        if ledger_mod.pid_alive(pid if isinstance(pid, int) else None):
            actions.append({"event": "watching", "id": dispatch_id, "pid": pid})
            continue
        ended = {
            "event": "ended",
            "id": dispatch_id,
            "executor": ev.get("executor"),
            "workdir": ev.get("workdir"),
            "brief_sha256": ev.get("brief_sha256"),
            "task_sha256": ev.get("task_sha256"),
            "pid": pid,
            "pgid": ev.get("pgid"),
            "started": ev.get("started"),
            "timeout": ev.get("timeout"),
            "run_dir": run_dir,
            "rc": None,
            "seconds": None,
            "timed_out": False,
            "receipt_present": os.path.isfile(os.path.join(run_dir, "RECEIPT.md")),
            "ended": "",
            "evidence": "supervisor_gone",
        }
        # Also record reconciled marker for audit (DECISION 5).
        ledger_mod.append_event(led, ended)
        ledger_mod.append_event(
            led,
            {
                "event": "reconciled",
                "id": dispatch_id,
                "evidence": "supervisor_gone",
                "run_dir": run_dir,
            },
        )
        actions.append(ended)
    return actions


def format_completion_line(ended: Mapping[str, Any]) -> str:
    """One-line completion message for inject_message (DECISION 6)."""
    return (
        "orch_dispatch ended id=%s executor=%s rc=%s seconds=%s receipt=%s run_dir=%s"
        % (
            ended.get("id"),
            ended.get("executor"),
            ended.get("rc"),
            ended.get("seconds"),
            ended.get("receipt_present"),
            ended.get("run_dir"),
        )
    )


def wait_for_exit(run_dir: str, *, timeout_s: float = 60.0, poll_s: float = 0.1) -> Optional[Dict[str, Any]]:
    """Test helper — poll until exit.json appears."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        info = ledger_mod.read_exit_json(run_dir)
        if info is not None:
            return info
        time.sleep(poll_s)
    return None
