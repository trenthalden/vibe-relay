"""Hermes child envelope — seal and verify delegate_task context (FR-012).

KE-2026-11-20-BRIEF-RULES-SKIPPED: the envelope binds goal+context so a hand-written
delegate_task cannot drop the delivered rules block.
"""
from __future__ import annotations

import hashlib
import re
from typing import Optional, Tuple

ENVELOPE_PREFIX = "[lean-orchestration envelope lo-env/1 "
ENVELOPE_RE = re.compile(
    r"^\[lean-orchestration envelope lo-env/1 ([0-9a-f]{64})\]\s*$"
)


def make_envelope_line(goal: str, context_without_envelope: str) -> str:
    """Return the last-line envelope for ``goal`` + context body (no trailing envelope)."""
    digest = _digest(goal, context_without_envelope)
    return "%s%s]" % (ENVELOPE_PREFIX, digest)


def seal_context(goal: str, context_body: str) -> str:
    """Append the envelope as the last line of context."""
    body = context_body.rstrip("\n")
    line = make_envelope_line(goal, body)
    if body:
        return body + "\n" + line + "\n"
    return line + "\n"


def verify_envelope(goal: str, context: str) -> Tuple[bool, str]:
    """Return (ok, reason). reason is empty on success."""
    text = context if context.endswith("\n") else context + "\n"
    lines = text.splitlines()
    if not lines:
        return False, "missing_envelope"
    last = lines[-1].strip()
    match = ENVELOPE_RE.match(last)
    if not match:
        return False, "missing_envelope"
    body = "\n".join(lines[:-1]).rstrip("\n")
    expected = match.group(1)
    actual = _digest(goal, body)
    if actual != expected:
        return False, "envelope_mismatch"
    return True, ""


def extract_envelope_digest(context: str) -> Optional[str]:
    lines = context.splitlines()
    if not lines:
        return None
    match = ENVELOPE_RE.match(lines[-1].strip())
    if not match:
        return None
    return match.group(1)


def _digest(goal: str, context_without_envelope: str) -> str:
    material = goal + "\n" + context_without_envelope
    return hashlib.sha256(material.encode("utf-8")).hexdigest()
