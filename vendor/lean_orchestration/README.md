# lean-orchestration

Hermes Agent plugin that makes orchestration rules for coding executors **mechanical and deterministic**.

A brief reaches an executor only through `orch_dispatch`. The plugin refuses incomplete briefs, appends the project's architecture rules, **area-linked** known-error full blocks, and lexical candidate **titles** (`lo-delivery/2`), and blocks the usual side doors (`delegate_task` without envelope, terminal launches of configured executor binaries). No model calls, network, or randomness inside the decision.

## Portion 0.1 (this tree)

- `lo_core/` — deterministic core (brief parser, rules source, gate, task assembly, envelope, bypass, accept decide, journal)
- `lo_exec/` — process/ledger side (dispatch, accept, detached supervisor, ledger, restart reconcile)
- `cli.py` — launcher entry (`gate`, `task`, `dispatch`, `accept`); same core as the tools
- Hermes tools `orch_gate`, `orch_dispatch`, `orch_accept`; slash command `/orch`; skill `lean-orchestration`
- Hooks `pre_tool_call` (delegate + bypass guards) and `on_session_start` (reconcile)
- Rules via DEV Framework ≥ 1.7.0 `navigate.py --json` ([contract](docs/CONTRACT-navigate-json.md)), else `.lean-orchestration/rules.json`, else an explicit `Rules: none in project`
- Acceptance by code: changed paths ⊆ ALLOWED, fresh gate bound to `tree_sha256`, probes from `.lean-orchestration/probes.json`
- `gate_enabled` (default true) — disable only in plugin config; never from the brief

**Bypass refusal** matches command text (and `execute_code` AST string constants): protection
against *accidental* bypass, not a sandbox. Wrappers are recognised by basename
(`/usr/bin/env`, `/usr/bin/nohup`, …); `python3 -B run_cursor.py`-style launches and
`bash -c '…'` are refused when they start a configured binary or launcher. Nested
`terminal()` calls inside `execute_code` are covered by the `terminal` hook itself.

## Quick CLI

```bash
python3 cli.py gate --repo /abs/path/to/project --brief /abs/path/to/BRIEF.md --json
python3 cli.py task --repo /abs/path/to/project --brief /abs/path/to/BRIEF.md
python3 cli.py dispatch --repo /abs/path --brief /abs/BRIEF.md --workdir /abs/path \
  --executor fake --data-dir /abs/plugin-data --config /abs/config.json
python3 cli.py accept --dispatch <id> --data-dir /abs/plugin-data --json
python3 cli.py accept --repo /abs/path --brief /abs/BRIEF.md --base <commit> --json
```

Exit codes: `0` PASS / dispatched / ACCEPTED, `3` REFUSED / REJECTED, `4` rules-source error, `2` usage.
`dispatch` prints the dispatch id and does not wait.

## Config example (Cursor CLI — EXAMPLE only, never a default)

Executors come **only** from user config. There is no built-in default executor and no default flags.

```json
{
  "executors": {
    "cursor": {
      "argv": [
        "/Users/<you>/.local/bin/cursor-agent",
        "-p",
        "--force",
        "--output-format",
        "text",
        "Read {task_file} and execute it exactly."
      ],
      "timeout_s": 3600,
      "grace_s": 20,
      "binary_names": ["cursor-agent"]
    }
  },
  "launcher_scripts": ["run_cursor.py"],
  "delegate_guard": "envelope",
  "watch_interval_s": 10,
  "gate_enabled": true,
  "brief_profiles": {
    "code": {
      "required_sections": ["INTENT", "ALLOWED", "FORBIDDEN", "DONE MEANS", "GATE", "Known errors", "Architecture rules"],
      "require_area_rules": true
    },
    "research": {
      "required_sections": ["INTENT", "FORBIDDEN"],
      "require_area_rules": false,
      "require_allowed": false
    }
  }
}
```

Placeholders in `argv`: `{task_file}`, `{workdir}`, `{receipt}`.

Completion lines use keyless `inject_message` bound to the dispatching call's context
(no `session_key` argument on the tools). After a Hermes restart, reconciled completions
are posted once on the next `orch_gate` / `orch_dispatch`. Gateway sessions that should
receive those lines need `allow_gateway_injection: true` for this plugin.

## Brief grammar (API-4)

**Section headings.** A line `## <text>` (outside a fenced code block) matches required
section `name` when the lowercased trimmed text equals `name`, or starts with `name`
followed by optional spaces and then one of `(`, `—`, `–`, `-`, `:`. Examples:
`## FORBIDDEN (сверх COMMON)`, `## DONE MEANS (тесты…)`, `## Known errors — field`.
Several headings that match the same name concatenate bodies in document order.
Headings inside Markdown fences (`` ``` `` / `~~~`, indent ≤ 3 spaces) are not headings.

**ALLOWED brace patterns.** A backtick token may contain one or more `{a,b,…}` groups
(no nesting). Groups expand to the cartesian product left-to-right before path→areas
matching; empty alternatives are allowed (`{,Tests}`). Cap: 64 expanded patterns per
token — over the cap → refusal `allowed_pattern_too_broad:<token>` (BULK-3). Nested or
unbalanced braces stay literal and appear in `unmapped`. The gate result's `allowed`
field lists the expanded patterns.

**Profiles.** `orch_dispatch(profile="research")` selects the research required sections
(INTENT + FORBIDDEN). A brief cannot pick its own profile.

## Tests

Run with `TMPDIR` **outside** any DEV Framework project (see `KE-2026-11-20-TMPDIR-NESTED-PROJECT`):

```bash
TMPDIR=/tmp/lo-scratch python3 -B .devframework/check.py finish
```
