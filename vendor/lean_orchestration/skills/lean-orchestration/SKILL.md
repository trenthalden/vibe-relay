---
name: lean-orchestration
description: Use the deterministic orch_gate / orch_dispatch / orch_accept door for coding executors. Refuse incomplete briefs; never bypass with terminal commands.
version: 0.1.0
---

# lean-orchestration — the one door to an executor

Use this skill whenever a coding brief should reach Cursor or another configured executor.
The plugin is **deterministic**: every gate, task and accept verdict is a pure function of
(brief, project rule files, plugin config, algorithm version). Do not re-judge the brief
yourself and do not launch the executor from a terminal.

## When to use which tool

| Tool | Use when |
|---|---|
| `orch_gate` | Dry-run: will this brief PASS? What rules/KEs would be delivered? |
| `orch_dispatch` | Start a configured CLI executor, or get a sealed `hermes_child` payload |
| `orch_accept` | After the executor finishes: accept by code (paths, gate, probes) |
| `/orch` | Coverage: gate on/off, last dispatches, accept counters |

## Brief sections (profile `code`)

Required: `INTENT`, `ALLOWED`, `FORBIDDEN`, `DONE MEANS`, `GATE`, `Known errors`,
`Architecture rules`. Headings may carry a documented qualifier after the name
(`(…)`, `—`, `–`, `-`, `:`). Fenced `##` lines are not headings.

Known errors form when nothing applies:

```
KE: none relevant (searched: <what you searched>)
```

Profile `research` needs only `INTENT` + `FORBIDDEN` (selected by the call argument
`profile="research"`, never by the brief text).

## What the guards refuse

- Incomplete / empty required sections, unknown cited ids, missing area rules for
  ALLOWED paths, porting without `path:line` + `Call site:`.
- `delegate_task` without a valid lean-orchestration envelope (use
  `orch_dispatch(executor="hermes_child")` and pass the payload unchanged).
- `terminal` / `execute_code` that starts a configured executor binary or launcher
  script — use `orch_dispatch` instead. This stops accidental bypass, not a sandbox.

## Acceptance

`orch_accept(dispatch_id)` checks: changed paths ⊆ ALLOWED, a fresh gate PASS bound to
the tree hash of the changed set, and probes from `.lean-orchestration/probes.json`.
The executor's receipt is a self-report — never the acceptance decision.

## Config-only disable

`gate_enabled: false` in plugin config disables the gate (journal + `/orch` show it).
A brief line or tool argument saying `gate_enabled: false` has **no** effect.

## Never

- Do not run `cursor-agent` / configured binaries from the terminal to skip the door.
- Do not edit `TASK.md` and re-launch outside `orch_dispatch`.
- Do not claim acceptance from a receipt alone — call `orch_accept`.
