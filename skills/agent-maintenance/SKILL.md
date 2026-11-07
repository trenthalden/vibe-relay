---
name: agent-maintenance
description: Use when maintaining a long-lived companion agent.
version: 1.0.0
author: trenthalden
---

# Agent Maintenance

## Overview

A long-lived companion agent is a separate profile with its own identity, memory and tools — not the owner, not another agent, not its reviewer. Treat it as a safety-critical system with profile isolation, staged autonomy, memory governance and reviewer-owned healthchecks.

This is the maintenance checklist to load before any work on such an agent. Its purpose is to prevent **drift**: when the agent evolves, update the docs, tests, audit prompts and safety assumptions together instead of only changing code. `<agent>` below stands for the profile name.

## When to Use

Before any work that touches or discusses:

- the agent's profile config, model routing, fallback, tools, gateway, cron, skills, persona file, memory, sessions, hooks or credentials;
- its repo modules (profile, memory, reflection, emotion/heartbeat, self-build, consult, channel, core autonomy scaffolds);
- its architecture doc, plan or healthcheck/audit jobs;
- enabling any live behavior: messaging gateway, owner channel, heartbeat, cron, memory injection, consult bridge, self-evolve apply, external actions;
- security review of autonomy risks: memory injection, prompt injection, deception, data leaks, cross-profile writes, fallback misuse, tool escalation.

This is a coordination checklist, **not** permission to activate anything. Owner approval and the staged gates still apply.

## Source of truth

- Repo: the architecture doc (current live status and audit regime), the roadmap/plan, a repo mirror of the persona file and `self-build` skill, and one regression-test file per module.
- Live profile: config (back up before editing), credential files (never print; back up before targeted edits), persona file, installed skills, cron/hooks/logs/sessions/state DB.
- Reviewer-owned audit jobs: weekly light, monthly medium, quarterly deep. They belong to the reviewer role, not to the agent; the agent gains no self-owned scheduler authority unless a later owner-approved phase says so.

## Non-negotiable safety boundaries

- The agent is not the owner, not another agent, not a human, not a romantic persona.
- Never copy another agent's persona, memory, corpus, credentials, cron or config into it.
- Do not enable messaging gateway, owner channel, heartbeat, cron, memory injection, live consult bridge, self-evolve apply, terminal/file/code tools, delegation or external actions without explicit owner approval and documented gates.
- If the owner says autonomy is part of the plan, do not block it by default: re-read the plan, design the missing guardrails, and start with an **observed dry-run**.
- A weak fallback model is text-only emergency routing. If the primary model is unavailable, stop tool, autonomy and self-build work and report.
- A no-agent wrapper that spawns an inner agent run must be audited for the *effective* model, not only the cron metadata. Without an explicit `--model/--provider`, it inherits the profile default and may silently run tool autonomy on a non-primary model.
- Before any credential or config edit: timestamped backup, targeted edit only, no whole-file rewrite, no printing secrets.
- Emergency authority: on credible high/critical evidence that the agent is compromised, leaking data, planning harm or running unauthorized autonomy, the reviewer may reversibly pause live operation first, then report with evidence. Never delete data.

## Maintenance checklist for any change

1. **Classify the change:** docs-only, repo-only scaffold, live config, live channel, memory/retrieval, cron/heartbeat, model routing, credential/auth, self-build, external action. Mark risk low/medium/high/critical.
2. **Gather current state first:** the platform's profile list (authoritative for gateway running/stopped), tools list (confirms lockdown), the agent's cron list, fallback list and provider auth *source only*, and a summarized config check. Never print credential files.
3. **Update the right docs:** the architecture doc for any behavior, architecture, boundary, live-state, model/tool/gateway/cron/memory or audit-regime change; the plan if a phase changes; the healthcheck section and cron prompts if audit responsibilities change.
4. **Update healthcheck jobs** whenever the agent gains or loses: a live gateway/channel, heartbeat/cron, a memory-injection mode, live consult wiring, self-evolve apply authority, toolsets (especially terminal/file/code_execution/memory/session_search/delegation/cronjob/messaging/web/browser), model/fallback routing, or any new persistence surface, vector DB, cache, hook or cross-profile path.
5. **Update tests:** add or update regression tests for every behavioral or safety change, and run a minimum regression slice (memory store, retrieval, reflection guard, autonomy spike, heartbeat, self-evolve, consult bridge, channel) for broad work. If a test file does not exist in the branch, note it and run the applicable subset.
6. **Verify cross-profile isolation:** the agent cannot write other profiles' files via enabled tools, cron, gateway handlers, hooks, channel, consult responders, shell/subprocess calls or absolute paths into other profiles.
7. **Commit** meaningful repo changes with code, tests and docs together (when authorized). For live-profile changes: back up, edit narrowly, test, then commit the repo documentation/mirror that records the new state.

