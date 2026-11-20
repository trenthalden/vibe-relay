"""Hermes adapter for lean-orchestration — registration only; no business rules."""
from __future__ import annotations

import contextvars
import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# Hyphenated plugin dir is not a normal import package name; put the plugin root on
# sys.path so sibling modules resolve under Hermes load (same pattern as vibe-relay).
#
# Module isolation (KE-2026-11-20-STALE-TOPLEVEL-MODULES, live incident 2026-11-20):
# Hermes loads each plugin as a package with submodule_search_locations and evicts
# stale ``<plugin>.*`` on reload, but our top-level ``lo_core`` / ``lo_exec`` names
# escape that eviction. A prior standalone install left those modules cached; the
# vendored copy's ``import lo_exec`` then reused a SUPERVISE_PY that pointed into the
# deleted tree, so the supervisor died immediately with stderr on DEVNULL and the
# waiter only saw ``evidence: supervisor_exited`` / ``rc=None``. Force this copy's
# root to sys.path[0] and drop every foreign ``lo_core`` / ``lo_exec`` entry before
# importing. CFG-1 / SEC-2: exactly one copy of this code serves a plugin instance.
PACKAGE = Path(__file__).resolve().parent


def _isolate_plugin_modules(package: Path = PACKAGE) -> None:
    """Pin ``sys.path`` and evict foreign ``lo_core`` / ``lo_exec`` modules (JOB-3)."""
    root = str(package.resolve())
    while root in sys.path:
        sys.path.remove(root)
    sys.path.insert(0, root)
    # cost: once per plugin load/reload; ≤ a few gateway restarts/day.
    stale = []
    for name, mod in list(sys.modules.items()):
        if name != "lo_core" and not name.startswith("lo_core."):
            if name != "lo_exec" and not name.startswith("lo_exec."):
                continue
        file_attr = getattr(mod, "__file__", None)
        if not file_attr:
            stale.append(name)
            continue
        try:
            resolved = Path(file_attr).resolve()
            resolved.relative_to(package.resolve())
        except (OSError, ValueError):
            stale.append(name)
    for name in stale:
        del sys.modules[name]


_isolate_plugin_modules()

from lo_core.bypass import code_starts_executor, command_starts_executor  # noqa: E402
from lo_core.config import merge_config  # noqa: E402
from lo_core.envelope import verify_envelope  # noqa: E402
from lo_core.gate import make_error_result, run_gate  # noqa: E402
from lo_core import journal as journal_mod  # noqa: E402  (bound at load: no lazy lo_* imports, KE-2026-11-20-STALE-TOPLEVEL-MODULES)
from lo_exec import accept as accept_mod  # noqa: E402
from lo_exec import dispatch as dispatch_mod  # noqa: E402
from lo_exec import ledger as ledger_mod  # noqa: E402

_PLUGIN_CTX = None
_WATCH_STARTED = False
_PENDING_POST: List[Tuple[str, str]] = []  # (dispatch_id, text) — post-restart / inject miss
_NOTIFIED: Set[str] = set()
_LIVE_WATCH: Set[str] = set()  # open dispatches that survived reconcile (supervisor alive)
_WATCH_LOCK = threading.Lock()


def _reset_runtime_state() -> None:
    """Test helper — clear watcher / notify bookkeeping between cases."""
    global _PLUGIN_CTX, _WATCH_STARTED, _PENDING_POST, _NOTIFIED, _LIVE_WATCH
    with _WATCH_LOCK:
        _PLUGIN_CTX = None
        _WATCH_STARTED = False
        _PENDING_POST = []
        _NOTIFIED = set()
        _LIVE_WATCH = set()


