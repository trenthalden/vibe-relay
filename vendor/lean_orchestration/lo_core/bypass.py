"""Bypass matcher — refuse terminal/execute_code that starts a configured executor (FR-013).

Pure function: no I/O. Protection against accidental bypass, not a sandbox
(KE-2026-11-20-BRIEF-RULES-SKIPPED). One strict matcher grammar (API-4).
"""
from __future__ import annotations

import ast
import os
import re
from typing import Iterable, List, Optional, Sequence, Set, Tuple

_SAFE_FLAGS = frozenset({"--version", "--help", "-h", "-V"})
_SHELLS = frozenset({"bash", "sh", "zsh"})
_WRAPPERS_SIMPLE = frozenset({"exec", "nohup", "time", "command"})
# Basename match so /usr/bin/env and /usr/bin/nohup work.
_PY_BASENAME = re.compile(r"^python(\d+(\.\d+)*)?$")
# Flag-like words for interpreters / wrappers (single -x or --long).
_FLAG_TOKEN = re.compile(r"^-")


def command_starts_executor(
    text: str,
    binary_names: Sequence[str],
    launcher_scripts: Sequence[str],
    _depth: int = 0,
) -> Optional[str]:
    """Return the matched token if ``text`` launches a configured executor; else None.

    Command position: start, after ``;`` ``&&`` ``||`` ``|`` newline ``&`` ``(`` ``$(``
    backtick, or after recognised wrappers / interpreters. Leading ``NAME=value``
    tokens (any count) are skipped at every command position, before and after
    wrappers (API-4; KE-2026-11-20-BRIEF-RULES-SKIPPED). Allowed: ``<binary>
    --version`` / ``--help``; non-command mentions (``echo cursor-agent``, ``grep …``).
    """
    if _depth > 3:
        return None
    names = _name_list(binary_names, launcher_scripts)
    if not text or not names:
        return None
    launcher_bases = _basename_set(launcher_scripts)
    binary_bases = _basename_set(binary_names)
    for segment in _command_segments(text):
        hit = _match_tokens(
            _tokenize(segment),
            names,
            binary_bases,
            launcher_bases,
            binary_names,
            launcher_scripts,
            _depth,
        )
        if hit:
            return hit
    return None


def code_starts_executor(
    code: str,
    binary_names: Sequence[str],
    launcher_scripts: Sequence[str],
) -> Optional[str]:
    """Match execute_code payloads: text grammar + AST string constants.

    Nested ``terminal()`` calls are covered by the ``pre_tool_call`` hook on
    ``terminal`` itself; this function still scans string constants that name
    binaries / launchers or embed a shell launch command.
    """
    hit = command_starts_executor(code, binary_names, launcher_scripts)
    if hit:
        return hit
    names = _name_list(binary_names, launcher_scripts)
    if not code or not names:
        return None
    binary_bases = _basename_set(binary_names)
    launcher_bases = _basename_set(launcher_scripts)
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        value = _ast_str(node)
        if value is None:
            continue
        hit = _string_constant_hit(
            value, names, binary_bases, launcher_bases, binary_names, launcher_scripts
        )
        if hit:
            return hit
    return None


def is_bypass_command(
    text: str,
    binary_names: Sequence[str],
    launcher_scripts: Sequence[str],
) -> bool:
    return command_starts_executor(text, binary_names, launcher_scripts) is not None


def _name_list(binary_names: Sequence[str], launcher_scripts: Sequence[str]) -> List[str]:
    names = [n for n in list(binary_names) + list(launcher_scripts) if n]
    # Longest first so ``cursor-agent`` wins over ``cursor``.
    return sorted(set(names), key=lambda s: (-len(s), s))


def _basename_set(items: Sequence[str]) -> Set[str]:
    out: Set[str] = set()
    for item in items:
        s = str(item).strip()
        if not s:
            continue
        out.add(s)
        out.add(os.path.basename(s))
    return out


