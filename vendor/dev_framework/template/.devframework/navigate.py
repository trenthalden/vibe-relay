"""Navigation for agents: brief | find | index | contract | checklist | handoff. Standard library only.

Every output here is read by an agent at the start of a session, so it is short by design:
the brief is one screen, `find` returns one block, `index` is one line per identifier.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
import unicodedata
from datetime import date
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

DOCS = ("docs/USE_CASES.md", "docs/KNOWN_ERRORS.md", "docs/REQUIREMENTS.md", "docs/BACKLOG.md",
        "docs/ARCHITECTURE.md", "docs/INVARIANTS.md", "docs/REGRESSION_TEST.md", "docs/RELEASE.md", "PROJECT.md")
TEMPLATE_DOCS = {"docs/CHECKLIST_TEMPLATE.md", "docs/USE_CASE_TEMPLATE.md", "docs/USE_CASES_SLICE_TEMPLATE.md", "docs/INDEX.md"}
HEADING = re.compile(r"^(#{2,4}) ((?:UC|KE|FR|NFR|SET|INV)-[A-Za-z0-9.-]+)(?:\s+[—-]\s+(.*))?\s*$")
PLACEHOLDER = re.compile(r"YYYY|###|\.\.\.|<[A-Za-z ]+>")
CHECKBOX = re.compile(r"^(\s*- \[)( |x|X)(\] .*)$")
# Checklist / handoff slugs: no path separators, no traversal. Reject empty, "..", and leading-dot forms.
SLUG = re.compile(r"^[A-Za-z0-9._-]+$")


def write(path: Path, text: str) -> None:
    path.write_bytes(text.encode("utf-8"))  # LF on every platform: generated files must hash the same everywhere


def read(root: Path, relative: str) -> str:
    path = root / relative
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _is_under(path: Path, parent: Path) -> bool:
    """True when *path* is *parent* or a descendant (after resolve)."""
    path, parent = path.resolve(), parent.resolve()
    return path == parent or parent in path.parents


def docs_dir(root: Path) -> Path:
    """Resolved ``root/docs``, refusing a docs tree that escapes the project root."""
    root_r = root.resolve()
    docs = (root / "docs").resolve()
    if not _is_under(docs, root_r):
        raise ValueError("docs/ escapes project root (symlink?)")
    return docs


def resolve_docs_slug(root: Path, slug: str) -> Path:
    """Resolve ``docs/<slug>.md`` under ``root/docs``; refuse traversal / absolute / nested paths.

    Accepts only ``[A-Za-z0-9._-]+``, rejects empty, ``..``, and leading-dot-only / leading-dot
    slugs. The resolved path must stay under ``root/docs`` before any read, write, or unlink.
    """
    text = str(slug or "").strip()
    if not text or not SLUG.fullmatch(text) or ".." in text or text.startswith("."):
        raise ValueError(
            f"invalid checklist slug {slug!r}: use only letters, digits, . _ - "
            "(no path separators, no '..', no leading dot)"
        )
    docs = docs_dir(root)
    path = (docs / f"{text}.md").resolve()
    if not _is_under(path, docs):
        raise ValueError(f"checklist path escapes docs/: {slug!r}")
    return path


def resolve_under_docs(root: Path, *parts: str) -> Path:
    """Join *parts* under ``root/docs`` and refuse any resolved escape."""
    docs = docs_dir(root)
    path = docs.joinpath(*parts).resolve()
    if not _is_under(path, docs):
        raise ValueError(f"path escapes docs/: {parts!r}")
    return path


def blocks(root: Path):
    """Yield (relative, id, title, body) for every identifier heading in the project documents."""
    for relative in DOCS:
        lines = read(root, relative).splitlines()
        fenced = False
        for number, line in enumerate(lines):
            if line.startswith("```"):
                fenced = not fenced  # examples inside code fences are not identifiers
            match = None if fenced else HEADING.match(line)
            if not match or PLACEHOLDER.search(match[2]) or "EXAMPLE" in match[2] or PLACEHOLDER.search(match[3] or ""):
                continue  # template examples (`UC-###`, `<short case name>`, KE-…-EXAMPLE-SLUG) are not project content
            level = len(match[1])
            body = []
            for following in lines[number + 1:]:
                if re.match(r"^#{1,%d} " % level, following):
                    break
                body.append(following)
            yield relative, match[2], (match[3] or "").strip(), body


def field(body: list[str], name: str) -> str:
    for line in body:
        match = re.match(r"^\s*(?:- )?\*\*%s:\*\*\s*(.*)$" % re.escape(name), line)
        if match:
            return match[1].strip()
    return ""


def git(root: Path, *args: str) -> str:
    try:
        run = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=15)  # git speaks UTF-8; the console locale does not
        return run.stdout.strip() if run.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def freshness(root: Path) -> str:
    """Is the checkout current? Parallel work (people or other agents) commits while you are away.

    Fetches the upstream of the current branch (best effort, 20 s) and reports behind/ahead.
    """
    if not git(root, "remote"):
        return "remote: none — nobody else can have pushed; still check `git log` against the last handoff"
    upstream = git(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")
    if not upstream:
        return "remote: branch has no upstream — compare with the shared branch by hand before editing"
    try:
        fetched = subprocess.run(["git", "-C", str(root), "fetch", "--quiet"], capture_output=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        fetched = None
    if fetched is None or fetched.returncode:  # offline or a sandbox without network: the refs are stale, not current
        return (f"remote: NOT CHECKED (fetch failed — offline or sandbox) — assume {upstream} may be ahead; "
                "pull before editing")
    counts = git(root, "rev-list", "--left-right", "--count", f"HEAD...{upstream}")
    if not counts:
        return f"remote: cannot compare with {upstream}"
    ahead, behind = (int(x) for x in counts.split())
    if behind:
        incoming = " | ".join(git(root, "log", "-3", "--format=%h %s", f"HEAD..{upstream}").splitlines())
        return f"remote: BEHIND {upstream} by {behind} commit(s) — pull/rebase BEFORE editing: {incoming}"
    return f"remote: up to date with {upstream}" + (f", {ahead} local commit(s) not pushed" if ahead else "")


def active_checklists(root: Path) -> list[Path]:
    docs = root / "docs"
    if not docs.is_dir():
        return []
    found = []
    for path in sorted(docs.glob("*.md")):
        relative = "docs/" + path.name
        if relative in DOCS or relative in TEMPLATE_DOCS:
            continue
        text = path.read_text(encoding="utf-8")
        if re.search(r"^\*\*Status:\*\*\s*in progress", text, re.M):
            found.append(path)
    return found


def open_items(path: Path) -> list[tuple[int, str]]:
    items = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = CHECKBOX.match(line)
        if match:
            text = match[3][2:].strip()
            if text and text != "...":  # template placeholders are not work
                items.append((match[2] != " ", text))
    return [(number, text) for number, (done, text) in enumerate(items, 1) if not done]


def untested(root: Path) -> list[str]:
    """Use cases whose **Test:** is neither covered nor NFV — advanced testing blocks a commit on them."""
    return [i for _, i, _, b in blocks(root) if i.startswith("UC-")
            and not field(b, "Test").lstrip("`*_ ").lower().startswith(("covered", "nfv"))]


HEALTH = ".devframework/ARCHITECTURE_HEALTHCHECK.md"
HEALTH_ROW = re.compile(r"^\| ([A-Z]+)-\d+ \|")
HEALTH_ROW_FULL = re.compile(r"^\| (([A-Z]+)-\d+) \| (.+) \| (.+) \|\s*$")
KE_HEADING = re.compile(
    r"^(#{2,4}) (KE-\d{4}-\d{2}-\d{2}-[A-Z0-9-]+)\s*[:：—–-]\s*(.*)$"
)
UC_HEADING = re.compile(r"^#{2,4} (UC-\d+)\s+[—-]\s+")
REQ_ROW = re.compile(r"^\| ((?:FR|NFR)-\d+) \|")
# Bold list-item definitions in docs/REQUIREMENTS.md (column 0 only; API-4).
# Forms: -/** * **<ID>:** | **<ID>**: | **<ID>** then space/—/-. Exact id (KE-2026-09-03-USECASE-PADDED-RANGE).
REQ_BULLET = re.compile(
    r"^[-*] \*\*((?:FR|NFR)-\d+)(?::\*\*|\*\*:|\*\*(?=[\s—–-]))"
)
# API-4 / KE-2026-09-03-USECASE-PADDED-RANGE: fenced examples are not id/entry sources.
# Opening: indent ≤ 3, then 3+ backticks or tildes; closing: same char, ≥ open length, no info string.
_FENCE_MARK = re.compile(r"^ {0,3}(`{3,}|~{3,})")


def _try_open_fence(line: str):
    """Return (char, min_len) if *line* opens a fence; else None."""
    match = _FENCE_MARK.match(line)
    if not match:
        return None
    run = match.group(1)
    return (run[0], len(run))


def _try_close_fence(line: str, fence: tuple) -> bool:
    """True when *line* closes the open fence (char, min_len)."""
    match = _FENCE_MARK.match(line)
    if not match:
        return False
    run = match.group(1)
    rest = line[match.end():]
    return run[0] == fence[0] and len(run) >= fence[1] and rest.strip() == ""


STATUS_LINE = re.compile(
    r"^\s*(?:- )?\*\*(?:Status|Статус):\*\*\s*(.*)$", re.IGNORECASE
)
DNR_LINE = re.compile(
    r"^\s*(?:- )?\*\*(?:Не возвращать|Do not return|Do not reintroduce):\*\*\s*(.*)$",
    re.IGNORECASE,
)
# Optional link line for area-based delivery (contract §3 --areas).
RULES_LINE = re.compile(
    r"^\s*(?:- )?\*\*(?:Rules|Areas):\*\*\s*(.*)$",
    re.IGNORECASE,
)
CAMEL_BOUND_1 = re.compile(r"(?<=[a-z])(?=[A-Z])")
CAMEL_BOUND_2 = re.compile(r"(?<=[A-Z])(?=[A-Z][a-z])")
# ke-lexical/1 stop words (ru+en, ≤ 80). Any change of this list bumps the algorithm name.
KE_STOP_WORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for", "of", "with",
    "by", "from", "as", "is", "are", "was", "were", "be", "been", "have", "has", "had",
    "do", "does", "did", "will", "can", "this", "that", "it", "its", "not", "no", "if",
    "when", "what", "which", "who", "how", "all", "any", "some", "also", "just", "only",
    "и", "в", "во", "не", "что", "он", "на", "я", "с", "со", "как", "а", "то", "все",
    "она", "так", "его", "но", "да", "ты", "к", "у", "же", "вы", "за", "бы", "по", "для",
    "мы", "они", "это", "или",
})
# Longest first. Required stems: тик/таймер/словар/минутн/tick/timer (ke-lexical/1).
RU_SUFFIXES = (
    "ого", "ему", "ыми", "ами", "ями", "иями", "ых", "их", "ой", "ый", "ий", "ая", "яя",
    "ое", "ее", "ые", "ие", "ую", "юю", "ом", "ем", "ам", "ям", "ах", "ях", "ов", "ев",
    "ей", "ью", "ии", "ье", "ья", "ьи", "ию", "ия", "а", "я", "у", "ю", "е", "ы", "и",
    "о", "ь",
)
EN_SUFFIXES = ("tion", "ness", "ment", "able", "ible", "ing", "ied", "ies", "ly", "ed", "es", "s")


class NavigateError(Exception):
    """Structured error for --json commands (exit 2)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def framework_version(root: Path) -> str:
    """Package version as recorded by the installer in `.devframework/manifest.json`."""
    path = root / ".devframework" / "manifest.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "0.0.0"
    version = data.get("version")
    if isinstance(version, str) and re.fullmatch(r"\d+\.\d+\.\d+", version):
        return version
    return "0.0.0"


