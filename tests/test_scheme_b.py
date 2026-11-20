"""Scheme B: one active lean-orchestration copy + hook interplay with vibe-relay."""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

from support import IsolatedHomeTest, ROOT

LO_ROOT = ROOT / "vendor" / "lean_orchestration"
ORCH_TOOLS = ("orch_gate", "orch_dispatch", "orch_accept")
LO_HOOKS = ("pre_tool_call", "on_session_start")


class RecordingCtx:
    """Fake PluginContext: accumulates hooks (Hermes runs all) and records registrations."""

    def __init__(
        self,
        *,
        standalone_plugins: Optional[Set[str]] = None,
        settings: Optional[Dict[str, Any]] = None,
        with_data_dir: bool = True,
    ) -> None:
        self._standalone = set(standalone_plugins or ())
        self._settings = dict(settings or {})
        self.tools: Dict[str, Dict[str, Any]] = {}
        self.hooks: List[tuple] = []  # (name, callback) in registration order
        self.commands: Dict[str, Any] = {}
        self.skills: Dict[str, Any] = {}
        self.providers: List[Any] = []
        self.blueprints: Dict[str, Any] = {}
        if with_data_dir:
            base = Path(tempfile.mkdtemp(prefix="vibe-scheme-b-"))
            self._cleanup_root = base
            self.state = type("S", (), {})()
            self.state.data_dir = base / "plugin-data"
            self.state.data_dir.mkdir(parents=True, exist_ok=True)
        else:
            self._cleanup_root = None
            self.state = type("S", (), {})()

    def cleanup(self) -> None:
        if self._cleanup_root is not None:
            shutil.rmtree(self._cleanup_root, ignore_errors=True)

    def has_plugin(self, plugin_id: str) -> bool:
        return plugin_id in self._standalone

    def get_config(self, key: str, default: Any = None) -> Any:
        # Dotted walk under settings (mirrors Hermes nested get_config).
        cur: Any = self._settings
        for part in str(key).split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    def register_tool(self, name: str, **kwargs: Any) -> Any:
        self.tools[name] = dict(kwargs)
        self.tools[name]["name"] = name
        return object()

    def register_hook(self, name: str, callback: Callable, **kwargs: Any) -> None:
        del kwargs
        self.hooks.append((name, callback))

    def register_command(self, name: str, handler: Any = None, **kwargs: Any) -> None:
        self.commands[name] = {"handler": handler, "kwargs": kwargs}

    def register_skill(self, name: str, path: Any = None, **kwargs: Any) -> None:
        self.skills[name] = {"path": path, "kwargs": kwargs}

    def register_delegation_provider(self, provider: Any) -> None:
        self.providers.append(provider)

    def register_automation_blueprint(self, key: str, **kwargs: Any) -> None:
        self.blueprints[key] = kwargs

    def dispatch_tool(self, tool: str, args: Any) -> str:
        return ""

    def hooks_named(self, name: str) -> List[Callable]:
        return [cb for n, cb in self.hooks if n == name]

    def run_pre_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs: Any):
        """Invoke every pre_tool_call hook in order; return first block directive."""
        for cb in self.hooks_named("pre_tool_call"):
            result = cb(tool_name=tool_name, args=dict(args), **kwargs)
            if isinstance(result, dict) and result.get("action") == "block":
                return result
        return None


