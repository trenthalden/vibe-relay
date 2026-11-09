"""Stop-at-threshold: block parent work + auto-park once per reset window.

When vibe-relay is the active delegation backend and routing says
``should_park`` (used ≥ ``stop_used_percent``, or exhausted), the PARENT's
``delegate_task`` and file-writing tools are blocked. Reads stay allowed.
Children are never blocked.

On the first such block, park automatically once per reset window via the
existing ``park()`` path (handoff + one one-shot cron). Repeated blocks in
the same window do not create duplicate cron jobs.

Public Hermes surface (same as executor-lane):

- ``ctx.register_hook("pre_tool_call", …)`` → ``{"action": "block", "message"}``
  via ``hermes_cli/plugins.py::_get_pre_tool_call_directive_details``
  (LIVE Hermes ≈ L1907–1951).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Mapping, Optional

from executor_lane import (
    is_blocked_parent_write,
    is_child_caller,
    load_delegation_config,
    vibe_relay_is_active_backend,
)
from park import park, schedule_iso
from quota import (
    DEFAULT_STOP_USED_PERCENT,
    clear_exhausted,
    earliest_reset_at,
    get_quota,
    max_used_percent,
    settings_from_delegation,
)
from routing import decide_from_usage
from state import load_state, update_state

logger = logging.getLogger(__name__)

DispatchFn = Callable[[str, dict], str]

_DELEGATE_TOOLS = frozenset({"delegate_task"})


def _parse_reset(reset_at: str) -> Optional[datetime]:
    text = str(reset_at or "").strip()
    if not text or text == "unknown":
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def resume_local_time(reset_at: str, buffer_seconds: int = 120) -> str:
    """Human local-time string for reset + buffer (camera-friendly)."""
    when = schedule_iso(str(reset_at), int(buffer_seconds))
    dt = _parse_reset(when)
    if dt is None:
        return when
    local = dt.astimezone()  # system local tz
    return local.strftime("%Y-%m-%d %H:%M %Z")


def maybe_clear_past_reset(usage: Mapping[str, Any]) -> Dict[str, Any]:
    """If wall-clock is past reset, drop exhausted_until so the stop can lift."""
    usage = dict(usage or {})
    stamp = usage.get("exhausted_until") or earliest_reset_at(usage)
    dt = _parse_reset(str(stamp or ""))
    if dt is None:
        return usage
    now = datetime.now(timezone.utc)
    if now >= dt:
        if usage.get("exhausted_until"):
            clear_exhausted()
            usage = dict(usage)
            usage.pop("exhausted_until", None)
    return usage


def stop_decision(
    *,
    usage: Optional[Mapping[str, Any]] = None,
    settings: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Return routing decision with ``should_park`` after past-reset cleanup.

    The stop block lifts when used drops below ``stop_used_percent``, or when
    wall-clock is past the window ``reset_at`` (even if a stale used% remains).
    """
    settings = dict(settings or {})
    usage = dict(usage) if usage is not None else get_quota(provider=settings.get("quota_provider"))
    usage = maybe_clear_past_reset(usage)
    decision = decide_from_usage(usage, lane="auto", settings=settings)
    used = max_used_percent(usage)
    stop = float(settings.get("stop_used_percent", DEFAULT_STOP_USED_PERCENT))
    reset = earliest_reset_at(usage) or usage.get("exhausted_until")
    buf = int(settings.get("park_buffer_seconds", 120))
    decision = dict(decision)
    decision["used_percent"] = used
    decision["stop_used_percent"] = stop
    decision["reset_at"] = reset
    decision["buffer_seconds"] = buf
    decision["resume_local"] = resume_local_time(str(reset or ""), buf) if reset else "?"

    dt = _parse_reset(str(reset or ""))
    if dt is not None and datetime.now(timezone.utc) >= dt:
        decision["should_park"] = False
        decision["lift_reason"] = "past reset_at"
    elif used is not None and float(used) < stop and not usage.get("exhausted_until"):
        decision["should_park"] = False
        decision["lift_reason"] = "used below stop"
    return decision


def is_stop_blocked_tool(tool_name: str, args: Optional[Mapping[str, Any]] = None) -> bool:
    name = str(tool_name or "").strip()
    if name in _DELEGATE_TOOLS:
        return True
    return is_blocked_parent_write(name, args)


def stop_block_message(
    *,
    used_percent: Optional[float],
    stop_used_percent: float,
    resume_local: str,
) -> str:
    if used_percent is None:
        n = "?"
    else:
        n = str(int(round(float(used_percent))))
    m = str(int(round(float(stop_used_percent))))
    return (
        f"vibe-relay: quota used {n}% ≥ stop {m}% — work parked; "
        f"resume scheduled at {resume_local} (reset + buffer)."
    )


def already_auto_parked_for_reset(reset_at: Optional[str]) -> bool:
    pending = load_state().get("pending_park") or {}
    if not isinstance(pending, dict) or not pending.get("auto"):
        return False
    if not reset_at:
        return bool(pending.get("job_id"))
    return str(pending.get("reset_at") or "") == str(reset_at)