def emit_json(obj: dict) -> None:
    # KE-2026-10-02-CONSOLE-ENCODING: UTF-8 bytes to stdout.buffer, never the locale text stream.
    raw = (
        json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        + b"\n"
    )
    buf = getattr(sys.stdout, "buffer", None)
    if buf is not None:
        buf.write(raw)
        buf.flush()
    else:
        sys.stdout.write(raw.decode("utf-8"))
        sys.stdout.flush()


def emit_error(root: Path, code: str, message: str) -> int:
    emit_json({
        "schema": "devframework.error/1",
        "framework_version": framework_version(root),
        "error": code,
        "message": message.splitlines()[0] if message else code,
    })
    return 2


def parse_health_rows(root: Path) -> list[dict]:
    text = read(root, HEALTH)
    if not text and not (root / HEALTH).is_file():
        raise NavigateError("missing_file", f"{HEALTH} is missing; run the installer with --update")
    rows = []
    fence = None  # (char, min_len) while inside a fenced example
    for line in text.splitlines():
        if fence is None:
            opened = _try_open_fence(line)
            if opened:
                fence = opened
                continue
            match = HEALTH_ROW_FULL.match(line)
            if not match:
                continue
            rows.append({
                "area": match[2],
                "id": match[1],
                "rule": match[3].strip(),
                "prevents": match[4].strip(),
            })
        elif _try_close_fence(line, fence):
            fence = None
    if not rows:
        raise NavigateError("missing_file", f"{HEALTH} is missing; run the installer with --update")
    return rows


