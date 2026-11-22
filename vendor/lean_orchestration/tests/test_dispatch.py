"""Portion 2 scenarios — orch_dispatch, guards, ledger, restart (FR-011/012/013/017).

CORRECTION-1: output.log, wait-based supervisor, keyless inject via contextvars,
expanded bypass table, no session_key in schemas.
"""
from __future__ import annotations

import contextvars
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from lo_core.bypass import bypass_table_cases, code_starts_executor, command_starts_executor
from lo_core.config import merge_config
from lo_core.envelope import seal_context, verify_envelope
from lo_exec import dispatch as dispatch_mod
from lo_exec import ledger as ledger_mod
from tests.fixtures import base_brief, make_fixture, write_brief

REPO_ROOT = Path(__file__).resolve().parents[1]
CLI = str(REPO_ROOT / "cli.py")
FAKE_EXEC = str(REPO_ROOT / "tests" / "fake_executor.py")
PY39 = "/usr/bin/python3" if os.path.isfile("/usr/bin/python3") else sys.executable
PY314 = "/opt/homebrew/bin/python3"

# ContextVar set by tests before orch_dispatch; waiter inject must see it (C1).
WAITER_MARKER = contextvars.ContextVar("lo_c1_waiter_marker", default=None)


def _pid_runnable(pid: int) -> bool:
    """Windows-safe liveness, excluding POSIX zombies awaiting reaping."""
    if not ledger_mod.pid_alive(pid):
        return False
    if os.name == "nt":
        return True
    try:
        out = subprocess.check_output(
            ["ps", "-p", str(pid), "-o", "state="],
            stderr=subprocess.DEVNULL,
        ).decode("utf-8", "replace").strip()
    except (subprocess.CalledProcessError, OSError):
        return False
    if not out:
        return False
    return not out.upper().startswith("Z")


class FakeCtx:
    def __init__(self, *, with_data_dir: bool = True, config: Optional[Dict[str, Any]] = None) -> None:
        self.tools: Dict[str, Any] = {}
        self.hooks: Dict[str, Any] = {}
        self._config: Dict[str, Any] = dict(config or {})
        # (content, session_key_or_SENTINEL, waiter_marker)
        self.injected: List[Tuple[str, Any, Any]] = []
        self.posted: List[str] = []
        self.inject_return = True
        self.spawned: List[Any] = []
        if with_data_dir:
            self.state = type("S", (), {})()
            self.state.data_dir = Path(tempfile.mkdtemp(prefix="lo-plugin-")) / "plugin-data"
            self.state.data_dir.mkdir(parents=True, exist_ok=True)
        else:
            self.state = type("S", (), {})()

    def register_tool(self, name: str, toolset: str, schema: dict, handler: Any, **kwargs: Any) -> None:
        self.tools[name] = {"toolset": toolset, "schema": schema, "handler": handler, "kwargs": kwargs}

    def register_hook(self, hook_name: str, callback: Any, **kwargs: Any) -> None:
        self.hooks[hook_name] = callback

    def get_config(self, key: str, default: Any = None) -> Any:
        return self._config.get(key, default)

    def inject_message(self, content: str, role: str = "user", **kwargs: Any) -> bool:
        del role
        # Record whether session_key was supplied (must not be for C1 keyless inject).
        sk = kwargs["session_key"] if "session_key" in kwargs else object()
        marker = WAITER_MARKER.get()
        self.injected.append((content, sk if "session_key" in kwargs else None, marker))
        # Silence unused when key absent — sk placeholder above.
        del sk
        return bool(self.inject_return)

    def post_message(self, text: str) -> Any:
        self.posted.append(text)
        return {"ok": True}

    def spawn_task(self, coro, *, name: Optional[str] = None) -> Any:
        self.spawned.append(name or "task")
        try:
            coro.close()
        except Exception:  # noqa: BLE001
            pass
        return None


def _load_plugin():
    import importlib.util

    spec = importlib.util.spec_from_file_location("lean_orchestration_plugin", REPO_ROOT / "__init__.py")
    assert spec and spec.loader
    plugin = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(plugin)
    plugin._reset_runtime_state()
    return plugin


def _executor_config(timeout_s: int = 30, grace_s: int = 2, sleep: float = 0.0, **extra: Any) -> Dict[str, Any]:
    argv = [
        PY39,
        "-B",
        FAKE_EXEC,
        "--task-file",
        "{task_file}",
        "--receipt",
        "{receipt}",
        "--sleep",
        str(sleep),
        "--write-receipt",
        "--exit",
        "0",
    ]
    if extra.pop("spawn_grandchild", False):
        argv.append("--spawn-grandchild")
        argv.extend(["--grandchild-sleep", str(extra.pop("grandchild_sleep", 30))])
    if "exit_code" in extra:
        idx = argv.index("--exit")
        argv[idx + 1] = str(extra.pop("exit_code"))
    return {
        "executors": {
            "fake": {
                "argv": argv,
                "timeout_s": timeout_s,
                "grace_s": grace_s,
                "binary_names": ["cursor-agent", "fake-executor"],
            }
        },
        "launcher_scripts": ["run_cursor.py"],
        "watch_interval_s": 0.2,
        "max_timeout_s": 7200,
    }


def _pass_brief(repo: str) -> str:
    text = base_brief(architecture_rules="UI-1, JOB-1, BULK-2 apply.")
    return write_brief(os.path.join(tempfile.mkdtemp(prefix="lo-brief-"), "brief.md"), text)


def _refuse_brief(repo: str) -> str:
    text = base_brief().replace("## Known errors\n\nKE-2026-09-23-MIRROR-FULL-SCAN — relevant prior defect.\n\n", "")
    return write_brief(os.path.join(tempfile.mkdtemp(prefix="lo-brief-"), "brief.md"), text)


def _research_brief() -> str:
    text = (
        "# BRIEF\n\n"
        "## INTENT\n\nResearch the due-hint timer.\n\n"
        "## FORBIDDEN\n\n- writing code\n\n"
    )
    return write_brief(os.path.join(tempfile.mkdtemp(prefix="lo-brief-"), "brief.md"), text)


def _wait_injected(ctx: FakeCtx, *, timeout_s: float = 30.0) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if ctx.injected:
            return
        time.sleep(0.05)