def _ast_str(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _string_constant_hit(
    value: str,
    names: Sequence[str],
    binary_bases: Set[str],
    launcher_bases: Set[str],
    binary_names: Sequence[str],
    launcher_scripts: Sequence[str],
) -> Optional[str]:
    base = os.path.basename(value)
    for name in names:
        name_base = os.path.basename(name)
        if value == name or base == name or base == name_base:
            return name
    if base in binary_bases or base in launcher_bases:
        return base
    # String that itself matches as a shell command.
    return command_starts_executor(value, binary_names, launcher_scripts)


def _command_segments(text: str) -> List[str]:
    """Split on command separators while respecting quotes (API-4)."""
    parts: List[str] = []
    buf: List[str] = []
    in_single = False
    in_double = False
    i = 0
    n = len(text)

    def flush() -> None:
        piece = "".join(buf).strip()
        buf[:] = []
        if piece:
            parts.append(piece)

    while i < n:
        ch = text[i]
        if ch == "'" and not in_double:
            in_single = not in_single
            buf.append(ch)
            i += 1
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            buf.append(ch)
            i += 1
            continue
        if not in_single and not in_double:
            # Separators: newline, ;, &&, ||, |, &, `, $(, (
            if ch == "\n" or ch == "\r" or ch == ";":
                flush()
                i += 1
                continue
            if ch == "&":
                if i + 1 < n and text[i + 1] == "&":
                    flush()
                    i += 2
                    continue
                flush()
                i += 1
                continue
            if ch == "|":
                if i + 1 < n and text[i + 1] == "|":
                    flush()
                    i += 2
                    continue
                flush()
                i += 1
                continue
            if ch == "`":
                flush()
                i += 1
                continue
            if ch == "$" and i + 1 < n and text[i + 1] == "(":
                flush()
                i += 2
                continue
            if ch == "(":
                flush()
                i += 1
                continue
        buf.append(ch)
        i += 1
    flush()
    return parts


_ASSIGN_TOKEN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=(.*)$")


def _is_assignment_token(token: str) -> bool:
    """True for shell ``NAME=value`` (value may be empty or contain spaces from quotes)."""
    return bool(_ASSIGN_TOKEN.match(token))


def _skip_leading_assignments(words: List[str]) -> List[str]:
    """Skip any number of leading NAME=value tokens (API-4 CORRECTION-2)."""
    i = 0
    while i < len(words) and _is_assignment_token(words[i]):
        i += 1
    return words[i:]


def _match_tokens(
    words: List[str],
    names: Sequence[str],
    binary_bases: Set[str],
    launcher_bases: Set[str],
    binary_names: Sequence[str],
    launcher_scripts: Sequence[str],
    depth: int,
) -> Optional[str]:
    words = list(words)
    while words:
        # Leading assignments at every command position, before and after wrappers.
        words = _skip_leading_assignments(words)
        if not words:
            return None
        first = words[0]
        base = os.path.basename(first)

        # Strip recognised wrappers (basename match).
        if base in _WRAPPERS_SIMPLE:
            words = words[1:]
            continue
        if base == "env":
            words = _consume_env(words)
            continue
        if base == "timeout":
            words = _consume_timeout(words)
            continue
        if base == "sudo":
            words = _consume_sudo(words)
            continue
        if base == "nice":
            words = _consume_nice(words)
            continue
        if base == "caffeinate":
            words = _consume_flag_wrapper(words)
            continue
        if base == "xargs":
            words = _consume_flag_wrapper(words)
            continue

        # bash|sh|zsh [flags] -c '<string>' → recurse into the string (depth ≤ 3).
        if base in _SHELLS:
            hit = _match_shell_c(words, binary_names, launcher_scripts, depth)
            if hit:
                return hit
            return None

        # Interpreter: python / python3 / python3.N → launcher_scripts basenames.
        if _PY_BASENAME.match(base):
            hit = _match_interpreter(words, launcher_bases, launcher_scripts)
            if hit:
                return hit
            return None

        # Direct binary / launcher in command position.
        return _match_head(words, names)

    return None


def _match_head(words: List[str], names: Sequence[str]) -> Optional[str]:
    if not words:
        return None
    first = words[0]
    base = os.path.basename(first)
    matched: Optional[str] = None
    for name in names:
        name_base = os.path.basename(name)
        if first == name or base == name or base == name_base or first.endswith("/" + name):
            matched = name
            break
    if matched is None:
        return None
    if len(words) == 2 and words[1] in _SAFE_FLAGS:
        return None
    return matched


def _match_interpreter(
    words: List[str],
    launcher_bases: Set[str],
    launcher_scripts: Sequence[str],
) -> Optional[str]:
    # words[0] is the python binary; skip flag words; -c/-m end the scan.
    i = 1
    while i < len(words):
        t = words[i]
        if t in ("-c", "-m"):
            return None
        if t.startswith("-") and "=" not in t[1:2]:
            # Short/long flags: -B, -u, -O, -OO, --help, …
            # Lone ``-`` is not a flag we skip into a script path.
            if t == "-":
                break
            i += 1
            continue
        break
    if i >= len(words):
        return None
    script = words[i]
    script_base = os.path.basename(script)
    if script_base in launcher_bases or script in launcher_bases:
        for name in launcher_scripts:
            if os.path.basename(str(name)) == script_base or str(name) == script:
                return str(name)
        return script_base
    return None


def _match_shell_c(
    words: List[str],
    binary_names: Sequence[str],
    launcher_scripts: Sequence[str],
    depth: int,
) -> Optional[str]:
    # Find -c and the following string argument.
    i = 1
    while i < len(words):
        t = words[i]
        if t == "-c":
            if i + 1 >= len(words):
                return None
            return command_starts_executor(
                words[i + 1], binary_names, launcher_scripts, _depth=depth + 1
            )
        if t.startswith("-") and t not in ("-c",):
            i += 1
            continue
        # Non-flag before -c: not a -c invocation we handle.
        break
    return None


def _consume_env(words: List[str]) -> List[str]:
    # env [VAR=val…] command…
    i = 1
    while i < len(words) and _is_assignment_token(words[i]):
        i += 1
    return words[i:]


def _consume_timeout(words: List[str]) -> List[str]:
    # timeout [flags] N command…
    i = 1
    while i < len(words):
        t = words[i]
        if t.startswith("-"):
            # --signal=X or -s SIGNAL (next token) or bare flags.
            if t in ("-s", "--signal") or t.startswith("--signal="):
                if "=" not in t and i + 1 < len(words):
                    i += 2
                    continue
                i += 1
                continue
            i += 1
            continue
        # Duration N
        i += 1
        break
    return words[i:]


def _consume_sudo(words: List[str]) -> List[str]:
    i = 1
    while i < len(words):
        t = words[i]
        if t.startswith("-"):
            # sudo -u user / -g group / -s takes optional arg for some flags.
            if t in ("-u", "-g", "-h", "-C", "-D", "-T") or t.startswith("--"):
                if "=" not in t and i + 1 < len(words) and not words[i + 1].startswith("-"):
                    # Conservative: -u user
                    if t in ("-u", "-g", "-C", "-D", "-T"):
                        i += 2
                        continue
                i += 1
                continue
            i += 1
            continue
        break
    return words[i:]


def _consume_nice(words: List[str]) -> List[str]:
    i = 1
    if i < len(words) and words[i] == "-n" and i + 1 < len(words):
        return words[i + 2 :]
    if i < len(words) and words[i].startswith("-") and re.match(r"^-\d+$", words[i]):
        return words[i + 1 :]
    return words[1:]


def _consume_flag_wrapper(words: List[str]) -> List[str]:
    i = 1
    while i < len(words) and words[i].startswith("-"):
        i += 1
    return words[i:]


def _tokenize(segment: str) -> List[str]:
    out: List[str] = []
    buf: List[str] = []
    in_single = False
    in_double = False
    i = 0
    while i < len(segment):
        ch = segment[i]
        if ch == "'" and not in_double:
            in_single = not in_single
            i += 1
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            i += 1
            continue
        if ch.isspace() and not in_single and not in_double:
            if buf:
                out.append("".join(buf))
                buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    if buf:
        out.append("".join(buf))
    return out


def bypass_table_cases(
    binary_names: Sequence[str],
    launcher_scripts: Sequence[str],
) -> List[Tuple[str, bool]]:
    """Reference table used by tests. Not used at runtime."""
    bin0 = binary_names[0] if binary_names else "cursor-agent"
    launch0 = launcher_scripts[0] if launcher_scripts else "run_cursor.py"
    return [
        # Original set (≥ 12)
        ("cd x && %s -p --force" % bin0, True),
        ("nohup %s -p" % bin0, True),
        ("env A=1 %s -p" % bin0, True),
        ("python3 -B %s" % launch0, True),
        ("%s --version" % bin0, False),
        ("%s --help" % bin0, False),
        ("echo %s" % bin0, False),
        ("grep %s file" % bin0, False),
        ("%s -p --force" % bin0, True),
        ("exec %s -p" % bin0, True),
        ("timeout 30 %s -p" % bin0, True),
        ("%s | cat" % bin0, True),
        ("(%s -p)" % bin0, True),
        ("true; %s -p" % bin0, True),
        # CORRECTION-1 additions
        ("cd x\n%s -p y" % bin0, True),
        ("sleep 1 & %s -p" % bin0, True),
        (
            "/usr/bin/python3 -B temp/run-20261120/%s L1 /repo 3600" % launch0,
            True,
        ),
        ("/opt/homebrew/bin/python3 %s" % launch0, True),
        ("sudo %s -p" % bin0, True),
        ("bash -c '%s -p x'" % bin0, True),
        ("/usr/bin/env %s -p" % bin0, True),
        ("time %s -p" % bin0, True),
        ("cat %s" % launch0, False),
        ('python3 -c "print(1)"', False),
        ("ls ~/.local/bin/%s" % bin0, False),
        ("grep -n %s notes.md" % launch0, False),
        # CORRECTION-2 — leading NAME=value without env
        ("X=1 %s -p hi" % bin0, True),
        ("FOO=bar /Users/dev/.local/bin/%s -p x" % bin0, True),
        ('A= B="x y" nohup %s -p' % bin0, True),
        (
            "cd /r && TMPDIR=/t /usr/bin/python3 -B temp/run/%s L1 /r 60" % launch0,
            True,
        ),
        ("X=1 echo %s" % bin0, False),
        ("A=%s" % bin0, False),
        ("export A=%s" % bin0, False),
    ]


def collect_binary_names(executors: Iterable) -> List[str]:
    """Gather binary_names from executor config values (sorted unique)."""
    names: List[str] = []
    seen = set()
    for item in executors:
        if not isinstance(item, dict):
            continue
        for n in item.get("binary_names") or []:
            s = str(n).strip()
            if s and s not in seen:
                seen.add(s)
                names.append(s)
    return sorted(names)
