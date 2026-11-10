"""Park work near quota exhaustion; resume via one-shot cron after reset."""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from quota import earliest_reset_at, get_quota, settings_from_delegation
from state import load_state, update_state
from tool_args import as_dict

DispatchFn = Callable[[str, dict], str]

# Handoff filenames under docs/ — same confinement as vendored navigate.py slugs.
_HANDOFF_SLUG = re.compile(r"^[A-Za-z0-9._-]+$")

# Block message fragment when cron.allow_agent_scheduling would be bypassed.
CRON_NO_RESUME_TIMER = (
    "no resume timer was set because this is a scheduled run"
)


def _parse_reset(reset_at: str) -> Optional[datetime]:
    text = str(reset_at or "").strip()
    if not text or text == "unknown":
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def schedule_iso(reset_at: str, buffer_seconds: int = 120) -> str:
    dt = _parse_reset(reset_at)
    if dt is None:
        # Fallback: fire in buffer seconds from now (tests may stub reset_at as ISO).
        dt = datetime.now(timezone.utc) + timedelta(seconds=max(buffer_seconds, 60))
    else:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt = dt + timedelta(seconds=int(buffer_seconds))
    return dt.isoformat()


def _truthy(value: str) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def in_cron_session() -> bool:
    """True when this process/session is a Hermes cron run (``HERMES_CRON_SESSION``).

    LIVE core sets the marker as a ContextVar in ``cron/scheduler.py``
    (``CronSessionContext.enter`` ≈ L2376–2379) so tool-layer guards stay
    meaningful; ``tools/cronjob_tools.py`` ``check_cronjob_requirements``
    (≈ L1222–1234) and ``_resolve_cron_disabled_toolsets`` (≈ L415–436) read
    the same marker / ``cron.allow_agent_scheduling`` gate. Fall back to
    process env for tests and hosts that only export the variable.
    """
    try:
        from gateway.session_context import get_session_env

        if _truthy(get_session_env("HERMES_CRON_SESSION", "")):
            return True
    except Exception:
        pass
    return _truthy(os.environ.get("HERMES_CRON_SESSION", ""))


def agent_scheduling_allowed() -> bool:
    """``cron.allow_agent_scheduling`` from Hermes config (default false)."""
    try:
        from hermes_cli.config import load_config

        cron = (load_config() or {}).get("cron") or {}
        return bool(cron.get("allow_agent_scheduling"))
    except Exception:
        return False


def cron_scheduling_blocked() -> bool:
    """Skip creating resume cron when inside a cron session without the gate."""
    return in_cron_session() and not agent_scheduling_allowed()


def _confine_handoff_slug(slug: str) -> str:
    text = str(slug or "").strip()
    if not text or not _HANDOFF_SLUG.fullmatch(text) or ".." in text or text.startswith("."):
        raise ValueError(
            f"invalid handoff slug {slug!r}: use only letters, digits, . _ - "
            "(no path separators, no '..', no leading dot)"
        )
    return text


def write_handoff(
    *,
    repo: Path,
    note: str,
    slug: str = "vibe-park",
) -> Path:
    """Write a plain markdown handoff under docs/ (slug confined to docs/)."""
    slug = _confine_handoff_slug(slug)
    root = repo.resolve()
    docs = (root / "docs").resolve()
    if docs != root and root not in docs.parents:
        raise ValueError("docs/ escapes project root (symlink?)")
    docs.mkdir(parents=True, exist_ok=True)
    path = (docs / f"{slug}.md").resolve()
    if path != docs and docs not in path.parents:
        raise ValueError(f"handoff path escapes docs/: {slug!r}")
    body = (
        f"# Vibe Relay handoff — {slug}\n\n"
        f"<!-- vibe-relay park handoff -->\n\n"
        f"{note.rstrip()}\n"
    )
    if path.is_file():
        existing = path.read_text(encoding="utf-8")
        if "## Handoff" in existing:
            path.write_text(existing.rstrip() + "\n\n## Handoff (park)\n\n" + note.rstrip() + "\n", encoding="utf-8")
            return path
    path.write_text(body, encoding="utf-8")
    return path


