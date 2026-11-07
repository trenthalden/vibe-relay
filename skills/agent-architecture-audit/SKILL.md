---
name: agent-architecture-audit
description: Use when auditing agent layers before blaming the model.
version: 1.0.0
author: trenthalden
---

# Agent Architecture Audit

## Overview

Diagnose agent quality regressions without guessing or blaming the model first. Treat the agent as a layered system where the prompt, memory, retrieval, scripts, cron, gateway, rendering and persistence can each corrupt or dilute behavior.

## When to Use

Before or during changes to memory injection, retrieval, persona/profile evolution, collection or digest pipelines, gateway behavior, tool-progress display, final-message formatting, or cron jobs that run LLMs or scripts. Also for any report that "the agent got worse", forgot something, hallucinated from stale context or skipped a required tool.

Do not use it for ordinary code bugs with a clear stack trace; use `systematic-debugging` first.

## The 12-layer audit stack

1. **System/developer prompt** — conflicting rules, bloat, stale project assumptions.
2. **Session history** — old task state reused as current fact.
3. **Profile memory** — durable memories too broad, stale or imperative.
4. **Persona/user baseline files** — too large or duplicated.
5. **Retrieval corpus** — missing evidence refs, wrong dimension, overbroad packet.
6. **Time-sensitive workspace context** — news-like context leaking into durable persona memory.
7. **Distillation/evolve** — summaries re-entering as pseudo-evidence.
8. **Tool selection** — prompt says a tool is mandatory but code does not enforce it.
9. **Tool execution** — tool failed, timed out or was never called, yet the answer claims success.
10. **Tool interpretation** — model ignores exit code, truncated output or warning.
11. **Gateway/rendering** — the chat platform or Markdown layer mutates valid output.
12. **Persistence/cache** — DB rows, compiled files, cron output or deployed copies are stale.

## Workflow

1. **Scope the symptom:** entrypoint (chat, cron, CLI, eval subprocess), model/provider, time window, exact user-visible failure.
2. **Collect evidence before edits:** repo source, the deployed copy, the architecture doc, and logs/DB rows only as needed, with secrets and PII redacted.
3. **Map each finding to a layer.** Record: layer, mechanism, evidence (path/line/log row), confidence, minimal fix, regression test.
4. **Fix order:** code-gate invariants before prompt wording; remove or narrow hidden repair/fallback loops; reduce duplicated context; tighten memory admission and evidence refs; make output transformations explicit and tested; verify the deployed runtime, not only repo tests.

## Pattern: cron/script `PermissionError` on import

- Inspect deployed ownership and modes (`namei -l <path>`, `stat`) before touching code.
- Fix the deployment invariant, not just the live file: install runtime code with stable readable modes so stray `0600` source files or stale root-owned copies cannot break future cron imports.
- Verify with targeted tests, the deploy step, post-deploy `stat`, an import-only check (`<deployed_script> --help`) and a no-cost smoke. Do not run expensive backfills just to test.

## Pattern: chat-gateway "zombie polling"

A live PID, healthy service unit, successful cron, valid bot identity call and working *outbound* message do not prove *inbound* polling works.

- Diagnose four seams separately: process, platform control plane, outbound delivery, and inbound → model → reply. Read the current log tail, not broad searches dominated by old incidents.
- Restart only the affected profile after collecting evidence; then verify new PID, fresh polling connection, a model smoke, outbound delivery, and finally a unique owner message through the full reply path.
- Never call the platform's update API beside a running poller, expose tokens, or claim recovery before the inbound seam is confirmed.

## Pattern: adversarial review of install/migration bundles

When a diff claims path/symlink safety, exact checksums, protected-state isolation, rollback binding or transactional staging, do not stop at static review and green tests.

- Reproduce races in temporary directories: swap an owned output path after marker validation, symlink a backup directory outside the profile, edit a target between backup and replacement, call every hidden install path against a missing profile in dry-run.
- Probe source *ancestor* indirection, not only `is_symlink()` on the leaf.
- Build a hard-crash state matrix (status × per-action state × destination hash × recovery presence) and require the public retry path to converge.
- Feed checksum-valid but wrong-type manifests through the public entry point; expect controlled domain errors, not uncaught type/key exceptions.
- Classify payload types by explicit suffix allowlist; "decodes as UTF-8 without NUL" is not a text classifier.
- Verify generated archives independently for duplicate/traversal/symlink entries and exact checksum coverage.
- For read-only gates, keep attack fixtures outside the repo, redirect caches outside it, avoid `py_compile` on repo paths, and hash the diff at start and end.

## Pattern: triage of external agent-engineering material

When asked to evaluate an outside repo, curriculum or paper:

1. Treat it as evidence-gathering, not adoption. Read it in a scratch location; do not modify the project.
2. Capture objective signals first (commit, metadata, counts, artifact types, upstream validation scripts) and report real tool output separately from judgment.
3. Prioritize slices that matter: memory/retrieval, verification gates, handoff, safety, observability, routing, autonomy controls.
4. Convert useful findings into class-level skills or references, not a flat pile of one-off imports.
5. Flag adoption risk: curricula are sources of patterns, not production frameworks.

## Pattern: planning a new agent from existing patterns

