"""Enforce the cheap-executor lane: block the PARENT from writing files itself.

When vibe-relay is the active delegation backend and routing selects the
executor lane (``policy=executor-always``, or ``auto`` with remaining ≤
threshold), the parent must route edits through ``delegate_task``. Children
are never blocked.

Public Hermes surface used to veto a call:

- ``ctx.register_hook("pre_tool_call", …)`` returning
  ``{"action": "block", "message": "…"}`` — consumed by
  ``hermes_cli/plugins.py::_get_pre_tool_call_directive_details``
  (LIVE Hermes ≈ L1907–1951) and dispatched from
  ``agent/tool_executor.py::_pre_tool_block`` (≈ L661–673).

Parent vs child: track live child session / subagent ids via the public
``subagent_start`` / ``subagent_stop`` hooks (no private core imports).
Also honour the process env marker ``HERMES_DELEGATED_CHILD_CONTEXT``.
"""
from __future__ import annotations

import logging
import os
import re
import threading
from typing import Any, Dict, Mapping, Optional, Set

from quota import get_quota, min_remaining_percent, settings_from_delegation
from routing import decide_from_usage

logger = logging.getLogger(__name__)

# Tools that always write files when invoked (Hermes file toolset).
_WRITE_TOOLS = frozenset({"patch", "write_file"})
_TERMINAL_TOOLS = frozenset({"terminal", "run_terminal", "shell"})

# Conservative write-shape detectors for terminal commands. Intentionally narrow:
# only in-place sed, stdout/append redirection, and tee. Reads, unittest/pytest,
# git status/diff, and plain pipes stay allowed.
_SED_INPLACE = re.compile(r"(?:^|[\s;|&])sed\s+(?:-[^\s]*i[^\s]*|--in-place(?:\b|=))", re.I)
_REDIRECT_OUT = re.compile(r"(?:^|[^0-9>&])>{1,2}\s*(?!&)")
_TEE = re.compile(r"(?:^|[\s;|&])tee(?:\s|$)")

_child_lock = threading.Lock()
_active_child_ids: Set[str] = set()


def clear_child_registry() -> None:
    """Test helper — drop tracked child ids."""
    with _child_lock:
        _active_child_ids.clear()


def note_child_start(
    *,
    child_session_id: Optional[str] = None,
    child_subagent_id: Optional[str] = None,
    **_kwargs: Any,
) -> None:
    ids = {str(x).strip() for x in (child_session_id, child_subagent_id) if x}
    if not ids:
        return
    with _child_lock:
        _active_child_ids.update(ids)


def note_child_stop(
    *,
    child_session_id: Optional[str] = None,
    child_subagent_id: Optional[str] = None,
    **_kwargs: Any,
) -> None:
    ids = {str(x).strip() for x in (child_session_id, child_subagent_id) if x}
    if not ids:
        return
    with _child_lock:
        _active_child_ids.difference_update(ids)


def is_child_caller(*, session_id: str = "", task_id: str = "", **_kwargs: Any) -> bool:
    """True when this tool call belongs to a delegate_task child."""
    if os.environ.get("HERMES_DELEGATED_CHILD_CONTEXT"):
        return True
    candidates = {str(session_id or "").strip(), str(task_id or "").strip()} - {""}
    if not candidates:
        return False
    with _child_lock:
        return bool(candidates & _active_child_ids)


def _parse_delegation_from_config_text(text: str) -> Dict[str, Any]:
    """Minimal parse of the ``delegation:`` section (no YAML dependency)."""
    out: Dict[str, Any] = {"backends": {"vibe-relay": {}}}
    # Prefer the top-level delegation.backend (first `backend:` under a delegation: block).
    m = re.search(
        r"(?ms)^delegation:\s*\n(?:^[ \t].*\n)*?[ \t]+backend:\s*([^\s#]+)",
        text,
    )
    if m:
        out["backend"] = m.group(1).strip().strip("\"'")
    else:
        m2 = re.search(r"(?m)^\s*backend:\s*(vibe-relay|vibe_relay)\s*$", text)
        if m2:
            out["backend"] = m2.group(1)
    own = out["backends"]["vibe-relay"]
    for key in (
        "policy",
        "self_remaining_threshold",
        "stop_used_percent",
        "executor_provider",
        "executor_model",
        "executor_profile",
        "park_buffer_seconds",
        "quota_provider",
        "enforce_executor_lane",
    ):
        km = re.search(rf"(?m)^\s*{re.escape(key)}:\s*(.+?)\s*$", text)
        if not km:
            continue
        raw = km.group(1).strip().strip("\"'")
        if raw.lower() in {"true", "false"}:
            own[key] = raw.lower() == "true"
        else:
            try:
                own[key] = float(raw) if "." in raw else int(raw)
            except ValueError:
                own[key] = raw
    return out