def health_area_names(rows: list[dict]) -> list[str]:
    return list(dict.fromkeys(r["area"] for r in rows))


def _health_table_lines(text: str) -> list[str]:
    """Healthcheck table rows outside fenced examples (API-4)."""
    rows: list[str] = []
    fence = None
    for line in text.splitlines():
        if fence is None:
            opened = _try_open_fence(line)
            if opened:
                fence = opened
                continue
            if HEALTH_ROW.match(line):
                rows.append(line)
        elif _try_close_fence(line, fence):
            fence = None
    return rows


def health(root: Path, areas: str) -> str:
    """Only the healthcheck rows for the areas a task touches (SCOPE always): a few hundred tokens, not the table."""
    rows = _health_table_lines(read(root, HEALTH))
    if not rows:
        raise ValueError(f"{HEALTH} is missing; run the installer with --update")
    names = list(dict.fromkeys(HEALTH_ROW.match(r)[1] for r in rows))
    if not areas:
        return ("areas: " + " ".join(f"{n.lower()}({sum(HEALTH_ROW.match(r)[1] == n for r in rows)})" for n in names) +
                "\nusage: navigate.py health db,tx,api — prints SCOPE plus those areas")
    wanted = {"SCOPE"} | {a.strip().upper() for a in areas.split(",") if a.strip()}
    unknown = wanted - set(names)
    if unknown:
        raise ValueError(f"unknown area(s): {', '.join(sorted(unknown)).lower()}; known: {' '.join(names).lower()}")
    return "\n".join(r for r in rows if HEALTH_ROW.match(r)[1] in wanted)


def health_json(root: Path, areas: str) -> dict:
    rows = parse_health_rows(root)
    names = set(health_area_names(rows))
    wanted = {"SCOPE"} | {a.strip().upper() for a in areas.split(",") if a.strip()}
    unknown = wanted - names
    if unknown:
        raise NavigateError(
            "bad_args",
            f"unknown area(s): {', '.join(sorted(unknown)).lower()}; known: {' '.join(sorted(names)).lower()}",
        )
    filtered = [r for r in rows if r["area"] in wanted]
    return {
        "schema": "devframework.health/1",
        "framework_version": framework_version(root),
        "areas": sorted(wanted),
        "rows": filtered,
    }


def normalize_path(path: str) -> str:
    text = path.replace("\\", "/")
    while "//" in text:
        text = text.replace("//", "/")
    if text.startswith("./"):
        text = text[2:]
    return text


def path_wildcards_to_x(path: str) -> str:
    """Replace each wildcard run (`**`, `*`, `?`) with the single letter `x` (contract §2 rule 3)."""
    out: list[str] = []
    i = 0
    while i < len(path):
        if path.startswith("**", i):
            out.append("x")
            i += 2
        elif path[i] in "*?":
            out.append("x")
            i += 1
        else:
            out.append(path[i])
            i += 1
    return "".join(out)


def glob_to_regex(glob: str) -> re.Pattern:
    """Own glob→regex (not fnmatch / PurePath.match). `**/` may match nothing."""
    # cost: one compile per map entry per health --paths call (map is small, ≪ 100)
    parts: list[str] = ["^"]
    i = 0
    g = glob
    while i < len(g):
        if g.startswith("**/", i):
            parts.append("(?:.*/)?")
            i += 3
        elif g.startswith("**", i):
            parts.append(".*")
            i += 2
        elif g[i] == "*":
            parts.append("[^/]*")
            i += 1
        elif g[i] == "?":
            parts.append("[^/]")
            i += 1
        else:
            parts.append(re.escape(g[i]))
            i += 1
    parts.append("$")
    return re.compile("".join(parts))


def load_areas_map(root: Path, known_areas: set[str]) -> list[dict]:
    """Parse project.json `areas` map; missing key → empty (all paths unmapped). Malformed → bad_config."""
    path = root / ".devframework" / "project.json"
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise NavigateError("bad_config", f"project.json unreadable: {error}") from error
    if "areas" not in data:
        return []
    raw = data["areas"]
    if not isinstance(raw, list):
        raise NavigateError("bad_config", "areas map must be a list")
    entries: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            raise NavigateError("bad_config", "areas map entry must be an object")
        glob = item.get("glob")
        area_list = item.get("areas")
        if not isinstance(glob, str) or not glob:
            raise NavigateError("bad_config", "areas map entry needs a non-empty glob string")
        if not isinstance(area_list, list) or not area_list or not all(isinstance(a, str) for a in area_list):
            raise NavigateError("bad_config", "areas map entry needs a non-empty areas string list")
        normalized = [a.strip().upper() for a in area_list]
        if any(not a for a in normalized):
            raise NavigateError("bad_config", "areas map entry has an empty area name")
        unknown = set(normalized) - known_areas
        if unknown:
            raise NavigateError(
                "bad_config",
                f"unknown area(s) in areas map: {', '.join(sorted(unknown)).lower()}",
            )
        entries.append({"glob": glob, "areas": normalized, "regex": glob_to_regex(normalize_path(glob))})
    return entries


def health_paths_json(root: Path, paths: list[str]) -> dict:
    rows = parse_health_rows(root)
    known = set(health_area_names(rows))
    area_map = load_areas_map(root, known)
    # input order, duplicates removed
    ordered: list[str] = list(dict.fromkeys(paths))
    matched: list[dict] = []
    unmapped: list[str] = []
    union: set[str] = {"SCOPE"}
    for raw in ordered:
        norm = normalize_path(raw)
        probe = path_wildcards_to_x(norm) if any(c in norm for c in "*?") else norm
        hit_globs: list[str] = []
        hit_areas: set[str] = set()
        for entry in area_map:
            if entry["regex"].fullmatch(probe):
                hit_globs.append(entry["glob"])
                hit_areas.update(entry["areas"])
        if not hit_globs:
            unmapped.append(raw)
            continue
        matched.append({
            "path": raw,
            "globs": hit_globs,
            "areas": sorted(hit_areas),
        })
        union.update(hit_areas)
    filtered = [r for r in rows if r["area"] in union]
    return {
        "schema": "devframework.health/1",
        "framework_version": framework_version(root),
        "areas": sorted(union),
        "matched": matched,
        "unmapped": unmapped,
        "rows": filtered,
    }


