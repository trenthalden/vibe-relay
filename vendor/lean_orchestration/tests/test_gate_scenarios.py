"""Scenario tests through cli.py and register() — DONE MEANS + CORRECTION-1."""
from __future__ import annotations

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

from tests.fixtures import LEXICAL_ONLY_KE, TOP_KE, base_brief, make_fixture, write_brief

REPO_ROOT = Path(__file__).resolve().parents[1]
CLI = str(REPO_ROOT / "cli.py")
PY39 = "/usr/bin/python3" if os.path.isfile("/usr/bin/python3") else sys.executable


class FakeCtx:
    def __init__(self, *, with_data_dir: bool = True) -> None:
        self.tools: Dict[str, Any] = {}
        self._config: Dict[str, Any] = {}
        if with_data_dir:
            self.state = type("S", (), {})()
            self.state.data_dir = Path(tempfile.mkdtemp()) / "plugin-data"
        else:
            self.state = type("S", (), {})()
            # no data_dir attribute — journal must surface journal_error

    def register_tool(self, name: str, toolset: str, schema: dict, handler: Any, **kwargs: Any) -> None:
        self.tools[name] = {"toolset": toolset, "schema": schema, "handler": handler, "kwargs": kwargs}

    def get_config(self, key: str, default: Any = None) -> Any:
        return self._config.get(key, default)


def _run_cli(
    args: List[str],
    *,
    env: Optional[Dict[str, str]] = None,
    python: str = PY39,
) -> Tuple[int, bytes, bytes]:
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    # Ensure TMPDIR stays outside framework projects for nested fixtures.
    full_env.setdefault("TMPDIR", os.environ.get("TMPDIR") or tempfile.gettempdir())
    full_env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        [python, "-B", CLI] + args,
        capture_output=True,
        env=full_env,
        timeout=60,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _gate_json(repo: str, brief_path: str, **kwargs: Any) -> Tuple[int, Dict[str, Any]]:
    args = ["gate", "--repo", repo, "--brief", brief_path, "--json"]
    for pattern in kwargs.get("allowed") or []:
        args.extend(["--allowed", pattern])
    if kwargs.get("journal"):
        args.extend(["--journal", kwargs["journal"]])
    if kwargs.get("config"):
        args.extend(["--config", kwargs["config"]])
    code, out, err = _run_cli(args)
    if not out:
        raise AssertionError("empty stdout; stderr=%r code=%s" % (err.decode("utf-8", "replace"), code))
    return code, json.loads(out.decode("utf-8"))


def _load_plugin():
    import importlib.util

    spec = importlib.util.spec_from_file_location("lean_orchestration_plugin", REPO_ROOT / "__init__.py")
    assert spec and spec.loader
    plugin = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(plugin)
    return plugin


class TestMissingAndEmptySections(unittest.TestCase):
    """DONE MEANS 1."""

    def test_missing_known_errors_refused(self) -> None:
        repo = make_fixture()
        text = base_brief().replace("## Known errors\n\nKE-2026-09-23-MIRROR-FULL-SCAN — relevant prior defect.\n\n", "")
        brief = write_brief(os.path.join(tempfile.mkdtemp(), "brief.md"), text)
        code, result = _gate_json(repo, brief)
        self.assertEqual(code, 3)
        self.assertEqual(result["decision"], "REFUSED")
        self.assertEqual(result["reasons"], ["missing_section:known errors"])
        plugin = _load_plugin()
        ctx = FakeCtx()
        plugin.register(ctx)
        tool = json.loads(ctx.tools["orch_gate"]["handler"]({"repo": repo, "brief": brief}))
        self.assertEqual(tool["decision"], result["decision"])
        self.assertEqual(tool["reasons"], result["reasons"])

    def test_empty_gate_refused(self) -> None:
        repo = make_fixture()
        text = base_brief().replace("## GATE\n\npython3 -B .devframework/check.py finish\n\n", "## GATE\n\n\n")
        brief = write_brief(os.path.join(tempfile.mkdtemp(), "brief.md"), text)
        code, result = _gate_json(repo, brief)
        self.assertEqual(code, 3)
        self.assertIn("empty_section:gate", result["reasons"])


