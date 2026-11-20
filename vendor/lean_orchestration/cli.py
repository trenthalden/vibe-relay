#!/usr/bin/env python3
"""CLI entry for lean-orchestration — same core as the Hermes tool (SEC-2, FR-009/011)."""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

# Hyphenated / non-package layout: put the plugin root on sys.path so `lo_core` imports
# work when invoked as `/usr/bin/python3 /path/to/repo/cli.py` from any cwd.
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from lo_core.config import load_config_file, merge_config  # noqa: E402
from lo_core.gate import run_gate  # noqa: E402
from lo_exec import accept as accept_mod  # noqa: E402
from lo_exec import dispatch as dispatch_mod  # noqa: E402


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="lean-orchestration")
    sub = parser.add_subparsers(dest="command")

    gate_p = sub.add_parser("gate", help="Run the deterministic brief gate")
    _add_common(gate_p)
    gate_p.add_argument("--json", action="store_true", help="Print versioned JSON result")
    gate_p.add_argument("--profile", default=None, help="Brief profile (code|research)")

    task_p = sub.add_parser("task", help="Print the executor task (PASS only)")
    _add_common(task_p)
    task_p.add_argument("--profile", default=None, help="Brief profile (code|research)")

    disp_p = sub.add_parser("dispatch", help="Gate + start CLI executor (does not wait)")
    _add_common(disp_p)
    disp_p.add_argument("--workdir", required=True, help="Absolute executor working directory")
    disp_p.add_argument("--executor", required=True, help="Configured executor name or hermes_child")
    disp_p.add_argument("--data-dir", required=True, help="Plugin data directory for ledger/runs")
    disp_p.add_argument("--profile", default=None, help="Brief profile (code|research)")
    disp_p.add_argument("--timeout", type=float, default=None, help="Wall timeout override (seconds)")

    acc_p = sub.add_parser("accept", help="Accept a dispatch or a repo/brief/base triple")
    acc_p.add_argument("--dispatch", default=None, help="Dispatch id from the ledger")
    acc_p.add_argument("--data-dir", default=None, help="Plugin data directory (required with --dispatch)")
    acc_p.add_argument("--repo", default=None, help="Absolute target repository (manual accept)")
    acc_p.add_argument("--brief", default=None, help="Brief file or text (manual accept)")
    acc_p.add_argument("--base", default=None, help="Base commit sha (manual accept)")
    acc_p.add_argument("--workdir", default=None, help="Working directory (defaults to --repo)")
    acc_p.add_argument("--config", default=None, help="Optional JSON config file")
    acc_p.add_argument("--journal", default=None, help="Journal file path")
    acc_p.add_argument("--profile", default=None, help="Brief profile (code|research)")
    acc_p.add_argument("--json", action="store_true", help="Print versioned JSON result")

    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help(sys.stderr)
        return 2

    try:
        config = load_config_file(args.config) if getattr(args, "config", None) else merge_config()
    except ValueError as exc:
        _write_stderr("usage error: %s\n" % exc)
        return 2

    if args.command == "accept":
        return _cmd_accept(args, config)

    brief_text, brief_err = _read_brief(args.brief)
    if brief_err:
        _write_stderr("%s\n" % brief_err)
        return 2

    if args.command == "dispatch":
        return _cmd_dispatch(args, brief_text, config)

    result = run_gate(
        args.repo,
        brief_text,
        allowed_extra=list(args.allowed or []),
        config=config,
        journal_path=args.journal,
        profile=getattr(args, "profile", None),
    )

    # Usage-class errors: exit 2 with the reason on stderr (C1).
    if result["decision"] == "ERROR" and result["reasons"] == ["bad_repo"]:
        _write_stderr("bad_repo\n")
        return 2

    if args.command == "task":
        if result["decision"] != "PASS":
            _print_text_decision(result)
            return _exit_code(result)
        _write_bytes(result["task"].encode("utf-8"))
        return 0

    # gate
    if args.json:
        payload = _public_result(result)
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        _write_bytes(raw.encode("utf-8"))
    else:
        _print_text_decision(result)
    return _exit_code(result)