def park(
    *,
    repo: str,
    note: str = "",
    dispatch: Optional[DispatchFn] = None,
    reset_at: Optional[str] = None,
    buffer_seconds: Optional[int] = None,
    ctx: Any = None,
    auto: bool = False,
) -> str:
    """Write handoff and create exactly ONE one-shot cron job at reset_at + buffer.

    ``auto=True`` (stop-threshold path): idempotent per reset window — if an
    auto-park already exists for the same ``reset_at``, return SKIPPED without
    creating another cron job. Manual ``/vibe park`` keeps ``auto=False`` and
    always schedules a fresh job.

    Inside a Hermes cron session (``HERMES_CRON_SESSION``), scheduling is skipped
    unless ``cron.allow_agent_scheduling`` is true — ``ctx.dispatch_tool`` would
    otherwise bypass the core toolset denylist that normally prevents cron loops.
    """
    root = Path(repo).expanduser().resolve()
    usage = get_quota()
    settings = settings_from_delegation(_delegation_config())
    reset = reset_at or earliest_reset_at(usage) or load_state().get("exhausted_until")
    if not reset:
        return "PARK FAILED: no reset_at available from quota windows or exhausted_until."
    buf = int(buffer_seconds if buffer_seconds is not None else settings.get("park_buffer_seconds", 120))
    when = schedule_iso(str(reset), buf)

    if auto:
        existing = load_state().get("pending_park") or {}
        if (
            isinstance(existing, dict)
            and existing.get("auto")
            and str(existing.get("reset_at") or "") == str(reset)
            and (existing.get("job_id") or existing.get("cron_skipped"))
        ):
            return (
                f"PARK SKIPPED (already auto-parked for reset {reset})\n"
                f"handoff: {existing.get('handoff')}\n"
                f"cron: one-shot at {existing.get('schedule')}\n"
                f"job_id: {existing.get('job_id')}"
            )

    handoff_note = note or (
        f"Quota window nearly exhausted. Resume after reset ({reset}).\n"
        f"Last gate: {json.dumps(as_dict(load_state().get('last_gate')), default=str)}\n"
        f"Last route: {json.dumps(load_state().get('last_route') or {}, default=str)}\n"
        "Re-read this handoff, check /vibe status, then continue the open task."
    )
    handoff_path = write_handoff(repo=root, note=handoff_note)

    # Clear any previous pending_park before creating a new one-shot job.
    # Leaving it set permanently made every later `/vibe park` return SKIPPED.
    update_state(pending_park=None)

    prompt = (
        f"Vibe Relay resume-after-reset. Read the handoff at {handoff_path} and continue the parked work. "
        f"Check quota with awareness that the window has reset. Run the gate before claiming done."
    )
    args = {
        "action": "create",
        "name": "vibe-relay-resume-after-reset",
        "schedule": when,
        "prompt": prompt,
        "repeat": 1,
        # Pin the resume session to the parked repo (not the gateway process cwd).
        "workdir": str(root),
    }

    if cron_scheduling_blocked():
        record = {
            "job_id": None,
            "schedule": None,
            "reset_at": reset,
            "buffer_seconds": buf,
            "handoff": str(handoff_path),
            "create_args": args,
            "dispatch_result": "",
            "auto": bool(auto),
            "cron_skipped": "scheduled_run",
        }
        update_state(pending_park=record)
        return (
            f"PARKED ({CRON_NO_RESUME_TIMER})\n"
            f"handoff: {handoff_path}\n"
            f"cron: (not scheduled — cron session without cron.allow_agent_scheduling)\n"
            f"job_id: (none)"
        )

    dispatcher = dispatch
    if dispatcher is None and ctx is not None:
        dispatcher = lambda tool, a: ctx.dispatch_tool(tool, a)

    if dispatcher is None:
        # Record intent without a live dispatcher (tests inject a stub).
        job_id = "stub-pending"
        result_text = json.dumps({"success": True, "job_id": job_id, "schedule": when})
    else:
        result_text = dispatcher("cronjob_manage", args)
        job_id = _extract_job_id(result_text) or "unknown"

    record = {
        "job_id": job_id,
        "schedule": when,
        "reset_at": reset,
        "buffer_seconds": buf,
        "handoff": str(handoff_path),
        "create_args": args,
        "dispatch_result": (result_text or "")[:1000],
        "auto": bool(auto),
    }
    update_state(pending_park=record)
    return (
        f"PARKED\n"
        f"handoff: {handoff_path}\n"
        f"cron: one-shot at {when} (reset {reset} + {buf}s)\n"
        f"job_id: {job_id}\n"
        f"dispatch: {result_text[:400]}"
    )



