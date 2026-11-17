# ADR-0169: A gate runs the test scope its stage names

## Status

Accepted.

## Context

The loop shares a base battery: `ruff check squad tests`, `mypy squad` and a pytest run. The pytest run over the whole tree costs the machine, and a work-branch tip that a small change reaches needs only the tests that change reaches. `squad/testselect.py` names those tests from the diff against a base, and answers `all: <reason>` when it cannot cover the diff.

The landing gate, the promotion proof and the nightly are the gates a delivery stands on. A cheaper scope must never weaken them. Before this decision the landing gate ran the base battery with no stage, so it inherited the delta the selector answers for a work tip.

A child's own gate carries a bound of its own ([ADR-0163](0163-a-proof-is-bounded-at-fifteen-minutes.md)). It waits behind the gates of its siblings, so against a full battery that bound arrives before the pytest ends: the gate stops, ends unproven with no check red, and the run spends its attempt on the machine instead of on the tree. One slot per child settles nothing, because a full battery at any child count is still the whole tree inside one bound.

## Decision

1. **A stage names the scope.** The battery takes a stage. An intermediate stage (`fast`, `wave-entry`, `wave-tip`, `prepush`) runs the delta that the change reaches. The `landing` stage, the promotion proof and the nightly run the full battery.
2. **A full stage never asks the diff.** The selector answers `all: <stage> runs the full battery` before it reads the diff. The promotion proof and the nightly run the full suite directly, with no selector, so no stage name narrows them.
3. **An unknown stage is full.** A stage the selector does not name as a delta stage answers `all`. A typo costs the battery and never narrows a gate.
4. **A delta that cannot cover the change falls back to the full battery, except at the child's gate.** An uncovered path, a shared file, a test helper, a base that does not resolve, and a selector failure each answer `all` with the reason. The full battery then runs; the delta never passes by a narrower check ([ADR-0018](0018-failing-gate-is-a-defect.md)). An uncommitted change the committed diff cannot place, and a delta whose every reached test the tip deleted, answer `all` too, naming the files.
   The child's own gate narrows instead: an `all` answer there runs no pytest, keeps the `delta` scope and records the change the delta cannot cover, so the gate ends unproven with that reason. The full battery belongs to the landing gate over the branch, and the narrowing is the caller's, never a stage's: the `fast` stage keeps its fallback for the wave entry, the wave tip and the landing check. Each of those proves a tree the loop stands on; the child's gate is a stop condition ([ADR-0012](0012-gate-is-a-stop-condition.md)) that proves what it can inside its bound.
   A test the walk reaches is selected by that edge, whatever else it loads: no importer's selection hides behind a test's own dynamic load, so a test never answers `all`. The refusal stays with a module on the edge, where a load the selector cannot see can hide the importers that module carries.
5. **An empty delta is unproven, never a pass.** When the change reaches no test (no changed file, or only inert ones), the selector answers `none: <reason>`, no pytest runs, and the gate records `unproven` with that reason and `pytest` among the checks that did not run ([ADR-0164](0164-delivery-outranks-the-proof.md)). A suite that never ran stays apart from a suite that passed.
6. **The gate records the scope.** The gate writes the selection and the scope it names to the status line and the history line: `delta` or `full` beside `selected N files` or `all: <reason>`. The reason in an `all:` line is why the delta refused, or the stage that asked for the full battery. A gate that ran no battery records no scope. The child's narrowed `all` answer records `selected 0 files` and `delta`, because no pytest ran and no full battery was asked for.
7. **The hub reads the scope it knows.** The hub accepts `delta` and `full` and rejects a line whose scope it does not know, so a corrupt record never draws a pass.
8. **The landing check asks for the landing stage.** Its local pytest is the full battery, never the delta. The work-branch tip, the wave entry through the fast gate, and the wave tip ask for a delta stage and stay a delta; the wave entry and the wave tip keep the full-battery fallback rule 4 names, and the child's own gate asks that same `fast` stage and narrows it alone.

## Consequences

A work-branch tip, a wave entry and a wave tip run only the tests their diff reaches, and the machine saves the time the other tests would take. A branch that lands in `dev` by the local gate runs the full non-heavy battery. The promotion proof and the nightly keep the full suite, so the heavy, serial and two-mode coverage stays where [ADR-0153](0153-the-full-suite-runs-nightly-in-both-modes.md) and [ADR-0173](0173-the-complete-tier-runs-on-the-promotion-request.md) place it.

A delta rests on the imports and path literals the selector reads. A test that reaches a changed module by a path the selector cannot see is not selected; the full battery at the landing gate, the CI suite and the nightly find it.

## Guard

`tests/test_testselect.py` guards rules 1 to 5: each delta stage selects the delta, the full stages answer `all`, an unknown stage answers `all`, an uncovered path or an uncommitted change answers `all` with its reason, and an empty delta answers `none` with its reason. `tests/test_gate_scope.py` guards rules 1, 4, 5, 6 and 8: the battery passes the stage to the selector, writes `delta` or `full`, runs the full battery for a full stage and for a delta that refuses, the landing and wave-tip scripts ask for their own stage, and the promotion proof and the nightly run the full suite with no selector. `tests/test_hub_gate_fact.py` guards rule 7. `contrib/squad-goal/scripts/test-fast-gate.sh` guards rule 1 at the work-branch tip, rule 5 (an empty selection is unproven in the status and the history), and rule 4 and rule 8 at the child's gate: an `all` answer there runs no pytest and ends unproven with its reason, an entry gate at the same stage still runs the full battery, and a pass the narrowed gate kept answers no entry gate. `contrib/squad-goal/scripts/test-branchland.sh` guards rule 8 at the landing gate, `contrib/squad-goal/scripts/test-wave.sh` guards rule 8 at the wave tip, and `contrib/squad-goal/scripts/test-gate.sh` guards rule 6 (the selection and the scope in the status line and the history line).
