"""Plugin configuration — defaults and load from a JSON file."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Mapping, MutableMapping, Optional

ALGORITHM_GATE = "lo-gate/1"
ALGORITHM_DELIVERY = "lo-delivery/2"
GATE_SCHEMA = "lean-orchestration.gate/1"
ACCEPT_SCHEMA = "lean-orchestration.accept/1"
MIN_FRAMEWORK_VERSION = (1, 7, 0)

# BULK-3: explicit caps for acceptance path listing and probe tails.
CHANGED_PATHS_CAP = 5000
PROBE_OUTPUT_CAP_BYTES = 64 * 1024

DEFAULT_REQUIRED_SECTIONS: List[str] = [
    "INTENT",
    "ALLOWED",
    "FORBIDDEN",
    "DONE MEANS",
    "GATE",
    "Known errors",
    "Architecture rules",
]

RESEARCH_REQUIRED_SECTIONS: List[str] = [
    "INTENT",
    "FORBIDDEN",
]

BUILTIN_BRIEF_PROFILES: Dict[str, Any] = {
    "code": {
        "required_sections": list(DEFAULT_REQUIRED_SECTIONS),
        "require_area_rules": True,
        "require_allowed": True,
    },
    "research": {
        "required_sections": list(RESEARCH_REQUIRED_SECTIONS),
        "require_area_rules": False,
        "require_allowed": False,
    },
}


def default_config() -> Dict[str, Any]:
    return {
        "required_sections": list(DEFAULT_REQUIRED_SECTIONS),
        "navigate_timeout_s": 10,
        "delivery_cap_bytes": 16384,
        "known_errors_limit": 5,
        "journal_rotate_bytes": 5 * 1024 * 1024,
        "algorithm_gate": ALGORITHM_GATE,
        "algorithm_delivery": ALGORITHM_DELIVERY,
        # Portion 2 — dispatch / guards (FR-011…FR-013, FR-017).
        "executors": {},
        "delegate_guard": "envelope",
        "brief_profiles": {
            "code": dict(BUILTIN_BRIEF_PROFILES["code"]),
            "research": dict(BUILTIN_BRIEF_PROFILES["research"]),
        },
        "launcher_scripts": [],
        "watch_interval_s": 10,
        "max_timeout_s": 7200,
        # Portion 3 — acceptance / coverage (FR-014…FR-016).
        "gate_enabled": True,
        "changed_paths_cap": CHANGED_PATHS_CAP,
        "probe_output_cap_bytes": PROBE_OUTPUT_CAP_BYTES,
        # Optional probe list when the target repo has no .lean-orchestration/probes.json (SEC-1).
        "probes": None,
    }


def merge_config(overrides: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Return a validated config dict. Invalid values fail fast (CFG-2, API-7)."""
    cfg = default_config()
    if overrides:
        for key, value in overrides.items():
            if key not in cfg:
                raise ValueError("unknown config key: %s" % key)
            if key == "brief_profiles" and isinstance(value, dict):
                merged = dict(cfg["brief_profiles"])
                for pname, pval in value.items():
                    if not isinstance(pval, dict):
                        raise ValueError("brief_profiles.%s must be an object" % pname)
                    base = dict(merged.get(pname) or {})
                    base.update(pval)
                    merged[pname] = base
                cfg["brief_profiles"] = merged
            elif key == "executors" and isinstance(value, dict):
                cfg["executors"] = dict(value)
            elif key == "probes":
                cfg["probes"] = value
            else:
                cfg[key] = value
    _validate(cfg)
    return cfg


def load_config_file(path: str) -> Dict[str, Any]:
    """Load config JSON from path (UTF-8). Missing/invalid → clear error (CFG-2, CFG-5)."""
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            raw = json.load(fh)
    except FileNotFoundError as exc:
        raise ValueError("config file not found: %s" % path) from exc
    except OSError as exc:
        raise ValueError("config file unreadable: %s (%s)" % (path, exc)) from exc
    except json.JSONDecodeError as exc:
        raise ValueError("config file is not valid JSON: %s (%s)" % (path, exc)) from exc
    if not isinstance(raw, dict):
        raise ValueError("config file must be a JSON object: %s" % path)
    return merge_config(raw)


def resolve_profile(cfg: Mapping[str, Any], profile: Optional[str]) -> Dict[str, Any]:
    """Return the named brief profile dict. Missing → ValueError naming the key."""
    name = (profile or "code").strip() or "code"
    profiles = cfg.get("brief_profiles") or {}
    if name not in profiles:
        raise ValueError("brief_profiles.%s" % name)
    raw = profiles[name]
    if not isinstance(raw, dict):
        raise ValueError("brief_profiles.%s" % name)
    return {
        "name": name,
        "required_sections": list(raw.get("required_sections") or DEFAULT_REQUIRED_SECTIONS),
        "require_area_rules": bool(raw.get("require_area_rules", True)),
        "require_allowed": bool(raw.get("require_allowed", True)),
    }


