# ADR-0139: A test whose fixture repository is absent is deselected, never skipped

## Status

Accepted.

## Context

Some tests need a second repository on the runner: a MesenAI checkout, a built emulator, a native core. A runner
that does not hold that repository cannot run them. Read as a failure, the absence of the checkout reads as a
defect of the code. Read as a skip, it reads green while proving nothing. Both readings are wrong (ADR-0018 item 6;
ADR-0062 items 2 and 3 refuse a skip). The test is real; the runner simply does not hold its fixture.

A gate that only ever sees one runner cannot hold such a test. The fixture-bound suite and the host-free suite have
to move by different rules, and the difference has to be visible in the test tree, not implied by an environment.

## Decision

1. **A test whose fixture is a repository the runner does not own is deselected on that runner.** The rule lives in
   the test tree: a marker on each such test and one collection hook that reads it. The marker is the visible rule.
2. **A fixture that is set but absent on disk counts as absent.** A name whose path is not on disk is no fixture
   (ADR-0018 item 6).
3. **Deselection is stated, never silent, and the removed tests are reported by name.** A run says which tests it
   dropped and why.
4. **Deselection is not skipping and not loosening.** On a runner that holds the fixture the assertions run exactly
   as they are written (ADR-0062 items 2 and 3).
5. **The suite that needs the fixture runs in its own CI step, outside the gate.** The gate keeps its own command
   unchanged; the fixture-bound suite has a step of its own and reports its own deselection honestly.

## Guard

`tests/test_tooling.py`.
