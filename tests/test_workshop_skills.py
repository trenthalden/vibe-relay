"""Agent Workshop skill pack: frontmatter parse + INDEX coverage."""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

import importlib.util
import sys

_spec = importlib.util.spec_from_file_location(
    "vibe_relay_plugin", ROOT / "__init__.py"
)
_mod = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
sys.modules["vibe_relay_plugin"] = _mod
_spec.loader.exec_module(_mod)
WORKSHOP_SKILL_NAMES = _mod.WORKSHOP_SKILL_NAMES

REQUIRED_FRONTMATTER = ("name", "description", "version", "author")
DESC_MAX = 60
DESC_PREFIX = "Use when"


def _parse_frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---"):
        raise AssertionError("SKILL.md must start with YAML frontmatter ---")
    end = text.find("\n---", 3)
    if end < 0:
        raise AssertionError("SKILL.md frontmatter not closed")
    block = text[3:end].strip()
    fields: dict[str, str] = {}
    for line in block.splitlines():
        if not line.strip() or line.strip().startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, raw = line.partition(":")
        val = raw.strip().strip('"').strip("'")
        fields[key.strip()] = val
    return fields


class WorkshopSkillFrontmatterTests(unittest.TestCase):
    def test_every_registered_workshop_skill_parses(self):
        skills_dir = ROOT / "skills"
        self.assertEqual(
            len(WORKSHOP_SKILL_NAMES),
            14,
            "workshop pack target is 10–14 focused skills",
        )
        for name in WORKSHOP_SKILL_NAMES:
            path = skills_dir / name / "SKILL.md"
            with self.subTest(skill=name):
                self.assertTrue(path.is_file(), f"missing {path}")
                fields = _parse_frontmatter(path.read_text(encoding="utf-8"))
                for key in REQUIRED_FRONTMATTER:
                    self.assertIn(key, fields, f"{name}: missing frontmatter field {key}")
                    self.assertTrue(fields[key], f"{name}: empty {key}")
                self.assertEqual(fields["name"], name)
                self.assertEqual(fields["version"], "1.0.0")
                self.assertEqual(fields["author"], "trenthalden")
                self.assertNotIn("license", fields)
                desc = fields["description"]
                self.assertTrue(
                    desc.startswith(DESC_PREFIX),
                    f"{name}: description must start with {DESC_PREFIX!r} (got {desc!r})",
                )
                self.assertLessEqual(
                    len(desc),
                    DESC_MAX,
                    f"{name}: description length {len(desc)} > {DESC_MAX}: {desc!r}",
                )

    def test_index_lists_every_workshop_skill(self):
        index = (ROOT / "skills" / "INDEX.md").read_text(encoding="utf-8")
        for name in WORKSHOP_SKILL_NAMES:
            self.assertIn(f"`{name}`", index)


if __name__ == "__main__":
    unittest.main()
