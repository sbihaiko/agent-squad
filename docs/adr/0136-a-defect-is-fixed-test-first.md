# ADR-0136: A defect is fixed test-first, and the red run kept

## Status

Accepted.

## Context

A fix written before its test is a fix nobody measured: it compiles, it looks
right, and the next change to that path finds out. The failing run is the only
evidence that the defect existed and that this change is what removed it.

The squad already asks for this in three places — the host project's
`SG_CODING_RULES`, the pre-push checklist of every request, and the fork-only
checklist that carries the `TDD RED` comment. Asking is not a guarantee: an
agent may obey or not, and where the deliverable is a local commit the only
signal today is a warning that lets the landing continue.

A defect is verifiable by execution, which is why this rule can be guaranteed
rather than requested: the test either fails on the parent commit or it does
not.

## Decision

1. **Every fix of a reported defect starts from a test that fails on the
   unfixed code.** The test is committed, and the fix comes after it.
2. **The red is measured at the parent.** The test, checked out at the commit
   before the fix, fails there. A test that already passed at the parent is not
   the red of this fix, and a fix whose test only ever ran green has not been
   verified.
3. **The red travels with the deliverable.** The test's name and its failure
   line, captured against the parent, go in the PR body where there is a PR,
   and in a `TDD RED` comment on the issue where the work is fork-only. The
   green run of the same test follows the fix the same way.
4. **The check refuses; it does not warn.** A landing whose fix carries no red
   run is a defect of the landing, not a note on it.
5. **A defect an agent finds is fixed the same way, whoever caused it and
   whenever it began** (ADR-0062, ADR-0133). There is no "pre-existing" path
   around this rule.
6. **A defect in the test itself is corrected as a test.** Deleting, skipping
   or loosening the assertion is not a fix (ADR-0062).

## Guard

None yet. The named guard is `scripts/check-tdd-red.sh` in the host project,
wired into `$SG_PREPUSH_CHECKS`: it takes the test a fix adds or changes,
checks that test out at the fix's parent commit, and fails the push when it
passes there. A push with no such test and a test that already passed are the
same refusal. Until that script and the squad test that proves the refusal
exist, this rule is a draft (ADR-0018).
