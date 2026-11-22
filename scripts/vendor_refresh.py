#!/usr/bin/env python3
"""Re-copy vendored lean-orchestration + DEV Framework and regenerate SOURCE pins.

stdlib only. Prints a diff summary (missing / extra / changed) vs the previous pin
when one exists. Does not network. Local DF patches required by vibe-relay tests are
re-applied after the DF copy (see LOCAL_DF_PATCHES).

Usage (from repo root)::

    LO_SOURCE=<lean-orchestration checkout> DF_SOURCE=<dev-framework checkout> python3 scripts/vendor_refresh.py

Pins record the public repository and the source checkout's commit (``+dirty`` when it has
uncommitted changes), never a local filesystem path.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor"

LO_SOURCE = Path(os.environ.get("LO_SOURCE", "")).expanduser()
LO_PUBLIC = "github.com/trenthalden/lean-orchestration"
LO_ITEMS = (
    "plugin.yaml",
    "__init__.py",
    "cli.py",
    "README.md",
    "lo_core",
    "lo_exec",
    "skills",
    "tests",
)
LO_VERSION = "0.1.0"

DF_SOURCE = Path(os.environ.get("DF_SOURCE", "")).expanduser()
DF_PUBLIC = "github.com/trenthalden/dev-framework"
DF_SKIP_DIRS = frozenset({".git", "__pycache__", ".github", "evals"})
DF_VERSION = "1.7.0"

# Patches vibe-relay tests need on top of upstream DF (API-4 documented list).
# Stored beside this script so a refresh re-applies them after a clean copy.
LOCAL_DF_PATCHES = (
    ("__init__.py", "df_local_patches/__init__.py"),
    ("tests/common.py", "df_local_patches/common.py"),
    ("tests/test_install.py", "df_local_patches/test_install.py"),
    ("tests/test_navigate_json.py", "df_local_patches/test_navigate_json.py"),
)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _iter_files(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel_parts = path.relative_to(root).parts
        if "__pycache__" in rel_parts or path.suffix == ".pyc":
            continue
        yield path


def _file_map(root: Path) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for path in _iter_files(root):
        rel = path.relative_to(root).as_posix()
        out[rel] = _sha256(path)
    return dict(sorted(out.items()))


def _ignore_pycache(dirpath: str, names: List[str]) -> List[str]:
    del dirpath
    return [n for n in names if n == "__pycache__" or n.endswith(".pyc")]


def _ignore_df(dirpath: str, names: List[str]) -> List[str]:
    del dirpath
    return [n for n in names if n in DF_SKIP_DIRS or n.endswith(".pyc")]


def _source_base(src: Path) -> str:
    """Commit of the source checkout (``+dirty`` with uncommitted changes); no local path."""
    head = subprocess.run(["git", "-C", str(src), "rev-parse", "HEAD"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if head.returncode != 0:
        raise SystemExit("not a git checkout: set LO_SOURCE / DF_SOURCE to a clone (%s)" % src.name)
    dirty = subprocess.run(["git", "-C", str(src), "status", "--porcelain"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    return head.stdout.strip() + ("+dirty" if dirty.stdout.strip() else "")


def _copy_lo(dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True)
    for item in LO_ITEMS:
        src = LO_SOURCE / item
        target = dst / item
        if not src.exists():
            raise SystemExit("lean-orchestration source missing: %s" % src)
        if src.is_dir():
            shutil.copytree(src, target, ignore=_ignore_pycache)
        else:
            shutil.copy2(src, target)


def _copy_df(dst: Path) -> None:
    if not DF_SOURCE.is_dir():
        raise SystemExit("DEV Framework source missing: %s" % DF_SOURCE)
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(DF_SOURCE, dst, ignore=_ignore_df)
    patch_root = Path(__file__).resolve().parent
    for dest_rel, patch_rel in LOCAL_DF_PATCHES:
        patch = patch_root / patch_rel
        if not patch.is_file():
            raise SystemExit("missing DF local patch file: %s" % patch)
        target = dst / dest_rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(patch, target)


def _write_pin(
    path: Path,
    *,
    version: str,
    source: str,
    base: str,
    files: Mapping[str, str],
) -> None:
    payload = {
        "base": base,
        "files": dict(sorted(files.items())),
        "source": source,
        "version": version,
    }
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    path.write_text(text, encoding="utf-8")


def _load_pin(path: Path) -> Optional[Dict[str, str]]:
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    files = data.get("files") or {}
    if not isinstance(files, dict):
        return None
    return {str(k): str(v) for k, v in files.items()}


def _diff_summary(label: str, old: Optional[Mapping[str, str]], new: Mapping[str, str]) -> None:
    if old is None:
        print("%s: new pin (%d files)" % (label, len(new)))
        return
    missing = sorted(set(old) - set(new))
    extra = sorted(set(new) - set(old))
    changed = sorted(k for k in set(old) & set(new) if old[k] != new[k])
    print(
        "%s: missing=%d extra=%d changed=%d (of %d -> %d files)"
        % (label, len(missing), len(extra), len(changed), len(old), len(new))
    )
    for name in missing[:20]:
        print("  missing  %s" % name)
    for name in extra[:20]:
        print("  extra    %s" % name)
    for name in changed[:20]:
        print("  changed  %s" % name)


def refresh() -> int:
    # An unset variable must not resolve to "." (that would vendor this repository into itself).
    for name in ("LO_SOURCE", "DF_SOURCE"):
        if not os.environ.get(name, "").strip():
            raise SystemExit("set %s to the source checkout (see the module docstring)" % name)
    lo_dst = VENDOR / "lean_orchestration"
    df_dst = VENDOR / "dev_framework"
    lo_pin_path = VENDOR / "lean_orchestration.SOURCE.json"
    df_pin_path = VENDOR / "dev_framework.SOURCE.json"

    old_lo = _load_pin(lo_pin_path)
    old_df = _load_pin(df_pin_path)

    _copy_lo(lo_dst)
    _copy_df(df_dst)

    lo_files = _file_map(lo_dst)
    df_files = _file_map(df_dst)

    _write_pin(
        lo_pin_path,
        version=LO_VERSION,
        source=LO_PUBLIC,
        base=_source_base(LO_SOURCE),
        files=lo_files,
    )
    _write_pin(
        df_pin_path,
        version=DF_VERSION,
        source=DF_PUBLIC,
        base=_source_base(DF_SOURCE),
        files=df_files,
    )

    _diff_summary("lean_orchestration", old_lo, lo_files)
    _diff_summary("dev_framework", old_df, df_files)
    return 0


if __name__ == "__main__":
    sys.exit(refresh())
