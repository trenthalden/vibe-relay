"""Night-shift orchestration: plan files, wakes, dispatch window, morning report.

cost: pre/post_tool_call run on every tool call when a night is active; parent-stamp
writes are throttled to ≤1 / 60 s; quota fetches use quota.py TTL; at most 3 cron
jobs per night (checkpoint + quota_reset + report).
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from park import _extract_job_id, in_cron_session
from quota import (
    account_windows,
    earliest_reset_at,
    get_quota,
    min_remaining_percent,
    settings_from_delegation,
)
from state import load_state, update_state
from tool_args import coerce_tool_args

logger = logging.getLogger(__name__)

DispatchFn = Callable[[str, dict], str]
NowFn = Callable[[], datetime]
CwdResolver = Callable[[], str]
ProviderReader = Callable[[], Tuple[str, str]]
FetchUsage = Callable[..., Any]

MAX_TEXT_CHARS = 2000
MAX_ENTRIES = 500
PARENT_STAMP_THROTTLE_SECONDS = 60
_CRON_PARENT_STAMP_TOOLS = frozenset({"vibe_night_log", "vibe_accept", "delegate_task"})
NIGHT_STATE_KEY = "night"
QUEUE_STATUSES = frozenset(
    {"queued", "dispatched", "accepted", "correction", "declined", "blocked", "done"}
)
_NEVER_BLOCK_TOOLS = frozenset(
    {
        "vibe_accept",
        "vibe_night_start",
        "vibe_night_log",
        "vibe_night_status",
        "vibe_night_arm",
        "vibe_night_report",
        "read_file",
        "search_files",
        "web_search",
        "browser_navigate",
        "memory",
    }
)
_TERMINAL_TOOLS = frozenset({"terminal", "run_terminal", "shell"})
_NIGHT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

_METADATA_KEYS = (
    "id",
    "dir",
    "started_at",
    "hard_stop",
    "dispatch_stop",
    "reserve_percent",
    "provider",
    "parent_active_minutes",
    "jobs",
    "last_parent_update",
    "last_parent_source",
    "active",
)


# ─── time / parse helpers ─────────────────────────────────────────────────────


def _aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return dt


def now_local() -> datetime:
    return datetime.now().astimezone()


def format_stamp(dt: datetime) -> str:
    dt = _aware(dt).astimezone()
    return dt.isoformat(timespec="seconds")


def night_id_from_datetime(dt: datetime) -> str:
    local = _aware(dt).astimezone()
    return local.strftime("%Y%m%d-%H%M")


def parse_hard_stop(value: str, *, now: Optional[datetime] = None) -> datetime:
    """Parse ISO datetime or local HH:MM (next occurrence at or after *now*)."""
    text = str(value or "").strip()
    if not text:
        raise ValueError("hard_stop is required (ISO datetime or HH:MM)")
    clock = _aware(now or now_local())
    if re.fullmatch(r"\d{1,2}:\d{2}", text):
        hour, minute = (int(p) for p in text.split(":", 1))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError(f"invalid HH:MM hard_stop: {text!r}")
        candidate = clock.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= clock:
            candidate = candidate + timedelta(days=1)
        return candidate
    raw = text
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f"invalid hard_stop: {value!r}") from exc
    return _aware(dt).astimezone()


def parse_iso(value: str) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text or text == "unknown":
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return _aware(datetime.fromisoformat(text))
    except ValueError:
        return None


def validate_user_id(value: str, *, label: str = "id") -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label} is required")
    if ".." in text or "/" in text or "\\" in text or os.sep in text:
        raise ValueError(f"{label} must not contain '..' or path separators: {value!r}")
    if not _NIGHT_ID_RE.fullmatch(text):
        raise ValueError(
            f"{label} must be a short slug (letters, digits, . _ -), got {value!r}"
        )
    return text


def bound_text(value: Any, *, field: str) -> str:
    text = str(value if value is not None else "")
    if len(text) > MAX_TEXT_CHARS:
        raise ValueError(f"{field} exceeds {MAX_TEXT_CHARS} characters ({len(text)})")
    return text


# ─── repo / hermes-home guards (D1) ───────────────────────────────────────────


def hermes_home_root() -> Path:
    """Profile Hermes home = plugin_data_dir('vibe-relay').parent.parent (no hermes_constants)."""
    from plugins.plugin_storage import plugin_data_dir

    return plugin_data_dir("vibe-relay").resolve().parent.parent


def default_cwd_resolver() -> str:
    try:
        from agent.runtime_cwd import resolve_agent_cwd

        return str(resolve_agent_cwd())
    except Exception:
        return str(Path.cwd())


def resolve_repo_root(cwd_resolver: Optional[CwdResolver] = None) -> Path:
    cwd = Path((cwd_resolver or default_cwd_resolver)()).expanduser().resolve()
    try:
        run = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"not a git work tree ({exc})") from exc
    if run.returncode != 0:
        detail = (run.stderr or run.stdout or "").strip() or f"exit {run.returncode}"
        raise ValueError(f"not a git work tree: {detail}")
    root = Path(run.stdout.strip()).resolve()
    home = hermes_home_root()
    # Refuse repo-root equal to or inside the Hermes home (D1).
    if root == home or home in root.parents:
        raise ValueError(
            f"refusing night files inside Hermes home ({home}): use a project git work tree"
        )
    return root


def ensure_night_dir(repo_root: Path, night_id: str) -> Path:
    night_id = validate_user_id(night_id, label="night id")
    vibe = (repo_root / ".vibe-relay").resolve()
    if vibe != repo_root and repo_root not in vibe.parents:
        raise ValueError(".vibe-relay escapes repo-root (symlink?)")
    vibe.mkdir(parents=True, exist_ok=True)
    gitignore = vibe / ".gitignore"
    if not gitignore.is_file():
        gitignore.write_text("*\n", encoding="utf-8")
    night_dir = (vibe / f"night-{night_id}").resolve()
    if night_dir != vibe and vibe not in night_dir.parents:
        raise ValueError(f"night dir escapes repo-root: {night_id!r}")
    if repo_root not in night_dir.parents and night_dir != repo_root:
        raise ValueError(f"night dir resolves outside repo-root: {night_dir}")
    night_dir.mkdir(parents=True, exist_ok=True)
    return night_dir


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


# ─── plan files ───────────────────────────────────────────────────────────────


COMMON_MD = """# COMMON — shared worker rules (night shift)

Closed brief required: INTENT / DECISIONS / ALLOWED / FORBIDDEN / TASK / DONE MEANS / GATE.

