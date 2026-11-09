"""Hermes plugin: DEV Framework as three tools plus two bundled skills.

The framework itself stays what it is (template/, scripts/install.py, .devframework/*.py — standard
library only). This file only removes what cost the agent tokens in the baseline runs: reading the
package to learn it, multi-step installs, verbose gate output, and Hermes' verify-on-stop not
recognising the finish gate as evidence.
"""
from __future__ import annotations

import logging
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)
PACKAGE = Path(__file__).resolve().parent
MAX_LINES, MAX_CHARS = 40, 3500

# Dot-folders under $HOME that must never receive a framework install.
_PROTECTED_HOME_DOTS = (".ssh", ".aws", ".config", ".gnupg", ".kube", ".docker")


def _path(raw: str) -> Path:
    """Accept D:/x, D:\\x and git-bash /d/x alike (the baseline lost turns to this on Windows)."""
    text = (raw or "").strip().strip('"').strip("'")
    if os.name == "nt":
        match = re.fullmatch(r"/([A-Za-z])/(.*)", text)
        if match:
            text = f"{match[1].upper()}:/{match[2]}"
    path = Path(text).expanduser()
    return path if path.is_absolute() else Path.cwd() / path


def _trim(text: str, lines: int = MAX_LINES, chars: int = MAX_CHARS) -> str:
    rows = text.strip().splitlines()
    if len(rows) > lines:
        rows = rows[: lines // 2] + [f"… ({len(rows) - lines} lines omitted)"] + rows[-lines // 2:]
    out = "\n".join(rows)
    return out if len(out) <= chars else out[: chars // 2] + "\n… (truncated)\n" + out[-chars // 2:]


def _child_env() -> dict[str, str]:
    """Minimal env for DF child processes — never the full parent ``os.environ``.

    Passes PATH, HOME, LANG/LC_*, SYSTEMROOT (Windows), PYTHONDONTWRITEBYTECODE.
    Does not pass GH_TOKEN: ``df_init`` / ``df_nav`` children do not run the optional
    ``gh`` repo-visibility check (that lives in target ``check.py`` / ``devlog`` via terminal).
    """
    env: dict[str, str] = {"PYTHONDONTWRITEBYTECODE": "1"}
    for key in ("PATH", "HOME", "SYSTEMROOT"):
        val = os.environ.get(key)
        if val is not None:
            env[key] = val
    for key, val in os.environ.items():
        if key == "LANG" or key.startswith("LC_"):
            env[key] = val
    return env


def _run(argv: list[str], cwd: Path, timeout: int) -> tuple[int, str]:
    try:
        run = subprocess.run(
            argv,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=_child_env(),
        )
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout}s"
    except OSError as error:
        return 127, f"could not start: {error}"
    return run.returncode, (run.stdout + ("\n" + run.stderr if run.stderr.strip() else "")).strip()


def _framework_dir(target: Path) -> Path | None:
    devframework = target / ".devframework"
    return devframework if (devframework / "check.py").is_file() else None


def _user_home() -> Path:
    return Path.home().expanduser().resolve()


def _hermes_home() -> Path:
    try:
        from hermes_constants import get_hermes_home
        return Path(get_hermes_home()).expanduser().resolve()
    except Exception:
        raw = (os.environ.get("HERMES_HOME") or "").strip()
        if raw:
            return Path(raw).expanduser().resolve()
        return (Path.home() / ".hermes").resolve()


def _session_cwd() -> Path | None:
    """Public Hermes session/terminal cwd (not the gateway process cwd)."""
    try:
        from agent.runtime_cwd import resolve_agent_cwd
        return Path(resolve_agent_cwd()).expanduser().resolve()
    except Exception:
        try:
            return Path.cwd().resolve()
        except OSError:
            return None


def _is_under(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except (ValueError, OSError):
        return False


def _is_git_work_tree(path: Path) -> bool:
    try:
        run = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            timeout=15,
            env=_child_env(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return run.returncode == 0 and "true" in (run.stdout or "").lower()


def _refuse_df_init_target(target: Path) -> str | None:
    """Refuse Hermes home / protected $HOME dots; require git work tree or session cwd."""
    resolved = target.expanduser().resolve()
    hermes = _hermes_home()
    if resolved == hermes or _is_under(resolved, hermes):
        return f"ERROR: refusing df_init into Hermes home or its subtree ({hermes})."

    home = _user_home()
    for name in _PROTECTED_HOME_DOTS:
        protected = (home / name).resolve()
        if resolved == protected or _is_under(resolved, protected):
            return f"ERROR: refusing df_init into protected path ({protected})."

    if _is_git_work_tree(resolved):
        return None

    session = _session_cwd()
    if session is not None and (resolved == session or _is_under(resolved, session)):
        return None

    return (
        "ERROR: df_init requires a git work tree, or the session working directory "
        "(or a path under it). Refuse Hermes home and protected $HOME dot-folders."
    )


# ---------------------------------------------------------------- tools

def df_init(args: dict, **kwargs) -> str:
    target = _path(args.get("target", ""))
    refusal = _refuse_df_init_target(target)
    if refusal:
        return refusal
    name = (args.get("name") or target.name).strip()
    scale = (args.get("scale") or "").strip()
    kind = args.get("kind") or ("" if args.get("update") else "product")
    if kind == "product" and not scale and not args.get("update"):
        return "ERROR: a product needs a scale (integer + unit, e.g. \"100 users\"); it is a product decision — ask the operator."
    profile = args.get("profile") or "generic"
    argv = [sys.executable, "-I", "-B", str(PACKAGE / "scripts" / "install.py"), "--target", str(target), "--name", name,
            "--kind", kind, "--profile", profile, "--devlog" if args.get("devlog") else "--no-devlog", "--hermes"]
    argv += ["--scale", scale] if scale else []
    argv += ["--testing", args["testing"]] if args.get("testing") in ("lean", "advanced") else []
    if args.get("update"):  # with kind: promotion explore → tool → product (product also needs scale)
        argv = [sys.executable, "-I", "-B", str(PACKAGE / "scripts" / "install.py"), "--target", str(target), "--update", "--hermes"]
        argv += (["--kind", kind] if kind else []) + (["--scale", scale] if scale else [])
    code, out = _run(argv, PACKAGE, 120)
    if code:
        return f"INIT FAILED (exit {code}):\n{_trim(out, 25)}"
    summary = [line for line in out.splitlines() if line.strip() and line[0] not in ' \t{}"'][-6:]  # skip the JSON plan
    return ("INIT OK: " + str(target) + "\n" + "\n".join(summary) +
            "\nNext: " + {
                "tool": "fill PROJECT.md, write docs/GUIDE.html and 1-3 `smoke` examples in .devframework/project.json",
                "explore": "write the intent and open questions in PROJECT.md",
            }.get(kind, "fill PROJECT.md and docs/ARCHITECTURE.md (no TODO(project) left), set the test command in "
                        ".devframework/project.json, write docs/USE_CASES.md") +
            ", then df_check doctor. Contract: df_nav contract."
            "\nReplies to the operator from now on: '🛠️ Tech' then '💬 Message' (plain language, ending with the ask); "
            "this replaces any profile footer — one Message, not two.")


def df_check(args: dict, **kwargs) -> str:
    """Return the exact gate command for the agent to run via the ``terminal`` tool.

    Does **not** execute commands from the target repo's ``.devframework/project.json``
    (or any other gate subprocess). Normal Hermes terminal approvals apply when the
    agent runs the returned command.
    """
    target = _path(args.get("target", ""))
    mode = (args.get("mode") or "doctor").strip()
    devframework = _framework_dir(target)
    if devframework is None:
        return f"ERROR: {target} has no .devframework/check.py — run df_init first."
    if mode not in ("doctor", "finish", "commit-check", "secrets", "hostcheck"):
        return "ERROR: mode must be doctor | finish | commit-check | secrets | hostcheck"
    # Agent runs this via terminal with cwd=target; do not pass -I/-P here — check.py
    # imports sibling modules from the target's .devframework via the script-dir path entry.
    if mode == "hostcheck":
        argv = [sys.executable, "-B", str(devframework / "hostcheck.py"), str(target)]
    else:
        extra = {"secrets": ["--worktree"], "doctor": ["--structural"] if args.get("structural") else []}.get(mode, [])
        if args.get("verbose") and mode in ("finish", "commit-check"):
            extra = ["--verbose"]
        argv = [sys.executable, "-B", str(devframework / "check.py"), mode, *extra]
    return (
        "RUN VIA TERMINAL (approvals apply; do not skip):\n"
        f"cwd: {target}\n"
        f"command: {shlex.join(argv)}\n"
        "Done means: the terminal exit code is 0 and the output shows the gate PASS "
        "(e.g. FINISH PASSED). Exit 0 alone is not enough if the gate reports NOT READY/FAIL."
    )


def df_nav(args: dict, **kwargs) -> str:
    """Run the plugin's vendored navigate.py against ``target`` — never the target's copy."""
    target = _path(args.get("target", ""))
    command = (args.get("command") or "brief").strip()
    if _framework_dir(target) is None:
        return f"ERROR: {target} has no .devframework/check.py — run df_init (or df_init with update=true on an older install)."
    if command not in ("brief", "find", "index", "contract", "checklist", "handoff"):
        return "ERROR: command must be brief | find | index | contract | checklist | handoff"
    extra = shlex.split(args.get("args") or "", posix=True)
    nav = PACKAGE / "template" / ".devframework" / "navigate.py"
    vendored_cwd = nav.parent
    # -I: isolated (implies -E/-P/-s) so the target's folder is not on the child's import path.
    argv = [sys.executable, "-I", "-B", str(nav), "--root", str(target), command, *extra]
    code, out = _run(argv, vendored_cwd, 60)
    return _trim(out, 60, 6000) if code == 0 else f"{command} failed (exit {code}):\n{_trim(out, 20)}"


SCHEMAS = {
    "df_init": {
        "name": "df_init",
        "description": (
            "Install the DEV Framework into a repository in one call (preview + install + summary): project context "
            "files, PROJECT.md, docs/ (use cases, known errors, backlog, requirements, architecture …) and the "
            ".devframework gates. Never overwrites existing project documents; refuses on a foreign context file. "
            "Use for a new project or before df-catch-up on an existing one. Then fill PROJECT.md/ARCHITECTURE.md, "
            "project.json test command and docs/USE_CASES.md, and run df_check doctor."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "Repository directory (absolute path; D:/x, D:\\x or /d/x)."},
                "name": {"type": "string", "description": "Product name as the operator calls it (defaults to the directory name)."},
                "kind": {"type": "string", "enum": ["product", "tool", "explore"], "description": "What is being built — the operator's decision, ask: product (full documents + tests), tool (guide.html + smoke examples), explore (minimum; promote later with update+kind)."},
                "scale": {"type": "string", "description": "Product only: integer + unit, e.g. '100 users'. A product decision — ask if unknown."},
                "profile": {"type": "string", "enum": ["generic", "personal-desktop", "service"], "description": "Workflow profile (default generic)."},
                "testing": {"type": "string", "enum": ["lean", "advanced"], "description": "Product only, ask: lean (default below 10,000 users) or advanced (tens of thousands of users, production, high cost of error)."},
                "devlog": {"type": "boolean", "description": "Verbatim devlog of finished dialogues — ask the operator, never assume (default false; local-only in public repos)."},
                "update": {"type": "boolean", "description": "Update an existing installation to this framework version instead of installing."},
            },
            "required": ["target"],
        },
    },
    "df_check": {
        "name": "df_check",
        "description": (
            "Return the exact DEV Framework gate command for the agent to run via the terminal tool (approvals apply). "
            "Does not execute project.json build/test commands itself. Modes: doctor, finish, commit-check, secrets, "
            "hostcheck. Done means terminal exit 0 AND gate PASS text (e.g. FINISH PASSED)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "Repository directory."},
                "mode": {"type": "string", "enum": ["doctor", "finish", "commit-check", "secrets", "hostcheck"], "description": "Which gate (default doctor)."},
                "structural": {"type": "boolean", "description": "doctor only: accept a fresh scaffold as structurally valid."},
                "verbose": {"type": "boolean", "description": "finish/commit-check: stream the full child output instead of logging it."},
            },
            "required": ["target", "mode"],
        },
    },
    "df_nav": {
        "name": "df_nav",
        "description": (
            "Navigate a DEV Framework project without reading whole documents. brief: one-screen session start "
            "(doctor state, git, use cases without tests, open known errors, active checklist + last handoff) — call it "
            "first in every session on a framework project. find <UC-|KE-|FR- id or keyword>: returns one block or a hit "
            "list. index: regenerate docs/INDEX.md. contract: what doctor/finish demand. checklist "
            "new|add|tick|show|archive <slug> [text|n]: multi-iteration plans in docs/<slug>.md. handoff <slug> "
            "--note '...': write the handoff block for the next session."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "Repository directory."},
                "command": {"type": "string", "enum": ["brief", "find", "index", "contract", "checklist", "handoff"]},
                "args": {"type": "string", "description": "Arguments for find/checklist/handoff, shell-style, e.g. 'UC-007', 'new v1 \"strict flag\"', 'tick v1 2', 'v1 --note \"parser done\"'."},
            },
            "required": ["target", "command"],
        },
    },
}
HANDLERS = {"df_init": df_init, "df_check": df_check, "df_nav": df_nav}


