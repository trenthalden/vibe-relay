# Parallel writer lanes in detached worktrees

Use when two or more workers implement at the same time. The main checkout becomes integration-only; each lane is the sole
writer in its own worktree.

## Setup (per portion)

1. **Split by file ownership, not by topic.** List the files each lane may edit; no file in two lanes. Interfaces one lane
   implements and another calls are pinned up front (exact names, return shapes) in the plan's decisions log; the calling
   lane tests against a fake.
2. **Shared rules in one `COMMON.md`** (project facts, forbidden effects, privacy, receipt format, interpreter, gate style).
   Each brief carries only INTENT, DECISIONS, ALLOWED, FORBIDDEN, TASK, DONE MEANS, GATE and its worktree/TMPDIR/receipt paths.
3. **Fresh detached worktree** under the project's ignored temp dir: `git worktree add --detach temp/<run>/wt-<portion> HEAD`,
   then apply every accepted patch so far, in order. No new branch, no branch switch in the main checkout. Record the
   baseline tree before dispatch: `git -C <wt> add -A && git -C <wt> write-tree > <portion>/base.tree`.
4. **Run the gate in the fresh worktree before dispatch** and record the baseline. Give each lane a short TMPDIR
   (Unix-socket paths overflow inside long worktree paths).
5. **Dispatch and confirm** each worker is actually running before you stop watching.

## Acceptance and integration

1. Read the receipt and the real diff in that worktree; changed paths must be inside ALLOWED.
2. Rerun the gate yourself in the worktree.
3. Build the patch against the recorded baseline, from the worktree's own index:
   `git -C <wt> add -A && git -C <wt> diff --cached --binary $(cat <portion>/base.tree) > <portion>/accepted.patch`.
   Diffing against HEAD would re-include hunks of earlier accepted patches.
4. `git apply --check`, then `git apply` in the integration tree (working tree only; no commit). Rerun affected modules.
5. The next portion starts from a **new** worktree = HEAD + all accepted patches. Exception: a portion's single correction
   runs in its own worktree; apply there any accepted patch it must build on and re-record the baseline first.
6. Run the full gate once in the integration tree when sources settle, not per portion.

## Pitfalls

- Two writers in one tree break each other's tests through half-edited imports even with disjoint ALLOWED lists.
- A patch touching a file another lane changed will not apply: that is an ownership-split defect — fix the split.
- When a hot shared file must change in both lanes, split ALLOWED by named region; separate hunks then merge cleanly
  (`git merge-file` three-way).
- Take the integration file set from the receipt's FILES cross-checked with the diff; files another lane landed meanwhile
  also differ, and copying them back would revert that lane.
- `git worktree remove` refuses a dirty worktree: save `accepted.patch` first, then remove task-owned worktrees only.
- The orchestrator is the bottleneck: accept from the receipt, the diff of key files and one gate rerun; log each event with
  one small edit; keep a continuation armed in case the orchestrating session stalls.
- Size portions to ~10–15 minutes of worker time. Briefs scoped much larger hit hard walls; smaller models may return a
  no-edit decline on large cross-module refactors — re-scope once or move the portion to a stronger worker.
