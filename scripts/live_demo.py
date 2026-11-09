#!/usr/bin/env python3
"""R5 live demo harness for vibe-relay (disposable vrdemo profile + toy repo).

Creates a fresh toy git repo under ``/tmp/vr-demo-<ts>`` (not under $HOME — the
path can appear on camera), initialises the vendored DEV Framework via real
``df_init``, runs one real Hermes parent turn that should delegate via
vibe-relay to a cheap executor (parent file writes blocked by
``enforce_executor_lane``; stop-at-threshold auto-parks), grades the result,
and records a scripted out-of-scope REJECT case. Outputs land under
out/live-demo/ (gitignored).

Paths are derived from $HOME / $HERMES_HOME / /tmp — never hard-code owner paths
other than the disposable profile name ``vrdemo`` required by the brief.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

PROFILE = "vrdemo"
PLUGIN_NAME = "vibe-relay"
ALLOWED_GLOBS = "textutil/**"
# DEV Framework finish gate (project.json test = toy unittest via run_unittest.py).
GATE_CMD = "python .devframework/check.py finish"
POSITIVE_PROMPT = (
    "The slugify tests fail. Fix it via delegate_task with allowed=[\"textutil/**\"] "
    "and gate=\"python .devframework/check.py finish\". Only touch textutil/. "
    "The DEV Framework finish gate must FAIL before the fix and PASS after. "
    "Then call vibe_accept."
)
NEGATIVE_ALLOWED = "textutil/**"


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def out_root() -> Path:
    return repo_root() / "out" / "live-demo"


def desktop_root() -> Path:
    explicit = os.environ.get("HERMES_DESKTOP_HOME", "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    # Brief pattern: HERMES_HOME=~/.hermes-desktop hermes -p vrdemo …
    home = os.environ.get("HERMES_HOME", "").strip()
    if home:
        p = Path(home).expanduser().resolve()
        if (p / "profiles").is_dir():
            return p
        if p.name == PROFILE and p.parent.name == "profiles":
            return p.parent.parent
    return Path.home() / ".hermes-desktop"


def profile_home() -> Path:
    return desktop_root() / "profiles" / PROFILE


def hermes_bin() -> Path:
    env = os.environ.get("HERMES_BIN", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    candidate = desktop_root() / "hermes-agent" / ".hermes" / "bin" / "hermes"
    if candidate.exists():
        return candidate
    which = shutil.which("hermes")
    if which:
        return Path(which)
    raise SystemExit("hermes binary not found; set HERMES_BIN")


def run(
    cmd: Sequence[str],
    *,
    cwd: Optional[Path] = None,
    env: Optional[Dict[str, str]] = None,
    check: bool = True,
    timeout: Optional[int] = None,
) -> subprocess.CompletedProcess:
    print("+", " ".join(str(c) for c in cmd), flush=True)
    return subprocess.run(
        list(cmd),
        cwd=str(cwd) if cwd else None,
        env=env,
        text=True,
        capture_output=True,
        check=check,
        timeout=timeout,
    )


def hermes_env() -> Dict[str, str]:
    env = os.environ.copy()
    env["HERMES_HOME"] = str(desktop_root())
    # Keep TMPDIR under the disposable profile cache when unset.
    env.setdefault(
        "TMPDIR",
        str(profile_home() / "cache" / "scratch" / "tmp"),
    )
    Path(env["TMPDIR"]).mkdir(parents=True, exist_ok=True)
    return env


def hermes_cmd(*args: str) -> List[str]:
    return [str(hermes_bin()), "-p", PROFILE, *args]


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _fill_project_md(dest: Path) -> None:
    """Replace TODO(project) markers so DF doctor is READY (explore kind)."""
    path = dest / "PROJECT.md"
    text = path.read_text(encoding="utf-8")
    replacements = {
        "TODO(project): what you are trying out, what is still unknown, and what would tell you that it works.": (
            "Demo explore: fix `textutil.slugify` so the unit tests pass. "
            "Done means `python .devframework/check.py finish` prints FINISH PASSED."
        ),
        "TODO(project): map the source areas, tests and consumers of shared code.": (
            "`textutil/` is the only editable package; `tests/` holds unittest; "
            "`.devframework/` is the vendored DEV Framework gate (do not edit in the task)."
        ),
        "TODO(project): list settings/database/log locations and isolated test locations, or N/A.": (
            "N/A — no databases or credentials; disposable toy under /tmp."
        ),
        "TODO(project): configure build/test commands in .devframework/project.json; document\n"
        "output locations and any process locks. A build never authorizes relaunch or deployment.": (
            "Test command in `.devframework/project.json` runs the toy unittest suite via "
            "`.devframework/run_unittest.py`. No build. Gate: `python .devframework/check.py finish`."
        ),
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    if "TODO(project)" in text:
        # Fallback: strip any remaining markers so doctor is ready.
        text = text.replace("TODO(project):", "")
    path.write_text(text, encoding="utf-8")


def init_dev_framework(dest: Path) -> Dict[str, Any]:
    """Initialise DEV Framework via the real vendored ``df_init`` tool path (not a copy-paste)."""
    vendor = str(repo_root() / "vendor")
    if vendor not in sys.path:
        sys.path.insert(0, vendor)
    import dev_framework as df  # noqa: WPS433 — real tool module

    target = str(Path(dest).expanduser().resolve())
    out = df.df_init(
        {
            "target": target,
            "name": "toy-slugify",
            "kind": "explore",
            "scale": "1 user",
            "profile": "generic",
        }
    )
    if not str(out).startswith("INIT OK"):
        raise SystemExit(f"df_init failed:\n{out}")
    _fill_project_md(dest)
    pj_path = dest / ".devframework" / "project.json"
    pj = json.loads(pj_path.read_text(encoding="utf-8"))
    # Toy unittest suite, counted for finish via run_unittest.py (prints TESTS: …).
    pj["commands"]["test"] = [
        "{python}",
        "-B",
        ".devframework/run_unittest.py",
        "--start",
        "tests",
    ]
    pj_path.write_text(json.dumps(pj, indent=2) + "\n", encoding="utf-8")
    return {"df_init": out, "test_command": pj["commands"]["test"], "gate": GATE_CMD}


def create_toy_repo(dest: Path) -> Path:
    # macOS: /tmp → /private/tmp symlink; vendored df_init rejects symlink ancestors.
    dest = Path(dest).expanduser().resolve()
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    (dest / "textutil").mkdir()
    (dest / "textutil" / "__init__.py").write_text(
        '"""Tiny text helpers for the vibe-relay live demo."""\nfrom .slugify import slugify\n',
        encoding="utf-8",
    )
    # Intentional bug: does not lowercase, does not collapse repeats, keeps underscores wrong.
    (dest / "textutil" / "slugify.py").write_text(
        '"""slugify — intentionally broken for the demo RED gate."""\n'
        "\n"
        "import re\n"
        "\n"
        "\n"
        "def slugify(value: str) -> str:\n"
        '    """Return a URL-ish slug. Demo starts RED (3 of 5 tests fail)."""\n'
        "    # BUG: missing lowercasing; replaces spaces only; leaves punctuation.\n"
        '    return value.strip().replace(" ", "-")\n',
        encoding="utf-8",
    )
    (dest / "tests").mkdir()
    (dest / "tests" / "__init__.py").write_text("", encoding="utf-8")
    (dest / "tests" / "test_slugify.py").write_text(
        "import unittest\n"
        "\n"
        "from textutil import slugify\n"
        "\n"
        "\n"
        "class SlugifyTests(unittest.TestCase):\n"
        "    def test_basic_words(self):\n"
        '        self.assertEqual(slugify("Hello World"), "hello-world")\n'
        "\n"
        "    def test_collapses_whitespace(self):\n"
        '        self.assertEqual(slugify("foo   bar"), "foo-bar")\n'
        "\n"
        "    def test_strips_punctuation(self):\n"
        '        self.assertEqual(slugify("Hello, World!"), "hello-world")\n'
        "\n"
        "    def test_already_slug(self):\n"
        '        self.assertEqual(slugify("already-slug"), "already-slug")\n'
        "\n"
        "    def test_trim_edges(self):\n"
        '        self.assertEqual(slugify("  spaced  "), "spaced")\n'
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    unittest.main()\n",
        encoding="utf-8",
    )
    (dest / "README.md").write_text(
        "# toy-slugify\n\nTiny package used by the vibe-relay live demo. `textutil.slugify` starts broken.\n"
        "Gate: `python .devframework/check.py finish` (DEV Framework; test = unittest).\n",
        encoding="utf-8",
    )
    (dest / ".gitignore").write_text(
        "__pycache__/\n*.py[cod]\n.pytest_cache/\n.devframework/last_run.log\n",
        encoding="utf-8",
    )
    run(["git", "init"], cwd=dest)
    run(["git", "config", "user.email", "demo@example.com"], cwd=dest)
    run(["git", "config", "user.name", "vibe-relay demo"], cwd=dest)
    run(["git", "add", "-A"], cwd=dest)
    run(["git", "commit", "-m", "chore: broken slugify baseline for live demo"], cwd=dest)

    # Real vendored df_init path (not a scaffold copy-paste).
    init_dev_framework(dest)
    run(["git", "add", "-A"], cwd=dest)
    run(["git", "commit", "-m", "chore: DEV Framework gate (df_init) for live demo"], cwd=dest)

    # Prove DF finish RED before the fix (unittest failures → finish FAIL).
    # Do not leave evidence files in the toy tree — they would pollute allowed diffs.
    red = run_gate(dest)
    if red.returncode == 0:
        raise SystemExit("toy DF finish unexpectedly GREEN before the fix")
    return dest


def ensure_plugin_symlink() -> Path:
    plugins = profile_home() / "plugins"
    plugins.mkdir(parents=True, exist_ok=True)
    link = plugins / PLUGIN_NAME
    target = repo_root()
    if link.is_symlink() or link.exists():
        if link.resolve() != target.resolve():
            if link.is_symlink() or link.is_file():
                link.unlink()
            else:
                shutil.rmtree(link)
            link.symlink_to(target)
    else:
        link.symlink_to(target)
    return link


def reload_plugin_into_gateway() -> None:
    """Chat -Q runs inside the long-lived host gateway, which keeps in-memory plugin
    code until reloaded. Disable→enable nudges ``reload-plugins`` so ``pre_tool_call``
    from the symlink is what the next turn actually runs.
    """
    env = hermes_env()
    run(hermes_cmd("plugins", "disable", PLUGIN_NAME), env=env, check=False)
    run(hermes_cmd("plugins", "enable", PLUGIN_NAME), env=env, check=False)


def configure_backend(*, policy: str) -> None:
    env = hermes_env()
    pairs = [
        ("delegation.backend", PLUGIN_NAME),
        ("delegation.backends.vibe-relay.executor_provider", "anthropic"),
        ("delegation.backends.vibe-relay.executor_model", "claude-haiku-4-5"),
        ("delegation.backends.vibe-relay.policy", policy),
        ("delegation.backends.vibe-relay.enforce_executor_lane", "true"),
        ("delegation.backends.vibe-relay.stop_used_percent", "90"),
        ("delegation.subagent_auto_approve", "true"),
        # Disposable demo profile only: non-TTY -Q must run unittest/git without prompts.
        ("approvals.mode", "off"),
    ]
    for key, value in pairs:
        run(hermes_cmd("config", "set", key, value), env=env)
    run(hermes_cmd("plugins", "enable", PLUGIN_NAME), env=env, check=False)


def _remaining_from_usage_payload(data: Dict[str, Any]) -> Tuple[Optional[float], Optional[float]]:
    """Match plugin routing: remaining = 100 - max(used_percent across windows)."""
    windows = data.get("windows") or []
    used_vals = [
        float(w["used_percent"])
        for w in windows
        if isinstance(w, dict) and w.get("used_percent") is not None
    ]
    if not used_vals:
        return None, None
    used = max(used_vals)
    return 100.0 - used, used


def read_usage() -> Dict[str, Any]:
    env = hermes_env()
    cp = run(hermes_cmd("usage", "--json"), env=env, check=False)
    stdout = (cp.stdout or "").strip()
    if cp.returncode == 0 and stdout.startswith("{"):
        data = json.loads(stdout)
        rem, used = _remaining_from_usage_payload(data)
        return {
            "raw": data,
            "remaining_percent": rem,
            "used_percent": used,
            "source": "hermes usage --json",
        }
    cp2 = run(hermes_cmd("usage"), env=env, check=False)
    text = (cp2.stdout or "") + (cp2.stderr or "")
    remaining = None
    used = None
    found = re.findall(
        r"(\d+)%\s+remaining\s+\((\d+)%\s+used\)",
        text,
        flags=re.I,
    )
    if found:
        rem_vals = [int(a) for a, _ in found]
        used_vals = [int(b) for _, b in found]
        remaining = float(min(rem_vals))
        used = float(max(used_vals))
    return {
        "raw": text,
        "remaining_percent": remaining,
        "used_percent": used,
        "source": "hermes usage text",
    }


def choose_policy(usage: Dict[str, Any]) -> Tuple[str, str]:
    """Return (policy, reason). Prefer auto when real quota already forces executor."""
    rem = usage.get("remaining_percent")
    threshold = 51.0
    if rem is None:
        return "executor-always", "quota remaining unknown → executor-always (documented)"
    if float(rem) <= threshold:
        return "auto", f"real remaining {rem:.0f}% ≤ {threshold:.0f}% → auto routes executor"
    return "executor-always", (
        f"real remaining {rem:.0f}% > {threshold:.0f}% would keep auto on self; "
        "using documented policy executor-always for the cheap-executor demo"
    )


def load_plugin_state() -> Dict[str, Any]:
    path = profile_home() / "plugin-data" / PLUGIN_NAME / "state.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def seed_plugin_quota(usage: Dict[str, Any]) -> None:
    """Write a usable quota snapshot into plugin state so stop/routing see used%.

    ``hermes usage`` can succeed while in-process ``fetch_account_usage`` returns
    no snapshot inside a gateway chat; seeding keeps should_park honest for demos.
    """
    path = profile_home() / "plugin-data" / PLUGIN_NAME / "state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except json.JSONDecodeError:
        state = {}
    used = usage.get("used_percent")
    rem = usage.get("remaining_percent")
    if used is None and rem is not None:
        used = 100.0 - float(rem)
    if used is None:
        return
    # Prefer a future ISO reset so park() can schedule; fall back to +4h.
    from datetime import timedelta

    reset = (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat()
    raw = usage.get("raw")
    if isinstance(raw, dict):
        for w in raw.get("windows") or []:
            if isinstance(w, dict) and w.get("reset_at"):
                reset = str(w["reset_at"])
                break
    state["quota"] = {
        "provider": "anthropic",
        "available": True,
        "windows": [
            {
                "label": "account",
                "used_percent": float(used),
                "reset_at": reset,
                "scope": "account",
            }
        ],
        "cache_fetched_at": time.time(),
        "from_cache": False,
        "seeded_from": usage.get("source") or "live_demo",
    }
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def git_diff_names(cwd: Path) -> List[str]:
    cp = run(["git", "diff", "--name-only", "HEAD"], cwd=cwd, check=False)
    names = [ln.strip() for ln in (cp.stdout or "").splitlines() if ln.strip()]
    cp2 = run(["git", "ls-files", "--others", "--exclude-standard"], cwd=cwd, check=False)
    names.extend(ln.strip() for ln in (cp2.stdout or "").splitlines() if ln.strip())
    # unique preserve order
    seen = set()
    out = []
    for n in names:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def path_allowed(path: str, globs: Sequence[str]) -> bool:
    from fnmatch import fnmatch

    norm = path.replace("\\", "/").lstrip("./")
    for g in globs:
        g = g.strip().replace("\\", "/")
        if not g:
            continue
        if fnmatch(norm, g) or fnmatch(norm, g.rstrip("/")):
            return True
        if g.endswith("/**") and (norm == g[:-3].rstrip("/") or norm.startswith(g[:-2])):
            return True
    return False


def run_gate(cwd: Path) -> subprocess.CompletedProcess:
    """DEV Framework finish gate (same command the agent must pass)."""
    return run(
        [sys.executable, ".devframework/check.py", "finish"],
        cwd=cwd,
        check=False,
    )


def hermes_python() -> Path:
    """Interpreter that can import Hermes + this plugin (same as the hermes wrapper)."""
    # hermes launcher: …/tools/python-…/bin/python3 -I -c …
    wrapper = hermes_bin().read_text(encoding="utf-8", errors="ignore")
    m = re.search(r"exec (\S+/python3) ", wrapper)
    if m and Path(m.group(1)).exists():
        return Path(m.group(1))
    return Path(sys.executable)


def plugin_python_env() -> Dict[str, str]:
    """Env for in-process vibe_accept / state probes against the vrdemo profile."""
    env = hermes_env()
    env["HERMES_HOME"] = str(profile_home())
    agent_root = desktop_root() / "hermes-agent"
    parts = [str(repo_root()), str(agent_root)]
    existing = env.get("PYTHONPATH", "")
    if existing:
        parts.append(existing)
    env["PYTHONPATH"] = os.pathsep.join(parts)
    # hermes wrapper clears PYTHONPATH with -I; we invoke python directly without -I.
    return env


def run_live_chat(
    *,
    toy: Path,
    run_dir: Path,
    prompt: str,
    max_turns: int = 40,
    timeout: int = 700,
) -> Dict[str, Any]:
    env = hermes_env()
    transcript = run_dir / "transcript.txt"
    stream_path = run_dir / "stream.jsonl"
    # Gateway CLI rejects --run-budget; rely on profile defaults + --max-turns.
    cmd = hermes_cmd(
        "chat",
        "-Q",
        "-q",
        prompt,
        "--in",
        str(toy),
        "--format",
        "stream-json",
        "--max-turns",
        str(max_turns),
        "--skills",
        "vibe-relay:lean-executor-orchestration",
        "--source",
        "tool",
    )
    t0 = time.time()
    cp = subprocess.run(
        cmd,
        cwd=str(toy),
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout,
    )
    elapsed = time.time() - t0
    combined = (cp.stdout or "") + ("\n--- STDERR ---\n" + cp.stderr if cp.stderr else "")
    transcript.write_text(combined, encoding="utf-8")
    stream_path.write_text(cp.stdout or "", encoding="utf-8")
    return {
        "returncode": cp.returncode,
        "elapsed_seconds": round(elapsed, 2),
        "stdout": cp.stdout or "",
        "stderr": cp.stderr or "",
        "cmd": cmd,
    }


def extract_final_reply(stream_text: str) -> str:
    """Best-effort final assistant text from stream-json or plain quiet output."""
    finals: List[str] = []
    for line in stream_text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        et = str(ev.get("type") or ev.get("event") or "")
        if et in {"assistant", "message", "final", "result", "text"}:
            content = ev.get("content") or ev.get("text") or ev.get("message") or ""
            if isinstance(content, list):
                parts = []
                for block in content:
                    if isinstance(block, dict) and block.get("text"):
                        parts.append(str(block["text"]))
                    elif isinstance(block, str):
                        parts.append(block)
                content = "".join(parts)
            if content:
                finals.append(str(content))
        if "message" in ev and isinstance(ev["message"], dict):
            role = ev["message"].get("role")
            if role == "assistant":
                c = ev["message"].get("content")
                if isinstance(c, str) and c.strip():
                    finals.append(c)
    if finals:
        return finals[-1].strip()
    # plain text fallback: last non-empty non-json block
    blocks = [b.strip() for b in stream_text.split("\n\n") if b.strip() and not b.strip().startswith("{")]
    return blocks[-1] if blocks else stream_text[-4000:]


def stream_mentions(stream_text: str, *needles: str) -> bool:
    low = stream_text.lower()
    return all(n.lower() in low for n in needles)


def parent_tool_uses(stream_text: str) -> List[str]:
    """Names of top-level stream-json ``tool_use`` events (parent turn only)."""
    names: List[str] = []
    for line in stream_text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if str(ev.get("type") or "") != "tool_use":
            continue
        name = str(ev.get("name") or "").strip()
        if name:
            names.append(name)
    return names


def parent_block_seen(stream_text: str) -> bool:
    return bool(
        re.search(r"vibe-relay:\s*quota is low", stream_text, flags=re.I)
        or re.search(r"route this edit through\s+delegate_task", stream_text, flags=re.I)
    )


def grade_positive(
    *,
    toy: Path,
    chat: Dict[str, Any],
    policy: str,
    policy_reason: str,
) -> Dict[str, Any]:
    state = load_plugin_state()
    last_route = state.get("last_route") or {}
    lane = str(last_route.get("lane") or "")
    reason = str(last_route.get("reason") or "")
    changed = git_diff_names(toy)
    gate = run_gate(toy)
    gate_pass = gate.returncode == 0
    outside = [p for p in changed if not path_allowed(p, [ALLOWED_GLOBS])]
    stream = chat.get("stdout") or ""
    accept_hit = bool(
        re.search(r"\bACCEPT\b", stream)
        or re.search(r"\bACCEPT\b", str(state.get("last_accept") or ""))
    )
    accept_text = probe_vibe_accept(toy, ALLOWED_GLOBS)
    if accept_text.startswith("ACCEPT"):
        accept_hit = True
    elif accept_text.startswith("REJECT"):
        accept_hit = False
    elif re.search(r"\bACCEPT\b", stream):
        accept_hit = True

    executor_lane = lane == "executor" or "executor" in reason or stream_mentions(
        stream, "executor"
    )
    parent_tools = parent_tool_uses(stream)
    parent_write_names = [n for n in parent_tools if n in {"patch", "write_file"}]
    parent_write_blocked = parent_block_seen(stream)
    # Top-level tool_use of patch/write_file is a parent attempt. Nested child
    # writes live inside the delegate_task result and do not emit parent tool_use.
    parent_edit_ok = (not parent_write_names) or parent_write_blocked
    haiku = stream_mentions(stream, "claude-haiku-4-5") or stream_mentions(
        stream, '"model": "claude-haiku-4-5"'
    )
    checks = {
        "tests_pass": gate_pass,
        "diff_subseteq_allowed": gate_pass and not outside and bool(changed),
        "executor_lane": executor_lane or lane == "executor",
        "executor_haiku": haiku,
        "accept_verdict": accept_hit,
        "delegate_called": "delegate_task" in parent_tools
        or stream_mentions(stream, "delegate_task")
        or bool(last_route.get("goal"))
        or bool(last_route.get("allowed")),
        "parent_edit_blocked_or_absent": parent_edit_ok,
        "parent_write_tools": parent_write_names,
        "parent_write_blocked_seen": parent_write_blocked,
    }
    # If tests pass and paths ok but changed empty (committed?), treat carefully
    if gate_pass and not changed:
        checks["diff_subseteq_allowed"] = False
        checks["note"] = "tests pass but git diff empty — executor may have committed or not edited"

    ok = all(
        [
            checks["tests_pass"],
            checks["diff_subseteq_allowed"],
            checks["executor_lane"],
            checks["executor_haiku"],
            checks["delegate_called"],
            checks["accept_verdict"],
            checks["parent_edit_blocked_or_absent"],
        ]
    )
    return {
        "verdict": "PASS" if ok else "FAIL",
        "checks": checks,
        "lane": lane,
        "route_reason": reason,
        "policy": policy,
        "policy_reason": policy_reason,
        "changed_files": changed,
        "outside_allowed": outside,
        "gate_returncode": gate.returncode,
        "gate_stdout_tail": (gate.stdout or "")[-2000:],
        "gate_stderr_tail": (gate.stderr or "")[-1000:],
        "accept_text": accept_text,
        "last_route": last_route,
        "final_reply": extract_final_reply(stream),
        "elapsed_seconds": chat.get("elapsed_seconds"),
    }


def probe_vibe_accept(cwd: Path, allowed: str) -> str:
    """Run vibe_accept with Hermes on PYTHONPATH; fall back to path-only REJECT heuristic."""
    py = hermes_python()
    env = plugin_python_env()
    code = (
        "import json,sys\n"
        "from acceptance import vibe_accept\n"
        "print(vibe_accept({'allowed': sys.argv[1], 'cwd': sys.argv[2]}))\n"
    )
    cp = subprocess.run(
        [str(py), "-c", code, allowed, str(cwd)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if cp.returncode == 0 and (cp.stdout or "").strip():
        return (cp.stdout or "").strip()
    # Fallback: mirror path check only (still enough to prove out-of-scope REJECT).
    changed = git_diff_names(cwd)
    outside = [p for p in changed if not path_allowed(p, [allowed])]
    if outside:
        return (
            "REJECT: changed paths outside allowed\n"
            + "\n".join(f"  OUT: {p}" for p in outside)
            + f"\n(fallback probe; vibe_accept err: {(cp.stderr or cp.stdout or '')[-300:]})"
        )
    return f"REJECT: vibe_accept probe failed\n{(cp.stderr or cp.stdout or '')[-800:]}"


def seed_gate_pass(cwd: Path, gate_output: str) -> None:
    """Write a fresh PASS into plugin state for the current tree (scripted negative)."""
    py = hermes_python()
    env = plugin_python_env()
    code = (
        "import sys\n"
        "from acceptance import working_tree_hash\n"
        "from state import update_state\n"
        "cwd, allowed, cmd, excerpt = sys.argv[1:5]\n"
        "update_state(\n"
        "  last_allowed=allowed,\n"
        "  last_gate={'passed': True, 'tree_hash': working_tree_hash(cwd),\n"
        "             'command': cmd, 'excerpt': excerpt},\n"
        ")\n"
        "print(working_tree_hash(cwd))\n"
    )
    subprocess.run(
        [str(py), "-c", code, str(cwd), NEGATIVE_ALLOWED, GATE_CMD, gate_output[-500:]],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def scripted_negative_reject(run_dir: Path, toy_base: Path) -> Dict[str, Any]:
    """Scripted out-of-scope edit → vibe_accept REJECT (labeled; no live model)."""
    toy = toy_base.parent / f"{toy_base.name}-neg"
    if toy.exists():
        shutil.rmtree(toy)
    shutil.copytree(
        toy_base,
        toy,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    (toy / "textutil" / "slugify.py").write_text(
        '"""slugify — fixed impl for negative-path fixture."""\n'
        "\n"
        "import re\n"
        "\n"
        "\n"
        "def slugify(value: str) -> str:\n"
        "    value = value.strip().lower()\n"
        '    value = re.sub(r"[^a-z0-9]+", "-", value)\n'
        '    return re.sub(r"-{2,}", "-", value).strip("-")\n',
        encoding="utf-8",
    )
    readme = toy / "README.md"
    readme.write_text(
        readme.read_text(encoding="utf-8") + "\n<!-- out of scope -->\n",
        encoding="utf-8",
    )
    gate = run_gate(toy)
    seed_gate_pass(toy, (gate.stdout or "") + (gate.stderr or ""))
    accept_text = probe_vibe_accept(toy, NEGATIVE_ALLOWED)
    changed = git_diff_names(toy)
    outside = [p for p in changed if not path_allowed(p, [NEGATIVE_ALLOWED])]
    ok = accept_text.startswith("REJECT") and bool(outside)
    result = {
        "label": "scripted-executor-diff (not a live model run)",
        "verdict": "PASS" if ok else "FAIL",
        "expect": "REJECT because README.md is outside allowed=textutil/**",
        "accept_text": accept_text,
        "changed_files": changed,
        "outside_allowed": outside,
        "gate_returncode": gate.returncode,
    }
    write_json(run_dir / "negative_verdict.json", result)
    (run_dir / "negative_accept.txt").write_text(accept_text, encoding="utf-8")
    return result


def write_recording_md(
    *,
    toy: Path,
    policy: str,
    policy_reason: str,
    grade: Dict[str, Any],
) -> None:
    path = out_root() / "RECORDING.md"
    # Use $HOME-relative display paths for privacy in any later paste; file itself is gitignored.
    toy_disp = str(toy).replace(str(Path.home()), "$HOME")
    desktop = str(desktop_root()).replace(str(Path.home()), "$HOME")
    hermes = str(hermes_bin()).replace(str(Path.home()), "$HOME")
    elapsed = grade.get("elapsed_seconds")
    path.write_text(
        f"""# vibe-relay live demo — recording sheet (R5)

