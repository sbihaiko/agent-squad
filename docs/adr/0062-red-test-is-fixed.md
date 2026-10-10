# ADR-0062: A red test is fixed, whoever broke it

## Status

Accepted.

## Context

A run can report "suite green, one pre-existing red test" and leave that test standing. A red test is a measured defect (ADR-0018). Who broke it does not change what it measures. "Pre-existing" moves the defect out of the report and into nobody's hands.

## Decision

1. **Fix each red test, check, or guard that a run sees in that run,** whether or not the run caused it. "Pre-existing", "not mine", "already failing" and "unrelated" are not valid end states.
2. **Fix the cause.** Do not delete, skip, loosen or mark the test flaky. One exception: if the test itself is shown to be wrong, correct it and state why.
3. **If the run cannot fix it, escalate.** Examples are a lock, a repo the run does not own, or a missing credential. Send the failing output and the obstacle to the owner. Do not park the test.
4. **Do not report "suite green" while any test is red.** Report the name of each red test and its repair.
5. **This holds for every agent in the workflow.**

## Guard

The full suite, run in both modes (ADR-0018).
