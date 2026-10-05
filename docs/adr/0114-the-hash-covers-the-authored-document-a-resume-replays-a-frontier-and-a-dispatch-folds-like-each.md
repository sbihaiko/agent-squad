# ADR-0114 — The hash covers the authored document; a resume replays a frontier; a dispatch folds like each

## Status

Accepted, 2026-09-30. Panel decision under autonomy. Frontier: `AGREED` (Lens A and Lens B concur, no Lens C needed). Hash and fold: `AGREED` 3–0 in the design-direction panel. Lens A: Codex `gpt-6.1-sol`. Lens B: Fable 5.1. Lens C: Gemini 3.1 Pro (High), on the request-shape split only. Amends ADR-0105 rule 3, ADR-0107 rules 1, 2, and 7, and ADR-0111 rules 3 and 8.

Amended 2026-09-30 (ticket #50): rule 4 now distinguishes the two crashes. No `call_start`: the child is in the frontier and launches. A `call_start` and no outcome: the child crashed, is not run again, and the verdict carries `crashed`.

## Context

ADR-0113 lets a run grow children at run time. The graph hash, the resume, and the verdict fold all assumed a fixed graph. ADR-0107 named the reopen: a resume must skip a node that already holds an exit event in its current entry, "for example when parallel `each` items partly passed." Parallel dispatch children are that case.

## Decision

1. **The graph hash covers the authored document only.** That includes each dispatch node, its envelope, and its `allow` list with pinned skill versions. It never covers a spawned child. Layout stays outside it (ADR-0111 rule 8). A change to the envelope or an allowed skill version is a new graph version.
2. **The record holds the realized graph.** A child exists in a run because a `spawn_accepted` event says so. That event holds the full child definition after validation, the parent, the depth, the reservation, and the record position.
3. **A resume replays a frontier, not one position.** It replays the walk as ADR-0107 rule 1 says. It then replays `spawn_accepted` events in record order to rebuild every child, the depth, and the fan-out count. The frontier is every accepted child with no exit event in its current entry, plus the restart position of rule 2 of ADR-0107 for authored nodes. Each frontier child starts a fresh session. A child that already holds an `outcome` event stands on that event and is not run again.
4. **A crash between acceptance and launch leaves a child in the frontier.** It has an accept event and no entry, so it launches on resume. The engine rechecks the funding of its reservation, since only a measured SDK result counts as spend (ADR-0108 rule 2). Unknown spend is never counted as zero.
5. **The workspace preflight applies to every frontier child** (ADR-0107 rule 5). A started child with no exit event is not repaired. The engine never stashes or recreates a workspace.
6. **The `resume` event lists every frontier position.** It replaces the single restart position of ADR-0107 rule 7 with a list. A run with no dispatch has a list of one.
7. **A dispatch node folds like `each`.** It joins the outcomes of its children under its `join` rule: `all` (fail, then deadline, then needs-human, then pass) or `first_failure`. The `checks` gates run per child, and a failed check is that child's `fail`. A dispatch entry with zero accepted children folds to `fail` with reason `no_dispatch`, so an empty run never folds to `completed`.
8. **The verdict function is unchanged.** The root is still an authored loop. Root pass is completed, root fail or deadline is failed, root needs-human is needs-human, and a truncated run is stopped (ADR-0105, ADR-0112). `conflict` comes from the target.

## Considered options

- **One restart position, extended by a replay step** (Lens B in the design-direction panel and Lens C). Rejected for the frontier when children run in parallel: one position cannot say which siblings passed. It suffices for a dispatch with sequence-only children. Reopen if the engine limits dispatch to one child at a time.
- **A run scope with no authored root** (Lens A, design-direction panel). Rejected for now: a root loop holding one dispatch node gives the same closure and keeps the verdict function unchanged. Reopen if a dynamic flow needs a run with no authored node at all.
- **The hash covers children.** Rejected: identity would change during the run, and no resume could match it.

## Consequences

ADR-0107 rule 2 now reads "one position, or a frontier when a dispatch holds parallel children". A resume of a graph with no dispatch behaves as before. The editor reads the new events as read-only (ADR-0111 rule 3).

## Guard

Tests that guard this rule:

- `tests/test_verdict.py::test_a_dispatch_entry_with_no_accepted_child_folds_to_fail_no_dispatch`
- `tests/test_dispatch.py::test_the_dispatch_folds_under_its_join_rule`
- `tests/test_dispatch.py::test_zero_accepted_children_fold_to_fail_no_dispatch`
- `tests/test_dispatch.py::test_a_resume_runs_only_the_frontier_and_never_a_passed_sibling`
- `tests/test_dispatch.py::test_a_child_with_accept_and_no_entry_is_funded_again_and_unknown_spend_is_not_zero`
- `tests/test_dispatch.py::test_the_workspace_preflight_applies_to_a_frontier_child`
- `tests/test_dispatch.py::test_the_router_fixture_record_yields_the_frontier_of_one_child_and_one_resume_event`
- `tests/test_dispatch.py::test_a_crash_before_any_request_is_decided_replays_the_entry_artifact`
- `tests/test_dispatch.py::test_a_deadline_child_folds_the_dispatch_as_deadline_not_a_new_exit_code`
- `tests/test_dispatch.py::test_a_child_that_crashed_after_launch_is_not_run_again_and_its_grant_stays_unmeasured`

No open part remains. A change to this rule changes its tests in the same commit (ADR-0018).