def get_executor(cfg: Mapping[str, Any], name: str) -> Dict[str, Any]:
    """Return a validated executor entry. Missing → ValueError naming the key."""
    executors = cfg.get("executors") or {}
    if name not in executors:
        raise ValueError("executors.%s" % name)
    entry = executors[name]
    if not isinstance(entry, dict):
        raise ValueError("executors.%s" % name)
    argv = entry.get("argv")
    if not isinstance(argv, list) or not argv or not all(isinstance(x, str) for x in argv):
        raise ValueError("executors.%s.argv" % name)
    out: Dict[str, Any] = {
        "name": name,
        "argv": list(argv),
        "env": dict(entry.get("env") or {}) if isinstance(entry.get("env"), dict) else {},
        "timeout_s": int(entry.get("timeout_s", 3600)),
        "grace_s": int(entry.get("grace_s", 20)),
        "binary_names": [str(x) for x in (entry.get("binary_names") or [])],
    }
    if out["timeout_s"] <= 0:
        raise ValueError("executors.%s.timeout_s" % name)
    if out["grace_s"] < 0:
        raise ValueError("executors.%s.grace_s" % name)
    return out


def _validate(cfg: MutableMapping[str, Any]) -> None:
    sections = cfg.get("required_sections")
    if not isinstance(sections, list) or not all(isinstance(s, str) and s.strip() for s in sections):
        raise ValueError("required_sections must be a non-empty list of strings")
    for key, typ in (
        ("navigate_timeout_s", (int, float)),
        ("delivery_cap_bytes", int),
        ("known_errors_limit", int),
        ("journal_rotate_bytes", int),
        ("watch_interval_s", (int, float)),
        ("max_timeout_s", (int, float)),
        ("changed_paths_cap", int),
        ("probe_output_cap_bytes", int),
    ):
        val = cfg.get(key)
        if not isinstance(val, typ) or (isinstance(val, bool)):
            raise ValueError("%s must be a number" % key)
        if key in ("navigate_timeout_s", "watch_interval_s", "max_timeout_s") and float(val) <= 0:
            raise ValueError("%s must be > 0" % key)
        if key not in ("navigate_timeout_s", "watch_interval_s", "max_timeout_s") and int(val) <= 0:
            raise ValueError("%s must be > 0" % key)

    if not isinstance(cfg.get("gate_enabled"), bool):
        raise ValueError("gate_enabled must be a boolean")

    probes = cfg.get("probes")
    if probes is not None and not isinstance(probes, list):
        raise ValueError("probes must be a list or null")
    for key in ("algorithm_gate", "algorithm_delivery"):
        if not isinstance(cfg.get(key), str) or not cfg[key].strip():
            raise ValueError("%s must be a non-empty string" % key)

    dg = cfg.get("delegate_guard")
    if dg not in ("envelope", "off"):
        raise ValueError("delegate_guard must be 'envelope' or 'off'")

    launchers = cfg.get("launcher_scripts")
    if not isinstance(launchers, list) or not all(isinstance(s, str) for s in launchers):
        raise ValueError("launcher_scripts must be a list of strings")

    executors = cfg.get("executors")
    if not isinstance(executors, dict):
        raise ValueError("executors must be an object")
    for ename, entry in executors.items():
        if not isinstance(entry, dict):
            raise ValueError("executors.%s" % ename)
        argv = entry.get("argv")
        if not isinstance(argv, list) or not all(isinstance(x, str) for x in argv):
            raise ValueError("executors.%s.argv" % ename)
        if "timeout_s" in entry:
            try:
                t = int(entry["timeout_s"])
            except (TypeError, ValueError) as exc:
                raise ValueError("executors.%s.timeout_s" % ename) from exc
            if t <= 0:
                raise ValueError("executors.%s.timeout_s" % ename)
        if "grace_s" in entry:
            try:
                g = int(entry["grace_s"])
            except (TypeError, ValueError) as exc:
                raise ValueError("executors.%s.grace_s" % ename) from exc
            if g < 0:
                raise ValueError("executors.%s.grace_s" % ename)
        if "binary_names" in entry and not isinstance(entry["binary_names"], list):
            raise ValueError("executors.%s.binary_names" % ename)
        if "env" in entry and not isinstance(entry["env"], dict):
            raise ValueError("executors.%s.env" % ename)

    profiles = cfg.get("brief_profiles")
    if not isinstance(profiles, dict) or not profiles:
        raise ValueError("brief_profiles must be a non-empty object")
    for pname, pval in profiles.items():
        if not isinstance(pval, dict):
            raise ValueError("brief_profiles.%s" % pname)
        rs = pval.get("required_sections")
        if rs is not None:
            if not isinstance(rs, list) or not all(isinstance(s, str) and s.strip() for s in rs):
                raise ValueError("brief_profiles.%s.required_sections" % pname)

    cfg["navigate_timeout_s"] = float(cfg["navigate_timeout_s"])
    cfg["delivery_cap_bytes"] = int(cfg["delivery_cap_bytes"])
    cfg["known_errors_limit"] = int(cfg["known_errors_limit"])
    cfg["journal_rotate_bytes"] = int(cfg["journal_rotate_bytes"])
    cfg["watch_interval_s"] = float(cfg["watch_interval_s"])
    cfg["max_timeout_s"] = float(cfg["max_timeout_s"])
    cfg["changed_paths_cap"] = int(cfg["changed_paths_cap"])
    cfg["probe_output_cap_bytes"] = int(cfg["probe_output_cap_bytes"])
    cfg["gate_enabled"] = bool(cfg["gate_enabled"])
    cfg["required_sections"] = [s.strip() for s in sections]
    cfg["launcher_scripts"] = [str(s) for s in launchers]
