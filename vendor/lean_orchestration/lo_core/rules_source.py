"""Rules source — navigate.py --json, fallback rules.json, or none (FR-007).

Shapes come from docs/CONTRACT-navigate-json.md (SCOPE-4); this module does not redefine them.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from lo_core.config import MIN_FRAMEWORK_VERSION

# cost: ≤ 5 navigate.py subprocesses per gate run × 5,000 runs/day → ≤ 25,000 local calls/day
# (health, known-errors lexical, known-errors --areas, known-errors --ids, ids --check).

# Areas recognised for rule-id citation parsing (ARCHITECTURE_HEALTHCHECK catalogue).
STANDARD_AREAS: List[str] = [
    "SCOPE", "API", "UI", "JOB", "BULK", "DB", "TX", "CFG", "VER", "SEC", "DEP", "LLM",
]

_RULES_LINE = re.compile(
    r"^\*\*(?:Rules|Areas):\*\*\s*(.+)$",
    re.IGNORECASE | re.MULTILINE,
)
_RULE_ID_TOKEN = re.compile(r"\b([A-Z]+)-\d+\b")


class RulesSourceError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def detect_kind(repo: str) -> str:
    navigate = os.path.join(repo, ".devframework", "navigate.py")
    if os.path.isfile(navigate):
        return "navigate"
    fallback = os.path.join(repo, ".lean-orchestration", "rules.json")
    if os.path.isfile(fallback):
        return "rules_json"
    return "none"


def parse_framework_version(text: str) -> Optional[Tuple[int, ...]]:
    parts: List[int] = []
    for piece in str(text).strip().split("."):
        if not piece.isdigit():
            return None
        parts.append(int(piece))
    return tuple(parts) if parts else None


def version_at_least(version: str, minimum: Tuple[int, ...] = MIN_FRAMEWORK_VERSION) -> bool:
    parsed = parse_framework_version(version)
    if parsed is None:
        return False
    length = max(len(parsed), len(minimum))
    left = parsed + (0,) * (length - len(parsed))
    right = minimum + (0,) * (length - len(minimum))
    return left >= right


def load_rules(
    repo: str,
    *,
    brief_text: str,
    allowed: Sequence[str],
    cited_ke: Sequence[str],
    timeout_s: float,
    known_errors_limit: int,
) -> Dict[str, Any]:
    """Normalized rules payload. navigate calls here ≤ 4; ids --check is separate (≤ 5 total)."""
    kind = detect_kind(repo)
    if kind == "navigate":
        return _from_navigate(
            repo,
            brief_text=brief_text,
            allowed=allowed,
            cited_ke=cited_ke,
            timeout_s=timeout_s,
            known_errors_limit=known_errors_limit,
        )
    if kind == "rules_json":
        return _from_rules_json(repo, allowed=allowed, cited_ke=cited_ke)
    return {
        "kind": "none",
        "framework_version": "",
        "areas": [],
        "unmapped": list(allowed),
        "rows": [],
        "candidates": [],
        "cited_blocks": [],
        "area_linked_blocks": [],
        "algorithms": {},
        "raw_jsons": [],
        "area_names": list(STANDARD_AREAS),
    }


def check_ids(repo: str, ids: Sequence[str], *, timeout_s: float) -> Dict[str, Any]:
    """Run ``ids --check`` (one call). Returns known/unknown lists."""
    if not ids:
        return {"known": [], "unknown": [], "raw": ""}
    kind = detect_kind(repo)
    if kind == "navigate":
        navigate = os.path.join(repo, ".devframework", "navigate.py")
        resp = _run_navigate(repo, navigate, ["ids", "--check"] + list(ids), timeout_s)
        obj = resp["obj"]
        return {
            "known": list(obj.get("known") or []),
            "unknown": [str(u) for u in obj.get("unknown") or []],
            "raw": resp["raw"],
            "framework_version": str(obj.get("framework_version") or ""),
        }
    if kind == "rules_json":
        return _ids_from_rules_json(repo, ids)
    return {"known": [], "unknown": list(ids), "raw": ""}


def _from_navigate(
    repo: str,
    *,
    brief_text: str,
    allowed: Sequence[str],
    cited_ke: Sequence[str],
    timeout_s: float,
    known_errors_limit: int,
) -> Dict[str, Any]:
    navigate = os.path.join(repo, ".devframework", "navigate.py")
    raw_jsons: List[str] = []

    # Call 1: health --paths <ALLOWED> (or health when ALLOWED empty).
    if allowed:
        health_args: List[str] = ["health", "--paths"] + list(allowed)
    else:
        health_args = ["health"]
    health = _run_navigate(repo, navigate, health_args, timeout_s)
    raw_jsons.append(health["raw"])
    health_obj = health["obj"]
    framework_version = str(health_obj.get("framework_version") or "")
    if not version_at_least(framework_version):
        raise RulesSourceError(
            "framework_too_old",
            "DEV Framework %s is below 1.7.0; upgrade for --json support"
            % (framework_version or "unknown"),
        )

    areas = [str(a).upper() for a in health_obj.get("areas") or []]
    unmapped = [str(u) for u in health_obj.get("unmapped") or []]
    rows = list(health_obj.get("rows") or [])
    area_names = sorted(set(STANDARD_AREAS + areas + [str(r.get("area", "")).upper() for r in rows if r.get("area")]))

    required_non_scope = sorted({a for a in areas if a != "SCOPE"})

    # Call 2: lexical candidates — titles only (no --full). KE-2026-11-20-BRIEF-RULES-SKIPPED.
    brief_path = _write_brief_copy(brief_text)
    try:
        ke_args: List[str] = ["known-errors", "--text-file", brief_path]
        if allowed:
            ke_args.extend(["--paths"] + list(allowed))
        ke_args.extend(["--limit", str(int(known_errors_limit))])
        candidates_resp = _run_navigate(repo, navigate, ke_args, timeout_s)
        raw_jsons.append(candidates_resp["raw"])
        candidates = list(candidates_resp["obj"].get("candidates") or [])
        algorithms = {"known_errors": candidates_resp["obj"].get("algorithm") or ""}
    finally:
        try:
            os.remove(brief_path)
        except OSError:
            pass

    # Call 3 (optional): area-linked full blocks. Skip when only SCOPE is required.
    area_linked_blocks: List[Dict[str, Any]] = []
    if required_non_scope:
        areas_arg = ",".join(required_non_scope)
        linked_resp = _run_navigate(
            repo,
            navigate,
            ["known-errors", "--areas", areas_arg, "--full"],
            timeout_s,
        )
        raw_jsons.append(linked_resp["raw"])
        area_linked_blocks = list(linked_resp["obj"].get("candidates") or [])

    # Call 4 (optional): known-errors --ids for cited KE blocks.
    cited_blocks: List[Dict[str, Any]] = []
    if cited_ke:
        cited_resp = _run_navigate(
            repo,
            navigate,
            ["known-errors", "--ids"] + list(cited_ke) + ["--full"],
            timeout_s,
        )
        raw_jsons.append(cited_resp["raw"])
        cited_blocks = list(cited_resp["obj"].get("candidates") or [])

    return {
        "kind": "navigate",
        "framework_version": framework_version,
        "areas": areas,
        "unmapped": unmapped,
        "rows": rows,
        "candidates": candidates,
        "cited_blocks": cited_blocks,
        "area_linked_blocks": area_linked_blocks,
        "algorithms": algorithms,
        "raw_jsons": raw_jsons,
        "area_names": area_names,
    }


def _from_rules_json(
    repo: str,
    *,
    allowed: Sequence[str],
    cited_ke: Sequence[str],
) -> Dict[str, Any]:
    path = os.path.join(repo, ".lean-orchestration", "rules.json")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = fh.read()
    except OSError as exc:
        raise RulesSourceError("rules_json_unreadable", "cannot read %s: %s" % (path, exc)) from exc
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RulesSourceError("rules_json_invalid", "invalid JSON in %s: %s" % (path, exc)) from exc
    if not isinstance(obj, dict) or obj.get("schema") != "lean-orchestration.rules/1":
        raise RulesSourceError("rules_json_invalid", "rules.json must use schema lean-orchestration.rules/1")

    area_map = obj.get("areas")
    if area_map is None:
        area_map = []
    if not isinstance(area_map, list):
        raise RulesSourceError("bad_config", "rules.json areas map is malformed")

    matched_areas, unmapped, matched_detail = match_paths_to_areas(list(allowed), area_map)
    areas = sorted(set(["SCOPE"] + matched_areas))
    all_rows = [r for r in (obj.get("rows") or []) if isinstance(r, dict)]
    area_set = set(areas)
    rows = [r for r in all_rows if str(r.get("area", "")).upper() in area_set]

    known_errors = [k for k in (obj.get("known_errors") or []) if isinstance(k, dict)]
    by_id = {str(k.get("id") or ""): k for k in known_errors if k.get("id")}

    # Contract §3 --areas link rule on fallback known_errors (lo-delivery/2).
    required_non_scope = [a for a in areas if a != "SCOPE"]
    health_rule_ids = {
        str(r.get("id") or "") for r in all_rows if r.get("id")
    }
    area_linked_blocks: List[Dict[str, Any]] = []
    linked_ids: List[str] = []
    for item in known_errors:
        kid = str(item.get("id") or "")
        links = linked_tokens_for_ke(item, required_non_scope, health_rule_ids)
        if not links:
            continue
        entry = _ke_item(item, score=0.0)
        entry["links"] = links
        area_linked_blocks.append(entry)
        linked_ids.append(kid)

    cited_blocks: List[Dict[str, Any]] = []
    for kid in cited_ke:
        if kid in by_id:
            cited_blocks.append(_ke_item(by_id[kid], score=0.0))

    # Non-linked, non-cited → titles only (no lexical rank in v0.1).
    delivered_full = set(linked_ids) | set(cited_ke)
    candidates: List[Dict[str, Any]] = []
    for item in known_errors:
        kid = str(item.get("id") or "")
        if kid and kid not in delivered_full:
            candidates.append(_ke_item(item, score=0.0))

    area_names = sorted(
        set(
            STANDARD_AREAS
            + [str(r.get("area", "")).upper() for r in all_rows if r.get("area")]
            + [str(a).upper() for entry in area_map for a in (entry.get("areas") or [])]
        )
    )
    return {
        "kind": "rules_json",
        "framework_version": "",
        "areas": areas,
        "unmapped": unmapped,
        "rows": rows,
        "candidates": candidates,
        "cited_blocks": cited_blocks,
        "area_linked_blocks": area_linked_blocks,
        "algorithms": {"known_errors": "rules-json-link/2"},
        "raw_jsons": [json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))],
        "area_names": area_names,
        "matched": matched_detail,
    }


def linked_tokens_for_ke(
    item: Mapping[str, Any],
    areas: Sequence[str],
    health_rule_ids: set,
) -> List[str]:
    """Contract §3: linked when block cites a health rule id of area A, or Rules/Areas line."""
    if not areas:
        return []
    area_set = {a.upper() for a in areas}
    block = str(item.get("block") or "")
    title = str(item.get("title") or "")
    text = block if block else title
    found: List[str] = []

    # Explicit **Rules:** / **Areas:** line tokens.
    for match in _RULES_LINE.finditer(text):
        for token in re.split(r"[,;\s]+", match.group(1).strip()):
            token = token.strip().rstrip(".")
            if not token:
                continue
            upper = token.upper()
            if upper in area_set and upper not in found:
                found.append(upper)
            elif token in health_rule_ids:
                area = token.split("-", 1)[0].upper()
                if area in area_set and token not in found:
                    found.append(token)

    # Any health rule id of a required area cited anywhere in the block.
    for match in _RULE_ID_TOKEN.finditer(text):
        rid = match.group(0)
        area = match.group(1).upper()
        if rid in health_rule_ids and area in area_set and rid not in found:
            found.append(rid)

    # Fixture shortcut: optional precomputed links list (must still match areas).
    for link in item.get("links") or []:
        link_s = str(link)
        upper = link_s.upper()
        if upper in area_set and upper not in found:
            found.append(upper)
        elif link_s in health_rule_ids:
            area = link_s.split("-", 1)[0].upper()
            if area in area_set and link_s not in found:
                found.append(link_s)

    return sorted(found)


def _ke_item(item: Mapping[str, Any], *, score: float) -> Dict[str, Any]:
    return {
        "id": str(item.get("id") or ""),
        "title": str(item.get("title") or ""),
        "status": str(item.get("status") or ""),
        "do_not_return": str(item.get("do_not_return") or ""),
        "block": str(item.get("block") or ""),
        "score": round(float(score), 4),
        "matched": [],
    }


def _ids_from_rules_json(repo: str, ids: Sequence[str]) -> Dict[str, Any]:
    path = os.path.join(repo, ".lean-orchestration", "rules.json")
    with open(path, "r", encoding="utf-8") as fh:
        obj = json.load(fh)
    known_ids = set()
    for row in obj.get("rows") or []:
        if isinstance(row, dict) and row.get("id"):
            known_ids.add(str(row["id"]))
    for item in obj.get("known_errors") or []:
        if isinstance(item, dict) and item.get("id"):
            known_ids.add(str(item["id"]))
    known = []
    unknown = []
    seen = set()
    for item in ids:
        if item in seen:
            continue
        seen.add(item)
        if item in known_ids:
            kind = "known_error" if item.startswith("KE-") else "health_rule"
            known.append({"id": item, "kind": kind})
        else:
            unknown.append(item)
    return {"known": known, "unknown": unknown, "raw": ""}


def match_paths_to_areas(
    paths: Sequence[str],
    area_map: Sequence[Mapping[str, Any]],
) -> Tuple[List[str], List[str], List[Dict[str, Any]]]:
    """Contract §2 matching — deterministic, OS-independent."""
    matched_detail: List[Dict[str, Any]] = []
    unmapped: List[str] = []
    all_areas: List[str] = []
    seen_paths = set()
    ordered_paths: List[str] = []
    for path in paths:
        if path in seen_paths:
            continue
        seen_paths.add(path)
        ordered_paths.append(path)

    for path in ordered_paths:
        hit_areas: List[str] = []
        hit_globs: List[str] = []
        probe = wildcard_to_probe(normalize_path(path))
        for entry in area_map:
            glob = str(entry.get("glob") or "")
            if not glob:
                continue
            if path_matches_glob(probe, glob):
                for area in entry.get("areas") or []:
                    area_u = str(area).upper()
                    if area_u not in hit_areas:
                        hit_areas.append(area_u)
                if glob not in hit_globs:
                    hit_globs.append(glob)
        hit_areas_sorted = sorted(hit_areas)
        if hit_areas_sorted:
            matched_detail.append({"path": path, "areas": hit_areas_sorted, "globs": hit_globs})
            for area in hit_areas_sorted:
                if area not in all_areas:
                    all_areas.append(area)
        else:
            unmapped.append(path)
    return sorted(all_areas), unmapped, matched_detail


def normalize_path(path: str) -> str:
    text = path.replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    while "//" in text:
        text = text.replace("//", "/")
    return text


def wildcard_to_probe(path: str) -> str:
    return re.sub(r"\*\*?|\?", "x", path)


def path_matches_glob(path: str, glob: str) -> bool:
    return bool(glob_to_regex(normalize_path(glob)).fullmatch(path))


def glob_to_regex(glob: str) -> "re.Pattern[str]":
    i = 0
    out: List[str] = ["^"]
    while i < len(glob):
        if glob.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif glob.startswith("**", i):
            out.append(".*")
            i += 2
        elif glob[i] == "*":
            out.append("[^/]*")
            i += 1
        elif glob[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(glob[i]))
            i += 1
    out.append("$")
    return re.compile("".join(out))


def _write_brief_copy(brief_text: str) -> str:
    # KE-2026-11-20-TMPDIR-NESTED-PROJECT: brief copy under TMPDIR, never inside a framework project.
    fd, path = tempfile.mkstemp(prefix="lo-brief-", suffix=".md", text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(brief_text)
    except Exception:
        try:
            os.remove(path)
        except OSError:
            pass
        raise
    return path


def _run_navigate(
    repo: str,
    navigate: str,
    args: Sequence[str],
    timeout_s: float,
) -> Dict[str, Any]:
    # API-1: timeout on every call. argv list, never shell=True.
    cmd = [sys.executable, navigate, "--root", repo] + list(args) + ["--json"]
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=float(timeout_s),
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise RulesSourceError("navigate_timeout", "navigate.py timed out after %ss" % timeout_s) from exc
    except OSError as exc:
        raise RulesSourceError("navigate_exec", "cannot run navigate.py: %s" % exc) from exc

    stdout = proc.stdout.decode("utf-8", errors="replace") if proc.stdout else ""
    stderr = proc.stderr.decode("utf-8", errors="replace") if proc.stderr else ""
    raw = stdout.strip()
    obj = _parse_json_object(raw)
    if obj is None or "schema" not in obj:
        # KE defence: non-JSON / missing schema → framework_too_old (API-4, API-7, DB-5).
        raise RulesSourceError(
            "framework_too_old",
            "navigate.py rejected --json or returned non-contract output (exit %s): %s"
            % (proc.returncode, (stderr or stdout or "")[:200]),
        )
    if obj.get("schema") == "devframework.error/1":
        raise RulesSourceError(
            str(obj.get("error") or "navigate_error"),
            str(obj.get("message") or "navigate.py error"),
        )
    if proc.returncode != 0:
        raise RulesSourceError(
            "navigate_exit",
            "navigate.py exit %s: %s" % (proc.returncode, obj.get("message") or raw[:200]),
        )
    return {"obj": obj, "raw": raw}


def _parse_json_object(raw: str) -> Optional[Dict[str, Any]]:
    if not raw:
        return None
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        # Some stubs may print a trailing newline already stripped; try first line only.
        line = raw.split("\n", 1)[0].strip()
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            return None
    if not isinstance(obj, dict):
        return None
    return obj