class TestRefusedStartsNoProcess(unittest.TestCase):
    def test_refused_brief_no_run_dir_no_ledger(self) -> None:
        plugin = _load_plugin()
        cfg = _executor_config()
        ctx = FakeCtx(config=cfg)
        plugin.register(ctx)
        repo = make_fixture()
        brief = _refuse_brief(repo)
        workdir = tempfile.mkdtemp(prefix="lo-wd-")
        handler = ctx.tools["orch_dispatch"]["handler"]
        raw = handler(
            {
                "repo": repo,
                "brief": brief,
                "workdir": workdir,
                "executor": "fake",
            }
        )
        result = json.loads(raw)
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("decision"), "REFUSED")
        data_dir = str(ctx.state.data_dir)
        runs = os.path.join(data_dir, "runs")
        self.assertTrue(not os.path.isdir(runs) or not os.listdir(runs))
        led = ledger_mod.ledger_path(data_dir)
        self.assertTrue(not os.path.isfile(led) or "dispatched" not in open(led, encoding="utf-8").read())


class TestPassStartsSupervisor(unittest.TestCase):
    def test_pass_delivers_rules_in_task_and_output_log(self) -> None:
        plugin = _load_plugin()
        cfg = _executor_config(sleep=0.05)
        ctx = FakeCtx(config=cfg)
        plugin.register(ctx)
        repo = make_fixture()
        brief = _pass_brief(repo)
        workdir = tempfile.mkdtemp(prefix="lo-wd-")
        handler = ctx.tools["orch_dispatch"]["handler"]
        raw = handler(
            {
                "repo": repo,
                "brief": brief,
                "workdir": workdir,
                "executor": "fake",
            }
        )
        result = json.loads(raw)
        self.assertTrue(result.get("ok"), result)
        run_dir = result["run_dir"]
        task_path = os.path.join(run_dir, "TASK.md")
        with open(task_path, "r", encoding="utf-8") as fh:
            task = fh.read()
        self.assertIn("Project rules delivered by lean-orchestration", task)
        self.assertIn("JOB-1", task)
        exit_info = dispatch_mod.wait_for_exit(run_dir, timeout_s=30)
        self.assertIsNotNone(exit_info)
        self.assertEqual(exit_info.get("rc"), 0)
        self.assertTrue(exit_info.get("receipt_present"))
        log_path = os.path.join(run_dir, "output.log")
        self.assertTrue(os.path.isfile(log_path), "output.log missing")
        with open(log_path, "rb") as fh:
            log_bytes = fh.read()
        self.assertIn(b"fake-executor:start", log_bytes)


class TestWallTimeoutKillsGroup(unittest.TestCase):
    def test_timeout_kills_grandchild(self) -> None:
        plugin = _load_plugin()
        cfg = _executor_config(timeout_s=1, grace_s=1)
        cfg["executors"]["fake"]["argv"] = [
            PY39,
            "-B",
            FAKE_EXEC,
            "--task-file",
            "{task_file}",
            "--receipt",
            "{receipt}",
            "--sleep",
            "30",
            "--spawn-grandchild",
            "--grandchild-sleep",
            "60",
            "--exit",
            "0",
        ]
        ctx = FakeCtx(config=cfg)
        plugin.register(ctx)
        repo = make_fixture()
        brief = _pass_brief(repo)
        workdir = tempfile.mkdtemp(prefix="lo-wd-")
        handler = ctx.tools["orch_dispatch"]["handler"]
        raw = handler(
            {
                "repo": repo,
                "brief": brief,
                "workdir": workdir,
                "executor": "fake",
            }
        )
        result = json.loads(raw)
        self.assertTrue(result.get("ok"), result)
        run_dir = result["run_dir"]
        exit_info = dispatch_mod.wait_for_exit(run_dir, timeout_s=30)
        self.assertIsNotNone(exit_info)
        self.assertTrue(exit_info.get("timed_out"), exit_info)
        gc_path = os.path.join(run_dir, "TASK.md.grandchild_pid")
        deadline = time.time() + 5
        self.assertTrue(os.path.isfile(gc_path), "grandchild pid evidence missing")
        with open(gc_path, "r", encoding="utf-8") as fh:
            gc_pid = int(fh.read().strip())
        self.assertGreater(gc_pid, 0)
        while time.time() < deadline:
            if not _pid_runnable(gc_pid):
                break
            time.sleep(0.1)
        self.assertFalse(_pid_runnable(gc_pid), "grandchild still runnable pid=%s" % gc_pid)
        sup = int(result["pid"])
        while time.time() < deadline:
            if os.name != "nt":
                try:
                    os.waitpid(sup, os.WNOHANG)  # windows-footgun: ok — POSIX-only reap.
                except ChildProcessError:
                    break
            if not _pid_runnable(sup):
                break
            time.sleep(0.1)
        self.assertFalse(_pid_runnable(sup), "supervisor still runnable pid=%s" % sup)


class TestRestartReconcile(unittest.TestCase):
    def test_three_reconcile_cases(self) -> None:
        data_dir = tempfile.mkdtemp(prefix="lo-data-")
        run_alive = os.path.join(data_dir, "runs", "aaaaaaaaaaaa")
        run_exit = os.path.join(data_dir, "runs", "bbbbbbbbbbbb")
        run_gone = os.path.join(data_dir, "runs", "cccccccccccc")
        for d in (run_alive, run_exit, run_gone):
            os.makedirs(d, exist_ok=True)

        alive = subprocess.Popen([PY39, "-c", "import time; time.sleep(30)"])
        led = ledger_mod.ledger_path(data_dir)
        ledger_mod.append_event(
            led,
            {
                "event": "dispatched",
                "id": "aaaaaaaaaaaa",
                "executor": "fake",
                "workdir": data_dir,
                "brief_sha256": "x",
                "task_sha256": "y",
                "pid": alive.pid,
                "pgid": alive.pid,
                "started": time.time(),
                "timeout": 60,
                "run_dir": run_alive,
            },
        )
        ledger_mod.write_exit_json(
            os.path.join(run_exit, "exit.json"),
            {
                "rc": 0,
                "seconds": 1.0,
                "timed_out": False,
                "receipt_present": True,
                "ended": "now",
            },
        )
        ledger_mod.append_event(
            led,
            {
                "event": "dispatched",
                "id": "bbbbbbbbbbbb",
                "executor": "fake",
                "workdir": data_dir,
                "brief_sha256": "x",
                "task_sha256": "y",
                "pid": 1,
                "pgid": 1,
                "started": time.time(),
                "timeout": 60,
                "run_dir": run_exit,
            },
        )
        ledger_mod.append_event(
            led,
            {
                "event": "dispatched",
                "id": "cccccccccccc",
                "executor": "fake",
                "workdir": data_dir,
                "brief_sha256": "x",
                "task_sha256": "y",
                "pid": 99999999,
                "pgid": 99999999,
                "started": time.time(),
                "timeout": 60,
                "run_dir": run_gone,
            },
        )
        try:
            actions = dispatch_mod.reconcile(data_dir)
            kinds = {a.get("id"): a for a in actions}
            self.assertEqual(kinds["aaaaaaaaaaaa"].get("event"), "watching")
            self.assertEqual(kinds["bbbbbbbbbbbb"].get("event"), "ended")
            self.assertEqual(kinds["bbbbbbbbbbbb"].get("evidence"), "exit_json")
            self.assertEqual(kinds["cccccccccccc"].get("event"), "ended")
            self.assertEqual(kinds["cccccccccccc"].get("evidence"), "supervisor_gone")
            self.assertIsNone(kinds["cccccccccccc"].get("rc"))
        finally:
            alive.kill()
            alive.wait(timeout=5)


