# Changelog

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
