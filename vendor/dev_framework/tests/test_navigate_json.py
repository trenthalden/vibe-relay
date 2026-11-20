"""Machine-readable navigate.py --json (contract v1 / DEV Framework 1.7.0).

Runs the real template/.devframework/navigate.py as a subprocess on fixture projects
under TMPDIR (never inside a repository). Covers DONE MEANS from the 1.7.0 brief.
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

ROOT = Path(__file__).resolve().parents[1]
NAV_SRC = ROOT / "template" / ".devframework" / "navigate.py"
HEALTH_SRC = ROOT / "template" / ".devframework" / "ARCHITECTURE_HEALTHCHECK.md"
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()

# Isolated scratch for RED mutations (lean-orchestration KE-2026-11-20-TMPDIR-NESTED-PROJECT).
SCRATCH = Path(os.environ.get("TMPDIR") or tempfile.gettempdir()).resolve()


def _load_nav():
    spec = importlib.util.spec_from_file_location("df_navigate_json_under_test", NAV_SRC)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


KE_FIXTURE = """# Known errors — fixture

## KE-2026-01-01-OTHER-A — unrelated network timeout on sync

- Some body about sockets and retries.
- **Status:** open
- **Не возвращать:** silent catch of network errors.

## KE-2026-09-23-MIRROR-FULL-SCAN — кросс-словарь каждую минуту читал все карточки и замораживал окно

Периодический тик раз в минуту перечитывал весь словарь карточек.

- **Status:** open
- **Не возвращать:** полный проход по всем карточкам по таймеру.

## KE-2026-01-02-OTHER-B — clipboard paste dropped newlines

- Body about pasteboard.
- **Status:** fixed in 1.0
- **Do not return:** stripping trailing newlines on paste.
"""

BRIEF_TICK = (
    "Подсказка на каждом секундном тике: не запускать полный проход карточек по таймеру.\n"
)

USE_CASES = """# Use cases

#### UC-001 — Preview an install

- **Test:** covered
"""

REQUIREMENTS = """# Requirements