Disposable profile: **vrdemo**. Cheap executor: `anthropic` / `claude-haiku-4-5`.
Routing policy used: **`{policy}`** — {policy_reason}.
Executor-lane enforcement: **on**. Stop-at-threshold + auto-park: **on** (`stop_used_percent`, default 90).
Toy gate: **`python .devframework/check.py finish`** (DEV Framework; test = unittest).

Target wall time on camera: **~60 s** of terminal (cut pauses). Full chat turns are longer off-cut.

## One-time setup (hide this noise off-camera)

```bash
export HERMES_HOME={desktop}
HERMES={hermes}
# Always reload the plugin into the gateway before a live chat (stale in-memory code voids the demo):
$HERMES -p vrdemo plugins disable vibe-relay
$HERMES -p vrdemo plugins enable vibe-relay
$HERMES -p vrdemo config set delegation.backend vibe-relay
$HERMES -p vrdemo config set delegation.backends.vibe-relay.executor_provider anthropic
$HERMES -p vrdemo config set delegation.backends.vibe-relay.executor_model claude-haiku-4-5
$HERMES -p vrdemo config set delegation.backends.vibe-relay.policy {policy}
$HERMES -p vrdemo config set delegation.backends.vibe-relay.enforce_executor_lane true
$HERMES -p vrdemo config set delegation.backends.vibe-relay.stop_used_percent 90
# toy under /tmp (camera-safe):
TOY={toy_disp}
# Or: python scripts/live_demo.py --setup-only
```