def _cmd_accept(args: Any, config: Dict[str, Any]) -> int:
    if args.dispatch:
        if not args.data_dir:
            _write_stderr("usage error: --data-dir required with --dispatch\n")
            return 2
        data_dir = os.path.abspath(args.data_dir)
        result = accept_mod.accept_dispatch(
            dispatch_id=str(args.dispatch),
            data_dir=data_dir,
            config=config,
            journal_path=args.journal,
            profile=args.profile,
        )
    elif args.repo and args.brief and args.base:
        brief_text, brief_err = _read_brief(args.brief)
        if brief_err:
            _write_stderr("%s\n" % brief_err)
            return 2
        result = accept_mod.accept_manual(
            repo=args.repo,
            brief_text=brief_text,
            base_commit=str(args.base),
            workdir=args.workdir,
            config=config,
            journal_path=args.journal,
            profile=args.profile,
            data_dir=os.path.abspath(args.data_dir) if args.data_dir else None,
        )
    else:
        _write_stderr(
            "usage error: accept needs --dispatch + --data-dir, "
            "or --repo + --brief + --base\n"
        )
        return 2

    if args.json:
        raw = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        _write_bytes(raw.encode("utf-8"))
    else:
        _write_text("%s\n" % result.get("decision"))
        for reason in result.get("reasons") or []:
            _write_text("%s\n" % reason)
    if result.get("decision") == "ACCEPTED":
        return 0
    return 3


def _cmd_dispatch(args: Any, brief_text: str, config: Dict[str, Any]) -> int:
    data_dir = os.path.abspath(args.data_dir)
    os.makedirs(data_dir, exist_ok=True)
    result = dispatch_mod.dispatch(
        repo=args.repo,
        brief_text=brief_text,
        workdir=args.workdir,
        executor=args.executor,
        data_dir=data_dir,
        config=config,
        profile=args.profile,
        timeout_s=args.timeout,
        allowed_extra=list(args.allowed or []),
        journal_path=args.journal,
        python_bin=sys.executable or "python3",
    )
    if not result.get("ok"):
        reasons = result.get("reasons") or ["refused"]
        for r in reasons:
            _write_stderr("%s\n" % r)
        decision = result.get("decision")
        if decision == "ERROR":
            return 4
        if decision == "REFUSED":
            return 3
        return 3
    # hermes_child: print sealed payload JSON; CLI executor: print id only (does not wait).
    if result.get("executor") == "hermes_child":
        payload = {
            "ok": True,
            "executor": "hermes_child",
            "delegate_task": result.get("delegate_task"),
            "profile": result.get("profile"),
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        _write_bytes(raw.encode("utf-8"))
        return 0
    _write_text("%s\n" % result.get("id"))
    return 0


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repo", required=True, help="Absolute path of the target repository")
    parser.add_argument("--brief", required=True, help="Brief file path or brief text")
    parser.add_argument("--allowed", action="append", default=[], help="Extra ALLOWED pattern (repeatable)")
    parser.add_argument("--config", default=None, help="Optional JSON config file")
    parser.add_argument("--journal", default=None, help="Journal file path")


def _read_brief(value: str) -> Tuple[str, Optional[str]]:
    """Return (text, error_code). Absolute missing path → brief_not_found."""
    if os.path.isfile(value):
        with open(value, "r", encoding="utf-8") as fh:
            return fh.read(), None
    if os.path.isabs(value) and (value.endswith(".md") or os.path.sep in value):
        if not os.path.exists(value):
            return "", "brief_not_found"
    return value, None


def _public_result(result: Dict[str, Any]) -> Dict[str, Any]:
    payload = {
        "schema": result["schema"],
        "decision": result["decision"],
        "reasons": result["reasons"],
        "areas": result["areas"],
        "allowed": list(result.get("allowed") or []),
        "unmapped": result["unmapped"],
        "cited": result["cited"],
        "delivered": {
            "rules": result["delivered"]["rules"],
            "known_errors": result["delivered"]["known_errors"],
        },
        "task_bytes": result["task_bytes"],
        "rules_source": result["rules_source"],
        "profile": result.get("profile") or "code",
    }
    if result.get("gate_disabled"):
        payload["gate_disabled"] = True
    return payload


def _print_text_decision(result: Dict[str, Any]) -> None:
    decision = result["decision"]
    if decision == "PASS":
        _write_text("PASS\n")
        return
    label = "REFUSED" if decision == "REFUSED" else "REFUSED"
    if decision == "ERROR":
        label = "REFUSED"
    _write_text("%s\n" % label)
    for reason in result["reasons"]:
        _write_text("%s\n" % reason)


def _exit_code(result: Dict[str, Any]) -> int:
    decision = result["decision"]
    if decision == "PASS":
        return 0
    if decision == "ERROR":
        return 4
    return 3


def _write_text(text: str) -> None:
    sys.stdout.buffer.write(text.encode("utf-8"))
    sys.stdout.buffer.flush()


def _write_stderr(text: str) -> None:
    sys.stderr.buffer.write(text.encode("utf-8"))
    sys.stderr.buffer.flush()


def _write_bytes(data: bytes) -> None:
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    sys.exit(main())
