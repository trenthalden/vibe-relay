---
name: agent-build-debug-loop
description: Use when building or debugging long-lived agents.
version: 1.0.0
author: trenthalden
---

# Agent Build / Debug Loop

## Overview

Default operating loop for repeated construction and debugging of a long-lived agent system (gateway, memory, retrieval, cron, tools, skills). It exists to stop you from rediscovering the same failure classes: stale live services, prompt-only fixes for missing mechanisms, untested async queues, cron/model fallback leaks, and undocumented architecture drift.

Pair it with `agent-architecture-audit` (layered diagnosis), `agent-harness-design` (script/tool/cron contracts), `systematic-debugging` (root cause) and `test-driven-development` (evidence selection).

## When to Use

Before any task that:

- builds or changes agent behavior, memory, retrieval, gateway, cron, tools, skills or persona/profile evolution;
- debugs why a live agent answered wrongly, skipped tools, used the wrong memory layer, failed to deliver, repeated work, claimed a capability it lacks, or timed out silently;
- adds a helper script, deterministic broker, LLM runner, no-agent cron job, queue, DB table or deploy path;
- verifies a live agent after code changes.

Do not use it as a substitute for the narrower skills above; load those too.

## Preflight

1. **Load the relevant skills** (audit, harness design, debugging, TDD).
2. **Read the known-errors registry** (`known-errors`) before coding, editing or deploying. If the current failure is a new class, append a dated entry after the fix.
3. **Ground in the source of truth.** The repo and its architecture doc are authoritative; live profile state is never the only record. Mirror and document behavior changes in the repo.
4. **Check model discipline.** If the approved high-capability model is unavailable, stop production-impacting engineering and report. A weak fallback may do text-only synthesis, not file/config/deploy/cron/security work.

## Diagnostic loop

1. **Reproduce or bound the symptom.** Capture the exact user-visible text, entrypoint, profile, model/provider and time window. For screenshots, read the suspicious claims and map each to a layer.
2. **Map the failure to layers.** Prompt/instructions → tool schema exposed to the model → host-side runner/protocol/parser → DB/file persistence → async producer/consumer → gateway/rendering/delivery → deployed long-lived process → cron/model/fallback budget.
3. **Prefer mechanism over blame.** Do not say "the model was confused" until you know what affordances and context it saw. Did we expose the wrong tool, omit the right structured field, or inject stale facts?
4. **Check the deployed runtime, not just the repo.** Compare repo code with deployed copies. Restart long-lived services whose modules are imported at process start. Verify PID/start time or a live smoke path.

## Implementation loop

1. **Small safe slice.** Fix the narrowest host-side invariant that prevents the class of bug. Prefer deterministic validators/brokers over prompt-only warnings for safety-critical behavior.
2. **Evidence in the same slice.** Add a regression that fails for the observed class; cover happy path and safety path (active vs draft memory, accepted vs blocked request, timeout vs success).
3. **Docs in the same change.** Any change to behavior, architecture, live state, tools, memory, cron or gateway updates the matching architecture doc.
4. **Deploy and verify.** Targeted tests, then selftests/smokes against the *deployed* scripts, then restart/verify live services.
5. **Commit** only after test + deploy + verification, with a clear message, and only when the owner has authorized it.

## Skill maintenance rule

After every complex build/debug task:

- An existing skill covered it but missed a pitfall → patch that skill now.
- A reusable class no skill covers → add a concise skill or a reference under the best existing one.
- A concrete recurring failure mode → also append to the known-errors registry.
- Do not store temporary task progress or commit IDs as memory. Skills hold procedures; the registry holds failure classes.

## Common pitfalls

