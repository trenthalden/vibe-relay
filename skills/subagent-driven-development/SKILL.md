---
name: subagent-driven-development
description: Use when executing plans via reviewed subagent tasks.
version: 1.0.0
author: trenthalden
---

# Subagent-Driven Development

## Overview

Execute an implementation plan by dispatching a **fresh subagent per task**, each followed by a **two-stage review** (spec compliance first, then code quality). Fresh context per task avoids accumulated confusion; automated review between tasks catches problems before they compound.

## When to Use

- You have an implementation plan or clear requirements.
- Tasks are mostly independent.
- Quality and spec compliance matter.
- You want review between tasks rather than only at the end.

Do not use it without a plan, or for a single trivial edit.

## The process

### 1. Read and parse the plan once

Read the plan a single time and extract **all** tasks with their full text and context. Create a todo list with one item per task. Do **not** make subagents read the plan file — paste the full task text into each brief.

**Approval and continuation boundary.** Before dispatch, record whether the owner approved one gate or the whole plan; never silently shrink whole-plan approval to the next gate. Track every remaining gate and its acceptance criteria. After verifying and committing a gate, continue with the next approved, dependency-ready gate without another start request. Stop only for an explicit owner boundary, a real blocker, a needed authorization or a scheduled evidence window. An async handoff is temporary: name the pending result and resume on its completion; dispatch is not completion. Do not claim automatic continuation unless a live delegated task or another concrete mechanism exists.

**Worker scope.** Give each worker an exact file allowlist, known unrelated worktree changes, the required evidence and a no-commit instruction when the parent owns integration. The parent stages explicit paths, inspects the staged path set and commits only the verified scope; blanket staging can capture another task's unfinished work.

### 2. Per-task workflow

**Step 1 — implementer subagent.** Give complete context:

```text
goal: Implement Task N: <one line>
context:
  TASK FROM PLAN: <paths to create/change, exact behavior>
  FOLLOW TDD: failing test → run (verify FAIL) → minimal code → run (verify PASS) → run suite (no regressions)
  RETURN: exact changed paths and RED/GREEN output; do not stage or commit
  PROJECT CONTEXT: language/runtime, framework, existing modules, how tests run
toolsets: terminal, file
```

**Step 2 — spec-compliance reviewer** (after the implementer finishes). Read-only. Checklist: all requirements implemented? file paths and signatures match? behavior matches? nothing extra added (no scope creep)? Output: `PASS` or a list of specific gaps. Fix gaps, re-run this review; continue only when compliant.

**Step 3 — code-quality reviewer** (only after spec = PASS). Read-only. Checklist: conventions and style, error handling, naming, test adequacy, bugs/edge cases, security. Output: Critical / Important / Minor issues and a verdict `APPROVED` or `REQUEST_CHANGES`. Fix and re-review until approved.

**Step 4 — mark the task complete** in the todo list.

### 3. Final integration review

After all tasks, dispatch one integration reviewer: do components work together? inconsistencies between tasks? all tests pass? ready to merge?

### 4. Verify and commit

Run the full suite and review `git diff --stat`. Stage explicit verified task paths only (`git add -- <paths>`), check that `git diff --cached --name-only` equals the intended scope exactly, then commit — only when the owner has authorized commits.

## Task granularity

Each task is **2–5 minutes** of focused work. Too big: "Implement user authentication." Right size: "Create the User model with email and password hash", "Add the hashing helper", "Create the login endpoint".

## Red flags — never do these

- Start implementing without a plan.
- Skip either review (spec *or* quality).
- Proceed with unfixed critical/important issues.
- Dispatch multiple implementers for tasks that touch the same files.
- Make a subagent read the plan file.
- Skip scene-setting context (the subagent must know where the task fits).
- Ignore a subagent's questions; answer before it proceeds.
- Accept "close enough" on spec compliance.
- Skip the re-review after fixes.
- Let an implementer's self-review replace a real review.
- **Start quality review before spec review passes** (wrong order).
- Move on while either review has open issues.

## Handling issues

- **Subagent asks questions:** answer clearly and completely; add context; do not rush it.
- **Reviewer finds issues:** the implementer (or a new one) fixes; the reviewer reviews again; repeat until approved.
- **Subagent fails a task:** dispatch a new fix subagent with specific instructions about what went wrong. Do not fix it manually in the controller session (context pollution).
- **Subagent hits a bug:** follow `systematic-debugging`: root cause first, then a regression test, then resume.

## Context discipline

For long runs (multi-phase plans, many subagents, large artifacts) watch for silent context degradation: read less per step as the window fills, prefer paths and summaries over full content, and checkpoint at gate boundaries.

Use the four gate types explicitly when designing checkpoints:

- **Pre-flight** — preconditions checked before work starts.
- **Revision** — a bounded fix-and-re-review loop.
- **Escalation** — hand a decision to the owner when the loop cannot close.
- **Abort** — stop and preserve state when a safety or scope boundary is crossed.

Each gate needs a defined entry condition, failure behavior and resumption rule.

## Why it works

- Fresh subagent per task: no pollution from earlier code or reasoning.
- Two-stage review: spec review catches under/over-building; quality review ensures it is well built.
- Cost trade-off: more invocations (implementer + two reviewers per task), but issues are caught before they compound.

## Integration with other skills

- **Plans** feed this skill; this skill executes them.
- **`test-driven-development`:** include the TDD instructions (or the agreed evidence method) in every implementer brief.
- **`lean-executor-orchestration`:** when a single external coding agent is the sole writer, prefer its one-brief, one-receipt loop instead.
- **`systematic-debugging`:** use when a subagent hits a bug.

## Remember

```text
Fresh subagent per task
Two-stage review every time
Spec compliance FIRST, code quality SECOND
Never skip reviews
Catch issues early
```
