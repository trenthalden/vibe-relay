# lean-orchestration

A Hermes Agent plugin that turns orchestration rules for coding executors into code.

When one model plans and another one codes, the plan usually carries a written rule: *read the project's known
errors and architecture rules, cite them in the brief*. Under time pressure that step gets skipped, and defect
classes the project has already recorded come back. This plugin makes the step mechanical: a brief reaches an
executor only through one door, the door refuses an incomplete brief, and the project's rules are delivered to the
executor by code, not by the orchestrator's memory.

![How a brief reaches the executor](assets/flow.svg)

## What it does

- **Gate.** `orch_dispatch` checks the brief before anything starts: required sections are present, every cited id
  exists in the project, and at least one architecture rule is cited for every area the `ALLOWED` paths touch.
  A refused brief starts nothing and returns the reasons.
- **Rules delivery.** The task the executor receives is the brief plus the rule rows of the touched areas, the full
  text of every known error linked to those rules or cited in the brief, and the titles of other likely candidates.
- **One door.** A `pre_tool_call` guard refuses the side doors: a configured executor CLI or launcher script started
  from the terminal, and `delegate_task` without the sealed envelope that `orch_dispatch` returns.
- **Acceptance by code.** `orch_accept` checks that the changed paths stay inside `ALLOWED`, re-runs the gate on the
  same tree and runs the project's probes. The verdict is `ACCEPTED` or `REJECTED` with reasons.
- **Coverage.** `/orch` shows whether the gate is on, the last dispatches and the counters.
- **Deterministic.** The gate, the task assembly and the acceptance decision use no model calls, network, clock or
  randomness. The same brief, project files and config give the same verdict and the same task bytes.

## Requirements

- Hermes Agent ≥ 0.22.5 (verified on 0.23.2).
- Target projects on [DEV Framework](https://github.com/trenthalden/dev-framework) ≥ 1.7.0: the plugin reads rules
  through `navigate.py health | known-errors | ids --json`. Projects below 1.7.0 are refused with
  `framework_too_old`; upgrade them with the DEV Framework installer (`install.py --target <repo> --update`).
- Python 3.9+ (standard library only).

## Install

```bash
hermes plugins install https://github.com/trenthalden/lean-orchestration --force --enable
```

The plugin starts executor processes (`subprocess`), so the Hermes security scan rates it *caution* and a
community install needs `--force`. Read the code before you do that; nothing else in it needs elevated rights.

## Configure

Executors come only from your config — there is no built-in executor and no default flags. Example for the Cursor
CLI (`plugins.entries.lean-orchestration` in the profile's `config.yaml`):

```yaml
plugins:
  entries:
    lean-orchestration:
      allow_gateway_injection: true        # completion messages into gateway sessions
      settings:
        executors:
          cursor:
            argv:
              - /Users/<you>/.local/bin/cursor-agent
              - --print
              - "Read {task_file} and execute it exactly. Work only in {workdir}. Write the receipt to {receipt}."
            env: {TMPDIR: /path/outside/your/projects}
            timeout_s: 3600
            grace_s: 20
            binary_names: [cursor-agent]
        launcher_scripts: [run_cursor.py]  # your own launch scripts, blocked outside the plugin
        delegate_guard: envelope
```

Placeholders in `argv`: `{task_file}`, `{workdir}`, `{receipt}`. `gate_enabled` (default `true`) can be turned off
only here, never from a brief or a tool argument.

## Brief

Profile `code` requires these sections: `INTENT`, `ALLOWED`, `FORBIDDEN`, `DONE MEANS`, `GATE`, `Known errors`,
`Architecture rules`. A heading may carry a qualifier (`## FORBIDDEN (in addition to COMMON)`); headings inside
fenced code are ignored. When no known error applies, write `KE: none relevant (searched: <what you searched>)`.
Profile `research` (chosen by the call argument, never by the brief) needs only `INTENT` and `FORBIDDEN`.

In the target project, a known error reaches executors in full when it is cited in the brief or linked to an area
rule — by the rule id in its text or by a line such as `**Rules:** JOB-1, BULK-2`. The optional path → area map
lives in the project's `.devframework/project.json`.

## Use

| Tool | When |
|---|---|
| `orch_gate` | Dry run: will this brief pass, and what will be delivered? |
| `orch_dispatch` | Start a configured executor, or get a sealed payload for a Hermes child |
| `orch_accept` | After the completion message (CLI or child): accept or reject by code |
| `/orch` | Gate state, last dispatches (incl. children), counters |

The plugin ships the skill `lean-orchestration:lean-orchestration` with the same rules for the orchestrating
model. A CLI with the same core is available for scripts: `python3 cli.py gate | task | dispatch | accept`.

## Limits

- The guard stops accidental bypasses of the door. It is not a sandbox.
- Known errors that are neither cited nor linked to a rule reach the executor only as titles. Linking old entries
  with a `**Rules:**` line is up to the project.
- Completion messages arrive when the orchestrating session is free to receive them; a long turn delays them.

## Claude Code

The same directory is also a Claude Code plugin (`.claude-plugin/plugin.json`). It needs `python3` on `PATH`
(3.9+, standard library only).

```bash
claude --plugin-dir /path/to/lean-orchestration
```

| Hermes | Claude Code |
|---|---|
| tools `orch_gate`, `orch_dispatch`, `orch_accept` | the same tools from the plugin's MCP server `lo`; the brief text goes straight into `brief` |
| `pre_tool_call` on `terminal` | `PreToolUse` on `Bash`: a configured executor or launcher started outside the door is denied |
| `hermes_child` + `delegate_task` envelope | `executor="claude_subagent"` returns a sealed `agent` payload; `PreToolUse` on `Agent` denies a writing subagent without it. Read-only types (`Explore`, `Plan`, `claude-code-guide`) pass; change the list with `delegate_exempt_types` |
| completion message | a background `PostToolUse` hook (`asyncRewake`) wakes the session with `orch_dispatch ended id=…` |
| restart reconcile | `SessionStart` reports dispatches that ended while no session was open |
| `/orch` | `/lean-orchestration:orch` |

Configuration is `config.json` in the plugin data directory (`~/.claude/plugins/data/<plugin-id>/`). It holds the
`settings` object from the Hermes example, written as JSON. Set `"delegate_guard": "off"` there to allow every
subagent.

## Bundled variant

[vibe-relay](https://github.com/trenthalden/vibe-relay) vendors a pinned copy of this plugin together with DEV
Framework. If the standalone plugin is enabled, vibe-relay skips its copy, so the tools are not registered twice.

## License

MIT — see [LICENSE](LICENSE).
