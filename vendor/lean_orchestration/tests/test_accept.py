"""Portion 3 scenarios — orch_accept, /orch, gate_enabled, skill (FR-014/015/016)."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional

from lo_core.accept_decide import decide_accept, path_in_allowed
from lo_core.config import merge_config
from lo_exec import accept as accept_mod
from lo_exec import dispatch as dispatch_mod
from lo_exec import ledger as ledger_mod
from tests.fixtures import base_brief, make_fixture, write_brief

REPO_ROOT = Path(__file__).resolve().parents[1]
CLI = str(REPO_ROOT / "cli.py")
FAKE_EXEC = str(REPO_ROOT / "tests" / "fake_executor.py")
PY39 = "/usr/bin/python3"
ALLOWED_FILE = "MainOS.Mac/Sources/MainOSMacCore/Run/Timer.swift"
OUTSIDE_FILE = "Outside/NotAllowed.swift"


class FakeCtx:
    def __init__(self, *, with_data_dir: bool = True, config: Optional[Dict[str, Any]] = None) -> None:
        self.tools: Dict[str, Any] = {}
        self.hooks: Dict[str, Any] = {}
        self.commands: Dict[str, Any] = {}
        self.skills: Dict[str, Any] = {}
        self._config: Dict[str, Any] = dict(config or {})
        self.injected: List[Any] = []
        self.posted: List[str] = []
        self.inject_return = True
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

    def register_command(self, name: str, handler: Any, **kwargs: Any) -> None:
        self.commands[name] = {"handler": handler, "kwargs": kwargs}

    def register_skill(self, name: str, path: Any, **kwargs: Any) -> None:
        self.skills[name] = {"path": str(path), "kwargs": kwargs}

    def get_config(self, key: str, default: Any = None) -> Any:
        return self._config.get(key, default)

    def inject_message(self, content: str, role: str = "user", **kwargs: Any) -> bool:
        del role, kwargs
        self.injected.append(content)
        return bool(self.inject_return)

    def post_message(self, text: str) -> Any:
        self.posted.append(text)
        return {"ok": True}


def _load_plugin():
    import importlib.util

    spec = importlib.util.spec_from_file_location("lean_orchestration_plugin_l4", REPO_ROOT / "__init__.py")
    assert spec and spec.loader
    plugin = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(plugin)
    plugin._reset_runtime_state()
    return plugin


def _git(cwd: str, *args: str) -> str:
    out = subprocess.check_output(["git", *args], cwd=cwd, stderr=subprocess.STDOUT)
    return out.decode("utf-8", "replace")


def _init_git_repo(root: str) -> None:
    _git(root, "init")
    _git(root, "config", "user.email", "lo@example.com")
    _git(root, "config", "user.name", "lo")
    # Seed allowed + outside paths so renames/diffs have a base.
    allowed = os.path.join(root, ALLOWED_FILE)
    os.makedirs(os.path.dirname(allowed), exist_ok=True)
    with open(allowed, "w", encoding="utf-8") as fh:
        fh.write("base\n")
    outside = os.path.join(root, OUTSIDE_FILE)
    os.makedirs(os.path.dirname(outside), exist_ok=True)
    with open(outside, "w", encoding="utf-8") as fh:
        fh.write("outside-base\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "seed")


def _executor_config(**extra: Any) -> Dict[str, Any]:
    cfg = {
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
                    "--write-receipt",
                    "--exit",
                    "0",
                ],
                "timeout_s": 30,
                "grace_s": 2,
                "binary_names": ["cursor-agent"],
            }
        },
        "max_timeout_s": 7200,
    }
    cfg.update(extra)
    return cfg


def _pass_brief_text() -> str:
    return base_brief(architecture_rules="UI-1, JOB-1, BULK-2 apply.")


def _setup_repo_and_dispatch(
    *,
    config: Optional[Dict[str, Any]] = None,
    wait: bool = True,
) -> Dict[str, Any]:
    repo = make_fixture()
    _init_git_repo(repo)
    data_dir = tempfile.mkdtemp(prefix="lo-data-")
    brief_path = write_brief(os.path.join(tempfile.mkdtemp(prefix="lo-brief-"), "b.md"), _pass_brief_text())
    with open(brief_path, "r", encoding="utf-8") as fh:
        brief_text = fh.read()
    cfg = merge_config(_executor_config(**(config or {})))
    result = dispatch_mod.dispatch(
        repo=repo,
        brief_text=brief_text,
        workdir=repo,
        executor="fake",
        data_dir=data_dir,
        config=cfg,
        python_bin=PY39,
    )
    assert result.get("ok"), result
    if wait:
        info = dispatch_mod.wait_for_exit(str(result["run_dir"]), timeout_s=30.0)
        assert info is not None
    return {
        "repo": repo,
        "data_dir": data_dir,
        "dispatch_id": str(result["id"]),
        "run_dir": str(result["run_dir"]),
        "brief_text": brief_text,
        "base_head": result.get("base_head"),
        "config": cfg,
        "result": result,
    }


def _write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


class TestAcceptAllowedChange(unittest.TestCase):
    def test_allowed_only_accepted_tool_and_cli(self) -> None:
        env = _setup_repo_and_dispatch()
        _write(os.path.join(env["repo"], ALLOWED_FILE), "changed-ok\n")

        plugin = _load_plugin()
        ctx = FakeCtx(config=dict(env["config"]))
        ctx.state.data_dir = Path(env["data_dir"])
        plugin.register(ctx)
        raw = ctx.tools["orch_accept"]["handler"]({"dispatch_id": env["dispatch_id"]})
        tool = json.loads(raw)
        self.assertEqual(tool["decision"], "ACCEPTED", tool)
        self.assertIn(ALLOWED_FILE, tool["changed_paths"])
        self.assertTrue(tool["tree_sha256"])

        proc = subprocess.run(
            [
                PY39,
                "-B",
                CLI,
                "accept",
                "--dispatch",
                env["dispatch_id"],
                "--data-dir",
                env["data_dir"],
                "--json",
                "--config",
                _write_config(env["config"]),
            ],
            capture_output=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace"))
        cli = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(cli["decision"], "ACCEPTED")
        self.assertEqual(cli["tree_sha256"], tool["tree_sha256"])


class TestAcceptOutsidePath(unittest.TestCase):
    def test_outside_file_rejected(self) -> None:
        env = _setup_repo_and_dispatch()
        _write(os.path.join(env["repo"], OUTSIDE_FILE), "changed-bad\n")
        result = accept_mod.accept_dispatch(
            dispatch_id=env["dispatch_id"],
            data_dir=env["data_dir"],
            config=env["config"],
        )
        self.assertEqual(result["decision"], "REJECTED")
        self.assertIn("path_outside_allowed:%s" % OUTSIDE_FILE, result["reasons"])


class TestAcceptRenameOutside(unittest.TestCase):
    def test_rename_allowed_to_outside_rejected(self) -> None:
        env = _setup_repo_and_dispatch()
        src = os.path.join(env["repo"], ALLOWED_FILE)
        dst = os.path.join(env["repo"], "Outside/Renamed.swift")
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        os.rename(src, dst)
        result = accept_mod.accept_dispatch(
            dispatch_id=env["dispatch_id"],
            data_dir=env["data_dir"],
            config=env["config"],
        )
        self.assertEqual(result["decision"], "REJECTED")
        self.assertTrue(
            any(r.startswith("path_outside_allowed:Outside/") for r in result["reasons"]),
            result["reasons"],
        )


class TestAcceptUntrackedInside(unittest.TestCase):
    def test_untracked_inside_allowed_accepted(self) -> None:
        env = _setup_repo_and_dispatch()
        new_path = "MainOS.Mac/Sources/MainOSMacCore/Run/NewFile.swift"
        _write(os.path.join(env["repo"], new_path), "new\n")
        result = accept_mod.accept_dispatch(
            dispatch_id=env["dispatch_id"],
            data_dir=env["data_dir"],
            config=env["config"],
        )
        self.assertEqual(result["decision"], "ACCEPTED", result)
        self.assertIn(new_path, result["changed_paths"])


class TestAcceptPreDirty(unittest.TestCase):
    def test_predirty_unchanged_outside_ignored(self) -> None:
        repo = make_fixture()
        _init_git_repo(repo)
        # Dirty outside before dispatch; leave unchanged.
        _write(os.path.join(repo, OUTSIDE_FILE), "dirty-unchanged\n")
        data_dir = tempfile.mkdtemp(prefix="lo-data-")
        cfg = merge_config(_executor_config())
        brief_text = _pass_brief_text()
        result = dispatch_mod.dispatch(
            repo=repo,
            brief_text=brief_text,
            workdir=repo,
            executor="fake",
            data_dir=data_dir,
            config=cfg,
            python_bin=PY39,
        )
        self.assertTrue(result.get("ok"), result)
        dispatch_mod.wait_for_exit(str(result["run_dir"]), timeout_s=30.0)
        # Change only an allowed file.
        _write(os.path.join(repo, ALLOWED_FILE), "allowed-change\n")
        acc = accept_mod.accept_dispatch(
            dispatch_id=str(result["id"]),
            data_dir=data_dir,
            config=cfg,
        )
        self.assertEqual(acc["decision"], "ACCEPTED", acc)
        self.assertNotIn(OUTSIDE_FILE, acc["changed_paths"])

    def test_predirty_changed_outside_rejected(self) -> None:
        repo = make_fixture()
        _init_git_repo(repo)
        _write(os.path.join(repo, OUTSIDE_FILE), "dirty-v1\n")
        data_dir = tempfile.mkdtemp(prefix="lo-data-")
        cfg = merge_config(_executor_config())
        result = dispatch_mod.dispatch(
            repo=repo,
            brief_text=_pass_brief_text(),
            workdir=repo,
            executor="fake",
            data_dir=data_dir,
            config=cfg,
            python_bin=PY39,
        )
        self.assertTrue(result.get("ok"), result)
        dispatch_mod.wait_for_exit(str(result["run_dir"]), timeout_s=30.0)
        _write(os.path.join(repo, OUTSIDE_FILE), "dirty-v2-changed\n")
        acc = accept_mod.accept_dispatch(
            dispatch_id=str(result["id"]),
            data_dir=data_dir,
            config=cfg,
        )
        self.assertEqual(acc["decision"], "REJECTED")
        self.assertIn("path_outside_allowed:%s" % OUTSIDE_FILE, acc["reasons"])


class TestAcceptGateRefuseAfter(unittest.TestCase):
    def test_brief_edited_gate_refuses(self) -> None:
        env = _setup_repo_and_dispatch()
        _write(os.path.join(env["repo"], ALLOWED_FILE), "ok\n")
        brief_path = os.path.join(env["run_dir"], "BRIEF.md")
        with open(brief_path, "r", encoding="utf-8") as fh:
            text = fh.read()
        # Remove Known errors section content → empty_section / known_errors_not_stated.
        broken = text.replace("## Known errors\n\nKE-2026-09-23-MIRROR-FULL-SCAN — relevant prior defect.\n", "## Known errors\n\n")
        with open(brief_path, "w", encoding="utf-8") as fh:
            fh.write(broken)
        acc = accept_mod.accept_dispatch(
            dispatch_id=env["dispatch_id"],
            data_dir=env["data_dir"],
            config=env["config"],
        )
        self.assertEqual(acc["decision"], "REJECTED", acc)
        self.assertTrue(any(r.startswith("gate:") for r in acc["reasons"]), acc["reasons"])


class TestAcceptProbes(unittest.TestCase):
    def test_probe_rc_mismatch_and_timeout(self) -> None:
        env = _setup_repo_and_dispatch()
        _write(os.path.join(env["repo"], ALLOWED_FILE), "ok\n")
        probes = {
            "probes": [
                {
                    "name": "fail_rc",
                    "argv": [PY39, "-c", "import sys; sys.exit(2)"],
                    "cwd": ".",
                    "timeout_s": 5,
                    "expect_rc": 0,
                },
                {
                    "name": "slow",
                    "argv": [PY39, "-c", "import time; time.sleep(5)"],
                    "cwd": ".",
                    "timeout_s": 0.2,
                    "expect_rc": 0,
                },
            ]
        }
        os.makedirs(os.path.join(env["repo"], ".lean-orchestration"), exist_ok=True)
        with open(
            os.path.join(env["repo"], ".lean-orchestration", "probes.json"),
            "w",
            encoding="utf-8",
        ) as fh:
            json.dump(probes, fh)
        acc = accept_mod.accept_dispatch(
            dispatch_id=env["dispatch_id"],
            data_dir=env["data_dir"],
            config=env["config"],
        )
        self.assertEqual(acc["decision"], "REJECTED")
        self.assertIn("probe_failed:fail_rc:rc=2", acc["reasons"])
        self.assertIn("probe_timeout:slow", acc["reasons"])


class TestAcceptNoGit(unittest.TestCase):
    def test_no_git_base(self) -> None:
        repo = make_fixture()  # no git init
        data_dir = tempfile.mkdtemp(prefix="lo-data-")
        # Manual accept with a fake base — workdir has no git.
        acc = accept_mod.accept_manual(
            repo=repo,
            brief_text=_pass_brief_text(),
            base_commit="deadbeef",
            workdir=repo,
            config=merge_config(),
            data_dir=data_dir,
        )
        self.assertEqual(acc["decision"], "REJECTED")
        self.assertIn("no_git_base", acc["reasons"])


class TestAcceptDeterminism(unittest.TestCase):
    def test_two_runs_identical_json(self) -> None:
        env = _setup_repo_and_dispatch()
        _write(os.path.join(env["repo"], ALLOWED_FILE), "det\n")
        a = accept_mod.accept_dispatch(
            dispatch_id=env["dispatch_id"],
            data_dir=env["data_dir"],
            config=env["config"],
        )
        b = accept_mod.accept_dispatch(
            dispatch_id=env["dispatch_id"],
            data_dir=env["data_dir"],
            config=env["config"],
        )
        # Strip non-decision bookkeeping if any.
        for key in ("journal_error", "ledger_error"):
            a.pop(key, None)
            b.pop(key, None)
        ja = json.dumps(a, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        jb = json.dumps(b, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        self.assertEqual(ja, jb)


class TestOrchCoverageAndGateDisable(unittest.TestCase):
    def test_orch_shows_gate_disabled(self) -> None:
        plugin = _load_plugin()
        ctx = FakeCtx(config={"gate_enabled": False})
        plugin.register(ctx)
        self.assertIn("orch", ctx.commands)
        text = ctx.commands["orch"]["handler"]("")
        self.assertTrue(text.startswith("gate: disabled"), text)
        self.assertIn("gate_enabled=false", text)
        # Live check 2026-11-20: Hermes redaction masked `gate_PASS=2` as `***`.
        counters = [line for line in text.splitlines() if line.startswith("counters:")]
        self.assertEqual(len(counters), 1, text)
        self.assertNotRegex(counters[0], r"(?i)pass\w*=")
        self.assertIn("gate pass ", counters[0])

    def test_brief_text_cannot_disable_gate(self) -> None:
        repo = make_fixture()
        brief = _pass_brief_text() + "\n\ngate_enabled: false\n"
        from lo_core.gate import run_gate

        result = run_gate(repo, brief, config=merge_config({"gate_enabled": True}))
        self.assertNotEqual(result.get("reasons"), ["gate_disabled"])
        self.assertFalse(result.get("gate_disabled"))
        # And config false really disables:
        disabled = run_gate(repo, brief, config=merge_config({"gate_enabled": False}))
        self.assertEqual(disabled["decision"], "PASS")
        self.assertEqual(disabled["reasons"], ["gate_disabled"])
        self.assertTrue(disabled.get("gate_disabled"))

    def test_call_arg_cannot_disable_gate(self) -> None:
        """Tool params have no gate_enabled field; forging config via brief is ignored."""
        plugin = _load_plugin()
        ctx = FakeCtx(config={"gate_enabled": True})
        plugin.register(ctx)
        repo = make_fixture()
        brief = write_brief(
            os.path.join(tempfile.mkdtemp(prefix="lo-b-"), "b.md"),
            _pass_brief_text() + "\ngate_enabled: false\n",
        )
        raw = ctx.tools["orch_gate"]["handler"](
            {"repo": repo, "brief": brief, "gate_enabled": False}
        )
        payload = json.loads(raw)
        self.assertFalse(payload.get("gate_disabled"))
        self.assertNotEqual(payload.get("reasons"), ["gate_disabled"])


class TestSkillRegistered(unittest.TestCase):
    def test_skill_registered(self) -> None:
        plugin = _load_plugin()
        ctx = FakeCtx()
        plugin.register(ctx)
        self.assertIn("lean-orchestration", ctx.skills)
        path = ctx.skills["lean-orchestration"]["path"]
        self.assertTrue(os.path.isfile(path))
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
        self.assertLessEqual(text.count("\n") + 1, 120)
        self.assertIn("orch_accept", text)
        self.assertIn("orch_dispatch", text)


class TestAcceptDispatchUnknown(unittest.TestCase):
    def test_unknown_dispatch(self) -> None:
        data_dir = tempfile.mkdtemp(prefix="lo-data-")
        acc = accept_mod.accept_dispatch(
            dispatch_id="nope",
            data_dir=data_dir,
            config=merge_config(),
        )
        self.assertEqual(acc["decision"], "REJECTED")
        self.assertIn("dispatch_unknown", acc["reasons"])


class TestManualAcceptCli(unittest.TestCase):
    def test_cli_manual_base(self) -> None:
        repo = make_fixture()
        _init_git_repo(repo)
        head = _git(repo, "rev-parse", "HEAD").strip()
        _write(os.path.join(repo, ALLOWED_FILE), "manual\n")
        brief = write_brief(os.path.join(tempfile.mkdtemp(prefix="lo-b-"), "b.md"), _pass_brief_text())
        proc = subprocess.run(
            [
                PY39,
                "-B",
                CLI,
                "accept",
                "--repo",
                repo,
                "--brief",
                brief,
                "--base",
                head,
                "--json",
            ],
            capture_output=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace") + proc.stdout.decode("utf-8", "replace"))
        payload = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(payload["decision"], "ACCEPTED")


class TestRedAllowedAlwaysPass(unittest.TestCase):
    def test_red_allowed_check_always_pass_on_scratch(self) -> None:
        """VER-3: mutate path_in_allowed to always True on a scratch copy; detector fails."""
        scratch = tempfile.mkdtemp(prefix="lo-red-accept-")
        pkg = os.path.join(scratch, "lo_core")
        shutil.copytree(str(REPO_ROOT / "lo_core"), pkg)
        decide_path = os.path.join(pkg, "accept_decide.py")
        with open(decide_path, "r", encoding="utf-8") as fh:
            src = fh.read()
        needle = "return False\n"
        # Replace the final return of path_in_allowed only.
        idx = src.find("def path_in_allowed")
        self.assertGreaterEqual(idx, 0)
        end = src.find("\n\n", idx)
        block = src[idx:end]
        mutated_block = block.replace(
            "    return False",
            "    return True  # RED mutation: always allow",
            1,
        )
        self.assertNotEqual(block, mutated_block, "mutation did not apply")
        mutated = src[:idx] + mutated_block + src[end:]
        with open(decide_path, "w", encoding="utf-8") as fh:
            fh.write(mutated)

        probe = (
            "import sys\n"
            "sys.path.insert(0, %r)\n"
            "from lo_core.accept_decide import decide_accept\n"
            "r = decide_accept(\n"
            "  changed_paths=['Outside/x.swift'],\n"
            "  allowed=['MainOS.Mac/**'],\n"
            "  gate_decision='PASS',\n"
            "  gate_reasons=[],\n"
            "  probe_results=[],\n"
            "  tree_sha256='abc',\n"
            "  has_git_base=True,\n"
            ")\n"
            "assert r['decision'] == 'REJECTED', r\n"
            "assert any(x.startswith('path_outside_allowed:') for x in r['reasons']), r\n"
        ) % scratch
        proc = subprocess.run([PY39, "-B", "-c", probe], capture_output=True)
        self.assertNotEqual(proc.returncode, 0)
        err = (proc.stderr or b"").decode("utf-8", "replace") + (proc.stdout or b"").decode(
            "utf-8", "replace"
        )
        self.assertTrue("AssertionError" in err or "assert" in err.lower(), err)
        print("RED_ACCEPT_ASSERTION=%r" % err.strip().splitlines()[-1][:200])


def _write_config(cfg: Dict[str, Any]) -> str:
    path = os.path.join(tempfile.mkdtemp(prefix="lo-cfg-"), "cfg.json")
    # merge_config-compatible subset
    public = {
        "executors": cfg.get("executors") or {},
        "max_timeout_s": cfg.get("max_timeout_s", 7200),
        "gate_enabled": cfg.get("gate_enabled", True),
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(public, fh)
    return path


if __name__ == "__main__":
    unittest.main()