- One writer per lane / worktree; file ownership is disjoint across lanes.
- Receipt must include FILES / GATE / BOUNDARIES / OPEN / RISKS.
- Use only the executor command the repository approves.
- No approval-bypass flags unless the repository explicitly allows them.
- Parent accepts by reading the actual diff and running the gate itself.
- One run + at most one correction per portion.
"""


def empty_night_plan(
    *,
    night_id: str,
    title: str,
    owner_instructions: str,
    lanes: List[dict],
    hard_stop: str,
    dispatch_stop: str,
    reserve_percent: float,
    stop_margin_minutes: int,
    parent_active_minutes: int,
    provider: str,
    repo_root: str,
    started_at: str,
) -> dict:
    return {
        "id": night_id,
        "title": title,
        "owner_instructions": owner_instructions,
        "lanes": lanes,
        "hard_stop": hard_stop,
        "dispatch_stop": dispatch_stop,
        "reserve_percent": reserve_percent,
        "stop_margin_minutes": stop_margin_minutes,
        "parent_active_minutes": parent_active_minutes,
        "provider": provider,
        "repo_root": repo_root,
        "started_at": started_at,
        "queue": [],
        "decisions": [],
        "events": [],
        "jobs": [],
        "active": True,
    }


def render_night_plan(plan: Mapping[str, Any]) -> str:
    lines = [
        f"# NIGHT PLAN — {plan.get('id')}",
        "",
        f"Title: {plan.get('title') or '(none)'}",
        f"Repo: {plan.get('repo_root')}",
        f"Started: {plan.get('started_at')}",
        f"Hard stop: {plan.get('hard_stop')}",
        f"Dispatch stop: {plan.get('dispatch_stop')}",
        f"Reserve: {plan.get('reserve_percent')}%",
        f"Provider: {plan.get('provider')}",
        f"Parent active window: {plan.get('parent_active_minutes')} minutes",
        "",
        "## Owner instructions",
        "",
        str(plan.get("owner_instructions") or "(none)"),
        "",
        "## Lanes",
        "",
    ]
    for lane in plan.get("lanes") or []:
        lines.append(
            f"- **{lane.get('name')}** worker=`{lane.get('worker')}` owns=`{lane.get('owns')}`"
        )
    if not plan.get("lanes"):
        lines.append("(none)")
    lines.extend(["", "## Queue", ""])
    queue = plan.get("queue") or []
    if not queue:
        lines.append("(empty)")
    else:
        lines.append("| id | lane | status | portion | evidence |")
        lines.append("|---|---|---|---|---|")
        for item in queue:
            portion = str(item.get("portion") or "").replace("|", "\\|").replace("\n", " ")
            evidence = str(item.get("evidence") or "").replace("|", "\\|").replace("\n", " ")
            lines.append(
                f"| {item.get('id')} | {item.get('lane')} | {item.get('status')} | "
                f"{portion[:120]} | {evidence[:120]} |"
            )
    lines.extend(["", "## Decisions", ""])
    decisions = plan.get("decisions") or []
    if not decisions:
        lines.append("(none)")
    else:
        for d in decisions:
            lines.append(
                f"- [{d.get('at')}] choice={d.get('choice')} — doubt={d.get('doubt')} "
                f"— reversible={d.get('why_reversible')}"
            )
    lines.extend(["", "## Timeline", ""])
    events = plan.get("events") or []
    if not events:
        lines.append("(none)")
    else:
        for e in events:
            lines.append(f"- [{e.get('at')}] {e.get('text')}")
    lines.extend(["", "## Wakes (recorded)", ""])
    jobs = plan.get("jobs") or []
    if not jobs:
        lines.append("(none)")
    else:
        for j in jobs:
            lines.append(
                f"- {j.get('boundary')}: job_id={j.get('job_id')} run_at={j.get('run_at')}"
            )
    lines.append("")
    return "\n".join(lines)


def load_night_json(night_dir: Path) -> dict:
    path = night_dir / "night.json"
    if not path.is_file():
        raise ValueError(f"night.json missing under {night_dir}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("night.json is not an object")
    return data


def save_night_plan(night_dir: Path, plan: dict) -> None:
    atomic_write(night_dir / "night.json", json.dumps(plan, indent=2, sort_keys=True, default=str) + "\n")
    atomic_write(night_dir / "NIGHT-PLAN.md", render_night_plan(plan))


def metadata_from_plan(plan: Mapping[str, Any], *, extra: Optional[Mapping[str, Any]] = None) -> dict:
    """Profile state payload — metadata only (D2). Never copies plan text fields."""
    meta = {
        "id": plan.get("id"),
        "dir": plan.get("dir") or plan.get("night_dir"),
        "started_at": plan.get("started_at"),
        "hard_stop": plan.get("hard_stop"),
        "dispatch_stop": plan.get("dispatch_stop"),
        "reserve_percent": plan.get("reserve_percent"),
        "provider": plan.get("provider"),
        "parent_active_minutes": plan.get("parent_active_minutes"),
        "jobs": list(plan.get("jobs") or []),
        "last_parent_update": plan.get("last_parent_update"),
        "last_parent_source": plan.get("last_parent_source"),
        "active": bool(plan.get("active", True)),
    }
    if extra:
        for key in _METADATA_KEYS:
            if key in extra:
                meta[key] = extra[key]
    # Strip any accidental private fields
    return {k: meta.get(k) for k in _METADATA_KEYS}


def active_night_meta() -> Optional[dict]:
    raw = load_state().get(NIGHT_STATE_KEY)
    if not isinstance(raw, dict) or not raw.get("active"):
        return None
    return raw


def _delegation_config() -> dict:
    try:
        from hermes_cli.config import load_config_readonly

        return (load_config_readonly() or {}).get("delegation") or {}
    except Exception:
        return {}


def night_settings(delegation: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Read plugin settings; night.* from backends.vibe-relay.night (or forwarded keys)."""
    config = dict(delegation) if delegation is not None else _delegation_config()
    settings = dict(settings_from_delegation(config))
    backends = config.get("backends") if isinstance(config, dict) else None
    own = backends.get("vibe-relay") if isinstance(backends, dict) else None
    own = own if isinstance(own, dict) else {}
    nested = own.get("night") if isinstance(own.get("night"), dict) else {}
    blocked_tools = nested.get("blocked_tools", settings.get("blocked_tools", []))
    patterns = nested.get(
        "blocked_command_patterns", settings.get("blocked_command_patterns", [])
    )
    if not isinstance(blocked_tools, list):
        blocked_tools = []
    if not isinstance(patterns, list):
        patterns = []
    settings["blocked_tools"] = [str(t) for t in blocked_tools]
    settings["blocked_command_patterns"] = [str(p) for p in patterns]
    return settings


def default_provider_reader() -> Tuple[str, str]:
    try:
        from hermes_cli.config import load_config_readonly

        model = (load_config_readonly() or {}).get("model") or {}
        provider = str(model.get("provider") or "").strip()
        name = str(model.get("default") or model.get("model") or "").strip() or "<model>"
        return provider, name
    except Exception:
        return "", "<model>"


# ─── quota helpers for night ──────────────────────────────────────────────────


def weekly_remaining_percent(usage: Mapping[str, Any]) -> Optional[float]:
    for w in account_windows(dict(usage)):
        label = str(w.get("label") or "").lower()
        if "week" in label and w.get("used_percent") is not None:
            return 100.0 - float(w["used_percent"])
    return None


def earliest_session_reset_at(usage: Mapping[str, Any]) -> Optional[str]:
    stamps = []
    for w in account_windows(dict(usage)):
        label = str(w.get("label") or "").lower()
        if "session" in label and w.get("reset_at"):
            stamps.append(str(w["reset_at"]))
    if stamps:
        return min(stamps)
    return earliest_reset_at(dict(usage))


def quota_unknown(usage: Mapping[str, Any]) -> bool:
    if not usage:
        return True
    if usage.get("unavailable_reason") and not usage.get("windows"):
        return True
    if weekly_remaining_percent(usage) is None and min_remaining_percent(dict(usage)) is None:
        return True
    return False