class TestUnknownId(unittest.TestCase):
    """DONE MEANS 2."""

    def test_unknown_ke_refused(self) -> None:
        repo = make_fixture()
        text = base_brief(known_errors="KE-2099-01-01-NOPE — fabricated.")
        brief = write_brief(os.path.join(tempfile.mkdtemp(), "brief.md"), text)
        code, result = _gate_json(repo, brief)
        self.assertEqual(code, 3)
        self.assertIn("unknown_id:KE-2099-01-01-NOPE", result["reasons"])


class TestAreaCoverage(unittest.TestCase):
    """DONE MEANS 3."""

    def test_missing_areas_refused(self) -> None:
        repo = make_fixture()
        text = base_brief(architecture_rules="Only UI-1 cited.")
        brief = write_brief(os.path.join(tempfile.mkdtemp(), "brief.md"), text)
        code, result = _gate_json(repo, brief)
        self.assertEqual(code, 3)
        self.assertIn("area_not_cited:BULK", result["reasons"])
        self.assertIn("area_not_cited:JOB", result["reasons"])

    def test_all_areas_pass(self) -> None:
        repo = make_fixture()
        text = base_brief(architecture_rules="UI-1, JOB-1, BULK-2 apply.")
        brief = write_brief(os.path.join(tempfile.mkdtemp(), "brief.md"), text)
        code, result = _gate_json(repo, brief)
        self.assertEqual(code, 0, result)
        self.assertEqual(result["decision"], "PASS")


class TestTaskDelivery(unittest.TestCase):
    """DONE MEANS 4 + C1(a): area-linked full block; lexical titles only."""

    def test_area_linked_full_block_and_lexical_title(self) -> None:
        repo = make_fixture()
        text = base_brief(
            known_errors="KE: none relevant (searched: timer due-hint)",
            architecture_rules="UI-1, JOB-1, BULK-2 apply.",
        )
        brief = write_brief(os.path.join(tempfile.mkdtemp(), "brief.md"), text)
        code, out, err = _run_cli(["task", "--repo", repo, "--brief", brief])
        self.assertEqual(code, 0, err.decode("utf-8", "replace"))
        task = out.decode("utf-8")
        self.assertIn("## INTENT", task)
        self.assertIn("JOB-1", task)
        # Area-linked KE (never cited) → full block.
        self.assertIn("KE-2026-09-23-MIRROR-FULL-SCAN", task)
        self.assertIn("per-minute full-scan", task)
        self.assertIn("**Rules:** JOB-1", task)
        # Lexical-only → titles section, not full block body.
        self.assertIn("### Possibly related (lexical, titles only)", task)
        self.assertIn(
            "- %s — %s" % (LEXICAL_ONLY_KE["id"], LEXICAL_ONLY_KE["title"]),
            task,
        )
        self.assertNotIn("This block must not appear as a full delivery", task)
        self.assertIn("lo-delivery/2", task)

    def test_delivery_cap_omits(self) -> None:
        repo = make_fixture()
        text = base_brief(
            known_errors="KE: none relevant (searched: timer)",
            architecture_rules="UI-1, JOB-1, BULK-2 apply.",
        )
        brief = write_brief(os.path.join(tempfile.mkdtemp(), "brief.md"), text)
        cfg_dir = tempfile.mkdtemp()
        cfg = os.path.join(cfg_dir, "cfg.json")
        with open(cfg, "w", encoding="utf-8") as fh:
            json.dump({"delivery_cap_bytes": 400}, fh)
        code, out, err = _run_cli(
            ["task", "--repo", repo, "--brief", brief, "--config", cfg]
        )
        self.assertEqual(code, 0, err.decode("utf-8", "replace"))
        task = out.decode("utf-8")
        self.assertIn("Not delivered (cap):", task)


