---
name: frozen-working-tree-review
description: Use when reviewing a frozen working tree read-only.
version: 1.0.0
author: trenthalden
---

# Frozen Working Tree Review

## Purpose

Perform an independent, fail-closed code or release review without changing the reviewed repository or adjacent runtime state. This differs from ordinary pre-commit verification: **no auto-fix, baseline stash, deployment, staging or commit** is permitted.

## Trigger

The user says the tree is frozen, requests a read-only review, supplies a precise diff plus untracked-file scope, or requires an independent machine-readable verdict.

## Workflow

### 1. Freeze the contract

- Extract prohibited actions, required inputs, intended behavior, known gates, focus areas and the exact output schema.
- Explicit read-only constraints override mutable steps in broader review skills.

### 2. Inventory all review inputs

- Read `git status --short`, `git diff --no-ext-diff --name-only` and the requested diff.
- Read every explicitly named untracked file separately; untracked content is absent from `git diff`.
- Split large diffs by file without creating temporary files in the repository.

### 3. Preserve snapshot integrity

- Record status and hashes of the tracked diff plus scoped untracked files **before dispatching** any reviewer; put the fingerprints in the brief and verify them at the end.
- Use reproducible fingerprints (run exact commands, never guess):
  - tracked: `git diff | sha256sum`
  - untracked: `for f in $(git ls-files --others --exclude-standard | sort); do sha256sum "$f"; done | sha256sum` — this hashes the *sorted per-file sha256sum lines*; omitting the sort yields a different value and falsely reports drift.
