---
name: context-budget-audit
description: Use when auditing agent context or token bloat.
version: 1.0.0
author: trenthalden
---

# Context Budget Audit

## Overview

Keep agent context intentional instead of letting skills, docs, tool output, memory packets and cron prompts grow without measurement. Combines three habits: budget accounting, strategic compaction, and runtime telemetry.

## When to Use

- A task is large enough that compaction or checkpointing may matter.
- Answers degrade after adding memory, context packets or prompt layers.
- You are adding new skills or long docs to an always-on agent.
- Retrieval or context packets change.
- Recurring cron jobs call LLMs.
- A router, classifier, heartbeat or background worker looks expensive relative to its output.
- Provider/agent telemetry shows high average prompt size, spend spikes or growing cache-read/input tokens.
- Tool output is noisy or repeatedly truncated.

## What to measure

Approximate tokens for:

- loaded skills and references;
- project instructions (`AGENTS.md` and similar);
- persona/system-prompt sections;
- always-injected profile memory / user profile blocks;
- available-skills catalogues added by enabling a skills toolset;
- tool schemas added by enabled toolsets;
- architecture-doc sections pulled into the prompt;
- retrieval-corpus packets and workspace-context packets;
- cron prompts and script output;
- user attachments and staged-upload excerpts;
- raw tool-result bulk and its replay across steps.

Heuristics (coarse, label as approximate): prose ≈ words × 1.3; mixed/code/logs ≈ chars / 4.

A quick estimator for local artifacts:

```bash
wc -c AGENTS.md docs/ARCHITECTURE.md | awk '{printf "%s: ~%d tokens\n", $2, $1/4}'
```

## Budget rules

- Prefer lazy-loaded skills over always-included prompt text.
- Put long stable guidance in references and load it only when needed.
- Summarize tool output to IDs/paths/counts; read full files only when necessary.
- Recurring jobs get hard call caps and logged prompt estimates.
- Compact or checkpoint at phase boundaries: after design, after tests, after deploy/commit.
- Never compact away unresolved failures or unverified assumptions.

## Warning signals

Investigate if:

- the same file or log content is loaded repeatedly;
- tool output is truncated more than once;
- more than 3 repair attempts happen without new evidence;
- a cron LLM job has no hard call cap;
- a context packet includes full articles or raw long rows;
- the agent cites a session summary as evidence instead of source refs.

## Telemetry pattern

Do not rely only on provider dashboards. Inspect the profile-local session store first and sanitize any provider API checks.

- Useful session fields: source, model, API call count, input/cache-read/output tokens, estimated cost, system prompt.
- Useful message fields: role, tool name, content length, first/last messages (for attachment/tool-output bulk).
- Group by *source* to separate live chat, routers, cron, heartbeat workers, healthchecks and manual runs; sort by input tokens and cost to find the real spend centers.
- Use a provider's per-key usage endpoint with the profile's existing key only for sanitized totals; never print keys. Treat account-wide analytics and generation-content endpoints as privileged unless explicitly authorized.

For a multi-step agent, estimate both the **fixed prefix** and the **replay cost**:

- system prompt: persona, project rules, tool discipline, memory, skills catalogue;
- tool-schema payload: enabled toolsets, especially skills and broad bundles;
- user/task payload: attachments, daily context, TODOs;
- history replay: prior assistant/tool messages resent every step.

## Lane-splitting recommendation

If a low-level call does not need personality or durable memory, do not run it as the full agent:

- **Router/classifier:** no persona, no memory, no skills, no tools, single turn, tiny JSON output.
- **Lean worker:** short safety/identity stub, retrieved facts only, minimal toolsets, capped outputs.
- **Persona/composer:** full persona and compact memory only for the final human-facing answer or reflective prose.
- **No-agent script:** deterministic reporting, budget checks and state summaries with no LLM.

Prompt caching is a cost/latency optimization, not a replacement for context reduction. Cache-read tokens still indicate dependence on repeated context and can hide context distraction/confusion.

## Audit output

```text
Context budget audit
Scope: <session|script|feature>
Largest contributors:
- <component>: ~N tokens — <why loaded>
Waste/repetition:
- <item>
Risk: low|medium|high
Actions: <remove | lazy-load | cap | summarize | compact>
Verification: <command/log/test>
```

## Verification checklist

- [ ] Largest context contributors identified.
- [ ] Proposed removals or lazy-loads preserve safety rules.
- [ ] Token estimates labeled approximate.
- [ ] Recurring LLM paths have hard budget/cap checks.
- [ ] The next compact/checkpoint boundary is explicit.
