"""Deterministic gate — one function for CLI and Hermes tool (SEC-2, FR-001…FR-008)."""
from __future__ import annotations

import hashlib
import os
from typing import Any, Dict, List, Mapping, Optional, Sequence

from lo_core import brief as brief_mod
from lo_core import journal as journal_mod
from lo_core import rules_source
from lo_core import task as task_mod
from lo_core.config import GATE_SCHEMA, merge_config, resolve_profile
from lo_core.rules_source import STANDARD_AREAS, RulesSourceError


def make_error_result(
    reason: str,
    *,
    repo: str = "",
    brief_sha256: str = "",
    config: Optional[Mapping[str, Any]] = None,
    profile: str = "code",
) -> Dict[str, Any]:
    """Shared ERROR shape for bad_repo / brief_not_found / … (SEC-2)."""
    cfg = merge_config(config)
    return {
        "schema": GATE_SCHEMA,
        "decision": "ERROR",
        "reasons": [reason],
        "areas": [],
        "allowed": [],
        "unmapped": [],
        "cited": {"ke": [], "rules": [], "uc": [], "fr": [], "nfr": []},
        "delivered": {"rules": [], "known_errors": [], "omitted": []},
        "task": "",
        "task_bytes": 0,
        "rules_source": {"kind": "", "framework_version": ""},
        "algorithms": {
            "gate": cfg["algorithm_gate"],
            "delivery": cfg["algorithm_delivery"],
            "known_errors": "",
        },
        "brief_sha256": brief_sha256,
        "repo": repo,
        "profile": profile,
    }


