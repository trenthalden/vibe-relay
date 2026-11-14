---
name: night-shift
description: Use when leaving an agent alone overnight on a big project.
version: 1.0.0
author: trenthalden
---

# Night Shift

## When to Use

The owner hands a large project to the agent for a whole night (or any long unattended window): "work until morning,
decide alone, I review when I wake up". The agent orchestrates one or more coding workers, keeps going across usage-window
resets and stops cleanly at a hard deadline with an honest morning report.

Not for a single task (use `lean-executor-orchestration`) and not for anything that needs the owner's password, approval
prompt, deployment or publication — those wait for the morning.

## What the plugin does for you (mechanics)

| Need | Tool / hook |
|---|---|
| One durable plan: owner instructions verbatim, window, hard limits, lanes, queue, decisions log, timeline | `vibe_night_start` → `.vibe-relay/night-<stamp>/NIGHT-PLAN.md` + `COMMON.md` |
| Record every queue status, decision and event with a real timestamp | `vibe_night_log` |
| "Is the parent alive?" stamp, refreshed by your own tool calls | `post_tool_call` hook (`LAST-PARENT-UPDATE`) |
| No new dispatch after the hard stop or below the quota reserve | `pre_tool_call` hook blocks `delegate_task` (+ configured tools/commands) |
| Wakes at quota reset and before the hard stop, checked against the quota they will run on | `vibe_night_arm` |
| A wake must not become a second orchestrator | generated continuation prompt starts with `vibe_night_status` and exits if the parent is active |
| Morning report from records, not from memory | `vibe_night_report` → `MORNING-REPORT.md` |
| Owner controls | `/night start|status|arm|report|stop` |

The plugin enforces limits it can see. It cannot infer permission, judge whether a worker's delta is correct, or kill work
that is already running. That is your job, below.

## Procedure

1. **Bound authority and time first.** Copy the owner's words into `owner_instructions`. Write the hard stop, the quota
   reserve (default: stop dispatching at 10 % remaining) and what is forbidden tonight (privileged changes, installs,
   publishing, outbound messages, live runs that need the owner). "Decide alone" never widens these.
2. **Plan the queue by dependency and by file ownership.** One row per portion: lane, scope, depends, status. Parallel lanes
   only with disjoint files, each in its own detached worktree (see
   `lean-executor-orchestration/references/parallel-worktree-lanes.md`). Pin shared interfaces in the decisions log before dispatching both sides.
   Size portions to ~10–15 minutes of worker time; weaker/cheaper workers get small, sharply bounded portions.
3. **Arm the wakes before the first dispatch.** `vibe_night_arm` once: an early checkpoint (the orchestrating session can
   stall), one after the quota reset, one before the hard stop. It refuses when the quota is unknown, the weekly
   reserve is gone, or the profile's default model provider (which cron uses) differs from the provider you checked — fix
   that with the printed `hermes cron edit … --provider … --model …` (an owner-side setting) instead of hoping. Wakes cannot
   arm further wakes; everything is armed now, from the live session.
4. **Dispatch with closed briefs.** Shared rules live in `COMMON.md`; each brief carries INTENT, DECISIONS, ALLOWED,
   FORBIDDEN, TASK, DONE MEANS, GATE and the receipt contract. Use only the executor command the repository approves.
   Confirm the worker actually started before moving on.
5. **Accept, don't trust exit codes.** Read the receipt and the real diff, rerun the gate yourself, grep the production path
   for fixtures/fakes/empty policies that make tests green. One run plus at most one correction per portion; an honest
   no-edit decline gets one smaller re-scope or a different worker, not a third attempt. Log each outcome with
   `vibe_night_log` (status + evidence path) the moment it happens.
6. **Decide alone, reversibly.** When a choice would normally go to the owner, take the reversible option and log it as a
   decision with its doubt. Irreversible or authority-widening choices are parked for the morning.
7. **Integrate and gate once sources settle.** Parent applies accepted patches; run the full gate once, then only rerun what a
   later change touches. Record what was not tested.
8. **Stop on time, report honestly.** At the dispatch stop the hook refuses new work; let running workers finish, accept or
   park them, then `vibe_night_report`. Lead the owner summary with outcome counts, what is accepted with evidence, decisions
   taken without them, what is not verified, and what needs them. Never write that something ran when the record does not
   show it.

## Failure modes seen in real nights

- **Wakes fired into an exhausted quota** (HTTP 429) — the night was lost silently. Nothing had checked the quota of the
  model the jobs would run on (cron uses the profile's default model, not the chat's). Fixed by the arm preflight and an
  owner-pinned job model.
- **The parent chat stalled for hours** while workers sat idle; a safety-net wake with the stale-stamp rule resumed the queue.
  That is why the default arm includes an early checkpoint, not only the quota-reset wake.
- **Green worker gates hid hollow production paths** (fixture model wired into the real service, empty policy objects,
  scaffold flags). Parent acceptance caught them; worker receipts did not.
- **Large cross-module portions** came back as no-edit declines from smaller models; moving them to a stronger worker fixed it.
- **Timing-based tests flaked under parallel load**; synchronize on events, not sleeps.
- **Output saved only to the job log** looked like a silent night. Wakes deliver to the originating chat by default.

## Stop rules

- Nothing new after the dispatch stop; nothing beyond the quota reserve.
- Never a second writer in a lane; a wake that finds the parent active exits with one line.
- Two runs per portion at most; ambiguous or forbidden outcomes are `BLOCKED` for the owner.
- No commits, pushes, installs, deployments or outbound messages unless the owner authorized exactly that for tonight.
