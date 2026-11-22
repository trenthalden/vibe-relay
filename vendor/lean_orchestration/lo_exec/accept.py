"""orch_accept — gather git/probe/gate facts and decide (FR-014, API-7, JOB-3)."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from lo_core import brief as brief_mod
from lo_core import journal as journal_mod
from lo_core.accept_decide import decide_accept, tree_hash_material
from lo_core.config import ACCEPT_SCHEMA, merge_config
from lo_core.gate import run_gate
from lo_core.rules_source import normalize_path
from lo_exec import ledger as ledger_mod

GIT_TIMEOUT_S = 30.0


def accept_dispatch(
    *,
    dispatch_id: str,
    data_dir: str,
    config: Optional[Mapping[str, Any]] = None,
    journal_path: Optional[str] = None,
    profile: Optional[str] = None,
) -> Dict[str, Any]:
    """Accept by ledger dispatch id (Hermes tool / CLI ``--dispatch``)."""
    cfg = merge_config(config)
    led = ledger_mod.ledger_path(data_dir)
    dispatched = _find_dispatched(led, dispatch_id)
    if dispatched is None:
        return _reject_early(
            reasons=["dispatch_unknown"],
            gate_disabled=not bool(cfg.get("gate_enabled", True)),
            journal_path=journal_path,
            cfg=cfg,
            extra={"dispatch_id": dispatch_id},
        )

    workdir = str(dispatched.get("workdir") or "")
    run_dir = str(dispatched.get("run_dir") or os.path.join(data_dir, "runs", dispatch_id))
    brief_path = os.path.join(run_dir, "BRIEF.md")
    if not os.path.isfile(brief_path):
        return _reject_early(
            reasons=["dispatch_unknown"],
            gate_disabled=not bool(cfg.get("gate_enabled", True)),
            journal_path=journal_path,
            cfg=cfg,
            extra={"dispatch_id": dispatch_id, "detail": "brief_missing"},
        )
    with open(brief_path, "r", encoding="utf-8-sig") as fh:
        brief_text = fh.read()

    repo = str(dispatched.get("repo") or workdir)
    base_head = dispatched.get("base_head")
    base_dirty = list(dispatched.get("base_dirty") or [])
    return accept_facts(
        repo=repo,
        workdir=workdir,
        brief_text=brief_text,
        base_head=base_head if isinstance(base_head, str) else None,
        base_dirty=base_dirty,
        run_dir=run_dir,
        exclude_run_dir=run_dir,
        config=cfg,
        journal_path=journal_path,
        profile=profile or str(dispatched.get("profile") or "") or None,
        dispatch_id=dispatch_id,
        data_dir=data_dir,
    )


def accept_manual(
    *,
    repo: str,
    brief_text: str,
    base_commit: str,
    workdir: Optional[str] = None,
    config: Optional[Mapping[str, Any]] = None,
    journal_path: Optional[str] = None,
    profile: Optional[str] = None,
    data_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Accept without a ledger row (CLI ``--repo --brief --base``)."""
    cfg = merge_config(config)
    wd = os.path.abspath(workdir or repo)
    run_dir = ""
    if data_dir:
        run_dir = os.path.join(os.path.abspath(data_dir), "runs", "_manual")
        os.makedirs(run_dir, exist_ok=True)
    return accept_facts(
        repo=os.path.abspath(repo),
        workdir=wd,
        brief_text=brief_text,
        base_head=base_commit,
        base_dirty=[],
        run_dir=run_dir,
        exclude_run_dir=run_dir or None,
        config=cfg,
        journal_path=journal_path,
        profile=profile,
        dispatch_id="",
        data_dir=data_dir or "",
    )


