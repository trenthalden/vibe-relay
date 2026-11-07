---
name: agent-harness-design
description: Use when designing agent tools, cron, or eval harnesses.
version: 1.0.0
author: trenthalden
---

# Agent Harness Design

## Overview

Make scripts, tools, cron jobs, retrieval packets and evals easy for agents to use correctly and easy for humans to trust. Quality rests on four things: **action-space quality**, **observation quality**, **recovery quality** and **context-budget quality**. The second half of this skill covers **evals** — treat them as unit tests for agent behavior.

## When to Use

When adding or changing cron scripts, retrieval/CLI commands, no-agent watchdogs, tool-like helpers consumed by an agent, digest/rendering pipelines, structured outputs another agent or job reads, model/provider routing or backup LLM capacity, external coding-agent harnesses — and for changes to memory, retrieval, routing, gateway behavior or prompt packets that need capability/regression evals *before* implementation.

## Action-space rules

- Prefer narrow commands with explicit modes over catch-all scripts.
- High-risk operations get micro-actions: backup → dry-run → apply → verify.
- Medium actions are fine for common read/search/render loops.
- Macro-actions only when round-trip cost dominates and rollback is clear.
- Inputs are schema-first (flags, JSON, YAML, typed fields); avoid ambiguous prose for deterministic work.

## Observation contract

Every output an agent consumes should make these trivially extractable:

```json
{
  "status": "success|warning|error",
  "summary": "one-line human summary",
  "counts": {"processed": 0, "changed": 0},
  "artifacts": ["/path/or/id"],
  "next_actions": ["actionable follow-up"],
  "warnings": [],
  "error": null
}
```

For no-agent cron stdout delivered to a chat, keep a compact human block and put verbose diagnostics on stderr.

## Error-recovery contract

Every failure path states: a root-cause hint, whether retry is safe, an explicit stop condition, artifact paths for logs/backups, and no secrets or raw owner text.

Bad: `Failed.`
Good: `status=error summary="DB locked" retry_safe=true next="retry after current collect finishes" artifact=".../store.db"`

## Context-budget rules

- Stable rules live in skills, not in every prompt; large docs live in references loaded on demand.
- Return IDs/paths plus compact summaries rather than whole files.
- Cap retrieval-packet rows and include evidence refs instead of article bodies.
- Log token estimates for recurring LLM jobs (see `context-budget-audit`).

## Model/provider fallback harnesses

- Prefer direct provider API keys for unattended routing, with provider-side billing caps and local healthchecks.
- Treat external coding agents and IDE assistants as sandboxed reviewers/workers, not drop-in model providers.
- Avoid browser automation, consumer-account workarounds or unofficial endpoints for production fallback.
- Classify tasks before routing (`engineering-critical`, `research-digest`, `cheap-bulk`, `manual-review`) and define the allowed model tier per class.
- If the approved high-capability model is unavailable and the fallback is below the task's tier, stop and report instead of continuing autonomous production edits.
- Do not hard-pin a premium model on jobs that may run on fallback; otherwise an auth failure can silently spill to a paid same-model route. Monitor spend by **key × model**.

## Safety defaults

- Live DB repair needs a timestamped backup first.
- Config/secret-file changes need a timestamped backup and a targeted edit only.
- Keep durable-memory stores and time-sensitive context stores separate.
- Production-impacting script changes follow: repo edit → test → deploy → deployed smoke → commit.
- Self-evolve/report-only harnesses: apply paths blocked; absolute/traversal/control/sensitive paths denied; public JSON/stdout limited to hashes and counts, never raw requests, proposed content or blocked paths.
- New self-evolving agents use a phased dry-run → live boundary: offline first, owner-visible dry-run next, live only with an explicit gate.

## Design review checklist

- [ ] Is the action narrow enough?
- [ ] Are inputs typed and documented?
- [ ] Does output expose status, summary, artifacts, counts, warnings?
- [ ] Are errors recoverable and explicit?
- [ ] Is stdout safe for user-visible cron delivery?
- [ ] Are diagnostics on stderr?
- [ ] Is the token/context footprint bounded?
- [ ] Is there a regression test for the output contract?
- [ ] For apply-capable harnesses, are apply paths blocked or gated?

---

# Evals

Define success and regression criteria before changing behavior. Prefer deterministic graders first; use live LLM evals only after the cheap gates pass.

## Eval types

**Capability eval** — a behavior the agent could not previously do reliably.

```text
Capability: <name>
Input fixture/query: <text or DB rows>
Expected behavior:
- <observable result>
- <required evidence/ref/metadata>
- <fallback behavior if absent>
```

**Regression eval** — a behavior that must not break.

```text
Regression: <name>
Baseline: <commit or known behavior>
Must remain true:
- <existing test/eval>
- <owner-approved assertion>
- <security/PII invariant>
```

**Negative / abstain eval** — a case where the system must not overclaim. Examples: no evidence ref → no memory fact injected; a country headline → not a company candidate; a stale news item → not promoted as a durable trend; an unsupported broad question → abstain or answer without fake refs.

## Grader order

1. **Code-based grader** — pytest, DB assertion, JSON field, rendered section.
2. **Fixture-based scorer** — gold-set scorer, extractive eval, cluster scorer.
3. **Live LLM smoke** — tiny set, minimal toolset, strict JSON where possible.
4. **Stratified LLM eval** — only when changing production memory behavior.
5. **Human review** — required for owner policy changes or new durable memory categories.

## Gate template (write it into the test/eval file before implementing)

```text
Feature: <short name>
Capability gates:   - <new behavior>
Regression gates:   - <old behavior preserved>
Negative gates:     - <must not happen>
Operational gate:   - repo test command
                    - deploy command if production-impacting
                    - deployed smoke command
```

Typical commands: targeted `pytest tests/<file>.py::<test> -q`, full `pytest -q`, the project deploy script, and a live LLM eval run with rules ignored and tools restricted to a safe toolset so the eval subprocess cannot browse or call tools.

## Pass/fail discipline

- A smoke subset passing does not make a feature done.
- Separate *coverage* failure from *answer-quality* failure.
- A model-based grader never replaces deterministic invariants.
- For broad owner questions, exact evidence-ref match can be too strict; use a cluster-level policy only when explicitly defined.

## Eval report format

```text
Eval report: <feature>
Capability: X/Y passed
Regression: X/Y passed
Negative/abstain: X/Y passed
Commands run: <real commands>
Deploy checked: yes/no
Residual risk: <risk>
Decision: ship | hold | needs owner review
```

## Eval checklist

- [ ] At least one capability gate exists before implementation.
- [ ] At least one regression gate protects old behavior.
- [ ] A negative/abstain case covers overclaim risk.
- [ ] Production-impacting changes have a deployed smoke check.
- [ ] Results cite real command output, not intuition.