## On-camera sequence (~60 s terminal)

### 1. Status (quota, lane, stop%)

```bash
export HERMES_HOME={desktop}
cd "$TOY"
$HERMES -p vrdemo chat -Q -q "/vibe" --in "$TOY"
```

Expect: windows + used/remaining, policy, `stop≥90%`, pending park `(none)`.

### 2. Task → delegate → DF gate FAIL→PASS → vibe_accept ACCEPT

```bash
$HERMES -p vrdemo chat -Q -q "The slugify tests fail. Fix it via delegate_task with allowed=[\\"textutil/**\\"] and gate=\\"python .devframework/check.py finish\\". Only touch textutil/. The DEV Framework finish gate must FAIL before the fix and PASS after. Then call vibe_accept." --in "$TOY"
```

Expect (~{elapsed or 90}–180 s wall; cut to highlights): `delegate_task` → haiku executor → finish FAIL then PASS → `vibe_accept` **ACCEPT**.

### 3. Lower stop below current usage → next request stopped + auto-parked

Read current used% from `/vibe` or `hermes -p vrdemo usage`. Set stop just below it (example: used 66% → stop 50):

```bash
$HERMES -p vrdemo config set delegation.backends.vibe-relay.stop_used_percent 50
$HERMES -p vrdemo plugins disable vibe-relay && $HERMES -p vrdemo plugins enable vibe-relay
$HERMES -p vrdemo chat -Q -q "Continue: add a docstring-only tweak via delegate_task." --in "$TOY"
```

