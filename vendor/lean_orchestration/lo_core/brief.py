"""Brief parsing — sections, ALLOWED patterns, cited ids (deterministic)."""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Sequence, Tuple, Union

# cost: one pass over brief text per gate run (≤ few KB typical; 5,000 runs/day planning scale).

# INV-006: real briefs use qualified headings; exact-only match falsely refused
# field briefs and pushed orchestrators to bypass the gate.
_HEADING_LINE = re.compile(r"^##\s+(.+?)\s*$")
# API-4 / same class as DEV Framework fence skip: indent ≤ 3, 3+ backticks or tildes.
_FENCE_MARK = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_BACKTICK = re.compile(r"`([^`]+)`")
_KE_ID = re.compile(r"\b(KE-\d{4}-\d{2}-\d{2}-[A-Z0-9]+(?:-[A-Z0-9]+)*)\b")
_UC_ID = re.compile(r"\b(UC-\d{3})\b")
_FR_ID = re.compile(r"\b(FR-\d{3})\b")
_NFR_ID = re.compile(r"\b(NFR-\d{3})\b")
_PATH_LINE = re.compile(r"(?P<path>[^\s:`][^:\s]*):(?P<line>\d+)\b")
_CALL_SITE = re.compile(r"Call site:\s*([^\s]+:\d+)", re.IGNORECASE)
_PORT_FROM = re.compile(r"PORT FROM:", re.IGNORECASE)
_KE_NONE = re.compile(
    r"KE:\s*none relevant\s*\(searched:\s*(.+?)\)",
    re.IGNORECASE | re.DOTALL,
)
_ALLOWED_TOKEN_OK = re.compile(r"(?:/|\*|\.[A-Za-z])")

# Qualifier delimiters after the section name (API-4 — documented grammar, no fuzzy match).
_QUALIFIER_DELIMS = ("(", "\u2014", "\u2013", "-", ":")  # ( — – - :

# BULK-3: explicit expansion cap per ALLOWED token.
ALLOWED_BRACE_CAP = 64

SectionList = List[Tuple[str, str]]
Sections = Union[SectionList, Dict[str, str]]


def _try_open_fence(line: str) -> Optional[Tuple[str, int]]:
    """Return (char, min_len) if *line* opens a fence; else None."""
    match = _FENCE_MARK.match(line)
    if not match:
        return None
    run = match.group(1)
    return (run[0], len(run))


def _try_close_fence(line: str, fence: Tuple[str, int]) -> bool:
    """True when *line* closes the open fence (char, min_len)."""
    match = _FENCE_MARK.match(line)
    if not match:
        return False
    run = match.group(1)
    rest = line[match.end() :]
    return run[0] == fence[0] and len(run) >= fence[1] and rest.strip() == ""


def heading_matches(heading: str, name: str) -> bool:
    """True when *heading* is section *name* or *name* plus a documented qualifier.

    Grammar: lowercased trimmed heading equals *name*, or starts with *name* then
    optional spaces and one of ``(``, ``—``, ``–``, ``-``, ``:``.
    """
    h = heading.strip().lower()
    n = name.strip().lower()
    if h == n:
        return True
    if not h.startswith(n):
        return False
    rest = h[len(n) :]
    i = 0
    while i < len(rest) and rest[i] == " ":
        i += 1
    if i >= len(rest):
        return False
    return rest[i] in _QUALIFIER_DELIMS


def parse_sections(text: str) -> SectionList:
    """Split brief into ``##`` sections in document order.

    Returns ``[(heading_lower, body), ...]``. Headings inside fenced code
    (```` ``` ```` / ``~~~``, indent ≤ 3) are not headings.
    """
    lines = text.split("\n")
    headings: List[Tuple[int, str]] = []
    fence: Optional[Tuple[str, int]] = None
    for i, line in enumerate(lines):
        check = line.rstrip("\r")
        if fence is not None:
            if _try_close_fence(check, fence):
                fence = None
            continue
        opened = _try_open_fence(check)
        if opened is not None:
            fence = opened
            continue
        match = _HEADING_LINE.match(check)
        if match:
            headings.append((i, match.group(1).strip().lower()))

    sections: SectionList = []
    for j, (idx, name) in enumerate(headings):
        start = idx + 1
        end = headings[j + 1][0] if j + 1 < len(headings) else len(lines)
        body = "\n".join(lines[start:end])
        sections.append((name, body))
    return sections


def section_body(sections: Sections, name: str) -> Optional[str]:
    """Return concatenated bodies of every heading matching *name*, or None."""
    name_l = name.strip().lower()
    bodies: List[str] = []
    if isinstance(sections, dict):
        items = list(sections.items())
    else:
        items = list(sections)
    for heading, body in items:
        if heading_matches(heading, name_l):
            bodies.append(body)
    if not bodies:
        return None
    return "".join(bodies)


def is_empty_section(body: Optional[str]) -> bool:
    if body is None:
        return True
    return not body.strip()