class TestPorting(unittest.TestCase):
    """DONE MEANS 5."""

    def test_port_from_without_call_site(self) -> None:
        repo = make_fixture()
        text = base_brief(
            architecture_rules="UI-1, JOB-1, BULK-2 apply.",
            extra_sections="PORT FROM: app/Sources/Old/File.swift:10\n",
        )
        brief = write_brief(os.path.join(tempfile.mkdtemp(), "brief.md"), text)
        code, result = _gate_json(repo, brief)
        self.assertEqual(code, 3)
        self.assertIn("call_site_missing", result["reasons"])


class TestRulesSource(unittest.TestCase):
    """DONE MEANS 6."""

    def test_old_framework_exit_4(self) -> None:
        repo = make_fixture(reject_json=True)
        text = base_brief(architecture_rules="UI-1, JOB-1, BULK-2 apply.")
        brief = write_brief(os.path.join(tempfile.mkdtemp(), "brief.md"), text)
        code, result = _gate_json(repo, brief)
        self.assertEqual(code, 4)
        self.assertIn("framework_too_old", result["reasons"])

    def test_none_without_statement_refused(self) -> None:
        root = tempfile.mkdtemp(prefix="lo-none-")
        text = base_brief(
            allowed="`docs/notes.md`",
            architecture_rules="Be careful.",
            known_errors="KE: none relevant (searched: notes)",
        )
        brief = write_brief(os.path.join(tempfile.mkdtemp(), "brief.md"), text)
        code, result = _gate_json(root, brief)
        self.assertEqual(code, 3)
        self.assertIn("rules_none_not_stated", result["reasons"])

    def test_none_with_statement_ok(self) -> None:
        root = tempfile.mkdtemp(prefix="lo-none-")
        text = base_brief(
            allowed="`docs/notes.md`",
            architecture_rules="Rules: none in project",
            known_errors="KE: none relevant (searched: notes)",
        )
        brief = write_brief(os.path.join(tempfile.mkdtemp(), "brief.md"), text)
        code, result = _gate_json(root, brief)
        self.assertEqual(code, 0, result)
        self.assertEqual(result["rules_source"]["kind"], "none")

    def test_rules_json_fallback_delivers_linked_kes(self) -> None:
        root = tempfile.mkdtemp(prefix="lo-rj-")
        lo = os.path.join(root, ".lean-orchestration")
        os.makedirs(lo, exist_ok=True)
        payload = {
            "schema": "lean-orchestration.rules/1",
            "rows": [
                {"area": "SCOPE", "id": "SCOPE-1", "rule": "scope", "prevents": "x"},
                {"area": "UI", "id": "UI-1", "rule": "ui", "prevents": "y"},
                {"area": "JOB", "id": "JOB-1", "rule": "job", "prevents": "z"},
                {"area": "BULK", "id": "BULK-2", "rule": "bulk", "prevents": "w"},
            ],
            "areas": [{"glob": "app/**/Run/**", "areas": ["UI", "JOB", "BULK"]}],
            "known_errors": [
                {
                    "id": "KE-2026-09-23-MIRROR-FULL-SCAN",
                    "title": "full scan",
                    "status": "",
                    "do_not_return": "scan",
                    "block": TOP_KE["block"],
                },
                {
                    "id": "KE-2026-01-01-TITLE-ONLY",
                    "title": "unlinked title only",
                    "status": "",
                    "do_not_return": "",
                    "block": "### KE-2026-01-01-TITLE-ONLY: unlinked title only\n\nno area link\n",
                },
            ],
        }
        with open(os.path.join(lo, "rules.json"), "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        text = base_brief(
            architecture_rules="UI-1, JOB-1, BULK-2 apply.",
            known_errors="KE: none relevant (searched: timer)",
        )
        brief = write_brief(os.path.join(tempfile.mkdtemp(), "brief.md"), text)
        code, out, err = _run_cli(["task", "--repo", root, "--brief", brief])
        self.assertEqual(code, 0, err.decode("utf-8", "replace"))
        task = out.decode("utf-8")
        self.assertIn("KE-2026-09-23-MIRROR-FULL-SCAN", task)
        self.assertIn("**Rules:** JOB-1", task)
        self.assertIn("- KE-2026-01-01-TITLE-ONLY — unlinked title only", task)
        self.assertNotIn("no area link", task)