# ─── start / log / status ─────────────────────────────────────────────────────


def start_night(
    *,
    hard_stop: str,
    reserve_percent: float = 10.0,
    stop_margin_minutes: int = 30,
    parent_active_minutes: int = 40,
    title: str = "",
    owner_instructions: str = "",
    lanes: Optional[Sequence[Mapping[str, Any]]] = None,
    quota_provider: Optional[str] = None,
    replace: bool = False,
    now: Optional[datetime] = None,
    cwd_resolver: Optional[CwdResolver] = None,
    provider_reader: Optional[ProviderReader] = None,
) -> dict:
    clock = _aware(now or now_local())
    existing = active_night_meta()
    if existing and not replace:
        raise ValueError(
            f"active night already exists ({existing.get('id')}); pass replace=true to replace"
        )

    title_s = bound_text(title, field="title")
    instructions = bound_text(owner_instructions, field="owner_instructions")
    lane_list: List[dict] = []
    for raw in lanes or []:
        if not isinstance(raw, dict):
            raise ValueError("each lane must be an object {name, worker, owns}")
        lane_list.append(
            {
                "name": bound_text(raw.get("name") or "", field="lane.name"),
                "worker": bound_text(raw.get("worker") or "", field="lane.worker"),
                "owns": bound_text(raw.get("owns") or "", field="lane.owns"),
            }
        )

    stop_at = parse_hard_stop(hard_stop, now=clock)
    margin = max(0, int(stop_margin_minutes))
    dispatch_stop = stop_at - timedelta(minutes=margin)
    night_id = night_id_from_datetime(clock)
    repo = resolve_repo_root(cwd_resolver)
    night_dir = ensure_night_dir(repo, night_id)

    provider = str(quota_provider or "").strip()
    if not provider:
        reader = provider_reader or default_provider_reader
        provider = reader()[0] or ""
    if not provider:
        try:
            from quota import active_provider_slug

            provider = active_provider_slug() or ""
        except Exception:
            provider = ""

    started = format_stamp(clock)
    plan = empty_night_plan(
        night_id=night_id,
        title=title_s,
        owner_instructions=instructions,
        lanes=lane_list,
        hard_stop=format_stamp(stop_at),
        dispatch_stop=format_stamp(dispatch_stop),
        reserve_percent=float(reserve_percent),
        stop_margin_minutes=margin,
        parent_active_minutes=int(parent_active_minutes),
        provider=provider,
        repo_root=str(repo),
        started_at=started,
    )
    plan["dir"] = str(night_dir)
    plan["night_dir"] = str(night_dir)
    plan["last_parent_update"] = started
    plan["last_parent_source"] = "session"
    save_night_plan(night_dir, plan)
    atomic_write(night_dir / "COMMON.md", COMMON_MD)

    meta = metadata_from_plan(plan)
    update_state(**{NIGHT_STATE_KEY: meta})
    return {
        "ok": True,
        "id": night_id,
        "dir": str(night_dir),
        "hard_stop": plan["hard_stop"],
        "dispatch_stop": plan["dispatch_stop"],
        "provider": provider,
        "plan": str(night_dir / "NIGHT-PLAN.md"),
    }


def _load_active_plan() -> Tuple[dict, dict]:
    meta = active_night_meta()
    if not meta:
        raise ValueError("no active night")
    night_dir = Path(str(meta.get("dir") or ""))
    if not night_dir.is_dir():
        raise ValueError(f"night dir missing: {night_dir}")
    plan = load_night_json(night_dir)
    plan["dir"] = str(night_dir)
    # Keep metadata stamp fields on the plan for status
    for key in ("last_parent_update", "last_parent_source", "jobs"):
        if meta.get(key) is not None and key not in plan:
            plan[key] = meta.get(key)
    if meta.get("jobs") is not None:
        plan["jobs"] = list(meta.get("jobs") or plan.get("jobs") or [])
    plan["last_parent_update"] = meta.get("last_parent_update") or plan.get("last_parent_update")
    plan["last_parent_source"] = meta.get("last_parent_source") or plan.get("last_parent_source")
    return meta, plan


def log_night(
    *,
    kind: str,
    now: Optional[datetime] = None,
    **fields: Any,
) -> dict:
    kind_s = str(kind or "").strip().lower()
    if kind_s not in {"decision", "queue", "event"}:
        raise ValueError("kind must be decision|queue|event")
    clock = _aware(now or now_local())
    stamp = format_stamp(clock)
    meta, plan = _load_active_plan()
    night_dir = Path(plan["dir"])

    if kind_s == "decision":
        if len(plan.get("decisions") or []) >= MAX_ENTRIES:
            raise ValueError(f"decision log exceeds {MAX_ENTRIES} entries")
        entry = {
            "at": stamp,
            "choice": bound_text(fields.get("choice") or "", field="choice"),
            "doubt": bound_text(fields.get("doubt") or "", field="doubt"),
            "why_reversible": bound_text(
                fields.get("why_reversible") or "", field="why_reversible"
            ),
        }
        plan.setdefault("decisions", []).append(entry)
    elif kind_s == "event":
        if len(plan.get("events") or []) >= MAX_ENTRIES:
            raise ValueError(f"event log exceeds {MAX_ENTRIES} entries")
        entry = {"at": stamp, "text": bound_text(fields.get("text") or "", field="text")}
        plan.setdefault("events", []).append(entry)
    else:
        qid = validate_user_id(str(fields.get("id") or ""), label="queue id")
        status = str(fields.get("status") or "queued").strip().lower()
        if status not in QUEUE_STATUSES:
            raise ValueError(
                f"status must be one of {', '.join(sorted(QUEUE_STATUSES))}, got {status!r}"
            )
        queue = list(plan.get("queue") or [])
        existing_idx = next((i for i, q in enumerate(queue) if q.get("id") == qid), None)
        depends = fields.get("depends") or []
        if not isinstance(depends, list):
            depends = [depends]
        item = {
            "id": qid,
            "lane": bound_text(fields.get("lane") or "", field="lane"),
            "portion": bound_text(fields.get("portion") or "", field="portion"),
            "depends": [str(d) for d in depends],
            "status": status,
            "evidence": bound_text(fields.get("evidence") or "", field="evidence"),
            "updated_at": stamp,
        }
        if existing_idx is None:
            if len(queue) >= MAX_ENTRIES:
                raise ValueError(f"queue exceeds {MAX_ENTRIES} entries")
            if len(plan.get("events") or []) >= MAX_ENTRIES:
                raise ValueError(f"event log exceeds {MAX_ENTRIES} entries")
            item["created_at"] = stamp
            queue.append(item)
            plan.setdefault("events", []).append(
                {"at": stamp, "text": f"{qid} added ({status})"}
            )
        else:
            prev = queue[existing_idx]
            old_status = str(prev.get("status") or "")
            if old_status != status:
                if len(plan.get("events") or []) >= MAX_ENTRIES:
                    raise ValueError(f"event log exceeds {MAX_ENTRIES} entries")
                plan.setdefault("events", []).append(
                    {"at": stamp, "text": f"{qid} {old_status} → {status}"}
                )
            item["created_at"] = prev.get("created_at") or stamp
            # Keep previous fields when not provided
            for key in ("lane", "portion", "depends", "evidence"):
                if not fields.get(key) and key != "depends":
                    item[key] = prev.get(key) or item[key]
                elif key == "depends" and fields.get("depends") is None:
                    item["depends"] = prev.get("depends") or []
            queue[existing_idx] = item
        plan["queue"] = queue

    save_night_plan(night_dir, plan)
    # Refresh metadata jobs/dir only — never copy private text into state
    meta = metadata_from_plan(
        {**plan, "dir": str(night_dir)},
        extra={
            "last_parent_update": meta.get("last_parent_update"),
            "last_parent_source": meta.get("last_parent_source"),
            "jobs": meta.get("jobs") or plan.get("jobs") or [],
            "active": True,
        },
    )
    update_state(**{NIGHT_STATE_KEY: meta})
    return {"ok": True, "kind": kind_s, "id": plan.get("id"), "at": stamp}