class TestCompletionInjectedOnce(unittest.TestCase):
    def test_inject_once_keyless_and_contextvar(self) -> None:
        plugin = _load_plugin()
        cfg = _executor_config(sleep=0.05)
        ctx = FakeCtx(config=cfg)
        plugin.register(ctx)
        repo = make_fixture()
        brief = _pass_brief(repo)
        workdir = tempfile.mkdtemp(prefix="lo-wd-")
        token = WAITER_MARKER.set("marker-from-caller")
        try:
            handler = ctx.tools["orch_dispatch"]["handler"]
            raw = handler(
                {
                    "repo": repo,
                    "brief": brief,
                    "workdir": workdir,
                    "executor": "fake",
                }
            )
        finally:
            WAITER_MARKER.reset(token)
        result = json.loads(raw)
        self.assertTrue(result.get("ok"), result)
        run_dir = result["run_dir"]
        self.assertIsNotNone(dispatch_mod.wait_for_exit(run_dir, timeout_s=30))
        _wait_injected(ctx, timeout_s=15)
        self.assertEqual(len(ctx.injected), 1, ctx.injected)
        content, sk, marker = ctx.injected[0]
        self.assertIn(result["id"], content)
        self.assertIsNone(sk, "inject_message must not receive session_key")
        self.assertEqual(marker, "marker-from-caller")
        # Waiter already ended the dispatch; a second notify must not inject again.
        time.sleep(0.3)
        self.assertEqual(len(ctx.injected), 1)

    def test_post_restart_pending_delivered_once(self) -> None:
        plugin = _load_plugin()
        cfg = _executor_config()
        ctx = FakeCtx(config=cfg)
        plugin.register(ctx)
        repo = make_fixture()
        brief = _pass_brief(repo)
        workdir = tempfile.mkdtemp(prefix="lo-wd-")

        # Simulate restart: open dispatch already finished (exit.json), inject path gone.
        plugin._reset_runtime_state()
        ctx2 = FakeCtx(config=cfg)
        ctx2.state.data_dir = ctx.state.data_dir
        ctx2.inject_return = False
        new_id = "dddddddddddd"
        new_run = os.path.join(str(ctx2.state.data_dir), "runs", new_id)
        os.makedirs(new_run, exist_ok=True)
        ledger_mod.write_exit_json(
            os.path.join(new_run, "exit.json"),
            {"rc": 0, "seconds": 0.1, "timed_out": False, "receipt_present": False, "ended": "t"},
        )
        ledger_mod.append_event(
            ledger_mod.ledger_path(str(ctx2.state.data_dir)),
            {
                "event": "dispatched",
                "id": new_id,
                "executor": "fake",
                "workdir": workdir,
                "brief_sha256": "a",
                "task_sha256": "b",
                "pid": 1,
                "pgid": 1,
                "started": time.time(),
                "timeout": 10,
                "run_dir": new_run,
            },
        )
        plugin.register(ctx2)
        # Reconcile queued pending (no inject after restart).
        self.assertEqual(len(ctx2.injected), 0)
        # Next orch_gate delivers pending via post_message exactly once.
        ctx2.tools["orch_gate"]["handler"]({"repo": repo, "brief": brief})
        self.assertEqual(sum(1 for p in ctx2.posted if new_id in p), 1)
        ctx2.tools["orch_gate"]["handler"]({"repo": repo, "brief": brief})
        self.assertEqual(sum(1 for p in ctx2.posted if new_id in p), 1)


class TestSchemasNoSessionKey(unittest.TestCase):
    def test_schemas_have_no_session_key(self) -> None:
        plugin = _load_plugin()
        ctx = FakeCtx(config=_executor_config())
        plugin.register(ctx)
        for name in ("orch_gate", "orch_dispatch"):
            schema = ctx.tools[name]["schema"]
            props = schema.get("parameters", {}).get("properties", {})
            self.assertNotIn("session_key", props, name)


class TestBypassCorrection1Cases(unittest.TestCase):
    """C1 table additions as their own cases (API-4)."""

    def test_newline_and_ampersand_separators(self) -> None:
        binaries = ["cursor-agent"]
        launchers = ["run_cursor.py"]
        self.assertIsNotNone(command_starts_executor("cd x\ncursor-agent -p y", binaries, launchers))
        self.assertIsNotNone(command_starts_executor("sleep 1 & cursor-agent -p", binaries, launchers))

    def test_interpreter_absolute_paths(self) -> None:
        binaries = ["cursor-agent"]
        launchers = ["run_cursor.py"]
        self.assertIsNotNone(
            command_starts_executor(
                "/usr/bin/python3 -B temp/run-20261120/run_cursor.py L1 /repo 3600",
                binaries,
                launchers,
            )
        )
        self.assertIsNotNone(
            command_starts_executor("/opt/homebrew/bin/python3 run_cursor.py", binaries, launchers)
        )

    def test_wrapper_basenames_and_shell_c(self) -> None:
        binaries = ["cursor-agent"]
        launchers = ["run_cursor.py"]
        for cmd in (
            "sudo cursor-agent -p",
            "bash -c 'cursor-agent -p x'",
            "/usr/bin/env cursor-agent -p",
            "time cursor-agent -p",
        ):
            self.assertIsNotNone(command_starts_executor(cmd, binaries, launchers), msg=cmd)

    def test_execute_code_ast_popen(self) -> None:
        binaries = ["cursor-agent"]
        launchers = ["run_cursor.py"]
        code = 'subprocess.Popen(["/Users/x/.local/bin/cursor-agent", "-p"])'
        self.assertIsNotNone(code_starts_executor(code, binaries, launchers))
        plugin = _load_plugin()
        ctx = FakeCtx(config=_executor_config())
        plugin.register(ctx)
        blocked = ctx.hooks["pre_tool_call"]("execute_code", {"code": code})
        self.assertEqual(blocked["action"], "block")

    def test_allow_list_c1(self) -> None:
        binaries = ["cursor-agent"]
        launchers = ["run_cursor.py"]
        for cmd in (
            "cat run_cursor.py",
            'python3 -c "print(1)"',
            "ls ~/.local/bin/cursor-agent",
            "cursor-agent --version",
            "grep -n run_cursor.py notes.md",
        ):
            self.assertIsNone(command_starts_executor(cmd, binaries, launchers), msg=cmd)


