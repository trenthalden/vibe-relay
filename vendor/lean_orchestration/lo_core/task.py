"""Executor task assembly — brief + delivered rules/known errors (FR-005). Pure function.

Algorithm ``lo-delivery/2``: full blocks = cited (citation order) then area-linked
(document order); lexical candidates are titles only (FR-004 / FR-005 / INV-002).
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

_RULE_LINE_ID = re.compile(r"^-\s+(\S+)")

RECEIPT_TEMPLATE = """### Receipt

STATUS: (filled by executor)
FILES:
GATE:
DONE MEANS:
RED:
BOUNDARIES:
NOT VERIFIED:
RESIDUAL RISK:
NEEDS RECORDING:
"""


def assemble_task(
    brief_text: str,
    *,
    rules: Mapping[str, Any],
    required_areas: Sequence[str],
    cited_ke_order: Sequence[str],
    delivery_cap_bytes: int,
    algorithm_delivery: str,
) -> Tuple[str, List[str], List[str], List[str]]:
    """Return (task_text, delivered_rule_ids, delivered_ke_ids, omitted_ids).

    Same inputs → same bytes (NFR-001). Cap applies to full known-error blocks.
    """
    delivered_block, rule_ids, ke_ids, omitted = build_delivered_block(
        rules=rules,
        required_areas=required_areas,
        cited_ke_order=cited_ke_order,
        delivery_cap_bytes=delivery_cap_bytes,
        algorithm_delivery=algorithm_delivery,
    )
    task = brief_text.rstrip() + "\n\n---\n" + delivered_block
    if not task.endswith("\n"):
        task += "\n"
    return task, rule_ids, ke_ids, omitted


def build_delivered_block(
    *,
    rules: Mapping[str, Any],
    required_areas: Sequence[str],
    cited_ke_order: Sequence[str],
    delivery_cap_bytes: int,
    algorithm_delivery: str,
) -> Tuple[str, List[str], List[str], List[str]]:
    provenance = _provenance_line(rules, algorithm_delivery)
    rule_lines, rule_ids = _rule_lines(rules.get("rows") or [], required_areas)
    full_blocks, full_ids = _full_known_error_blocks(
        cited_order=cited_ke_order,
        cited_blocks=list(rules.get("cited_blocks") or []),
        area_linked=list(rules.get("area_linked_blocks") or []),
    )
    lexical_titles = _lexical_title_lines(
        candidates=list(rules.get("candidates") or []),
        already=set(full_ids),
    )

    header = "## Project rules delivered by lean-orchestration\n" + provenance + "\n"
    body = header
    delivered_rules: List[str] = []
    for line in rule_lines:
        body += line + "\n"
        match = _RULE_LINE_ID.match(line)
        if match:
            rid = match.group(1)
            if rid and rid not in delivered_rules:
                delivered_rules.append(rid)

    body += "\n### Known errors you must not repeat\n"
    delivered_kes: List[str] = []
    omitted: List[str] = []
    cap = int(delivery_cap_bytes)
    # Cap on full KE blocks only (FR-005 / lo-delivery/2).
    for index, (block, kid) in enumerate(full_blocks):
        text = block if block.endswith("\n") else block + "\n"
        piece = "\n" + text
        candidate = body + piece
        if len(candidate.encode("utf-8")) > cap:
            for _later_block, later_id in full_blocks[index:]:
                if later_id and later_id not in omitted:
                    omitted.append(later_id)
            if omitted:
                body += "Not delivered (cap): " + ", ".join(omitted) + "\n"
            break
        body = candidate
        if kid and kid not in delivered_kes:
            delivered_kes.append(kid)

    body += "\n### Possibly related (lexical, titles only)\n"
    if lexical_titles:
        for line in lexical_titles:
            body += line + "\n"
    else:
        body += "(none)\n"

    body += "\n" + RECEIPT_TEMPLATE
    return body, delivered_rules, delivered_kes, omitted


def _provenance_line(rules: Mapping[str, Any], algorithm_delivery: str) -> str:
    raw_jsons = list(rules.get("raw_jsons") or [])
    digest = hashlib.sha256("".join(raw_jsons).encode("utf-8")).hexdigest()
    kind = str(rules.get("kind") or "")
    version = str(rules.get("framework_version") or "") or "-"
    return (
        "provenance: kind=%s framework_version=%s algorithm=%s raw_sha256=%s"
        % (kind, version, algorithm_delivery, digest)
    )


def _rule_lines(rows: Sequence[Mapping[str, Any]], required_areas: Sequence[str]) -> Tuple[List[str], List[str]]:
    want = set(["SCOPE"] + [a.upper() for a in required_areas])
    lines: List[str] = []
    ids: List[str] = []
    # Contract order: keep rows order as returned (SCOPE first typically).
    for row in rows:
        area = str(row.get("area") or "").upper()
        if area not in want:
            continue
        rid = str(row.get("id") or "")
        rule = str(row.get("rule") or "")
        prevents = str(row.get("prevents") or "")
        line = "- %s — %s (prevents: %s)" % (rid, rule, prevents)
        lines.append(line)
        if rid and rid not in ids:
            ids.append(rid)
    return lines, ids


def _full_known_error_blocks(
    *,
    cited_order: Sequence[str],
    cited_blocks: Sequence[Mapping[str, Any]],
    area_linked: Sequence[Mapping[str, Any]],
) -> Tuple[List[Tuple[str, str]], List[str]]:
    by_id: Dict[str, Mapping[str, Any]] = {}
    for item in list(cited_blocks) + list(area_linked):
        kid = str(item.get("id") or "")
        if kid and kid not in by_id:
            by_id[kid] = item

    ordered_ids: List[str] = []
    for kid in cited_order:
        if kid in by_id and kid not in ordered_ids:
            ordered_ids.append(kid)
    # Area-linked in document order (already ordered by rules source).
    for item in area_linked:
        kid = str(item.get("id") or "")
        if kid and kid not in ordered_ids:
            ordered_ids.append(kid)

    blocks: List[Tuple[str, str]] = []
    for kid in ordered_ids:
        item = by_id[kid]
        block = str(item.get("block") or "").strip()
        if not block:
            title = str(item.get("title") or "")
            block = "### %s: %s" % (kid, title)
        blocks.append((block, kid))
    return blocks, ordered_ids


def _lexical_title_lines(
    *,
    candidates: Sequence[Mapping[str, Any]],
    already: set,
) -> List[str]:
    lines: List[str] = []
    seen = set(already)
    for item in candidates:
        kid = str(item.get("id") or "")
        if not kid or kid in seen:
            continue
        seen.add(kid)
        title = str(item.get("title") or "")
        lines.append("- %s — %s" % (kid, title))
    return lines