def status_night(
    *,
    now: Optional[datetime] = None,
    fetch_usage: Optional[FetchUsage] = None,
    force_quota: bool = False,
) -> dict:
    clock = _aware(now or now_local())
    meta = load_state().get(NIGHT_STATE_KEY)
    in_cron = in_cron_session()
    if not isinstance(meta, dict) or not meta.get("active"):
        return {
            "active": False,
            "id": None,
            "dir": None,
            "now": format_stamp(clock),
            "in_cron": in_cron,
        }

    night_dir = Path(str(meta.get("dir") or ""))
    plan: dict = {}
    if night_dir.is_dir() and (night_dir / "night.json").is_file():
        plan = load_night_json(night_dir)

    dispatch_stop = parse_iso(str(meta.get("dispatch_stop") or ""))
    minutes_to = None
    dispatch_open = True
    if dispatch_stop is not None:
        delta = (dispatch_stop - clock).total_seconds() / 60.0
        minutes_to = delta
        dispatch_open = clock < dispatch_stop

    last_parent = parse_iso(str(meta.get("last_parent_update") or ""))
    parent_age = None
    parent_active = False
    active_minutes = float(meta.get("parent_active_minutes") or 40)
    if last_parent is not None:
        parent_age = (clock - last_parent).total_seconds() / 60.0
        parent_active = parent_age < active_minutes

    provider = str(meta.get("provider") or "")
    usage = get_quota(provider=provider or None, fetch=fetch_usage, force=force_quota)
    rem = min_remaining_percent(usage)
    session_reset = earliest_session_reset_at(usage)
    unknown = quota_unknown(usage) or rem is None

    counts = {s: 0 for s in sorted(QUEUE_STATUSES)}
    for item in plan.get("queue") or []:
        st = str(item.get("status") or "")
        if st in counts:
            counts[st] += 1

    jobs = []
    for j in meta.get("jobs") or plan.get("jobs") or []:
        if isinstance(j, dict):
            jobs.append(
                {
                    "boundary": j.get("boundary"),
                    "job_id": j.get("job_id"),
                    "run_at": j.get("run_at"),
                }
            )

    return {
        "active": True,
        "id": meta.get("id"),
        "dir": meta.get("dir"),
        "now": format_stamp(clock),
        "hard_stop": meta.get("hard_stop"),
        "dispatch_stop": meta.get("dispatch_stop"),
        "minutes_to_dispatch_stop": minutes_to,
        "dispatch_open": dispatch_open,
        "last_parent_update": meta.get("last_parent_update"),
        "parent_age_minutes": parent_age,
        "parent_active": parent_active,
        "quota": {
            "provider": usage.get("provider") or provider,
            "remaining_percent": rem,
            "session_reset": session_reset,
            "unknown": unknown,
        },
        "queue_counts": counts,
        "jobs": jobs,
        "in_cron": in_cron,
    }


# ─── continuation prompt / arm / report ───────────────────────────────────────


def continuation_prompt(
    *,
    night_id: str,
    repo_root: str,
    boundary: str,
) -> str:
    header = (
        f"Vibe Relay night-shift continuation.\n"
        f"Night id: {night_id}\n"
        f"Repo: {repo_root}\n"
        f"Boundary: {boundary}\n\n"
    )
    if boundary == "report":
        return (
            header
            + "Call vibe_night_report, reply with its summary, and stop. "
            + "Always produce the report; never dispatch work or exit early."
        )
    return (
        header
        + "1. Call vibe_night_status.\n"
        + "2. If parent_active is true, reply exactly one line: \"parent active — exiting\" and stop.\n"
        + "3. If dispatch_open is false, call vibe_night_report, reply with its summary, and stop.\n"
        + "4. Otherwise read NIGHT-PLAN.md and COMMON.md under the night dir. "
        + "Before dispatching, confirm no worker still owns a lane. "
        + "Accept finished portions with the gate. "
        + "Dispatch the next queued portion within its budget. "
        + "Log every decision and status with vibe_night_log. "
        + "Stop when remaining quota reaches the reserve. "
        + "Never arm new cron jobs from a continuation wake."
    )


def _cron_jobs_from_list(result_text: str) -> Optional[Dict[str, dict]]:
    """Parse cron list output into job records; None means unusable output."""
    text = result_text or ""
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None

    candidates: List[Any] = []
    if isinstance(data, dict):
        for key in ("jobs", "items", "result"):
            if isinstance(data.get(key), list):
                candidates.extend(data[key])
        if not candidates and data.get("job_id"):
            candidates.append(data)
        if not candidates and data.get("id"):
            candidates.append(data)
        if not candidates and not any(isinstance(data.get(key), list) for key in ("jobs", "items", "result")):
            return None
    elif isinstance(data, list):
        candidates = data
    else:
        return None

    jobs: Dict[str, dict] = {}
    for item in candidates:
        if isinstance(item, dict):
            jid = item.get("job_id") or item.get("id")
            if jid:
                jobs[str(jid)] = item
        elif isinstance(item, str):
            jobs[item] = {"job_id": item}
    return jobs


def _cron_job_ids_from_list(result_text: str) -> set:
    """Compatibility wrapper for callers that only need live job IDs."""
    jobs = _cron_jobs_from_list(result_text)
    if jobs is not None:
        return set(jobs)
    # Preserve tolerance for text with embedded job identifiers.
    text = result_text or ""
    ids = set()
    for m in re.finditer(r'"job_id"\s*:\s*"([^"]+)"', text):
        ids.add(m.group(1))
    for m in re.finditer(r'"id"\s*:\s*"([^"]+)"', text):
        ids.add(m.group(1))
    return ids


def _in_cron_guard(ctx: Any = None) -> bool:
    if in_cron_session():
        return True
    if ctx is not None:
        try:
            if ctx.current_cron_execution():
                return True
        except Exception:
            pass
    return False


DEFAULT_ARM_BOUNDARIES = ("checkpoint", "quota_reset", "report")
CHECKPOINT_PROXIMITY_MINUTES = 30
CHECKPOINT_PAST_SLACK_MINUTES = 5


def _planned_quota_reset_wake(
    usage: Mapping[str, Any],
    *,
    reset_buffer_minutes: int,
    dispatch_stop: Optional[datetime],
) -> Optional[datetime]:
    """Return the quota_reset run_at if it would be scheduled (before dispatch_stop)."""
    reset = earliest_session_reset_at(usage)
    if not reset:
        return None
    base = parse_iso(reset)
    if base is None:
        return None
    run_at = base + timedelta(minutes=max(0, int(reset_buffer_minutes)))
    if dispatch_stop is not None and run_at >= dispatch_stop:
        return None
    return run_at