def register(ctx: Any) -> None:
    """Register tools and hooks on the documented PluginContext API only."""
    global _PLUGIN_CTX
    _PLUGIN_CTX = ctx

    ctx.register_tool(
        name="orch_gate",
        toolset="lean-orchestration",
        schema=_orch_gate_schema(),
        handler=_safe_tool(_handle_orch_gate),
    )
    ctx.register_tool(
        name="orch_dispatch",
        toolset="lean-orchestration",
        schema=_orch_dispatch_schema(),
        handler=_safe_tool(_handle_orch_dispatch),
    )
    ctx.register_tool(
        name="orch_accept",
        toolset="lean-orchestration",
        schema=_orch_accept_schema(),
        handler=_safe_tool(_handle_orch_accept),
    )

    if hasattr(ctx, "register_hook"):
        ctx.register_hook("pre_tool_call", _pre_tool_call)
        ctx.register_hook("on_session_start", _on_session_start)

    if hasattr(ctx, "register_command"):
        try:
            ctx.register_command(
                name="orch",
                handler=_orch_command,
                description=(
                    "Coverage: gate enabled/disabled, last dispatches, accept counters "
                    "(FR-015)."
                ),
                args_hint="",
            )
        except Exception:  # noqa: BLE001 — older hosts
            pass

    # Mirror DEV Framework skill registration (documented register_skill API only).
    skill_path = PACKAGE / "skills" / "lean-orchestration" / "SKILL.md"
    if skill_path.is_file() and hasattr(ctx, "register_skill"):
        try:
            ctx.register_skill(
                name="lean-orchestration",
                path=skill_path,
                description="Deterministic orch_gate / orch_dispatch / orch_accept door",
            )
        except Exception:  # noqa: BLE001 — older hosts still get the tools
            pass

    # Register-time reconcile (FR-017). Context is gone after a Hermes restart:
    # ended → pending post_message; live supervisors → watch loop only while needed.
    data_dir = _data_dir(ctx)
    if data_dir:
        try:
            _apply_reconcile(ctx, data_dir)
        except Exception:  # noqa: BLE001 — never fail registration
            pass
        _maybe_start_watch_loop(ctx, data_dir)


def _safe_tool(fn):
    def handler(params: Dict[str, Any], **kwargs: Any) -> str:
        try:
            return fn(_PLUGIN_CTX, params, **kwargs)
        except Exception as exc:  # noqa: BLE001 — last resort; still JSON
            payload = make_error_result("rules_source_error:tool")
            payload["journal_error"] = str(exc)
            return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    return handler


def _orch_gate_schema() -> Dict[str, Any]:
    return {
        "name": "orch_gate",
        "description": (
            "Dry-run the lean-orchestration brief gate for a target repository. "
            "Returns a versioned JSON decision (PASS/REFUSED/ERROR), reasons, and "
            "what would be delivered to an executor."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "description": "Absolute path of the target repository",
                },
                "brief": {
                    "type": "string",
                    "description": "Absolute path of a brief file, or the brief text itself",
                },
                "allowed": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional extra ALLOWED patterns",
                },
                "profile": {
                    "type": "string",
                    "description": "Brief profile name (code|research); not chosen by the brief",
                },
            },
            "required": ["repo", "brief"],
        },
    }


def _orch_dispatch_schema() -> Dict[str, Any]:
    return {
        "name": "orch_dispatch",
        "description": (
            "Single door to an executor: gate the brief, assemble the task with delivered "
            "rules, then start a configured CLI executor (or return a sealed hermes_child "
            "delegate_task payload). Refused briefs start no process."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "description": "Absolute path of the target repository",
                },
                "brief": {
                    "type": "string",
                    "description": "Absolute path of a brief file, or the brief text itself",
                },
                "workdir": {
                    "type": "string",
                    "description": "Absolute working directory for the executor",
                },
                "executor": {
                    "type": "string",
                    "description": "Configured executor name, or hermes_child",
                },
                "profile": {
                    "type": "string",
                    "description": "Brief profile (code|research)",
                },
                "timeout_s": {
                    "type": "number",
                    "description": "Optional wall timeout override (≤ config max_timeout_s)",
                },
                "allowed": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional extra ALLOWED patterns",
                },
            },
            "required": ["repo", "brief", "workdir", "executor"],
        },
    }


def _orch_accept_schema() -> Dict[str, Any]:
    return {
        "name": "orch_accept",
        "description": (
            "Accept a finished dispatch by code: changed paths ⊆ ALLOWED, fresh gate PASS "
            "bound to the tree hash, and project probes. Never trusts the receipt alone."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "dispatch_id": {
                    "type": "string",
                    "description": "Dispatch id from orch_dispatch / ledger",
                },
                "profile": {
                    "type": "string",
                    "description": "Brief profile override (optional)",
                },
            },
            "required": ["dispatch_id"],
        },
    }