class TestJournal(unittest.TestCase):
    """DONE MEANS 7."""

    def test_journal_line_and_rotation(self) -> None:
        repo = make_fixture()
        text = base_brief(architecture_rules="UI-1, JOB-1, BULK-2 apply.")
        brief = write_brief(os.path.join(tempfile.mkdtemp(), "brief.md"), text)
        cfg_dir = tempfile.mkdtemp()
        cfg = os.path.join(cfg_dir, "cfg.json")
        with open(cfg, "w", encoding="utf-8") as fh:
            json.dump({"journal_rotate_bytes": 200}, fh)
        journal = os.path.join(cfg_dir, "journal.jsonl")
        for _ in range(5):
            code, result = _gate_json(repo, brief, journal=journal, config=cfg)
            self.assertEqual(code, 0, result)
        self.assertTrue(os.path.isfile(journal))
        with open(journal, "r", encoding="utf-8") as fh:
            line = fh.readline()
        obj = json.loads(line)
        self.assertEqual(list(obj.keys()), sorted(obj.keys()))
        self.assertIn("ts", obj)
        self.assertIn("decision", obj)
        self.assertTrue(
            os.path.isfile(journal + ".1") or os.path.getsize(journal) <= 200 * 5,
            "expected rotation at small configured size",
        )
        with open(journal, "wb") as fh:
            fh.write(b"x" * 250)
        code, result = _gate_json(repo, brief, journal=journal, config=cfg)
        self.assertEqual(code, 0, result)
        self.assertTrue(os.path.isfile(journal + ".1"))
        self.assertTrue(os.path.isfile(journal))


class TestDeterminism(unittest.TestCase):
    """DONE MEANS 8 / C1(d)."""

    def test_gate_json_and_task_hashseed_stable(self) -> None:
        repo = make_fixture()
        text = base_brief(architecture_rules="UI-1, JOB-1, BULK-2 apply.")
        brief = write_brief(os.path.join(tempfile.mkdtemp(), "brief.md"), text)
        outs = []
        for seed in ("0", "12345"):
            code, out, err = _run_cli(
                ["gate", "--repo", repo, "--brief", brief, "--json"],
                env={"PYTHONHASHSEED": seed},
            )
            self.assertEqual(code, 0, err.decode("utf-8", "replace"))
            outs.append(out)
        self.assertEqual(outs[0], outs[1])

        tasks = []
        for seed in ("0", "12345"):
            code, out, err = _run_cli(
                ["task", "--repo", repo, "--brief", brief],
                env={"PYTHONHASHSEED": seed},
            )
            self.assertEqual(code, 0, err.decode("utf-8", "replace"))
            tasks.append(out)
        self.assertEqual(tasks[0], tasks[1])