| ID | Requirement | Status |
|---|---|---|
| FR-001 | A project carries its rules. | implemented |
| NFR-001 | Deterministic gates. | implemented |
"""


class NavigateJsonTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="nav-json-", dir=str(SCRATCH))).resolve()
        self.addCleanup(lambda: shutil.rmtree(self.base, ignore_errors=True))
        self.repo = self.base / "repo"
        self._seed(self.repo)

    def _seed(self, repo: Path) -> None:
        df = repo / ".devframework"
        df.mkdir(parents=True, exist_ok=True)
        shutil.copy(NAV_SRC, df / "navigate.py")
        shutil.copy(HEALTH_SRC, df / "ARCHITECTURE_HEALTHCHECK.md")
        (df / "manifest.json").write_text(
            json.dumps({"format": 1, "version": VERSION, "parameters": {}, "files": {}}, ensure_ascii=False),
            encoding="utf-8",
        )
        (df / "project.json").write_text(
            json.dumps({
                "testing": "lean",
                "areas": [
                    {"glob": "MainOS.Mac/**/Run/**", "areas": ["UI", "JOB", "BULK"]},
                    {"glob": "**/*Sync*.swift", "areas": ["DB", "TX"]},
                ],
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        docs = repo / "docs"
        docs.mkdir(parents=True, exist_ok=True)
        (docs / "KNOWN_ERRORS.md").write_text(KE_FIXTURE, encoding="utf-8")
        (docs / "USE_CASES.md").write_text(USE_CASES, encoding="utf-8")
        (docs / "REQUIREMENTS.md").write_text(REQUIREMENTS, encoding="utf-8")
        (repo / "BRIEF.md").write_text(BRIEF_TICK, encoding="utf-8")

    def _run(self, *argv: str, env: dict | None = None, timeout: float = 30.0) -> subprocess.CompletedProcess:
        cmd = [sys.executable, "-B", str(self.repo / ".devframework" / "navigate.py"),
               "--root", str(self.repo), *argv]
        merged = os.environ.copy()
        if env:
            merged.update(env)
        # cost: one navigate.py subprocess per assertion (~ms–s); suite keeps this under the 1 s gate budget
        return subprocess.run(
            cmd, capture_output=True, timeout=timeout, env=merged,
        )

    def _json(self, *argv: str, env: dict | None = None, timeout: float = 30.0) -> tuple[int, dict, bytes]:
        result = self._run(*argv, env=env, timeout=timeout)
        raw = result.stdout
        try:
            data = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as error:
            self.fail(f"stdout not JSON (exit {result.returncode}): {raw!r} / stderr={result.stderr!r} / {error}")
        return result.returncode, data, raw

    # --- DONE MEANS 1: health --json / --paths ---

    def test_health_json_areas_and_paths_map(self):
        code, data, _ = self._json("health", "ui", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(data["schema"], "devframework.health/1")
        self.assertEqual(data["framework_version"], VERSION)
        self.assertEqual(data["areas"], ["SCOPE", "UI"])
        self.assertTrue(any(r["id"] == "SCOPE-1" for r in data["rows"]))
        self.assertTrue(any(r["id"] == "UI-1" for r in data["rows"]))
        self.assertFalse(any(r["area"] == "DB" for r in data["rows"]))

        code, data, _ = self._json(
            "health", "--paths",
            "MainOS.Mac/Sources/MainOSMacCore/Run/**",
            "docs/notes.md",
            "--json",
        )
        self.assertEqual(code, 0)
        self.assertEqual(data["areas"], ["BULK", "JOB", "SCOPE", "UI"])
        self.assertEqual(len(data["matched"]), 1)
        self.assertEqual(data["matched"][0]["path"], "MainOS.Mac/Sources/MainOSMacCore/Run/**")
        self.assertEqual(data["matched"][0]["globs"], ["MainOS.Mac/**/Run/**"])
        self.assertEqual(data["matched"][0]["areas"], ["BULK", "JOB", "UI"])
        self.assertEqual(data["unmapped"], ["docs/notes.md"])

    # --- DONE MEANS 2: known-errors ranking + --ids ---

    def test_known_errors_ranks_tick_block_first_and_ids_order(self):
        code, data, _ = self._json(
            "known-errors", "--json", "--text-file", str(self.repo / "BRIEF.md"), "--full",
        )
        self.assertEqual(code, 0)
        self.assertEqual(data["algorithm"], "ke-lexical/1")
        self.assertGreaterEqual(data["total"], 3)
        self.assertGreaterEqual(len(data["candidates"]), 1)
        self.assertEqual(data["candidates"][0]["id"], "KE-2026-09-23-MIRROR-FULL-SCAN")
        self.assertIn("полный проход", data["candidates"][0]["do_not_return"])
        self.assertIn("block", data["candidates"][0])
        self.assertTrue(data["candidates"][0]["block"].startswith("## KE-2026-09-23-MIRROR-FULL-SCAN"))

        code, data, _ = self._json(
            "known-errors", "--json", "--ids",
            "KE-2026-01-02-OTHER-B", "KE-2026-01-01-OTHER-A", "KE-2099-01-01-NOPE",
            "KE-2026-01-02-OTHER-B",
            "--full",
        )
        self.assertEqual(code, 0)
        ids = [c["id"] for c in data["candidates"]]
        self.assertEqual(ids, ["KE-2026-01-02-OTHER-B", "KE-2026-01-01-OTHER-A"])
        self.assertEqual(data["candidates"][0]["score"], 0.0)
        self.assertEqual(data["candidates"][0]["matched"], [])

    # --- DONE MEANS 3: ids --check ---

    def test_ids_check_four_kinds_input_order(self):
        code, data, _ = self._json(
            "ids", "--check",
            "KE-2026-09-23-MIRROR-FULL-SCAN", "JOB-1", "UC-001", "FR-001",
            "KE-2099-01-01-NOPE", "JOB-1", "NFR-001",
            "--json",
        )
        self.assertEqual(code, 0)
        self.assertEqual(data["schema"], "devframework.ids/1")
        self.assertEqual(
            [(k["id"], k["kind"]) for k in data["known"]],
            [
                ("KE-2026-09-23-MIRROR-FULL-SCAN", "known_error"),
                ("JOB-1", "health_rule"),
                ("UC-001", "use_case"),
                ("FR-001", "requirement"),
                ("NFR-001", "requirement"),
            ],
        )
        self.assertEqual(data["unknown"], ["KE-2099-01-01-NOPE"])

    # --- DONE MEANS 4: errors ---

    def test_errors_missing_file_bad_config_bad_args(self):
        (self.repo / "docs" / "KNOWN_ERRORS.md").unlink()
        code, data, _ = self._json("known-errors", "--json", "--terms", "тик")
        self.assertEqual(code, 2)
        self.assertEqual(data["schema"], "devframework.error/1")
        self.assertEqual(data["error"], "missing_file")

        self._seed(self.repo)  # restore
        cfg = json.loads((self.repo / ".devframework" / "project.json").read_text(encoding="utf-8"))
        cfg["areas"] = [{"glob": "x/**", "areas": ["NOTANAREA"]}]
        (self.repo / ".devframework" / "project.json").write_text(
            json.dumps(cfg, ensure_ascii=False), encoding="utf-8",
        )
        code, data, _ = self._json("health", "--paths", "x/y", "--json")
        self.assertEqual(code, 2)
        self.assertEqual(data["error"], "bad_config")

        cfg["areas"] = "not-a-list"
        (self.repo / ".devframework" / "project.json").write_text(
            json.dumps(cfg, ensure_ascii=False), encoding="utf-8",
        )
        code, data, _ = self._json("health", "--paths", "x/y", "--json")
        self.assertEqual(code, 2)
        self.assertEqual(data["error"], "bad_config")

        self._seed(self.repo)
        code, data, _ = self._json(
            "known-errors", "--json", "--ids", "KE-2026-01-01-OTHER-A", "--terms", "тик",
        )
        self.assertEqual(code, 2)
        self.assertEqual(data["error"], "bad_args")

    # --- DONE MEANS 5: text health unchanged ---

    def test_health_ui_text_byte_identical_without_json(self):
        result = self._run("health", "ui")
        self.assertEqual(result.returncode, 0)
        text = result.stdout.decode("utf-8")
        self.assertIn("| SCOPE-1 |", text)
        self.assertIn("| UI-1 |", text)
        self.assertNotIn("| DB-1 |", text)
        # Pin exact bytes against a second run (API-9 / KE-2026-08-31-UPGRADE).
        again = self._run("health", "ui")
        self.assertEqual(result.stdout, again.stdout)
        pinned = text
        self.assertTrue(pinned.startswith("| SCOPE-1 |"))
        self.assertTrue(pinned.rstrip("\n").endswith("| broken screens |"))

    # --- DONE MEANS 6: determinism ---

    def test_json_commands_deterministic_across_hashseeds(self):
        commands = [
            ["health", "ui", "--json"],
            ["health", "--paths", "MainOS.Mac/Sources/MainOSMacCore/Run/**", "docs/notes.md", "--json"],
            ["known-errors", "--json", "--text-file", str(self.repo / "BRIEF.md"), "--full"],
            ["ids", "--check", "UC-001", "JOB-1", "FR-001", "KE-2099-01-01-NOPE", "--json"],
        ]
        for argv in commands:
            with self.subTest(argv=argv):
                _, _, a = self._json(*argv, env={"PYTHONHASHSEED": "0"})
                _, _, b = self._json(*argv, env={"PYTHONHASHSEED": "12345"})
                self.assertEqual(a, b)

    # --- DONE MEANS 7: encoding ---

    def test_json_utf8_with_ascii_locale(self):
        env = {"PYTHONIOENCODING": "ascii", "LC_ALL": "C", "LANG": "C"}
        code, data, raw = self._json(
            "known-errors", "--json", "--text-file", str(self.repo / "BRIEF.md"), "--full",
            env=env,
        )
        self.assertEqual(code, 0)
        self.assertIn("кросс-словарь".encode("utf-8"), raw)
        self.assertIn("полный проход", data["candidates"][0]["do_not_return"])

    # --- DONE MEANS 9: latency ---

    def test_known_errors_latency_150_blocks(self):
        lines = ["# Known errors — generated\n"]
        for i in range(150):
            lines.append(
                f"## KE-2026-03-{(i % 28) + 1:02d}-GEN-{i:04d} — generated block {i} таймер тик карточки\n\n"
                f"Body {i} with словарь and ticks and timers.\n"
                f"- **Status:** open\n"
                f"- **Не возвращать:** полный проход {i} по таймеру.\n\n"
            )
        (self.repo / "docs" / "KNOWN_ERRORS.md").write_text("".join(lines), encoding="utf-8")
        brief = ("подсказка на каждом секундном тике " * 200)[:10240]
        brief_path = self.repo / "BIG_BRIEF.md"
        brief_path.write_text(brief, encoding="utf-8")
        self.assertGreaterEqual(len(brief.encode("utf-8")), 10000)
        started = time.perf_counter()
        code, data, _ = self._json(
            "known-errors", "--json", "--text-file", str(brief_path), "--limit", "10",
            timeout=10.0,
        )
        elapsed = time.perf_counter() - started
        self.assertEqual(code, 0)
        self.assertEqual(data["total"], 150)
        self.assertLess(elapsed, 3.0, f"known-errors took {elapsed:.3f}s")
        # Recorded for the receipt; product budget for the whole gate is ≤ 1 s wall for this call ideally.
        print(f"LATENCY known-errors 150-block: {elapsed:.4f}s", flush=True)

    # --- stemmer examples (decision 4) ---

    def test_ke_lexical_stemmer_required_examples(self):
        nav = _load_nav()
        cases = {
            "тике": "тик", "тику": "тик", "тика": "тик",
            "таймера": "таймер", "таймеру": "таймер",
            "словаря": "словар", "словарь": "словар",
            "минутный": "минутн", "минутного": "минутн",
            "ticks": "tick", "timers": "timer",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                stems = nav.ke_stems(raw)
                self.assertEqual(stems, [expected])
        self.assertLessEqual(len(nav.KE_STOP_WORDS), 80)

    # --- CORRECTION-1: known-errors --areas link mode ---

    def test_known_errors_areas_link_mode(self):
        ke_areas = """# Known errors — areas fixture

