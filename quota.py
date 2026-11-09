"""Quota windows via public ``fetch_account_usage`` — cached ≤ 1 fetch / 180 s."""
from __future__ import annotations

import time
from dataclasses import asdict, is_dataclass
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from state import get as state_get, load_state, update_state

CACHE_TTL_SECONDS = 180
# Empty / failed fetches must not poison the plugin cache for a full TTL —
# that made stop-at-threshold miss on the real chat path after state.json wipe
# (setup script) when fetch_account_usage briefly returned no snapshot.
NEGATIVE_CACHE_TTL_SECONDS = 15
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


def _usable_usage(usage: Optional[Dict[str, Any]]) -> bool:
    """True when *usage* has at least one account window with a used_percent."""
    if not isinstance(usage, dict):
        return False
    return max_used_percent(usage) is not None


def _hermes_cached_snapshot(provider: Optional[str]) -> Any:
    """In-process Hermes usage cache (warmed by ``/usage`` / per-turn session.usage)."""
    if not provider:
        return None
    try:
        from agent.account_usage_cache import cached_account_usage

        return cached_account_usage(str(provider))
    except Exception:
        return None


def get_quota(
    *,
    provider: Optional[str] = None,
    fetch: Optional[FetchFn] = None,
    now: Optional[float] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """Return cached usage dict; fetch at most once per ``CACHE_TTL_SECONDS``.

    Empty snapshots use a short negative TTL and fall back to Hermes's in-process
    ``cached_account_usage`` so stop-at-threshold still sees real used% on the
    live ``chat --oneshot`` path after ``state.json`` deletion.
    """
    now = time.time() if now is None else now
    state = load_state()
    cached = state.get("quota") if isinstance(state.get("quota"), dict) else None
    fetched_at = float((cached or {}).get("cache_fetched_at") or 0)
    exhausted_until = state.get("exhausted_until")
    age = now - fetched_at

    if cached and not force:
        usable = _usable_usage(cached)
        ttl = CACHE_TTL_SECONDS if usable else NEGATIVE_CACHE_TTL_SECONDS
        if age < ttl:
            usage = dict(cached)
            if exhausted_until:
                usage["exhausted_until"] = exhausted_until
            usage["from_cache"] = True
            # Empty plugin cache: still try Hermes in-process snapshot before giving up.
            if not usable:
                slug_hint = provider or active_provider_slug() or usage.get("provider")
                fallback = _hermes_cached_snapshot(slug_hint)
                if fallback is not None:
                    fb = snapshot_to_dict(fallback)
                    if _usable_usage(fb):
                        fb["provider"] = slug_hint or fb.get("provider")
                        fb["cache_fetched_at"] = now
                        fb["from_cache"] = False
                        fb["from_hermes_cache"] = True
                        if exhausted_until:
                            fb["exhausted_until"] = exhausted_until
                        update_state(quota=fb, quota_provider=slug_hint)
                        return fb
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
    if not _usable_usage(usage):
        fallback = _hermes_cached_snapshot(slug)
        if fallback is not None:
            fb = snapshot_to_dict(fallback)
            if _usable_usage(fb):
                usage = fb
                usage["from_hermes_cache"] = True
                error = None
        elif _usable_usage(cached) and (now - fetched_at) < CACHE_TTL_SECONDS:
            # Prefer a still-fresh previous good snapshot over a new empty fetch.
            usage = dict(cached)
            usage["from_cache"] = True
            if exhausted_until:
                usage["exhausted_until"] = exhausted_until
            return usage

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
    try:
        from stop_park import parked_context_lines

        lines.extend(parked_context_lines(usage=usage, settings=settings_from_delegation(_delegation_for_context())))
    except Exception:
        pass
    return "\n".join(lines)


def _delegation_for_context() -> dict:
    try:
        from hermes_cli.config import load_config

        return (load_config() or {}).get("delegation") or {}
    except Exception:
        return {}


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
        # When true (default), parent file writes are blocked if routing selects the executor lane.
        "enforce_executor_lane": own.get("enforce_executor_lane", True),
    }
