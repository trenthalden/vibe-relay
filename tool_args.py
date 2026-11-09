"""Normalize tool-handler inputs from Hermes dispatch / deferred ``tool_call``.

Hermes usually passes ``args`` as a dict, but deferred-tool bridges and some
repair paths can hand a JSON string (or other non-dict). State fields such as
``last_gate`` must also be treated as dict records — a bare string is the
delegate ``gate`` command, not a gate PASS record.
"""
from __future__ import annotations

import json
from typing import Any, Callable, Dict, Mapping, Optional


def coerce_tool_args(args: Any) -> Dict[str, Any]:
    """Return a dict suitable for ``handler(args, **kwargs)``.

    - ``None`` / empty → ``{}``
    - ``dict`` → as-is (shallow copy not required; callers may mutate)
    - JSON object string → parsed dict
    - anything else → ``{}``
    """
    if args is None:
        return {}
    if isinstance(args, dict):
        return args
    if isinstance(args, str):
        text = args.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    if isinstance(args, Mapping):
        return dict(args)
    return {}


def as_dict(value: Any, default: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Return *value* when it is a dict; otherwise *default* or ``{}``."""
    if isinstance(value, dict):
        return value
    return {} if default is None else default


def wrap_tool_handler(handler: Callable[..., str]) -> Callable[..., str]:
    """Wrap a ``handler(args, **kwargs) -> str`` so string/None args are coerced."""

    def _wrapped(args: Any = None, **kwargs: Any) -> str:
        return handler(coerce_tool_args(args), **kwargs)

    _wrapped.__name__ = getattr(handler, "__name__", "wrapped_handler")
    _wrapped.__doc__ = getattr(handler, "__doc__", None)
    return _wrapped
