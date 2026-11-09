---
name: lean-executor-orchestration
description: Use when delegating code. Dispatch once; accept receipt.
version: 1.0.0
author: trenthalden
---

# Lean Executor Orchestration

## When to Use

Use for a bounded development task delegated to a coding agent (for example Cursor), or for a narrowly triggered read-only review by a second model (for example Claude).

## Goal

The executor spends the implementation tokens and tool calls. The orchestrator sends **one closed brief**, receives a **compact receipt**, and decides: accept, one correction, or block. Do not build an orchestration platform for normal development tasks.

## Roles

- **Executor (coding agent):** sole writer of source, tests, builds and task-scoped documentation.
- **Reviewer model:** read-only plan or review, only for a multi-component change or a change touching security, authority, memory, delivery, runtime or installation. Not a default second pass.
- **Orchestrator:** dispatches, checks the receipt and the *actual* delta, then accepts, corrects once or blocks. Does not recreate the implementation work.
- **Owner:** resolves blocked work and separately authorizes commit, push, installation, restart or deployment.

## Dispatch

1. Capture the working-tree state without changing it.
2. Send one self-contained brief with `INTENT`, `DECISIONS`, `ALLOWED`, `FORBIDDEN`, `TASK`, `DONE MEANS`, one `GATE`, a one-pass limit and the required receipt fields.
3. Require the final receipt: `FILES`, `GATE` command and result, `BOUNDARIES`, residual risk.
4. Run one executor session. If acceptance fails, send one resume/correction with the exact observed error. Do not start a fresh review cascade.

### Hermes + vibe-relay

When the active delegation backend is `vibe-relay`, do **not** edit the failing code in the parent turn. Call `delegate_task` once with:

- `goal` — the closed brief (or a short plain-language task)
- `allowed` — path globs the child may change (required; e.g. `textutil/**`)
- `gate` — one shell command that must PASS on the real diff (required; e.g. `python -m unittest discover -s tests -v`)
- `lane` — `auto` unless the owner forced otherwise

After the child returns: run the gate yourself if needed, then `vibe_accept`. Refuse to widen `allowed` to “fix” an out-of-scope edit.

### Brief template

```text
INTENT: <one sentence>
DECISIONS: <already made; do not reopen>
ALLOWED: <paths/globs>
FORBIDDEN: commit, push, install, service/config/runtime changes, paths outside ALLOWED
TASK: <2–5 concrete steps>
DONE MEANS: <observable outcome>
GATE: <one command>
LIMIT: one pass; stop with the blocker if incomplete
RECEIPT: FILES; GATE command + concise result; BOUNDARIES ok|exception; residual risk
```

### Periodic simplicity checkpoint

At each outcome-sized iteration boundary, after two unsuccessful correction/review rounds, or after about 30 minutes of engineering without a new useful functional result, spend **at most five minutes** critically checking architecture *and* test scope. Ask which layers/options protect a current requirement, which tests duplicate the harness, and which existing simpler path can deliver the outcome. Record only keep / cut / defer, the next functional run and safety constraints in the existing checklist. Trigger it immediately if verification/coordination outweighs implementation.

Do not add a reviewer, audit document, timer, service or gate for the checkpoint. Never simplify away privacy, authority, data integrity, resource bounds or Stop.

## Acceptance

For agent projects, use a short safety smoke, then let the **real agent** perform the authorized functional task through its actual executor; independently verify tool effects and artifacts. Synthetic replies prove plumbing only. Do not require exhaustive timing/queue/failure matrices or a polished regression harness before a harmless disposable functional run. A demonstrated critical safety defect still blocks its affected operation. Keep required completion/release gates and explicit deployment permissions. A missing worker receipt limits *source* acceptance but is not a blanket ban on otherwise safe, authorized functional observation.

Accept only when:

1. the receipt exists;
2. actual changed paths match `FILES` and stay inside `ALLOWED`;
3. the stated gate is the gate from the brief and succeeds;
4. `DONE MEANS` follows from the delta and the gate;
5. a risk-triggered independent gate rerun, when required, succeeds.

Rerun the gate independently only when the task changes a security, authority, memory, delivery or runtime boundary; changes the gate itself; or has a receipt that conflicts with the actual delta. Do not add synthetic edge-case suites for normal work.

## Stop rules

- At most **two executor runs** per task: original plus one correction.
- At most **one** reviewer-model pass, only when its trigger applies.
- A forbidden effect, second failure or ambiguous outcome is `BLOCKED` for the owner. Do not widen scope, add agents or retry indefinitely.
- Keep any owner-set quota reserve in force.

## Lessons from operation

- **Launch the executor in a mode that allows its shell calls** (for Cursor: `cursor-agent --print --force "$(cat brief.md)"` as a notified background process). Without it, shell calls are rejected, the worker cannot run its gate, and the receipt is an implementation receipt, not verification.
- **One writer at a time.** Queue the next brief behind the running worker (`while kill -0 <pid>; do sleep 15; done; <next run>`). Two writers in one tree collide on files and on the shared full gate. Also check the process tree for duplicate resumed sessions on the same worktree before a write phase.
- **Measure a runtime knob before building on it.** A few live read-only calls show whether a documented parameter (thinking off, token cap) is honoured. Before changing a config value, grep the pinned framework for hard floors on that key; a value below a startup floor passes unit tests and stops the agent at start.
- **Diagnose a live failure from service logs first:** tool names, error classes, timestamps — never message content. Repeated identical errors across retries of one event usually mean the host retries a turn that can never succeed; record a host defect instead of only scoring the model.
- **Stop patching around a model.** If the same agent fails the same natural task after two host-side fixes, report a model/architecture-fit problem and propose the larger change instead of a third workaround.
- **Read the diff, not only the receipt.** Workers sometimes relax a shared rule (a filter, an ordering query, a guard) to make their feature pass. One correction restores it with a narrow exemption and a regression test.
- **RED evidence must reach the promised assertion.** A new automated regression detector must fail on the relevant old/broken behavior, not on syntax/import/attribute errors. Ordinary functional observations and real-agent runs do not first need planted faults. A script that successfully observes the old limitation is not a failing success assertion.
- **Your gate, your interpreter.** Before a commit, rerun the full gate in the parent environment. A worker's shell may resolve a different `python3`; a module that imports a pinned runtime can pass in the worker's receipt and fail under the project's host interpreter.
- **Verification shells fail closed:** start multi-step gates with `set -euo pipefail`; print the success marker only after all assertions.

## Optional journal

Use a task board only as a manual status journal when several independent tasks are active. Do not make it a required transport layer or build a custom runner/MCP integration unless the owner explicitly reopens that work.

## Limits

This is a cooperative workflow, not a sandbox. An executor may technically write outside scope; the post-run path check detects it rather than preventing it. Backend-compute cancellation proof, full crash recovery and adversarial executor containment are separate work, not prerequisites for lean delegation.