Expect: tool error `vibe-relay: quota used N% ≥ stop M% — work parked; resume scheduled at <local time>…`; handoff under `docs/vibe-park.md`; **one** cron job.

```bash
$HERMES -p vrdemo cron list
```

Show the `vibe-relay-resume-after-reset` line (schedule = real reset + buffer).

### 4. Trigger the job now (supported CLI) → resumed session continues

LIVE Hermes supports an immediate trigger:

- CLI parser: `hermes_cli/subcommands/cron.py` L196–198 (`hermes cron run <job_id>`)
- Handler: `hermes_cli/cron.py` `_job_action` L876–908 / wired at L1028

```bash
JOB=$( $HERMES -p vrdemo cron list | awk '/vibe-relay-resume-after-reset/ {{print $1}}' | head -1 )
$HERMES -p vrdemo cron run "$JOB"
```

Expect: session reads the handoff and re-runs the DF gate / continues parked work.

### 5. Restore stop_used_percent afterwards

```bash
$HERMES -p vrdemo config set delegation.backends.vibe-relay.stop_used_percent 90
$HERMES -p vrdemo plugins disable vibe-relay && $HERMES -p vrdemo plugins enable vibe-relay
```

## What to say

1. `/vibe` — quota, lane, stop%.
2. Coding work goes to haiku via `delegate_task`; parent cannot write when the executor lane is on.
3. Gate is DEV Framework **finish**, not bare unittest — FAIL before, PASS after; ACCEPT needs that PASS.
4. Lower stop → auto-park once (one cron); `hermes cron run` fires it now without waiting for the real reset.
5. Restore stop to 90 when done.