## KE-2026-04-01-JOB-CITE — idle scan cites JOB-1 in body

Periodic job re-read everything. Breaks JOB-1 cheap check.

- **Status:** open
- **Не возвращать:** full scan every tick.

## KE-2026-04-02-UI-RULES — background races

Shared mutable UI state.

- **Status:** open
- **Rules:** UI-2
- **Do not return:** mutating UI from a worker.

## KE-2026-04-03-JOB10-ONLY — mentions JOB-10 only (phantom / non-table id)

Text about JOB-10 phantom citation; exact id match required (no prefix).

- **Status:** open
"""
        (self.repo / "docs" / "KNOWN_ERRORS.md").write_text(ke_areas, encoding="utf-8")

        code, data, _ = self._json("known-errors", "--json", "--areas", "job", "--full")
        self.assertEqual(code, 0)
        ids = [c["id"] for c in data["candidates"]]
        self.assertEqual(ids, ["KE-2026-04-01-JOB-CITE"])
        self.assertEqual(data["candidates"][0]["links"], ["JOB-1"])
        self.assertEqual(data["candidates"][0]["score"], 0.0)
        self.assertEqual(data["candidates"][0]["matched"], [])
        self.assertIn("block", data["candidates"][0])

        code, data, _ = self._json("known-errors", "--json", "--areas", "ui,job")
        self.assertEqual(code, 0)
        self.assertEqual(
            [c["id"] for c in data["candidates"]],
            ["KE-2026-04-01-JOB-CITE", "KE-2026-04-02-UI-RULES"],
        )
        self.assertEqual(data["candidates"][1]["links"], ["UI-2"])

        code, data, _ = self._json(
            "known-errors", "--json", "--areas", "job", "--terms", "тик",
        )
        self.assertEqual(code, 2)
        self.assertEqual(data["error"], "bad_args")

        code, data, _ = self._json("known-errors", "--json", "--areas", "notanarea")
        self.assertEqual(code, 2)
        self.assertEqual(data["error"], "bad_args")

        _, _, a = self._json(
            "known-errors", "--json", "--areas", "ui,job",
            env={"PYTHONHASHSEED": "0"},
        )
        _, _, b = self._json(
            "known-errors", "--json", "--areas", "ui,job",
            env={"PYTHONHASHSEED": "12345"},
        )
        self.assertEqual(a, b)

    # --- D1b: fenced examples are not entries (API-4) ---

    def test_fenced_known_error_examples_ignored(self):
        template_ke = (ROOT / "template" / "docs" / "KNOWN_ERRORS.md").read_text(encoding="utf-8")
        # Extract the fenced entry-format example from the live template (a).
        fence_start = template_ke.index("```\n### KE-2026-01-31-EXAMPLE-SLUG")
        fence_end = template_ke.index("```", fence_start + 3) + 3
        fenced_example = template_ke[fence_start:fence_end]
        self.assertIn("KE-2026-01-31-EXAMPLE-SLUG", fenced_example)

        ke_fence = f"""# Known errors — fence fixture