def _is_cyrillic_token(token: str) -> bool:
    return any("А" <= ch <= "я" or ch in "Ёё" for ch in token)


def ke_tokenize(text: str) -> list[str]:
    """ke-lexical/1 tokenizer: NFKC, CamelCase split, lower, ё→е, non-alnum split, drop short/digits/stops."""
    if not text:
        return []
    normalized = unicodedata.normalize("NFKC", text)
    normalized = CAMEL_BOUND_1.sub(" ", normalized)
    normalized = CAMEL_BOUND_2.sub(" ", normalized)
    normalized = normalized.lower().replace("ё", "е")
    parts = re.split(r"[^0-9a-zа-я]+", normalized)
    out: list[str] = []
    for part in parts:
        if len(part) < 3 or part.isdigit() or part in KE_STOP_WORDS:
            continue
        out.append(part)
    return out


def ke_stem(token: str) -> str:
    """ke-lexical/1 stemmer: one suffix, longest first, ≥ 3 chars remain."""
    suffixes = RU_SUFFIXES if _is_cyrillic_token(token) else EN_SUFFIXES
    for suffix in suffixes:
        if token.endswith(suffix) and len(token) - len(suffix) >= 3:
            return token[: len(token) - len(suffix)]
    return token


def ke_stems(text: str) -> list[str]:
    return [ke_stem(t) for t in ke_tokenize(text)]


def path_query_text(path: str) -> str:
    """Wildcards and file extensions removed, then tokenized as query text."""
    text = normalize_path(path)
    text = re.sub(r"\*\*|\*|\?", " ", text)
    text = re.sub(r"\.[A-Za-z0-9]{1,8}\b", " ", text)
    text = text.replace("/", " ").replace(".", " ").replace("_", " ").replace("-", " ")
    return text


def parse_known_error_blocks(text: str) -> list[dict]:
    """Parse KE blocks (heading levels 2–4; `:` or em/en/`-` after the id).

    Headings inside Markdown fences are not entries (API-4). A fence inside a real
    block stays in that block's ``block`` text; only heading detection skips it.
    """
    lines = text.splitlines()
    blocks: list[dict] = []
    i = 0
    fence = None  # (char, min_len) while inside a fence
    while i < len(lines):
        line = lines[i]
        if fence is not None:
            if _try_close_fence(line, fence):
                fence = None
            i += 1
            continue
        opened = _try_open_fence(line)
        if opened:
            fence = opened
            i += 1
            continue
        match = KE_HEADING.match(line)
        if not match:
            i += 1
            continue
        level = len(match[1])
        ke_id = match[2]
        title = (match[3] or "").strip()
        start = i
        i += 1
        body_lines: list[str] = []
        # cost: one linear pass per known-errors document; fence state is O(1)
        while i < len(lines):
            body_line = lines[i]
            if fence is None:
                opened = _try_open_fence(body_line)
                if opened:
                    fence = opened
                    body_lines.append(body_line)
                    i += 1
                    continue
                next_h = re.match(r"^(#{1,%d}) " % level, body_line)
                if next_h:
                    break
                body_lines.append(body_line)
                i += 1
            else:
                if _try_close_fence(body_line, fence):
                    fence = None
                body_lines.append(body_line)
                i += 1
        status = ""
        do_not_return = ""
        rules_raw = ""
        remaining: list[str] = []
        for body_line in body_lines:
            sm = STATUS_LINE.match(body_line)
            dm = DNR_LINE.match(body_line)
            rm = RULES_LINE.match(body_line)
            if sm and not status:
                status = sm[1].strip()
            elif dm and not do_not_return:
                do_not_return = dm[1].strip()
            elif rm and not rules_raw:
                rules_raw = rm[1].strip()
            else:
                remaining.append(body_line)
        block_md = "\n".join(lines[start:i])
        blocks.append({
            "id": ke_id,
            "title": title,
            "status": status,
            "do_not_return": do_not_return,
            "rules_raw": rules_raw,
            "body": "\n".join(remaining).strip(),
            "block": block_md,
            "title_stems": set(ke_stems(title)),
            "dnr_stems": set(ke_stems(do_not_return)),
            "body_stems": set(ke_stems("\n".join(remaining))),
        })
    return blocks


def _rules_line_tokens(raw: str) -> list[str]:
    if not raw:
        return []
    return [p for p in re.split(r"[,;\s]+", raw.strip()) if p]


def find_health_rule_ids_in_text(text: str, known_ids: set[str]) -> list[str]:
    """Exact rule-id matches only (KE-2026-09-03: JOB-1 must not link JOB-10; JOB-1x is not an id)."""
    if not text or not known_ids:
        return []
    # Longest first so JOB-10 wins over JOB-1 when both exist.
    ordered = sorted(known_ids, key=len, reverse=True)
    pattern = re.compile(r"(?<![A-Za-z0-9-])(?:" + "|".join(re.escape(i) for i in ordered) + r")(?![A-Za-z0-9-])")
    found: list[str] = []
    seen: set[str] = set()
    for match in pattern.finditer(text):
        rid = match.group(0)
        if rid not in seen:
            seen.add(rid)
            found.append(rid)
    return found


def block_area_links(
    block: dict,
    wanted_areas: set[str],
    id_to_area: dict[str, str],
    known_ids: set[str],
    known_areas: set[str],
) -> list[str]:
    """Rule ids / area names that link *block* to any of *wanted_areas* (sorted)."""
    links: set[str] = set()
    for rid in find_health_rule_ids_in_text(block["block"], known_ids):
        area = id_to_area.get(rid)
        if area in wanted_areas:
            links.add(rid)
    for token in _rules_line_tokens(block.get("rules_raw", "")):
        upper = token.upper()
        if token in known_ids and id_to_area.get(token) in wanted_areas:
            links.add(token)
        elif upper in known_areas and upper in wanted_areas:
            links.add(upper)
    return sorted(links)


def load_known_errors(root: Path) -> list[dict]:
    path = root / "docs" / "KNOWN_ERRORS.md"
    if not path.is_file():
        # API-7, DB-5: missing file is an error, never an empty result.
        raise NavigateError("missing_file", "docs/KNOWN_ERRORS.md is missing")
    return parse_known_error_blocks(path.read_text(encoding="utf-8"))


