---
name: agent-field-acceptance
description: Use when proving agent work with field-first acceptance.
version: 1.0.0
author: trenthalden
---

# Agent Field Acceptance

Field-first acceptance for agent runtime, tool, memory, delivery or scheduled-work changes in a project that follows a documented development framework (project instructions, a selected testing profile, a known-errors registry, a living checklist). The procedure is portable: it contains no personal profile, machine paths or live data.

## When to Use

Use for agent runtime, tool, memory, delivery or scheduled-work changes. Do not use it to turn an ordinary conversation into a project.

## Procedure

1. **Orient.** Read the project's instructions, selected profile, relevant known errors and the current living checklist. Check the actual branch and preserve unrelated work. Resolve paths from the active profile and real deployment, never from a historical example.
2. **Name one observable functional outcome** and the existing entrypoint that can demonstrate it. Reuse platform features before adding infrastructure. State permitted data, tools, destinations, spending and stop conditions.
3. **Work in outcome-sized iterations.** At an outcome boundary, after two failed correction/review rounds, or after about 30 minutes without useful functional progress, spend at most five minutes checking architecture *and* test complexity. Record keep / cut / defer, the simplest next functional run and remaining safety constraints in the existing checklist. No extra reviewer, document or gate.
4. **Safety smoke, then the real agent.** Run a short safety smoke, then let the **real agent** exercise its authorized environment and tools through its actual executor. Independently verify tool effects, artifacts and memory. Stubs prove plumbing, not agent competence.
5. **Fix concretely.** Diagnose a concrete failed functional task, make the smallest correction and repeat the same task. Keep a minimal regression for an observed deterministic defect where useful. Do not add speculative queue/timing/failure matrices as prerequisites for a harmless disposable field experiment.
6. **Document and gate.** Update only the project documents whose facts changed, and the existing handoff. Keep *source checks*, *field results*, *owner acceptance* and *deployment* separate. Run affected checks while changing and the required full gate once at completion.

## Boundaries and pitfalls

- Preserve privacy, authority, data integrity, resource bounds and Stop. A critical safety failure blocks its affected operation; field-first is not a bypass.
- Dangerous fault-injection tests use isolated fictitious data, never production credentials, databases or personal history. Do not fabricate outputs to advance a scenario when a dependency or login is unavailable.
- A model's claim is not evidence: independently read the exact artifact or effect. A persisted fact is not proof that a restarted agent recalled and used it correctly.
- A permission blocker is not an invitation to build another synthetic platform. Ask only for genuinely missing human input or authority.
- Do not rewrite working code for architectural purity. Remove a layer only after tracing its consumers and identifying a sufficient existing replacement.
- Commit, push, installation, account login, schedules and service restarts each need their own authorization. An explicitly paused assignment stays paused.

## Verification

Record the actual agent task, the environment, the chosen tools, the checked effects, the observed failure and the repeat outcome. Keep unproven properties explicit. Green test totals, health metadata and worker receipts alone are not functional acceptance.

Related: `lean-executor-orchestration` (delegation), `test-driven-development` (evidence selection), `systematic-debugging` (root cause).