def _skip_wake_for_session_reserve(
    *,
    run_at: datetime,
    usage: Mapping[str, Any],
    reserve: float,
) -> Optional[str]:
    """Skip wakes that fire before the next session reset while session/min remaining ≤ reserve."""
    rem = min_remaining_percent(dict(usage))
    if rem is None or rem > reserve:
        return None
    reset = earliest_session_reset_at(usage)
    reset_dt = parse_iso(reset) if reset else None
    if reset_dt is None:
        return None
    if run_at < reset_dt:
        return (
            f"remaining {rem:.0f}% ≤ reserve {reserve:.0f}% and wake "
            f"{format_stamp(run_at)} is before session reset {format_stamp(reset_dt)}"
        )
    return None


def arm_night(
    *,
    boundaries: Optional[Sequence[str]] = None,
    reset_buffer_minutes: int = 3,
    report_lead_minutes: int = 30,
    checkpoint_after_minutes: int = 90,
    allow_provider_mismatch: bool = False,
    dispatch: Optional[DispatchFn] = None,
    fetch_usage: Optional[FetchUsage] = None,
    provider_reader: Optional[ProviderReader] = None,
    now: Optional[datetime] = None,
    ctx: Any = None,
) -> dict:
    if _in_cron_guard(ctx):
        raise ValueError(
            "refusing to arm night wakes inside a cron execution (D5); arm from the live session"
        )
    clock = _aware(now or now_local())
    meta, plan = _load_active_plan()
    night_dir = Path(plan["dir"])
    repo_root = str(plan.get("repo_root") or "")
    night_id = str(plan.get("id") or meta.get("id") or "")
    provider = str(meta.get("provider") or plan.get("provider") or "")
    reserve = float(meta.get("reserve_percent") if meta.get("reserve_percent") is not None else 10)

    usage = get_quota(provider=provider or None, fetch=fetch_usage, force=True)
    if quota_unknown(usage):
        raise ValueError(
            "refusing to arm: quota unknown/unavailable for the night provider — "
            "cannot schedule a wake into an exhausted or opaque window"
        )
    weekly = weekly_remaining_percent(usage)
    # Weekly window is optional (session-only / spend-balance providers are fine).
    # Refuse only when a weekly window EXISTS and remaining ≤ reserve.
    if weekly is not None and weekly <= reserve:
        raise ValueError(
            f"refusing to arm: weekly remaining {weekly:.0f}% ≤ reserve {reserve:.0f}%"
        )

    reader = provider_reader or default_provider_reader
    profile_provider, profile_model = reader()
    night_provider = provider or str(usage.get("provider") or "")
    mismatch_note = ""
    if profile_provider and night_provider and profile_provider != night_provider:
        if not allow_provider_mismatch:
            raise ValueError(
                f"profile default provider {profile_provider!r} differs from night quota "
                f"provider {night_provider!r}. Re-arm with allow_provider_mismatch=true, then run: "
                f"hermes cron edit <job-id> --provider {night_provider} --model {profile_model}"
            )
        mismatch_note = (
            f"provider mismatch allowed; after create run: "
            f"hermes cron edit <job-id> --provider {night_provider} --model {profile_model}"
        )

    wanted = list(boundaries) if boundaries is not None else list(DEFAULT_ARM_BOUNDARIES)
    wanted = [str(b).strip() for b in wanted if str(b).strip()]
    if not wanted:
        wanted = list(DEFAULT_ARM_BOUNDARIES)

    dispatcher = dispatch
    if dispatcher is None and ctx is not None:
        dispatcher = lambda tool, a: ctx.dispatch_tool(tool, a)
    if dispatcher is None:
        raise ValueError("arm requires a dispatch callable (ctx.dispatch_tool)")

    list_text = dispatcher("cronjob_manage", {"action": "list"})
    live_ids = _cron_job_ids_from_list(list_text)

    recorded = {j.get("boundary"): dict(j) for j in (meta.get("jobs") or []) if isinstance(j, dict)}
    results = []
    skipped = []
    new_jobs = []

    dispatch_stop = parse_iso(str(meta.get("dispatch_stop") or ""))
    hard_stop = parse_iso(str(meta.get("hard_stop") or ""))
    started_at = parse_iso(str(meta.get("started_at") or plan.get("started_at") or ""))
    planned_quota_reset = _planned_quota_reset_wake(
        usage,
        reset_buffer_minutes=reset_buffer_minutes,
        dispatch_stop=dispatch_stop,
    )

    for boundary in wanted:
        existing = recorded.get(boundary)
        if existing and existing.get("job_id") and str(existing["job_id"]) in live_ids:
            entry = {
                "boundary": boundary,
                "job_id": existing.get("job_id"),
                "run_at": existing.get("run_at"),
                "verified": True,
                "kept": True,
            }
            results.append(entry)
            new_jobs.append({k: existing.get(k) for k in ("boundary", "job_id", "run_at")})
            continue

        run_at_dt: Optional[datetime] = None
        if boundary == "quota_reset":
            reset = earliest_session_reset_at(usage)
            if not reset:
                skipped.append({"boundary": boundary, "reason": "no session reset_at"})
                continue
            base = parse_iso(reset)
            if base is None:
                skipped.append({"boundary": boundary, "reason": f"unparseable reset_at {reset!r}"})
                continue
            run_at_dt = base + timedelta(minutes=max(0, int(reset_buffer_minutes)))
            if dispatch_stop is not None and run_at_dt >= dispatch_stop:
                skipped.append(
                    {
                        "boundary": boundary,
                        "reason": (
                            f"quota_reset {format_stamp(run_at_dt)} is at/after "
                            f"dispatch_stop {meta.get('dispatch_stop')}"
                        ),
                    }
                )
                continue
        elif boundary == "report":
            if hard_stop is None:
                skipped.append({"boundary": boundary, "reason": "no hard_stop"})
                continue
            run_at_dt = hard_stop - timedelta(minutes=max(0, int(report_lead_minutes)))
        elif boundary == "checkpoint":
            if started_at is None:
                skipped.append({"boundary": boundary, "reason": "no started_at"})
                continue
            run_at_dt = started_at + timedelta(minutes=max(0, int(checkpoint_after_minutes)))
            if run_at_dt < clock + timedelta(minutes=CHECKPOINT_PAST_SLACK_MINUTES):
                skipped.append(
                    {
                        "boundary": boundary,
                        "reason": (
                            f"checkpoint {format_stamp(run_at_dt)} is already in the past "
                            f"(< now + {CHECKPOINT_PAST_SLACK_MINUTES} min)"
                        ),
                    }
                )
                continue
            if dispatch_stop is not None and run_at_dt >= dispatch_stop:
                skipped.append(
                    {
                        "boundary": boundary,
                        "reason": (
                            f"checkpoint {format_stamp(run_at_dt)} is at/after "
                            f"dispatch_stop {meta.get('dispatch_stop')}"
                        ),
                    }
                )
                continue
            if planned_quota_reset is not None:
                proximity = abs((run_at_dt - planned_quota_reset).total_seconds()) / 60.0
                if proximity <= CHECKPOINT_PROXIMITY_MINUTES:
                    skipped.append(
                        {
                            "boundary": boundary,
                            "reason": (
                                f"checkpoint {format_stamp(run_at_dt)} within "
                                f"{CHECKPOINT_PROXIMITY_MINUTES} min of quota_reset "
                                f"{format_stamp(planned_quota_reset)}"
                            ),
                        }
                    )
                    continue
        else:
            skipped.append({"boundary": boundary, "reason": f"unknown boundary {boundary!r}"})
            continue

        reserve_skip = _skip_wake_for_session_reserve(
            run_at=run_at_dt, usage=usage, reserve=reserve
        )
        if reserve_skip:
            skipped.append({"boundary": boundary, "reason": reserve_skip})
            continue

        schedule = format_stamp(run_at_dt.astimezone())
        prompt = continuation_prompt(
            night_id=night_id, repo_root=repo_root, boundary=boundary
        )
        create_args = {
            "action": "create",
            "schedule": schedule,
            "prompt": prompt,
            "name": f"vibe-night {boundary} {night_id}",
            "repeat": 1,
            "skills": ["vibe-relay:night-shift"],
            "workdir": repo_root,
        }
        # D3: never pass deliver / model / provider / pinned
        result_text = dispatcher("cronjob_manage", create_args)
        job_id = _extract_job_id(result_text) or "unknown"
        list_text2 = dispatcher("cronjob_manage", {"action": "list"})
        verified = job_id in _cron_job_ids_from_list(list_text2)
        entry = {
            "boundary": boundary,
            "job_id": job_id,
            "run_at": schedule,
            "verified": verified,
        }
        if mismatch_note:
            entry["owner_followup"] = (
                f"hermes cron edit {job_id} --provider {night_provider} --model {profile_model}"
            )
        results.append(entry)
        new_jobs.append({"boundary": boundary, "job_id": job_id, "run_at": schedule})

    # Preserve jobs for boundaries not requested this call
    kept_boundaries = {j["boundary"] for j in new_jobs}
    for boundary, existing in recorded.items():
        if boundary not in kept_boundaries and boundary not in wanted:
            new_jobs.append(
                {k: existing.get(k) for k in ("boundary", "job_id", "run_at")}
            )

    plan["jobs"] = new_jobs
    save_night_plan(night_dir, plan)
    meta = metadata_from_plan(
        {**plan, "dir": str(night_dir)},
        extra={
            "jobs": new_jobs,
            "last_parent_update": meta.get("last_parent_update"),
            "last_parent_source": meta.get("last_parent_source"),
            "active": True,
        },
    )
    update_state(**{NIGHT_STATE_KEY: meta})
    out = {"ok": True, "jobs": results, "skipped": skipped}
    if mismatch_note:
        out["note"] = mismatch_note
    return out


