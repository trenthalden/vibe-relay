---
name: memory-engineering
description: Use when engineering agent memory or retrieval.
version: 1.0.0
author: trenthalden
---

# Memory Engineering

## Overview

Engineer an agent's memory store, retrieval pipeline and answer policy safely, and select context through a bounded loop instead of flooding or starving the model. Covers: live memory injection, evals for memory, durable vs time-sensitive stores, and the iterative retrieval loop (also usable to prepare context for subagents).

Do not impersonate the person the memory describes; this is an engineering skill.

## When to Use

- Changing memory ingest/compile/retrieval scripts or packet rendering.
- Validating an owner-approved gold set or scoring retrieval.
- Running extractive or real-LLM answer evals.
- Wiring live memory injection into the normal reply path.
- Building a profile memory retrieval layer for a new agent.
- Selecting context for subagents or investigating an unfamiliar code area.

## Architecture rule: inject ephemerally, in the existing reply path

Retrieved, owner-approved memory is injected **ephemerally** into the existing reply path. Do not build a parallel sender bot, review-send queue or second delivery pipeline.

Live flow:

1. The gateway receives the owner message through the normal profile path.
2. A pre-turn hook takes the latest user message before the agent runs.
3. The hook calls the retrieval command (JSON output) with a short timeout.
4. Results are filtered: only rows with an `evidence_ref` enter the packet; refs are guarded against hallucinated provenance.
5. Mode `on`: a compact memory block is added to the ephemeral system prompt for this turn only. Mode `dry_run`: retrieve and log, but do not change the prompt (`injected=false`). Mode `off`: hook disabled.
6. Empty, slow or failed retrieval falls back to the normal answer with no memory packet.

Hard gates:

- Restrict injection to the verified owner channel and verified owner identity.
- Logs carry metadata only: message id, hash, refs, coarse token count, latency, mode, injected/not-injected. No raw message text, no raw packet.

## Non-negotiables

1. The repo is the source of truth; read the architecture doc before changing memory behavior.
2. Never edit live secrets, `.env`, auth stores, persona files or live memory directly.
3. Production-impacting code: repo edit → tests → deploy → deployed smoke → commit.
4. Architecture/behavior change → update the architecture doc in the same commit.
5. Keep PII out of the retrieval corpus and persona files: store patterns and owner-approved assertions, not direct identifiers.
6. Owner-approved gold refs are the safety/eval contract.
7. Persona/memory compaction stays **compile-only** until the owner approves a mutation phase: do not change entry status, supersede rows, or treat "same evidence ref" as a dedup criterion. Identify gold anchors defensively (by source type *or* ref prefix) and never hide them behind non-gold.

## Standard workflow

1. Inspect current architecture and scripts if scope is unclear.
2. If the owner asks for a plan first, write it (files, tests, deploy gate, risks) and do not mutate code until they approve.
3. Strict TDD for new behavior: focused failing test → verify expected failure → minimal code → targeted tests → relevant suite.
4. Deterministic harnesses before live LLM calls: retrieval eval → cluster-critical eval → extractive answer eval → only then real-LLM smoke/stratified eval.
5. Make eval outputs compatible with the gold-set scorer.
6. Record real command output, interpretation and the next gate in the single source-of-truth doc; do not create doc sprawl.
7. Deploy only after tests pass, then verify the deployed script path under the live profile.
8. If a previous turn stopped mid-change, resume from repo state (`git status`/diff), read the touched code, finish the smallest remaining loop, and do not report closure until the deployed path ran.
9. Commit with a concise `feat:`/`fix:`/`docs:`/`refactor:` message (when authorized).

## Memory-injection checklist

1. Keep injection ephemeral: never mutate baseline persona/user files or the cached system prompt inside the reply path.
2. Explicit feature-flag semantics: `off`, `dry_run`, `on`.
3. Strict owner gate.
4. Safe fallback: timeout, error or empty results never block the normal answer.
5. Guard refs: every injected fact has an allowed `evidence_ref`; unsupported refs are excluded.
6. Log safely (metadata only).
7. Test all three modes and at least one fallback path.
8. After deploy, smoke the *deployed* profile path.

## Real-LLM eval pattern

- Isolate from persona contamination (ignore persona/rules for the eval call).
- Restrict to a safe toolset so the subprocess cannot browse or call tools.
- Tag the source for traceability; require strict JSON.
- Require `evidence_ref` to be one of the packet refs, or `null` on abstain; score hallucinated refs explicitly.
- Start with a tiny smoke subset before a stratified run.
- Keep `latency_ms` aligned with the scorer's semantics (e.g. packet-build latency); report generation time in a separate field.

## Interpreting gates honestly

- A smoke subset can pass while the full scorer returns `ship: false` due to coverage. Say so.
- Cluster-critical safety is not exact recall.
- Extractive pass shows extraction is possible when the right anchor is present; it does not validate free-form LLM behavior.
- A real-LLM smoke pass shows the call path works on selected cases; it is not permission to change production behavior.
- Before changing live memory behavior, require a stratified slice with red lines, negative/abstain cases and broad prompt clusters.
- Broad prompts: exact ref scoring can be too strict; a different active approved ref in the same query/dimension cluster can be valid. Keep exact metrics for diagnosis, use an explicit cluster policy for broad behavior.
- Distinguish coverage failure from answer-quality failure; never present partial coverage as readiness.
- If a new risk gate emerges after the plan is complete, label it as follow-up rather than silently extending the roadmap.

## Store separation and time-sensitive context