def ensure_auto_park(
    *,
    repo: str,
    reset_at: Optional[str],
    buffer_seconds: int = 120,
    dispatch: Optional[DispatchFn] = None,
    ctx: Any = None,
    note: str = "",
) -> Dict[str, Any]:
    """Create at most one auto-park cron job for this reset window."""
    if already_auto_parked_for_reset(reset_at):
        pending = load_state().get("pending_park") or {}
        return {
            "created": False,
            "skipped": True,
            "pending_park": pending,
            "message": f"auto-park already set for reset {reset_at}",
        }

    handoff_note = note or (
        "Auto-parked: quota hit stop_used_percent. "
        "Do not start new work until the window resets. "
        "Re-read this handoff after resume, check /vibe status, then continue."
    )
    # Prefer a freshly reloaded park() — gateway plugin reload can leave a stale
    # park module in sys.modules that predates the ``auto=`` kwarg (R6 live miss).
    import importlib
    import park as park_mod

    try:
        park_mod = importlib.reload(park_mod)
    except Exception as exc:
        logger.debug("vibe-relay: park reload skipped: %s", exc)
    park_fn = getattr(park_mod, "park", park)
    text = park_fn(
        repo=repo,
        note=handoff_note,
        dispatch=dispatch,
        ctx=ctx,
        reset_at=reset_at,
        buffer_seconds=buffer_seconds,
        auto=True,
    )
    pending = load_state().get("pending_park") or {}
    return {
        "created": "PARKED" in text and "SKIPPED" not in text,
        "skipped": "SKIPPED" in text,
        "pending_park": pending,
        "message": text,
    }


def resolve_park_repo(explicit: Optional[str] = None) -> str:
    if explicit and str(explicit).strip():
        return str(explicit).strip()
    try:
        from acceptance import resolve_session_cwd

        return resolve_session_cwd()
    except Exception:
        from pathlib import Path

        return str(Path.cwd())


def pre_tool_call_directive(
    tool_name: str = "",
    args: Optional[Mapping[str, Any]] = None,
    task_id: str = "",
    session_id: str = "",
    *,
    delegation: Optional[Mapping[str, Any]] = None,
    usage: Optional[Mapping[str, Any]] = None,
    dispatch: Optional[DispatchFn] = None,
    ctx: Any = None,
    repo: Optional[str] = None,
    **_kwargs: Any,
) -> Optional[Dict[str, str]]:
    """Return a stop/park block directive, or None to allow / defer to other enforcers."""
    if is_child_caller(session_id=session_id, task_id=task_id):
        return None

    delegation = dict(delegation) if delegation is not None else load_delegation_config()
    if not vibe_relay_is_active_backend(delegation):
        return None

    settings = settings_from_delegation(delegation)
    decision = stop_decision(usage=usage, settings=settings)
    if not decision.get("should_park"):
        return None

    if not is_stop_blocked_tool(tool_name, args):
        return None

    # First block in this reset window → auto-park once.
    try:
        ensure_auto_park(
            repo=resolve_park_repo(repo),
            reset_at=decision.get("reset_at"),
            buffer_seconds=int(decision.get("buffer_seconds") or 120),
            dispatch=dispatch,
            ctx=ctx,
        )
    except Exception as exc:
        logger.warning("vibe-relay: auto-park failed: %s", exc)

    message = stop_block_message(
        used_percent=decision.get("used_percent"),
        stop_used_percent=float(decision.get("stop_used_percent") or DEFAULT_STOP_USED_PERCENT),
        resume_local=str(decision.get("resume_local") or "?"),
    )
    logger.info("vibe-relay: stop-park blocking parent %s", tool_name)
    update_state(last_stop={"blocked_tool": tool_name, "message": message, "decision": decision})
    return {"action": "block", "message": message}


def parked_context_lines(
    *,
    usage: Optional[Mapping[str, Any]] = None,
    settings: Optional[Mapping[str, Any]] = None,
) -> list[str]:
    """Extra ``pre_llm_call`` lines when stop is active / work is parked."""
    settings = dict(settings or {})
    try:
        decision = stop_decision(usage=usage, settings=settings)
    except Exception:
        return []
    if not decision.get("should_park"):
        return []
    resume = decision.get("resume_local") or "?"
    used = decision.get("used_percent")
    stop = decision.get("stop_used_percent")
    used_s = "?" if used is None else f"{float(used):.0f}"
    stop_s = "?" if stop is None else f"{float(stop):.0f}"
    lines = [
        f"  STOP: used {used_s}% ≥ stop {stop_s}% — parked until {resume}; do not start new work.",
    ]
    pending = load_state().get("pending_park") or {}
    if pending.get("job_id"):
        lines.append(
            f"  pending park: job {pending.get('job_id')} at {pending.get('schedule')} → {pending.get('handoff')}"
        )
    return lines