class TestBypassCorrection2Assignments(unittest.TestCase):
    """C2: leading NAME=value without env (API-4)."""

    def test_leading_assignments_block(self) -> None:
        binaries = ["cursor-agent"]
        launchers = ["run_cursor.py"]
        for cmd in (
            "X=1 cursor-agent -p hi",
            "FOO=bar /Users/dev/.local/bin/cursor-agent -p x",
            'A= B="x y" nohup cursor-agent -p',
            "cd /r && TMPDIR=/t /usr/bin/python3 -B temp/run/run_cursor.py L1 /r 60",
        ):
            self.assertIsNotNone(command_starts_executor(cmd, binaries, launchers), msg=cmd)

    def test_leading_assignments_allow(self) -> None:
        binaries = ["cursor-agent"]
        launchers = ["run_cursor.py"]
        for cmd in (
            "X=1 echo cursor-agent",
            "A=cursor-agent",
            "export A=cursor-agent",
        ):
            self.assertIsNone(command_starts_executor(cmd, binaries, launchers), msg=cmd)

    def test_leading_assignments_via_hook(self) -> None:
        plugin = _load_plugin()
        ctx = FakeCtx(config=_executor_config())
        plugin.register(ctx)
        hook = ctx.hooks["pre_tool_call"]
        for cmd in (
            "X=1 cursor-agent -p hi",
            "FOO=bar /Users/dev/.local/bin/cursor-agent -p x",
            'A= B="x y" nohup cursor-agent -p',
            "cd /r && TMPDIR=/t /usr/bin/python3 -B temp/run/run_cursor.py L1 /r 60",
        ):
            blocked = hook("terminal", {"command": cmd})
            self.assertEqual(blocked["action"], "block", msg=cmd)
            self.assertIn("orch_dispatch", blocked["message"])
        for cmd in (
            "X=1 echo cursor-agent",
            "A=cursor-agent",
            "export A=cursor-agent",
        ):
            self.assertIsNone(hook("terminal", {"command": cmd}), msg=cmd)


class TestDelegateGuard(unittest.TestCase):
    def test_no_envelope_tampered_valid(self) -> None:
        plugin = _load_plugin()
        cfg = _executor_config()
        ctx = FakeCtx(config=cfg)
        plugin.register(ctx)
        hook = ctx.hooks["pre_tool_call"]

        blocked = hook("delegate_task", {"goal": "g", "context": "no envelope here"})
        self.assertEqual(blocked["action"], "block")
        self.assertIn("orch_dispatch", blocked["message"])

        goal = "Execute the sealed lean-orchestration task exactly."
        sealed = seal_context(goal, "body rules")
        lines = sealed.splitlines()
        tampered = "TAMPERED\n" + lines[-1] + "\n"
        blocked2 = hook("delegate_task", {"goal": goal, "context": tampered})
        self.assertEqual(blocked2["action"], "block")

        ok = hook("delegate_task", {"goal": goal, "context": sealed})
        self.assertIsNone(ok)

        ok2 = hook("delegate_task", {"tasks": [{"goal": goal, "context": sealed}]})
        self.assertIsNone(ok2)


class TestBypassGuardTable(unittest.TestCase):
    def test_table_and_hook(self) -> None:
        binaries = ["cursor-agent"]
        launchers = ["run_cursor.py"]
        table = bypass_table_cases(binaries, launchers)
        self.assertGreaterEqual(len(table), 12)
        for cmd, expect_block in table:
            hit = command_starts_executor(cmd, binaries, launchers)
            if expect_block:
                self.assertIsNotNone(hit, msg="expected block for %r" % cmd)
            else:
                self.assertIsNone(hit, msg="expected allow for %r" % cmd)

        # C1 execute_code AST case
        ast_code = 'subprocess.Popen(["/Users/x/.local/bin/cursor-agent", "-p"])'
        self.assertIsNotNone(code_starts_executor(ast_code, binaries, launchers))

        plugin = _load_plugin()
        cfg = _executor_config()
        ctx = FakeCtx(config=cfg)
        plugin.register(ctx)
        hook = ctx.hooks["pre_tool_call"]
        blocked = hook("terminal", {"command": "cd x && cursor-agent -p --force"})
        self.assertEqual(blocked["action"], "block")
        self.assertIn("orch_dispatch", blocked["message"])
        # C1 table additions via hook
        for cmd in (
            "cd x\ncursor-agent -p y",
            "sleep 1 & cursor-agent -p",
            "/usr/bin/python3 -B temp/run-20261120/run_cursor.py L1 /repo 3600",
            "/opt/homebrew/bin/python3 run_cursor.py",
            "sudo cursor-agent -p",
            "bash -c 'cursor-agent -p x'",
            "/usr/bin/env cursor-agent -p",
            "time cursor-agent -p",
            "X=1 cursor-agent -p hi",
            "FOO=bar /Users/dev/.local/bin/cursor-agent -p x",
            'A= B="x y" nohup cursor-agent -p',
            "cd /r && TMPDIR=/t /usr/bin/python3 -B temp/run/run_cursor.py L1 /r 60",
        ):
            b = hook("terminal", {"command": cmd})
            self.assertEqual(b["action"], "block", msg=cmd)
        for cmd in (
            "cat run_cursor.py",
            'python3 -c "print(1)"',
            "ls ~/.local/bin/cursor-agent",
            "cursor-agent --version",
            "grep -n run_cursor.py notes.md",
            "echo cursor-agent",
            "X=1 echo cursor-agent",
            "A=cursor-agent",
            "export A=cursor-agent",
        ):
            self.assertIsNone(hook("terminal", {"command": cmd}), msg=cmd)
        blocked_ast = hook("execute_code", {"code": ast_code})
        self.assertEqual(blocked_ast["action"], "block")


