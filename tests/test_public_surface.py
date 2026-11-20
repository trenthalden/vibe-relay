"""Public Hermes import boundary and removed account-routing surface."""
from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE_PACKAGES = {
    "agent", "hermes_cli", "gateway", "tools", "plugins", "cron",
    "hermes_constants", "hermes_state", "run_agent", "model_tools",
    "utils", "toolsets",
}
# Only hermes_compat.py may import Hermes internals; other modules use the adapter
# or PluginContext. Vendored trees are excluded from this gate.
FORBIDDEN_TERMS = (
    "account_route", "account_routing", "credential_pool", "runtime_api_key",
    "hermes_cli.profiles", "delegate_tool_routes",
)
# BRIEF V2: no direct host imports outside hermes_compat (excl. vendor/, tests/).
_DIRECT_HOST_IMPORT = re.compile(
    r"^\s*(from|import) (hermes_cli|agent|gateway|tools|model_tools|run_agent)\b",
    re.MULTILINE,
)


def plugin_sources() -> list[Path]:
    sources = list(ROOT.glob("*.py"))
    for directory in (ROOT / "scripts", ROOT / "vendor"):
        if directory.exists():
            sources.extend(directory.rglob("*.py"))
    return sorted(path for path in sources if "tests" not in path.relative_to(ROOT).parts)


def _compat_sources() -> list[Path]:
    """Product package for the direct-import grep (excl. vendor/, tests/, hermes_compat).

    ``scripts/df_local_patches/`` is excluded too: it is the overlay source of the vendored
    DEV Framework ``__init__.py`` (byte-identical to ``vendor/dev_framework``), i.e. DEV
    Framework code governed by its own pin, not vibe-relay code. Do not disguise imports there
    to satisfy this grep.
    """
    out: list[Path] = []
    for path in ROOT.rglob("*.py"):
        rel = path.relative_to(ROOT)
        parts = rel.parts
        if "vendor" in parts or "tests" in parts:
            continue
        if parts[:2] == ("scripts", "df_local_patches"):
            continue
        if path.name == "hermes_compat.py" and len(parts) == 1:
            continue
        out.append(path)
    return sorted(out)


class PublicSurfaceTests(unittest.TestCase):
    def test_core_imports_only_in_hermes_compat(self):
        failures = []
        for path in plugin_sources():
            rel = path.relative_to(ROOT)
            if rel.parts[0] == "vendor":
                continue
            if rel.parts[:2] == ("scripts", "df_local_patches"):
                continue  # vendored DEV Framework overlay source (see _compat_sources)
            if path.name == "hermes_compat.py" and len(rel.parts) == 1:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.split(".", 1)[0] in CORE_PACKAGES:
                            failures.append(f"{rel}:{node.lineno}: import {alias.name}")
                elif isinstance(node, ast.ImportFrom) and node.module:
                    top = node.module.split(".", 1)[0]
                    if top in CORE_PACKAGES:
                        failures.append(
                            f"{rel}:{node.lineno}: from {node.module} import …"
                        )
        self.assertEqual(
            failures, [],
            "Hermes core imports must live in hermes_compat.py only:\n" + "\n".join(failures),
        )

    def test_no_direct_host_imports_outside_compat(self):
        """BRIEF: grep package (excl. vendor/, tests/, hermes_compat.py) → none."""
        failures = []
        for path in _compat_sources():
            text = path.read_text(encoding="utf-8")
            for match in _DIRECT_HOST_IMPORT.finditer(text):
                line_no = text.count("\n", 0, match.start()) + 1
                failures.append(
                    f"{path.relative_to(ROOT)}:{line_no}: {match.group(0).strip()}"
                )
        self.assertEqual(
            failures, [],
            "direct Hermes host imports outside hermes_compat:\n" + "\n".join(failures),
        )

    def test_account_routing_surface_is_absent(self):
        failures = []
        for path in plugin_sources():
            text = path.read_text(encoding="utf-8")
            for term in FORBIDDEN_TERMS:
                if term in text:
                    failures.append(f"{path.relative_to(ROOT)} contains {term}")
        self.assertEqual(failures, [], "removed account-routing terms found:\n" + "\n".join(failures))


if __name__ == "__main__":
    unittest.main()