## Entry format

{fenced_example}

## Open

### KE-2026-05-04-REAL-WITH-SNIPPET — real entry whose body holds a fenced fake heading

Body before the snippet.

```
### KE-2026-05-05-INSIDE-FENCE — not an entry
**Status:** decoy
```

Body after the snippet.

- **Status:** open
- **Не возвращать:** keep the fenced decoy inside the real block.

### KE-2026-05-06-JOB-CITE — cites JOB-1 for area delivery

Periodic job re-read. Breaks JOB-1 cheap check.

- **Status:** open
- **Rules:** JOB-1
"""
        (self.repo / "docs" / "KNOWN_ERRORS.md").write_text(ke_fence, encoding="utf-8")

        code, data, _ = self._json(
            "ids", "--check",
            "KE-2026-01-31-EXAMPLE-SLUG", "KE-2026-05-05-INSIDE-FENCE",
            "KE-2026-05-04-REAL-WITH-SNIPPET", "KE-2026-05-06-JOB-CITE",
            "--json",
        )
        self.assertEqual(code, 0)
        self.assertEqual(
            data["unknown"],
            ["KE-2026-01-31-EXAMPLE-SLUG", "KE-2026-05-05-INSIDE-FENCE"],
        )
        self.assertEqual(
            [k["id"] for k in data["known"]],
            ["KE-2026-05-04-REAL-WITH-SNIPPET", "KE-2026-05-06-JOB-CITE"],
        )

        code, data, _ = self._json(
            "known-errors", "--json", "--ids",
            "KE-2026-05-04-REAL-WITH-SNIPPET",
            "--full",
        )
        self.assertEqual(code, 0)
        self.assertEqual(data["total"], 2)
        self.assertEqual(len(data["candidates"]), 1)
        block = data["candidates"][0]["block"]
        self.assertIn("### KE-2026-05-05-INSIDE-FENCE — not an entry", block)
        self.assertIn("```", block)

        code, data, _ = self._json("known-errors", "--json", "--areas", "job", "--full")
        self.assertEqual(code, 0)
        self.assertEqual([c["id"] for c in data["candidates"]], ["KE-2026-05-06-JOB-CITE"])
        self.assertEqual(data["candidates"][0]["links"], ["JOB-1"])
        self.assertNotIn("KE-2026-01-31-EXAMPLE-SLUG", [c["id"] for c in data["candidates"]])

    def test_install_template_example_slug_unknown(self):
        target = self.base / "installed"
        # cost: one install.py + one ids --check per suite run
        install = subprocess.run(
            [
                sys.executable, "-B", str(ROOT / "scripts" / "install.py"),
                "--target", str(target),
                "--name", "FenceInstall",
                "--kind", "tool",
                "--no-devlog",
            ],
            capture_output=True,
            timeout=60.0,
            cwd=str(ROOT),
        )
        self.assertEqual(
            install.returncode, 0,
            f"install failed: {install.stdout!r} {install.stderr!r}",
        )
        nav = target / ".devframework" / "navigate.py"
        result = subprocess.run(
            [
                sys.executable, "-B", str(nav),
                "--root", str(target),
                "ids", "--check", "KE-2026-01-31-EXAMPLE-SLUG", "--json",
            ],
            capture_output=True,
            timeout=30.0,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))
        data = json.loads(result.stdout.decode("utf-8"))
        self.assertEqual(data["unknown"], ["KE-2026-01-31-EXAMPLE-SLUG"])
        self.assertEqual(data["known"], [])

    # --- D2: bold list-item FR/NFR definitions in REQUIREMENTS.md ---

    # Pre-change / table-only ids --check JSON (API-9); pin expected bytes.
    TABLE_ONLY_IDS_JSON = (
        '{"framework_version":"' + VERSION + '",'
        '"known":[{"id":"FR-001","kind":"requirement"},'
        '{"id":"NFR-001","kind":"requirement"}],'
        '"schema":"devframework.ids/1",'
        '"unknown":["FR-999"]}\n'
    ).encode("utf-8")

    def test_req_bold_bullet_definitions_recognised(self):
        req = """# Requirements