class TestResearchProfile(unittest.TestCase):
    def test_research_passes_without_allowed(self) -> None:
        plugin = _load_plugin()
        cfg = _executor_config()
        ctx = FakeCtx(config=cfg)
        plugin.register(ctx)
        repo = make_fixture()
        brief = _research_brief()
        raw = ctx.tools["orch_gate"]["handler"]({"repo": repo, "brief": brief, "profile": "research"})
        result = json.loads(raw)
        self.assertEqual(result["decision"], "PASS", result)
        self.assertEqual(result.get("profile"), "research")


class TestDeterminismDispatchIdAndEnvelope(unittest.TestCase):
    def test_dispatch_id_and_envelope_stable(self) -> None:
        task = b"hello task"
        a = dispatch_mod.make_dispatch_id(task, "/tmp/wd", "fake", 3)
        b = dispatch_mod.make_dispatch_id(task, "/tmp/wd", "fake", 3)
        self.assertEqual(a, b)
        self.assertEqual(len(a), 12)
        goal = "g"
        ctx1 = seal_context(goal, "body")
        ctx2 = seal_context(goal, "body")
        self.assertEqual(ctx1, ctx2)
        ok, reason = verify_envelope(goal, ctx1)
        self.assertTrue(ok, reason)


class TestCliDispatch(unittest.TestCase):
    def test_cli_dispatch_prints_id(self) -> None:
        repo = make_fixture()
        brief = _pass_brief(repo)
        workdir = tempfile.mkdtemp(prefix="lo-wd-")
        data_dir = tempfile.mkdtemp(prefix="lo-data-")
        cfg_path = os.path.join(tempfile.mkdtemp(), "cfg.json")
        with open(cfg_path, "w", encoding="utf-8") as fh:
            json.dump(_executor_config(sleep=0.05), fh)
        env = os.environ.copy()
        env.setdefault("TMPDIR", os.environ.get("TMPDIR") or tempfile.gettempdir())
        env["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.run(
            [
                PY39,
                "-B",
                CLI,
                "dispatch",
                "--repo",
                repo,
                "--brief",
                brief,
                "--workdir",
                workdir,
                "--executor",
                "fake",
                "--data-dir",
                data_dir,
                "--config",
                cfg_path,
            ],
            capture_output=True,
            env=env,
            timeout=60,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace"))
        dispatch_id = proc.stdout.decode("utf-8").strip()
        self.assertEqual(len(dispatch_id), 12)
        run_dir = os.path.join(data_dir, "runs", dispatch_id)
        self.assertIsNotNone(dispatch_mod.wait_for_exit(run_dir, timeout_s=30))

    def test_cli_hermes_child_json_carries_id(self) -> None:
        repo = make_fixture()
        brief = _pass_brief(repo)
        workdir = tempfile.mkdtemp(prefix="lo-wd-")
        data_dir = tempfile.mkdtemp(prefix="lo-data-")
        cfg_path = os.path.join(tempfile.mkdtemp(), "cfg.json")
        with open(cfg_path, "w", encoding="utf-8") as fh:
            json.dump(_executor_config(), fh)
        env = os.environ.copy()
        env.setdefault("TMPDIR", os.environ.get("TMPDIR") or tempfile.gettempdir())
        env["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.run(
            [
                PY39,
                "-B",
                CLI,
                "dispatch",
                "--repo",
                repo,
                "--brief",
                brief,
                "--workdir",
                workdir,
                "--executor",
                "hermes_child",
                "--data-dir",
                data_dir,
                "--config",
                cfg_path,
            ],
            capture_output=True,
            env=env,
            timeout=60,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace"))
        payload = json.loads(proc.stdout.decode("utf-8"))
        self.assertTrue(payload.get("ok"), payload)
        self.assertTrue(payload.get("id"), payload)
        self.assertEqual(payload.get("executor"), "hermes_child")
        self.assertIn("[lo-dispatch:%s]" % payload["id"], payload["delegate_task"]["goal"])
        self.assertTrue(os.path.isdir(os.path.join(data_dir, "runs", payload["id"])))


class TestRedBypassMatcher(unittest.TestCase):
    def test_red_disabled_matcher_on_scratch(self) -> None:
        scratch = Path(tempfile.mkdtemp(prefix="lo-red-bypass-"))
        for name in ("lo_core", "lo_exec", "tests", "cli.py", "__init__.py", "plugin.yaml"):
            src = REPO_ROOT / name
            dst = scratch / name
            if src.is_dir():
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)
        bypass_path = scratch / "lo_core" / "bypass.py"
        text = bypass_path.read_text(encoding="utf-8")
        needle = "names = _name_list(binary_names, launcher_scripts)\n"
        self.assertIn(needle, text)
        mutated = text.replace(
            needle,
            "return None\n    names = _name_list(binary_names, launcher_scripts)\n",
            1,
        )
        bypass_path.write_text(mutated, encoding="utf-8")

        env = os.environ.copy()
        env["PYTHONPATH"] = str(scratch)
        env.setdefault("TMPDIR", os.environ.get("TMPDIR") or tempfile.gettempdir())
        probe = (
            "from lo_core.bypass import command_starts_executor\n"
            "hit = command_starts_executor('cursor-agent -p', ['cursor-agent'], [])\n"
            "assert hit is not None, 'expected block'\n"
        )
        proc = subprocess.run(
            [PY39, "-B", "-c", probe],
            capture_output=True,
            env=env,
            timeout=30,
            cwd=str(scratch),
        )
        self.assertNotEqual(proc.returncode, 0)
        err = (proc.stderr + proc.stdout).decode("utf-8", "replace")
        self.assertIn("expected block", err)

    def test_red_assignment_skip_disabled_on_scratch(self) -> None:
        """VER-3: without leading NAME=value skip, everyday launch slips past."""
        scratch = Path(tempfile.mkdtemp(prefix="lo-red-assign-"))
        for name in ("lo_core", "lo_exec", "tests", "cli.py", "__init__.py", "plugin.yaml"):
            src = REPO_ROOT / name
            dst = scratch / name
            if src.is_dir():
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)
        bypass_path = scratch / "lo_core" / "bypass.py"
        text = bypass_path.read_text(encoding="utf-8")
        needle = "words = _skip_leading_assignments(words)\n"
        self.assertIn(needle, text)
        mutated = text.replace(needle, "words = words\n", 1)
        bypass_path.write_text(mutated, encoding="utf-8")

        env = os.environ.copy()
        env["PYTHONPATH"] = str(scratch)
        env.setdefault("TMPDIR", os.environ.get("TMPDIR") or tempfile.gettempdir())
        probe = (
            "from lo_core.bypass import command_starts_executor\n"
            "hit = command_starts_executor('X=1 cursor-agent -p hi', ['cursor-agent'], [])\n"
            "assert hit is not None, 'expected block for leading assignment'\n"
        )
        proc = subprocess.run(
            [PY39, "-B", "-c", probe],
            capture_output=True,
            env=env,
            timeout=30,
            cwd=str(scratch),
        )
        self.assertNotEqual(proc.returncode, 0)
        err = (proc.stderr + proc.stdout).decode("utf-8", "replace")
        self.assertIn("expected block for leading assignment", err)


