"""Delegation backend ``vibe-relay``."""
from __future__ import annotations

from typing import Any, Mapping

from agent.delegation_provider import DelegationProvider, DelegationRouteError

from quota import get_quota, settings_from_delegation
from routing import decide_from_usage
from state import update_state


class VibeRelayProvider(DelegationProvider):
    @property
    def name(self) -> str:
        return "vibe-relay"

    @property
    def display_name(self) -> str:
        return "Vibe Relay"

    @property
    def summary(self) -> str:
        return "Quota-aware self vs cheap executor; requires allowed + gate per task."

    def task_parameters(self, config: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
        # Deterministic for a given config (prompt-cache rule). No live quota in schema.
        return {
            "lane": {
                "type": "string",
                "enum": ["auto", "self", "executor"],
                "description": (
                    "auto: route by remaining quota vs threshold; self: parent model; "
                    "executor: configured cheap provider/model or profile."
                ),
            },
            "allowed": {
                "type": "string",
                "description": "Comma-separated path globs the executor may change (required).",
            },
            "gate": {
                "type": "string",
                "description": "Shell gate command that must PASS on the real diff (required).",
            },
        }

    def description(self, config: Mapping[str, Any]) -> str:
        # No quota numbers here — those go through pre_llm_call context.
        return (
            "Every delegated task MUST include `allowed` (path globs) and `gate` (command). "
            "Exit 0 alone is not done — the gate must report PASS on the actual diff. "
            "Receipt fields: FILES, GATE command+result, BOUNDARIES, residual risk."
        )

    def route_task(self, task: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, Any]:
        allowed = str(task.get("allowed") or "").strip()
        gate = str(task.get("gate") or "").strip()
        if not allowed or not gate:
            raise DelegationRouteError(
                "vibe-relay refuses tasks without both `allowed` (path globs) and `gate` (command). "
                "Resend with allowed and gate set."
            )

        settings = settings_from_delegation(dict(config))
        usage = get_quota(provider=settings.get("quota_provider"))
        decision = decide_from_usage(usage, lane=str(task.get("lane") or "auto"), settings=settings)
        update_state(
            last_route={
                "lane": decision["lane"],
                "reason": decision["reason"],
                "allowed": allowed,
                "gate": gate,
                "goal": str(task.get("goal") or "")[:200],
            },
            last_allowed=allowed,
            last_gate=gate,
        )

        if decision["lane"] == "self":
            return {"label": f"self ({decision['reason']})"}

        # executor route
        if settings.get("executor_profile"):
            return {
                "profile": str(settings["executor_profile"]),
                "label": f"executor-profile ({decision['reason']})",
            }
        provider = settings.get("executor_provider")
        model = settings.get("executor_model")
        if not provider and not model:
            raise DelegationRouteError(
                "Executor lane selected but no executor target configured. Set "
                "delegation.backends.vibe-relay.executor_provider + executor_model, "
                "or executor_profile."
            )
        route: dict[str, Any] = {"label": f"executor ({decision['reason']})"}
        if provider:
            route["provider"] = str(provider)
        if model:
            route["model"] = str(model)
        return route
