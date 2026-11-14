"""Public Hermes import boundary and removed account-routing surface."""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE_PACKAGES = {
    "agent", "hermes_cli", "gateway", "tools", "plugins", "cron",
    "hermes_constants", "hermes_state", "run_agent", "model_tools",
    "utils", "toolsets",
}
ALLOWED_IMPORTS = {
    ("agent.delegation_provider", "DelegationProvider"),
    ("agent.delegation_provider", "DelegationRouteError"),
    ("agent.account_usage", "fetch_account_usage"),
    ("agent.runtime_cwd", "resolve_agent_cwd"),
    ("gateway.session_context", "get_session_env"),
    ("plugins.plugin_storage", "plugin_data_dir"),
    ("hermes_cli.config", "load_config_readonly"),
    # Documented profile-safe path helper (developer-guide/contributing.md "Profile-safe paths");
    # the vendored DEV Framework uses it to refuse installing into the Hermes home.
    ("hermes_constants", "get_hermes_home"),
}
FORBIDDEN_TERMS = (
    "account_route", "account_routing", "credential_pool", "runtime_api_key",
    "hermes_cli.profiles", "delegate_tool_routes",
)


def plugin_sources() -> list[Path]:
    sources = list(ROOT.glob("*.py"))
    for directory in (ROOT / "scripts", ROOT / "vendor"):
        if directory.exists():
            sources.extend(directory.rglob("*.py"))
    return sorted(path for path in sources if "tests" not in path.relative_to(ROOT).parts)


class PublicSurfaceTests(unittest.TestCase):
    def test_core_imports_are_allowlisted(self):
        failures = []
        for path in plugin_sources():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.split(".", 1)[0] in CORE_PACKAGES:
                            failures.append(f"{path.relative_to(ROOT)}:{node.lineno}: import {alias.name}")
                elif isinstance(node, ast.ImportFrom) and node.module:
                    top = node.module.split(".", 1)[0]
                    if top not in CORE_PACKAGES:
                        continue
                    for alias in node.names:
                        if (node.module, alias.name) in ALLOWED_IMPORTS:
                            continue
                        failures.append(
                            f"{path.relative_to(ROOT)}:{node.lineno}: from {node.module} import {alias.name}"
                        )
        self.assertEqual(failures, [], "undocumented Hermes core imports:\n" + "\n".join(failures))

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