class TestStaticImports(unittest.TestCase):
    """DONE MEANS 9."""

    FORBIDDEN_HERMES = (
        "hermes_cli",
        "from agent",
        "import agent",
        "from tools",
        "import tools",
        "from gateway",
        "import gateway",
        "run_agent",
        "import cron",
        "from cron",
        "plugins.plugin_storage",
    )
    FORBIDDEN_NET = ("import socket", "import http", "import urllib", "import ssl", "from socket", "from http", "from urllib", "from ssl")

    def test_no_hermes_or_network_imports(self) -> None:
        roots = [REPO_ROOT / "lo_core", REPO_ROOT / "cli.py", REPO_ROOT / "__init__.py"]
        files: List[Path] = []
        for root in roots:
            if root.is_file():
                files.append(root)
            else:
                files.extend(root.rglob("*.py"))
        for path in files:
            text = path.read_text(encoding="utf-8")
            for token in self.FORBIDDEN_HERMES + self.FORBIDDEN_NET:
                self.assertNotIn(token, text, msg="%s contains %r" % (path, token))

    def test_lo_core_stdlib_only(self) -> None:
        import ast

        stdlib_ok = {
            "annotations", "argparse", "ast", "hashlib", "json", "os", "re", "subprocess",
            "sys", "tempfile", "typing", "datetime", "pathlib", "__future__",
        }
        for path in (REPO_ROOT / "lo_core").rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        top = alias.name.split(".")[0]
                        self.assertTrue(
                            top in stdlib_ok or top == "lo_core",
                            msg="%s imports %s" % (path, alias.name),
                        )
                elif isinstance(node, ast.ImportFrom):
                    if node.module is None:
                        continue
                    top = node.module.split(".")[0]
                    self.assertTrue(
                        top in stdlib_ok or top == "lo_core",
                        msg="%s imports from %s" % (path, node.module),
                    )


class TestRegisterTool(unittest.TestCase):
    """DONE MEANS 10."""

    def test_register_and_handler_matches_cli(self) -> None:
        repo = make_fixture()
        text = base_brief(architecture_rules="UI-1, JOB-1, BULK-2 apply.")
        brief = write_brief(os.path.join(tempfile.mkdtemp(), "brief.md"), text)

        plugin = _load_plugin()
        ctx = FakeCtx()
        plugin.register(ctx)
        self.assertIn("orch_gate", ctx.tools)
        handler = ctx.tools["orch_gate"]["handler"]
        raw = handler({"repo": repo, "brief": brief})
        tool_result = json.loads(raw)

        code, cli_result = _gate_json(repo, brief)
        self.assertEqual(code, 0)
        self.assertEqual(tool_result["decision"], cli_result["decision"])
        self.assertEqual(tool_result["reasons"], cli_result["reasons"])


class TestLatency(unittest.TestCase):
    """DONE MEANS 11."""

    def test_gate_under_one_second_with_150_kes(self) -> None:
        repo = make_fixture(extra_kes=150)
        text = base_brief(architecture_rules="UI-1, JOB-1, BULK-2 apply.")
        brief = write_brief(os.path.join(tempfile.mkdtemp(), "brief.md"), text)
        start = time.perf_counter()
        code, result = _gate_json(repo, brief)
        elapsed = time.perf_counter() - start
        self.assertEqual(code, 0, result)
        self.assertLess(elapsed, 1.0, "gate took %.4fs" % elapsed)
        print("LATENCY_150_KE=%.4f" % elapsed)


class TestBadInput(unittest.TestCase):
    """C1(b): bad_repo / brief_not_found through CLI and tool."""

    def test_bad_repo_cli_and_tool(self) -> None:
        text = base_brief(architecture_rules="UI-1, JOB-1, BULK-2 apply.")
        brief = write_brief(os.path.join(tempfile.mkdtemp(), "brief.md"), text)
        code, out, err = _run_cli(
            ["gate", "--repo", "/no/such/absolute/repo", "--brief", brief, "--json"]
        )
        self.assertEqual(code, 2)
        self.assertIn(b"bad_repo", err)
        self.assertEqual(out, b"")

        plugin = _load_plugin()
        ctx = FakeCtx()
        plugin.register(ctx)
        tool = json.loads(
            ctx.tools["orch_gate"]["handler"]({"repo": "/no/such/absolute/repo", "brief": brief})
        )
        self.assertEqual(tool["decision"], "ERROR")
        self.assertEqual(tool["reasons"], ["bad_repo"])

    def test_brief_not_found_cli_and_tool(self) -> None:
        repo = make_fixture()
        missing = os.path.join(tempfile.mkdtemp(), "missing-brief.md")
        code, out, err = _run_cli(
            ["gate", "--repo", repo, "--brief", missing, "--json"]
        )
        self.assertEqual(code, 2)
        self.assertIn(b"brief_not_found", err)

        plugin = _load_plugin()
        ctx = FakeCtx()
        plugin.register(ctx)
        tool = json.loads(
            ctx.tools["orch_gate"]["handler"]({"repo": repo, "brief": missing})
        )
        self.assertEqual(tool["decision"], "ERROR")
        self.assertEqual(tool["reasons"], ["brief_not_found"])


