"""Gate capture, path allow-list checks, vibe_accept tool."""
from __future__ import annotations

import fnmatch
import hashlib
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from state import load_state, update_state
from tool_args import as_dict, coerce_tool_args

PASS_PATTERNS = (
    re.compile(r"FINISH PASSED", re.I),
    re.compile(r"\bPASSED\b"),
    re.compile(r"ok\s*$", re.M),
    re.compile(r"(\d+)\s+passed", re.I),
    re.compile(r"Ran \d+ tests? in .*OK", re.S),
)


def resolve_session_cwd() -> str:
    """Session/terminal cwd via public Hermes API — not the gateway process cwd.

    Documented for plugins: ``agent.runtime_cwd.resolve_agent_cwd()`` (reads the
    session pin / ``TERMINAL_CWD`` bridge). Falls back to ``Path.cwd()`` outside Hermes.
    """
    try:
        from agent.runtime_cwd import resolve_agent_cwd
        return str(resolve_agent_cwd())
    except Exception:
        return str(Path.cwd())


def _cwd(cwd: Optional[str] = None) -> Path:
    raw = (cwd or "").strip()
    return Path(raw).expanduser().resolve() if raw else Path(resolve_session_cwd()).expanduser().resolve()


def working_tree_hash(cwd: Optional[str] = None) -> str:
    """Stable hash of porcelain status + diff (read-only)."""
    root = _cwd(cwd)
    chunks: List[bytes] = []
    for args in (
        ["git", "status", "--porcelain=v1", "-uall"],
        ["git", "diff", "HEAD"],
        ["git", "ls-files", "--others", "--exclude-standard"],
    ):
        try:
            run = subprocess.run(
                args, cwd=str(root), capture_output=True, timeout=30, check=False,
            )
            chunks.append(run.stdout or b"")
            chunks.append(run.stderr or b"")
        except (OSError, subprocess.TimeoutExpired):
            chunks.append(args[0].encode() + b"-unavailable")
    return hashlib.sha256(b"\0".join(chunks)).hexdigest()[:16]