# ---------------------------------------------------------------- CLI: hermes devframework <init|check|nav>

def _cli_setup(parser) -> None:
    sub = parser.add_subparsers(dest="df_command", required=True)
    init = sub.add_parser("init", help="install the framework into a repository")
    init.add_argument("target"); init.add_argument("--name"); init.add_argument("--scale")
    init.add_argument("--profile", default="generic"); init.add_argument("--devlog", action="store_true")
    init.add_argument("--kind", choices=["product", "tool", "explore"])
    init.add_argument("--update", action="store_true")
    check = sub.add_parser("check", help="doctor | finish | commit-check | secrets")
    check.add_argument("target"); check.add_argument("mode", nargs="?", default="doctor"); check.add_argument("--verbose", action="store_true")
    nav = sub.add_parser("nav", help="brief | find | index | contract | checklist | handoff")
    nav.add_argument("target"); nav.add_argument("command"); nav.add_argument("rest", nargs="*")


def _cli_handler(args) -> int:
    if args.df_command == "init":
        print(df_init({"target": args.target, "name": args.name, "scale": args.scale, "profile": args.profile, "kind": args.kind,
                       "devlog": args.devlog, "update": args.update}))
    elif args.df_command == "check":
        print(df_check({"target": args.target, "mode": args.mode, "verbose": args.verbose}))
    else:
        print(df_nav({"target": args.target, "command": args.command, "args": " ".join(shlex.quote(r) for r in args.rest)}))
    return 0