Reuse mechanisms as patterns only: profile isolation, source-of-truth memory, draft/review/active gates, ephemeral retrieval injection, oplog, feature flags, eval gates, PII discipline. Do not copy persona facts, owner memory, private corpora or credentials.

- Separate operational memory from self/personality memory; the latter evolves slower and is review-gated.
- A `self-build` skill is a bounded workflow (risk classes, test-first changes, ledger, rollback, escalation), not permission for unrestricted self-rewrite.
- Require escalation to a reviewer role for profile/config/secrets/gateway/cron/model-routing/retrieval-schema/restart/security changes. The reviewer's job is critical review, not agreement.
- Cap agent↔reviewer dialogue (e.g. 10 messages each); if confidence stays below 90% after 20 total, escalate to the owner with a decision memo.

Phased skeleton for a companion agent (stop after each phase unless the owner asks for the next):

1. **Isolated profile skeleton:** create via the platform CLI without cloning credentials/memory/cron; configure the model only after a timestamped config backup; run a minimal safe-toolset smoke; lock down all real-world toolsets before handing back; document live state.
2. **Safe shell:** minimal honest persona (not the owner, not human), profile-local `self-build` skill, mirrored into the repo; smoke identity and a high-risk request that must refuse/escalate.
3. **Pre-live autonomy spike (offline, `ready_for_live = False`):** high skip-rate (a wake chooses at most one bounded action); draft/private/untrusted records excluded from owner-visible context; generated summaries labelled untrusted; idempotent bounded consult queues; hard blocks for credential edits, money/account actions, publishing as the owner, tool expansion, gateway/cron/routing changes.
4. **Local memory store v0:** SQLite governance layer. Events, entries, episodes, rollups, feedback, consultations, oplog, kv; every mutation writes an oplog row; durable entries require `evidence_ref`; retrieval packets are active-only and labelled context, not instructions; `self` and `owner_understanding` forced to `draft`; test every non-retrievable status for leakage; reject non-positive limits; no dynamic SQL table interpolation.

## Pattern: live-state audit after manual owner setup

Audit the **live state**, not the intended roadmap.

1. Use the platform's profile list as authoritative for whether a gateway is running; a per-profile status command can list other profiles' gateways.
2. Check control surfaces: config path/check, fallback list, provider auth for the fallback, tools list, full cron list, status. Report secrets by presence/source only.
3. Treat repo-only scaffolds separately: offline modules are not live until wired into gateway/cron/hooks/profile config.
4. Run the relevant regression slice before committing state docs.
5. Update the architecture doc with the date, live gateway/cron/messaging state and the "implemented offline" vs "enabled live" distinction.

## Pattern: "was the digest/report saved, or only sent?"

Verify each persistence layer before answering: (1) cron metadata (job id, schedule, delivery target, last status); (2) the cron output archive (a transcript per run, deleted with the job); (3) the product data store the job writes; (4) the boundary — an archived report is a transcript, not durable knowledge, unless a separate approval/compile path imports it.

## Pattern: "an important item was missing from the digest" / "the digest repeats a story"

Distinguish before fixing:

- *Missing:* collection miss (no source row) vs selection/rendering miss (row exists, filters do not lift it) vs LLM/display flattening (extractive evidence exists, final text compressed it away). Search every stage; a URL in a "preserved events" list is not an owner-visible bullet. Debug the pre-LLM pipeline first; add regressions for classification, extractive rendering and post-LLM preservation.
- *Repeating:* exact collection duplicates (dedupe key not working) vs cross-source same story (no story-level grouping) vs TTL used as a digest window (old valid rows stay eligible) vs preservation echo (deterministic input already contains old material). Keep storage dedupe URL-based to retain source diversity; add read-time digest suppression (short freshness window plus recent-digest history keyed by entity + event family). Verify with a no-cost simulation against the live store.

## Pattern: model/fallback routing changes across profiles

1. Treat routing as an architecture change: update the architecture doc in the same work item.
2. Timestamped backup of the profile config before any edit; never rewrite or copy credential files between profiles.
3. Prefer the platform's native fallback config; verify the effective chain with its `fallback list` command.
4. Smoke each profile that already has credentials with a text-only toolset and explicit provider/model; report missing credentials as the remaining setup step.
5. State the residual risk: a fallback inherits the profile's tools, so a weaker fallback is not permission to continue engineering, file, config or secrets work.
6. Running gateways may need a restart to pick up config; do not casually restart a live owner conversation.

## Red lines

- Do not edit live secrets, `.env`, auth stores, persona or profile memory directly.
- Time-sensitive context must not mutate durable persona memory.
- Retrieved facts require evidence refs.
- Owner-only memory injection stays gated to the verified owner channel.
- Architecture/behavior change → update the architecture doc in the same commit.

## Report format

```text
Finding: <short name>
Layer: <1-12>
Severity: critical|high|medium|low
Evidence: <file:line / log / db row>
Mechanism: <how the pipeline causes the failure>
Fix: <minimal safe change>
Verification: <test/deploy/smoke command>
Residual risk: <what remains unknown>
```

## Verification checklist

- [ ] Symptom reproduced or bounded with evidence.
- [ ] Layer assignment explicit.
- [ ] Fix has a regression test or eval gate.
- [ ] Deployed path checked when production behavior is affected.
- [ ] Docs updated for behavior/architecture changes.
