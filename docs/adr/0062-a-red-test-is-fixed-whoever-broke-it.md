# ADR-0062: A red test is fixed, whoever broke it

## Status

Accepted, 2026-09-29.

## Context

A run can report "suite green, one pre-existing red test". The run then delivers its own change and leaves the red test standing. The test was red before the run started.

Rafael's rule: a red test must be fixed. It does not matter which session or change broke it. A broken test means something does not work as expected.

A red test is a measured defect (ADR-0018). Who broke it, and when, does not change what it measures. "Pre-existing" moves the defect out of the report and into nobody's hands. That is the silent drop that ADR-0003 forbids.

## Decision

1. **Fix each red test, check, or guard that a run sees in that run.** Fix it whether or not the run caused it. "Pre-existing", "not mine", "already failing", and "unrelated" are not valid end states for a report.
2. **Fix the cause.** Do not delete the test. Do not skip it. Do not loosen its assertion. Do not mark it flaky. One exception exists. If the test itself is shown to be wrong, correct the test and state why.
3. **If the run cannot fix it, escalate.** Examples are a lock, a repo the run does not own, or a missing credential. Send the failing output and the obstacle to Rafael. Do not park the test.
4. **Do not report "suite green" while any test in the run suites is red.** Report the count, the name of each red test, and the repair.
5. **This holds for every agent in the workflow.**

## Consequences

A report cannot hide a known defect behind a label. Each run leaves the suites at least as green as it found them.

Cost: a run can take longer than its own change needs. The alternative is a red test that nobody owns.

Guard: none yet. This rule is a draft until a named guard test exists (ADR-0018).
