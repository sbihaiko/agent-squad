# ADR-0103: Six setting proposal kinds over a closed facts registry; a rejected proposal changes nothing

## Status

Accepted

## Context

The model proposes and code validates and applies. A node is one SDK `query()` call, so no setting changes inside a running call. This record fixes the closed menu of proposal kinds, who declares the bounds, which facts exist, and what happens to a proposal outside the menu. Facts are observations (ADR-0012: a gate is a stop condition, never a target; a narration is an indication, not a measurement).

## Decision

### Proposals

1. **Six proposal kinds, closed:** `set_model`, `set_effort`, `narrow_tools`, `shrink_stalls`, `shrink_spend`, `shrink_loop_max`. The validator refuses any other kind. Each kind changes one setting on one existing node. No kind adds, removes, or reorders a node. No kind touches a gate. A spawn request is not a proposal kind (ADR-0113).
2. **Skip and repeat are not kinds.** Skip is a `when` the graph author declares on a skippable node. A gate never carries a `when`. Repeat is the reserved word `repeat` on a `next`, bounded by the loop maximum (ADR-0100).
3. **The target node declares its own range.** An `adapt` block lists the kinds it allows, the model list, the effort list, and the tool subset. The minimums for the stall cap, the spend cap, and the loop maximum live on the existing `budget` and `max` fields. A node with no `adapt` block is not adaptable.
4. **A proposal never widens a bound.** It swaps a value inside a declared list or shrinks a value toward its declared minimum. A wider bound is a graph edit by a person.
5. **An agent node authors proposals,** as a declared field of its own output schema. Code lifts them out. The graph author declares which nodes may propose. An applied change lives in this run only.
6. **The applier acts between nodes.** A change takes effect at the next entry of its target, never mid-session.
7. **The run record gets one event per state change** (proposed, validated, applied, rejected). An event holds the proposal id, kind, target, old value, requested value, the range checked, the facts cited (instrument, source, timestamp), the proposer node and session id, and the record position.
8. **A proposal outside the menu or its range is rejected whole.** The applier logs a `rejected` event with the reason. The run goes on with the graph unchanged. It never clamps a value, never applies part of a proposal, and a rejection changes no node outcome and no verdict.

### Facts

9. **A closed `facts` registry in the graph document declares each fact:** name, type, instrument, source kind. Only deterministic code produces a fact. A model never mints one. A `when` and a proposal read registered names only.
10. **Each proposing node lists `sees: [names]` in its `adapt` block.** The validator refuses an unregistered or forbidden name. A proposal that cites a fact outside its proposer's `sees` list is rejected whole.
11. **The starting set:**
    - `outcome:<node>` (its value may be `deadline`);
    - loop entry count;
    - spend so far against the node's cap, and the stalls it has hit;
    - elapsed time, against the node's deadline;
    - diff size read from the target: files, added lines, sensitive paths;
    - rejected-proposal count in this run;
    - `depth`, `children_count`, and `spawn_rejected_count` for a dispatching agent (ADR-0113);
    - `tier`, for a `when` only, until an independent instrument measures the scout's size estimate;
    - gate finding counts by class: a proposer may see them and may never cite one as a trigger.
12. **Refused:** a cross-run fact, a model's confidence or self-report, a judge's score, and any fact whose instrument is a model. A gate's pass rate or finding count or trend never triggers a proposal. Each is a guard fixture.
13. **A gate's outcome may trigger a proposal** on an agent sibling in the gate's loop, or `shrink_loop_max` on that loop. Every kind only tightens. Print the number, act on the outcome.
14. **A fact carries instrument, source, timestamp, and severity.** The source is a run id plus a record position, or a target commit plus a path. Code refuses a fact with no source or timestamp, a source with a newer version than the fact used, and a future record position. An old fact from an unmoved source is valid.
15. **Only an agent authors a proposal.** A deterministic response to a fact is an author-declared `when`. A fact that must stop the run is a gate or a `when`, never a silent setting change.

Rejected: a proposal that carries its own bounds; clamping to the nearest value (a partial apply is a silent drop); a run-level ceiling at the graph root (one knob, many effects); a registry-free read of any record event.

## Guard

`tests/test_graph.py`, `tests/test_adapt.py`, `tests/test_facts.py`. A change to this rule changes its tests in the same commit (ADR-0018).