def known_errors_json(
    root: Path,
    *,
    terms: list[str] | None = None,
    text_file: Path | None = None,
    paths: list[str] | None = None,
    ids: list[str] | None = None,
    areas: str | None = None,
    limit: int = 10,
    full: bool = False,
) -> dict:
    has_lexical = bool(terms) or text_file is not None or bool(paths)
    # Modes are mutually exclusive: --ids | --areas | lexical (--terms/--text-file/--paths).
    if ids is not None and (has_lexical or areas is not None):
        raise NavigateError("bad_args", "--ids cannot be combined with --terms/--text-file/--paths/--areas")
    if areas is not None and (has_lexical or ids is not None):
        raise NavigateError("bad_args", "--areas cannot be combined with --ids/--terms/--text-file/--paths")
    blocks = load_known_errors(root)
    version = framework_version(root)
    if ids is not None:
        by_id = {b["id"]: b for b in blocks}
        seen: set[str] = set()
        candidates = []
        for ke_id in ids:
            if ke_id in seen:
                continue
            seen.add(ke_id)
            block = by_id.get(ke_id)
            if block is None:
                continue
            item = {
                "id": block["id"],
                "title": block["title"],
                "status": block["status"],
                "do_not_return": block["do_not_return"],
                "score": 0.0,
                "matched": [],
            }
            if full:
                item["block"] = block["block"]
            candidates.append(item)
        return {
            "schema": "devframework.known-errors/1",
            "framework_version": version,
            "algorithm": "ke-lexical/1",
            "total": len(blocks),
            "candidates": candidates,
        }

    if areas is not None:
        rows = parse_health_rows(root)
        known_areas = set(health_area_names(rows))
        wanted = {a.strip().upper() for a in areas.split(",") if a.strip()}
        if not wanted:
            raise NavigateError("bad_args", "--areas requires at least one area")
        unknown = wanted - known_areas
        if unknown:
            raise NavigateError(
                "bad_args",
                f"unknown area(s): {', '.join(sorted(unknown)).lower()}; "
                f"known: {' '.join(sorted(known_areas)).lower()}",
            )
        id_to_area = {r["id"]: r["area"] for r in rows}
        known_ids = set(id_to_area)
        # cost: one pass over N KE blocks × known rule ids; N≲200, ids≲80 per orchestration brief
        candidates = []
        for block in blocks:
            links = block_area_links(block, wanted, id_to_area, known_ids, known_areas)
            if not links:
                continue
            item = {
                "id": block["id"],
                "title": block["title"],
                "status": block["status"],
                "do_not_return": block["do_not_return"],
                "score": 0.0,
                "matched": [],
                "links": links,
            }
            if full:
                item["block"] = block["block"]
            candidates.append(item)
        return {
            "schema": "devframework.known-errors/1",
            "framework_version": version,
            "algorithm": "ke-lexical/1",
            "total": len(blocks),
            "candidates": candidates,
        }

    # cost: one pass over N KE blocks × Q query stems; N≲200, Q≲100 per orchestration brief
    query: list[str] = []
    for term in terms or []:
        query.extend(ke_stems(term))
    if text_file is not None:
        try:
            query.extend(ke_stems(text_file.read_text(encoding="utf-8")))
        except OSError as error:
            raise NavigateError("missing_file", f"cannot read --text-file: {error}") from error
    for path in paths or []:
        query.extend(ke_stems(path_query_text(path)))
    query_stems = list(dict.fromkeys(query))

    n = len(blocks)
    df: dict[str, int] = {}
    for stem in query_stems:
        count = 0
        for block in blocks:
            if stem in block["title_stems"] or stem in block["dnr_stems"] or stem in block["body_stems"]:
                count += 1
        df[stem] = count

    def idf(stem: str) -> float:
        return math.log((n + 1) / (df[stem] + 1)) + 1

    scored = []
    for block in blocks:
        score = 0.0
        matched: list[str] = []
        for stem in query_stems:
            in_title = stem in block["title_stems"]
            in_dnr = stem in block["dnr_stems"]
            in_body = stem in block["body_stems"]
            if not (in_title or in_dnr or in_body):
                continue
            weight = 3 * int(in_title) + 3 * int(in_dnr) + 1 * int(in_body)
            score += idf(stem) * weight
            matched.append(stem)
        score = round(score, 4)
        if score <= 0:
            continue
        item = {
            "id": block["id"],
            "title": block["title"],
            "status": block["status"],
            "do_not_return": block["do_not_return"],
            "score": score,
            "matched": sorted(matched),
        }
        if full:
            item["block"] = block["block"]
        scored.append(item)
    scored.sort(key=lambda c: (-c["score"], c["id"]))
    if limit < 0:
        raise NavigateError("bad_args", "--limit must be >= 0")
    return {
        "schema": "devframework.known-errors/1",
        "framework_version": version,
        "algorithm": "ke-lexical/1",
        "total": n,
        "candidates": scored[:limit],
    }


def collect_project_ids(root: Path) -> dict[str, str]:
    """Map id → kind for ids --check (exact headings/rows only; no range expansion)."""
    found: dict[str, str] = {}
    ke_path = root / "docs" / "KNOWN_ERRORS.md"
    if ke_path.is_file():
        for block in parse_known_error_blocks(ke_path.read_text(encoding="utf-8")):
            found.setdefault(block["id"], "known_error")
    try:
        for row in parse_health_rows(root):
            found.setdefault(row["id"], "health_rule")
    except NavigateError:
        pass
    uc_path = root / "docs" / "USE_CASES.md"
    if uc_path.is_file():
        fence = None
        for line in uc_path.read_text(encoding="utf-8").splitlines():
            if fence is None:
                opened = _try_open_fence(line)
                if opened:
                    fence = opened
                    continue
                match = UC_HEADING.match(line)
                if match:
                    found.setdefault(match[1], "use_case")
            elif _try_close_fence(line, fence):
                fence = None
    req_path = root / "docs" / "REQUIREMENTS.md"
    if req_path.is_file():
        # cost: one linear pass over REQUIREMENTS.md; table + bullet defs (BULK-2)
        fence = None
        for line in req_path.read_text(encoding="utf-8").splitlines():
            if fence is None:
                opened = _try_open_fence(line)
                if opened:
                    fence = opened
                    continue
                match = REQ_ROW.match(line) or REQ_BULLET.match(line)
                if match:
                    found.setdefault(match[1], "requirement")
            elif _try_close_fence(line, fence):
                fence = None
    return found


def ids_check_json(root: Path, id_list: list[str]) -> dict:
    catalog = collect_project_ids(root)
    known: list[dict] = []
    unknown: list[str] = []
    seen: set[str] = set()
    for ident in id_list:
        if ident in seen:
            continue
        seen.add(ident)
        kind = catalog.get(ident)
        if kind is None:
            unknown.append(ident)
        else:
            known.append({"id": ident, "kind": kind})
    return {
        "schema": "devframework.ids/1",
        "framework_version": framework_version(root),
        "known": known,
        "unknown": unknown,
    }