- **FR-303:** Таймеры привязаны к рабочему дню
* **NFR-010**: Deterministic gates stay machine-checkable
- **FR-505** — bulk path stays bounded
- **FR-506** something about retries
- **FR-909**-linked note

A prose mention of **FR-303:** must not redefine it.

  - **FR-777:** indented list item is not a definition

```
- **FR-888:** fenced bullet is not a definition
```
"""
        (self.repo / "docs" / "REQUIREMENTS.md").write_text(req, encoding="utf-8")
        code, data, _ = self._json(
            "ids", "--check",
            "FR-303", "NFR-010", "FR-505", "FR-506", "FR-909",
            "FR-777", "FR-888", "FR-30",
            "--json",
        )
        self.assertEqual(code, 0)
        self.assertEqual(
            [(k["id"], k["kind"]) for k in data["known"]],
            [
                ("FR-303", "requirement"),
                ("NFR-010", "requirement"),
                ("FR-505", "requirement"),
                ("FR-506", "requirement"),
                ("FR-909", "requirement"),
            ],
        )
        # KE-2026-09-03-USECASE-PADDED-RANGE: FR-30 ≠ FR-303; indented/fenced ignored.
        self.assertEqual(data["unknown"], ["FR-777", "FR-888", "FR-30"])

    def test_req_table_only_ids_json_byte_identical(self):
        # Table-only fixture: bullet grammar must not change table-row JSON (API-9).
        (self.repo / "docs" / "REQUIREMENTS.md").write_text(REQUIREMENTS, encoding="utf-8")
        result = self._run("ids", "--check", "FR-001", "NFR-001", "FR-999", "--json")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, self.TABLE_ONLY_IDS_JSON)


if __name__ == "__main__":
    unittest.main()
