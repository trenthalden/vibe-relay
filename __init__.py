"""vibe-relay — vibecoding without burning your best model."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)
PACKAGE = Path(__file__).resolve().parent

# Hyphenated plugin dir is not a normal import package name; put the plugin root
# (and vendored DF) on sys.path so sibling modules resolve under Hermes load,
# the validate capability probe, and unittest discovery alike.
for _p in (PACKAGE, PACKAGE / "vendor"):
    _s = str(_p)
    if _s not in sys.path:
        sys.path.insert(0, _s)


def _on_pre_llm_call(session_id="", **kwargs):
    from quota import context_block
    try:
        return {"context": context_block()}
    except Exception as exc:
        logger.debug("vibe-relay pre_llm_call: %s", exc)
        return None


def _on_api_request_error(**kwargs):
    """Observe only — mark exhausted until reset; do not alter core retry classification."""
    try:
        from quota import earliest_reset_at, get_quota, mark_exhausted_until_reset
        err = str(kwargs.get("error") or kwargs.get("reason") or "")
        status = kwargs.get("status_code")
        # Heuristic observe: rate-limit / quota language or 429
        lower = err.lower()
        interesting = status == 429 or any(
            s in lower for s in ("rate limit", "quota", "usage limit", "too many requests", "exhausted")
        )
        if not interesting:
            return None
        usage = get_quota()
        mark_exhausted_until_reset(earliest_reset_at(usage))
    except Exception as exc:
        logger.debug("vibe-relay api_request_error: %s", exc)
    return None


def _on_post_tool_call(tool_name="", args=None, result=None, **kwargs):
    if tool_name not in {"terminal", "run_terminal", "shell"}:
        return None
    try:
        from acceptance import capture_terminal_gate
        capture_terminal_gate(args=args or {}, result=str(result or ""))
    except Exception as exc:
        logger.debug("vibe-relay post_tool_call: %s", exc)
    return None


def _on_pre_verify(changed_paths=None, coding=False, **kwargs):
    try:
        from acceptance import pre_verify_directive
        return pre_verify_directive(changed_paths_arg=changed_paths)
    except Exception as exc:
        logger.debug("vibe-relay pre_verify: %s", exc)
        return None


def _on_subagent_stop(**kwargs):
    """Observer fallback: record child completion; cannot issue continue."""
    try:
        from state import update_state
        update_state(
            last_subagent={
                "child_session_id": kwargs.get("child_session_id"),
                "child_status": kwargs.get("child_status"),
                "child_summary": str(kwargs.get("child_summary") or "")[:300],
            }
        )
    except Exception as exc:
        logger.debug("vibe-relay subagent_stop: %s", exc)
    return None


# Agent Workshop skill pack (W3b). df-* stay under skills/ for host discovery but are
# registered by the vendored DEV Framework (df-import / df-catch-up) — do not double-register.
WORKSHOP_SKILL_NAMES = (
    "agent-architecture-audit",
    "agent-build-debug-loop",
    "agent-field-acceptance",
    "agent-harness-design",
    "agent-maintenance",
    "context-budget-audit",
    "frozen-working-tree-review",
    "known-errors",
    "lean-executor-orchestration",
    "memory-engineering",
    "product-aware-engineering-releases",
    "subagent-driven-development",
    "systematic-debugging",
    "test-driven-development",
)


def _register_workshop_skills(ctx) -> None:
    """Register portable Agent Workshop skills as ``vibe-relay:<name>`` via ``skill_view``."""
    skills_dir = PACKAGE / "skills"
    for name in WORKSHOP_SKILL_NAMES:
        path = skills_dir / name / "SKILL.md"
        if not path.is_file():
            logger.warning("vibe-relay: workshop skill missing: %s", path)
            continue
        try:
            ctx.register_skill(name=name, path=path)
        except Exception as exc:
            logger.debug("vibe-relay: skill %s not registered: %s", name, exc)


def register(ctx) -> None:
    from acceptance import VIBE_ACCEPT_SCHEMA, vibe_accept
    from park import vibe_command
    from provider import VibeRelayProvider

    ctx.register_delegation_provider(VibeRelayProvider())

    ctx.register_tool(
        name="vibe_accept",
        toolset="vibe_relay",
        schema=VIBE_ACCEPT_SCHEMA,
        handler=vibe_accept,
        description=VIBE_ACCEPT_SCHEMA["description"][:120],
        emoji="✅",
    )

    # Vendored DEV Framework tools (skip if a separate dev-framework plugin already registered them).
    # Skills: DF registers df-import / df-catch-up from vendor/; workshop skills register below.
    try:
        import dev_framework as df  # vendor/ on sys.path

        outcome = df.register(ctx, register_skills=True, register_cli=False, skip_existing_tools=True)
        if outcome.get("skipped"):
            logger.info(
                "vibe-relay: skipped DF tools already present: %s "
                "(disable the standalone dev-framework plugin to use the vendored copy)",
                ", ".join(outcome["skipped"]),
            )
    except Exception as exc:
        logger.warning("vibe-relay: vendored DF register failed: %s", exc)

    _register_workshop_skills(ctx)

    ctx.register_hook("pre_llm_call", _on_pre_llm_call)
    ctx.register_hook("api_request_error", _on_api_request_error)
    ctx.register_hook("post_tool_call", _on_post_tool_call)
    ctx.register_hook("pre_verify", _on_pre_verify)
    ctx.register_hook("subagent_stop", _on_subagent_stop)

    def _cmd(raw_args: str = "", **kwargs):
        return vibe_command(raw_args, ctx=ctx)

    ctx.register_command(
        name="vibe",
        handler=_cmd,
        description="Quota / mode / last gate / park status; `/vibe park` parks until reset.",
        args_hint="[status|park [repo] [--note text]]",
    )

    try:
        ctx.register_automation_blueprint(
            "resume-after-reset",
            title="Resume after quota reset",
            description=(
                "One-shot wake after a usage window resets: read the vibe-relay park handoff and continue."
            ),
            schedule_template="in 1h",
            prompt_template=(
                "Vibe Relay resume-after-reset. Find the latest docs/*vibe-park*.md handoff in the "
                "working project, read it, check quota, and continue the parked task. Run the gate "
                "before claiming done."
            ),
            category="coding",
            tags=("vibe-relay", "quota", "resume"),
        )
    except Exception as exc:
        logger.debug("automation blueprint not registered: %s", exc)
