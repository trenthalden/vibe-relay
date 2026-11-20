"""Pure acceptance decision from gathered facts (FR-014, SEC-2).

No filesystem, no subprocess — inputs are already normalised facts.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence

from lo_core.config import ACCEPT_SCHEMA
from lo_core.rules_source import normalize_path, path_matches_glob


def path_in_allowed(path: str, allowed: Sequence[str]) -> bool:
    """True when *path* matches at least one expanded ALLOWED glob (same matcher as the gate)."""
    norm = normalize_path(path)
    for pattern in allowed:
        if path_matches_glob(norm, normalize_path(str(pattern))):
            return True
    return False


def decide_accept(
    *,
    changed_paths: Sequence[str],
    allowed: Sequence[str],
    gate_decision: str,
    gate_reasons: Sequence[str],
    probe_results: Sequence[Mapping[str, Any]],
    tree_sha256: str,
    has_git_base: bool,
    dispatch_known: bool = True,
    probes_status: str = "ok",
    paths_capped: bool = False,
    gate_disabled: bool = False,
) -> Dict[str, Any]:
    """Return a versioned accept result. Reasons use the fixed order from the brief.

    Order: path_outside_allowed:* (sorted), gate:*, probe_failed:*/probe_timeout:*,
    no_git_base, dispatch_unknown, bad_probes. Decision ACCEPTED only when none fire
    and gate PASS and every probe matches expect_rc.
    """
    # Fixed reason order (BRIEF DECISION 2): path_outside_allowed → gate → probe →
    # no_git_base → dispatch_unknown (+ bad_probes with probes).
    reasons: List[str] = []

    outside: List[str] = []
    for path in sorted({normalize_path(p) for p in changed_paths}):
        if not path_in_allowed(path, allowed):
            outside.append(path)
    for path in outside:
        reasons.append("path_outside_allowed:%s" % path)

    if gate_decision != "PASS":
        for gr in gate_reasons or ["refused"]:
            reasons.append("gate:%s" % gr)

    if probes_status == "bad_probes":
        reasons.append("bad_probes")
    for pr in sorted(probe_results, key=lambda r: str(r.get("name") or "")):
        name = str(pr.get("name") or "")
        if pr.get("timed_out"):
            reasons.append("probe_timeout:%s" % name)
            continue
        rc = pr.get("rc")
        expect = pr.get("expect_rc")
        if rc != expect:
            reasons.append("probe_failed:%s:rc=%s" % (name, rc))

    if not has_git_base:
        reasons.append("no_git_base")
    if not dispatch_known:
        reasons.append("dispatch_unknown")

    reasons = _stable_unique(reasons)

    decision = "ACCEPTED" if not reasons else "REJECTED"
    result: Dict[str, Any] = {
        "schema": ACCEPT_SCHEMA,
        "decision": decision,
        "reasons": reasons,
        "changed_paths": sorted({normalize_path(p) for p in changed_paths}),
        "allowed": list(allowed),
        "tree_sha256": tree_sha256,
        "probes": probes_status if probes_status == "none" else "ran",
        "paths_capped": bool(paths_capped),
    }
    if gate_disabled:
        result["gate_disabled"] = True
    return result


def tree_hash_material(path_hashes: Mapping[str, str]) -> str:
    """Canonical bytes for tree_sha256: sorted ``path\\0sha256`` joined, then sha256 hex.

    Caller hashes the joined UTF-8 bytes.
    """
    parts: List[str] = []
    for path in sorted(path_hashes.keys()):
        parts.append("%s\0%s" % (normalize_path(path), path_hashes[path]))
    return "".join(parts)


def _stable_unique(items: Sequence[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out