1. **Prompt-only fixes for missing mechanisms.** If the right host-side route/tool/field does not exist, wording will eventually fail.
2. **Two memory systems exposed at once.** A generic markdown memory tool and a custom DB path need explicit, separate host-side write/retrieval affordances.
3. **Async queue without a consumer.** Enqueueing is not delivery. Verify producer → broker → consumer → injected context or user-visible result.
4. **Deploy without restart.** Long-lived bridges keep imported objects until restarted.
5. **Tests only in the repo.** For live behavior, also verify the deployed path and service state.
6. **Over-building per-intent scripts.** Prefer a bounded general tool runner unless the task is recurring, deterministic and worth a reviewed helper.
7. **Ignoring docs/healthchecks.** Drift makes later audits flag the wrong things.
8. **Fixing a wrong-profile cron by copying scripts.** After a platform or profile update, compare cron state across profiles first; remove a misplaced duplicate rather than copying scripts into the wrong profile.
9. **Assuming no-agent cron inherited the profile `.env`.** Test with an empty process environment and load only the one required secret from the active profile when it is missing. Never print it.
10. **Inventing model economics from names.** Fetch live model metadata and real usage stats; if quota/pricing endpoints are unavailable, say so.
11. **Prompt-only protection against compaction.** If an event class must never be dropped, classify it into a deterministic protected lane and keep a post-LLM restoration guard. Gate admission on provenance (approved first-party source + specific entity + explicit material action). Test a deliberately omitting fake LLM, a genuine event, and adversarial near-matches (tutorials, near-match URLs, third-party reports).
12. **Undercounting a cron outage.** For per-day pipelines, enumerate every absent calendar date from the first success to yesterday and replay each in order; do not infer continuity from `MAX(success_date)`. If recovery code lands after a current-day success, run one controlled replay immediately or the new success hides the old gap.
13. **Async reviews need a barrier.** A background reviewer is a release gate only after it completes. Freeze the reviewed tree; any later patch stales that verdict. A superseded reviewer still running is a possible veto source: before deploy/commit, require every in-flight review in the lineage to be completed or cancelled plus a pass on the final frozen tree. Never mark release todos complete while any review is pending. If a late finding lands after rollout, retract artifacts, disclose the premature closure, add RED regressions, apply the smallest fix and repeat the cycle.
14. **Green tests after regression loss are false confidence.** Never restore an overwritten test file with `git checkout -- <file>` unless the wanted regressions are committed, stashed or copied. After any recovery, compare test *names* against the prior gate, not just the pass count.
15. **Publisher metadata is not event metadata.** A country attached at collection time identifies the publisher, not where the event occurred. Keep provenance separate from event attributes, and do not let a delivery layer rewrite facts.
16. **Raw-exporting a live profile for migration.** A live profile can hold identity, credentials, memories, sessions, cron and runtime DBs. Build an allowlisted bundle, create new targets explicitly through the platform CLI, validate symlinked ancestors, persist per-action `pending/applying/applied` state, back up before publish, and roll back only destinations whose hash proves the action was applied.
17. **Normal fix/commit loop during a frozen review.** If the owner froze the diff, do not stage, stash, reset, patch, deploy or commit. Use `frozen-working-tree-review`.
18. **Trusting transaction labels without proving physical state.** Treat manifest status and per-action state as tamperable input; prove actual bytes (absent / pre-install / installed / diverged) before claiming a terminal state, and validate recovery data before every success/no-op return.
19. **Scanning cryptographic digests as payload prose.** A hex digest can contain a Luhn-valid digit run and trip a card detector. Validate digest syntax separately; run PII detectors on payload content only.

## Verification checklist

- [ ] Relevant skills and the known-errors registry loaded.
- [ ] Symptom mapped to concrete layers/mechanisms.
- [ ] Regression added for the class of bug.
- [ ] Architecture docs updated if behavior changed.
- [ ] Production code deployed; long-lived services restarted and verified.
- [ ] Review barrier closed: every dispatched review completed or cancelled, final frozen tree passed.
- [ ] If review scope was frozen: nothing was staged, patched, deployed or committed.
- [ ] Commit made only with owner authorization.
- [ ] Skill or registry patched if a new reusable lesson emerged.