def render_morning_report(
    plan: Mapping[str, Any],
    *,
    usage: Optional[Mapping[str, Any]] = None,
    gate: Optional[Mapping[str, Any]] = None,
    wake_observations: Optional[Mapping[str, str]] = None,
    now: Optional[datetime] = None,
) -> str:
    clock = _aware(now or now_local())
    queue = list(plan.get("queue") or [])
    counts = {s: 0 for s in sorted(QUEUE_STATUSES)}
    for item in queue:
        st = str(item.get("status") or "")
        if st in counts:
            counts[st] += 1
    outcome = ", ".join(f"{k}={v}" for k, v in counts.items() if v)
    lines = [
        f"# MORNING REPORT — {plan.get('id')}",
        "",
        f"Generated: {format_stamp(clock)}",
        f"Window: {plan.get('started_at')} → {plan.get('hard_stop')}",
        f"Dispatch stop: {plan.get('dispatch_stop')}",
        f"Outcome: {outcome or 'no queue items'}",
        "",
        "## Queue",
        "",
        "| id | lane | status | evidence |",
        "|---|---|---|---|",
    ]
    if not queue:
        lines.append("| (empty) | | | |")
    else:
        for item in queue:
            ev = str(item.get("evidence") or "").replace("|", "\\|").replace("\n", " ")
            lines.append(
                f"| {item.get('id')} | {item.get('lane')} | {item.get('status')} | {ev[:200]} |"
            )

    lines.extend(["", "## Last acceptance gate", ""])
    if gate and (gate.get("passed") is not None or gate.get("tree_hash")):
        lines.append(
            f"passed={bool(gate.get('passed'))} tree_hash={gate.get('tree_hash') or '?'}"
        )
    else:
        lines.append("(none)")

    lines.extend(["", "## Decisions", ""])
    decisions = plan.get("decisions") or []
    if not decisions:
        lines.append("(none)")
    else:
        for d in decisions:
            lines.append(
                f"- [{d.get('at')}] {d.get('choice')} "
                f"(doubt={d.get('doubt')}; reversible={d.get('why_reversible')})"
            )

    lines.extend(["", "## Timeline", ""])
    events = plan.get("events") or []
    if not events:
        lines.append("(none)")
    else:
        for e in events:
            lines.append(f"- [{e.get('at')}] {e.get('text')}")

    lines.extend(["", "## Wakes", ""])
    obs = wake_observations or {}
    jobs = plan.get("jobs") or []
    if not jobs:
        lines.append("(none recorded)")
    else:
        for j in jobs:
            jid = str(j.get("job_id") or "")
            mark = obs.get(jid, "not observed (cron list unavailable)")
            lines.append(
                f"- {j.get('boundary')}: job_id={jid} planned={j.get('run_at')} — {mark}"
            )

    lines.extend(["", "## Not done / not verified", ""])
    pending = [
        q
        for q in queue
        if str(q.get("status") or "") not in {"accepted", "done"}
    ]
    if not pending:
        lines.append("(none)")
    else:
        for q in pending:
            lines.append(f"- {q.get('id')} [{q.get('status')}] lane={q.get('lane')}")

    lines.extend(["", "## Quota at report time", ""])
    usage = dict(usage or {})
    rem = min_remaining_percent(usage)
    lines.append(f"provider: {usage.get('provider') or plan.get('provider') or '?'}")
    lines.append(f"remaining_percent: {rem if rem is not None else 'unknown'}")
    lines.append(f"session_reset: {earliest_session_reset_at(usage) or 'unknown'}")
    lines.append("")
    return "\n".join(lines)


