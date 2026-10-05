# ADR-0108 — Run spend is a measured fact; a cap never starts an entry it cannot fund; the floor is an optional gate

## Status

Accepted, 2026-09-30. Panel decision under autonomy (issue #19). AGREED on all five sub-questions. Lens A: Codex `gpt-6-sol`. Lens B: Fable 5.1. No split, so no third lens.

Amended 2026-09-30 (ticket #50): a child with a `call_start` event and no outcome crashed: a resume writes its outcome as fail, reasons `crashed` and `launched_no_outcome`, and keeps its grant as unmeasured spend (ADR-0051 `call_start`).

## Context

ADR-0100 defines budget as a turn cap and a spend cap, each with a minimum and a maximum. ADR-0106 puts a run spend cap in the `run` block, and an override may only lower it. ADR-0103 lets a proposal shrink a cap toward its minimum and never raise it. The SDK checks `max_budget_usd` after a response, so spend can pass a cap by one response (`docs/research/sdk-per-node-primitives.md`, section 1). dev_squad used its budget object once, as a token floor before Execute (T15). The prototype wrote that floor as a gate named `budget_gate`.

## Decision

1. **Node spend counts against the run cap.** Code reads each agent entry's spend from the `ResultMessage` whose `origin` is empty. A subagent's spend is already inside it. Code sums the spend into `run_spend`, a registered fact. Its instrument is the SDK result cost, and its source is the run id and record position. `run_spend_remaining` is a derived fact: the run cap minus `run_spend`.
2. **The `outcome` event carries `spend`, `turns`, and the effective cap** that the engine passed to the SDK, as diagnostic fields. A node killed at its deadline may yield no `ResultMessage`. Its event then records `spend: null`, and `run_spend` leaves it out. This is an instrument gap, unverified, to close with a probe and not with a guess (ADR-0012 rule 4).
3. **Before each agent entry the engine passes the SDK the lesser of the node's cap and the run remainder.** The run cap taking effect is not an engine override of the node's budget (ADR-0106 rule 9). Parallel `each` items reserve their spend atomically, so two items never claim the same remainder.
4. **A cap ends the node through its artifact.** The outcome still derives from the artifact (ADR-0102 rule 8). If the artifact is absent or invalid, the outcome is `fail`, with the reason `node_turn_cap`, `node_spend_cap`, or `run_spend_cap`. If the artifact is valid, the outcome is `pass`, and the cap reason rides in the record as a diagnostic. Probed 2026-10-04 (SDK 0.2.161, `max_turns=2`, JSON written in the first turn): a capped call returns `ResultMessage(subtype='error_max_turns', result=None)`, and the adapter reads the artifact from `result` only. So through the real SDK a capped node has no artifact and reads `fail`; the `pass` branch is a defensive path. Guards: `tests/test_spend.py` (agent node) and `tests/test_dispatch.py::test_a_capped_child_with_a_valid_artifact_passes_and_its_cap_rides_as_a_diagnostic` (dispatch child).
5. **The engine starts no agent entry that it cannot fund.** When `run_spend_remaining` is at or below zero, or below the node's spend minimum, the entry does not start. The node ends `fail`, with the reason `run_spend_cap`, and no session id. Then `next.fail` applies as for any fail. An author may route it to a wait node if a person should decide. The default fold is the run verdict `failed` with the reason (ADR-0105).
6. **Spend that passes a cap by one response is recorded as measured.** It counts toward `run_spend` and is not a defect. Overshoot beyond one response is a defect to investigate (ADR-0018).
7. **A proposer sees `run_spend`, `run_spend_remaining`, and a node's spend and turns only when its `sees` list names them.** A proposal may shrink a later node's cap toward its declared minimum. No kind raises a cap (ADR-0103).
8. **The dev_squad floor (T15) is an optional gate.** An author may place a gate that compares `run_spend_remaining` to a fixed threshold, with `next.fail` set to `exit:fail` and the reason `spend_floor`. Its threshold never adapts. It stops the run before a whole loop, for example before action nodes create worktrees. It is the author's choice, not an engine rule.
9. **The validator refuses a graph whose run spend cap is below any agent node's spend minimum.** It also refuses an engine option that lowers the run cap below the first agent node's minimum.

## Considered options

- **The run cap as a separate meter that only a gate reads.** Rejected: no cap would reach the SDK call, and a node could spend the whole remainder.
- **A cap outcome of `needs-human`.** Rejected: raising a cap is a graph edit (ADR-0103 rule 4), and a changed graph cannot resume (ADR-0107 rule 1), so a person has nothing to unblock in place. A wait node after the failed node stays available.
- **A fifth outcome `budget`.** Rejected: ADR-0100 rule 6 and ADR-0105 hold four outcomes.
- **Hide spend from the proposer as a proxy for effort.** Rejected: the instrument is the SDK result and not the model, so it is a measurement. Every kind only tightens, and ADR-0103 names the reopen condition for a bias toward cheap proposals. Reopen if a record shows a proposer citing `run_spend` to `set_model` an actor cheaper before a gate turns red. Then restrict spend facts to shrink kinds.
- **The floor as a `when` on the next loop.** Rejected: a `when` skips the loop silently, and the run could fold `completed` with no work done.
- **No floor; the per-entry cap replaces it.** Rejected: the per-entry cap fires only at zero remainder and per node, not at a phase boundary.
- **Start an entry below its minimum.** Rejected: it spends money on a session that the author declared unable to do the work. Reopen if an author needs a best-effort tail node; that would be an opt-in node field.

## Consequences

GLOSSARY.md gains `Run spend`. The registry gains `run_spend` and `run_spend_remaining`. The `outcome` event gains `spend`, `turns`, and `effective_cap`. A later probe should check whether a deadline-killed node leaves any cost figure. If #18 rules a stopped run something other than needs-human, a run-cap stop may follow it.

## Guard

Tests that guard this rule:

- `tests/test_graph.py::test_refuses_a_run_cap_below_an_agent_spend_minimum`
- `tests/test_spend.py::test_run_spend_sums_measured_results_only`
- `tests/test_spend.py::test_unknown_spend_is_never_counted_as_zero`
- `tests/test_spend.py::test_an_entry_the_run_cannot_fund_is_not_started_and_the_node_folds_fail`
- `tests/test_spend.py::test_the_engine_passes_the_lesser_of_the_node_cap_and_the_run_remainder`
- `tests/test_spend.py::test_a_cap_ends_a_node_through_its_artifact_with_a_reason`
- `tests/test_spend.py::test_a_spend_cap_hit_under_a_small_remainder_names_the_run_cap`
- `tests/test_spend.py::test_spend_past_a_cap_by_one_response_is_recorded_as_measured`
- `tests/test_spend.py::test_the_optional_spend_floor_gate_stops_the_run_with_its_reason`
- `tests/test_spend.py::test_the_reservation_never_exceeds_the_remainder`
- `tests/test_spend.py::test_a_run_cap_below_an_agent_spend_minimum_is_refused_at_load`
- `tests/test_each.py::test_two_parallel_items_reserve_atomically_against_a_small_remainder`
- `tests/test_dispatch.py::test_an_unmeasured_child_keeps_its_grant_out_of_measured_spend_on_resume`
- `tests/test_dispatch.py::test_a_child_that_crashed_after_launch_is_not_run_again_and_its_grant_stays_unmeasured`

No open part remains. A change to this rule changes its tests in the same commit (ADR-0018).