def _extract_job_id(text: str) -> Optional[str]:
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data.get("job_id") or (data.get("job") or {}).get("id")
    except (json.JSONDecodeError, TypeError):
        pass
    m = re.search(r'"job_id"\s*:\s*"([^"]+)"', text or "")
    return m.group(1) if m else None


def _delegation_config() -> dict:
    try:
        from hermes_cli.config import load_config
        return (load_config() or {}).get("delegation") or {}
    except Exception:
        return {}


def status_text() -> str:
    usage = get_quota()
    state = load_state()
    settings = settings_from_delegation(_delegation_config())
    lines = ["vibe-relay status", ""]
    from quota import render_block, min_remaining_percent, max_used_percent
    lines.extend(render_block({"provider": usage.get("provider"), "usage": usage}))
    rem = min_remaining_percent(usage)
    used = max_used_percent(usage)
    lines.append(f"mode/policy: {settings.get('policy')} (self if remaining > {settings.get('self_remaining_threshold')}%)")
    if rem is not None:
        lines.append(f"remaining: {rem:.0f}% · used: {used:.0f}% · stop≥{settings.get('stop_used_percent')}%")
    route = state.get("last_route") or {}
    if route:
        lines.append(f"last route: {route.get('lane')} — {route.get('reason')}")
    gate = as_dict(state.get("last_gate"))
    if gate:
        lines.append(
            f"last gate: {'PASS' if gate.get('passed') else 'FAIL/unknown'} @ {gate.get('tree_hash')} "
            f"cmd={gate.get('command', '')[:80]}"
        )
    else:
        cmd = state.get("last_gate_command") or (state.get("last_route") or {}).get("gate")
        if cmd:
            lines.append(f"last gate: (no PASS record yet) cmd={str(cmd)[:80]}")
        else:
            lines.append("last gate: (none)")
    park = as_dict(state.get("pending_park")) if state.get("pending_park") else {}
    if park:
        lines.append(f"pending park: job {park.get('job_id')} at {park.get('schedule')} → {park.get('handoff')}")
    else:
        lines.append("pending park: (none)")
    if state.get("exhausted_until"):
        lines.append(f"exhausted_until: {state.get('exhausted_until')}")
    return "\n".join(lines)


def vibe_command(raw_args: str = "", *, ctx: Any = None, dispatch: Optional[DispatchFn] = None) -> str:
    text = (raw_args or "").strip()
    if not text or text in {"status", "show"}:
        return status_text()
    if text.startswith("park"):
        rest = text[4:].strip()
        repo = "."
        note = ""
        # park [path] [--note ...]
        if rest.startswith("--note"):
            note = rest[len("--note"):].strip().strip("'\"")
        elif rest:
            parts = rest.split(None, 1)
            repo = parts[0]
            if len(parts) > 1 and parts[1].startswith("--note"):
                note = parts[1][len("--note"):].strip().strip("'\"")
            elif len(parts) > 1:
                note = parts[1]
        return park(repo=repo, note=note, ctx=ctx, dispatch=dispatch)
    return "Usage: /vibe [status] | /vibe park [repo] [--note text]"