def report_night(
    *,
    now: Optional[datetime] = None,
    fetch_usage: Optional[FetchUsage] = None,
    dispatch: Optional[DispatchFn] = None,
    ctx: Any = None,
) -> dict:
    clock = _aware(now or now_local())
    meta, plan = _load_active_plan()
    night_dir = Path(plan["dir"])
    provider = str(meta.get("provider") or plan.get("provider") or "")
    usage = get_quota(provider=provider or None, fetch=fetch_usage, force=False)

    gate = load_state().get("last_gate")
    if not isinstance(gate, dict):
        gate = None
    else:
        gate = {"passed": gate.get("passed"), "tree_hash": gate.get("tree_hash")}

    wake_obs: Dict[str, str] = {}
    dispatcher = dispatch
    if dispatcher is None and ctx is not None:
        dispatcher = lambda tool, a: ctx.dispatch_tool(tool, a)
    listed_jobs: Optional[Dict[str, dict]] = None
    if dispatcher is not None:
        try:
            listed_jobs = _cron_jobs_from_list(
                dispatcher("cronjob_manage", {"action": "list"})
            )
        except Exception as exc:
            logger.warning("vibe-relay night report: cron list failed: %s", exc)
    for j in plan.get("jobs") or meta.get("jobs") or []:
        if not isinstance(j, dict):
            continue
        jid = str(j.get("job_id") or "")
        if listed_jobs is None:
            observation = "not observed (cron list unavailable)"
        else:
            live_job = listed_jobs.get(jid)
            if live_job is None:
                observation = "not observed (job no longer listed)"
            elif live_job.get("last_run_at"):
                observation = f"ran {live_job['last_run_at']} ({live_job.get('last_status') or 'unknown'})"
                if live_job.get("last_error"):
                    observation += f"; error: {str(live_job['last_error'])[:120]}"
            else:
                observation = f"scheduled, next {live_job.get('next_run_at') or 'unknown'}"
        wake_obs[jid] = observation

    body = render_morning_report(
        plan,
        usage=usage,
        gate=gate,
        wake_observations=wake_obs,
        now=clock,
    )
    path = night_dir / "MORNING-REPORT.md"
    atomic_write(path, body)

    counts = {s: 0 for s in sorted(QUEUE_STATUSES)}
    for item in plan.get("queue") or []:
        st = str(item.get("status") or "")
        if st in counts:
            counts[st] += 1
    summary = (
        f"night {plan.get('id')}: "
        + ", ".join(f"{k}={v}" for k, v in counts.items() if v)
        + ("" if any(counts.values()) else "no queue items")
    )
    return {"path": str(path), "summary": summary}


def stop_night(
    *,
    now: Optional[datetime] = None,
    dispatch: Optional[DispatchFn] = None,
    fetch_usage: Optional[FetchUsage] = None,
    ctx: Any = None,
) -> dict:
    if _in_cron_guard(ctx):
        raise ValueError("refusing cron create/remove inside a cron execution (D5)")
    meta, plan = _load_active_plan()
    dispatcher = dispatch
    if dispatcher is None and ctx is not None:
        dispatcher = lambda tool, a: ctx.dispatch_tool(tool, a)

    removed = []
    for j in list(meta.get("jobs") or plan.get("jobs") or []):
        if not isinstance(j, dict):
            continue
        jid = j.get("job_id")
        if not jid or dispatcher is None:
            continue
        try:
            dispatcher("cronjob_manage", {"action": "remove", "job_id": jid})
            removed.append(jid)
        except Exception as exc:
            logger.warning("vibe-relay night stop: remove %s failed: %s", jid, exc)

    report = report_night(now=now, fetch_usage=fetch_usage, dispatch=dispatch, ctx=ctx)
    night_dir = Path(plan["dir"])
    plan["active"] = False
    plan["jobs"] = []
    save_night_plan(night_dir, plan)
    meta = metadata_from_plan(
        {**plan, "dir": str(night_dir)},
        extra={
            "jobs": [],
            "active": False,
            "last_parent_update": meta.get("last_parent_update"),
            "last_parent_source": meta.get("last_parent_source"),
        },
    )
    update_state(**{NIGHT_STATE_KEY: meta})
    return {"ok": True, "removed_jobs": removed, "report": report}


# ─── hooks ────────────────────────────────────────────────────────────────────


def pre_tool_call_directive(
    *,
    tool_name: str = "",
    args: Optional[Mapping[str, Any]] = None,
    now: Optional[datetime] = None,
    fetch_usage: Optional[FetchUsage] = None,
    delegation: Optional[Mapping[str, Any]] = None,
    usage: Optional[Mapping[str, Any]] = None,
    **kwargs: Any,
) -> Optional[Dict[str, str]]:
    meta = active_night_meta()
    if not meta:
        return None
    name = str(tool_name or "").strip()
    if name in _NEVER_BLOCK_TOOLS or name.startswith("vibe_night_"):
        return None

    clock = _aware(now or now_local())
    settings = night_settings(delegation)
    blocked_tools = set(settings.get("blocked_tools") or [])
    patterns = list(settings.get("blocked_command_patterns") or [])

    dispatch_stop = parse_iso(str(meta.get("dispatch_stop") or ""))
    window_closed = dispatch_stop is not None and clock >= dispatch_stop
    reserve = float(meta.get("reserve_percent") if meta.get("reserve_percent") is not None else 10)

    rem = None
    if usage is not None:
        rem = min_remaining_percent(dict(usage))
    else:
        try:
            u = get_quota(
                provider=str(meta.get("provider") or "") or None,
                fetch=fetch_usage,
                force=False,
            )
            rem = min_remaining_percent(u)
        except Exception as exc:
            logger.warning("vibe-relay night pre_tool_call quota failed open: %s", exc)

    reserve_hit = rem is not None and rem <= reserve

    if not window_closed and not reserve_hit:
        return None

    args = coerce_tool_args(args)
    should_block = False
    if name == "delegate_task" or name in blocked_tools:
        should_block = True
    elif name in _TERMINAL_TOOLS and patterns:
        cmd = str(args.get("command") or args.get("cmd") or "")
        for pat in patterns:
            try:
                if re.search(pat, cmd):
                    should_block = True
                    break
            except re.error as exc:
                logger.warning("vibe-relay night: bad blocked_command_pattern %r: %s", pat, exc)

    if not should_block:
        return None

    if window_closed:
        why = (
            f"night dispatch window closed at {meta.get('dispatch_stop')} "
            f"(hard_stop {meta.get('hard_stop')} − stop margin)"
        )
    else:
        why = (
            f"night quota remaining {rem:.0f}% ≤ reserve {reserve:.0f}%"
        )
    return {
        "action": "block",
        "message": (
            f"vibe-relay night: blocked {name} — {why}. "
            "Already-running work is not stopped."
        ),
    }


def post_tool_call_stamp(
    *,
    tool_name: str = "",
    now: Optional[datetime] = None,
    ctx: Any = None,
    **kwargs: Any,
) -> None:
    """Refresh parent liveness; cron wakes count only orchestrator actions as activity."""
    meta = active_night_meta()
    if not meta:
        return
    name = str(tool_name or "").strip()
    if name == "vibe_night_status":
        return
    in_cron = _in_cron_guard(ctx)
    if in_cron and name not in _CRON_PARENT_STAMP_TOOLS:
        return
    clock = _aware(now or now_local())
    last = parse_iso(str(meta.get("last_parent_update") or ""))
    if last is not None:
        age = (clock - last).total_seconds()
        if age < PARENT_STAMP_THROTTLE_SECONDS:
            return
    source = "cron" if in_cron else "session"
    meta = dict(meta)
    meta["last_parent_update"] = format_stamp(clock)
    meta["last_parent_source"] = source
    # Persist metadata only
    clean = {k: meta.get(k) for k in _METADATA_KEYS}
    update_state(**{NIGHT_STATE_KEY: clean})


# ─── tool handlers / command ──────────────────────────────────────────────────


def _json(data: Any) -> str:
    return json.dumps(data, indent=2, sort_keys=True, default=str)


