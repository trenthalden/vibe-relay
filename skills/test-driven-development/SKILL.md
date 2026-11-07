---
name: test-driven-development
description: Use when testing code or agents. Choose minimal evidence.
version: 1.0.0
author: trenthalden
---

# Risk-Proportional Test-Driven Development

## Select the evidence before writing a test

Use the project's selected testing depth and the owner's acceptance method. TDD is a method for exposing a concrete defect or implementing a specified deterministic behavior, not permission to build a testing platform. Do not promote lean work to advanced testing without the owner's decision.

- **Deterministic defect:** reproduce the observed failure, make the smallest correction, repeat the same assertion. Keep one minimal regression when it protects a meaningful promise.
- **Agent behavior / tool environment:** a short safety smoke followed by the **real agent** executing a functional task in its authorized environment is the primary evidence. Observe actual tool effects, files, memory and failures. A synthetic response or schema inventory is not functional success. Add a deterministic regression only for a concrete adapter/guard defect, not for every imaginable model trajectory.
- **Docs, skills, ordinary configuration and disposable exploration:** use the relevant parser, validator or functional smoke. Do not manufacture a failing unit test or request an exception merely to satisfy a ritual.
- **Critical data, permission or process boundary:** keep the necessary negative/regression checks on isolated fictitious data. Never use production credentials, personal stores or destructive live actions as fixtures. Field-first does not grant deployment authority or weaken safety controls.

## One short RED → GREEN loop

1. Name the user-visible promise and choose the cheapest check able to falsify it through its real entrypoint.
2. Reproduce the relevant failure. If adding an automated regression, see its intended assertion fail on old/broken behavior (in a scratch copy when needed; protect the working tree).
3. Apply the minimal production correction. Do not add speculative modes, layers, hooks or dependencies just to ease testing.
4. Repeat the same check and the affected module. Run the project's full required gate **once** at the completed iteration/release boundary, not after every assertion.
5. Refactor only task-relevant duplication with a demonstrated maintenance benefit. Do not expand into a cleanup project.

A non-zero subprocess is not automatically RED. Confirm the operation and intended assertion were reached; reject syntax/import/attribute errors and invalid test-method names. A test asserting the old limitation documents that limitation; it is not a failing success assertion. Never catch the test's own failure in a broad expected-exception handler.

Do not delete existing implementation or unrelated work-in-progress because it predates a test. Preserve before-images and test the existing delta honestly. Never hardcode a fake agent answer or synthetic API result into production code to obtain GREEN. Do not commit, install or activate anything solely because tests passed.

## Simplicity checkpoint (part of the existing iteration, not another gate)

At each outcome boundary — and earlier after two failed corrections/reviews, or roughly 30 minutes of engineering without a new useful functional result — spend at most five minutes asking:

- What user result became possible? What exact next functional check demonstrates it?
- Does each new layer, timeout mode and test protect a present requirement or a reproduced failure?
- Can existing platform functionality or one simpler path replace the new machinery?
- Which checks duplicate others or test our harness rather than the product?
- What can be removed or deferred now without weakening privacy, authority, data integrity or Stop?

Record only the keep/cut/defer decision in the living checklist and resume the shortest functional path. If testing effort exceeds implementation, or a second round only repairs the harness, simplify the loop instead of starting another review or raising test counts. The checkpoint has no daemon, new reviewer, scheduler or separate document.

## Test design and completion

- Prefer one scenario per product promise, not one test per function.
- Use the project's framework; mocks replace unsafe external effects, not the behavior being claimed.
- Avoid source-substring/count assertions, duplicate happy paths and exhaustive boundary matrices in lean work. For a demonstrated high-risk failure, choose the few negative cases that establish its specific invariant.
- A check that already passes can legitimately verify existing behavior; do not break unrelated code to manufacture RED. Establish RED before trusting a *newly claimed regression detector*, not before every read-only observation or authorized functional acceptance run.
- For process/I-O promises, thread joins and pure-function tests cannot prove process exit, durable writes or server-side compute cancellation. State exactly what was observed and leave stronger guarantees unproven. Cover the concrete safety risk proportionately instead of requiring every possible race before a harmless disposable field task.

**Completion** means: the requested functional outcome is exercised and evidenced, required project checks ran at the appropriate boundary, known limitations are explicit, and no unsupported deployment claim is made. Test count alone is not completion.

Related: `systematic-debugging`, `lean-executor-orchestration`, `agent-field-acceptance`.
