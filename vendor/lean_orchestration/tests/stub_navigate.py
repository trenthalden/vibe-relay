"""Contract-shaped stub for `.devframework/navigate.py` used in fixture projects.

Copy this file into a fixture's `.devframework/navigate.py`, or import helpers from tests.
"""
from __future__ import annotations

# This module is a library for tests; the actual stub script is written by fixture helpers.
STUB_NAVIGATE_SOURCE = r'''#!/usr/bin/env python3
"""STUB navigate.py — answers lean-orchestration contract commands from fixture data."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, ".lean-orchestration-stub", "data.json")

_RULES_LINE = re.compile(
    r"^\*\*(?:Rules|Areas):\*\*\s*(.+)$",
    re.IGNORECASE | re.MULTILINE,
)
_RULE_ID_TOKEN = re.compile(r"\b([A-Z]+)-\d+\b")


def emit(obj):
    raw = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    sys.stdout.buffer.write(raw.encode("utf-8"))


def load_data():
    with open(DATA, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _parse_areas_arg(value):
    """Accept AREA[,AREA ...] as one token or multiple argv tokens."""
    if value is None:
        return []
    if isinstance(value, list):
        parts = []
        for item in value:
            parts.extend(str(item).split(","))
        return [p.strip().upper() for p in parts if p.strip()]
    return [p.strip().upper() for p in str(value).split(",") if p.strip()]


def _links_for_item(item, area_set, health_ids):
    """Contract §3 linking: Rules/Areas line, cited rule ids, or fixture links field."""
    found = []
    text = str(item.get("block") or "") + "\n" + str(item.get("title") or "")
    for match in _RULES_LINE.finditer(text):
        for token in re.split(r"[,;\s]+", match.group(1).strip()):
            token = token.strip().rstrip(".")
            if not token:
                continue
            upper = token.upper()
            if upper in area_set and upper not in found:
                found.append(upper)
            elif token in health_ids:
                area = token.split("-", 1)[0].upper()
                if area in area_set and token not in found:
                    found.append(token)
    for match in _RULE_ID_TOKEN.finditer(text):
        rid = match.group(0)
        area = match.group(1).upper()
        if rid in health_ids and area in area_set and rid not in found:
            found.append(rid)
    for link in item.get("links") or []:
        link_s = str(link)
        upper = link_s.upper()
        if upper in area_set and upper not in found:
            found.append(upper)
        elif link_s in health_ids:
            area = link_s.split("-", 1)[0].upper()
            if area in area_set and link_s not in found:
                found.append(link_s)
    return sorted(found)


def main(argv=None):
    data = load_data()
    argv = list(sys.argv[1:] if argv is None else argv)
    # Contract places --json after the subcommand args; accept it anywhere.
    if "--json" in argv:
        argv = [a for a in argv if a != "--json"]
        want_json = True
    else:
        want_json = False
    if data.get("reject_json"):
        # Mimic DEV Framework < 1.7.0 argparse rejection of --json.
        sys.stderr.write("error: unrecognized arguments: --json\n")
        return 2
    if not want_json:
        sys.stderr.write("stub requires --json\n")
        return 2

    parser = argparse.ArgumentParser()
    parser.add_argument("--root")
    sub = parser.add_subparsers(dest="cmd")

    h = sub.add_parser("health")
    h.add_argument("areas_csv", nargs="?")
    h.add_argument("--paths", nargs="*")

    ke = sub.add_parser("known-errors")
    ke.add_argument("--terms", nargs="*")
    ke.add_argument("--text-file")
    ke.add_argument("--paths", nargs="*")
    ke.add_argument("--ids", nargs="*")
    ke.add_argument("--areas", nargs="*")
    ke.add_argument("--limit", type=int, default=10)
    ke.add_argument("--full", action="store_true")

    ids_p = sub.add_parser("ids")
    ids_p.add_argument("--check", nargs="+")

    args = parser.parse_args(argv)
    version = data.get("framework_version", "1.7.0")

    if args.cmd == "health":
        if args.paths is not None:
            path_map = data.get("path_areas", {})
            areas = set(["SCOPE"])
            matched = []
            unmapped = []
            for path in args.paths:
                hit = path_map.get(path)
                if hit:
                    areas.update(hit)
                    matched.append({"path": path, "areas": sorted(hit), "globs": data.get("globs", {}).get(path, [])})
                else:
                    # substring / glob-ish: any key that is a prefix pattern
                    found = None
                    for key, vals in path_map.items():
                        if path == key or path.startswith(key.rstrip("*").rstrip("/")) or key.rstrip("*") in path:
                            found = vals
                            break
                    if found:
                        areas.update(found)
                        matched.append({"path": path, "areas": sorted(found), "globs": [key]})
                    else:
                        unmapped.append(path)
            area_list = sorted(areas)
            rows = [r for r in data.get("rows", []) if r.get("area") in area_list]
            emit({
                "schema": "devframework.health/1",
                "framework_version": version,
                "areas": area_list,
                "matched": matched,
                "unmapped": unmapped,
                "rows": rows,
            })
            return 0
        areas = ["SCOPE"] + sorted(data.get("default_areas", []))
        rows = [r for r in data.get("rows", []) if r.get("area") in set(areas)]
        emit({
            "schema": "devframework.health/1",
            "framework_version": version,
            "areas": areas,
            "rows": rows,
        })
        return 0

    if args.cmd == "known-errors":
        modes = sum([
            args.ids is not None,
            args.areas is not None,
            args.text_file is not None or args.terms is not None or args.paths is not None,
        ])
        # Combining --areas with --ids/--terms/--text-file/--paths is bad_args.
        if args.areas is not None and (args.ids is not None or args.text_file or args.terms or args.paths):
            emit({
                "schema": "devframework.error/1",
                "framework_version": version,
                "error": "bad_args",
                "message": "known-errors --areas cannot combine with --ids/--terms/--text-file/--paths",
            })
            return 2
        if args.ids is not None and (args.text_file or args.terms or args.paths):
            emit({
                "schema": "devframework.error/1",
                "framework_version": version,
                "error": "bad_args",
                "message": "known-errors --ids cannot combine with search flags",
            })
            return 2

        if args.ids is not None:
            by_id = {k["id"]: k for k in data.get("known_errors", [])}
            cands = []
            seen = set()
            for kid in args.ids:
                if kid in seen:
                    continue
                seen.add(kid)
                item = by_id.get(kid)
                if not item:
                    continue
                entry = {
                    "id": item["id"],
                    "title": item.get("title", ""),
                    "status": item.get("status", ""),
                    "do_not_return": item.get("do_not_return", ""),
                    "score": 0.0,
                    "matched": [],
                }
                if args.full:
                    entry["block"] = item.get("block", "")
                cands.append(entry)
            emit({
                "schema": "devframework.known-errors/1",
                "framework_version": version,
                "algorithm": "ke-lexical/1",
                "candidates": cands,
                "total": len(data.get("known_errors", [])),
            })
            return 0

        if args.areas is not None:
            area_set = set(_parse_areas_arg(args.areas))
            health_ids = {r["id"] for r in data.get("rows", []) if r.get("id")}
            cands = []
            # Document order of known_errors.
            for item in data.get("known_errors", []):
                links = _links_for_item(item, area_set, health_ids)
                if not links:
                    continue
                entry = {
                    "id": item["id"],
                    "title": item.get("title", ""),
                    "status": item.get("status", ""),
                    "do_not_return": item.get("do_not_return", ""),
                    "score": 0.0,
                    "matched": [],
                    "links": links,
                }
                if args.full:
                    entry["block"] = item.get("block", "")
                cands.append(entry)
            emit({
                "schema": "devframework.known-errors/1",
                "framework_version": version,
                "algorithm": "ke-areas/1",
                "candidates": cands,
                "total": len(data.get("known_errors", [])),
            })
            return 0

        # Lexical candidates by precomputed rank in data (titles; --full optional).
        cands = list(data.get("candidates", data.get("known_errors", [])))
        limit = args.limit if args.limit is not None else 10
        out = []
        for item in cands[:limit]:
            entry = {
                "id": item["id"],
                "title": item.get("title", ""),
                "status": item.get("status", ""),
                "do_not_return": item.get("do_not_return", ""),
                "score": round(float(item.get("score", 1.0)), 4),
                "matched": sorted(item.get("matched", [])),
            }
            if args.full:
                entry["block"] = item.get("block", "")
            out.append(entry)
        emit({
            "schema": "devframework.known-errors/1",
            "framework_version": version,
            "algorithm": "ke-lexical/1",
            "candidates": out,
            "total": len(data.get("known_errors", [])),
        })
        return 0

    if args.cmd == "ids":
        known_set = set(data.get("known_ids", []))
        known = []
        unknown = []
        seen = set()
        for item in args.check or []:
            if item in seen:
                continue
            seen.add(item)
            if item in known_set:
                kind = "known_error" if item.startswith("KE-") else (
                    "requirement" if item.startswith(("FR-", "NFR-")) else (
                        "use_case" if item.startswith("UC-") else "health_rule"
                    )
                )
                known.append({"id": item, "kind": kind})
            else:
                unknown.append(item)
        emit({
            "schema": "devframework.ids/1",
            "framework_version": version,
            "known": known,
            "unknown": unknown,
        })
        return 0

    emit({
        "schema": "devframework.error/1",
        "framework_version": version,
        "error": "bad_args",
        "message": "unknown command",
    })
    return 2


if __name__ == "__main__":
    sys.exit(main())
'''