def accept_facts(
    *,
    repo: str,
    workdir: str,
    brief_text: str,
    base_head: Optional[str],
    base_dirty: Sequence[Mapping[str, Any]],
    run_dir: str,
    exclude_run_dir: Optional[str],
    config: Mapping[str, Any],
    journal_path: Optional[str],
    profile: Optional[str],
    dispatch_id: str,
    data_dir: str,
) -> Dict[str, Any]:
    """Shared gather + decide path (SEC-2)."""
    cfg = merge_config(config)
    gate_disabled = not bool(cfg.get("gate_enabled", True))

    # API-7: workdir without git → REJECTED no_git_base, never "accept all".
    if not _is_git_repo(workdir):
        result = decide_accept(
            changed_paths=[],
            allowed=[],
            gate_decision="PASS",
            gate_reasons=[],
            probe_results=[],
            tree_sha256="",
            has_git_base=False,
            dispatch_known=True,
            probes_status="none",
            gate_disabled=gate_disabled,
        )
        return _finalize(
            result,
            journal_path=journal_path,
            cfg=cfg,
            dispatch_id=dispatch_id,
            data_dir=data_dir,
            gate=None,
        )

    if not base_head:
        result = decide_accept(
            changed_paths=[],
            allowed=[],
            gate_decision="PASS",
            gate_reasons=[],
            probe_results=[],
            tree_sha256="",
            has_git_base=False,
            dispatch_known=True,
            probes_status="none",
            gate_disabled=gate_disabled,
        )
        return _finalize(
            result,
            journal_path=journal_path,
            cfg=cfg,
            dispatch_id=dispatch_id,
            data_dir=data_dir,
            gate=None,
        )

    sections = brief_mod.parse_sections(brief_text)
    allowed_body = brief_mod.section_body(sections, "allowed") or ""
    allowed, _brace_reasons, _unmapped = brief_mod.extract_allowed_patterns(allowed_body)

    changed, path_hashes, paths_capped = collect_changed_paths(
        workdir,
        base_head=base_head,
        base_dirty=base_dirty,
        exclude_run_dir=exclude_run_dir,
        cap=int(cfg["changed_paths_cap"]),
    )
    material = tree_hash_material(path_hashes)
    tree_sha = hashlib.sha256(material.encode("utf-8")).hexdigest()

    gate = run_gate(
        repo,
        brief_text,
        config=cfg,
        journal_path=journal_path,
        profile=profile,
    )
    # Bind evidence: record tree hash on the gate journal via accept journal line.

    probes_status, probe_results = run_probes(
        repo=repo,
        workdir=workdir,
        run_dir=run_dir,
        config=cfg,
    )

    result = decide_accept(
        changed_paths=changed,
        allowed=allowed,
        gate_decision=str(gate.get("decision") or ""),
        gate_reasons=list(gate.get("reasons") or []),
        probe_results=probe_results,
        tree_sha256=tree_sha,
        has_git_base=True,
        dispatch_known=True,
        probes_status=probes_status,
        paths_capped=paths_capped,
        gate_disabled=gate_disabled or bool(gate.get("gate_disabled")),
    )
    result["gate_decision"] = gate.get("decision")
    result["brief_sha256"] = gate.get("brief_sha256")
    if paths_capped:
        result["cap_hit"] = "changed_paths"
    return _finalize(
        result,
        journal_path=journal_path,
        cfg=cfg,
        dispatch_id=dispatch_id,
        data_dir=data_dir,
        gate=gate,
    )


def capture_git_base(workdir: str, *, timeout_s: float = GIT_TIMEOUT_S) -> Dict[str, Any]:
    """Record dispatch base: ``base_head`` + sorted ``base_dirty`` with content sha256 (DB-5)."""
    if not _is_git_repo(workdir):
        return {"base_head": None, "base_dirty": []}
    head = _git_stdout(workdir, ["rev-parse", "HEAD"], timeout_s=timeout_s)
    base_head = head.strip() if head is not None else None
    dirty = _status_paths(workdir, timeout_s=timeout_s)
    base_dirty: List[Dict[str, str]] = []
    for path in sorted(dirty):
        base_dirty.append({"path": path, "sha256": file_sha256(os.path.join(workdir, path))})
    return {"base_head": base_head, "base_dirty": base_dirty}


