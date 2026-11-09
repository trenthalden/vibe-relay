#!/usr/bin/env python3
"""R3 live demo harness for vibe-relay (disposable vrdemo profile + toy repo).

Creates a fresh toy git repo, runs one real Hermes parent turn that should
delegate via vibe-relay to a cheap executor, grades the result, and records a
scripted out-of-scope REJECT case. Outputs land under out/live-demo/ (gitignored).

Paths are derived from $HOME / $HERMES_HOME — never hard-code owner paths other
than the disposable profile name ``vrdemo`` required by the brief.
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
GATE_CMD = "python -m unittest discover -s tests -v"
POSITIVE_PROMPT = (
    "The slugify tests fail. Fix it so the unit tests pass. "
    "Only touch textutil/ — nothing else."
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


def create_toy_repo(dest: Path) -> Path:
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
        "# toy-slugify\n\nTiny package used by the vibe-relay live demo. `textutil.slugify` starts broken.\n",
        encoding="utf-8",
    )
    (dest / ".gitignore").write_text("__pycache__/\n*.py[cod]\n.pytest_cache/\n", encoding="utf-8")
    run(["git", "init"], cwd=dest)
    run(["git", "config", "user.email", "demo@example.com"], cwd=dest)
    run(["git", "config", "user.name", "vibe-relay demo"], cwd=dest)
    run(["git", "add", "-A"], cwd=dest)
    run(["git", "commit", "-m", "chore: broken slugify baseline for live demo"], cwd=dest)
    # Prove RED
    red = run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
        cwd=dest,
        check=False,
    )
    if red.returncode == 0:
        raise SystemExit("toy repo unexpectedly GREEN before the fix")
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


def configure_backend(*, policy: str) -> None:
    env = hermes_env()
    pairs = [
        ("delegation.backend", PLUGIN_NAME),
        ("delegation.backends.vibe-relay.executor_provider", "anthropic"),
        ("delegation.backends.vibe-relay.executor_model", "claude-haiku-4-5"),
        ("delegation.backends.vibe-relay.policy", policy),
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
    return run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
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
    checks = {
        "tests_pass": gate_pass,
        "diff_subseteq_allowed": gate_pass and not outside and bool(changed),
        "executor_lane": executor_lane or lane == "executor",
        "accept_verdict": accept_hit,
        "delegate_called": stream_mentions(stream, "delegate_task")
        or bool(last_route.get("goal"))
        or bool(last_route.get("allowed")),
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
            checks["accept_verdict"],
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
        f"""# vibe-relay live demo — recording sheet (R3)

Disposable profile: **vrdemo**. Cheap executor: `anthropic` / `claude-haiku-4-5`.
Routing policy used: **`{policy}`** — {policy_reason}.

## One-time setup (hide this noise off-camera)

```bash
export HERMES_HOME={desktop}
HERMES={hermes}
# plugin already symlinked + enabled in vrdemo; re-apply config if needed:
$HERMES -p vrdemo config set delegation.backend vibe-relay
$HERMES -p vrdemo config set delegation.backends.vibe-relay.executor_provider anthropic
$HERMES -p vrdemo config set delegation.backends.vibe-relay.executor_model claude-haiku-4-5
$HERMES -p vrdemo config set delegation.backends.vibe-relay.policy {policy}
# toy repo (or reuse):
TOY={toy_disp}
```

Optional helper (creates a fresh toy + prints the chat command):

```bash
python scripts/live_demo.py --setup-only
```

## On-camera (1–2 commands)

Expected wall time: **~{elapsed or 90}–180 s** for the successful chat turn (parent sonnet + haiku executor + unittest gate).

```bash
export HERMES_HOME={desktop}
cd "$TOY"
$HERMES -p vrdemo chat -Q -q "The slugify unit tests are failing. Fix the bug so all tests pass. Only touch files under textutil/." --in "$TOY"
```

Optional status beat before the fix (≈5 s):

```bash
$HERMES -p vrdemo chat -Q -q "/vibe" --in "$TOY"
```

## What to show / say

1. `/vibe` or the chat start — policy `{policy}`, executor haiku.
2. Parent calls `delegate_task` with `allowed=textutil/**` and the unittest gate.
3. Child label shows **executor** (reason from quota/policy).
4. Gate goes RED→GREEN; `vibe_accept` prints **ACCEPT**.
5. Honesty line if policy is `executor-always`: remaining quota would have preferred self under `auto`.

## Negative (off-camera / cutaway)

Scripted out-of-scope README edit → `vibe_accept` **REJECT** (see `out/live-demo/run-*/negative_verdict.json`). Labeled scripted — not a second live model spend.
""",
        encoding="utf-8",
    )


def print_verdict_table(grade: Dict[str, Any], negative: Dict[str, Any]) -> None:
    checks = grade.get("checks") or {}
    rows = [
        ("tests_pass", checks.get("tests_pass")),
        ("diff ⊆ allowed", checks.get("diff_subseteq_allowed")),
        ("executor_lane", checks.get("executor_lane")),
        ("accept ACCEPT", checks.get("accept_verdict")),
        ("overall", grade.get("verdict")),
        ("negative REJECT", negative.get("verdict")),
    ]
    print("\n=== R3 VERDICT TABLE ===")
    print(f"{'check':<22} {'result'}")
    print("-" * 36)
    for name, val in rows:
        print(f"{name:<22} {val}")
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
    usage = read_usage()
    write_json(run_dir / "usage.json", usage)
    if args.policy:
        policy, policy_reason = args.policy, f"forced via --policy {args.policy}"
    else:
        policy, policy_reason = choose_policy(usage)
    configure_backend(policy=policy)
    write_json(
        run_dir / "config_snapshot.json",
        {
            "policy": policy,
            "policy_reason": policy_reason,
            "usage": usage,
            "executor_provider": "anthropic",
            "executor_model": "claude-haiku-4-5",
            "profile": PROFILE,
        },
    )

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if args.toy:
        toy = Path(args.toy).expanduser().resolve()
    else:
        toy = profile_home() / "cache" / f"toy-{ts}"
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
