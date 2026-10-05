# ADR-0115 — Control is order-based; a `next` takes six reserved words; there are no sibling jumps

## Status

Accepted, 2026-09-30. Ratified by the owner (issue #15). Settles the provisional rule 5 of [ADR-0101](0101-two-more-node-kinds-each-and-action-a-backstop-is-a-fact-and-a-when-holds-one-predicate.md). A fresh panel, run at the owner's request, was AGREED on B: Lens A Codex `gpt-6-sol`, Lens B Fable 5.1. The two lenses ran blind to each other's answer, but not in one message.

## Context

The first panel split three ways on the control model (issue #15). A: keep forward jumps. B: no jumps. C: the prototype's rules, where a jump enters its target even when the target's `when` is false. B was the working default. No scenario in the ten prototype scenarios needed a jump.

## Decision

1. **Control is order-based.** The root is a loop. A loop runs its ordered children.
2. **A `next` takes six reserved words and nothing else:** `repeat`, `continue`, `exit:pass`, `exit:fail`, `exit:deadline`, `exit:needs-human`. A `next` never names a node. This holds for every node kind.
3. **There are no sibling jumps.** Routing to a later sibling is a `when` on that sibling over a declared fact, including `outcome:<node>`.
4. **A `when` is read on every entry and is never bypassed.** No node has two entry semantics.
5. **Reopen** if a target must run on a jump but not by order under the same facts, and no derived fact can express it.

## Considered options

- **A. Forward jumps kept.** Rejected: a forward jump and a `when` on the target give the target two readings, and the author must reason about both. Every jump is `when outcome:<sender>` on the target under B.
- **C. Prototype rules, R3.** Rejected: a jump that bypasses `when` gives one node two entry semantics. A backward jump is a second loop mechanism beside `repeat` and the loop maximum, and it breaks `loop_max_exhausted` (ADR-0105).

## Consequences

Adding jumps later is one schema enum and one engine branch. Removing them later would rewrite every graph that used them. ADR-0101 rule 5 is no longer provisional. `GLOSSARY.md` and the schema drop the provisional mark.

## Guard

Tests that guard this rule:

- `tests/test_graph.py::test_refuses_a_gate_next_that_names_a_node`
- `tests/test_graph.py::test_refuses_a_next_that_names_a_node_on_every_node_kind`
- `tests/test_graph.py::test_the_next_words_are_the_six_reserved_words`
- `tests/test_engine_skeleton.py::test_next_words_continue_drops_a_fail_and_exit_fail_ends_the_loop`
- `tests/test_engine_skeleton.py::test_a_when_is_read_on_every_entry_and_a_skip_is_recorded`

No open part remains. A change to this rule changes its tests in the same commit (ADR-0018).
