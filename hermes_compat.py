"""Single adapter for Hermes host access beyond ``PluginContext`` (API-1 / API-7).

Plugin settings stay on ``ctx.get_config`` (CFG-1). Everything else that must touch
Hermes internals — top-level config subtrees, session cwd without a ctx, cron session
markers, account usage, the delegation ABC — goes through this module only.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# cost: ≤1 warning log per missing capability per process
_logged_missing: set[str] = set()


@dataclass(frozen=True)
class MissingCapability:
    """Named result when a Hermes host surface is absent (never a silent default)."""

    name: str
    reason: str

    def __bool__(self) -> bool:
        return False


def _log_missing_once(name: str, exc: BaseException) -> MissingCapability:
    if name not in _logged_missing:
        _logged_missing.add(name)
        logger.warning("vibe-relay: Hermes capability %s missing: %s", name, exc)
    return MissingCapability(name=name, reason=f"{type(exc).__name__}: {exc}")


def is_missing(value: Any) -> bool:
    return isinstance(value, MissingCapability)


# ---------------------------------------------------------------------------
# Delegation ABC — required by ctx.register_delegation_provider; no ctx equivalent
# for the base class itself (only the registrar is public on PluginContext).
# ---------------------------------------------------------------------------
try:
    from agent.delegation_provider import (  # noqa: F401
        DelegationProvider,
        DelegationRouteError,
    )
except ImportError as _exc:  # pragma: no cover - host without delegation surface
    _cap = _log_missing_once("delegation_provider", _exc)
    DelegationProvider = None  # type: ignore[assignment,misc]

    class DelegationRouteError(Exception):  # type: ignore[no-redef]
        """Named refusal type when the host ABC cannot be imported."""

        missing = _cap


# ---------------------------------------------------------------------------
# Top-level config — ctx.get_config is plugin-settings only (CFG-1); no public
# PluginContext accessor for delegation / cron / model subtrees.
# ---------------------------------------------------------------------------
def load_config_readonly() -> Any:
    """Return the Hermes readonly config mapping, or ``MissingCapability``."""
    try:
        from hermes_cli.config import load_config_readonly as _load
    except ImportError as exc:
        return _log_missing_once("load_config_readonly", exc)
    try:
        return _load() or {}
    except Exception as exc:
        return _log_missing_once("load_config_readonly", exc)


def delegation_config() -> dict:
    """``delegation`` subtree; empty dict only when the host reported no section."""
    cfg = load_config_readonly()
    if is_missing(cfg):
        return {}
    raw = (cfg or {}).get("delegation") if isinstance(cfg, dict) else None
    return dict(raw) if isinstance(raw, dict) else {}


def model_provider_and_name() -> tuple[str, str]:
    """``(provider, model_name)`` from the ``model`` subtree; blanks if unavailable."""
    cfg = load_config_readonly()
    if is_missing(cfg) or not isinstance(cfg, dict):
        return "", "<model>"
    model = cfg.get("model") or {}
    if not isinstance(model, dict):
        return "", "<model>"
    provider = str(model.get("provider") or "").strip()
    name = str(model.get("default") or model.get("model") or "").strip() or "<model>"
    return provider, name


def active_provider_slug() -> Optional[str]:
    provider, _name = model_provider_and_name()
    return provider or None


def agent_scheduling_allowed() -> bool:
    """``cron.allow_agent_scheduling`` (default false when config/capability missing)."""
    cfg = load_config_readonly()
    if is_missing(cfg) or not isinstance(cfg, dict):
        return False
    cron = cfg.get("cron") or {}
    if not isinstance(cron, dict):
        return False
    return bool(cron.get("allow_agent_scheduling"))


# ---------------------------------------------------------------------------
# Session cwd — prefer PluginContext.session_cwd when a ctx is in hand; otherwise
# agent.runtime_cwd.resolve_agent_cwd (documented plugin helper; no ctx-free public
# equivalent on PluginContext).
# ---------------------------------------------------------------------------
def resolve_agent_cwd(ctx: Any = None) -> str:
    if ctx is not None and callable(getattr(ctx, "session_cwd", None)):
        try:
            value = ctx.session_cwd()
            if value:
                return str(value)
        except Exception as exc:
            logger.debug("vibe-relay: ctx.session_cwd failed: %s", exc)
    try:
        from agent.runtime_cwd import resolve_agent_cwd as _resolve
    except ImportError as exc:
        _log_missing_once("resolve_agent_cwd", exc)
        return str(Path.cwd())
    try:
        return str(_resolve())
    except Exception:
        return str(Path.cwd())


# ---------------------------------------------------------------------------
# Cron session marker — no PluginContext getter for HERMES_CRON_SESSION;
# gateway.session_context.get_session_env is the documented session-env helper.
# ---------------------------------------------------------------------------
def get_session_env(name: str, default: str = "") -> str:
    try:
        from gateway.session_context import get_session_env as _get
    except ImportError as exc:
        _log_missing_once("get_session_env", exc)
        return os.environ.get(name, default)
    try:
        value = _get(name, default)
        return default if value is None else str(value)
    except Exception as exc:
        _log_missing_once("get_session_env", exc)
        return os.environ.get(name, default)


# ---------------------------------------------------------------------------
# Account usage — no PluginContext equivalent; public agent.account_usage helper.
# ---------------------------------------------------------------------------
def fetch_account_usage(provider: Optional[str], *, read_only: bool = True) -> Any:
    try:
        from agent.account_usage import fetch_account_usage as _fetch
    except ImportError as exc:
        return _log_missing_once("fetch_account_usage", exc)
    try:
        return _fetch(provider, read_only=read_only)
    except Exception as exc:
        return _log_missing_once("fetch_account_usage", exc)


# ---------------------------------------------------------------------------
# Plugin data dir — documented plugins.plugin_storage helper (not on ctx).
# ---------------------------------------------------------------------------
def plugin_data_dir(plugin_id: str) -> Path:
    try:
        from plugins.plugin_storage import plugin_data_dir as _dir
    except ImportError as exc:
        missing = _log_missing_once("plugin_data_dir", exc)
        raise RuntimeError(f"Hermes capability plugin_data_dir missing: {missing.reason}") from exc
    return Path(_dir(plugin_id))