def run_gate(
    repo: str,
    brief_text: str,
    *,
    allowed_extra: Optional[Sequence[str]] = None,
    config: Optional[Mapping[str, Any]] = None,
    journal_path: Optional[str] = None,
    profile: Optional[str] = None,
) -> Dict[str, Any]:
    """Evaluate the brief against the target repository.

    Returns a versioned result dict (``schema`` = lean-orchestration.gate/1).
    Journal failures add ``journal_error`` without changing ``decision``.
    Bad repo → ``decision: ERROR``, ``reasons: ["bad_repo"]`` (never raises).
    ``profile`` selects ``brief_profiles.<name>`` (default ``code``); a brief cannot
    pick its own profile (FR-011 / DECISION 8).
    """
    cfg = merge_config(config)
    brief_sha = hashlib.sha256(brief_text.encode("utf-8")).hexdigest()
    try:
        prof = resolve_profile(cfg, profile)
    except ValueError as exc:
        return make_error_result(
            "unknown_profile:%s" % exc,
            repo=str(repo or ""),
            brief_sha256=brief_sha,
            config=cfg,
            profile=str(profile or ""),
        )
    profile_name = prof["name"]
    required_sections = list(prof["required_sections"])
    require_area_rules = bool(prof["require_area_rules"])

    # SEC-1: absolute existing directory; never write into it from the gate.
    if not repo or not os.path.isabs(repo) or not os.path.isdir(repo):
        return make_error_result(
            "bad_repo",
            repo=str(repo or ""),
            brief_sha256=brief_sha,
            config=cfg,
            profile=profile_name,
        )

    repo = os.path.abspath(repo)

    # FR-016: gate_enabled only from plugin config — never brief text or call args.
    if not bool(cfg.get("gate_enabled", True)):
        return _gate_disabled_result(
            repo=repo,
            brief_text=brief_text,
            brief_sha=brief_sha,
            cfg=cfg,
            profile_name=profile_name,
            allowed_extra=allowed_extra,
            journal_path=journal_path,
        )

    sections = brief_mod.parse_sections(brief_text)
    reasons: List[str] = []

    # FR-001: required / empty sections (profile-selected list).
    for name in required_sections:
        key = name.strip().lower()
        body = brief_mod.section_body(sections, key)
        if body is None:
            reasons.append("missing_section:%s" % key)
        elif brief_mod.is_empty_section(body):
            reasons.append("empty_section:%s" % key)

    allowed_body = brief_mod.section_body(sections, "allowed") or ""
    allowed, brace_reasons, brace_unmapped = brief_mod.extract_allowed_patterns(
        allowed_body, allowed_extra
    )
    reasons.extend(brace_reasons)

    # Cite ids using the standard area catalogue (rules source areas refine delivery).
    cited = brief_mod.extract_cited_ids(brief_text, STANDARD_AREAS)

    rules: Dict[str, Any]
    rules_error: Optional[RulesSourceError] = None
    try:
        rules = rules_source.load_rules(
            repo,
            brief_text=brief_text,
            allowed=allowed,
            cited_ke=cited["ke"],
            timeout_s=cfg["navigate_timeout_s"],
            known_errors_limit=cfg["known_errors_limit"],
        )
    except RulesSourceError as exc:
        rules_error = exc
        rules = {
            "kind": "error",
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
        if exc.code == "framework_too_old":
            reasons.append("framework_too_old")
        else:
            reasons.append("rules_source_error:%s" % exc.code)

    # FR-007 none: brief must state Rules: none in project.
    if rules_error is None and rules.get("kind") == "none":
        arch = brief_mod.section_body(sections, "architecture rules") or ""
        if "Rules: none in project" not in arch:
            reasons.append("rules_none_not_stated")

    # FR-002: one ids --check call (5th navigate call when kind=navigate).
    flat_ids = brief_mod.all_cited_flat(cited)
    if flat_ids and rules_error is None:
        try:
            id_result = rules_source.check_ids(repo, flat_ids, timeout_s=cfg["navigate_timeout_s"])
            if id_result.get("raw"):
                rules.setdefault("raw_jsons", []).append(id_result["raw"])
            for unknown in id_result.get("unknown") or []:
                reasons.append("unknown_id:%s" % unknown)
        except RulesSourceError as exc:
            if exc.code == "framework_too_old":
                reasons.append("framework_too_old")
            else:
                reasons.append("rules_source_error:%s" % exc.code)

    # Known errors section must cite a KE or the explicit none line (when required).
    ke_required = any(s.strip().lower() == "known errors" for s in required_sections)
    if ke_required:
        if "missing_section:known errors" not in reasons and "empty_section:known errors" not in reasons:
            ke_body = brief_mod.section_body(sections, "known errors") or ""
            if not brief_mod.known_errors_stated(ke_body):
                reasons.append("known_errors_not_stated")

    # FR-003: required areas from health --paths (except SCOPE); skipped for research.
    required_areas = sorted(
        {str(a).upper() for a in (rules.get("areas") or []) if str(a).upper() != "SCOPE"}
    )
    cited_rules = set(cited.get("rules") or [])
    if require_area_rules and rules_error is None and rules.get("kind") != "none":
        for area in required_areas:
            if not any(rid.startswith(area + "-") for rid in cited_rules):
                reasons.append("area_not_cited:%s" % area)

    # FR-006 porting reference.
    if brief_mod.declares_porting(brief_text, sections):
        has_ref, has_call = brief_mod.porting_findings(brief_text)
        if not has_ref:
            reasons.append("port_reference_missing")
        if not has_call:
            reasons.append("call_site_missing")

    reasons = sorted(set(reasons))
    is_source_error = any(
        r == "framework_too_old" or r.startswith("rules_source_error:") for r in reasons
    )
    if not reasons:
        decision = "PASS"
    elif is_source_error:
        decision = "ERROR"
    else:
        decision = "REFUSED"

    delivered_rules: List[str] = []
    delivered_kes: List[str] = []
    task_text = ""
    omitted: List[str] = []
    if rules_error is None:
        task_text, delivered_rules, delivered_kes, omitted = task_mod.assemble_task(
            brief_text,
            rules=rules,
            required_areas=required_areas,
            cited_ke_order=cited.get("ke") or [],
            delivery_cap_bytes=cfg["delivery_cap_bytes"],
            algorithm_delivery=cfg["algorithm_delivery"],
        )

    areas_out = list(rules.get("areas") or [])
    # Nested/unbalanced brace tokens stay literal and are listed in unmapped (BULK-3).
    unmapped_out: List[str] = []
    seen_unmapped = set()
    for item in list(brace_unmapped) + list(rules.get("unmapped") or []):
        if item not in seen_unmapped:
            seen_unmapped.add(item)
            unmapped_out.append(item)
    result: Dict[str, Any] = {
        "schema": GATE_SCHEMA,
        "decision": decision,
        "reasons": reasons,
        "areas": areas_out,
        "allowed": list(allowed),
        "unmapped": unmapped_out,
        "cited": {
            "ke": list(cited.get("ke") or []),
            "rules": list(cited.get("rules") or []),
            "uc": list(cited.get("uc") or []),
            "fr": list(cited.get("fr") or []),
            "nfr": list(cited.get("nfr") or []),
        },
        "delivered": {
            "rules": delivered_rules,
            "known_errors": delivered_kes,
            "omitted": omitted,
        },
        "task": task_text if decision == "PASS" else "",
        "task_bytes": len(task_text.encode("utf-8")) if decision == "PASS" else 0,
        "rules_source": {
            "kind": rules.get("kind"),
            "framework_version": rules.get("framework_version") or "",
        },
        "algorithms": {
            "gate": cfg["algorithm_gate"],
            "delivery": cfg["algorithm_delivery"],
            "known_errors": (rules.get("algorithms") or {}).get("known_errors") or "",
        },
        "brief_sha256": brief_sha,
        "repo": repo,
        "profile": profile_name,
    }
    if rules_error is not None:
        result["rules_source_error"] = {"code": rules_error.code, "message": rules_error.message}

    if journal_path:
        try:
            journal_mod.append_journal(
                journal_path,
                {
                    "brief_sha256": brief_sha,
                    "repo": repo,
                    "decision": decision,
                    "reasons": reasons,
                    "cited": result["cited"],
                    "areas": result["areas"],
                    "unmapped": result["unmapped"],
                    "delivered_rules": delivered_rules,
                    "delivered_known_errors": delivered_kes,
                    "algorithms": result["algorithms"],
                    "rules_source": result["rules_source"],
                    "profile": profile_name,
                },
                rotate_bytes=cfg["journal_rotate_bytes"],
            )
        except Exception as exc:  # noqa: BLE001 — surface; do not change decision
            result["journal_error"] = str(exc)

    return result


def _gate_disabled_result(
    *,
    repo: str,
    brief_text: str,
    brief_sha: str,
    cfg: Mapping[str, Any],
    profile_name: str,
    allowed_extra: Optional[Sequence[str]],
    journal_path: Optional[str],
) -> Dict[str, Any]:
    """FR-016 short-circuit: PASS with reasons [gate_disabled]; still assemble a task."""
    sections = brief_mod.parse_sections(brief_text)
    allowed_body = brief_mod.section_body(sections, "allowed") or ""
    allowed, _brace_reasons, brace_unmapped = brief_mod.extract_allowed_patterns(
        allowed_body, allowed_extra
    )
    cited = brief_mod.extract_cited_ids(brief_text, STANDARD_AREAS)
    rules: Dict[str, Any]
    try:
        rules = rules_source.load_rules(
            repo,
            brief_text=brief_text,
            allowed=allowed,
            cited_ke=cited["ke"],
            timeout_s=cfg["navigate_timeout_s"],
            known_errors_limit=cfg["known_errors_limit"],
        )
    except RulesSourceError:
        rules = {
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
    required_areas = sorted(
        {str(a).upper() for a in (rules.get("areas") or []) if str(a).upper() != "SCOPE"}
    )
    task_text, delivered_rules, delivered_kes, omitted = task_mod.assemble_task(
        brief_text,
        rules=rules,
        required_areas=required_areas,
        cited_ke_order=cited.get("ke") or [],
        delivery_cap_bytes=cfg["delivery_cap_bytes"],
        algorithm_delivery=cfg["algorithm_delivery"],
    )
    unmapped_out: List[str] = []
    seen_unmapped = set()
    for item in list(brace_unmapped) + list(rules.get("unmapped") or []):
        if item not in seen_unmapped:
            seen_unmapped.add(item)
            unmapped_out.append(item)
    reasons = ["gate_disabled"]
    result: Dict[str, Any] = {
        "schema": GATE_SCHEMA,
        "decision": "PASS",
        "reasons": reasons,
        "areas": list(rules.get("areas") or []),
        "allowed": list(allowed),
        "unmapped": unmapped_out,
        "cited": {
            "ke": list(cited.get("ke") or []),
            "rules": list(cited.get("rules") or []),
            "uc": list(cited.get("uc") or []),
            "fr": list(cited.get("fr") or []),
            "nfr": list(cited.get("nfr") or []),
        },
        "delivered": {
            "rules": delivered_rules,
            "known_errors": delivered_kes,
            "omitted": omitted,
        },
        "task": task_text,
        "task_bytes": len(task_text.encode("utf-8")),
        "rules_source": {
            "kind": rules.get("kind"),
            "framework_version": rules.get("framework_version") or "",
        },
        "algorithms": {
            "gate": cfg["algorithm_gate"],
            "delivery": cfg["algorithm_delivery"],
            "known_errors": (rules.get("algorithms") or {}).get("known_errors") or "",
        },
        "brief_sha256": brief_sha,
        "repo": repo,
        "profile": profile_name,
        "gate_disabled": True,
    }
    if journal_path:
        try:
            journal_mod.append_journal(
                journal_path,
                {
                    "brief_sha256": brief_sha,
                    "repo": repo,
                    "decision": "PASS",
                    "reasons": reasons,
                    "cited": result["cited"],
                    "areas": result["areas"],
                    "unmapped": result["unmapped"],
                    "delivered_rules": delivered_rules,
                    "delivered_known_errors": delivered_kes,
                    "algorithms": result["algorithms"],
                    "rules_source": result["rules_source"],
                    "profile": profile_name,
                    "gate_disabled": True,
                },
                rotate_bytes=cfg["journal_rotate_bytes"],
            )
        except Exception as exc:  # noqa: BLE001
            result["journal_error"] = str(exc)
    return result
