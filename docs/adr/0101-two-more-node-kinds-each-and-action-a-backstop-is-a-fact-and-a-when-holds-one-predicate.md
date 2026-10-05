# ADR-0101 — Two more node kinds, `each` and `action`; a backstop is a fact; a `when` holds one predicate

## Status

Accepted, 2026-09-29. Panel decision under autonomy (issue #11). AGREED on the two kinds and on the backstop. AGREED 2–1 on a gate inside a conditional loop, and on one predicate per `when`. The control model was provisional; [ADR-0115](0115-control-is-order-based-and-a-next-takes-six-reserved-words-with-no-sibling-jumps.md) ratified it (issue #15).

Amends [ADR-0100](0100-the-graph-has-four-node-kinds-and-a-gate-node-is-never-skipped.md): six kinds, not four; rule 3's backstop sentence is replaced.

## Context

The prototype on branch `prototype/five-phases-as-graph-data` wrote the five dev_squad phases as a graph in the ADR-0100 vocabulary and walked ten scenarios. The five phases, three loops, and three tier skips walk as data. Two things needed fields outside the vocabulary, and one rule of ADR-0100 could not be run without a bypass.

1. Execute runs work items in batches. No kind fans out.
2. A git commit, a worktree create or revert, and landing a merge are code with side effects. They are not model calls and not checks. No kind fits.
3. dev_squad's size backstop (T5) re-enables the critic that the trivial tier's `when` skipped. ADR-0100 calls it a gate. A gate whose fail edge routes into a node is not a stop condition (ADR-0012). The walk worked only when an edge bypassed the target's `when`.
4. The visual loop carries a `when` and holds a gate. A `when` on a loop skips the loop's gate children.
5. "tier is not trivial and stop_after is not scout" does not fit one `{fact, op, value}`.

## Decision

1. **Six node kinds, closed: agent, gate, wait, loop, each, action.**
   - **each**: runs its ordered children once per item of a declared fact (`items`), in `mode` parallel or sequence. Its outcome is the join of the item outcomes. The join rule is decided in issue #10.
   - **action**: deterministic code with declared side effects, no model, no spend. It may carry a `when`. It has an outcome like any node.
   - A gate cannot be a subagent, so "work items are subagents inside one agent node" was rejected.
2. **A backstop is a fact, not a gate.** Code derives `review_required` from the tier and the diff (files, added lines, sensitive paths). The critic's `when` reads it. A gate's fail edge never routes into a node; a gate stops or repeats.
3. **A gate inside a conditional loop is allowed.** The loop's `when` governs the loop. ADR-0100's rule concerns a `when` on the gate itself. A gate guards the product of its loop; when the loop does not run there is nothing to check. Lens B adds a validator rule, recorded as a suggestion: a loop that carries a `when` must hold at least one agent, each, or action child, or it is a skip in disguise.
4. **A `when` holds one predicate.** A compound condition becomes a named code-derived fact with its own instrument, source, and timestamp (ADR-0008, ADR-0012). `stop_after` is a run option the engine applies by truncating the run, not a node predicate.
5. **Control model (ratified by ADR-0115, which adds `exit:deadline` and `exit:needs-human`):** control is order-based; the root is a loop; a child's `next.<outcome>` takes only the reserved words `repeat`, `continue`, `exit:pass`, `exit:fail`; a `when` is read on every entry and is never bypassed. Routing to a later sibling is a `when` on that sibling. This is the smallest of the three readings and the easiest to extend.

## Considered options

- Fan-out as a `for_each` field on loop. Rejected: a loop's `max` and an item count are two different numbers, and `max` is dead under `for_each`.
- A commit as an agent with a tool surface limited to git. Rejected: an agent cannot promise determinism or zero spend, and the editor's card would lie.
- Keep the backstop as a gate and let an explicit edge bypass the target's `when` (prototype rule R3). Rejected by both lenses: one node would have two entry semantics, and the gate's fail would be non-terminal.
- Forbid a `when` on a loop that holds a gate (Lens A, dissent on Q5): "gate never skipped" should mean never skipped, and lifting the gate out makes its inactive case explicit. Rejected 2–1: lifting produces the vacuous gate the learning check already shows.
- `all` and `any` in `when` now (Lens A, dissent on Q6): compound tier and stop-after rules stay graph data. Rejected 2–1: a compound predicate carries no instrument, and one predicate covered all ten scenarios.

## Consequences

GLOSSARY.md gains `Each` and `Action`, and the Gate and When entries change. The prototype fixture is not rewritten; it stays as the primary source on its branch. Issue #10 decides the `each` join and parallel semantics. Issue #9 decides the derived facts, the `outcome:<node>` fact, and the facts and gates registries the fixture needed. Issue #16 decides which run options are graph data.

## Guard

Tests that guard this rule:

- `tests/test_graph.py::test_refuses_a_gate_next_that_names_a_node`
- `tests/test_graph.py::test_refuses_a_when_with_two_predicates`
- `tests/test_graph.py::test_refuses_a_loop_with_a_when_that_holds_only_gates`
- `tests/test_engine_skeleton.py::test_a_when_is_read_on_every_entry_and_a_skip_is_recorded`
- `tests/test_engine_skeleton.py::test_a_when_false_skips_the_node_and_no_call_is_made`
- `tests/test_engine_skeleton.py::test_next_words_continue_drops_a_fail_and_exit_fail_ends_the_loop`
- `tests/test_actions.py::test_an_action_may_carry_a_when_and_a_skip_is_recorded`
- `tests/test_actions.py::test_worktree_create_makes_a_worktree_and_records_its_head`
- `tests/test_actions.py::test_merge_land_merges_the_worktree_branch_into_the_target`
- `tests/test_actions.py::test_a_merge_conflict_fails_the_node_and_leaves_the_target_clean`
- `tests/test_actions.py::test_the_validator_gives_an_action_a_deadline_and_a_when_field`
- `tests/test_each.py::test_each_runs_the_children_once_per_item_and_numbers_items_from_one`
- `tests/test_each.py::test_the_join_is_fail_then_deadline_then_needs_human_then_pass`
- `tests/test_each.py::test_needs_human_beats_pass_in_the_join`
- `tests/test_each.py::test_first_failure_stops_the_items_after_the_first_failing_one`
- `tests/test_each.py::test_first_failure_cancels_running_siblings_in_parallel_mode`
- `tests/test_each.py::test_parallel_items_run_together_up_to_max_parallel`
- `tests/test_each.py::test_the_run_limit_caps_max_parallel`
- `tests/test_each.py::test_an_item_gets_the_lesser_of_its_deadline_and_the_remaining_each_time`
- `tests/test_each.py::test_two_parallel_items_reserve_atomically_against_a_small_remainder`
- `tests/test_resume.py::test_a_wait_ends_the_process_with_needs_human_and_exit_10`
- `tests/test_five_phases.py::test_a_trivial_input_skips_spec_and_plan_but_the_gates_still_run`
- `tests/test_five_phases.py::test_a_medium_input_runs_spec_and_skips_plan`

No open part remains. A change to this rule changes its tests in the same commit (ADR-0018).
