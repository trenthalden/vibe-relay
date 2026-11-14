# Acceptance checklist

Run through this for every worker receipt before integrating. It is short on purpose; stop at the first hard failure.

## 1. Scope

- `git status --short` and `git diff --stat` in the worker's tree match the receipt's FILES.
- Every changed path is inside ALLOWED. An out-of-scope edit is reverted or rejected — never legalized by widening ALLOWED.
- No checklist items, shared rules, filters, guards or ordering owned by someone else were relaxed to make this feature pass.

## 2. Gate

- The gate run is the brief's gate, verbatim, from the worker's tree, in the project's interpreter.
- You ran it yourself; record the numbers (tests run, failures, skips, validator summary).
- New regression tests fail on the old behaviour for the promised reason (not on an import error) and pass on the new one.
- Timing-based tests are synchronized on events, not sleeps.

## 3. Production path

- Grep the production composition for fixtures, fakes, injected test models, placeholder flags and empty policy objects.
- Every external consumer of a changed message, schema or file format still accepts it (other services, other hosts,
  stored data written by the previous version).
- Persisted state and logs contain only what the privacy rules allow (use sentinel strings in tests).

## 4. Outcome

- `DONE MEANS` follows from the delta and the gate, not from the receipt's prose.
- Unverified parts are listed as unverified in your handoff; nothing is reported as done that the record does not show.

## 5. Decision

- **Accept:** integrate, log the status with evidence, then dispatch dependents.
- **Correct once:** one brief with the exact observed failure and the narrow fix expected.
- **Block:** after a second failure, a forbidden effect or an ambiguous outcome — park it for the owner with evidence.