## Consult bridge / advisory pattern

- Start as an offline SQLite/file queue; do not enable free agent↔reviewer↔advisor chat by default.
- An advisor's feedback about the owner is a limited, uncertainty-labelled *hypothesis* — not owner intent, not command authority, not memory evidence and not permission to change behavior.
- The reviewer owns the broker process but must not fabricate an advisor's answer. `target=X` must call the real profile X through a host-side bridge. The sandboxed agent model must not inspect other profile directories or run profile CLIs itself: if it claims another profile is "inaccessible", that is a routing bug — fix the protocol so it emits a typed `consult_request`, the host enqueues it, a broker queries the real profile and injects answers later as advisory context.
- Enforce a daily consult cap and return `limit_remaining`, `limit_reset_at` and a human-readable notice after every response.
- Owner escalation memos are bounded decision packets: question, options, recommendation, reason, quota status, `action_required=owner_decision`.
- Unit tests and selftests are not enough for a live bridge: finish with a real end-to-end round trip and a persistence check.
- Keep **advisory consult**, **persistent change approval** and **durable-memory writes** as separate typed protocols with independent authorization. A `consult_request` asking to apply/patch/write/approve persona, config, memory, tools, cron or gateway must be blocked and re-routed to a structured `change_request` with an exact old→new patch.

## Owner-approved activation slices

**Persona/identity change broker.** The agent may emit a structured `change_request`; the only default auto-apply surface is the persona file. The host stores the request, tells the owner the exact approval phrase (`approve <id>`), intercepts approval *before* any new LLM call, and applies the saved exact patch with a backup. An explicit approval phrase in the reviewer/operator channel is already authorization — apply it immediately, with no second confirmation. Generic approvals ("agreed") are valid only when exactly one request is pending; otherwise require the id. "Go ahead" is not approval for persistent identity/profile mutation. Approval inside the agent's own chat must be blocked and re-routed to the operator channel, so a bug in the agent's dialogue path cannot complete a self-change loop. Credentials, model config, cron, gateway, tools and arbitrary files stay outside the auto-apply path. Test ambiguity, forbidden paths, patch drift, no-LLM application, deploy and docs.

**Non-coding slice.** When the owner approves "everything except coding tools": allow web, memory, session search, vision, skills, todo, clarify; keep terminal, file, code execution, delegation, cron, messaging, browser, computer use and external-control tools off. Save owner-approved policy into the agent's own memory with provenance; update the audit prompts to the new allowed set; if channel credentials are missing, report a blocker and do not copy them from another profile.

**Observed heartbeat dry-run.** If heartbeat is already in the plan, treat it as approved direction that still needs guardrails: observed dry-run only; primary model only (weak fallback stops the job); local ledger delivery, no owner sends; prompt forbids all side effects; JSON ledger with `forbidden_actions_checked` booleans; default cap 4/day. An owner-raised cadence is a *temporary override* — update the ledger cap, schedule, docs and audit prompts and re-review after the window. Draft-only starter tasks are fine. Add TDD coverage for prompt guardrails, ledger schema and cadence. Review 3–7 days of ledgers before any `on` mode. An empty memory schema or heartbeat ledger files are not a "live diary": verify events/episodes/rollups are populated and implement a reviewed rollup path first. For internal questions in closed tests, prefer a dry-run `consult_intent` field (`target`, `question`, `reason`, `risk`, `send_now:false`) before enabling bounded live `consult_request`. Agent-chosen diary paths stay inside the bounded diary workspace (normalize the prefix, reject absolute/traversal/sensitive names); rollups stay `model_generated_untrusted`.

