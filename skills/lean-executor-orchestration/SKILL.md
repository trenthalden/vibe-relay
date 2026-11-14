---
name: lean-executor-orchestration
description: Use when delegating code. Dispatch once; accept receipt.
version: 2.0.0
author: trenthalden
---

# Lean Executor Orchestration

## When to Use

A bounded development task goes to a coding worker (an external coding CLI or a Hermes child via `delegate_task`), or a
narrowly triggered read-only review goes to a second model. For a whole unattended night of such tasks, add `night-shift`.

## Goal

The worker spends the implementation tokens. The orchestrator sends **one closed brief**, gets a **compact receipt**, and
decides: accept, one correction, or block. Do not build an orchestration platform for normal development.

## Roles

- **Worker:** the only writer of source, tests and task-scoped docs in its tree.
- **Reviewer model:** read-only plan or review, only for multi-component changes or changes to security, authority, memory,
  delivery, runtime or installation boundaries. Not a default second pass.
- **Orchestrator:** dispatches, checks the receipt and the *actual* delta, accepts / corrects once / blocks. Does not redo
  the implementation.
- **Owner:** resolves blocked work; separately authorizes commit, push, install, restart, deploy.

## Choosing the worker

- Ask the owner once per assignment if they have not named a worker; reuse the recorded choice for continuations.
- Long vertical portions go to the strongest worker; small, sharply bounded portions (tests-only fixes, mechanical edits, one
  small guard) suit cheaper or smaller models. A no-edit decline on a large refactor is a sizing signal: re-scope once, then
  move the portion, do not retry the same brief.
- Switching workers mid-task: stop the old writer at a safe point, verify it released the tree, keep its partial delta, and
  give the new worker one continuation brief. Never two writers at once. Switching does not reset the correction budget.

## Dispatch

1. Capture the working-tree state without changing it. Check that no other writer owns the tree (process list, live children).
2. One self-contained brief: `INTENT`, `DECISIONS`, `ALLOWED`, `FORBIDDEN`, `TASK`, `DONE MEANS`, one `GATE`, one-pass limit,
   receipt fields. Put rules shared by several briefs in one `COMMON.md`.
3. Launch with **only the executor command the repository approves**. Never infer from an example that approval-bypass flags
   (`--force`, `--yolo`, `--trust` and similar) are allowed; where the repository does not allow them, use the normal approval
   path or report the blocker.
4. External CLI workers: run as a tracked background process with a hard wall and its own session, so a host restart does not
   kill it; keep pid and output under the project's ignored temp dir; unset the host's `HERMES_HOME` and set an explicit
   `TMPDIR`, so a worker that runs `hermes` uses an isolated home. Confirm the process exists ~20 s after launch — an earlier
   exit is a launch failure, not a correction run.
5. Hermes children: use `delegate_task` and its lifecycle; do not wrap a child in a CLI launcher.

### Hermes + vibe-relay

With the `vibe-relay` delegation backend, do **not** edit failing code in the parent turn. Call `delegate_task` once with
`goal` (the brief), `allowed` (path globs), `gate` (one command that must pass on the real diff) and `lane` (`auto` unless the
owner forced otherwise). After the child returns, rerun the gate if required, then `vibe_accept`. Never widen `allowed` to
legalize an out-of-scope edit. At the stop threshold the plugin parks the work and can arm `resume-after-reset`.

### Brief template

```text
INTENT: <one sentence: why>
DECISIONS: <already made; do not reopen; pinned interfaces>
ALLOWED: <paths/globs owned by this worker>
FORBIDDEN: commit, push, install, service/config/runtime changes, paths outside ALLOWED
TASK: <2–5 concrete steps>
DONE MEANS: <observable outcome>
GATE: <exact commands, run from the worker's tree>
LIMIT: one pass; stop with the blocker if incomplete
RECEIPT: FILES; GATE commands + results; BOUNDARIES ok|exception; OPEN; RISKS
```

## Acceptance

Accept only when:

1. the receipt exists and its `FILES` match the actual changed paths, all inside `ALLOWED`;
2. the gate is the brief's gate and passes **when you run it** in the project's interpreter;
3. `DONE MEANS` follows from the delta, not from the receipt's wording;
4. the production path contains no fixture, fake, placeholder or empty-policy object that only makes tests green;
5. every external consumer of a changed message or schema still accepts it.

Accept a portion before dispatching the next brief that depends on it. Read the diff for relaxed shared rules (a filter,
guard, ordering, or a checklist item owned by someone else); one correction restores them with a narrow exemption and a
test. When an asynchronous review arrives, check it against the current code before acting — it may describe an older tree.
Details: `references/acceptance-checklist.md`.

For agent projects, prove behaviour with a short safety smoke and then one real authorized run of the actual agent; synthetic
replies prove plumbing only. Do not demand exhaustive timing or failure matrices before a harmless disposable functional run.

## Parallel lanes

Two or more writers only in separate detached worktrees with disjoint file ownership; the parent integrates. See
`references/parallel-worktree-lanes.md`.

## Long runs and quota

- Budget the whole run, not the next call: parent review is usually the bottleneck. Stop dispatching at the owner's reserve
  (default 90 % used) and keep enough to checkpoint.
- Arm one continuation at the start of a long window, just after the known quota reset — not after the quota is gone.
  Scheduled jobs run on the profile's default model; make sure that model has quota, or have the owner pin the job.
- Keep one canonical handoff (checklist with timeline and decisions); a fresh session must be able to continue from it alone.
- Overnight: use `night-shift`.

## Stop rules

- At most **two** worker runs per task: original plus one correction. Renaming a third pass does not reset the budget.
- At most **one** reviewer-model pass, only when its trigger applies.
- A forbidden effect, second failure or ambiguous outcome is `BLOCKED` for the owner. Do not widen scope, add agents or
  retry indefinitely.

## Lessons from operation

- **Measure a runtime knob before building on it.** A few live read-only calls show whether a documented parameter is
  honoured; grep the framework for hard floors before changing a config value.
- **Diagnose live failures from service logs** (tool names, error classes, timestamps — never message content). Identical
  errors across retries usually mean the host retries a turn that cannot succeed.
- **Stop patching around a model.** After two host-side fixes for the same failing natural task, report a model/architecture
  fit problem instead of adding a third workaround.
- **RED evidence must reach the promised assertion**, not fail on an import or syntax error.
- **Your gate, your interpreter.** A worker's shell may resolve a different `python3`; rerun the full gate in the parent
  environment before any commit.
- **Verification shells fail closed:** `set -euo pipefail`; print the success marker only after all assertions.
- **Simplicity checkpoint:** at an iteration boundary, after two failed rounds, or ~30 minutes without a new functional
  result, spend at most five minutes asking which layers and tests protect a current requirement; record keep / cut / defer.
  Never simplify away privacy, authority, data integrity, resource bounds or Stop.

## Limits

A cooperative workflow, not a sandbox. A worker can technically write outside scope; the post-run path check detects it.
Cancellation proof, crash recovery and adversarial containment are separate work.
