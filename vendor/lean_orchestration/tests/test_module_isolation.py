"""Module isolation + supervisor evidence (L6 / KE silent-supervisor door).

VER-2: Hermes-style loader (spec_from_file_location + submodule_search_locations).
VER-3: RED removes eviction on a scratch copy outside the worktree.
"""
from __future__ import annotations

import importlib.util
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

from lo_exec import dispatch as dispatch_mod
from lo_exec import ledger as ledger_mod
from tests.fixtures import base_brief, make_fixture, write_brief

REPO_ROOT = Path(__file__).resolve().parents[1]
FAKE_EXEC = str(REPO_ROOT / "tests" / "fake_executor.py")
PY39 = "/usr/bin/python3"

# Names that must not collide with a prior plugin load in this process.
_LOAD_SEQ = 0


class FakeCtx:
    def __init__(self, *, config: Optional[Dict[str, Any]] = None) -> None:
        self.tools: Dict[str, Any] = {}
        self.hooks: Dict[str, Any] = {}
        self._config: Dict[str, Any] = dict(config or {})
        self.injected: List[Tuple[str, Any, Any]] = []
        self.posted: List[str] = []
        self.inject_return = True
        self.spawned: List[Any] = []
        self.state = type("S", (), {})()
        self.state.data_dir = Path(tempfile.mkdtemp(prefix="lo-iso-data-")) / "plugin-data"
        self.state.data_dir.mkdir(parents=True, exist_ok=True)

    def register_tool(self, name: str, toolset: str, schema: dict, handler: Any, **kwargs: Any) -> None:
        self.tools[name] = {"toolset": toolset, "schema": schema, "handler": handler, "kwargs": kwargs}

    def register_hook(self, hook_name: str, callback: Any, **kwargs: Any) -> None:
        self.hooks[hook_name] = callback

    def get_config(self, key: str, default: Any = None) -> Any:
        return self._config.get(key, default)

    def inject_message(self, content: str, role: str = "user", **kwargs: Any) -> bool:
        del role, kwargs
        self.injected.append((content, None, None))
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


def _copy_plugin(dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    for name in ("lo_core", "lo_exec", "tests", "skills", "cli.py", "__init__.py", "plugin.yaml"):
        src = REPO_ROOT / name
        dst = dest / name
        if not src.exists():
            continue
        if src.is_dir():
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)
    return dest


def _load_plugin_hermes(plugin_dir: Path, name: Optional[str] = None) -> Any:
    """Load as Hermes does: package with submodule_search_locations."""
    global _LOAD_SEQ
    _LOAD_SEQ += 1
    mod_name = name or ("lean_orch_iso_%s" % _LOAD_SEQ)
    init = plugin_dir / "__init__.py"
    spec = importlib.util.spec_from_file_location(
        mod_name,
        init,
        submodule_search_locations=[str(plugin_dir.resolve())],
    )
    assert spec and spec.loader
    plugin = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = plugin
    spec.loader.exec_module(plugin)
    plugin._reset_runtime_state()
    return plugin


def _install_stale_lo_exec(stale_root: Path) -> str:
    """Create a fake lo_exec whose SUPERVISE_PY does not exist; import into sys.modules."""
    pkg = stale_root / "lo_exec"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text("# stale lo_exec\n", encoding="utf-8")
    bad_supervise = str(stale_root / "missing_supervise_DOES_NOT_EXIST.py")
    (pkg / "dispatch.py").write_text(
        (
            "import os\n"
            "SUPERVISE_PY = %r\n"
            "PLUGIN_ROOT = %r\n"
            "def dispatch(**kwargs):\n"
            "    raise RuntimeError('stale lo_exec.dispatch must not run')\n"
        )
        % (bad_supervise, str(stale_root)),
        encoding="utf-8",
    )
    # Evict any current lo_exec so the stale one wins until plugin isolation runs.
    for key in list(sys.modules):
        if key == "lo_exec" or key.startswith("lo_exec."):
            del sys.modules[key]
    sys.path.insert(0, str(stale_root))
    import lo_exec  # noqa: F401
    import lo_exec.dispatch as stale_dispatch  # noqa: F401

    assert stale_dispatch.SUPERVISE_PY == bad_supervise
    return bad_supervise