def _handle_orch_gate(ctx: Any, params: Dict[str, Any], **kwargs: Any) -> str:
    del kwargs
    repo = str(params.get("repo") or "")
    brief_arg = str(params.get("brief") or "")
    allowed = params.get("allowed") or []
    if not isinstance(allowed, list):
        allowed = [str(allowed)]
    profile = params.get("profile")

    brief_text, brief_err = _resolve_brief(brief_arg)
    if brief_err:
        _flush_pending_post(ctx)
        return _dumps(make_error_result(brief_err, repo=repo, profile=str(profile or "code")))

    config = _config_from_ctx(ctx)
    journal_path = _journal_path(ctx)

    result = run_gate(
        repo,
        brief_text,
        allowed_extra=[str(a) for a in allowed],
        config=config,
        journal_path=journal_path,
        profile=str(profile) if profile else None,
    )
    payload = _gate_public(result)
    if result.get("journal_error"):
        payload["journal_error"] = result["journal_error"]
    if journal_path is None:
        payload["journal_error"] = "no plugin data directory"
    _flush_pending_post(ctx)
    return _dumps(payload)


def _handle_orch_accept(ctx: Any, params: Dict[str, Any], **kwargs: Any) -> str:
    del kwargs
    dispatch_id = str(params.get("dispatch_id") or "")
    profile = params.get("profile")
    config = _config_from_ctx(ctx)
    data_dir = _data_dir(ctx)
    if not data_dir:
        _flush_pending_post(ctx)
        return _dumps(
            {
                "schema": "lean-orchestration.accept/1",
                "decision": "REJECTED",
                "reasons": ["dispatch_unknown"],
                "changed_paths": [],
                "allowed": [],
                "tree_sha256": "",
                "probes": "none",
            }
        )
    journal_path = _journal_path(ctx)
    result = accept_mod.accept_dispatch(
        dispatch_id=dispatch_id,
        data_dir=data_dir,
        config=config,
        journal_path=journal_path,
        profile=str(profile) if profile else None,
    )
    _flush_pending_post(ctx)
    return _dumps(result)


def _orch_command(raw_args: str = "", **kwargs: Any) -> str:
    """Slash command `/orch` — coverage from ledger + journal only (FR-015)."""
    del raw_args, kwargs
    ctx = _PLUGIN_CTX
    if ctx is None:
        return "gate: unknown (no plugin context)\n"
    config = _config_from_ctx(ctx)
    data_dir = _data_dir(ctx) or ""
    journal_path = _journal_path(ctx)
    with _WATCH_LOCK:
        pending = len(_PENDING_POST)
    return accept_mod.format_orch_coverage(
        data_dir=data_dir,
        journal_path=journal_path,
        config=config,
        pending_count=pending,
    )


def _handle_orch_dispatch(ctx: Any, params: Dict[str, Any], **kwargs: Any) -> str:
    del kwargs
    # SEC-2: capture the caller's context so keyless inject_message targets this
    # session (documented keyless injection). No model-supplied session_key.
    snapshot = contextvars.copy_context()

    repo = str(params.get("repo") or "")
    brief_arg = str(params.get("brief") or "")
    workdir = str(params.get("workdir") or "")
    executor = str(params.get("executor") or "")
    profile = params.get("profile")
    timeout_s = params.get("timeout_s")
    allowed = params.get("allowed") or []
    if not isinstance(allowed, list):
        allowed = [str(allowed)]

    brief_text, brief_err = _resolve_brief(brief_arg)
    if brief_err:
        _flush_pending_post(ctx)
        return _dumps({"ok": False, "decision": "ERROR", "reasons": [brief_err], "id": ""})

    config = _config_from_ctx(ctx)
    data_dir = _data_dir(ctx)
    if not data_dir:
        _flush_pending_post(ctx)
        return _dumps(
            {
                "ok": False,
                "decision": "ERROR",
                "reasons": ["no plugin data directory"],
                "id": "",
            }
        )
    journal_path = _journal_path(ctx)

    result = dispatch_mod.dispatch(
        repo=repo,
        brief_text=brief_text,
        workdir=workdir,
        executor=executor,
        data_dir=data_dir,
        config=config,
        profile=str(profile) if profile else None,
        timeout_s=float(timeout_s) if timeout_s is not None else None,
        allowed_extra=[str(a) for a in allowed],
        journal_path=journal_path,
        python_bin=sys.executable or "python3",
    )

    supervisor = result.pop("_supervisor", None)
    if supervisor is not None and result.get("ok"):
        _start_completion_waiter(
            ctx,
            snapshot,
            supervisor,
            data_dir=data_dir,
            dispatch_id=str(result.get("id") or ""),
            run_dir=str(result.get("run_dir") or ""),
            executor_name=str(result.get("executor") or ""),
            workdir=workdir,
            brief_sha=str(result.get("brief_sha256") or ""),
            task_sha=str(result.get("task_sha256") or ""),
            started=None,
            timeout=None,
            pid=result.get("pid"),
            pgid=result.get("pgid"),
        )

    _flush_pending_post(ctx)

    public = {k: v for k, v in result.items() if k != "gate" and not str(k).startswith("_")}
    if "gate" in result and isinstance(result["gate"], dict):
        public["gate_decision"] = result["gate"].get("decision")
        public["gate_reasons"] = result["gate"].get("reasons")
        public["task_bytes"] = result["gate"].get("task_bytes")
    return _dumps(public)