class TestJournalErrorNoDataDir(unittest.TestCase):
    """C1(c): no ctx.state.data_dir → journal_error, decision unchanged."""

    def test_journal_error_without_data_dir(self) -> None:
        repo = make_fixture()
        text = base_brief(architecture_rules="UI-1, JOB-1, BULK-2 apply.")
        brief = write_brief(os.path.join(tempfile.mkdtemp(), "brief.md"), text)
        plugin = _load_plugin()
        ctx = FakeCtx(with_data_dir=False)
        plugin.register(ctx)
        tool = json.loads(ctx.tools["orch_gate"]["handler"]({"repo": repo, "brief": brief}))
        self.assertEqual(tool["decision"], "PASS")
        self.assertEqual(tool["journal_error"], "no plugin data directory")


class TestBriefGrammar(unittest.TestCase):
    """L2: qualified headings, fenced headings, ALLOWED brace expansion (API-4, BULK-3)."""

    def _qualified_brief(self, **kwargs: Any) -> str:
        known = kwargs.get(
            "known_errors",
            "KE-2026-09-23-MIRROR-FULL-SCAN — relevant prior defect.",
        )
        arch = kwargs.get("architecture_rules", "UI-1, JOB-1, BULK-2 apply.")
        allowed = kwargs.get(
            "allowed",
            "`app/Sources/AppCore/Run/**`",
        )
        return (
            "# BRIEF\n\n"
            "## INTENT (goal)\n\nFix the due-hint timer.\n\n"
            "## ALLOWED (paths only)\n\n%s\n\n"
            "## FORBIDDEN (сверх COMMON)\n\n- other paths\n\n"
            "## DONE MEANS (тесты пишешь, не запускаешь)\n\n1. Works.\n\n"
            "## GATE (local)\n\npython3 -B .devframework/check.py finish\n\n"
            "## Known errors — field\n\n%s\n\n"
            "## Architecture rules: must cite\n\n%s\n\n"
        ) % (allowed, known, arch)

    def test_qualified_headings_accepted_cli_and_tool(self) -> None:
        repo = make_fixture()
        brief = write_brief(
            os.path.join(tempfile.mkdtemp(), "brief.md"),
            self._qualified_brief(),
        )
        code, result = _gate_json(repo, brief)
        self.assertEqual(code, 0, result)
        self.assertEqual(result["decision"], "PASS")
        self.assertFalse(
            any(r.startswith("missing_section:") for r in result["reasons"]),
            result["reasons"],
        )
        plugin = _load_plugin()
        ctx = FakeCtx()
        plugin.register(ctx)
        tool = json.loads(ctx.tools["orch_gate"]["handler"]({"repo": repo, "brief": brief}))
        self.assertEqual(tool["decision"], "PASS")
        self.assertEqual(tool["reasons"], result["reasons"])

    def test_fenced_allowed_heading_ignored(self) -> None:
        repo = make_fixture()
        text = base_brief(
            architecture_rules="UI-1, JOB-1, BULK-2 apply.",
            allowed=(
                "`app/Sources/AppCore/Run/**`\n\n"
                "```\n## ALLOWED\n`fake/inside/fence/**`\n```\n"
            ),
        )
        brief = write_brief(os.path.join(tempfile.mkdtemp(), "brief.md"), text)
        code, result = _gate_json(repo, brief)
        self.assertEqual(code, 0, result)
        self.assertIn("app/Sources/AppCore/Run/**", result["allowed"])
        self.assertNotIn("fake/inside/fence/**", result["allowed"])

    def test_brace_expansion_two_groups_and_empty_alt(self) -> None:
        repo = make_fixture()
        text = base_brief(
            architecture_rules="UI-1, JOB-1, BULK-2 apply.",
            allowed=(
                "`pkg/{A,B}/{,X}.swift`\n"
                "`app/Sources/AppCore/Run/**`"
            ),
        )
        brief = write_brief(os.path.join(tempfile.mkdtemp(), "brief.md"), text)
        code, result = _gate_json(repo, brief)
        self.assertEqual(code, 0, result)
        allowed = result["allowed"]
        self.assertIn("pkg/A/.swift", allowed)
        self.assertIn("pkg/A/X.swift", allowed)
        self.assertIn("pkg/B/.swift", allowed)
        self.assertIn("pkg/B/X.swift", allowed)
        self.assertNotIn("pkg/{A,B}/{,X}.swift", allowed)

    def test_brace_cap_refuses(self) -> None:
        repo = make_fixture()
        # 65 alternatives → product 65 > cap 64 (BULK-3).
        alts = ",".join(["a%02d" % i for i in range(65)])
        brace_pat = "dir/{%s}.py" % alts
        text = base_brief(
            architecture_rules="UI-1, JOB-1, BULK-2 apply.",
            allowed="`%s`" % brace_pat,
        )
        brief = write_brief(os.path.join(tempfile.mkdtemp(), "brief.md"), text)
        code, result = _gate_json(repo, brief)
        self.assertEqual(code, 3)
        self.assertEqual(result["decision"], "REFUSED")
        self.assertIn("allowed_pattern_too_broad:%s" % brace_pat, result["reasons"])
        self.assertEqual(result["allowed"], [])

    def test_nested_braces_literal_unmapped(self) -> None:
        repo = make_fixture()
        brace_pat = "Services/{ActivityProbe,{Nested,X}}.swift"
        text = base_brief(
            architecture_rules="Be careful.",
            allowed="`%s`" % brace_pat,
            known_errors="KE: none relevant (searched: nested)",
        )
        # No matching areas → kind may be navigate with only SCOPE; state none rules.
        root = tempfile.mkdtemp(prefix="lo-nested-")
        brief = write_brief(os.path.join(tempfile.mkdtemp(), "brief.md"), text)
        # Use empty fixture without path map match; still need a rules source.
        code, result = _gate_json(root, brief)
        self.assertIn(brace_pat, result["unmapped"])
        self.assertIn(brace_pat, result["allowed"])

    def test_brace_maps_runcoordinator_area(self) -> None:
        run_file = (
            "app/Sources/App/Services/RunCoordinator.swift"
        )
        probe = (
            "app/Sources/App/Services/ActivityProbe.swift"
        )
        repo = make_fixture(path_areas={run_file: ["UI", "JOB", "BULK"]})
        brace_pat = (
            "app/Sources/App/Services/"
            "{ActivityProbe,RunCoordinator}.swift"
        )
        text = base_brief(
            allowed="`%s`" % brace_pat,
            architecture_rules="UI-1, JOB-1, BULK-2 apply.",
        )
        brief = write_brief(os.path.join(tempfile.mkdtemp(), "brief.md"), text)
        code, result = _gate_json(repo, brief)
        self.assertEqual(code, 0, result)
        self.assertEqual(
            result["allowed"],
            [probe, run_file],
        )
        self.assertIn("JOB", result["areas"])
        self.assertIn("UI", result["areas"])
        self.assertIn("BULK", result["areas"])
        self.assertIn(probe, result["unmapped"])
        self.assertNotIn(brace_pat, result["allowed"])


