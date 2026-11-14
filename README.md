# vibe-relay

![vibe-relay](assets/banner.png)

[▶ Demo video (≈90 s)](assets/demo.mp4) — live run: Claude Sonnet hands the edit to Claude Haiku, DEV Framework tests, pause at the quota limit and resume.

![demo](assets/shot-2.png)

Vibecoding without burning your best model: a Hermes delegation backend that routes coding work to yourself when quota remains, or to a cheap executor when it does not — and only counts work done after a green gate on the real diff, parking itself across usage-window resets. For big projects it adds a **night shift**: leave the agent alone overnight with a hard stop, quota-checked continuation wakes and a morning report built from records.

## Built on DEV Framework

The gate, project rules and `df_init` / `df_check` / `df_nav` come from **[DEV Framework](https://github.com/trenthalden/dev-framework)**, vendored here under `vendor/dev_framework/` (version in `vendor/dev_framework/VERSION`). vibe-relay adds quota routing, the executor lane, acceptance and park/resume on top of it.

Want only the framework, or want it in another coding agent? Use the parent repository:

| Host | Install |
|---|---|
| Hermes Agent (framework only) | `hermes plugins install trenthalden/dev-framework`, then `hermes plugins enable dev-framework` |
| Claude Code | `/plugin marketplace add trenthalden/dev-framework`, then `/plugin install dev-framework@dev-framework` |
| Cursor | copy or link the repository into `~/.cursor/plugins/local/dev-framework`, then restart Cursor |
| Any agent with a shell | clone it and run `python scripts/install.py` |

Both enabled in one Hermes profile? See [collision](#collision-with-a-standalone-dev-framework-plugin).

## 30-second quickstart

```bash
# In an isolated Hermes home (hackathon / CI pattern)
export HERMES_HOME=/path/to/isolated/home
hermes plugins install /path/to/vibe-relay
hermes config set delegation.backend vibe-relay
hermes config set delegation.backends.vibe-relay.executor_provider openrouter
hermes config set delegation.backends.vibe-relay.executor_model google/gemini-2.5-flash
# optional: policy executor-always | auto (default); stop_used_percent 90
```

Then in a session:

```text
/delegation vibe-relay --now
/vibe
delegate_task with lane=auto, allowed="src/**,tests/**", gate="python -m unittest discover -s tests -v"
# when the window is nearly gone:
/vibe park .
```

## Demo script

See [docs/DEMO.md](docs/DEMO.md) for a &lt;90 s shot list.

## Tools / commands

| Surface | Name | Role |
|---|---|---|
| Delegation backend | `vibe-relay` | `lane` auto\|self\|executor; requires `allowed` + `gate` |
| Tool | `vibe_accept` | Changed paths ⊆ allowed + fresh gate PASS → ACCEPT/REJECT |
| Tool | `df_init` / `df_check` / `df_nav` | Vendored DEV Framework (df_check returns a terminal command) |
| Slash | `/vibe` | Quota, mode, last gate, pending park |
| Slash | `/vibe park` | Handoff + one-shot cron at reset+buffer (clears prior pending_park) |
| Blueprint | `resume-after-reset` | Manual catalog twin of the park resume job |
| Tool | `vibe_night_start` / `vibe_night_log` / `vibe_night_status` / `vibe_night_arm` / `vibe_night_report` | Night shift: plan, records, liveness, wakes, morning report |
| Slash | `/night start\|status\|arm\|report\|stop` | Owner controls for the night shift |
| Hooks | `pre_llm_call`, `api_request_error`, `pre_tool_call`, `post_tool_call`, `pre_verify`, `subagent_start`, `subagent_stop` | Quota + stop context; enforce stop/park, executor lane and the night hard stop; capture gate; parent liveness stamp; track children |

## Configuration (`delegation.backends.vibe-relay`)

| Key | Default | Meaning |
|---|---|---|
| `policy` | `auto` | `auto` or `executor-always` |
| `self_remaining_threshold` | `51` | Remaining % above which `auto` stays on self |
| `stop_used_percent` | `90` | Used % that **enforces stop + auto-park** (see below) |
| `executor_provider` / `executor_model` | — | Cheap executor route |
| `executor_profile` | — | Or run child as another Hermes profile |
| `park_buffer_seconds` | `120` | Delay after `reset_at` before resume cron |
| `quota_provider` | active model provider | Passed to `fetch_account_usage(..., read_only=True)` |
| `enforce_executor_lane` | `true` | When vibe-relay is active and routing selects **executor**, block the **parent** from writing files itself (see below) |

### Stop-at-threshold + auto park (`stop_used_percent`)

When vibe-relay is `delegation.backend` **and** routing says `should_park` (account used ≥ `stop_used_percent`, default **90**, or exhausted):

- **Blocked for the parent:** `delegate_task`, `patch`, `write_file`, and the same conservative terminal writers as executor-lane (`sed -i`, `>` / `>>`, `tee`).
- **Still allowed:** reads, `vibe_accept`, gate/test commands (`unittest` / `pytest` / `python .devframework/check.py …` / `git status|diff`).
- **Never blocked:** delegate children.
- **Auto-park (once per reset window):** on the first blocked call, write the handoff and create **exactly one** one-shot cron via `park()` at `reset_at + park_buffer_seconds`. Repeated blocks / turns in the same window do **not** create more cron jobs (**cost: 1 cron job per reset window**).
- **Cron loop guard:** if the session is itself a Hermes cron run (`HERMES_CRON_SESSION`) and `cron.allow_agent_scheduling` is not true, park still writes the handoff and the stop hook still blocks, but **no resume cron is created** (message: *no resume timer was set because this is a scheduled run*). Same guard for manual `/vibe park`.
- **Context:** `pre_llm_call` adds `parked until …; do not start new work`.
- **Lifts automatically** when used drops below stop, or wall-clock passes `reset_at`.
- Manual `/vibe park` still works and schedules a fresh job when not inside a gated-off cron session.

Block message shape: `vibe-relay: quota used N% ≥ stop M% — work parked; resume scheduled at <local time> (reset + buffer).`

Demo without waiting for the real reset: `hermes cron run <job_id>` (LIVE Hermes CLI).

### Executor-lane enforcement (`enforce_executor_lane`)

When enabled (default) **and** vibe-relay is `delegation.backend` **and** the parent’s auto routing decision is `executor` (`policy=executor-always`, or `auto` with remaining ≤ `self_remaining_threshold`) **and** stop is not already active:

- **Blocked for the parent:** `patch`, `write_file`, and terminal commands that *obviously* write files — `sed -i` / `sed --in-place`, shell `>` / `>>` redirection, and `tee`. Kept deliberately conservative (other writers are not scanned).
- **Still allowed for the parent:** reads, `delegate_task`, `vibe_accept`, and gate/test commands (`unittest` / `pytest` / `git status|diff`, etc.).
- **Never blocked:** delegate children (tracked via `subagent_start` / `subagent_stop`, plus the `HERMES_DELEGATED_CHILD_CONTEXT` env marker).
- **Off / inactive:** set `enforce_executor_lane: false`, switch `delegation.backend` away from vibe-relay, or stay on the parent lane (high remaining under `auto`) — parent writes proceed as usual.

Block message shape: `vibe-relay: quota is low (N% left) — route this edit through delegate_task with allowed + gate; the cheap executor will make it.`

## Night shift

For a large project the owner leaves overnight: "work until morning, decide alone, I review when I wake up". The judgment
lives in the `vibe-relay:night-shift` skill; the plugin makes the mechanics hard to get wrong.

```text
/night start 07:30            # hard stop 07:30, stop dispatching 30 min earlier, quota reserve 10 %
vibe_night_log …              # queue rows, decisions taken without the owner, events — timestamped by code
/night arm                    # early checkpoint wake, one after the quota reset, one before the hard stop
/night status                 # liveness stamp, window, quota, queue counts, armed wakes
/night report                 # MORNING-REPORT.md from the records
```

- **Plan on disk, not in memory.** `<repo>/.vibe-relay/night-<stamp>/` holds `NIGHT-PLAN.md` (owner instructions verbatim,
  window and hard limits, lanes and file ownership, queue, decisions log, timeline), `COMMON.md` (shared worker rules) and
  `night.json`. The directory ships its own `.gitignore` (`*`), so your repository's ignore files are never edited.
- **Hard stop.** After `hard_stop − stop_margin` (or when the quota falls to the reserve) the `pre_tool_call` hook blocks
  new `delegate_task` calls and any tools / terminal command patterns you list in `night.blocked_tools` /
  `night.blocked_command_patterns`. Reads, `vibe_accept` and the night tools stay available; running work is not killed.
- **Liveness stamp.** Your own tool calls refresh `LAST-PARENT-UPDATE` (at most once a minute). A wake first calls
  `vibe_night_status` and exits with one line when the parent was active within `parent_active_minutes` (default 40), so it
  never becomes a second orchestrator. This is advisory: Hermes has no public "is that session alive" API.
- **Wakes that can actually run.** `vibe_night_arm` creates one-shot jobs through `cronjob_manage` (`repeat: 1`, delivered
  to the chat you armed them from, `vibe-relay:night-shift` skill loaded): an early `checkpoint` (default 90 min after the
  start, skipped when it would land next to the reset wake), `quota_reset` and `report`. Before arming it checks the quota of
  the provider the job will run on and refuses when it is unknown or the weekly reserve is gone; a wake that would run before
  the session reset while the session is already at the reserve is skipped. It also refuses when the profile's default
  provider (which cron uses) differs from the checked one. Plugins cannot choose a cron job's model; the refusal prints the
  `hermes cron edit <id> --provider … --model …` command for you. Wakes are armed from the live session only — cron runs
  cannot schedule further cron runs unless you enabled `cron.allow_agent_scheduling`.
- **Morning report from records.** `MORNING-REPORT.md` lists the queue with statuses and evidence, the last acceptance gate,
  decisions taken without you, wakes and their observed outcome, and everything not accepted under *Not done / not verified*.
  The report never marks anything done itself.

**cost:** hooks add an in-memory check per tool call and at most one small state write per minute; quota reads reuse the
180 s cache; up to three cron jobs per night by default (checkpoint, quota reset, report).

## Data & network disclosure / security

- **Network:** only through Hermes public `agent.account_usage.fetch_account_usage(provider, read_only=True)` (usable snapshots cached ≤ 180 s under plugin storage; empty/failed fetches use a **15 s negative TTL** so a brief miss after `state.json` wipe does not lock out stop/routing for a full TTL). No private `account_usage_cache` import, no direct credential file reads, no xAI/grok-cli paths.
- **Slug confinement:** vendored `df_nav` → `navigate.py` checklist/handoff (and vibe-relay park handoff) accept only `[A-Za-z0-9._-]+` slugs; reject `..`, empty, and leading-dot forms; the resolved path must stay under `root/docs` before any read, write, or unlink (blocks `../`, nested `a/b`, absolute paths, and symlinked docs/file escapes).
- **Cron loop guard:** park/auto-park will not call `cronjob_manage` when `HERMES_CRON_SESSION` is set unless `cron.allow_agent_scheduling` is true (core’s loop-prevention gate); the stop block still applies and says no resume timer was set because this is a scheduled run.
- **Reads outside plugin data:** acceptance runs read-only `git status` / `diff` / `ls-files` child processes on the working tree (default cwd = session terminal cwd via `agent.runtime_cwd.resolve_agent_cwd()`); optional handoff write under the target repo’s `docs/` (slug-confined).
- **Local child processes:**
  - `df_init` runs the **plugin’s** vendored installer (`vendor/dev_framework/scripts/install.py`) as an isolated Python child (`python -I -B`). It writes framework files only into an allowed target: a git work tree, or the session working directory / a path under it. It **refuses** `hermes_constants.get_hermes_home()` (and its subtree), **`$HOME` itself**, **filesystem root (`/`)**, and protected `$HOME` dot-folders (`.ssh`, `.aws`, `.config`, `.gnupg`, `.kube`, `.docker`).
  - `df_nav` runs the **plugin’s** vendored `template/.devframework/navigate.py` with `--root <target>` (cwd = that vendored folder, `python -I -B`). It does **not** execute the target repo’s `.devframework/navigate.py`.
  - Child env for those DF tool processes is scrubbed to: `PATH`, `HOME`, `LANG`/`LC_*`, `SYSTEMROOT` (Windows), `PYTHONDONTWRITEBYTECODE`. No parent `*_API_KEY` / secrets; `GH_TOKEN` is not passed (the optional `gh` visibility check is only in target `devlog`/`check` when the agent runs them via `terminal`).
  - Template scripts copied into a target repo may call `git` (and `gh` for devlog visibility) only when the user or agent runs them.
- **Shell / approvals:** does not bypass approvals. `df_check` returns the gate command for the agent to run via `terminal`. Park creates a cron job through `cronjob_manage` (normal tool path) except under the cron loop guard above. No YOLO / non-interactive shells.
- **Stored data:** `$HERMES_HOME/plugin-data/vibe-relay/state.json` (quota cache, last gate as pass/fail + tree hash, pending park, last route metadata, night-shift metadata: directory, times, job ids, liveness stamp). Gate commands, gate output and task text are not persisted there. Night plan content (instructions, queue, decisions, report) is written only inside the working repository under `.vibe-relay/` (self-ignored).
- **Scheduled work:** park and `vibe_night_arm` create one-shot cron jobs through `cronjob_manage`; `/night stop` removes the night's jobs. The plugin never sets a job's model or provider.

## Collision with a standalone `dev-framework` plugin

If `dev-framework` is already enabled, tool names `df_init` / `df_check` / `df_nav` may already be registered. vibe-relay detects that and **skips** re-registration (log line), keeping the already-loaded handlers. Disable the standalone plugin to use this repo’s vendored copy (`df_check` returns a terminal command instead of executing gates in-process).

## Agent Workshop skill pack

Fifteen portable agent-engineering skills ship under `skills/` and register as
`vibe-relay:<name>` (opt-in via `skill_view`). See [`skills/INDEX.md`](skills/INDEX.md).

| Skill | When |
|---|---|
| `agent-build-debug-loop` | Building or debugging long-lived agents |
| `agent-architecture-audit` | Auditing agent layers before blaming the model |
| `agent-harness-design` | Designing tools, cron, or eval harnesses |
| `context-budget-audit` | Auditing context or token bloat |
| `memory-engineering` | Engineering memory or retrieval |
| `known-errors` | Reading or appending a known-errors registry |
| `frozen-working-tree-review` | Strict read-only review of a frozen tree |
| `product-aware-engineering-releases` | Safe release splitting + owner-facing updates |
| `subagent-driven-development` | Plans via reviewed subagent tasks |
| `agent-maintenance` | Maintaining a long-lived companion agent |
| `lean-executor-orchestration` | Delegating code once; accept a receipt (2.0: worker choice, parallel worktree lanes, acceptance checklist) |
| `night-shift` | Leaving an agent alone overnight on a big project |
| `agent-field-acceptance` | Field-first acceptance of agent work |
| `test-driven-development` | Minimal, risk-proportional evidence |
| `systematic-debugging` | Root cause before fixing |

Merged near-duplicates: iterative-retrieval → `memory-engineering`; eval-harness → `agent-harness-design`; owner-facing communication → `product-aware-engineering-releases`. DEV Framework skills (`df-*`) remain available alongside.

Credit: `test-driven-development`, `systematic-debugging`, and `subagent-driven-development` are condensed adaptations of the Hermes bundled skills of the same names, registered namespaced as `vibe-relay:<name>` so they do not collide.

## Evidence from daily use

Before this plugin existed, the author ran the same loop by hand for 15 days (23 Oct – 6 Nov 2026) across 113 Hermes sessions: about 80 cheap-executor launches (Cursor CLI), 85 `delegate_task` calls, 8 read-only review runs and 8 one-shot continuations armed at a quota reset. Vibe Relay turns that manual routine — route by remaining quota, demand allowed paths and a gate, accept only a fresh PASS, park and resume at reset — into a plugin. Counts are from assistant turns in the author's local session store; they describe the workflow, not plugin telemetry.

The night shift comes from four overnight runs on a large agent project (Oct–Nov 2026). The best one (13–14 Nov) ran two
workers in separate worktrees: 20 dispatches, 14 portions accepted, 2 honest no-edit declines, 1 incomplete portion, both
safety-net wakes fired, final gate of 1,800 tests. The worst (7–8 Nov) lost the night: both wakes fired into an exhausted
quota (HTTP 429) and nothing had checked the quota of the model the jobs would run on — the reason `vibe_night_arm` checks
first. Counts are from the author's night plans and cron history, not plugin telemetry.

## Limitations

- Live executor quality and real provider quota endpoints need a signed-in profile (see `OPEN` in `RECEIPT.md`).
- `pre_verify` nudges the **parent** turn that edited code; with `enforce_executor_lane` the parent should not be editing in executor mode (child edits are observed via `subagent_stop` + gate capture on `terminal`).
- Cooperative workflow: path checks detect out-of-scope writes after the fact; they do not sandbox the executor. Executor-lane enforcement blocks only the listed parent write shapes — not every possible write vector.
- Night shift: the hard stop blocks new dispatch only; it does not stop workers already running. The liveness stamp is advisory. Wakes need the Hermes gateway (scheduler) running and the machine awake; one-shot jobs missed while the machine was off are skipped.

## License

License: TBD by author
