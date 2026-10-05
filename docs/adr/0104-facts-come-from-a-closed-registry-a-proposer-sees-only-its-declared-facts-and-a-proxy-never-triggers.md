# ADR-0104 — Facts come from a closed registry; a proposer sees only its declared facts; a proxy never triggers

## Status

Accepted, 2026-09-30. Panel decision under autonomy (issue #9). AGREED on the registry, the `sees` list, the forbidden-trigger rule, and that only an agent proposes. AGREED 2–1 on `tier`, on gate outcomes as triggers, and on staleness. Lens A: Codex `gpt-6-sol`. Lens B: Fable 5.1. Lens C: Gemini 3.1 Pro (High), on the three splits only.


Amended 2026-09-30 by [ADR-0113](0113-a-dispatch-node-lets-an-agent-request-children-inside-an-author-fixed-envelope-and-code-validates-each-request.md): the registry gains `depth`, `children_count`, and `spawn_rejected_count`, and a dispatching agent may list them in `sees`.

## Context

ADR-0103 fixes the six proposal kinds and says the applier logs the facts a proposal cites. ADR-0008 fixes the observation contract. ADR-0012 says a gate is a stop condition and never a target, and that an agent's narration is an indication, not a measurement. This record fixes which facts exist, who produces them, which the model sees, and which it may never cite as a trigger. dev_squad's predicates read changed files, `webApp`, learning counts, the effectiveness index, task count, and architecture issue count (`docs/research/dev-squad-adaptivity.md`, section 7).

## Decision

1. **A closed `facts` registry in the graph document declares each fact:** its name, type, instrument, and source kind. Only deterministic code produces a fact. A model never mints one. A `when` and a proposal read registered names only. A derived fact is code computing over a node's artifact, the run record, or the target.
2. **Each proposing node lists `sees: [names]` inside its `adapt` block.** The validator refuses a name that is not registered or is on the forbidden list. A proposal that cites a fact outside its proposer's `sees` list is rejected whole (ADR-0103 rule 8).
3. **The starting set:**
   - `outcome:<node>` (its value may be `deadline`; there is no separate deadline fact);
   - loop entry count;
   - turns and spend so far, against the node's caps;
   - elapsed time, against the node's deadline;
   - diff size read from the target: files, added lines, sensitive paths;
   - rejected-proposal count in this run;
   - `tier`, for a `when` only. A proposal may not cite it, until the scout's size estimate is measured by an independent instrument;
   - gate finding counts by class: a proposer may see them and may never cite one as a trigger.
4. **Refused now:** a cross-run fact (effectiveness index, learning counts), a model's confidence or self-report, a model judge's score, and any fact whose instrument is a model. A cross-run fact returns when a later ticket gives it a generator, a source, and a commit (ADR-0008 rule 6).
5. **The forbidden-trigger rule has two parts.** The closed registry is the mechanism: an unregistered fact is refused at the validator. The ADR names the forbidden examples, and each is a fixture for the guard (ADR-0008 rule 7): a gate's pass rate, a gate finding count or trend, a model self-report, a judge score.
6. **A gate's outcome may trigger a proposal.** The target may be an agent sibling in the gate's loop, or `shrink_loop_max` on that loop. Every kind only tightens, and none touches a gate. A gate's finding count never triggers one. This follows the rule "print the number, act on the outcome" (ADR-0012).
7. **A fact carries instrument, source, timestamp, and severity.** The source is a run id plus a record position, or a target commit plus a path. Code refuses a fact with no timestamp or no source. Code refuses a fact whose source has a newer version than the one the fact used, and a fact that names a future record position. A fact is not refused only because it predates the position where it is read. The proposer sees the value with these fields, and the applier logs what it cited.
8. **Only an agent authors a proposal.** Code lifts and validates it. A deterministic response to a fact is a `when` the author declared, read on every entry. A fact that must stop the run is a gate or a `when`, never a silent setting change.

## Considered options

- **`tier` as a proposer trigger now** (Lens B, dissent). Lens B: `tier` is code over the scout's artifact, and the backstop already uses the same instrument. Rejected 2–1: the size estimate is the model's own narration, and ADR-0012 rule 4 calls that an indication until an instrument measures it. Reopen when an independent size instrument exists.
- **A gate outcome may never trigger a proposal that touches its loop** (Lens A, dissent). Rejected 2–1: every kind only tightens and none touches a gate, so a proposal cannot retune the gate, and the outcome is a decision, not the printed number. Reopen if a run shows a proposer cycling `set_model` on an actor until the gate turns green. Then restrict gate-outcome triggers to shrink kinds.
- **Re-derive every fact at the read position, so no fact is older than that position** (Lens B, dissent on staleness). Rejected 2–1: freshness follows the source, not the position. A fact whose source did not move is still true. Reopen if a run shows stale state from an unmoved source.
- **No registry; any run-record event is readable.** Rejected by all lenses: a proposer would see every proxy, and the editor could not show what it sees.
- **A model may register a fact if code re-derives it.** Rejected: this is a minted identifier with an extra step (ADR-0008 rule 2).
- **Code auto-applies a declared shrink rule.** Rejected: code gets a second author path that the record schema, with its proposer node and session id, cannot name.
- **The proposer sees the value alone.** Rejected: a model would cite a value whose instrument it cannot name (ADR-0012 rule 4).

## Consequences

GLOSSARY.md changes the `Fact` entry and gains `Facts registry` and `Sees list`. Ticket #14 folds outcomes and reads the same `outcome:<node>` fact. The schema gains a `facts` registry and a `sees` field in the `adapt` block. Cross-run facts stay refused; ADR-0110 rules them out of round one and states the reopen condition.

## Guard

Tests that guard this rule:

- `tests/test_graph.py::test_refuses_an_unregistered_fact_in_a_when_and_a_sees_name`
- `tests/test_graph.py::test_refuses_tier_in_a_sees_list_but_reads_it_in_a_when`
- `tests/test_graph.py::test_refuses_each_forbidden_trigger_example`
- `tests/test_graph.py::test_refuses_a_fact_whose_instrument_is_a_model`
- `tests/test_graph.py::test_false_positive_valid_documents_raise_no_error`
- `tests/test_facts.py::test_the_registry_loads_from_the_graph_document_and_refuses_an_unregistered_read`
- `tests/test_facts.py::test_tier_is_readable_by_a_when_and_refused_in_a_sees_list`
- `tests/test_facts.py::test_each_forbidden_trigger_example_is_refused_by_the_closed_registry`
- `tests/test_facts.py::test_a_fact_with_no_source_or_no_timestamp_is_refused`
- `tests/test_facts.py::test_freshness_refuses_a_future_position_and_a_newer_source_version`
- `tests/test_facts.py::test_false_positive_an_old_fact_from_an_unmoved_source_passes`
- `tests/test_adapt.py::test_a_bad_proposal_writes_one_rejected_event_and_changes_nothing`
- `tests/test_adapt.py::test_an_unavailable_cited_fact_rejects_the_proposal`
- `tests/test_adapt.py::test_each_kind_is_applied_and_logged_as_proposed_validated_applied`

No open part remains. A change to this rule changes its tests in the same commit (ADR-0018).