def doctor_line(root: Path) -> str:
    try:
        from verification import doctor
        report = doctor(root)
    except Exception as error:  # doctor itself is the authority; the brief only summarises it
        return f"doctor: unavailable ({error})"
    wip = report.get("under_construction") or []
    note = f" · under construction: {', '.join(wip)}" if wip else ""
    if report["errors"]:
        more = f" (+{len(report['errors']) - 1})" if len(report["errors"]) > 1 else ""
        return f"doctor: STRUCTURE FAILED — {report['errors'][0]}{more}{note}"
    if not report["ready"]:
        return f"doctor: NOT READY — {len(report['setup'])} setup items, first: {report['setup'][0]}{note}"
    return f"doctor: READY (structure/configuration; tests not run — run `check.py finish`){note}"


def brief(root: Path) -> str:
    out = []
    title = next((line[2:] for line in read(root, "PROJECT.md").splitlines() if line.startswith("# ")), root.name)
    out.append(f"# {title} — session brief ({date.today().isoformat()})")
    try:
        from verification import installed_kind
        out.append(f"kind: {installed_kind(root)} (product / tool / explore — decides the documents and the proof)")
        import json
        testing = json.loads(read(root, ".devframework/project.json") or "{}").get("testing") or "not set"
        out.append(f"testing: {testing} (lean / advanced — AGENTS.md §4 says what each demands)")
        from verification import testing_note
        note = testing_note(root)
        if note:
            out.append("  ⚠ " + note)
    except Exception:  # an older verification.py next to this file: the brief still works without the line
        pass
    out.append(doctor_line(root))
    try:
        import hostcheck
        unresolved, resolved = hostcheck.report(root)
        if unresolved or resolved:
            out.append(f"host rules: {len(unresolved)} unresolved conflict(s), {len(resolved)} decided — `python .devframework/hostcheck.py` for the quotes and fixes")
    except Exception as error:
        out.append(f"host rules: check unavailable ({error})")
    branch = git(root, "symbolic-ref", "--short", "HEAD") or git(root, "rev-parse", "--abbrev-ref", "HEAD")  # works on an unborn branch too
    if branch:
        dirty = git(root, "status", "--porcelain")
        out.append(f"git: {branch}, {len(dirty.splitlines())} changed file(s); last: "
                   + (" | ".join(git(root, "log", "-3", "--format=%h %s").splitlines()) or "no commits yet"))
        out.append(freshness(root))
    else:
        out.append("git: no repository (finish/commit-check need one)")
    uc = [(i, t, field(b, "Test")) for _, i, t, b in blocks(root) if i.startswith("UC-")]
    gaps = [i for i, _, test in uc if not test.lstrip("`*_ ").lower().startswith("covered")]  # markdown emphasis allowed
    out.append(f"use cases: {len(uc)}" + (f", without covering test: {', '.join(gaps[:8])}" + (" …" if len(gaps) > 8 else "") if gaps else ""))
    ke = [(i, t) for r, i, t, b in blocks(root) if i.startswith("KE-") and not field(b, "Status").lower().startswith("fixed")]
    out.append(f"open known errors: {len(ke)}" + ("".join(f"\n  {i} — {t}" for i, t in ke[:6]) if ke else ""))
    progress = [row for row in read(root, "docs/BACKLOG.md").splitlines()
                if row.startswith("| ") and ("| in progress" in row.lower() or "| todo" in row.lower())]
    if progress:
        out.append("backlog in progress: " + "; ".join(r.strip("| ").split(" | ")[0] for r in progress[:4]))
    active = active_checklists(root)
    for path in active:
        items = open_items(path)
        out.append(f"checklist docs/{path.name}: {len(items)} open" + ("".join(f"\n  [{n}] {t}" for n, t in items[:6]) if items else " — all done, archive it"))
        text = path.read_text(encoding="utf-8")
        match = re.search(r"^## Handoff.*?$\n(.*?)(?=^## |\Z)", text, re.M | re.S)
        if match:
            lines = [l.strip().lstrip("- ") for l in match[1].splitlines() if ":" in l and not l.strip().endswith(":")]
            if lines:
                out.append("  handoff: " + " / ".join(lines[:4]))
    if not active:
        out.append("checklist: none active (for multi-iteration work: `navigate.py checklist new <slug> \"<title>\"`)")
    out.append("next: read documents only where this brief points; `navigate.py find <ID|keyword>` returns one block, not a file.")
    return "\n".join(out)


def find(root: Path, query: str, limit: int = 40) -> str:
    query_l = query.lower()
    hits = []
    for relative, ident, title, body in blocks(root):
        if ident.lower() == query_l:
            lines = "\n".join(body).strip().splitlines()
            return f"{relative}: {ident} — {title}\n" + "\n".join(lines[:limit]) + ("\n… (block truncated; open the file for the rest)" if len(lines) > limit else "")
        if query_l in ident.lower() or query_l in title.lower():
            hits.append(f"{ident} — {title} ({relative})")
    for relative in DOCS:
        for number, line in enumerate(read(root, relative).splitlines(), 1):
            if query_l in line.lower() and not HEADING.match(line):
                hits.append(f"{relative}:{number}: {line.strip()[:120]}")
    if not hits:
        return f"no match for {query!r} in project documents"
    return "\n".join(hits[:limit]) + (f"\n… {len(hits) - limit} more" if len(hits) > limit else "")


def index(root: Path) -> Path:
    rows: dict[str, list[str]] = {"UC": [], "KE": [], "FR": [], "INV": []}
    for relative, ident, title, body in blocks(root):
        kind = ident.split("-", 1)[0]
        kind = "FR" if kind == "NFR" else kind
        if kind == "SET":
            continue
        extra = field(body, "Test") if kind == "UC" else field(body, "Status") if kind == "KE" else ""
        rows.setdefault(kind, []).append(f"| {ident} | {title} | {extra[:80]} |")
    lines = ["# Index — generated by `.devframework/navigate.py index` (finish regenerates it; do not edit)", ""]
    for kind, header in (("UC", "test"), ("KE", "status"), ("FR", "-"), ("INV", "-")):
        if rows.get(kind):
            lines += [f"## {kind}", f"| id | title | {header} |", "|---|---|---|", *rows[kind], ""]
    path = root / "docs" / "INDEX.md"
    write(path, "\n".join(lines).rstrip("\n") + "\n")
    return path


