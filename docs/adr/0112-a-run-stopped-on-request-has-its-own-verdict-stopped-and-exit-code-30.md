# ADR-0112 — A run stopped on request has its own verdict, stopped, and exit code 30

## Status

Accepted, 2026-09-30. Owner ruling on issue #18. It replaces the PROVISIONAL working default in ADR-0106 rule 3.

## Context

`--stop-after <node>` truncates a run on purpose (ADR-0106). The four verdicts fit it badly. `completed` breaks ADR-0029 rule 4. `needs-human` breaks rule 7, since a deliberate stop has no owner deadline. The owner chose a fifth verdict.

## Decision

1. **The verdict is `stopped`, exit code `30`.** The reason is `stopped_after:<node>`.
2. **Precedence.** A truncated run folds first as usual. Any node that ran and failed gives `failed`. A conflict at the target gives `conflict`. A wait that holds gives `needs-human`. Only a run that is otherwise sound gives `stopped`.
3. **`stopped` is not success.** Exit 0 stays reserved for a fully completed run.
4. **The message names what remains by step and the resume command** (ADR-0029 rules 5 and 6, ADR-0107).
5. **A stopped run resumes** under ADR-0107 rule 6: its root has no `outcome` event.

## Consequences

ADR-0029 rule 3 and ADR-0100 rule 6 now list five verdicts. ADR-0105 rule 9 lists the fifth code. A caller that scripts on exit codes can tell a deliberate stop from a wait.

Cost: a fifth code is a contract. A future code needs an ADR that names its caller.

## Guard

Tests that guard this rule:

- `tests/test_verdict.py::test_a_truncated_run_with_every_node_passed_is_stopped_exit_30`
- `tests/test_verdict.py::test_a_truncated_run_with_a_failed_node_is_failed_not_stopped`
- `tests/test_verdict.py::test_a_truncated_run_with_a_conflict_is_conflict_and_with_a_wait_is_needs_human`
- `tests/test_verdict.py::test_a_failed_node_takes_precedence_over_a_conflict_and_a_wait`
- `tests/test_cli.py::test_stop_after_truncates_to_stopped_exit_30_with_the_resume_command`
- `tests/test_cli.py::test_a_stopped_run_resumes_to_completion_through_the_cli`

No open part remains. A change to this rule changes its tests in the same commit (ADR-0018).