class TestHooksRegistered(unittest.TestCase):
    def test_plugin_yaml_and_register(self) -> None:
        yaml_text = (REPO_ROOT / "plugin.yaml").read_text(encoding="utf-8")
        self.assertIn("orch_dispatch", yaml_text)
        self.assertIn("pre_tool_call", yaml_text)
        self.assertIn("on_session_start", yaml_text)
        plugin = _load_plugin()
        ctx = FakeCtx(config=_executor_config())
        plugin.register(ctx)
        self.assertIn("orch_dispatch", ctx.tools)
        self.assertIn("pre_tool_call", ctx.hooks)
        self.assertIn("on_session_start", ctx.hooks)
        # Every registered hook and tool is declared in plugin.yaml (`hermes plugins validate`).
        declared_hooks, declared_tools, section = set(), set(), None
        for line in yaml_text.splitlines():
            if line.startswith("provides_hooks:"):
                section = declared_hooks
            elif line.startswith("provides_tools:"):
                section = declared_tools
            elif line.startswith("  - ") and section is not None:
                section.add(line[4:].strip())
            elif line and not line.startswith(" "):
                section = None
        self.assertEqual(set(ctx.hooks) - declared_hooks, set())
        self.assertEqual(set(ctx.tools) - declared_tools, set())


class TestUnknownExecutor(unittest.TestCase):
    def test_unknown_executor_refused(self) -> None:
        plugin = _load_plugin()
        ctx = FakeCtx(config=_executor_config())
        plugin.register(ctx)
        repo = make_fixture()
        brief = _pass_brief(repo)
        workdir = tempfile.mkdtemp(prefix="lo-wd-")
        raw = ctx.tools["orch_dispatch"]["handler"](
            {
                "repo": repo,
                "brief": brief,
                "workdir": workdir,
                "executor": "no_such_executor",
            }
        )
        result = json.loads(raw)
        self.assertFalse(result.get("ok"))
        self.assertIn("unknown_executor", result.get("reasons") or [])
        data_dir = str(ctx.state.data_dir)
        runs = os.path.join(data_dir, "runs")
        self.assertTrue(not os.path.isdir(runs) or not os.listdir(runs))


