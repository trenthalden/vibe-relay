"""Quota-aware lane decision for vibe-relay delegation."""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from quota import (
    DEFAULT_SELF_REMAINING_THRESHOLD,
    DEFAULT_STOP_USED_PERCENT,
    max_used_percent,
    min_remaining_percent,
)


def decide_lane(
    *,
    lane: str = "auto",
    policy: str = "auto",
    remaining_percent: Optional[float],
    used_percent: Optional[float] = None,
    exhausted: bool = False,
    self_remaining_threshold: float = DEFAULT_SELF_REMAINING_THRESHOLD,
    stop_used_percent: float = DEFAULT_STOP_USED_PERCENT,
) -> Dict[str, Any]:
    """Return ``{lane, reason, should_park}``.

    Policies:
    - ``auto`` (default): remaining > threshold → self, else executor.
    - ``executor-always``: always executor (unless lane forces self).
    Explicit ``lane`` of ``self`` / ``executor`` wins over auto; ``auto`` follows policy.
    """
    lane = (lane or "auto").strip().lower()
    policy = (policy or "auto").strip().lower()
    should_park = False
    if used_percent is not None and float(used_percent) >= float(stop_used_percent):
        should_park = True
    if exhausted:
        should_park = True

    if lane == "self":
        return {"lane": "self", "reason": "lane=self", "should_park": should_park}
    if lane == "executor":
        return {"lane": "executor", "reason": "lane=executor", "should_park": should_park}

    # lane == auto
    if policy in {"executor-always", "executor_always", "always-executor"}:
        return {"lane": "executor", "reason": "policy=executor-always", "should_park": should_park}

    if remaining_percent is None:
        # Fail soft toward executor when quota unknown (preserve strong model).
        return {"lane": "executor", "reason": "quota unavailable → executor", "should_park": should_park}

    if float(remaining_percent) > float(self_remaining_threshold):
        return {
            "lane": "self",
            "reason": f"remaining {remaining_percent:.0f}% > {self_remaining_threshold:.0f}%",
            "should_park": should_park,
        }
    return {
        "lane": "executor",
        "reason": f"remaining {remaining_percent:.0f}% ≤ {self_remaining_threshold:.0f}%",
        "should_park": should_park,
    }


def decide_from_usage(
    usage: Mapping[str, Any],
    *,
    lane: str = "auto",
    settings: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    settings = dict(settings or {})
    rem = min_remaining_percent(dict(usage))
    used = max_used_percent(dict(usage))
    return decide_lane(
        lane=lane,
        policy=str(settings.get("policy") or "auto"),
        remaining_percent=rem,
        used_percent=used,
        exhausted=bool(usage.get("exhausted_until")),
        self_remaining_threshold=float(
            settings.get("self_remaining_threshold", DEFAULT_SELF_REMAINING_THRESHOLD)
        ),
        stop_used_percent=float(settings.get("stop_used_percent", DEFAULT_STOP_USED_PERCENT)),
    )