- If the brief supplies one **combined** fingerprint it must also supply the exact byte-level algorithm (path markers, NUL separators, `--binary`, ordering, raw bytes vs per-file hashes). A proven pattern: `SHA256(git diff --binary || for each sorted untracked path: NUL + "UNTRACKED" + NUL + path + NUL + raw file bytes)`. Do not brute-force plausible encodings and accidentally approve a different contract.
- Recompute immediately after recovering the canonical command and again before the verdict. First mismatch → fail closed. Two reviewer-side recomputations that differ → report active drift as a snapshot-integrity blocker and stop substantive approval.
- Treat contradictory read evidence (changed line counts, content beyond a file's earlier end, status/inventory mismatch) as a drift alarm: recheck fingerprints before further inspection. Inspecting two snapshots cannot be combined into an approval of either.
- Recheck fingerprints immediately before and after every test command. A green suite is evidence only for the exact pre-test snapshot; if the post-test fingerprint differs, invalidate the result and stop rather than rerunning against a moving target.
- When the contract fingerprints the **staged diff** (`git diff --cached --binary`), remember ordinary tests run working-tree files, not index blobs. Either require no unstaged delta for relevant paths (`git diff --quiet -- <paths>`) or export the index: `tmp=$(mktemp -d); git checkout-index --all --prefix="$tmp/"` and run the exact command from `$tmp`. Clean up only that reviewer-created directory, then recheck the staged hash and status.
- Track staged-target integrity and whole-tree freeze integrity separately. Concurrent unstaged edits do not change a staged-diff hash but violate a claimed frozen tree. If the contract is scoped to staged content, a pass on the exported index may approve that target while reporting unstaged files as unreviewed; if the contract requires a clean/frozen tree, the divergence is a blocker. Report both facts.
- Structure the test wrapper so the post-test fingerprint always runs: capture the test exit code, recompute and compare the fingerprint, then return the captured code (do not rely on `set -e`). If collection fails only because the repo is not importable, retry with the repo root on `PYTHONPATH` while keeping the same no-bytecode/no-cache controls.
- Recompute the canonical fingerprint a final time **after all reads/probes and immediately before constructing the verdict**, paired with `git status --short`, taken sequentially in one command. If it differs, report before/after digests and the newly visible scope, invalidate earlier test evidence, and fail closed.
- Once dispatched, do not patch "one last thing". A changed snapshot invalidates that reviewer as an approval.
- Keep one definitive final reviewer in flight. A superseded review must finish or be cancelled before the next final gate is dispatched or accepted; older reproducible findings remain veto inputs.
- If the snapshot changes, fail closed. Do not reset, stash, clean or delete unexpected artifacts.
- In verdicts after drift, report snapshot integrity as the blocker. Findings reproduced only on an earlier fingerprint are stale/intermediate observations, not defects of the current tree.

### 4. Keep verification actually read-only

- Prefer static reads and non-writing commands.
- Never run `py_compile` against repository paths (it creates `__pycache__`).
- Python tests: `PYTHONDONTWRITEBYTECODE=1`, `-p no:cacheprovider`, and all other caches/temp output outside the repo. If write-free execution cannot be guaranteed, rely on supplied gate evidence and say so in the verdict.
- Never deploy or read secret values to strengthen a review.

### 5. Review behavior, not only syntax

- Trace each intended pipeline stage to an enforceable condition. Ranking is not admission: sorting by reliability is not a reliability gate.
- A claimed global/comparative budget must be one bounded selection, not independent per-section quotas.
- Test path-resolution precedence as a matrix: canonical override, legacy override, profile-scoped home, unset environment, shadow-stage compatibility.
- For profile splits, follow scripts, skills, archives, cron sources and default paths transitively. A profile-local default is not a boundary if CLI arguments or environment overrides can point source/archive paths into another profile.
- Trace scheduler dependencies through activation: helpers that need a newly assigned job ID, output directory or environment binding must have an executable post-create binding step. A valid cron record that silently no-ops is a blocker.
- Verify empty-output semantics through the scheduler mode, not only stdout. Some schedulers gate the agent on a trailing JSON line (e.g. `{"wakeAgent": false}`) that skips both the LLM run and delivery; confirm the mechanism exists in the *installed* scheduler source before trusting or flagging it.
- Compare docs, config/spec field names, counts, flags and activation order literally. For final post-cutover reviews, do a **document-wide temporal-consistency sweep**: search for stale lifecycle terms (`staged`, `pre-cutover`, `until activation`, `default-off`, old "last edited" dates, future-tense completion claims) and check each current-state claim against live artifacts using bounded, non-secret summaries. Stale unchanged text is a blocking logic error when docs are required to be truthful.
- Treat deployment manifests as dependency graphs. For every changed executable copied to more than one destination, resolve imports and companions per destination, reproduce the smallest deployed layout outside the repo and run a harmless entry path (`--help`).
- Compare instruction contracts to required output structure; a rule must be implementable for every output shape.
- Distinguish byte identity from semantic identity for deployed text artifacts (a lone trailing newline). Require exact hashes for code/release artifacts; do not invent a regression from EOF normalization unless byte identity is the stated contract.
- Probe negative guards adversarially: changed counts in one event, partial/disjoint metadata, systemic exceptions inside normally excluded content, and natural-language variants from every enabled source language.
- For every lexical editorial predicate require a matched negative, a matched positive using the same trigger, and an unmatched negative with alternate wording or separators. A punctuation-plus-conjunction regex cannot prove two clauses are unrelated.
- Model metadata freshness as three states: absent, explicitly empty, non-empty but stale/contradictory. Recomputing only absent/empty metadata does not satisfy a stale-safety claim.
- Bind jurisdiction to the named institution, not to any country token in the article. Do not equate court level or any numeric claimant count with systemic impact; probe one claimant, unrelated numbers and a high-court ruling with no class-wide consequence.
- Treat required renderer fields as total contracts: test eligible rows with missing and contradictory metadata. Conditional omission is a blocker unless a sentinel or fail-closed path is defined.
- "Source-free" / "no URL" is a syntax family, not an `http(s)://` substring check. Probe bare `www.`, protocol-relative `//host/path`, Markdown links with protocol-relative targets, `mailto:`, mixed-case schemes, encoded separators and split URLs — at both the pre-model and final-delivery gates.
- For durable ledgers, test existing-path security as well as first creation: pre-create the DB with broad permissions, as a symlink, under a symlinked parent, and with a malformed/partial schema. Repair or fail closed before writing; a chmod on creation only is not a private-state guarantee.
- Define replay semantics at item and package level: same key with changed title/URL/time; reordered, omitted and appended items. Require an explicit policy (immutable first write, exact-envelope rejection, documented extension).
- For resumable provider collectors, test stale cache replay: keep a position-named cache file while changing, reordering, inserting or deleting the current batch. Every skip and offline-apply path must compare the stored ordered request envelope to the current request. Bind the completion manifest to the full canonical request plan, not a count.
- For positional APIs, preserve request-to-response identity: validate response type and count, reject duplicate business keys before mapping, and probe two submitted identifiers that share one lookup key.
- For identity gates stated as "exact normalized X", probe absent/empty operands; normalizers that map two missing values to one sentinel fabricate matches. Product-family exclusions (fund/preferred/REIT) must inspect every classification and descriptive field that can carry the type.
- Treat provider errors as layered data: HTTP status *and* structured payload code. Probe contradictions (HTTP 404 carrying 401/429/500); only recognized entitlement/not-found cases may degrade to per-item gaps; auth, rate-limit and server failures hard-stop.
- Exercise CLI error paths as subprocesses: missing input, malformed JSON, invalid UTF-8, deeply nested JSON, cache mismatch, unknown fields, network exceptions. Expect non-zero exit, empty stdout, stable sanitized stderr, no traceback, no credential-bearing URL, no raw payload. `JSONDecodeError`/`OSError` handlers do not catch `UnicodeDecodeError` or `RecursionError`. Closed schemas reject unexpected keys before durable writes. Use synthetic canary strings and assert their absence.
- Market-data corroboration: same calendar date is not same trading session; probe pre-market, after-hours, weekends/holidays, adjacent market dates, timestamp units, crossed or one-sided books, currency/quote-unit mismatches and stale ranges.
- When dedupe lets long-summary similarity override entity/number drift, add a matched negative with different entities/amounts and identical boilerplate; require event-specific anchors.
- Test every systemic-content exception with mundane uses of its trigger phrase (`court rules`, `critical infrastructure`) so an obituary, sponsored item or probate dispute is not promoted.
- For message-ingestion cursors, acknowledgement must identify the exact run/batch that consumed the payload; a generic prior `last_status=ok` is stale-success-prone. Inspect the *installed* scheduler's field names, update timing and crash behavior rather than trusting fixtures built from an assumed schema.
- Verify ownership claims against authoritative identity metadata, not blank or missing sender fields; exercise the top-level caller so newly required owner filters cannot be omitted while unit tests stay green.

### 6. Security-scan the complete scope

- Scan added diff lines *and* untracked files for credentials, account/chat/user identifiers, phone numbers, private paths and cross-profile state access.
- Trace credentials through failure paths. If a provider needs a secret in a URL/query, raw exception text can leak the prepared URL; require sanitized messages and probe stderr with a synthetic canary.
- "Raw responses stay out of Git" is not proved by filenames. Recursively inventory generated JSON/report keys and credential-shaped values; regenerate human reports from staged JSON and compare bytes where renderers are deterministic.
- A test asserting a real identifier is absent still leaks it; use synthetic fixtures.
- Treat hardcoded cross-profile archive, cron-output, session, memory, auth or env paths as boundary findings even when read-only.
- Inspect every newly deployed tree transitively even if unchanged in Git: skills and references can carry persona facts, stale job IDs, foreign paths or instructions that bypass a new helper.
- Validate containment at every use boundary: source-entry symlinks, existing and dangling destination symlinks, manifest symlinks, symlinked ancestors of managed paths.
- When an environment variable identifies the active profile, exercise every canonical and legacy path override against a synthetic foreign profile. Selecting another profile's data is a boundary failure unless a privileged mode is explicit, unavailable to scheduled jobs and documented.

### 7. Fail-closed verdict

- A non-empty security or logic finding list forces failure when the schema says so.
- Return exactly the requested JSON/schema, with no markdown fences or surrounding prose.
- Suggestions stay non-blocking and must not hide blockers.

### 8. Keep runtime activation behind the final-tree gate

- Profile creation, secret staging, credential discovery, gateway install, cron creation and state migration are not harmless parallel work while the review/fix lineage is open.
- A newly created profile may hydrate credentials from shared CLI stores even if not cloned; verify persisted credential metadata and obtain independent auth when the architecture requires it.
- After a passing final review: hand secrets over through a confirmation barrier, smoke-test, then cut over without duplicate jobs.

## Profile-split review checklist

- No credentials, personal memory, sessions, persona files, corpora or private archives copied or read across profiles.
- New profile defaults cannot redirect legacy active jobs during a shadow period.
- Cron schedules parse under the real scheduler with explicit timezone assumptions.
- No-agent jobs are silent on no work and on expected missing sources; agent-with-script jobs skip the model when script output is empty.
- Archive helpers enforce profile ownership for defaults and every explicit override.
- Deploy preflights all sources/destinations before touching active assets and avoids partial mixed-version installs.
- Rollback covers every target mutated after the transaction starts, including legacy mirrors, shared scripts/hooks and cleanup deletions.
- Destination permissions do not broaden access relative to the profile root.

## Test-suite redundancy audits (read-only)

1. Inventory test *files* and test *cases* separately; `def test_*` counts are not pytest-item counts under parametrization.
2. A test is removable only if its executable contract is exactly covered elsewhere. Similar fixtures or adjacent layers (unit vs CLI vs end-to-end) are not enough.
3. Classify overlap: **exact duplicate**, **mergeable implementation** (parameterize while keeping every case), or **unique safety/incident contract**.
4. Preserve adversarial negatives (symlink containment, permission/schema hardening of existing state, rollback/retry, stale acknowledgement, idempotency, quiet no-op, redaction) unless an equivalent case demonstrably remains.
5. Report candidates with file/line, risk and confidence; say whether a reduction changes organization only or removes behavioral cases. Never claim a large safe reduction without coverage-equivalence evidence.
6. If tests may not be executed, rely on static evidence and say so.

## Common pitfalls

- A "clean" test command that silently writes bytecode or cache files.
- Scanning only `git diff` and missing sensitive untracked fixtures.
- Accepting tests that assert strings exist instead of exercising the real resolution or scheduler path.
- Calling separate category caps a comparative budget.
- Assuming English-only event cues work for a multilingual source registry.
- Treating documentation discrepancies as cosmetic when operators use those names for activation.
