# Scheme B — architecture (gate included)

**Use case:** a Hermes profile that enables only `vibe-relay` still gets (1) DEV Framework tools and (2) the
lean-orchestration deterministic brief gate, without installing those plugins separately.

**Components**

| Piece | Location | Active when |
|---|---|---|
| DEV Framework | `vendor/dev_framework/` | always attempted; skips tool names already registered |
| lean-orchestration | `vendor/lean_orchestration/` | `ctx.has_plugin("lean-orchestration")` is false at vibe-relay `register()` |
| Pins | `vendor/*.SOURCE.json` | tests fail on missing / extra / changed files |
| Refresh | `scripts/vendor_refresh.py` | maintainer re-copy + pin regenerate |

**Hook interplay:** Hermes invokes every `pre_tool_call` hook. A `delegate_task` built from
`orch_dispatch(executor="hermes_child")` plus vibe-relay lane args (`lane`, `allowed`, `gate`) must pass both
vibe-relay's stop/park/executor-lane hook and lean-orchestration's envelope guard. A hand-written lane
`delegate_task` without the envelope is blocked with lean-orchestration's message. vibe-relay stop/park and
executor-lane behaviour is unchanged.

**Settings:** vendored LO reads `plugins.entries.vibe-relay.settings.lean_orchestration.*` via a thin
`get_config` proxy (CFG-1). Standalone LO keeps reading its own plugin id's settings.

**Load order:** without `requires_plugins` edges, Hermes loads plugins alphabetically by registry key, so
`lean-orchestration` loads before `vibe-relay` when both are enabled — the skip path is the common case.

The vendored skill is namespaced by the host plugin: `skill_view("vibe-relay:lean-orchestration")`
(the standalone plugin exposes `lean-orchestration:lean-orchestration`).

The vendored gate is executed fresh on every `register()`; a cached `lean_orchestration_vendored`
module from an earlier install is never reused, because a long-lived gateway keeps `sys.modules`
across plugin remove/install (live check 2026-11-20: the old object served the previous install's code).