def _executor_config(sleep: float = 0.05) -> Dict[str, Any]:
    return {
        "executors": {
            "fake": {
                "argv": [
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
                ],
                "timeout_s": 30,
                "grace_s": 2,
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


def _wait_exit(run_dir: str, *, timeout_s: float = 30.0) -> Optional[Dict[str, Any]]:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        info = ledger_mod.read_exit_json(run_dir)
        if info is not None:
            return info
        time.sleep(0.05)
    return None


def _wait_ended(data_dir: str, dispatch_id: str, *, timeout_s: float = 30.0) -> Optional[Dict[str, Any]]:
    led = ledger_mod.ledger_path(data_dir)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        for ev in ledger_mod.read_events(led):
            if ev.get("event") == "ended" and str(ev.get("id")) == dispatch_id:
                return ev
        time.sleep(0.05)
    return None


def _wait_injected(ctx: FakeCtx, *, timeout_s: float = 15.0) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if ctx.injected:
            return
        time.sleep(0.05)


class TestStaleModuleIsolation(unittest.TestCase):
    def test_stale_lo_exec_evicted_and_real_supervisor_runs(self) -> None:
        """(a) Stale SUPERVISE_PY in sys.modules → plugin load replaces it; exit.json written."""
        base = Path(tempfile.mkdtemp(prefix="lo-iso-a-"))
        stale_root = base / "stale"
        plugin_dir = _copy_plugin(base / "plugin")
        bad = _install_stale_lo_exec(stale_root)
        self.assertFalse(os.path.isfile(bad))
        self.assertIn("lo_exec", sys.modules)
        self.assertEqual(sys.modules["lo_exec.dispatch"].SUPERVISE_PY, bad)

        plugin = _load_plugin_hermes(plugin_dir)
        # After isolation the live lo_exec must be the copy's.
        live_sup = sys.modules["lo_exec.dispatch"].SUPERVISE_PY
        self.assertTrue(os.path.isfile(live_sup), live_sup)
        self.assertTrue(str(Path(live_sup).resolve()).startswith(str(plugin_dir.resolve())))

        cfg = _executor_config()
        ctx = FakeCtx(config=cfg)
        plugin.register(ctx)
        repo = make_fixture()
        brief = _pass_brief(repo)
        workdir = tempfile.mkdtemp(prefix="lo-wd-")
        raw = ctx.tools["orch_dispatch"]["handler"](
            {"repo": repo, "brief": brief, "workdir": workdir, "executor": "fake"}
        )
        result = json.loads(raw)
        self.assertTrue(result.get("ok"), result)
        run_dir = result["run_dir"]
        exit_info = _wait_exit(run_dir, timeout_s=30)
        self.assertIsNotNone(exit_info, "exit.json missing — stale SUPERVISE_PY still in use?")
        self.assertEqual(exit_info.get("rc"), 0)
        self.assertTrue(os.path.isfile(os.path.join(run_dir, "supervisor.log")))


class TestTwoCopyReload(unittest.TestCase):
    def test_load_a_delete_a_load_b_dispatch_works(self) -> None:
        """(b) Two copies: load A, delete A, load B, dispatch → works."""
        base = Path(tempfile.mkdtemp(prefix="lo-iso-b-"))
        copy_a = _copy_plugin(base / "A")
        copy_b = _copy_plugin(base / "B")
        plugin_a = _load_plugin_hermes(copy_a, name="lean_orch_copy_a")
        self.assertTrue(str(sys.modules["lo_exec"].__file__).startswith(str(copy_a.resolve())))
        shutil.rmtree(copy_a)
        plugin_b = _load_plugin_hermes(copy_b, name="lean_orch_copy_b")
        live_file = Path(sys.modules["lo_exec"].__file__).resolve()
        self.assertTrue(str(live_file).startswith(str(copy_b.resolve())), live_file)

        cfg = _executor_config()
        ctx = FakeCtx(config=cfg)
        plugin_b.register(ctx)
        repo = make_fixture()
        brief = _pass_brief(repo)
        workdir = tempfile.mkdtemp(prefix="lo-wd-")
        raw = ctx.tools["orch_dispatch"]["handler"](
            {"repo": repo, "brief": brief, "workdir": workdir, "executor": "fake"}
        )
        result = json.loads(raw)
        self.assertTrue(result.get("ok"), result)
        self.assertIsNotNone(_wait_exit(result["run_dir"], timeout_s=30))
        del plugin_a  # silence unused — load side effect is the point


class TestSupervisorMissing(unittest.TestCase):
    def test_missing_supervise_py_errors_without_ledger(self) -> None:
        """(c) Missing supervisor script → supervisor_missing; no ledger dispatched."""
        data_dir = tempfile.mkdtemp(prefix="lo-miss-")
        repo = make_fixture()
        brief_text = open(_pass_brief(repo), encoding="utf-8").read()
        workdir = tempfile.mkdtemp(prefix="lo-wd-")
        cfg = _executor_config()
        missing = os.path.join(tempfile.mkdtemp(prefix="lo-nosup-"), "no_such_supervise.py")
        old = dispatch_mod.SUPERVISE_PY
        try:
            dispatch_mod.SUPERVISE_PY = missing
            result = dispatch_mod.dispatch(
                repo=repo,
                brief_text=brief_text,
                workdir=workdir,
                executor="fake",
                data_dir=data_dir,
                config=cfg,
                python_bin=PY39,
            )
        finally:
            dispatch_mod.SUPERVISE_PY = old
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("decision"), "ERROR")
        reasons = result.get("reasons") or []
        self.assertTrue(any(str(r).startswith("supervisor_missing:") for r in reasons), reasons)
        led = ledger_mod.ledger_path(data_dir)
        self.assertTrue(not os.path.isfile(led) or "dispatched" not in open(led, encoding="utf-8").read())


class TestSupervisorDiesVisible(unittest.TestCase):
    def test_dying_supervisor_records_rc_and_log_tail(self) -> None:
        """(d) Supervisor exits 2 → ledger supervisor_rc=2, log tail, message mentions it."""
        dying = Path(tempfile.mkdtemp(prefix="lo-die-")) / "die.py"
        dying.write_text(
            "import sys\n"
            "sys.stderr.write('boom: intentional supervisor death\\n')\n"
            "sys.exit(2)\n",
            encoding="utf-8",
        )
        # Load plugin from worktree via Hermes path so waiter runs.
        plugin = _load_plugin_hermes(REPO_ROOT, name="lean_orch_die_test")
        # Point the live dispatch module's SUPERVISE_PY at the dying script.
        import lo_exec.dispatch as live_dispatch

        old = live_dispatch.SUPERVISE_PY
        live_dispatch.SUPERVISE_PY = str(dying)
        try:
            cfg = _executor_config()
            ctx = FakeCtx(config=cfg)
            plugin.register(ctx)
            repo = make_fixture()
            brief = _pass_brief(repo)
            workdir = tempfile.mkdtemp(prefix="lo-wd-")
            raw = ctx.tools["orch_dispatch"]["handler"](
                {"repo": repo, "brief": brief, "workdir": workdir, "executor": "fake"}
            )
            result = json.loads(raw)
            self.assertTrue(result.get("ok"), result)
            dispatch_id = result["id"]
            run_dir = result["run_dir"]
            data_dir = str(ctx.state.data_dir)
            ended = _wait_ended(data_dir, dispatch_id, timeout_s=30)
            self.assertIsNotNone(ended, "ended event missing")
            self.assertEqual(ended.get("evidence"), "supervisor_exited")
            self.assertEqual(ended.get("supervisor_rc"), 2)
            tail = ended.get("supervisor_log_tail") or ""
            self.assertIn("intentional supervisor death", tail)
            _wait_injected(ctx, timeout_s=15)
            self.assertTrue(ctx.injected, "completion not injected")
            msg = ctx.injected[0][0]
            self.assertIn("supervisor_rc=2", msg)
            self.assertIn("see %s/supervisor.log" % run_dir, msg)
            self.assertFalse(os.path.isfile(os.path.join(run_dir, "exit.json")))
        finally:
            live_dispatch.SUPERVISE_PY = old


class TestRedModuleIsolation(unittest.TestCase):
    def test_red_eviction_removed_on_scratch(self) -> None:
        """VER-3: without eviction, the stale-module detector's assertion fails."""
        scratch = Path(tempfile.mkdtemp(prefix="lo-red-iso-"))
        _copy_plugin(scratch)
        init_path = scratch / "__init__.py"
        text = init_path.read_text(encoding="utf-8")
        needle = "    for name in stale:\n        del sys.modules[name]\n"
        self.assertIn(needle, text)
        # RED mutation: keep path pin, skip eviction — stale SUPERVISE_PY survives (incident).
        init_path.write_text(
            text.replace(
                needle,
                "    for name in stale:\n        pass  # RED: eviction disabled\n",
                1,
            ),
            encoding="utf-8",
        )
        # Stale tree = full lo_exec + lo_core with SUPERVISE_PY pointed at a missing file
        # (mirrors the live incident: a previous install left complete packages cached).
        stale = Path(tempfile.mkdtemp(prefix="lo-red-stale-"))
        shutil.copytree(scratch / "lo_exec", stale / "lo_exec")
        shutil.copytree(scratch / "lo_core", stale / "lo_core")
        bad = str(stale / "NO_SUCH_SUPERVISE.py")
        disp = (stale / "lo_exec" / "dispatch.py").read_text(encoding="utf-8")
        # Rewrite SUPERVISE_PY assignment to the missing path.
        lines = []
        for line in disp.splitlines(True):
            if line.startswith("SUPERVISE_PY = "):
                lines.append("SUPERVISE_PY = %r\n" % bad)
            else:
                lines.append(line)
        (stale / "lo_exec" / "dispatch.py").write_text("".join(lines), encoding="utf-8")

        probe = scratch / "red_probe.py"
        probe.write_text(
            "\n".join(
                [
                    "from __future__ import annotations",
                    "import importlib.util, os, sys",
                    "from pathlib import Path",
                    "SCRATCH = Path(%r)" % str(scratch),
                    "STALE = Path(%r)" % str(stale),
                    "bad = %r" % bad,
                    "for k in list(sys.modules):",
                    "    if k == 'lo_exec' or k.startswith('lo_exec.') or k == 'lo_core' or k.startswith('lo_core.'):",
                    "        del sys.modules[k]",
                    "sys.path.insert(0, str(STALE))",
                    "import lo_exec.dispatch as stale_d",
                    "assert stale_d.SUPERVISE_PY == bad",
                    "assert not os.path.isfile(bad)",
                    "spec = importlib.util.spec_from_file_location(",
                    "    'lean_orch_red',",
                    "    SCRATCH / '__init__.py',",
                    "    submodule_search_locations=[str(SCRATCH)],",
                    ")",
                    "plugin = importlib.util.module_from_spec(spec)",
                    "sys.modules['lean_orch_red'] = plugin",
                    "spec.loader.exec_module(plugin)",
                    "live = sys.modules['lo_exec.dispatch'].SUPERVISE_PY",
                    "# Detector for (a): after Hermes-style load, SUPERVISE_PY must be this copy's.",
                    "assert os.path.isfile(live), (",
                    "    'expected real SUPERVISE_PY after isolation, got missing %r' % (live,)",
                    ")",
                    "assert str(Path(live).resolve()).startswith(str(SCRATCH.resolve())), (",
                    "    'expected SUPERVISE_PY under plugin copy, got %r' % (live,)",
                    ")",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        env = os.environ.copy()
        env.setdefault("TMPDIR", os.environ.get("TMPDIR") or tempfile.gettempdir())
        proc = subprocess.run(
            [PY39, "-B", str(probe)],
            capture_output=True,
            env=env,
            timeout=60,
            cwd=str(scratch),
        )
        self.assertNotEqual(proc.returncode, 0)
        err = (proc.stderr + proc.stdout).decode("utf-8", "replace")
        self.assertIn("expected real SUPERVISE_PY after isolation, got missing", err)


class TestReconcileSupervisorLog(unittest.TestCase):
    def test_reconcile_uses_supervisor_exited_when_log_exists(self) -> None:
        data_dir = tempfile.mkdtemp(prefix="lo-rec-")
        run_dir = os.path.join(data_dir, "runs", "eeeeeeeeeeee")
        os.makedirs(run_dir, exist_ok=True)
        with open(os.path.join(run_dir, "supervisor.log"), "wb") as fh:
            fh.write(b"can't open file '/gone/supervise.py'\n")
        led = ledger_mod.ledger_path(data_dir)
        ledger_mod.append_event(
            led,
            {
                "event": "dispatched",
                "id": "eeeeeeeeeeee",
                "executor": "fake",
                "workdir": data_dir,
                "brief_sha256": "a",
                "task_sha256": "b",
                "pid": 99999999,
                "pgid": 99999999,
                "started": time.time(),
                "timeout": 10,
                "run_dir": run_dir,
            },
        )
        actions = dispatch_mod.reconcile(data_dir)
        ended = [a for a in actions if a.get("event") == "ended"][0]
        self.assertEqual(ended.get("evidence"), "supervisor_exited")
        self.assertIn("can't open file", ended.get("supervisor_log_tail") or "")
        line = dispatch_mod.format_completion_line(ended)
        self.assertIn("see %s/supervisor.log" % run_dir, line)


class TestNoLazyPluginImports(unittest.TestCase):
    """KE-2026-11-20-STALE-TOPLEVEL-MODULES: a lo_* import inside a function resolves at call
    time through sys.modules, which another loaded copy may have replaced; bind at load only."""

    def test_entry_module_has_no_function_level_lo_imports(self) -> None:
        import ast

        tree = ast.parse((REPO_ROOT / "__init__.py").read_text(encoding="utf-8"))
        lazy = []
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(fn):
                names = []
                if isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                elif isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                for name in names:
                    if name.split(".")[0] in ("lo_core", "lo_exec"):
                        lazy.append("%s:%d %s" % (fn.name, node.lineno, name))
        self.assertEqual(lazy, [])


if __name__ == "__main__":
    unittest.main()
