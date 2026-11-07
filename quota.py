"""Quota windows via public ``fetch_account_usage`` — cached ≤ 1 fetch / 180 s."""
from __future__ import annotations

import time
from dataclasses import asdict, is_dataclass
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from state import get as state_get, load_state, update_state

CACHE_TTL_SECONDS = 180
DEFAULT_SELF_REMAINING_THRESHOLD = 51.0  # remaining % → self; else executor
DEFAULT_STOP_USED_PERCENT = 90.0

FetchFn = Callable[..., Any]


def snapshot_to_dict(snapshot: Any) -> Dict[str, Any]:
    """AccountUsageSnapshot → plain dict with ISO dates (from account-usage patterns)."""
    if snapshot is None:
        return {"available": False, "unavailable_reason": "no snapshot"}
    data = asdict(snapshot) if is_dataclass(snapshot) else dict(snapshot)
    for window in data.get("windows") or []:
        reset_at = window.get("reset_at")
        if isinstance(reset_at, datetime):
            window["reset_at"] = reset_at.isoformat()
    fetched = data.get("fetched_at")
    if isinstance(fetched, datetime):
        data["fetched_at"] = fetched.isoformat()
    data["available"] = bool(data.get("windows") or data.get("details")) and not data.get("unavailable_reason")
    return data


def render_block(block: Dict[str, Any]) -> List[str]:
    """Human lines for one provider usage block (subset of account-usage render_block)."""
    lines: List[str] = []
    usage = block.get("usage") or block
    provider = block.get("provider") or usage.get("provider") or "?"
    lines.append(f"{provider}")
    if usage.get("unavailable_reason") and not usage.get("windows"):
        lines.append(f"  limits: unavailable ({usage.get('unavailable_reason')})")
        return lines
    for w in usage.get("windows") or []:
        used = w.get("used_percent")
        rem = None if used is None else 100.0 - float(used)
        label = w.get("label") or "window"
        reset = w.get("reset_at") or "?"
        if used is None:
            lines.append(f"  {label}: used n/a · resets {reset}")
        else:
            lines.append(f"  {label}: {float(used):.0f}% used ({rem:.0f}% remaining) · resets {reset}")
    if usage.get("exhausted_until"):
        lines.append(f"  exhausted until {usage['exhausted_until']} (api_request_error observe)")
    return lines


def account_windows(usage: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Windows that scope to the whole account (ignore model-only caps when marked)."""
    out = []
    for w in usage.get("windows") or []:
        if str(w.get("scope") or "account").lower() == "model":
            continue
        out.append(w)
    return out or list(usage.get("windows") or [])


def max_used_percent(usage: Dict[str, Any]) -> Optional[float]:
    vals = [float(w["used_percent"]) for w in account_windows(usage) if w.get("used_percent") is not None]
    return max(vals) if vals else None


def min_remaining_percent(usage: Dict[str, Any]) -> Optional[float]:
    used = max_used_percent(usage)
    return None if used is None else 100.0 - used


def earliest_reset_at(usage: Dict[str, Any]) -> Optional[str]:
    stamps = [str(w["reset_at"]) for w in account_windows(usage) if w.get("reset_at")]
    return min(stamps) if stamps else None


def active_provider_slug() -> Optional[str]:
    try:
        from hermes_cli.config import load_config
        cfg = load_config() or {}
        model = cfg.get("model") or {}
        slug = str(model.get("provider") or "").strip()
        return slug or None
    except Exception:
        return None


def default_fetch(provider: Optional[str]) -> Any:
    from agent.account_usage import fetch_account_usage
    return fetch_account_usage(provider, read_only=True)


def get_quota(
    *,
    provider: Optional[str] = None,
    fetch: Optional[FetchFn] = None,
    now: Optional[float] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """Return cached usage dict; fetch at most once per ``CACHE_TTL_SECONDS``."""
    now = time.time() if now is None else now
    state = load_state()
    cached = state.get("quota") if isinstance(state.get("quota"), dict) else None
    fetched_at = float((cached or {}).get("cache_fetched_at") or 0)
    exhausted_until = state.get("exhausted_until")

    if cached and not force and (now - fetched_at) < CACHE_TTL_SECONDS:
        usage = dict(cached)
        if exhausted_until:
            usage["exhausted_until"] = exhausted_until
        usage["from_cache"] = True
        return usage

    slug = provider or active_provider_slug() or (cached or {}).get("provider")
    fetcher = fetch or default_fetch
    snapshot = None
    error = None
    try:
        snapshot = fetcher(slug) if slug else None
    except Exception as exc:
        error = str(exc)

    usage = snapshot_to_dict(snapshot)
    if slug:
        usage["provider"] = slug
    if error:
        usage["unavailable_reason"] = usage.get("unavailable_reason") or error
        usage["available"] = False
    usage["cache_fetched_at"] = now
    usage["from_cache"] = False
    if exhausted_until:
        usage["exhausted_until"] = exhausted_until

    update_state(quota=usage, quota_provider=slug)
    return usage


def mark_exhausted_until_reset(reset_at: Optional[str] = None) -> Dict[str, Any]:
    """Observer path for ``api_request_error`` — no mutation of core retry classification."""
    usage = load_state().get("quota") if isinstance(load_state().get("quota"), dict) else {}
    stamp = reset_at or earliest_reset_at(usage) or "unknown"
    return update_state(exhausted_until=stamp, exhausted_marked_at=time.time())


def clear_exhausted() -> None:
    from state import save_state
    state = load_state()
    state.pop("exhausted_until", None)
    state.pop("exhausted_marked_at", None)
    save_state(state)


def context_block(usage: Optional[Dict[str, Any]] = None) -> str:
    usage = usage or get_quota()
    lines = ["[vibe-relay quota]"]
    lines.extend(render_block({"provider": usage.get("provider"), "usage": usage}))
    rem = min_remaining_percent(usage)
    if rem is not None:
        lines.append(f"  remaining (min): {rem:.0f}%")
    if state_get("exhausted_until"):
        lines.append(f"  policy: exhausted until {state_get('exhausted_until')}")
    return "\n".join(lines)


def settings_from_delegation(config: Optional[dict] = None) -> Dict[str, Any]:
    """Read ``delegation.backends.vibe-relay`` with defaults."""
    config = config or {}
    backends = config.get("backends") if isinstance(config, dict) else None
    own = backends.get("vibe-relay") if isinstance(backends, dict) else None
    own = own if isinstance(own, dict) else {}
    return {
        "policy": str(own.get("policy") or "auto"),
        "self_remaining_threshold": float(own.get("self_remaining_threshold", DEFAULT_SELF_REMAINING_THRESHOLD)),
        "stop_used_percent": float(own.get("stop_used_percent", DEFAULT_STOP_USED_PERCENT)),
        "executor_provider": own.get("executor_provider"),
        "executor_model": own.get("executor_model"),
        "executor_profile": own.get("executor_profile"),
        "park_buffer_seconds": int(own.get("park_buffer_seconds", 120)),
        "quota_provider": own.get("quota_provider"),
    }
