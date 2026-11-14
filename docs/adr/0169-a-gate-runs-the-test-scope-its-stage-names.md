# ADR-0166: A gate runs the test scope its stage names

## Status

Accepted.

## Context

The loop shares a base battery: `ruff check squad tests`, `mypy squad` and a pytest run. The pytest run over the whole tree costs the machine, and a work-branch tip that a small change reaches needs only the tests that change reaches. `squad/testselect.py` names those tests from the diff against a base, and answers `all: <reason>` when it cannot cover the diff.

The landing gate, the promotion proof and the nightly are the gates a delivery stands on. A cheaper scope must never weaken them. Before this decision the landing gate ran the base battery with no stage, so it inherited the delta the selector answers for a work tip.

## Decision

1. **A stage names the scope.** The battery takes a stage. An intermediate stage (`fast`, `wave-entry`, `wave-tip`, `prepush`) runs the delta that the change reaches. The `landing` stage, the promotion proof and the nightly run the full battery.
2. **A full stage never asks the diff.** The selector answers `all: <stage> runs the full battery` before it reads the diff. The promotion proof and the nightly run the full suite directly, with no selector, so no stage name narrows them.
3. **An unknown stage is full.** A stage the selector does not name as a delta stage answers `all`. A typo costs the battery and never narrows a gate.
4. **A delta that cannot cover the change falls back to the full battery.** An uncovered path, a shared file, a test helper, a base that does not resolve, and a selector failure each answer `all` with the reason. The full battery then runs; the delta never passes by a narrower check ([ADR-0018](0018-failing-gate-is-a-defect.md)). An uncommitted change the committed diff cannot place, and a delta whose every reached test the tip deleted, answer `all` too, naming the files.
   A test the walk reaches is selected by that edge, whatever else it loads: no importer's selection hides behind a test's own dynamic load, so a test never answers `all`. The refusal stays with a module on the edge, where a load the selector cannot see can hide the importers that module carries.
5. **An empty delta is unproven, never a pass.** When the change reaches no test (no changed file, or only inert ones), the selector answers `none: <reason>`, no pytest runs, and the gate records `unproven` with that reason and `pytest` among the checks that did not run ([ADR-0164](0164-delivery-outranks-the-proof.md)). A suite that never ran stays apart from a suite that passed.
6. **The gate records the scope.** The gate writes the selection and the scope it names to the status line and the history line: `delta` or `full` beside `selected N files` or `all: <reason>`. The reason in an `all:` line is why the delta refused, or the stage that asked for the full battery. A gate that ran no battery records no scope.
7. **The hub reads the scope it knows.** The hub accepts `delta` and `full` and rejects a line whose scope it does not know, so a corrupt record never draws a pass.
8. **The landing check asks for the landing stage.** Its local pytest is the full battery, never the delta. The work-branch tip, the wave entry through the fast gate, and the wave tip ask for a delta stage and stay a delta.

## Consequences

A work-branch tip, a wave entry and a wave tip run only the tests their diff reaches, and the machine saves the time the other tests would take. A branch that lands in `dev` by the local gate runs the full non-heavy battery. The promotion proof and the nightly keep the full suite, so the heavy, serial and two-mode coverage stays where [ADR-0153](0153-the-full-suite-runs-nightly-in-both-modes.md) and [ADR-0156](0156-a-full-proof-carries-across-a-covered-delta.md) place it.

A delta rests on the imports and path literals the selector reads. A test that reaches a changed module by a path the selector cannot see is not selected; the full battery at the landing gate, the CI suite and the nightly find it.

## Guard

`tests/test_testselect.py` guards rules 1 to 5: each delta stage selects the delta, the full stages answer `all`, an unknown stage answers `all`, an uncovered path or an uncommitted change answers `all` with its reason, and an empty delta answers `none` with its reason. `tests/test_gate_scope.py` guards rules 1, 4, 5, 6 and 8: the battery passes the stage to the selector, writes `delta` or `full`, runs the full battery for a full stage and for a delta that refuses, the landing and wave-tip scripts ask for their own stage, and the promotion proof and the nightly run the full suite with no selector. `tests/test_hub_gate_fact.py` guards rule 7. `contrib/squad-goal/scripts/test-fast-gate.sh` guards rule 1 at the work-branch tip and rule 5 (an empty selection is unproven in the status and the history), `contrib/squad-goal/scripts/test-branchland.sh` guards rule 8 at the landing gate, `contrib/squad-goal/scripts/test-wave.sh` guards rule 8 at the wave tip, and `contrib/squad-goal/scripts/test-gate.sh` guards rule 6 (the selection and the scope in the status line and the history line).