def register(ctx, *, register_skills: bool = True, register_cli: bool = True,
             skip_existing_tools: bool = True) -> dict:
    """Register DF tools. When ``skip_existing_tools`` is True, a name already claimed
    by another plugin (e.g. a separately installed ``dev-framework``) is skipped."""
    outcome = {"tools": [], "skipped": [], "skills": [], "cli": False}
    for name, schema in SCHEMAS.items():
        handle = ctx.register_tool(name=name, toolset="dev_framework", schema=schema, handler=HANDLERS[name],
                                   description=schema["description"][:120], emoji="📐")
        if handle is None and skip_existing_tools:
            outcome["skipped"].append(name)
            logger.info("dev-framework tool %s already registered — skipping (vendored copy)", name)
        else:
            outcome["tools"].append(name)
    if register_skills:
        for skill in ("df-import", "df-catch-up"):
            path = PACKAGE / "skills" / skill / "SKILL.md"
            if path.is_file():
                try:
                    ctx.register_skill(name=skill, path=path, description=f"DEV Framework: {skill}")
                    outcome["skills"].append(skill)
                except Exception as error:
                    logger.debug("skill %s not registered: %s", skill, error)
    if register_cli:
        try:
            ctx.register_cli_command(name="devframework", help="DEV Framework: init | check | nav",
                                     setup_fn=_cli_setup, handler_fn=_cli_handler,
                                     description="Install, gate and navigate a DEV Framework project from the shell.")
            outcome["cli"] = True
        except Exception as error:
            logger.debug("cli command not registered: %s", error)
    return outcome