def vibe_night_start(args: dict, **kwargs) -> str:
    args = coerce_tool_args(args)
    try:
        result = start_night(
            hard_stop=str(args.get("hard_stop") or ""),
            reserve_percent=float(args.get("reserve_percent", 10)),
            stop_margin_minutes=int(args.get("stop_margin_minutes", 30)),
            parent_active_minutes=int(args.get("parent_active_minutes", 40)),
            title=str(args.get("title") or ""),
            owner_instructions=str(args.get("owner_instructions") or ""),
            lanes=args.get("lanes") or [],
            quota_provider=args.get("quota_provider"),
            replace=bool(args.get("replace", False)),
            cwd_resolver=kwargs.get("cwd_resolver"),
            provider_reader=kwargs.get("provider_reader"),
            now=kwargs.get("now"),
        )
        return _json(result)
    except Exception as exc:
        return f"ERROR: {exc}"


def vibe_night_log(args: dict, **kwargs) -> str:
    args = coerce_tool_args(args)
    try:
        result = log_night(
            kind=str(args.get("kind") or ""),
            now=kwargs.get("now"),
            choice=args.get("choice"),
            doubt=args.get("doubt"),
            why_reversible=args.get("why_reversible"),
            id=args.get("id"),
            lane=args.get("lane"),
            portion=args.get("portion"),
            depends=args.get("depends"),
            status=args.get("status"),
            evidence=args.get("evidence"),
            text=args.get("text"),
        )
        return _json(result)
    except Exception as exc:
        return f"ERROR: {exc}"


def vibe_night_status(args: dict, **kwargs) -> str:
    coerce_tool_args(args)
    try:
        return _json(
            status_night(
                now=kwargs.get("now"),
                fetch_usage=kwargs.get("fetch_usage"),
            )
        )
    except Exception as exc:
        return f"ERROR: {exc}"


def vibe_night_arm(args: dict, **kwargs) -> str:
    args = coerce_tool_args(args)
    try:
        result = arm_night(
            boundaries=args.get("boundaries"),
            reset_buffer_minutes=int(args.get("reset_buffer_minutes", 3)),
            report_lead_minutes=int(args.get("report_lead_minutes", 30)),
            checkpoint_after_minutes=int(args.get("checkpoint_after_minutes", 90)),
            allow_provider_mismatch=bool(args.get("allow_provider_mismatch", False)),
            dispatch=kwargs.get("dispatch"),
            fetch_usage=kwargs.get("fetch_usage"),
            provider_reader=kwargs.get("provider_reader"),
            now=kwargs.get("now"),
            ctx=kwargs.get("ctx"),
        )
        return _json(result)
    except Exception as exc:
        return f"ERROR: {exc}"


def vibe_night_report(args: dict, **kwargs) -> str:
    coerce_tool_args(args)
    try:
        return _json(
            report_night(
                now=kwargs.get("now"),
                fetch_usage=kwargs.get("fetch_usage"),
                dispatch=kwargs.get("dispatch"),
                ctx=kwargs.get("ctx"),
            )
        )
    except Exception as exc:
        return f"ERROR: {exc}"


def night_command(
    raw_args: str = "",
    *,
    ctx: Any = None,
    dispatch: Optional[DispatchFn] = None,
    fetch_usage: Optional[FetchUsage] = None,
    cwd_resolver: Optional[CwdResolver] = None,
    provider_reader: Optional[ProviderReader] = None,
    now: Optional[datetime] = None,
) -> str:
    text = (raw_args or "").strip()
    if not text or text.split()[0] == "status":
        return vibe_night_status({}, now=now, fetch_usage=fetch_usage)
    parts = text.split()
    cmd = parts[0].lower()
    if cmd == "start":
        if len(parts) < 2:
            return "Usage: /night start <HH:MM|ISO> [reserve%]"
        reserve = float(parts[2]) if len(parts) >= 3 else 10.0
        return vibe_night_start(
            {"hard_stop": parts[1], "reserve_percent": reserve},
            now=now,
            cwd_resolver=cwd_resolver,
            provider_reader=provider_reader,
        )
    if cmd == "arm":
        return vibe_night_arm(
            {},
            now=now,
            dispatch=dispatch,
            fetch_usage=fetch_usage,
            provider_reader=provider_reader,
            ctx=ctx,
        )
    if cmd == "report":
        return vibe_night_report(
            {}, now=now, fetch_usage=fetch_usage, dispatch=dispatch, ctx=ctx
        )
    if cmd == "stop":
        try:
            return _json(
                stop_night(
                    now=now,
                    dispatch=dispatch,
                    fetch_usage=fetch_usage,
                    ctx=ctx,
                )
            )
        except Exception as exc:
            return f"ERROR: {exc}"
    return "Usage: /night start <HH:MM|ISO> [reserve%] | status | arm | report | stop"


VIBE_NIGHT_START_SCHEMA = {
    "name": "vibe_night_start",
    "description": (
        "Start a night shift: create .vibe-relay/night-* plan files and activate metadata. "
        "One active night per profile unless replace=true."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "hard_stop": {
                "type": "string",
                "description": 'ISO datetime or local HH:MM (next occurrence).',
            },
            "reserve_percent": {"type": "number", "description": "Stop dispatch at this remaining %."},
            "stop_margin_minutes": {"type": "integer"},
            "parent_active_minutes": {"type": "integer"},
            "title": {"type": "string"},
            "owner_instructions": {"type": "string"},
            "lanes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "worker": {"type": "string"},
                        "owns": {"type": "string"},
                    },
                },
            },
            "quota_provider": {"type": "string"},
            "replace": {"type": "boolean"},
        },
        "required": ["hard_stop"],
    },
}

VIBE_NIGHT_LOG_SCHEMA = {
    "name": "vibe_night_log",
    "description": "Append or update a night decision, queue item, or event; re-renders NIGHT-PLAN.md.",
    "parameters": {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": ["decision", "queue", "event"]},
            "choice": {"type": "string"},
            "doubt": {"type": "string"},
            "why_reversible": {"type": "string"},
            "id": {"type": "string"},
            "lane": {"type": "string"},
            "portion": {"type": "string"},
            "depends": {"type": "array", "items": {"type": "string"}},
            "status": {
                "type": "string",
                "enum": sorted(QUEUE_STATUSES),
            },
            "evidence": {"type": "string"},
            "text": {"type": "string"},
        },
        "required": ["kind"],
    },
}

VIBE_NIGHT_STATUS_SCHEMA = {
    "name": "vibe_night_status",
    "description": "Read-only night status JSON (does not refresh the parent activity stamp).",
    "parameters": {"type": "object", "properties": {}, "required": []},
}

VIBE_NIGHT_ARM_SCHEMA = {
    "name": "vibe_night_arm",
    "description": (
        "Arm one-shot continuation wakes (checkpoint / quota_reset / report) from the live "
        "session. Refuses inside cron; never sets model/provider/deliver."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "boundaries": {
                "type": "array",
                "items": {"type": "string"},
                "description": 'Default ["checkpoint","quota_reset","report"].',
            },
            "reset_buffer_minutes": {"type": "integer"},
            "report_lead_minutes": {"type": "integer"},
            "checkpoint_after_minutes": {
                "type": "integer",
                "description": "Minutes after night start for the checkpoint safety-net wake (default 90).",
            },
            "allow_provider_mismatch": {"type": "boolean"},
        },
        "required": [],
    },
}

VIBE_NIGHT_REPORT_SCHEMA = {
    "name": "vibe_night_report",
    "description": "Write MORNING-REPORT.md from night.json + gate/quota state; marks nothing done.",
    "parameters": {"type": "object", "properties": {}, "required": []},
}