- **Durable memory** (owner-approved, evidence-backed) and **time-sensitive workspace context** (news-like, with `as_of`, source URLs, TTL, confidence, lifecycle status, counter-signals) are separate stores. The latter must never mutate durable memory or persona files.
- Long-lived knowledge comes only from a trend/promotion layer: `items → observations → candidates → capsules + evidence + reviews`. Never promote a single headline directly; keep original URLs for deep research.
- A baseline source may produce compact baseline observations for comparison, not mirrored page content or broad durable facts.
- Monitoring candidates (companies, entities) proposed by deterministic matching need owner approval before becoming active watchlist entries; candidate inference must be conservative (regression-test macro noise such as "raises interest rates" so countries/central banks are not proposed as companies).
- Use boundary-aware keyword matching in topic classification; naive substring matching routes "software" to "war".
- Treat time-sensitive context as context, not investment advice or permanent memory.
- Software-level "mixture of experts": retrieval dimensions/stores act as experts and a deterministic router sparsely activates relevant ones per turn with budget and load-balancing metrics. Do not propose training or steering a model's internal MoE.

## Compaction of persona/profile memory

When persona or profile memory grows too large, do not hand-prune live files. Improve the evolve/compile procedure in the repo so duplicate insights compress, the prompt layer stays lean, and important durable memory is promoted into the structured store with evidence/provenance. First version: candidate output, compaction report, fixture tests, doc update, deploy, commit.

## Cron jobs that feed memory or context stores

- Compare the script's logical call cap with the scheduler's outer timeout. Add an internal wall-clock budget, stop before launching a batch if remaining budget is too small, and bound each subprocess timeout by remaining budget.
- Treat model spend as a hard budget: tested default call caps, CLI upper bounds, stderr telemetry (`prompt_chars`, coarse estimated tokens). Prefer deterministic extraction/classification before LLM synthesis.
- For long backfills the owner monitors, emit a compact progress block on productive runs (bar, done/total, percent); diagnostics go to stderr.
- Relative `script` paths resolve inside the job's own profile; after a platform update, compare cron state across profiles and remove misplaced duplicates rather than copying scripts.
- No-agent cron does not inherit the profile `.env`; load only the required variable when the process env lacks it, never printing it.
- When a digest misses an item, debug the pre-LLM pipeline first (stored row? disabled source feed? topic/entity/event/materiality assignment? present in the extractive render?) before the final summarizer. If a protected bullet is dropped by the LLM, add a narrow post-LLM preservation guard plus a fake-LLM regression test. If a source has no feed but a reachable HTML index, add a targeted parser with a fixture.

---

# Iterative retrieval

Avoid two failures: sending too much context (wasted tokens) and too little (hallucinated or generic answers). Use a bounded **retrieve → evaluate → refine** loop, at most **3 cycles** unless the owner approves deeper research.

## Four-phase loop

1. **Dispatch** — start broad but bounded.
   - Memory: query terms + dimensions + owner-approved refs only.
   - Time-sensitive context: country/topic/time/source-tier filters.
   - Code: file patterns + keywords + excludes.
   - Subagent: goal + 3–8 likely files/refs, not the whole repo.
2. **Evaluate** — score before loading more: `high` (answers the query), `medium` (related), `low` (tangential), `reject` (stale, duplicate, off-domain, unsafe). For each high/medium item record `path/ref/url`, relevance, why, and what is still missing.
3. **Refine** — add exact names from high-relevance hits, synonyms used by the source, exclude confirmed-irrelevant paths/sources, narrow by dimension/time/tier/status.
4. **Loop or stop** — stop when enough high-relevance evidence exists, remaining gaps need owner input, the next cycle would exceed budget, or results are low-confidence (label them so).

## Retrieval rules by store

- **Durable memory:** evidence refs are mandatory for injected facts; owner-approved gold anchors outrank inferred/compiled content; never use time-sensitive headlines as persona facts; prefer compact capsules over raw long entries.
- **Time-sensitive context:** treat as perishable; prefer trend capsules over single headlines; keep `as_of`, source URLs, TTL, confidence and counter-signals.
- **Subagents** do not know the conversation. Pass the task goal, relevant files/refs, constraints, expected output format and explicit stop conditions. Never pass raw secrets, direct PII or whole DB dumps.

## Retrieval report

```text
Retrieval cycles: 1|2|3
High relevance: <count>   Medium: <count>   Rejected: <count>
Selected context:
- <ref/path> — <why>
Open gaps:
- <gap>
Budget risk: low|medium|high
```

## Profile-memory retrieval dry-run (new agents)

Before any live prompt injection or gateway hook for a new profile:

- implement `off | dry_run | on`, with `on` blocked until a later live gate;
- in `dry_run`, build the packet but force `injected=false`;
- log metadata only (hashes, coarse lengths, refs, counts, error/fallback, injected flag);
- never log raw user query text, raw memory statements or full packet bodies;
- missing DB or retrieval failures fall back to the normal no-memory reply path;
- test stdout/log privacy explicitly before committing.

## Verification checklist

- [ ] Context was selected through an explicit loop, not guessed.
- [ ] Rejected/off-domain items tracked; evidence refs/URLs preserved.
- [ ] Token/context budget bounded; remaining uncertainty labeled.
- [ ] Injection stays ephemeral, owner-gated, with safe fallback; all three modes tested.
- [ ] Gold/negative/abstain cases run; coverage vs quality reported separately.
- [ ] Deployed path smoke-tested; docs updated; live injection for new profiles disabled until a separate gate.