def collect_changed_paths(
    workdir: str,
    *,
    base_head: str,
    base_dirty: Sequence[Mapping[str, Any]],
    exclude_run_dir: Optional[str],
    cap: int,
) -> Tuple[List[str], Dict[str, str], bool]:
    """Changed set = tracked diff ∪ untracked ∪ dirty-whose-hash-changed; minus run dir.

    # cost: one git diff + one ls-files per accept; paths capped (BULK-3).
    """
    paths: List[str] = []
    seen = set()

    dirty_map: Dict[str, str] = {}
    for entry in base_dirty:
        p = normalize_path(str(entry.get("path") or ""))
        if p:
            dirty_map[p] = str(entry.get("sha256") or "")

    def _add(path: str) -> None:
        norm = normalize_path(path)
        if not norm or norm in seen:
            return
        if _under_exclude(workdir, norm, exclude_run_dir):
            return
        # DECISION 1: paths already dirty at dispatch are judged by content change
        # vs the recorded sha256 — not by diff vs HEAD (unchanged pre-dirty ignored).
        if norm in dirty_map:
            cur = file_sha256(os.path.join(workdir, norm))
            if cur == dirty_map[norm]:
                return
        seen.add(norm)
        paths.append(norm)

    # Tracked + staged + unstaged vs base_head (incl. renames as both paths). BULK-1: stream -z.
    name_status = _git_stdout(
        workdir,
        ["diff", "--name-status", "-z", base_head],
        timeout_s=GIT_TIMEOUT_S,
    )
    if name_status is not None:
        for p in _parse_name_status_z(name_status):
            _add(p)

    # Untracked.
    others = _git_stdout(
        workdir,
        ["ls-files", "-o", "--exclude-standard", "-z"],
        timeout_s=GIT_TIMEOUT_S,
    )
    if others is not None:
        for p in others.split("\0"):
            if p:
                _add(p)

    # Pre-dirty whose content hash changed (may already be added via diff).
    for path, old_hash in sorted(dirty_map.items()):
        cur = file_sha256(os.path.join(workdir, path))
        if cur != old_hash:
            _add(path)

    capped = len(paths) > cap
    if capped:
        paths = paths[:cap]

    hashes: Dict[str, str] = {}
    for path in paths:
        hashes[path] = file_sha256(os.path.join(workdir, path))
    return paths, hashes, capped


def run_probes(
    *,
    repo: str,
    workdir: str,
    run_dir: str,
    config: Mapping[str, Any],
) -> Tuple[str, List[Dict[str, Any]]]:
    """Load `.lean-orchestration/probes.json` or config probes; run each (JOB-3, SEC-1).

    Returns ``(status, results)`` where status is ``none`` | ``ok`` | ``bad_probes``.
    """
    probes, status = load_probes(repo, config)
    if status != "ok":
        return status, []
    if not probes:
        return "none", []

    results: List[Dict[str, Any]] = []
    out_cap = int(config["probe_output_cap_bytes"])
    if run_dir:
        os.makedirs(run_dir, exist_ok=True)
        probe_dir = os.path.join(run_dir, "probes")
        os.makedirs(probe_dir, exist_ok=True)
    else:
        probe_dir = ""

    # cost: one subprocess per probe; ≤ handful per accept at planning scale.
    for probe in probes:
        name = str(probe["name"])
        argv = [str(x) for x in probe["argv"]]
        cwd_rel = str(probe.get("cwd") or ".")
        cwd = os.path.normpath(os.path.join(workdir, cwd_rel))
        # SEC-1: cwd must stay under workdir.
        if not _is_under(cwd, workdir):
            results.append(
                {
                    "name": name,
                    "rc": None,
                    "expect_rc": probe["expect_rc"],
                    "timed_out": False,
                    "error": "cwd_escape",
                }
            )
            continue
        timeout_s = float(probe["timeout_s"])
        timed_out = False
        rc: Optional[int] = None
        stdout_b = b""
        stderr_b = b""
        try:
            # JOB-3: explicit argv/cwd/env/timeout; argv list only — never shell.
            proc = subprocess.run(
                argv,
                cwd=cwd,
                capture_output=True,
                timeout=timeout_s,
                env=dict(os.environ),
            )
            rc = int(proc.returncode)
            stdout_b = proc.stdout or b""
            stderr_b = proc.stderr or b""
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            stdout_b = exc.stdout or b""
            stderr_b = exc.stderr or b""
            rc = None
        except OSError:
            rc = 127
            timed_out = False

        if probe_dir:
            _store_probe_tail(
                os.path.join(probe_dir, "%s.out" % name),
                stdout_b,
                stderr_b,
                cap=out_cap,
            )

        results.append(
            {
                "name": name,
                "rc": rc,
                "expect_rc": probe["expect_rc"],
                "timed_out": timed_out,
            }
        )
    return "ok", results