def changed_paths(cwd: Optional[str] = None) -> List[Dict[str, str]]:
    """List changed paths with status letters (read-only git)."""
    root = _cwd(cwd)
    try:
        run = subprocess.run(
            ["git", "status", "--porcelain=v1", "-uall"],
            cwd=str(root), capture_output=True, text=True, timeout=30, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [{"path": "", "status": "error", "detail": str(exc)}]
    out: List[Dict[str, str]] = []
    for line in (run.stdout or "").splitlines():
        if not line.strip():
            continue
        status = line[:2]
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[-1]
        kind = "modified"
        if status.strip() == "??":
            kind = "untracked"
        elif "D" in status:
            kind = "deleted"
        elif "A" in status or status.strip().startswith("A"):
            kind = "added"
        out.append({"path": path.replace("\\", "/"), "status": status, "kind": kind})
    return out


def parse_allowed(allowed: str) -> List[str]:
    parts = []
    for chunk in re.split(r"[,;\n]+", allowed or ""):
        g = chunk.strip().strip("'\"")
        if g:
            parts.append(g.replace("\\", "/"))
    return parts


def path_allowed(path: str, globs: Sequence[str]) -> bool:
    path = path.replace("\\", "/").lstrip("./")
    for g in globs:
        g = g.replace("\\", "/").lstrip("./")
        if fnmatch.fnmatch(path, g) or fnmatch.fnmatch(path, g.rstrip("/") + "/**"):
            return True
        # directory prefix: "src" matches "src/a.py"
        if g.endswith("/**") and path.startswith(g[:-3]):
            return True
        if g.endswith("/") and path.startswith(g):
            return True
    return False


def looks_like_pass(output: str) -> bool:
    text = output or ""
    if re.search(r"\bFAIL(ED)?\b", text) and not re.search(r"0 failed", text, re.I):
        # still allow explicit FINISH PASSED
        if not re.search(r"FINISH PASSED", text, re.I):
            if re.search(r"FAILED|failures?=([1-9])", text, re.I):
                return False
    return any(p.search(text) for p in PASS_PATTERNS)


def capture_terminal_gate(
    *,
    args: Optional[dict] = None,
    result: Optional[str] = None,
    cwd: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """If terminal output looks like a gate PASS/FAIL, bind it to the tree hash."""
    args = coerce_tool_args(args)
    result = result or ""
    cmd = str(args.get("command") or args.get("cmd") or "")
    if not looks_like_pass(result) and "FINISH" not in result.upper() and "passed" not in result.lower():
        # Still record FAIL for FINISH NOT READY etc.
        if not re.search(r"FINISH|PASSED|FAILED|Ran \d+ tests", result, re.I):
            return None
    tree = working_tree_hash(cwd or args.get("workdir") or args.get("cwd"))
    passed = looks_like_pass(result)
    record = {
        "command": cmd[:500],
        "passed": passed,
        "tree_hash": tree,
        "excerpt": (result or "")[-1200:],
    }
    update_state(last_gate=record)
    return record


def fresh_pass_for_tree(cwd: Optional[str] = None) -> bool:
    gate = as_dict(load_state().get("last_gate"))
    if not gate.get("passed"):
        return False
    return gate.get("tree_hash") == working_tree_hash(cwd)


def pre_verify_directive(
    *,
    changed_paths_arg: Optional[List[str]] = None,
    cwd: Optional[str] = None,
) -> Optional[Dict[str, str]]:
    paths = changed_paths_arg
    if paths is None:
        paths = [p["path"] for p in changed_paths(cwd) if p.get("path")]
    if not paths:
        return None
    if fresh_pass_for_tree(cwd):
        return None
    return {
        "action": "continue",
        "message": "continue: run the gate (no fresh PASS bound to the current working-tree hash)",
    }


def vibe_accept(args: dict, **kwargs) -> str:
    """Compare changed paths to allowed globs; report accept/reject with reasons."""
    args = coerce_tool_args(args)
    explicit = args.get("cwd") or args.get("workdir")
    cwd = str(explicit) if explicit else None
    allowed_raw = args.get("allowed") or load_state().get("last_allowed") or ""
    globs = parse_allowed(str(allowed_raw))
    if not globs:
        return "REJECT: no `allowed` globs provided (and no last_allowed in state)."

    entries = changed_paths(cwd)
    if len(entries) == 1 and entries[0].get("status") == "error":
        return f"REJECT: cannot read git status ({entries[0].get('detail')})."

    inside, outside = [], []
    for e in entries:
        path = e["path"]
        if path_allowed(path, globs):
            inside.append(e)
        else:
            outside.append(e)

    # last_gate must be the PASS record dict; a bare string is the delegate gate
    # command (legacy / provider bug) and must not crash .get().
    gate = as_dict(load_state().get("last_gate"))
    tree = working_tree_hash(cwd)
    gate_ok = bool(gate.get("passed") and gate.get("tree_hash") == tree)
    lines = [
        f"allowed: {', '.join(globs)}",
        f"changed: {len(entries)} path(s)",
        f"inside allowed: {len(inside)}",
        f"outside allowed: {len(outside)}",
        f"gate: {'PASS@' + gate.get('tree_hash', '?') if gate_ok else 'missing/stale/fail'}",
        f"tree_hash: {tree}",
    ]
    for e in outside:
        lines.append(f"  OUT: {e['kind']} {e['path']}")
    for e in inside:
        lines.append(f"  OK:  {e['kind']} {e['path']}")

    reasons = []
    if outside:
        reasons.append("changed paths outside allowed")
    if not gate_ok:
        reasons.append("no fresh gate PASS for current tree")
    if not entries:
        reasons.append("no changes to accept")

    if reasons:
        return "REJECT: " + "; ".join(reasons) + "\n" + "\n".join(lines)
    return "ACCEPT\n" + "\n".join(lines)


VIBE_ACCEPT_SCHEMA = {
    "name": "vibe_accept",
    "description": (
        "Accept or reject delegated work: changed paths (git status, read-only) must be ⊆ allowed "
        "globs, and a fresh gate PASS must be bound to the current working-tree hash. "
        "Exit 0 alone is not acceptance."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "allowed": {
                "type": "string",
                "description": "Comma-separated globs (defaults to last delegated allowed).",
            },
            "cwd": {"type": "string", "description": "Repository root (default: session terminal cwd)."},
        },
        "required": [],
    },
}
