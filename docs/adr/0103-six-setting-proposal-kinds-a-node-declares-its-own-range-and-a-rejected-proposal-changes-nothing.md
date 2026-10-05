# ADR-0103 — Six setting proposal kinds; a node declares its own range; a rejected proposal changes nothing

## Status

Accepted, 2026-09-30. Panel decision under autonomy (issue #8). AGREED on ranges and where they live, on the applier log and timing, and on rejection. AGREED 2–1 on the kind list, on skip, and on who authors a proposal. Lens A: Codex `gpt-6-sol`. Lens B: Fable 5.1. Lens C: Gemini 3.1 Pro (High), on the three splits only.


Amended 2026-09-30 by [ADR-0113](0113-a-dispatch-node-lets-an-agent-request-children-inside-an-author-fixed-envelope-and-code-validates-each-request.md): a spawn request sits in an agent's output beside proposals. It is not a proposal kind. No proposal adds a node. A spawn instantiates a child inside a dispatch envelope.

## Context

ADR-0100 to ADR-0102 fix the node kinds, the `when`, the budget, the deadline, and the run record. The map rules that the model proposes and code validates and applies (issue #2). dev_squad adapts in 39 places (counts unverified), and code makes every one of them from model output plus constants (`docs/research/dev-squad-adaptivity.md`, sections 2 and 7). This record fixes the closed menu of proposal kinds, who declares the bounds, what the applier logs, and what happens to a proposal outside the menu. Ticket #9 decides which observations trigger a proposal.

An SDK node is one `query()` call. It takes model, effort, tool surface, turn cap, and spend cap per call. `set_model` is client-only, so no setting changes inside a running call (`docs/research/sdk-per-node-primitives.md`, sections 1 and 2).

## Decision

1. **Six proposal kinds, closed:** `set_model`, `set_effort`, `narrow_tools`, `shrink_turns`, `shrink_spend`, `shrink_loop_max`. The validator refuses any other kind. Each kind changes one setting on one existing node. No kind adds, removes, or reorders a node. No kind touches a gate.
2. **Skip and repeat are not kinds.**
   - Skip is a `when` that the graph author declares on each skippable node. An agent's output yields a code-derived fact, and the `when` reads that fact on every entry (ADR-0101 rules 2, 4, and 5). A gate never carries a `when`.
   - Repeat is the reserved word `repeat` on a `next`, bounded by the loop maximum.
3. **The target node declares its own range.** An `adapt` block on the node lists the kinds it allows, the model list, the effort list, and the tool subset. The minimums for the turn cap, the spend cap, and the loop maximum live on the existing `budget` and `max` fields. A node with no `adapt` block is not adaptable.
4. **A proposal never widens a bound.** It cannot raise a cap, add a tool, or add a node. It swaps a value inside a declared list or shrinks a value toward its declared minimum. A wider bound is a graph edit by a person (ADR-0010).
5. **An agent node authors proposals.** It returns them as a declared field of its own output schema. Code lifts them out. The graph author declares which nodes may propose. An applied change lives in this run only. The graph file changes by a human edit.
6. **The applier acts between nodes.** A validated change takes effect at the next entry of its target and never mid-session.
7. **The run record gets one event per state change** (proposed, validated, applied, rejected). An event holds the proposal id, the kind, the target node, the old value, the requested value, the range it was checked against, the facts it cites with instrument, source, and timestamp, the proposer node and its session id, and the record position (ADR-0008, ADR-0030).
8. **A proposal outside the menu or outside its range is rejected whole.** The applier logs a `rejected` event with the reason and the run goes on with the graph unchanged. The applier never clamps a value and never applies part of a proposal. A rejection changes no node outcome and no run verdict.

## Considered options

- **Skip and repeat as kinds** (Lens A, dissent on the kind list and on skip). Lens A's position: the ticket names them, and a skip that holds for the rest of the run on a node whose `adapt` block permits it (never a gate) is useful. Rejected 2–1: a run-long skip gives the node a second entry semantics, which ADR-0101 rejected for the backstop, and it adds a control path beside `when` and `repeat`. Reopen if ticket #9 shows a needed skip that one `{fact, op, value}` cannot express.
- **A kind that shrinks the deadline.** Rejected by all three lenses in effect: a deadline carries no minimum, so it has no declared range to shrink into. Reopen if a later ticket gives a deadline a minimum.
- **A run-level ceiling at the graph root.** Rejected: one knob with many effects is dev_squad's D13 defect, and the editor cannot show a node's effective range on its card.
- **A proposal that carries its own bounds.** Rejected: code would check only the schema, and the model would author its own limits.
- **Widening with a wait-node approval.** Rejected: a wait cannot mint a bound the graph did not declare. Reopen if a run must exceed a declared cap and a between-run edit is too slow.
- **Clamp to the nearest allowed value.** Rejected: code rewrites the request, and a partial apply is a silent drop (ADR-0003).
- **A dedicated proposer node, or either form per node** (Lens A, dissent on authorship: either, declared per node). Rejected 2–1: a second session and its spend for a fact the producing node already holds, and it puts artificial nodes in the topology. Reopen if a node that proposes its own successor's model shows a bias toward cheap proposals.
- **Effect at once for a cap.** Rejected: a node is one `query()` with its caps fixed at call time.

## Consequences

GLOSSARY.md gains `Adapt block` and the `Proposal kind` entry names the six kinds. Ticket #9 decides which facts trigger a proposal and whether a count of rejections is a fact. Ticket #14 folds outcomes, and a rejection adds none. The schema gains an `adapt` block and a `proposals` field in an agent's output schema.

## Guard

Tests that guard this rule:

- `tests/test_graph.py::test_refuses_a_seventh_proposal_kind`
- `tests/test_graph.py::test_refuses_an_adapt_range_that_widens_a_declared_bound`
- `tests/test_graph.py::test_refuses_shrink_loop_max_on_an_agent_and_a_model_kind_on_a_loop`
- `tests/test_adapt.py::test_each_kind_is_applied_and_logged_as_proposed_validated_applied`
- `tests/test_adapt.py::test_shrink_loop_max_changes_the_loop_at_its_next_entry`
- `tests/test_adapt.py::test_a_seventh_kind_is_rejected_whole_with_one_event_and_no_outcome`
- `tests/test_adapt.py::test_a_bad_proposal_writes_one_rejected_event_and_changes_nothing`
- `tests/test_adapt.py::test_a_node_that_may_not_propose_is_rejected`
- `tests/test_adapt.py::test_a_good_proposal_beside_a_bad_one_applies_only_the_good_one`
- `tests/test_adapt.py::test_a_change_takes_effect_at_the_next_entry_and_never_mid_session`
- `tests/test_adapt.py::test_a_proposal_does_not_change_the_running_loop_entry`

No open part remains. A change to this rule changes its tests in the same commit (ADR-0018).