def load_probes(
    repo: str, config: Mapping[str, Any]
) -> Tuple[List[Dict[str, Any]], str]:
    """Return ``(probes, status)``. Probes come only from repo file or plugin config (SEC-1)."""
    path = os.path.join(repo, ".lean-orchestration", "probes.json")
    raw: Any = None
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8-sig") as fh:
                raw = json.load(fh)
        except (OSError, json.JSONDecodeError):
            return [], "bad_probes"
    elif isinstance(config.get("probes"), list):
        raw = {"probes": config.get("probes")}
    else:
        return [], "none"

    if not isinstance(raw, dict) or "probes" not in raw:
        return [], "bad_probes"
    items = raw.get("probes")
    if not isinstance(items, list):
        return [], "bad_probes"
    if not items:
        return [], "none"

    out: List[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            return [], "bad_probes"
        name = item.get("name")
        argv = item.get("argv")
        if not isinstance(name, str) or not name.strip():
            return [], "bad_probes"
        if not isinstance(argv, list) or not argv or not all(isinstance(x, str) for x in argv):
            return [], "bad_probes"
        try:
            timeout_s = float(item.get("timeout_s", 30))
            expect_rc = int(item.get("expect_rc", 0))
        except (TypeError, ValueError):
            return [], "bad_probes"
        if timeout_s <= 0:
            return [], "bad_probes"
        out.append(
            {
                "name": name.strip(),
                "argv": list(argv),
                "cwd": str(item.get("cwd") or "."),
                "timeout_s": timeout_s,
                "expect_rc": expect_rc,
            }
        )
    # Deterministic order by name.
    out.sort(key=lambda p: p["name"])
    return out, "ok"


def file_sha256(path: str) -> str:
    """Content sha256; missing/unreadable → sha256 of empty bytes (API-7 still names the path)."""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            # cost: stream file once per dirty/changed path; capped by changed_paths_cap.
            while True:
                chunk = fh.read(1024 * 1024)
                if not chunk:
                    break
                h.update(chunk)
    except OSError:
        pass
    return h.hexdigest()


def format_orch_coverage(
    *,
    data_dir: str,
    journal_path: Optional[str],
    config: Mapping[str, Any],
    pending_count: int = 0,
) -> str:
    """Plain-text `/orch` report ≤ 40 lines (FR-015).

    # cost: bounded tail read of journal + full ledger scan once per /orch.
    """
    cfg = merge_config(config)
    gate_on = bool(cfg.get("gate_enabled", True))
    lines: List[str] = []
    lines.append(
        "gate: %s (config gate_enabled=%s)"
        % ("enabled" if gate_on else "disabled", "true" if gate_on else "false")
    )

    led = ledger_mod.ledger_path(data_dir)
    events = ledger_mod.read_events(led)
    dispatches = [e for e in events if e.get("event") == "dispatched"]
    ended_by_id = {
        str(e.get("id")): e for e in events if e.get("event") == "ended" and e.get("id")
    }
    accepted_by_id = {
        str(e.get("id")): e
        for e in events
        if e.get("event") == "accepted" and e.get("id")
    }

    lines.append("last dispatches:")
    for ev in dispatches[-10:]:
        did = str(ev.get("id") or "")
        ended = ended_by_id.get(did)
        acc = accepted_by_id.get(did)
        status = "running"
        rc = "-"
        if ended:
            status = "ended"
            rc = str(ended.get("rc"))
        accepted = "-"
        if acc:
            accepted = str(acc.get("decision") or "-")
        lines.append(
            "  %s executor=%s status=%s rc=%s accepted=%s"
            % (did, ev.get("executor"), status, rc, accepted)
        )
    if not dispatches:
        lines.append("  (none)")

    counters = _journal_counters(journal_path)
    accept_acc = sum(
        1
        for e in events
        if e.get("event") == "accepted" and e.get("decision") == "ACCEPTED"
    )
    accept_rej = sum(
        1
        for e in events
        if e.get("event") == "accepted" and e.get("decision") == "REJECTED"
    )
    # Labels avoid `NAME=value` with PASS/TOKEN-like names: Hermes output redaction masked
    # `gate_PASS=2` as `***` in the 2026-11-20 live check.
    lines.append(
        "counters: gate pass %d, gate refused %d, dispatches %d, "
        "accepted %d, rejected %d, briefs citing known errors %d, briefs citing rules %d, pending %d"
        % (
            counters["pass"],
            counters["refused"],
            len(dispatches),
            accept_acc,
            accept_rej,
            counters["ke"],
            counters["rules"],
            pending_count,
        )
    )
    # Cap at 40 lines (BRIEF).
    if len(lines) > 40:
        lines = lines[:39] + ["... truncated"]
    return "\n".join(lines) + "\n"


# --- helpers -----------------------------------------------------------------


def _finalize(
    result: Dict[str, Any],
    *,
    journal_path: Optional[str],
    cfg: Mapping[str, Any],
    dispatch_id: str,
    data_dir: str,
    gate: Optional[Mapping[str, Any]],
) -> Dict[str, Any]:
    if journal_path:
        try:
            journal_mod.append_journal(
                journal_path,
                {
                    "event": "accept",
                    "schema": ACCEPT_SCHEMA,
                    "decision": result.get("decision"),
                    "reasons": result.get("reasons"),
                    "tree_sha256": result.get("tree_sha256"),
                    "dispatch_id": dispatch_id,
                    "gate_disabled": bool(result.get("gate_disabled")),
                    "brief_sha256": (gate or {}).get("brief_sha256")
                    or result.get("brief_sha256")
                    or "",
                },
                rotate_bytes=cfg["journal_rotate_bytes"],
            )
        except Exception as exc:  # noqa: BLE001
            result["journal_error"] = str(exc)

    if data_dir and dispatch_id:
        try:
            ledger_mod.append_event(
                ledger_mod.ledger_path(data_dir),
                {
                    "event": "accepted",
                    "id": dispatch_id,
                    "decision": result.get("decision"),
                    "reasons": result.get("reasons"),
                    "tree_sha256": result.get("tree_sha256"),
                },
            )
        except Exception as exc:  # noqa: BLE001
            result["ledger_error"] = str(exc)

    if result.get("gate_disabled"):
        result["gate_disabled"] = True
    return result


def _reject_early(
    *,
    reasons: List[str],
    gate_disabled: bool,
    journal_path: Optional[str],
    cfg: Mapping[str, Any],
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "schema": ACCEPT_SCHEMA,
        "decision": "REJECTED",
        "reasons": list(reasons),
        "changed_paths": [],
        "allowed": [],
        "tree_sha256": "",
        "probes": "none",
        "paths_capped": False,
    }
    if gate_disabled:
        result["gate_disabled"] = True
    if extra:
        result.update(extra)
    return _finalize(
        result,
        journal_path=journal_path,
        cfg=cfg,
        dispatch_id=str((extra or {}).get("dispatch_id") or ""),
        data_dir="",
        gate=None,
    )


def _find_dispatched(led_path: str, dispatch_id: str) -> Optional[Dict[str, Any]]:
    found: Optional[Dict[str, Any]] = None
    for ev in ledger_mod.read_events(led_path):
        if ev.get("event") == "dispatched" and str(ev.get("id") or "") == dispatch_id:
            found = ev
    return found


def _is_git_repo(workdir: str) -> bool:
    if not workdir or not os.path.isdir(workdir):
        return False
    return (
        _git_stdout(workdir, ["rev-parse", "--is-inside-work-tree"], timeout_s=5.0) or ""
    ).strip() == "true"


def _git_stdout(cwd: str, args: List[str], *, timeout_s: float) -> Optional[str]:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            timeout=timeout_s,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return (proc.stdout or b"").decode("utf-8", "replace")


def _status_paths(workdir: str, *, timeout_s: float) -> List[str]:
    raw = _git_stdout(
        workdir,
        ["status", "--porcelain=v1", "-z", "--untracked-files=all"],
        timeout_s=timeout_s,
    )
    if raw is None:
        return []
    return sorted(set(_parse_porcelain_z(raw)))


def _parse_porcelain_z(raw: str) -> List[str]:
    """Parse ``git status --porcelain=v1 -z`` into paths (renames → both)."""
    paths: List[str] = []
    i = 0
    data = raw
    n = len(data)
    while i < n:
        if data[i] == "\0":
            i += 1
            continue
        # XY + optional space + path.
        if i + 2 > n:
            break
        xy = data[i : i + 2]
        i += 2
        if i < n and data[i] == " ":
            i += 1
        # Read NUL-terminated path.
        j = data.find("\0", i)
        if j < 0:
            path1 = data[i:]
            i = n
        else:
            path1 = data[i:j]
            i = j + 1
        if path1:
            paths.append(normalize_path(path1))
        # Rename/copy: second path.
        if xy[0] in ("R", "C") or xy[1] in ("R", "C"):
            j = data.find("\0", i)
            if j < 0:
                path2 = data[i:]
                i = n
            else:
                path2 = data[i:j]
                i = j + 1
            if path2:
                paths.append(normalize_path(path2))
    return paths


def _parse_name_status_z(raw: str) -> List[str]:
    """Parse ``git diff --name-status -z``: status\\0path\\0 or R\\0old\\0new\\0."""
    parts = raw.split("\0")
    paths: List[str] = []
    i = 0
    while i < len(parts):
        status = parts[i]
        if not status:
            i += 1
            continue
        code = status.split("\t")[0] if "\t" in status else status
        # Sometimes status and path are in one tab-separated field without -z split.
        if "\t" in status:
            bits = status.split("\t")
            code = bits[0]
            if code.startswith("R") or code.startswith("C"):
                if len(bits) >= 3:
                    paths.append(normalize_path(bits[1]))
                    paths.append(normalize_path(bits[2]))
                    i += 1
                    continue
            elif len(bits) >= 2:
                paths.append(normalize_path(bits[1]))
                i += 1
                continue
        if code.startswith("R") or code.startswith("C"):
            old = parts[i + 1] if i + 1 < len(parts) else ""
            new = parts[i + 2] if i + 2 < len(parts) else ""
            if old:
                paths.append(normalize_path(old))
            if new:
                paths.append(normalize_path(new))
            i += 3
        else:
            path = parts[i + 1] if i + 1 < len(parts) else ""
            if path:
                paths.append(normalize_path(path))
            i += 2
    return paths


def _under_exclude(workdir: str, rel_path: str, exclude_run_dir: Optional[str]) -> bool:
    if not exclude_run_dir:
        return False
    abs_path = os.path.normpath(os.path.join(workdir, rel_path))
    return _is_under(abs_path, exclude_run_dir)


def _is_under(path: str, root: str) -> bool:
    path_n = os.path.normcase(os.path.abspath(path))
    root_n = os.path.normcase(os.path.abspath(root))
    if path_n == root_n:
        return True
    prefix = root_n + os.sep
    return path_n.startswith(prefix)


def _store_probe_tail(path: str, stdout_b: bytes, stderr_b: bytes, *, cap: int) -> None:
    blob = b"--- stdout ---\n" + (stdout_b or b"") + b"\n--- stderr ---\n" + (stderr_b or b"")
    if len(blob) > cap:
        # BULK-3: explicit cap; keep the tail.
        blob = b"... truncated\n" + blob[-cap:]
    with open(path, "wb") as fh:
        fh.write(blob)


def _journal_counters(journal_path: Optional[str]) -> Dict[str, int]:
    out = {"pass": 0, "refused": 0, "ke": 0, "rules": 0}
    if not journal_path or not os.path.isfile(journal_path):
        return out
    # Bounded read: last 5 MiB only (# cost note in format_orch_coverage).
    try:
        size = os.path.getsize(journal_path)
        with open(journal_path, "rb") as fh:
            if size > 5 * 1024 * 1024:
                fh.seek(size - 5 * 1024 * 1024)
                fh.readline()  # drop partial line
            text = fh.read().decode("utf-8", "replace")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        if row.get("event") == "accept":
            continue
        decision = row.get("decision")
        if decision == "PASS":
            out["pass"] += 1
        elif decision in ("REFUSED", "ERROR"):
            out["refused"] += 1
        cited = row.get("cited") or {}
        if isinstance(cited, dict):
            if cited.get("ke"):
                out["ke"] += 1
            if cited.get("rules"):
                out["rules"] += 1
    return out