def _start_completion_waiter(
    ctx: Any,
    snapshot: "contextvars.Context",
    supervisor: Any,
    *,
    data_dir: str,
    dispatch_id: str,
    run_dir: str,
    executor_name: str,
    workdir: str,
    brief_sha: str,
    task_sha: str,
    started: Any,
    timeout: Any,
    pid: Any,
    pgid: Any,
) -> None:
    """Block on supervisor Popen.wait inside the captured call context (no polling)."""

    def _wait_and_notify() -> None:
        supervisor_rc = None
        try:
            supervisor_rc = supervisor.wait()
        except Exception:  # noqa: BLE001
            supervisor_rc = getattr(supervisor, "returncode", None)
        exit_info = ledger_mod.read_exit_json(run_dir)
        if exit_info:
            ended = {
                "event": "ended",
                "id": dispatch_id,
                "executor": executor_name,
                "workdir": workdir,
                "brief_sha256": brief_sha,
                "task_sha256": task_sha,
                "pid": pid,
                "pgid": pgid,
                "started": started,
                "timeout": timeout,
                "run_dir": run_dir,
                "rc": exit_info.get("rc"),
                "seconds": exit_info.get("seconds"),
                "timed_out": exit_info.get("timed_out"),
                "receipt_present": exit_info.get("receipt_present"),
                "ended": exit_info.get("ended"),
                "evidence": "exit_json",
            }
        else:
            # API-7: supervisor died without exit.json — surface rc + log tail.
            ended = {
                "event": "ended",
                "id": dispatch_id,
                "executor": executor_name,
                "workdir": workdir,
                "brief_sha256": brief_sha,
                "task_sha256": task_sha,
                "pid": pid,
                "pgid": pgid,
                "started": started,
                "timeout": timeout,
                "run_dir": run_dir,
                "rc": None,
                "seconds": None,
                "timed_out": False,
                "receipt_present": os.path.isfile(os.path.join(run_dir, "RECEIPT.md")),
                "ended": "",
                "evidence": "supervisor_exited",
                "supervisor_rc": supervisor_rc,
                "supervisor_log_tail": dispatch_mod.supervisor_log_tail(run_dir),
            }
        # Avoid double-append if reconcile already recorded this id.
        led = ledger_mod.ledger_path(data_dir)
        already = any(
            ev.get("event") == "ended" and str(ev.get("id")) == dispatch_id
            for ev in ledger_mod.read_events(led)
        )
        if not already:
            ledger_mod.append_event(led, ended)
        _notify_completion(ctx, ended, via_inject=True)

    def _runner() -> None:
        snapshot.run(_wait_and_notify)

    t = threading.Thread(
        target=_runner,
        name="lean-orchestration-wait-%s" % dispatch_id,
        daemon=True,
    )
    t.start()