## Negative (off-camera / cutaway)

Scripted out-of-scope README edit → `vibe_accept` **REJECT** (see `out/live-demo/run-*/negative_verdict.json`). Labeled scripted — not a live model spend.
""",
        encoding="utf-8",
    )


def print_verdict_table(grade: Dict[str, Any], negative: Dict[str, Any]) -> None:
    checks = grade.get("checks") or {}
    print("\n=== R5 VERDICT TABLE ===")
    print(f"{'check':<28} {'result'}")
    print("-" * 42)
    rows = [
        ("tests_pass", checks.get("tests_pass")),
        ("diff ⊆ allowed", checks.get("diff_subseteq_allowed")),
        ("executor_lane", checks.get("executor_lane")),
        ("executor_haiku", checks.get("executor_haiku")),
        ("delegate_called", checks.get("delegate_called")),
        ("parent_edit blocked/absent", checks.get("parent_edit_blocked_or_absent")),
        ("accept ACCEPT", checks.get("accept_verdict")),
        ("overall", grade.get("verdict")),
        ("negative REJECT", negative.get("verdict")),
    ]
    for name, val in rows:
        print(f"{name:<28} {val}")
    print(f"policy: {grade.get('policy')} ({grade.get('policy_reason')})")
    print(f"lane: {grade.get('lane')} — {grade.get('route_reason')}")
    print(f"files: {grade.get('changed_files')}")
    print("--- final reply ---")
    print(grade.get("final_reply") or "(empty)")
    print("=== END VERDICT ===\n")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--setup-only", action="store_true", help="Create toy repo + configure; do not chat")
    parser.add_argument("--run", type=int, default=1, help="Run index under out/live-demo/run-N")
    parser.add_argument("--skip-live", action="store_true", help="Skip live chat (grade existing toy / negative only)")
    parser.add_argument("--toy", type=str, default="", help="Reuse an existing toy repo path")
    parser.add_argument("--policy", type=str, default="", help="Force policy auto|executor-always")
    args = parser.parse_args(list(argv) if argv is not None else None)

    out_root().mkdir(parents=True, exist_ok=True)
    run_dir = out_root() / f"run-{args.run}"
    if run_dir.exists():
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True)

    ensure_plugin_symlink()
    # Drop stale last_route / last_accept from a prior demo so grading is not polluted.
    state_path = profile_home() / "plugin-data" / PLUGIN_NAME / "state.json"
    if state_path.exists():
        try:
            state_path.write_text("{}\n", encoding="utf-8")
        except OSError:
            pass
    usage = read_usage()
    write_json(run_dir / "usage.json", usage)
    seed_plugin_quota(usage)
    if args.policy:
        policy, policy_reason = args.policy, f"forced via --policy {args.policy}"
    else:
        policy, policy_reason = choose_policy(usage)
    configure_backend(policy=policy)
    reload_plugin_into_gateway()
    # Re-seed after reload/clear so the gateway session sees used% for stop/routing.
    seed_plugin_quota(usage)
    write_json(
        run_dir / "config_snapshot.json",
        {
            "policy": policy,
            "policy_reason": policy_reason,
            "usage": usage,
            "executor_provider": "anthropic",
            "executor_model": "claude-haiku-4-5",
            "profile": PROFILE,
            "enforce_executor_lane": True,
            "gateway_plugin_reloaded": True,
        },
    )

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if args.toy:
        toy = Path(args.toy).expanduser().resolve()
    else:
        # Brief: under /tmp/vr-demo-* (camera-safe). Resolve for macOS /tmp symlink
        # so vendored df_init (no symlink ancestors) can install.
        toy = (Path("/tmp") / f"vr-demo-{ts}").resolve()
        create_toy_repo(toy)
    write_json(run_dir / "toy_path.json", {"toy": str(toy)})

    if args.setup_only:
        print(f"TOY={toy}")
        print(f"POLICY={policy}")
        print(policy_reason)
        return 0

    chat: Dict[str, Any] = {"stdout": "", "stderr": "", "elapsed_seconds": 0, "returncode": 0}
    if not args.skip_live:
        print(f"Live chat starting in {toy} (policy={policy})…", flush=True)
        chat = run_live_chat(toy=toy, run_dir=run_dir, prompt=POSITIVE_PROMPT)
        write_json(
            run_dir / "timings.json",
            {"elapsed_seconds": chat.get("elapsed_seconds"), "returncode": chat.get("returncode")},
        )

    grade = grade_positive(toy=toy, chat=chat, policy=policy, policy_reason=policy_reason)
    write_json(run_dir / "verdict.json", grade)
    (run_dir / "final_reply.txt").write_text(grade.get("final_reply") or "", encoding="utf-8")
    (run_dir / "accept.txt").write_text(str(grade.get("accept_text") or ""), encoding="utf-8")

    negative = scripted_negative_reject(run_dir, toy)
    write_recording_md(toy=toy, policy=policy, policy_reason=policy_reason, grade=grade)
    print_verdict_table(grade, negative)

    summary = {
        "positive": grade.get("verdict"),
        "negative": negative.get("verdict"),
        "policy": policy,
        "toy": str(toy),
        "run_dir": str(run_dir),
    }
    write_json(run_dir / "summary.json", summary)
    return 0 if grade.get("verdict") == "PASS" and negative.get("verdict") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