class TestRedDetectors(unittest.TestCase):
    """RED evidence for regression detectors (VER-3) — mutations under TMPDIR only."""

    def test_red_missing_section_detector(self) -> None:
        """Mutate a scratch copy of gate.py so missing sections are ignored; detector must fail."""
        scratch = Path(tempfile.mkdtemp(prefix="lo-red-"))
        for name in ("lo_core", "cli.py"):
            src = REPO_ROOT / name
            dst = scratch / name
            if src.is_dir():
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)
        gate_path = scratch / "lo_core" / "gate.py"
        text = gate_path.read_text(encoding="utf-8")
        mutated = text.replace(
            'reasons.append("missing_section:%s" % key)',
            "pass  # RED mutation: skip missing_section",
        )
        self.assertNotEqual(text, mutated)
        gate_path.write_text(mutated, encoding="utf-8")

        repo = make_fixture()
        brief_text = base_brief().replace(
            "## Known errors\n\nKE-2026-09-23-MIRROR-FULL-SCAN — relevant prior defect.\n\n",
            "",
        )
        brief = write_brief(os.path.join(tempfile.mkdtemp(), "brief.md"), brief_text)
        env = os.environ.copy()
        # Scratch lo_core first; repo root next so cli.py can import lo_exec (Hermes
        # PYTHONPATH does not include the project and would otherwise hide it).
        env["PYTHONPATH"] = (
            str(scratch) + os.pathsep + str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
        )
        proc = subprocess.run(
            [PY39, "-B", str(scratch / "cli.py"), "gate", "--repo", repo, "--brief", brief, "--json"],
            capture_output=True,
            env=env,
            timeout=60,
        )
        result = json.loads(proc.stdout.decode("utf-8"))
        with self.assertRaises(AssertionError) as ctx:
            self.assertIn("missing_section:known errors", result["reasons"])
        print("RED_ASSERTION=%r" % str(ctx.exception))

    def test_red_qualifier_heading_detector(self) -> None:
        """Revert qualifier match on a scratch copy; detector for qualified briefs must fail."""
        scratch = Path(tempfile.mkdtemp(prefix="lo-red-qual-"))
        for name in ("lo_core", "cli.py"):
            src = REPO_ROOT / name
            dst = scratch / name
            if src.is_dir():
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)
        brief_path = scratch / "lo_core" / "brief.py"
        text = brief_path.read_text(encoding="utf-8")
        # RED mutation: exact heading equality only (pre-L2 behaviour).
        mutated = text.replace(
            "return rest[i] in _QUALIFIER_DELIMS",
            "return False  # RED mutation: ignore qualifier match",
        )
        self.assertNotEqual(text, mutated)
        brief_path.write_text(mutated, encoding="utf-8")

        repo = make_fixture()
        qualified = (
            "# BRIEF\n\n"
            "## INTENT (goal)\n\nFix.\n\n"
            "## ALLOWED (paths)\n\n`app/Sources/AppCore/Run/**`\n\n"
            "## FORBIDDEN (сверх COMMON)\n\n- other\n\n"
            "## DONE MEANS (тесты)\n\n1. Works.\n\n"
            "## GATE (local)\n\npython3 -B .devframework/check.py finish\n\n"
            "## Known errors — field\n\nKE-2026-09-23-MIRROR-FULL-SCAN — x.\n\n"
            "## Architecture rules: cite\n\nUI-1, JOB-1, BULK-2 apply.\n\n"
        )
        brief = write_brief(os.path.join(tempfile.mkdtemp(), "brief.md"), qualified)
        env = os.environ.copy()
        env["PYTHONPATH"] = (
            str(scratch) + os.pathsep + str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
        )
        proc = subprocess.run(
            [PY39, "-B", str(scratch / "cli.py"), "gate", "--repo", repo, "--brief", brief, "--json"],
            capture_output=True,
            env=env,
            timeout=60,
        )
        result = json.loads(proc.stdout.decode("utf-8"))
        with self.assertRaises(AssertionError) as ctx:
            self.assertFalse(
                any(r.startswith("missing_section:") for r in result["reasons"]),
                result["reasons"],
            )
        print("RED_QUALIFIER_ASSERTION=%r" % str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