CONTRACT = """\
DEV Framework contract (what the gates check). Details: .devframework/VERIFICATION.md; rules: AGENTS.md.
wip     = `<!-- under-construction: reason (until YYYY-MM-DD) -->` in PROJECT.md or docs/: doctor skips that
          document's checks and names it every run; never secrets, tests or file presence; expires on the date.
health  = `navigate.py health db,api,...` before designing or debugging; name the rule ids in the plan.
testing = `testing` in project.json: lean (one scenario test per user promise, suite < 1 min) or advanced
          (unit + integration + e2e, staging; commit-check refuses a UC `gap`); AGENTS.md §4.
kind    = product (this contract). A tool or an exploration prints its own; promote with --update --kind.
doctor  = structure + configuration. READY needs: every framework file present; PROJECT.md and docs/ARCHITECTURE.md
          without `TODO(project):`; .devframework/project.json with a reviewed `test` argv (build may be null with a
          `build_not_applicable` text); docs/USE_CASES.md with >= 1 `#### UC-### — title` heading, each with a
          `**Test:**` field and a row in the traceability table; SET/UC citations only to existing ids.
finish  = doctor + worktree secret heuristic + build (if any) + test + extra checks; argv form, no shell; needs git.
          The test command prints `TESTS: total=N failed=F skipped=S` (run_unittest.py does); > 0 run, none failed.
          `--jobs auto` gives it one process per test module. It discovers unittest-style tests (TestCase
          classes) only. Plain pytest functions: run_pytest.py (same line; pytest must be installed).
          Prints the counts and FINISH PASSED; regenerates docs/INDEX.md.
commit-check = finish + staged secret scan. Only before an authorized commit.
Documents: PROJECT.md (facts) · docs/USE_CASES.md (value paths, stable UC-### ids, Test: covered | gap | not
          functionally verifiable) · docs/KNOWN_ERRORS.md (KE-YYYY-MM-DD-SLUG, Status: not fixed | fixed in …) ·
          docs/BACKLOG.md · docs/REQUIREMENTS.md (FR-/NFR-) · docs/ARCHITECTURE.md · docs/INVARIANTS.md ·
          docs/REGRESSION_TEST.md · docs/RELEASE.md. Multi-iteration work: one checklist in docs/ (Status: in progress).
Navigation: navigate.py brief | find <ID|keyword> | index | checklist new|add|tick|show|archive | handoff <slug>.
JSON (1.7+): health --json [areas] | health --paths … --json | known-errors --json [--areas A,B | --ids | lexical]
          | ids --check … --json (schema-versioned; text without --json stays byte-identical to 1.6.x).
          project.json may hold an `areas` map (glob → health areas) for health --paths; known-errors --areas
          delivers KEs linked to those health areas (Rules/Areas line or cited rule id).
Rules of thumb: a statement in a document is read from code/tests or marked unconfirmed; tests use temp fixtures
only; no commit/push without authorization; finish is evidence for the worktree, never for a commit."""


def checklist(root: Path, action: str, slug: str = "", text: str = "") -> str:
    if action == "show":
        active = active_checklists(root)
        if not active:
            return "no active checklist"
        return "\n".join(f"docs/{p.name}: " + (", ".join(f"[{n}] {t}" for n, t in open_items(p)) or "all done") for p in active)
    if not slug:
        raise ValueError("checklist <new|add|tick|archive> needs a slug (file docs/<slug>.md)")
    path = resolve_docs_slug(root, slug)
    if action == "new":
        if path.exists():
            raise ValueError(f"{path.name} already exists")
        template = read(root, "docs/CHECKLIST_TEMPLATE.md").split("\n---\n", 1)[-1]
        body = template.replace("<TOPIC> — <one line saying what this programme achieves>",
                                f"{slug} — {text or 'TODO: one line saying what this programme achieves'}")
        body = body.replace("**Started:** YYYY-MM-DD", f"**Started:** {date.today().isoformat()}")
        body = "\n".join(l for l in body.splitlines() if l.strip() != "- [ ] ...")
        docs_dir(root).mkdir(parents=True, exist_ok=True)
        write(path, body.lstrip() + "\n")
        return f"created docs/{path.name}; add items with: checklist add {slug} \"<item>\""
    if not path.is_file():
        raise ValueError(f"no checklist docs/{slug}.md")
    lines = path.read_text(encoding="utf-8").splitlines()
    boxes = [i for i, l in enumerate(lines) if CHECKBOX.match(l) and l.split("] ", 1)[-1].strip() not in ("", "...")]
    if action == "add":
        if not text:
            raise ValueError("checklist add needs the item text")
        lines.insert(boxes[-1] + 1 if boxes else len(lines), f"- [ ] {text}")
        write(path, "\n".join(lines) + "\n")
        return f"added item {len(boxes) + 1}: {text}"
    if action == "tick":
        number = int(text)
        if not 1 <= number <= len(boxes):
            raise ValueError(f"item {number} does not exist (1..{len(boxes)})")
        i = boxes[number - 1]
        lines[i] = CHECKBOX.sub(lambda m: m[1] + "x" + m[3], lines[i])
        write(path, "\n".join(lines) + "\n")
        remaining = open_items(path)
        return f"ticked [{number}]; {len(remaining)} open" + (": " + "; ".join(t for _, t in remaining[:5]) if remaining
                                                             else f" — all done, run: checklist archive {slug}")
    if action == "archive":
        if open_items(path):
            raise ValueError("open items remain; archive only a finished checklist")
        archive_dir = resolve_under_docs(root, "archive")
        archive_dir.mkdir(exist_ok=True)
        dest = resolve_under_docs(root, "archive", path.name)
        content = "\n".join(lines).replace("**Status:** in progress", f"**Status:** done {date.today().isoformat()}", 1)
        write(dest, content + "\n")
        path.unlink()
        return f"archived to docs/archive/{path.name}"
    raise ValueError(f"unknown checklist action {action!r}")


def handoff(root: Path, slug: str, note: str) -> str:
    path = resolve_docs_slug(root, slug)
    if not path.is_file():
        raise ValueError(f"no checklist docs/{slug}.md")
    items = open_items(path)
    block = [f"### {date.today().isoformat()} (written by navigate.py handoff)",
             f"- Branch/commit: {git(root, 'rev-parse', '--abbrev-ref', 'HEAD') or 'no git'} @ "
             f"{git(root, 'rev-parse', '--short', 'HEAD') or '-'}; uncommitted: {len(git(root, 'status', '--porcelain').splitlines())} file(s)",
             f"- Open items: {len(items)}" + (f"; next: [{items[0][0]}] {items[0][1]}" if items else " — programme finished"),
             f"- {doctor_line(root)}",
             f"- Note: {note or '(none given)'}"]
    text = path.read_text(encoding="utf-8")
    marker = re.search(r"^## Handoff.*$", text, re.M)
    if marker:
        text = text[:marker.end()] + "\n\n" + "\n".join(block) + "\n" + text[marker.end():]
    else:
        text += "\n## Handoff\n\n" + "\n".join(block) + "\n"
    write(path, text)
    return "\n".join(block)


