"""Agent Workshop skill pack: frontmatter parse, INDEX coverage, links and public-text hygiene."""
from __future__ import annotations

import re
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
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
# Machine-specific markers that must never ship in the public skill pack.
FORBIDDEN_TEXT = re.compile(r"/Users/|/home/[a-z]|[\w.+-]+@[\w-]+\.[a-z]{2,}", re.IGNORECASE)
CYRILLIC = re.compile(r"[\u0400-\u04FF]")
BYPASS_FLAG = re.compile(r"(?:^|\s)--(?:force|yolo|dangerously[-\w]*)\b")


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


def _skill_markdown_files() -> list[Path]:
    files: list[Path] = []
    for name in WORKSHOP_SKILL_NAMES:
        files.extend(sorted((ROOT / "skills" / name).rglob("*.md")))
    return files


def _fenced_blocks(text: str) -> list[str]:
    return re.findall(r"```[^\n]*\n(.*?)```", text, flags=re.DOTALL)


class WorkshopSkillFrontmatterTests(unittest.TestCase):
    def test_every_registered_workshop_skill_parses(self):
        skills_dir = ROOT / "skills"
        self.assertEqual(
            len(WORKSHOP_SKILL_NAMES),
            15,
            "workshop pack: 14 focused skills + night-shift",
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
                self.assertRegex(fields["version"], SEMVER)
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

    def test_referenced_files_exist(self):
        for name in WORKSHOP_SKILL_NAMES:
            skill_dir = ROOT / "skills" / name
            text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
            for ref in sorted(set(re.findall(r"`(references/[\w./-]+\.md)`", text))):
                with self.subTest(skill=name, ref=ref):
                    self.assertTrue((skill_dir / ref).is_file(), f"{name}: missing {ref}")

    def test_public_skill_text_has_no_private_markers(self):
        for path in _skill_markdown_files():
            text = path.read_text(encoding="utf-8")
            with self.subTest(file=str(path.relative_to(ROOT))):
                self.assertIsNone(FORBIDDEN_TEXT.search(text), FORBIDDEN_TEXT.search(text))
                self.assertIsNone(CYRILLIC.search(text), "public skills are English-only")

    def test_no_approval_bypass_recipes_in_code_blocks(self):
        for path in _skill_markdown_files():
            for block in _fenced_blocks(path.read_text(encoding="utf-8")):
                with self.subTest(file=str(path.relative_to(ROOT))):
                    self.assertIsNone(BYPASS_FLAG.search(block), block[:200])


if __name__ == "__main__":
    unittest.main()