def expand_allowed_braces(token: str) -> Tuple[List[str], Optional[str], bool]:
    """Expand ``{a,b}`` groups in an ALLOWED token (cartesian product, no nesting).

    Returns ``(patterns, refusal_reason_or_None, keep_literal_unmapped)``.
    Nested or unbalanced braces → ``([token], None, True)`` (literal + unmapped).
    Product above :data:`ALLOWED_BRACE_CAP` → ``([], reason, False)``.
    """
    groups: List[Tuple[int, int, List[str]]] = []  # start, end_exclusive, alts
    i = 0
    n = len(token)
    while i < n:
        if token[i] != "{":
            i += 1
            continue
        start = i
        i += 1
        depth_extra = False
        parts: List[str] = []
        buf: List[str] = []
        closed = False
        while i < n:
            ch = token[i]
            if ch == "{":
                depth_extra = True
                break
            if ch == "}":
                parts.append("".join(buf))
                closed = True
                i += 1
                break
            if ch == ",":
                parts.append("".join(buf))
                buf = []
                i += 1
                continue
            buf.append(ch)
            i += 1
        if depth_extra or not closed:
            return [token], None, True
        groups.append((start, i, parts))
    if not groups:
        return [token], None, False

    # cost: product of alternatives per token; capped at ALLOWED_BRACE_CAP (BULK-3).
    total = 1
    for _s, _e, alts in groups:
        total *= len(alts)
        if total > ALLOWED_BRACE_CAP:
            return [], "allowed_pattern_too_broad:%s" % token, False

    # Expand left-to-right cartesian product.
    patterns = [""]
    cursor = 0
    for start, end, alts in groups:
        prefix = token[cursor:start]
        next_patterns: List[str] = []
        for base in patterns:
            for alt in alts:
                next_patterns.append(base + prefix + alt)
        patterns = next_patterns
        cursor = end
    suffix = token[cursor:]
    patterns = [p + suffix for p in patterns]
    return patterns, None, False


def extract_allowed_patterns(
    allowed_section: str,
    extra: Optional[Sequence[str]] = None,
) -> Tuple[List[str], List[str], List[str]]:
    """Backtick tokens with ``/``, ``*``, or ``.``+letters; brace-expand; extras.

    Returns ``(patterns, reasons, brace_unmapped)`` in first-seen order (deduped).
    Expanded patterns are what callers pass to path→areas matching.
    """
    found: List[str] = []
    reasons: List[str] = []
    brace_unmapped: List[str] = []
    seen = set()
    seen_reasons = set()
    seen_unmapped = set()

    def _add_pattern(item: str) -> None:
        if item not in seen:
            seen.add(item)
            found.append(item)

    for token in _BACKTICK.findall(allowed_section or ""):
        token = token.strip()
        if not token or not _ALLOWED_TOKEN_OK.search(token):
            continue
        expanded, reason, literal_unmapped = expand_allowed_braces(token)
        if reason:
            if reason not in seen_reasons:
                seen_reasons.add(reason)
                reasons.append(reason)
            continue
        for item in expanded:
            _add_pattern(item)
        if literal_unmapped:
            if token not in seen_unmapped:
                seen_unmapped.add(token)
                brace_unmapped.append(token)
    for item in extra or ():
        item = str(item).strip()
        if not item:
            continue
        expanded, reason, literal_unmapped = expand_allowed_braces(item)
        if reason:
            if reason not in seen_reasons:
                seen_reasons.add(reason)
                reasons.append(reason)
            continue
        for piece in expanded:
            _add_pattern(piece)
        if literal_unmapped and item not in seen_unmapped:
            seen_unmapped.add(item)
            brace_unmapped.append(item)
    return found, reasons, brace_unmapped


def extract_cited_ids(text: str, areas: Sequence[str]) -> Dict[str, List[str]]:
    """Return cited id lists in first-seen order (KE, rules, UC, FR, NFR)."""
    return {
        "ke": _unique_in_order(_KE_ID.findall(text)),
        "rules": _extract_rule_ids(text, areas),
        "uc": _unique_in_order(_UC_ID.findall(text)),
        "fr": _unique_in_order(_FR_ID.findall(text)),
        "nfr": _unique_in_order(_NFR_ID.findall(text)),
    }


def all_cited_flat(cited: Dict[str, List[str]]) -> List[str]:
    out: List[str] = []
    seen = set()
    for key in ("ke", "rules", "uc", "fr", "nfr"):
        for item in cited.get(key, []):
            if item not in seen:
                seen.add(item)
                out.append(item)
    return out


def known_errors_stated(known_errors_body: str) -> bool:
    if _KE_ID.search(known_errors_body or ""):
        return True
    match = _KE_NONE.search(known_errors_body or "")
    return bool(match and match.group(1).strip())


def declares_porting(text: str, sections: Sections) -> bool:
    if section_body(sections, "reference") is not None:
        return True
    return bool(_PORT_FROM.search(text))


def porting_findings(text: str) -> Tuple[bool, bool]:
    """Return (has_path_line_reference, has_call_site)."""
    has_call = bool(_CALL_SITE.search(text))
    # Any path:line counts as a reference (including Call site itself).
    has_ref = bool(_PATH_LINE.search(text))
    return has_ref, has_call


def _extract_rule_ids(text: str, areas: Sequence[str]) -> List[str]:
    if not areas:
        return []
    # Longest area names first so JOB matches before JO if both existed.
    ordered = sorted({a.upper() for a in areas}, key=lambda a: (-len(a), a))
    pattern = re.compile(r"\b((?:%s)-\d+)\b" % "|".join(re.escape(a) for a in ordered))
    return _unique_in_order(pattern.findall(text))


def _unique_in_order(items: Sequence[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out