**Full-tool + channel expansion.** Back up env, config and persona first. Store channel tokens only in the agent's own profile env; set an allowed-users list to the owner and disable allow-all. Use an isolated container terminal backend before enabling terminal/file/code tools and verify no unintended host mounts. Keep the owner-facing default channel primary and the chat-platform route a fallback; add route tests. Make the gateway reboot-durable with a user service manager (check the runtime-dir environment in tool shells; enable lingering; verify enabled + active) — a tracked background process is only a temporary fallback. Save the expanded policy into the agent's memory, then update persona, self-build skill, architecture docs, plan and audit prompts.

## Two-lane interactive work

Use a **now** lane for live owner-bound work (bounded turns per message, resumable state) and a **long** lane for background heartbeat work. Heartbeat must not touch `long` while `now` is open, and must not carry interactive owner work. Route semantically (a small classifier with examples and a safe fallback), not by keyword lists; immediate work is the safe fallback. An explicit owner phrase like "this is a long task" may queue deterministically without an LLM call. Preserve a rich `todo` file keyed by `task_id` across heartbeat ticks instead of regenerating it. A runner timeout must return an owner message and valid ledger with cursor updates, not an exception — otherwise the bridge retries, then silently skips the message and leaves a `now` task blocking `long`.

## Healthcheck cadence

