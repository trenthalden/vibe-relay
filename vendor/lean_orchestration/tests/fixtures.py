"""Fixture project builders — always under TMPDIR (KE-2026-11-20-TMPDIR-NESTED-PROJECT)."""
from __future__ import annotations

import json
import os
import tempfile
from typing import Any, Dict, List, Optional

from tests.stub_navigate import STUB_NAVIGATE_SOURCE

JOB1 = {
    "area": "JOB",
    "id": "JOB-1",
    "prevents": "idle cost burn",
    "rule": "A periodic job starts with a cheap deterministic check; the expensive step runs only when needed.",
}
UI1 = {
    "area": "UI",
    "id": "UI-1",
    "prevents": "frozen app",
    "rule": "The UI never blocks on I/O: long work runs in the background with visible progress and cancel.",
}
BULK2 = {
    "area": "BULK",
    "id": "BULK-2",
    "prevents": "O(n²) slowdown",
    "rule": "Per-item work never rescans the whole set: batch lookups, use maps and indexes.",
}
SCOPE1 = {
    "area": "SCOPE",
    "id": "SCOPE-1",
    "prevents": "over-engineering",
    "rule": "Build only what the task names; every new layer needs a present requirement.",
}

# Area-linked (JOB-1) — delivered as a full block under lo-delivery/2.
TOP_KE = {
    "id": "KE-2026-09-23-MIRROR-FULL-SCAN",
    "title": "per-minute full-scan froze the window",
    "status": "fixed in example",
    "do_not_return": "full dictionary scan on a timer tick",
    "score": 12.3456,
    "matched": ["timer", "tick"],
    "links": ["JOB-1"],
    "block": (
        "### KE-2026-09-23-MIRROR-FULL-SCAN: per-minute full-scan froze the window\n\n"
        "**Do not return:** full dictionary scan on a timer tick.\n"
        "**Rules:** JOB-1\n"
        "**Status:** fixed in example\n"
    ),
}

# Lexical-only candidate — titles section only (not area-linked).
LEXICAL_ONLY_KE = {
    "id": "KE-2026-01-01-UNRELATED-LEXICAL",
    "title": "unrelated lexical candidate about common project words",
    "status": "",
    "do_not_return": "",
    "score": 99.0,
    "matched": ["project", "common"],
    "links": [],
    "block": (
        "### KE-2026-01-01-UNRELATED-LEXICAL: unrelated lexical candidate about common project words\n\n"
        "This block must not appear as a full delivery when only lexical.\n"
    ),
}

RUN_PATH = "MainOS.Mac/Sources/MainOSMacCore/Run/**"


def make_fixture(
    *,
    reject_json: bool = False,
    path_areas: Optional[Dict[str, List[str]]] = None,
    rows: Optional[List[Dict[str, Any]]] = None,
    known_errors: Optional[List[Dict[str, Any]]] = None,
    candidates: Optional[List[Dict[str, Any]]] = None,
    known_ids: Optional[List[str]] = None,
    framework_version: str = "1.7.0",
    extra_kes: int = 0,
) -> str:
    """Create a fixture project under TMPDIR; return its absolute path."""
    root = tempfile.mkdtemp(prefix="lo-fix-")
    df = os.path.join(root, ".devframework")
    stub_dir = os.path.join(root, ".lean-orchestration-stub")
    os.makedirs(df, exist_ok=True)
    os.makedirs(stub_dir, exist_ok=True)

    rows = list(rows or [SCOPE1, UI1, JOB1, BULK2])
    known_errors = list(known_errors or [TOP_KE, LEXICAL_ONLY_KE])
    for i in range(extra_kes):
        kid = "KE-2026-01-01-EXTRA-%03d" % i
        known_errors.append(
            {
                "id": kid,
                "title": "extra %d" % i,
                "status": "",
                "do_not_return": "",
                "score": 1.0,
                "matched": [],
                "links": [],
                "block": "### %s: extra %d\n\nbody %d\n" % (kid, i, i),
            }
        )
    # Lexical rank: unrelated first, then linked TOP_KE (field-probe shape).
    if candidates is None:
        candidates = [LEXICAL_ONLY_KE, TOP_KE] + [
            k for k in known_errors if k["id"] not in (LEXICAL_ONLY_KE["id"], TOP_KE["id"])
        ]
    else:
        candidates = list(candidates)
    path_areas = path_areas or {RUN_PATH: ["UI", "JOB", "BULK"]}
    known_ids = list(
        known_ids
        or (
            [r["id"] for r in rows]
            + [k["id"] for k in known_errors]
            + ["FR-001", "NFR-001", "UC-001"]
        )
    )

    data = {
        "reject_json": reject_json,
        "framework_version": framework_version,
        "path_areas": path_areas,
        "globs": {RUN_PATH: ["MainOS.Mac/**/Run/**"]},
        "default_areas": ["UI", "JOB", "BULK"],
        "rows": rows,
        "known_errors": known_errors,
        "candidates": candidates,
        "known_ids": known_ids,
    }
    with open(os.path.join(stub_dir, "data.json"), "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=True)

    navigate = os.path.join(df, "navigate.py")
    with open(navigate, "w", encoding="utf-8") as fh:
        fh.write(STUB_NAVIGATE_SOURCE)
    os.chmod(navigate, 0o755)
    return root


def write_brief(path: str, text: str) -> str:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def base_brief(
    *,
    known_errors: str = "KE-2026-09-23-MIRROR-FULL-SCAN — relevant prior defect.",
    architecture_rules: str = "UI-1, JOB-1, BULK-2 apply.",
    allowed: str = "`MainOS.Mac/Sources/MainOSMacCore/Run/**`",
    gate: str = "python3 -B .devframework/check.py finish",
    intent: str = "Fix the due-hint timer.",
    extra_sections: str = "",
) -> str:
    return (
        "# BRIEF\n\n"
        "## INTENT\n\n%s\n\n"
        "## ALLOWED\n\n%s\n\n"
        "## FORBIDDEN\n\n- other paths\n\n"
        "## DONE MEANS\n\n1. Works.\n\n"
        "## GATE\n\n%s\n\n"
        "## Known errors\n\n%s\n\n"
        "## Architecture rules\n\n%s\n\n"
        "%s"
    ) % (intent, allowed, gate, known_errors, architecture_rules, extra_sections)