def _git_init_workdir(root: str) -> None:
    subprocess.check_call(["git", "init"], cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.check_call(["git", "config", "user.email", "lo@example.com"], cwd=root)
    subprocess.check_call(["git", "config", "user.name", "lo"], cwd=root)
    allowed = os.path.join(root, "app/Sources/AppCore/Run/Timer.swift")
    os.makedirs(os.path.dirname(allowed), exist_ok=True)
    with open(allowed, "w", encoding="utf-8") as fh:
        fh.write("base\n")
    outside = os.path.join(root, "Outside/NotAllowed.swift")
    os.makedirs(os.path.dirname(outside), exist_ok=True)
    with open(outside, "w", encoding="utf-8") as fh:
        fh.write("outside-base\n")
    subprocess.check_call(["git", "add", "-A"], cwd=root, stdout=subprocess.DEVNULL)
    subprocess.check_call(["git", "commit", "-m", "seed"], cwd=root, stdout=subprocess.DEVNULL)


class TestHermesChildPayload(unittest.TestCase):
    def test_hermes_child_returns_sealed_payload(self) -> None:
        plugin = _load_plugin()
        ctx = FakeCtx(config=_executor_config())
        plugin.register(ctx)
        repo = make_fixture()
        brief = _pass_brief(repo)
        workdir = tempfile.mkdtemp(prefix="lo-wd-")
        raw = ctx.tools["orch_dispatch"]["handler"](
            {
                "repo": repo,
                "brief": brief,
                "workdir": workdir,
                "executor": "hermes_child",
            }
        )
        result = json.loads(raw)
        self.assertTrue(result.get("ok"), result)
        self.assertTrue(result.get("id"), result)
        payload = result["delegate_task"]
        ok, reason = verify_envelope(payload["goal"], payload["context"])
        self.assertTrue(ok, reason)
        self.assertIn("[lo-dispatch:%s]" % result["id"], payload["goal"])
        self.assertIn("Project rules delivered by lean-orchestration", payload["context"])
        led = ledger_mod.ledger_path(str(ctx.state.data_dir))
        with open(led, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn('"event":"dispatched"', text)
        self.assertIn(result["id"], text)
        run_dir = result["run_dir"]
        self.assertTrue(os.path.isfile(os.path.join(run_dir, "BRIEF.md")))
        self.assertTrue(os.path.isfile(os.path.join(run_dir, "TASK.md")))

    def test_hermes_child_accept_allowed_and_outside(self) -> None:
        plugin = _load_plugin()
        ctx = FakeCtx(config=_executor_config())
        plugin.register(ctx)
        repo = make_fixture()
        _git_init_workdir(repo)
        brief = _pass_brief(repo)
        raw = ctx.tools["orch_dispatch"]["handler"](
            {"repo": repo, "brief": brief, "workdir": repo, "executor": "hermes_child"}
        )
        result = json.loads(raw)
        self.assertTrue(result.get("ok"), result)
        dispatch_id = result["id"]
        allowed = os.path.join(repo, "app/Sources/AppCore/Run/Timer.swift")
        with open(allowed, "w", encoding="utf-8") as fh:
            fh.write("changed-ok\n")
        acc_raw = ctx.tools["orch_accept"]["handler"]({"dispatch_id": dispatch_id})
        acc = json.loads(acc_raw)
        self.assertEqual(acc["decision"], "ACCEPTED", acc)
        outside = os.path.join(repo, "Outside/NotAllowed.swift")
        with open(outside, "w", encoding="utf-8") as fh:
            fh.write("changed-bad\n")
        # Fresh dispatch for the outside case (base snapshot must precede the outside edit).
        plugin2 = _load_plugin()
        ctx2 = FakeCtx(config=_executor_config())
        plugin2.register(ctx2)
        raw2 = ctx2.tools["orch_dispatch"]["handler"](
            {"repo": repo, "brief": brief, "workdir": repo, "executor": "hermes_child"}
        )
        res2 = json.loads(raw2)
        with open(outside, "w", encoding="utf-8") as fh:
            fh.write("changed-bad-2\n")
        rej = json.loads(ctx2.tools["orch_accept"]["handler"]({"dispatch_id": res2["id"]}))
        self.assertEqual(rej["decision"], "REJECTED", rej)
        self.assertTrue(
            any(str(r).startswith("path_outside_allowed:") for r in rej.get("reasons") or []),
            rej,
        )

    def test_subagent_start_stop_once_and_unknown_ignored(self) -> None:
        plugin = _load_plugin()
        ctx = FakeCtx(config=_executor_config())
        plugin.register(ctx)
        self.assertIn("subagent_start", ctx.hooks)
        self.assertIn("subagent_stop", ctx.hooks)
        repo = make_fixture()
        brief = _pass_brief(repo)
        workdir = tempfile.mkdtemp(prefix="lo-wd-")
        result = json.loads(
            ctx.tools["orch_dispatch"]["handler"](
                {"repo": repo, "brief": brief, "workdir": workdir, "executor": "hermes_child"}
            )
        )
        dispatch_id = result["id"]
        goal = result["delegate_task"]["goal"]
        ctx.hooks["subagent_start"](
            child_session_id="child-sess-1",
            child_goal=goal,
        )
        led = ledger_mod.ledger_path(str(ctx.state.data_dir))
        events = ledger_mod.read_events(led)
        self.assertTrue(
            any(e.get("event") == "child_started" and e.get("id") == dispatch_id for e in events),
            events,
        )
        ctx.hooks["subagent_stop"](
            child_session_id="child-sess-1",
            child_status="completed",
            duration_ms=1500,
        )
        events = ledger_mod.read_events(led)
        ended = [e for e in events if e.get("event") == "ended" and e.get("id") == dispatch_id]
        self.assertEqual(len(ended), 1, events)
        self.assertEqual(ended[0].get("evidence"), "subagent_stop")
        self.assertEqual(ended[0].get("child_status"), "completed")
        self.assertEqual(ended[0].get("seconds"), 1.5)
        self.assertEqual(len(ctx.injected), 1)
        self.assertIn("status=completed", ctx.injected[0][0])
        self.assertIn(dispatch_id, ctx.injected[0][0])
        # Second stop adds nothing.
        ctx.hooks["subagent_stop"](
            child_session_id="child-sess-1",
            child_status="completed",
            duration_ms=1500,
        )
        events2 = ledger_mod.read_events(led)
        self.assertEqual(
            len([e for e in events2 if e.get("event") == "ended" and e.get("id") == dispatch_id]),
            1,
        )
        self.assertEqual(len(ctx.injected), 1)
        # Unknown child ignored.
        before = len(events2)
        ctx.hooks["subagent_start"](child_session_id="other", child_goal="no tag here")
        ctx.hooks["subagent_stop"](child_session_id="other", child_status="completed", duration_ms=10)
        self.assertEqual(len(ledger_mod.read_events(led)), before)

    def test_edited_dispatch_id_in_goal_refused(self) -> None:
        plugin = _load_plugin()
        cfg = _executor_config()
        ctx = FakeCtx(config=cfg)
        plugin.register(ctx)
        repo = make_fixture()
        brief = _pass_brief(repo)
        workdir = tempfile.mkdtemp(prefix="lo-wd-")
        result = json.loads(
            ctx.tools["orch_dispatch"]["handler"](
                {"repo": repo, "brief": brief, "workdir": workdir, "executor": "hermes_child"}
            )
        )
        payload = result["delegate_task"]
        edited_goal = payload["goal"].replace(result["id"], "ffffffffffff")
        blocked = ctx.hooks["pre_tool_call"](
            "delegate_task",
            {"goal": edited_goal, "context": payload["context"]},
        )
        self.assertEqual(blocked["action"], "block")
        self.assertIn("envelope_mismatch", blocked["message"])

    def test_child_stays_open_across_session_start(self) -> None:
        """CORRECTION-1: a new session in the same live host must not host_restarted the child."""
        plugin = _load_plugin()
        ctx = FakeCtx(config=_executor_config())
        plugin.register(ctx)
        repo = make_fixture()
        brief = _pass_brief(repo)
        workdir = tempfile.mkdtemp(prefix="lo-wd-")
        result = json.loads(
            ctx.tools["orch_dispatch"]["handler"](
                {"repo": repo, "brief": brief, "workdir": workdir, "executor": "hermes_child"}
            )
        )
        dispatch_id = result["id"]
        led = ledger_mod.ledger_path(str(ctx.state.data_dir))
        dispatched = [
            e for e in ledger_mod.read_events(led) if e.get("event") == "dispatched" and e.get("id") == dispatch_id
        ][0]
        self.assertEqual(dispatched.get("host_pid"), os.getpid())
        self.assertTrue(dispatched.get("host_started"))
        # Another session on the same profile/data dir.
        ctx.hooks["on_session_start"]()
        open_ids = {str(e.get("id")) for e in ledger_mod.open_dispatches(led)}
        self.assertIn(dispatch_id, open_ids)
        self.assertFalse(any(e.get("event") == "ended" and e.get("id") == dispatch_id for e in ledger_mod.read_events(led)))
        goal = result["delegate_task"]["goal"]
        ctx.hooks["subagent_start"](child_session_id="live-child", child_goal=goal)
        ctx.hooks["subagent_stop"](child_session_id="live-child", child_status="completed", duration_ms=900)
        ended = [e for e in ledger_mod.read_events(led) if e.get("event") == "ended" and e.get("id") == dispatch_id]
        self.assertEqual(len(ended), 1, ended)
        self.assertEqual(ended[0].get("evidence"), "subagent_stop")
        self.assertEqual(len(ctx.injected), 1)

    def test_child_reconcile_host_restarted(self) -> None:
        """CORRECTION-1: dead host_pid → host_restarted (spawn+reap a short child for a dead pid)."""
        dead = subprocess.Popen([sys.executable or "python3", "-c", "pass"])
        dead_pid = dead.pid
        dead.wait(timeout=10)
        data_dir = tempfile.mkdtemp(prefix="lo-data-")
        run_dir = os.path.join(data_dir, "runs", "dddddddddddd")
        os.makedirs(run_dir, exist_ok=True)
        led = ledger_mod.ledger_path(data_dir)
        ledger_mod.append_event(
            led,
            {
                "event": "dispatched",
                "id": "dddddddddddd",
                "executor": "hermes_child",
                "workdir": data_dir,
                "brief_sha256": "x",
                "task_sha256": "y",
                "run_dir": run_dir,
                "profile": "code",
                "base_head": None,
                "base_dirty": [],
                "host_pid": dead_pid,
                "host_started": "dead-process-token",
            },
        )
        actions = dispatch_mod.reconcile(data_dir)
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].get("event"), "ended")
        self.assertEqual(actions[0].get("evidence"), "host_restarted")
        self.assertEqual(actions[0].get("child_status"), "host_restarted")

    def test_red_child_host_liveness_dropped_on_scratch(self) -> None:
        """VER-3: drop host liveness in reconcile → live-host session_start falsely ends the child."""
        scratch = Path(tempfile.mkdtemp(prefix="lo-red-child-host-"))
        for name in ("lo_core", "lo_exec", "tests", "cli.py", "__init__.py", "plugin.yaml"):
            src = REPO_ROOT / name
            dst = scratch / name
            if src.is_dir():
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)
        dispatch_path = scratch / "lo_exec" / "dispatch.py"
        text = dispatch_path.read_text(encoding="utf-8")
        needle = "if ledger_mod.host_process_alive("
        self.assertIn(needle, text)
        # RED: always treat the child host as gone.
        mutated = text.replace(
            needle,
            "if False and ledger_mod.host_process_alive(",
            1,
        )
        self.assertNotEqual(text, mutated)
        dispatch_path.write_text(mutated, encoding="utf-8")
        env = os.environ.copy()
        env["PYTHONPATH"] = str(scratch)
        env.setdefault("TMPDIR", os.environ.get("TMPDIR") or tempfile.gettempdir())
        probe = (
            "import json, os, tempfile\n"
            "from tests.test_dispatch import FakeCtx, _executor_config, _load_plugin, _pass_brief\n"
            "from lo_exec import ledger as ledger_mod\n"
            "from tests.fixtures import make_fixture\n"
            "plugin = _load_plugin()\n"
            "ctx = FakeCtx(config=_executor_config())\n"
            "plugin.register(ctx)\n"
            "repo = make_fixture()\n"
            "result = json.loads(ctx.tools['orch_dispatch']['handler']("
            "{'repo': repo, 'brief': _pass_brief(repo), 'workdir': tempfile.mkdtemp(prefix='lo-wd-'),"
            " 'executor': 'hermes_child'}))\n"
            "dispatch_id = result['id']\n"
            "ctx.hooks['on_session_start']()\n"
            "led = ledger_mod.ledger_path(str(ctx.state.data_dir))\n"
            "open_ids = {str(e.get('id')) for e in ledger_mod.open_dispatches(led)}\n"
            "assert dispatch_id in open_ids, 'expected child to stay open on live host; got ended'\n"
        )
        proc = subprocess.run(
            [PY39, "-B", "-c", probe],
            capture_output=True,
            env=env,
            timeout=60,
            cwd=str(scratch),
        )
        self.assertNotEqual(proc.returncode, 0)
        err = (proc.stderr + proc.stdout).decode("utf-8", "replace")
        self.assertIn("expected child to stay open on live host", err)
        print("RED_CHILD_HOST_ASSERTION=%r" % err.strip().splitlines()[-1][:240])

    def test_orch_counts_child_dispatches(self) -> None:
        from tests.test_accept import FakeCtx as AcceptFakeCtx

        plugin = _load_plugin()
        ctx = AcceptFakeCtx(config=_executor_config())
        plugin.register(ctx)
        repo = make_fixture()
        brief = _pass_brief(repo)
        workdir = tempfile.mkdtemp(prefix="lo-wd-")
        result = json.loads(
            ctx.tools["orch_dispatch"]["handler"](
                {"repo": repo, "brief": brief, "workdir": workdir, "executor": "hermes_child"}
            )
        )
        text = ctx.commands["orch"]["handler"]("")
        self.assertIn(result["id"], text)
        self.assertIn("executor=hermes_child", text)


