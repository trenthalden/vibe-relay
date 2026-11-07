---
name: product-aware-engineering-releases
description: Use when product decisions require safe release splitting.
version: 1.0.0
author: trenthalden
---

# Product-Aware Engineering Releases

## Purpose

Translate a technically complex change into a product-owner decision, and split a large release into smaller independently useful product boundaries when independent review shows the diff is too coupled. Includes the rules for **owner-facing engineering communication** (Part 2).

This complements a generic pre-commit reviewer loop: that defines the review; this skill governs owner communication, scope decomposition, exact staged-release construction and honest stop conditions.

## When to Use

- A non-technical owner must choose between engineering approaches, or says the alternatives are too technical.
- A monolithic diff repeatedly fails independent review.
- Released artifacts must exclude experimental, licensed, secret, raw-provider or unrelated data.
- The working tree holds preserved work that must not enter the current commit.
- A smaller slice needs a fresh frozen review boundary.
- You are reporting multi-step engineering, debugging, deployment, migration or configuration work to a non-technical owner.

---

# Part 1 — Release decisions and splitting

## Product-owner decision format

Never present a choice as internal APIs, cache schemas, libraries or refactor tactics. For each option explain:

1. **Deliverable:** what usable result the owner gets.
2. **Excluded/deferred scope:** what they do not get yet.
3. **Trade-offs:** completeness, safety, time, cost, rollback.
4. **Operational impact:** cron/live delivery, credentials, licensing, owner action.
5. **Recommendation:** choose one and say why in product terms.

Prefer one recommended default. Do not make the owner infer consequences from implementation vocabulary.

## Release-boundary split

When review rejects a coupled change:

1. Identify the smallest independently useful owner-visible artifact.
2. Confirm the split with the owner if it changes sequence, scope, delivery or cost.
3. Remove code/data dependencies between slices, not only visible fields.
4. Preserve deferred work locally, but rebuild the Git index from an explicit allowlist.
5. Assert the exact staged paths and export the index to a private temporary tree.
6. Run the full suite and artifact gates against that export, not the mixed working tree.
7. Update architecture docs to describe only released behavior.
8. Freeze a staged hash and run the deterministic release gate against that exact tree.
9. Use an independent LLM verdict only for acceptance criteria that cannot be encoded reliably (e.g. nuanced semantic fidelity) — not as the default gate for parsers, schemas, filesystem boundaries, concurrency, privacy or deployment manifests.
10. Any patch stales the deterministic verdict and requires a new hash and gate run.

A smaller boundary is a legitimate new review slice only when the prior combined candidate is **abandoned** as a release candidate. Do not relabel the same coupled diff to reset review-cycle limits.

## Deterministic-first gate design

For codeable behavior, prefer one local harness plus a versioned catalog of good/bad examples over repeated LLM review:

- declare the finite acceptance matrix before implementation;
- keep fixtures free of credentials, PII, live provider data and irreversible production state;
- encode each concrete defect as a RED regression or catalog case before fixing it;
- include operational probes on temporary state: concurrency, replay/idempotency, permissions, schema integrity, symlink/path safety, CLI failure boundaries, irreversible-ID behavior;
- include the full tests, static parsing, staged-path allowlist, secret/runtime-state scan and exact staged SHA verification;
- emit one compact machine-readable verdict: `passed`, counts, checks, sanitized failures;
- record `llm_calls: 0` and `network_calls: 0` when those are gate invariants;
- deploy only after `passed=true`, then verify deployed hashes and runtime wiring, then commit.

Do not add or install external reviewers merely because a reviewer's quota, credential or sandbox is blocked. A generic "continue" means continue along the agreed gate, not invent new reviewer infrastructure.

## Artifact minimization

- Build output from an allowlist schema; do not copy broad source rows and delete fields afterwards.
- Assert forbidden fields and terms are absent.
- Keep credentials and raw provider payloads outside Git.
- Add artifact tests for schema/version, exact counts, renderer equivalence, forbidden data and provenance boundaries.
- Confirm the staged slice has no imports or runtime dependencies on excluded untracked files.
- Treat deployment manifests as dependency graphs: reproduce every target layout that receives a changed executable, including secondary copies, and run a harmless `--help`.
- Before freezing a cron/profile release, compare touched canonical specs with bounded live summaries (model/provider, schedule, skills, delivery); correct policy drift without copying secrets or runtime state into Git.
- For production smokes involving irreversible IDs, counters, watermarks or deliveries, run deployed code against private temporary state; never burn durable production identifiers with fixtures.

## Review-cycle discipline