KIND_CONTRACT = {
    "tool": """\
DEV Framework contract for a TOOL (what the gates check). Rules: AGENTS.md.
wip     = `<!-- under-construction: reason (until YYYY-MM-DD) -->` in PROJECT.md or docs/: doctor skips that
          document's checks and names it every run; never secrets, tests or file presence; expires on the date.
health  = `navigate.py health db,api,...` before designing or debugging; name the rule ids in the plan.
testing = `testing` in project.json: lean (one scenario test per user promise, suite < 1 min) or advanced
          (unit + integration + e2e, staging; commit-check refuses a UC `gap`); AGENTS.md §4.
doctor  = structure + configuration. READY needs: PROJECT.md and docs/GUIDE.html without `TODO(project):`
          (the guide is the user's instruction: install, run, examples, errors, limits); 1-3 `smoke` examples in
          .devframework/project.json: {"name", "run": [argv, "{python}" allowed], "stdin"?, "expect_stdout": file |
          "expect_contains": text, "expect_exit"?}. No use cases, backlog or regression plan.
finish  = doctor + secret heuristic + run_smoke.py (the tool run on every example = its tests); needs git.
          A wrong example fails the gate.
commit-check = finish + staged secret scan. Only before an authorized commit.
Grow into a product: install.py --update --kind product --scale "<target>" (adds documents, overwrites none).""",
    "explore": """\
DEV Framework contract for an EXPLORATION (what the gates check). Rules: AGENTS.md.
wip     = `<!-- under-construction: reason (until YYYY-MM-DD) -->` in PROJECT.md or docs/: doctor skips that
          document's checks and names it every run; never secrets, tests or file presence; expires on the date.
health  = `navigate.py health db,api,...` before designing or debugging; name the rule ids in the plan.
testing = `testing` in project.json: lean (one scenario test per user promise, suite < 1 min) or advanced
          (unit + integration + e2e, staging; commit-check refuses a UC `gap`); AGENTS.md §4.
doctor  = structure + configuration. READY needs: PROJECT.md without `TODO(project):` (intent, open questions).
finish  = doctor + secret heuristic + tests if a test command is configured; without one it passes and says
          plainly that behaviour is NOT proven. Record what you learn, dated, under "What we learned".
commit-check = finish + staged secret scan. Only before an authorized commit.
Grow: install.py --update --kind tool, or --kind product --scale "<target>" (adds documents, overwrites none).""",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=HERE.parent)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("brief")
    f = sub.add_parser("find"); f.add_argument("query")
    sub.add_parser("index")
    sub.add_parser("contract")
    hc = sub.add_parser("health", help="architecture healthcheck rows for the areas a task touches")
    hc.add_argument("areas", nargs="?", default="", help="comma-separated, e.g. db,tx,api (SCOPE always)")
    hc.add_argument("--json", action="store_true", help="machine-readable envelope (schema devframework.health/1)")
    hc.add_argument("--paths", nargs="+", metavar="P", help="derive areas from project.json areas map")
    ke = sub.add_parser("known-errors", help="candidate known errors (ke-lexical/1)")
    ke.add_argument("--json", action="store_true", help="required for machine-readable output")
    ke.add_argument("--terms", nargs="+", metavar="T", default=None)
    ke.add_argument("--text-file", type=Path, default=None)
    ke.add_argument("--paths", nargs="+", metavar="P", default=None)
    ke.add_argument("--ids", nargs="+", metavar="ID", default=None)
    ke.add_argument("--areas", default=None, help="comma-separated health areas (link mode, no ranking)")
    ke.add_argument("--limit", type=int, default=10)
    ke.add_argument("--full", action="store_true")
    ids_p = sub.add_parser("ids", help="resolve project identifiers")
    ids_p.add_argument("--check", nargs="+", metavar="ID", dest="check_ids", default=None)
    ids_p.add_argument("--json", action="store_true")
    c = sub.add_parser("checklist"); c.add_argument("action", choices=["new", "add", "tick", "show", "archive"])
    c.add_argument("slug", nargs="?", default=""); c.add_argument("text", nargs="?", default="")
    h = sub.add_parser("handoff"); h.add_argument("slug"); h.add_argument("--note", default="")
    args = parser.parse_args()
    root = args.root.resolve()
    want_json = bool(getattr(args, "json", False) or args.command in ("known-errors", "ids"))
    if not want_json:
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        if args.command == "brief":
            print(brief(root))
        elif args.command == "find":
            print(find(root, args.query))
        elif args.command == "index":
            print(f"INDEX written: {index(root).relative_to(root).as_posix()}")
        elif args.command == "contract":
            from verification import installed_kind
            print(KIND_CONTRACT.get(installed_kind(root), CONTRACT))
        elif args.command == "health":
            if args.paths is not None and not args.json:
                raise NavigateError("bad_args", "health --paths requires --json")
            if args.paths is not None and args.areas:
                raise NavigateError("bad_args", "health --paths cannot be combined with positional areas")
            if args.json:
                if args.paths is not None:
                    emit_json(health_paths_json(root, args.paths))
                else:
                    emit_json(health_json(root, args.areas))
            else:
                print(health(root, args.areas))
        elif args.command == "known-errors":
            if not args.json:
                raise NavigateError("bad_args", "known-errors requires --json")
            emit_json(known_errors_json(
                root,
                terms=args.terms,
                text_file=args.text_file,
                paths=args.paths,
                ids=args.ids,
                areas=args.areas,
                limit=args.limit,
                full=args.full,
            ))
        elif args.command == "ids":
            if not args.json:
                raise NavigateError("bad_args", "ids requires --json")
            if not args.check_ids:
                raise NavigateError("bad_args", "ids requires --check ID [ID ...]")
            emit_json(ids_check_json(root, args.check_ids))
        elif args.command == "checklist":
            print(checklist(root, args.action, args.slug, args.text))
        elif args.command == "handoff":
            print(handoff(root, args.slug, args.note))
    except NavigateError as error:
        if want_json or args.command in ("health", "known-errors", "ids"):
            return emit_error(root, error.code, error.message)
        print(f"ERROR: {error.message}")
        return 1
    except ValueError as error:
        if want_json:
            return emit_error(root, "bad_args", str(error))
        print(f"ERROR: {error}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