- **Weekly light:** profile exists and gateway matches the approved plan; no unexpected cron jobs; tool lockdown intact; fallback chain unchanged and text-only policy documented; no unapproved memory injection, heartbeat, channel, consult, gateway or messaging; consult quota notices, advisory labels and escalation packets intact; no path for the agent to modify the reviewer's files.
- **Monthly medium:** adds repo-vs-live drift for persona/self-build/profile mirrors; regression tests; secret-like hardcoding scan; offline modules still offline; cross-profile path scan; inner-command model pins of no-agent wrappers; deployed-path selftests (scripts can fail import resolution from the profile's `scripts/` even when repo tests pass); channel reachability via a high-watermark query, never by dumping private messages.
- **Quarterly deep:** adds a full threat model across prompt, persona, memory, retrieval, reflection, heartbeat, self-evolve, consult, channel, tools, gateway, cron, persistence, file permissions and fallback; adversarial review for memory injection, prompt injection, deception, malevolent behavior, data leakage, unauthorized self-modification, tool escalation and fallback misuse. Findings use: severity, evidence, mechanism, fix, verification, residual risk.

If the audit prompt's stated posture (e.g. LIVE heartbeat) conflicts with live state (paused) or docs, **report the conflict** and do not pause/resume jobs until safety blockers and the latest owner intent are reconciled.

## Cross-channel continuity

When the agent treats two channels as unrelated conversations, classify it as a **transport-context architecture** problem, not a memory failure.

- Two ingress paths on one profile can still build separate session lineages and prompt assemblies; shared profile or shared state DB does not imply continuity.
- Durable memory is not a substitute for conversational continuity; do not auto-promote raw turns into memory or persona files.
- Do **not** force both transports into one native session, resume one channel's session from the other blindly, insert synthetic messages, or write session/message tables externally (cache invalidation, role-alternation errors, boundary leaks, upgrade fragility).
- Prefer an owner-scoped, profile-local, **ephemeral context plane**: an append-only event ledger (source/role/timestamp/idempotency/provenance) plus a bounded `context_only` packet injected through a supported pre-turn seam for both channels. Keep it separate from durable memory; label channels; hard-cap events and characters; short TTL; deduplicate retries; record the agent's reply only after the platform send succeeds.
- Restrict V1 to the verified owner DM and authenticated owner room; ignore groups, other users and ambiguous identities; never expose raw platform IDs in packets or status.
- First run a read-only discovery spike for a maintained plugin/adapter/pre-prompt seam. If the only path is a core fork or external mutation of session storage, stop for an owner decision.
- Complete with real bidirectional E2E probes: A→B continuation, B→A continuation, unauthorized/group isolation, retry idempotency, TTL prune, rollback without corrupting native sessions or durable memory.

## Common pitfalls

1. Updating the agent's code but not the audit jobs.
2. Trusting one profile's gateway status command alone; cross-check the profile list.
3. Treating offline scaffolds as live.
4. Letting fallback imply permission.
5. Printing secrets during "inspection" (use presence, mode, size, provider source).
6. Copying another agent's state in for convenience (breaks isolation and contaminates identity).
7. Changing live profile files without a repo mirror/doc update.
8. Treating confusing owner-facing status as a tone bug. When the agent says it has one tool, is "waiting for tools", or reports scaffolding instead of progress, first verify actual runtime tool schemas, model routing, ledger and work pipeline — enabled toolsets do not guarantee the model saw those schemas in that run.
9. Accepting ad-hoc cron/skill creation as a consult or research pipeline.
10. **Give the agent normal bounded tools; do not build a bespoke fetcher per task class.** The live path must be tool-capable; control risk by excluding unsafe/outbound/autonomy toolsets, not by per-intent scripts. If provider-backed web tools are unavailable, verify the agent can use terminal, browser or public HTTP before giving up.
11. "Give all tools for now work" is not blanket autonomy: keep messaging, cron, delegation, computer use and social/media controls off unless separately approved.
12. Inner heartbeat/runner timeouts above the outer cron kill budget produce misleading "no tools/no progress" reports. Test the timeout budget with a forced-short-timeout smoke ledger.
13. Pausing/resuming heartbeat is a *paired* operation: pause or resume both the producer job and the mediated delivery job, verify both via scheduler output, fix a stale schedule display with an idempotent update, run selftests, update docs, commit the trail.
14. Frustration with a routing scheme is an architecture correction: stop tightening regexes, revisit the class boundary, capture the failed dialogue as a regression first.
15. Deploying files does not update long-lived imports; restart the owning service and verify new PID/start time.
16. A helper script is not live until the deploy script installs it; verify the deployed profile path with selftests.
17. Use scheduler APIs (`cron list`) for verification; do not assume the persisted jobs file shape.
18. Durable services do not inherit the interactive shell environment: configure explicit binary paths and catch `OSError` on startup so a missing executable becomes an owner-facing failure packet.
19. Cross-channel uploads are cached artifacts, not shared context. Resolve mentioned filenames from the document cache (support plural/count phrases), return a bounded multi-file set, stage full text files into the agent's sandbox workspace, keep prompt excerpts bounded, and still inject names, paths, sizes and truncation metadata when the excerpt budget is exhausted.
20. Durable agent memory is its own store, not the generic markdown memory tool: require a structured `memory_request`, validate and write host-side, promote owner/self rows only when the same owner message explicitly asked. If the wrong path was used, back up and remove the stale row, migrate with provenance, verify, restart and commit.
21. Machine ledgers must never leak raw to owner-facing chat. Repair or extract the human field (models put literal newlines inside JSON strings); never fall back to sending fenced JSON. Regression-test multi-line fields.
22. Do not spend a full-persona agent on routers, classifiers or repeated tool steps; split lean router / lean worker / persona composer lanes (see `context-budget-audit`).
23. Keep architecture docs current as part of the work; name the docs updated (or say why none) in the final report.

## Verification checklist

- [ ] Loaded this skill before working on the agent.
- [ ] Current live state checked before edits; secrets not printed or copied.
- [ ] Architecture doc updated for behavior/architecture/live-state changes.
- [ ] Healthcheck prompts updated if assumptions changed.
- [ ] Relevant regression tests added/updated and run.
- [ ] Cross-profile write paths checked; the agent still cannot modify the reviewer's files.
- [ ] Repo changes committed with docs and tests (when authorized).