def load_delegation_config() -> Dict[str, Any]:
    """Best-effort read of ``delegation`` from the active Hermes home config."""
    try:
        from hermes_cli.config import load_config

        cfg = load_config() or {}
        delegation = cfg.get("delegation")
        if isinstance(delegation, dict) and delegation:
            return dict(delegation)
    except Exception as exc:
        logger.debug("vibe-relay: load_config failed: %s", exc)

    try:
        from hermes_constants import get_hermes_home

        path = get_hermes_home() / "config.yaml"
        if path.is_file():
            return _parse_delegation_from_config_text(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.debug("vibe-relay: config.yaml fallback failed: %s", exc)
    return {}


def vibe_relay_is_active_backend(delegation: Optional[Mapping[str, Any]] = None) -> bool:
    """True when the saved / session delegation backend is vibe-relay."""
    # Public surface: agent.delegation_backends.saved_backend()
    try:
        from agent.delegation_backends import saved_backend

        name = str(saved_backend() or "").strip().lower()
        if name in {"vibe-relay", "vibe_relay"}:
            return True
        if name and name not in {"", "default"}:
            return False
    except Exception as exc:
        logger.debug("vibe-relay: saved_backend failed: %s", exc)

    delegation = dict(delegation) if delegation is not None else load_delegation_config()
    backend = str(delegation.get("backend") or "").strip().lower()
    return backend in {"vibe-relay", "vibe_relay"}


def enforce_enabled(settings: Optional[Mapping[str, Any]] = None) -> bool:
    settings = dict(settings or {})
    raw = settings.get("enforce_executor_lane", True)
    if isinstance(raw, str):
        return raw.strip().lower() not in {"0", "false", "no", "off"}
    return bool(raw)


def parent_executor_decision(
    *,
    usage: Optional[Mapping[str, Any]] = None,
    settings: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Lane the parent would take under auto routing (policy + quota)."""
    settings = dict(settings or {})
    usage = dict(usage) if usage is not None else get_quota(provider=settings.get("quota_provider"))
    return decide_from_usage(usage, lane="auto", settings=settings)


def block_message(remaining_percent: Optional[float]) -> str:
    if remaining_percent is None:
        n = "?"
    else:
        n = str(int(round(float(remaining_percent))))
    return (
        f"vibe-relay: quota is low ({n}% left) — route this edit through "
        "delegate_task with allowed + gate; the cheap executor will make it."
    )


def terminal_command_obviously_writes(command: str) -> bool:
    """Conservative: only sed -i / >|>> / tee. Documented in README."""
    cmd = str(command or "")
    if not cmd.strip():
        return False
    return bool(_SED_INPLACE.search(cmd) or _REDIRECT_OUT.search(cmd) or _TEE.search(cmd))


def is_blocked_parent_write(tool_name: str, args: Optional[Mapping[str, Any]] = None) -> bool:
    name = str(tool_name or "").strip()
    if name in _WRITE_TOOLS:
        return True
    if name in _TERMINAL_TOOLS:
        args = args or {}
        command = args.get("command")
        if command is None:
            command = args.get("cmd")
        return terminal_command_obviously_writes(str(command or ""))
    return False


def pre_tool_call_directive(
    tool_name: str = "",
    args: Optional[Mapping[str, Any]] = None,
    task_id: str = "",
    session_id: str = "",
    *,
    delegation: Optional[Mapping[str, Any]] = None,
    usage: Optional[Mapping[str, Any]] = None,
    **_kwargs: Any,
) -> Optional[Dict[str, str]]:
    """Return a ``pre_tool_call`` block directive, or None to allow."""
    if is_child_caller(session_id=session_id, task_id=task_id):
        return None

    delegation = dict(delegation) if delegation is not None else load_delegation_config()
    if not vibe_relay_is_active_backend(delegation):
        return None

    settings = settings_from_delegation(delegation)
    if not enforce_enabled(settings):
        return None

    if not is_blocked_parent_write(tool_name, args):
        return None

    decision = parent_executor_decision(usage=usage, settings=settings)
    if decision.get("lane") != "executor":
        return None

    rem = None
    try:
        u = usage if usage is not None else get_quota(provider=settings.get("quota_provider"))
        rem = min_remaining_percent(dict(u or {}))
    except Exception:
        rem = None
    message = block_message(rem)
    logger.info("vibe-relay: blocking parent %s (%s)", tool_name, decision.get("reason"))
    return {"action": "block", "message": message}
