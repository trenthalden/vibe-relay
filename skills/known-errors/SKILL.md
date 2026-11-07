---
name: known-errors
description: Use when reading or appending a known-errors registry.
version: 1.0.0
author: trenthalden
---

# Known Errors Registry

## Overview

A known-errors registry is a short, dated, append-only list of **failure classes** the team has already paid for, with the rule that avoids each one. It is read *before* coding and appended *after* a new class is fixed. It stores reusable patterns, not private operations.

The registry is a project file (for example `docs/KNOWN_ERRORS.md` or a shared skill of the same name). This skill is the how-to; the content belongs to your project.

## When to Use

- **Read** at the start of any coding, edit, config or deploy task, and before debugging a recurring-looking symptom.
- **Append** after fixing a failure that is a *class* (it could happen again somewhere else), including after a near-miss or a premature "done" that a later review overturned.
- **Do not append** one-off typos, task progress, commit IDs or temporary state. Skills hold procedures; the registry holds failure classes.

## Read-before-coding procedure

1. Open the registry and scan headings for the area you are touching (queues, cron, config, deploy, parsing, review gates, fallback routing…).
2. For each matching entry, write down the **rule** you must obey in this change.
3. If the symptom matches an entry's *Symptom* line, apply its fix first instead of re-deriving it.
4. After the task, state in your report which entries you consulted and whether you added a new one.

## Entry format

Newest entries first. Keep each entry to roughly 4–8 lines.

```markdown
## YYYY-MM-DD — <one-line class name, mechanism not blame>

**Symptom:** what was observed, in user-visible or log terms.
**Cause:** the mechanism (why the system allowed it).
**Rule:** the reusable invariant or procedure that prevents it.
**Test:** the regression or check that proves the rule (optional but preferred).
```

Writing rules:

- Date every entry. Name the *class*, not the incident.
- Preserve the pattern; strip private context: no people, hosts, IPs, account or chat IDs, internal job IDs, file paths from a private machine, or raw logs.
- Write the rule as something checkable ("bound retries to N and dead-letter the head item"), not an exhortation.
- If an entry is superseded, mark it `Superseded by <date>` rather than deleting it.
- Retired components: keep only their generalized lessons, never the commands to restore them.
- Keep the file small. Merge near-duplicates and move long case studies into a referenced doc.

## Seed classes (generic, worth having in any agent project)

1. **Poll/queue loop retries forever.** A failing head-of-queue item that does not advance the cursor is reprocessed every poll and blocks everything behind it. *Rule:* bound retries, then advance or dead-letter.
2. **One invocation is not one LLM request.** An agent loop with N turns multiplies requests. *Rule:* when diagnosing request/cost counts, multiply by turns; cap turns for single-output jobs or use a no-agent script.
3. **Multi-turn agent cron for a deterministic periodic job.** *Rule:* keep the outer job a no-agent script with cheap idle gates and deterministic persistence/delivery; run a bounded inner agent only for work that needs tools.
4. **Premium-model fallback leak.** A hard-pinned expensive model silently spills to a paid same-model route when auth fails. *Rule:* do not hard-pin premium models on jobs that may run on fallback; monitor spend by key × model.
5. **Idle agent cron burning a frontier model.** *Rule:* gate the LLM behind a deterministic "is there work?" check that exits before any model call.
6. **"Ask X" that never calls X.** A stored label (`responder="x"`) was mistaken for a real consult. *Rule:* verify the code literally invokes the target.
7. **Async ask/answer queue with no consumer.** "Stored" was mistaken for "delivered". *Rule:* every async queue needs an explicit consumer that surfaces answers back to the asker; verify the full round trip.
8. **Trusting an LLM to emit closed JSON.** A truncated object makes a strict parser drop the whole ledger silently. *Rule:* repair narrowly (balance brackets, strip trailing commas), take the first object and ignore trailing prose, and keep the raw text if repair fails. Never send raw machine JSON to a human channel as a fallback.
9. **Full YAML re-dump strips comments.** *Rule:* for one key, use a guarded idempotent text insert; always take a timestamped backup of config before editing.
10. **Inner timeout above the outer kill budget.** The scheduler kills the wrapper before it can write diagnostics. *Rule:* keep inner subprocess timeouts below the outer budget and test the timeout path.
11. **Service environments lack interactive PATH.** *Rule:* configure explicit executable paths and catch `OSError` on subprocess start.
12. **Copying files does not refresh long-lived processes.** *Rule:* restart only the exact owning service and verify PID/start time plus deployed hash.
13. **Deploy gate: shell must fail closed.** A multi-step verification without `set -euo pipefail` can end with exit 0 after an earlier error. *Rule:* start release shells with `set -euo pipefail` and print the success marker only after all assertions.
14. **Killing a wrapper leaves its children running.** *Rule:* run bounded commands in their own process group and clean the whole group on exit and timeout.
15. **Directory containment does not validate child symlinks.** *Rule:* validate every consumed source and destination leaf, not only the parent root; test read and write escapes.
16. **Stale success acknowledges a new batch.** *Rule:* bind each pending batch to the scheduler's baseline run identity; acknowledge only a successful run whose identity differs.
17. **Cron-ID dependencies are not a manifest row.** *Rule:* create the producer, resolve its real ID, persist and verify the binding, then create the consumer; roll back on failure.
18. **Recursive `chmod a-x` removes directory traversal.** *Rule:* change modes by entry type (directories traversable, files non-executable).
19. **Provenance-free ownership heuristics.** Missing sender/direction metadata is not proof a message belongs to the owner. *Rule:* verify against authoritative identity metadata.
20. **Language prompts do not guarantee delivery language.** *Rule:* add a deterministic final-output guard with bounded retry that fails closed; never delete offending characters as "repair".
21. **LLM review inflates a deterministic gate.** *Rule:* encode codeable acceptance as a deterministic harness; reserve LLM review for semantic questions only.
22. **Happy-path tests plus a late async review ship destructive changes.** *Rule:* close the review barrier (every dispatched review complete or cancelled, final frozen tree passed) before release.

## Appending checklist

- [ ] The failure is a reusable class, not a one-off.
- [ ] Entry is dated and uses Symptom / Cause / Rule format.
- [ ] No private names, hosts, IDs, secrets or machine paths.
- [ ] The Rule is checkable; a regression test or gate is referenced when one exists.
- [ ] Near-duplicate entries were merged instead of added.