def _pre_tool_call(
    tool_name: str = "",
    args: Optional[Dict[str, Any]] = None,
    **kwargs: Any,
) -> Optional[Dict[str, str]]:
    """Shared guards for delegate_task envelope and terminal/execute_code bypass (SEC-2)."""
    del kwargs
    name = str(tool_name or "")
    args = dict(args) if isinstance(args, dict) else {}
    cfg = _config_from_ctx(_PLUGIN_CTX) if _PLUGIN_CTX is not None else merge_config()

    if name == "delegate_task":
        return delegate_guard(args, cfg)
    if name in ("terminal", "execute_code", "run_terminal", "shell"):
        return bypass_guard(name, args, cfg)
    return None


def delegate_guard(args: Dict[str, Any], cfg: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """Refuse delegate_task without a valid envelope (FR-012). One shared function."""
    if str(cfg.get("delegate_guard") or "envelope") == "off":
        return None
    tasks = _delegate_tasks(args)
    if not tasks:
        return {
            "action": "block",
            "message": (
                "lean-orchestration: delegate_task refused — no tasks. "
                "Use orch_dispatch(executor='hermes_child') to obtain a sealed payload."
            ),
        }
    for task in tasks:
        goal = str(task.get("goal") or task.get("prompt") or "")
        context = str(task.get("context") or "")
        ok, reason = verify_envelope(goal, context)
        if not ok:
            return {
                "action": "block",
                "message": (
                    "lean-orchestration: delegate_task refused (%s). "
                    "Call orch_dispatch(executor='hermes_child') and pass its "
                    "delegate_task payload unchanged."
                    % reason
                ),
            }
    return None


def bypass_guard(
    tool_name: str,
    args: Dict[str, Any],
    cfg: Dict[str, Any],
) -> Optional[Dict[str, str]]:
    """Refuse terminal/execute_code that starts a configured executor (FR-013). Shared."""
    binaries: List[str] = []
    for _ename, entry in sorted((cfg.get("executors") or {}).items()):
        if isinstance(entry, dict):
            for b in entry.get("binary_names") or []:
                s = str(b).strip()
                if s and s not in binaries:
                    binaries.append(s)
    launchers = [str(x) for x in (cfg.get("launcher_scripts") or [])]
    if tool_name == "execute_code":
        text = _tool_command_text(tool_name, args)
        hit = code_starts_executor(text, binaries, launchers)
    else:
        text = _tool_command_text(tool_name, args)
        hit = command_starts_executor(text, binaries, launchers)
    if hit is None:
        return None
    return {
        "action": "block",
        "message": (
            "lean-orchestration: refused launching %r outside the plugin. "
            "Use orch_dispatch."
            % hit
        ),
    }


def _delegate_tasks(args: Dict[str, Any]) -> List[Dict[str, Any]]:
    tasks = args.get("tasks")
    if isinstance(tasks, list) and tasks:
        return [t for t in tasks if isinstance(t, dict)]
    # Single-task form.
    if any(k in args for k in ("goal", "context", "prompt")):
        return [args]
    return []


def _tool_command_text(tool_name: str, args: Dict[str, Any]) -> str:
    if tool_name == "execute_code":
        for key in ("code", "source", "script"):
            if key in args:
                return str(args.get(key) or "")
        return ""
    for key in ("command", "cmd", "code"):
        if key in args:
            return str(args.get(key) or "")
    return ""


def _on_session_start(**kwargs: Any) -> None:
    del kwargs
    ctx = _PLUGIN_CTX
    if ctx is None:
        return
    data_dir = _data_dir(ctx)
    if data_dir:
        try:
            _apply_reconcile(ctx, data_dir)
        except Exception:  # noqa: BLE001
            pass
        _maybe_start_watch_loop(ctx, data_dir)


def _apply_reconcile(ctx: Any, data_dir: str) -> None:
    """Reconcile open dispatches. Ended → pending post; live → LIVE_WATCH."""
    actions = dispatch_mod.reconcile(data_dir)
    for action in actions:
        event = action.get("event")
        dispatch_id = str(action.get("id") or "")
        if event == "ended":
            _notify_completion(ctx, action, via_inject=False)
        elif event == "watching" and dispatch_id:
            with _WATCH_LOCK:
                _LIVE_WATCH.add(dispatch_id)


def _maybe_start_watch_loop(ctx: Any, data_dir: str) -> None:
    """JOB-1: poll only while reconciled live dispatches exist; stop when empty.

    # cost: watch_interval_s default 10 → polls/day = live_open × (86400 / interval).
    # At ≤ 5 concurrent live opens and T=10 s: 5 × 8640 = 43,200 cheap exists-checks/day.
    # Loop exits when LIVE_WATCH is empty (no forever idle poll after restart drain).
    """
    global _WATCH_STARTED
    with _WATCH_LOCK:
        if not _LIVE_WATCH:
            return
        if _WATCH_STARTED:
            return
        _WATCH_STARTED = True

    cfg = _config_from_ctx(ctx)
    interval = float(cfg.get("watch_interval_s") or 10)

    def _loop() -> None:
        global _WATCH_STARTED
        while True:
            with _WATCH_LOCK:
                live = set(_LIVE_WATCH)
            if not live:
                with _WATCH_LOCK:
                    _WATCH_STARTED = False
                return
            try:
                _watch_once(ctx, data_dir)
            except Exception:  # noqa: BLE001
                pass
            with _WATCH_LOCK:
                if not _LIVE_WATCH:
                    _WATCH_STARTED = False
                    return
            time.sleep(interval)

    t = threading.Thread(target=_loop, name="lean-orchestration-watch", daemon=True)
    t.start()


def _watch_once(ctx: Any, data_dir: str) -> None:
    # JOB-1: cheap check — does exit.json exist for live reconciled dispatches?
    with _WATCH_LOCK:
        live_ids = set(_LIVE_WATCH)
    for dispatch_id in sorted(live_ids):
        run_dir = os.path.join(data_dir, "runs", dispatch_id)
        # Prefer ledger run_dir when present.
        for ev in ledger_mod.open_dispatches(ledger_mod.ledger_path(data_dir)):
            if str(ev.get("id") or "") == dispatch_id:
                run_dir = str(ev.get("run_dir") or run_dir)
                break
        if not os.path.isfile(os.path.join(run_dir, "exit.json")):
            # Supervisor gone without exit.json → reconcile will end it.
            if not any(
                str(ev.get("id")) == dispatch_id
                and ledger_mod.pid_alive(ev.get("pid") if isinstance(ev.get("pid"), int) else None)
                for ev in ledger_mod.open_dispatches(ledger_mod.ledger_path(data_dir))
            ):
                actions = dispatch_mod.reconcile(data_dir)
                for ended in actions:
                    if ended.get("event") == "ended" and str(ended.get("id")) == dispatch_id:
                        with _WATCH_LOCK:
                            _LIVE_WATCH.discard(dispatch_id)
                        _notify_completion(ctx, ended, via_inject=False)
            continue
        actions = dispatch_mod.reconcile(data_dir)
        for ended in actions:
            if ended.get("event") == "ended" and str(ended.get("id")) == dispatch_id:
                with _WATCH_LOCK:
                    _LIVE_WATCH.discard(dispatch_id)
                _notify_completion(ctx, ended, via_inject=False)


def _notify_completion(ctx: Any, ended: Dict[str, Any], *, via_inject: bool) -> None:
    dispatch_id = str(ended.get("id") or "")
    with _WATCH_LOCK:
        if dispatch_id in _NOTIFIED:
            return
        _NOTIFIED.add(dispatch_id)
    text = dispatch_mod.format_completion_line(ended)
    if via_inject and hasattr(ctx, "inject_message"):
        injected = False
        try:
            # Keyless: Hermes routes from the caller's bound session context.
            injected = bool(ctx.inject_message(text))
        except TypeError:
            try:
                injected = bool(ctx.inject_message(text))
            except Exception:  # noqa: BLE001
                injected = False
        except Exception:  # noqa: BLE001
            injected = False
        if injected:
            return
        _journal_injection_miss(ctx, dispatch_id, text)
    # Reconciled / inject-miss → pending; delivered once on next orch_gate/orch_dispatch.
    with _WATCH_LOCK:
        _PENDING_POST.append((dispatch_id, text))


def _journal_injection_miss(ctx: Any, dispatch_id: str, text: str) -> None:
    path = _journal_path(ctx)
    if not path:
        return
    try:
        journal_mod.append_journal(
            path,
            {
                "event": "inject_message_false",
                "id": dispatch_id,
                "text": text[:500],
            },
            rotate_bytes=5 * 1024 * 1024,
        )
    except Exception:  # noqa: BLE001
        return


def _flush_pending_post(ctx: Any) -> None:
    """Deliver pending completion lines via post_message exactly once (any session)."""
    global _PENDING_POST
    with _WATCH_LOCK:
        items = list(_PENDING_POST)
        _PENDING_POST = []
    if not items:
        return
    if not hasattr(ctx, "post_message"):
        # Put back if we cannot deliver.
        with _WATCH_LOCK:
            _PENDING_POST.extend(items)
        return
    for dispatch_id, text in items:
        try:
            ctx.post_message(text)
            _journal_pending_delivered(ctx, dispatch_id, text)
        except Exception:  # noqa: BLE001
            with _WATCH_LOCK:
                _PENDING_POST.append((dispatch_id, text))


def _journal_pending_delivered(ctx: Any, dispatch_id: str, text: str) -> None:
    path = _journal_path(ctx)
    if not path:
        return
    try:
        journal_mod.append_journal(
            path,
            {
                "event": "pending_post_delivered",
                "id": dispatch_id,
                "text": text[:500],
            },
            rotate_bytes=5 * 1024 * 1024,
        )
    except Exception:  # noqa: BLE001
        return


def _dumps(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _gate_public(result: Dict[str, Any]) -> Dict[str, Any]:
    payload = {
        "schema": result["schema"],
        "decision": result["decision"],
        "reasons": result["reasons"],
        "areas": result["areas"],
        "allowed": list(result.get("allowed") or []),
        "unmapped": result["unmapped"],
        "cited": result["cited"],
        "delivered": {
            "rules": result["delivered"]["rules"],
            "known_errors": result["delivered"]["known_errors"],
        },
        "task_bytes": result["task_bytes"],
        "rules_source": result["rules_source"],
        "profile": result.get("profile") or "code",
    }
    if result.get("gate_disabled"):
        payload["gate_disabled"] = True
    return payload


def _resolve_brief(value: str) -> tuple:
    """Return (text, error_code_or_None)."""
    if os.path.isabs(value) and os.path.isfile(value):
        with open(value, "r", encoding="utf-8") as fh:
            return fh.read(), None
    if os.path.isabs(value) and (value.endswith(".md") or os.sep in value):
        if not os.path.exists(value):
            return "", "brief_not_found"
    return value, None


def _config_from_ctx(ctx: Any) -> Dict[str, Any]:
    """Plugin settings via ctx.get_config (documented accessor)."""
    overrides: Dict[str, Any] = {}
    for key in (
        "navigate_timeout_s",
        "delivery_cap_bytes",
        "known_errors_limit",
        "journal_rotate_bytes",
        "required_sections",
        "executors",
        "delegate_guard",
        "brief_profiles",
        "launcher_scripts",
        "watch_interval_s",
        "max_timeout_s",
        "gate_enabled",
        "changed_paths_cap",
        "probe_output_cap_bytes",
        "probes",
    ):
        try:
            value = ctx.get_config(key, default=None)
        except Exception:  # noqa: BLE001 — fake contexts may be minimal
            value = None
        if value is not None:
            overrides[key] = value
    return merge_config(overrides or None)


def _data_dir(ctx: Any) -> Optional[str]:
    try:
        state = getattr(ctx, "state", None)
        data_dir = getattr(state, "data_dir", None) if state is not None else None
        if data_dir is not None:
            path = Path(data_dir)
            path.mkdir(parents=True, exist_ok=True)
            return str(path)
    except Exception:  # noqa: BLE001
        return None
    return None


def _journal_path(ctx: Any) -> Optional[str]:
    """Journal under the plugin data directory via ctx.state.data_dir when present.

    Documented Hermes durable state is ``ctx.state`` / plugin-data; no Hermes imports.
    No data_dir → None (caller sets journal_error; no /tmp fallback — API-7, C1).
    """
    data_dir = _data_dir(ctx)
    if data_dir:
        return str(Path(data_dir) / "gate-journal.jsonl")
    return None
