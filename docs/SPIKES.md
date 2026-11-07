# Spikes (W3a) — evidence before build

Isolated home: `$HERMES_HOME` under the hackathon scratch `homes/vibe-relay`.
Runner: Hermes 0.23 checkout `.venv/bin/python`. Raw outputs: `docs/spikes/s{1,2,3}_out.txt`.

## S1 — Delegation provider

**Question:** Can we register a provider, pass per-task `lane` / `allowed` / `gate`, and route to another model?

**Evidence:** Probe provider registered via `agent.delegation_registry.register_provider`; `get_active_provider` selected it when `delegation.backend` matched. `task_parameters` exposed `lane|allowed|gate`. Missing `allowed`/`gate` raised `DelegationRouteError`. Executor route returned `{provider, model, label}`; self route returned `{label: "self"}` (empty inheritance of parent route).

**Route to another model:** Yes — public route vocabulary includes `provider`+`model`, and `profile`. The catalog `cursor-acp` plugin is a **model provider**, not a delegation backend; select it as `provider` (if installed) the same way as any other slug. Tests do not require Cursor.

**`requires_hermes` floor:** `>=0.22.5` — confirmed by official `advanced-delegation` catalog entry ("first release with pluggable delegation providers").

## S2 — `pre_verify` (and `subagent_stop` fallback)

**Contract:** Hook kwargs include `session_id`, `platform`, `model`, `coding`, `attempt`, `final_response`, `changed_paths`. Return `{"action": "continue", "message": "..."}` (first non-empty wins) to keep the turn going; other returns let the turn finish. Bounded by `agent.max_verify_nudges`.

**When it fires:** At the edited-code verify gate in `conversation_loop`, only when the **current** agent edited code this turn and at least one `pre_verify` hook is registered. It does **not** automatically fire solely because a subagent edited files.

**Fallback:** `subagent_stop` is observer-only (return ignored). Use it to notice child completion; gate enforcement stays on parent `pre_verify` + `post_tool_call` gate capture.

## S3 — One-shot cron via `dispatch_tool` / blueprints

**`ctx.dispatch_tool(tool_name, args) -> str`:** works for `cronjob_manage` with `action=create`, `schedule` as ISO timestamp or `in Nm` (both parse to `kind: once`), `prompt`, optional `repeat`.

**Automation blueprint:** `ctx.register_automation_blueprint(key, title=..., description=..., schedule_template=..., prompt_template=...)` is public API — register `resume-after-reset`.

**Choice for park:** create exactly one one-shot job at `reset_at + buffer` via `dispatch_tool("cronjob_manage", ...)` (stubbed in tests); also register the blueprint for manual `/blueprint` use.