def _load_plugin():
    """Load vibe-relay via file path (avoid the broken ``import __init__`` name under discover)."""
    import importlib.util

    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    mod_name = "vibe_relay_plugin_scheme_b"
    existing = sys.modules.get(mod_name)
    if existing is not None and hasattr(existing, "register"):
        return existing
    spec = importlib.util.spec_from_file_location(mod_name, ROOT / "__init__.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


class VendoredLoaderFreshTests(unittest.TestCase):
    """A cached ``lean_orchestration_vendored`` from an earlier install must not be reused."""

    def test_cached_module_is_replaced_by_this_install(self):
        import types

        plugin = _load_plugin()
        stale = types.ModuleType("lean_orchestration_vendored")
        stale.__file__ = "/nonexistent/old-install/vendor/lean_orchestration/__init__.py"
        saved = sys.modules.get("lean_orchestration_vendored")
        sys.modules["lean_orchestration_vendored"] = stale
        try:
            mod = plugin._load_vendored_lean_orchestration()
            self.assertIsNot(mod, stale)
            self.assertEqual(
                Path(mod.__file__).resolve(),
                (ROOT / "vendor" / "lean_orchestration" / "__init__.py").resolve(),
            )
        finally:
            if saved is not None:
                sys.modules["lean_orchestration_vendored"] = saved


class SchemeBRegistrationTests(IsolatedHomeTest):
    def tearDown(self):
        if getattr(self, "_ctx", None) is not None:
            self._ctx.cleanup()
        super().tearDown()

    def test_standalone_present_skips_all_lo_registrations(self):
        plugin = _load_plugin()
        self._ctx = RecordingCtx(standalone_plugins={"lean-orchestration"})
        plugin.register(self._ctx)
        for name in ORCH_TOOLS:
            self.assertNotIn(name, self._ctx.tools)
        self.assertNotIn("orch", self._ctx.commands)
        self.assertNotIn("lean-orchestration", self._ctx.skills)
        # vibe-relay still registers its own pre_tool_call; LO must not add a second.
        pre = self._ctx.hooks_named("pre_tool_call")
        self.assertEqual(len(pre), 1, "expected only vibe-relay pre_tool_call when standalone LO present")
        self.assertEqual(len(self._ctx.hooks_named("on_session_start")), 0)
        # vibe-relay surfaces still present
        self.assertIn("vibe_accept", self._ctx.tools)
        self.assertIn("vibe", self._ctx.commands)

    def test_standalone_absent_registers_all_lo_surfaces(self):
        plugin = _load_plugin()
        self._ctx = RecordingCtx(
            standalone_plugins=set(),
            settings={"lean_orchestration": {"gate_enabled": True}},
        )
        plugin.register(self._ctx)
        for name in ORCH_TOOLS:
            self.assertIn(name, self._ctx.tools)
        self.assertIn("orch", self._ctx.commands)
        self.assertIn("lean-orchestration", self._ctx.skills)
        pre = self._ctx.hooks_named("pre_tool_call")
        self.assertEqual(len(pre), 2, "vibe-relay + vendored LO pre_tool_call")
        self.assertEqual(len(self._ctx.hooks_named("on_session_start")), 1)

    def test_nested_settings_reach_vendored_lo(self):
        """CFG-1: plugins.entries.vibe-relay.settings.lean_orchestration.* via proxy."""
        plugin = _load_plugin()
        self._ctx = RecordingCtx(
            settings={"lean_orchestration": {"delegate_guard": "off"}},
        )
        plugin.register(self._ctx)
        # With guard off, a hand-written delegate_task must not be blocked by LO.
        blocked = self._ctx.run_pre_tool_call(
            "delegate_task",
            {"goal": "x", "context": "no envelope"},
        )
        # vibe-relay stop/executor-lane may still run; neither blocks bare delegate_task
        # when quota/backend not configured for stop. LO with guard off returns None.
        if blocked is not None:
            self.assertNotIn("lean-orchestration: delegate_task refused", str(blocked.get("message")))


class SchemeBHookInterplayTests(IsolatedHomeTest):
    def tearDown(self):
        if getattr(self, "_ctx", None) is not None:
            self._ctx.cleanup()
        super().tearDown()

    def _register(self):
        plugin = _load_plugin()
        self._ctx = RecordingCtx(standalone_plugins=set())
        plugin.register(self._ctx)
        return self._ctx

    def _lo_fixtures(self):
        """Return (base_brief, make_fixture) from the vendored LO tree without clobbering vibe-relay tests."""
        import importlib.util
        import types

        lo_path = str(LO_ROOT)
        if lo_path not in sys.path:
            sys.path.insert(0, lo_path)

        stub_name = "lo_vendor_stub_navigate"
        fix_name = "lo_vendor_fixtures"
        if fix_name in sys.modules:
            mod = sys.modules[fix_name]
            return mod.base_brief, mod.make_fixture

        stub_spec = importlib.util.spec_from_file_location(
            stub_name, LO_ROOT / "tests" / "stub_navigate.py"
        )
        assert stub_spec and stub_spec.loader
        stub_mod = importlib.util.module_from_spec(stub_spec)
        sys.modules[stub_name] = stub_mod
        stub_spec.loader.exec_module(stub_mod)

        # fixtures.py does `from tests.stub_navigate import STUB_NAVIGATE_SOURCE`
        pkg = types.ModuleType("tests")
        pkg.__path__ = []  # type: ignore[attr-defined]
        # Only inject if we can do so without destroying an already-imported vibe-relay tests pkg
        # used as a namespace. Prefer a private alias by rewriting the import at load time:
        src = (LO_ROOT / "tests" / "fixtures.py").read_text(encoding="utf-8")
        src = src.replace(
            "from tests.stub_navigate import STUB_NAVIGATE_SOURCE",
            "from lo_vendor_stub_navigate import STUB_NAVIGATE_SOURCE",
        )
        fix_mod = types.ModuleType(fix_name)
        sys.modules[fix_name] = fix_mod
        exec(compile(src, str(LO_ROOT / "tests" / "fixtures.py"), "exec"), fix_mod.__dict__)
        del pkg
        return fix_mod.base_brief, fix_mod.make_fixture

    def _sealed_payload(self, brief_text: str, repo: str, workdir: str) -> Dict[str, Any]:
        ctx = self._ctx
        handler = ctx.tools["orch_dispatch"]["handler"]
        raw = handler(
            {
                "repo": str(repo),
                "brief": brief_text,
                "workdir": str(workdir),
                "executor": "hermes_child",
            }
        )
        result = json.loads(raw)
        self.assertTrue(result.get("ok"), result)
        payload = result["delegate_task"]
        self.assertIn("goal", payload)
        self.assertIn("context", payload)
        # vibe-relay lane args on the same delegate_task call
        return {
            "goal": payload["goal"],
            "context": payload["context"],
            "lane": "executor",
            "allowed": "app/Sources/AppCore/Run/**",
            "gate": "python3 -B .devframework/check.py finish",
        }

    def test_sealed_delegate_task_with_lane_args_passes_both_hooks(self):
        ctx = self._register()
        base_brief, make_fixture = self._lo_fixtures()
        repo = make_fixture()
        self.addCleanup(lambda: shutil.rmtree(repo, ignore_errors=True))
        args = self._sealed_payload(base_brief(), repo, repo)
        blocked = ctx.run_pre_tool_call("delegate_task", args)
        self.assertIsNone(blocked, "sealed + lane args must pass both hooks; got %r" % (blocked,))

    def test_handwritten_lane_delegate_task_blocked_by_lo_message(self):
        ctx = self._register()
        handwritten = {
            "goal": "edit the code",
            "context": "no envelope here",
            "lane": "executor",
            "allowed": "src/**",
            "gate": "python -m unittest discover -s tests -v",
        }
        blocked = ctx.run_pre_tool_call("delegate_task", handwritten)
        self.assertIsNotNone(blocked)
        self.assertEqual(blocked.get("action"), "block")
        msg = str(blocked.get("message") or "")
        self.assertIn("lean-orchestration: delegate_task refused", msg)

    def test_executor_lane_still_blocks_parent_write(self):
        """vibe-relay executor-lane behaviour unchanged when LO is also registered."""
        from unittest import mock

        ctx = self._register()
        # Force vibe-relay backend + executor lane decision.
        delegation = {
            "backend": "vibe-relay",
            "backends": {
                "vibe-relay": {
                    "policy": "executor-always",
                    "enforce_executor_lane": True,
                    "stop_used_percent": 99,
                }
            },
        }
        usage = {
            "windows": [{"label": "Session", "used_percent": 10.0, "remaining_percent": 90.0}],
        }
        with mock.patch("executor_lane.load_delegation_config", return_value=delegation), mock.patch(
            "executor_lane.get_quota", return_value=usage
        ), mock.patch("stop_park.load_delegation_config", return_value=delegation), mock.patch(
            "stop_park.get_quota", return_value=usage
        ):
            blocked = ctx.run_pre_tool_call(
                "write_file",
                {"path": "src/x.py", "content": "x"},
            )
        self.assertIsNotNone(blocked)
        self.assertIn("vibe-relay:", str(blocked.get("message") or ""))

    def test_refused_brief_starts_no_process(self):
        ctx = self._register()
        _base_brief, make_fixture = self._lo_fixtures()
        repo = make_fixture()
        self.addCleanup(lambda: shutil.rmtree(repo, ignore_errors=True))
        handler = ctx.tools["orch_dispatch"]["handler"]
        # Incomplete brief → REFUSED; no hermes_child payload / no process.
        raw = handler(
            {
                "repo": str(repo),
                "brief": "# INTENT\nonly intent, nothing else\n",
                "workdir": str(repo),
                "executor": "hermes_child",
            }
        )
        result = json.loads(raw)
        self.assertFalse(result.get("ok", True), result)
        self.assertIn(result.get("decision"), ("REFUSED", "ERROR"))
        self.assertNotIn("delegate_task", result)
        self.assertNotIn("pid", result)


class SchemeBLoadOrderDocTests(unittest.TestCase):
    def test_alphabetical_order_loads_lean_orchestration_before_vibe_relay(self):
        # Mirrors hermes_cli.plugins_manifest.resolve_plugin_load_order with empty edges.
        keys = sorted(["lean-orchestration", "vibe-relay", "dev-framework"])
        self.assertLess(keys.index("lean-orchestration"), keys.index("vibe-relay"))


if __name__ == "__main__":
    unittest.main()