class TestConfigKeyErrors(unittest.TestCase):
    def test_invalid_executor_argv_names_key(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            merge_config({"executors": {"x": {"argv": "not-a-list"}}})
        self.assertIn("executors.x.argv", str(ctx.exception))

    def test_invalid_delegate_guard_names_key(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            merge_config({"delegate_guard": "maybe"})
        self.assertIn("delegate_guard", str(ctx.exception))


class TestCliRefusedDispatch(unittest.TestCase):
    def test_cli_refused_prints_reason(self) -> None:
        repo = make_fixture()
        brief = _refuse_brief(repo)
        workdir = tempfile.mkdtemp(prefix="lo-wd-")
        data_dir = tempfile.mkdtemp(prefix="lo-data-")
        cfg_path = os.path.join(tempfile.mkdtemp(), "cfg.json")
        with open(cfg_path, "w", encoding="utf-8") as fh:
            json.dump(_executor_config(), fh)
        env = os.environ.copy()
        env.setdefault("TMPDIR", os.environ.get("TMPDIR") or tempfile.gettempdir())
        proc = subprocess.run(
            [
                PY39,
                "-B",
                CLI,
                "dispatch",
                "--repo",
                repo,
                "--brief",
                brief,
                "--workdir",
                workdir,
                "--executor",
                "fake",
                "--data-dir",
                data_dir,
                "--config",
                cfg_path,
            ],
            capture_output=True,
            env=env,
            timeout=60,
        )
        self.assertEqual(proc.returncode, 3)
        self.assertIn(b"missing_section", proc.stderr)
        self.assertTrue(
            not os.path.isdir(os.path.join(data_dir, "runs"))
            or not os.listdir(os.path.join(data_dir, "runs"))
        )


if __name__ == "__main__":
    unittest.main()
