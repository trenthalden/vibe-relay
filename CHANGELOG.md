# Changelog

## 0.3.2

- **Review fixes (teknium1 / PR #150547):** confine checklist/handoff slugs in vendored `navigate.py` (and park handoff) to `[A-Za-z0-9._-]+` with a resolved-path check under `root/docs` before any read/write/unlink; respect the Hermes cron loop guard (`HERMES_CRON_SESSION` + `cron.allow_agent_scheduling`) so auto-park and `/vibe park` do not schedule a resume timer from a cron session unless the gate is on (block message says so); drop private `agent.account_usage_cache.cached_account_usage` and keep only public `fetch_account_usage(..., read_only=True)` with the 15 s negative TTL; `df_init` also refuses `$HOME` itself and filesystem root.

## 0.3.1

- **Fix vibe_accept TOOL_ERROR on deferred tools:** `provider.route_task` no longer overwrites `last_gate` (PASS record dict) with the gate *command string*; stores `last_gate_command` instead. Handlers (`vibe_accept`, `df_init`/`df_check`/`df_nav`) coerce JSON-string args and treat non-dict state fields safely — no more `AttributeError: 'str' object has no attribute 'get'`.
- **Fix stop+park miss on live `chat --oneshot`:** empty `fetch_account_usage` results no longer poison the plugin quota cache for 180s (negative TTL 15s). Falls back to Hermes in-process `cached_account_usage` (warmed by prior turns / `/usage`) so `should_park` sees real used% after `state.json` wipe + `policy auto`.
- **Fix auto-park after gateway plugin reload:** `register()` reloads sibling modules; `ensure_auto_park` reloads `park` before calling `park(..., auto=True)` so a stale pre-0.3.0 `park` in `sys.modules` cannot drop the handoff/cron after a successful stop block.

## 0.3.0


- **Enforced stop-at-threshold + auto park:** when vibe-relay is the active backend and routing says `should_park` (used ≥ `stop_used_percent`, default 90, or exhausted), a `pre_tool_call` hook blocks the **parent** `delegate_task` and file-writing tools with `vibe-relay: quota used N% ≥ stop M% — work parked; resume scheduled at <local time> (reset + buffer).` Reads stay allowed; children never blocked. On the first block, auto-parks **once per reset window** (handoff + one one-shot cron via `park()`); repeated blocks do not create duplicate jobs. `pre_llm_call` context states `parked until …; do not start new work`. Block lifts when used drops below stop or wall-clock passes `reset_at`.
- Live demo toy initialises vendored **DEV Framework** via real `df_init`; gate is `python .devframework/check.py finish` (project.json test = toy unittest).
- Resume-now for demos: `hermes cron run <job_id>` (LIVE Hermes CLI).

## 0.2.4

- Enforce the cheap-executor lane: when vibe-relay is the active backend and routing selects executor (`auto` with remaining ≤ threshold, or `policy=executor-always`), a `pre_tool_call` hook blocks the **parent** from `patch` / `write_file` and from terminal commands that obviously write (`sed -i`, `>` / `>>`, `tee`). Children are never blocked. Config: `delegation.backends.vibe-relay.enforce_executor_lane` (default `true`).
- Registers `pre_tool_call` + `subagent_start` (child id tracking) alongside existing hooks.

## 0.2.3

- Live-demo hardening: `delegate_task` schema/description and `lean-executor-orchestration` skill now steer the parent to route coding fixes with required `allowed` + `gate` (then `vibe_accept`) instead of editing in-parent.
- `scripts/live_demo.py` harness for a disposable profile + toy repo (outputs under `out/live-demo/`).

## 0.2.2

- Security (maintainer review): `df_nav` runs the plugin’s vendored `navigate.py` with `--root` under `python -I` (target code not on the child import path); DF child env scrubbed (no full `os.environ`); `df_init` refuses Hermes home and protected `$HOME` dot-folders and requires a git work tree or the session cwd.
- `acceptance`: default cwd is the session terminal cwd via `agent.runtime_cwd.resolve_agent_cwd()`.
- `park`: clear `pending_park` before scheduling so a later `/vibe park` is not permanently skipped.
- README disclosure updated for what runs and which env vars are passed.

## 0.2.1

- Removed in-repo privacy-name scan from workshop tests (privacy checks stay in external review tooling).
- Honest disclosure: `df_init` local Python child installer, acceptance `git` child processes, template scripts may call `git`/`gh` when run.
- README evidence from daily use filled; credit for condensed Hermes bundled skill adaptations.
- Catalog draft: drop `provides_skills` (unknown capabilities key under official validator).

## 0.2.0

- Agent Workshop skill pack (14 skills) under `skills/`, registered via `ctx.register_skill` as `vibe-relay:<name>`; `skills/INDEX.md`.
- Privacy scan over the whole repo + frontmatter parse tests for every workshop skill.
- README / catalog description updated for the skill pack.

## 0.1.0

- Initial MVP: quota cache, `vibe-relay` delegation backend, gate capture + `pre_verify`, `vibe_accept`, `/vibe` park/resume, vendored DEV Framework tools (`df_check` returns terminal command).
- Plugin root on `sys.path` so sibling modules resolve under Hermes load and `hermes plugins validate` capability probe.