- Start with a finite, executable acceptance matrix and threat boundary. For codeable guarantees, build the gate before requesting any LLM review; for durable ledgers also load `frozen-working-tree-review` so filesystem, parser, schema, payload and concurrency probes are not discovered one review at a time.
- Request an LLM reviewer only when the remaining question is materially semantic. State it narrowly and cap the review/fix cycles.
- A new *reproducible* bypass may expand the deterministic matrix; hypothetical hardening outside the declared threat model is non-blocking unless the owner changes scope.
- Convert every reproducible finding into a RED regression before fixing; fix only that class and its immediate variants.
- Classify reviewer outcomes explicitly: `passed`, `product-failed`, or `verification-blocked`. A denied command, timeout, unavailable sandbox, quota/billing failure, missing after-hash or incomplete probe is **verification-blocked**: neither approval nor evidence of a defect. Keep the tree frozen; do not spend a defect-fix cycle without a reproducible code issue.
- For `verification-blocked`, allow at most one simpler retry on the already-approved path. If that blocks too, stop the release and escalate. Do not chain through ad-hoc CLI installation, credential discovery, provider switching or weaker fallback models to manufacture `passed=true`; new reviewer infrastructure needs explicit owner approval.
- A blocked or running reviewer is bound to the old fingerprint. If you change the tree meanwhile, declare that review superseded, rerun all gates and issue a fresh fingerprint; a late pass on the old tree cannot release the new one.
- On the final allowed cycle, state the stop condition before dispatch: pass and release, or fail and escalate without further patching.
- If the owner says "stop", stop immediately: cancel active reviewers where supported, perform no further reads, retries, cleanup, installs, status checks, deploys, commits or cron operations, and return one concise frozen-state summary.
- Never deploy, commit or call an artifact final while the deterministic gate has failed or a genuinely required semantic review is pending, product-failed or verification-blocked.

---

# Part 2 — Owner-facing engineering communication

Keep a non-technical owner informed without duplicating every tool action or inflating the conversation.

## Core contract

1. Treat one investigation or implementation as a **single communication chain**.
2. During the chain, send an interim message only for: a decision the owner must make; an action the owner must perform; a material blocker, safety stop or changed risk; or a concise milestone whose absence would leave a long silent gap.
3. Do not narrate routine reads, searches, tests, patches, deploy steps or repeated verification when tool progress already shows them.
4. **Silence is the default between tool calls.** A normal chain with no owner action and no blocker sends zero interim prose, however many internal steps it has.
5. Do not say the same thing in technical language and then again in simplified language in every update.
6. Put a short **plain-language summary block** only in the *final* response of the chain, never in interim messages (not even the opening "I am investigating" message). If one was sent mid-work, do not send another until the real final answer.

## Interim update shape

At most 1–3 short paragraphs: what was established; what changed in the plan, if anything; what the owner must do now, only if action is required. For interactive authorization, give only the exact URL/code mapping and the response expected next. Avoid a premature final summary while background flows still run.

## Final response shape

Lead with the outcome, then only material evidence: root cause or decision; what changed; verification result; remaining risk or limitation; owner action, if any. Finish with one short plain-language block of 2–4 sentences; do not restate the whole technical report there.

## Product-owner translation

Prefer consequences over implementation detail: **result** (what works or remains unavailable), **risk** (what can still fail), **cost** (extra model calls, infrastructure, owner effort), **scope** (follows the agreed plan, or adds a new component). Identifiers, paths, hashes and commands appear only when needed for audit or owner action.

## Question type, not pasted evidence

If the owner asks *why* something happened (a skill rewrite, a missed lookup, a stray footer), answer that engineering cause. Do **not** re-research or re-deliver the content they pasted as evidence; a code or snippet in the paste is an exhibit, not a request for another briefing. Treat it as research only when they ask to expand, verify or re-run it. If both appear in one message, lead with the why.

## Communication pitfalls

- A summary block after every tool call or milestone.
- Announcing an intent and immediately sending another message with the same intent.
- Calling a task complete while OAuth, background processes, deploy verification or a user action is pending. For a multi-gate plan, lead with "this stage is complete, the whole piece is not ready"; say whether the behavior is live and name the remaining acceptance gates. A green schema/corpus harness proves its own contract, not semantic quality, delivery or production readiness.
- Ending at an intermediate gate as though whole-plan approval had expired. Continue approved work without another start request; if an async result or scheduled shadow window is the actual boundary, name it.
- Repeating unchanged health/status facts across updates.
- Expanding a simple correction into a process explanation.
- Hiding a new component or workaround behind jargon instead of saying it was not in the prior plan.

## Before sending

- Is this truly the final message in the chain? If not, omit the summary block.
- Does the owner need this interim update now?
- Is any sentence duplicated in different wording?
- Are result, risk, scope and required owner action clear?

## Verification checklist

- [ ] Owner choices were explained as deliverables and trade-offs, with one explicit recommendation.
- [ ] Deferred work is excluded from the staged path allowlist; released code has no dependency on it.
- [ ] Full tests ran against an exported staged tree or exact staged SHA.
- [ ] Artifact schema and forbidden-data gates passed; docs describe only released behavior.
- [ ] Codeable acceptance criteria passed one deterministic machine-readable gate.
- [ ] Any required LLM review was narrowly semantic; cycle limit and final stop condition honored.
- [ ] Owner updates ended with current usable result, plan-vs-new-component, and what (if anything) the owner must decide.
