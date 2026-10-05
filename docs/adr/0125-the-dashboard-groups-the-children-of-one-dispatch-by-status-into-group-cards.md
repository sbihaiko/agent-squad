# ADR-0125 — The dashboard groups the children of one dispatch by status into group cards

## Status

Accepted, 2026-10-03. Owner request after the dashboard UI review (`docs/ui-review/2026-10-03-dashboard-review.md`, items 8 and 9, and the running and failed states). Extends [ADR-0124](0124-the-dashboard-side-panel-is-an-inspector-that-starts-hidden-and-tools-live-in-the-top-bar-and-on-the-canvas.md). The first version of this ADR grouped by a count (more than 12 children); the owner replaced that rule with grouping by status.

## Context

A dispatch can spawn 26 children. One card each fills the page, and the cards' text ran past their edges. Two children can run the same issue, so "issue 11" named two cards. A count threshold hid a failed child among 21 passed ones.

## Decision

1. **Child cards clip their text** to the card. The full text stays in the tooltip.
2. **A child title carries its index** among the children of its dispatch: "1 · issue 11".
3. **One bus serves each child column**, and each group card. One line leaves the dispatch entry. A rail runs beside each column, with a stub into each card.
4. **Children of one dispatch group by status.** Children that are done with the same outcome form one group card (21 passed and 5 failed make two cards). Children in flight (a call running, or started with no outcome yet) with the same harness (model, effort, skill) form one group card. A group needs at least 3 children; a smaller set stays as single cards. A queued child and a rejected request are never grouped. There is no count threshold.
5. **A group card shows** "dispatch · N outcome" (a running group adds its harness on a second line), a summary line (total time, cost, tokens in and out; a figure the record lacks reads as a dash), and one square per child. Fill and glyph both show the outcome (✓ ✗ ◷ ! ▶, with … for a call started and – for a child not yet started), so color is never the only cue. A click on a square selects that child. A hover shows its name and cost.
6. **A double-press on a group card expands it** into its child cards in place; the layout is recomputed and the bus edges stay. A double-press on any card of an expanded group folds the group back. The flag lives in page state, not in the record, keyed by a JSON array `["group", dispatch, state, harness]`, so a `:` in a name cannot make two groups read as one. Every card is a keyboard stop: Tab reaches it, Enter selects it, Space opens or folds a group (the focus stays on the card). It survives live updates and the replay cursor. A key that has fewer than 3 children shows no group and keeps its flag.
7. **Every node card and child card carries a state glyph** at its top-right corner, with the same glyphs as the squares and the legend (∅ for a node that never ran). The glyph, class and legend row of a state come from one table (`STATES` in `dashboard/core/state.js`). A running card has a thick teal outline and the ▶ glyph. Nothing animates, so `prefers-reduced-motion` has nothing to switch off. A failure reason is 12 px, light on the dark red card, clipped with the full text in the tooltip.
8. **"No verdict yet" has its own color** (violet), apart from the amber of a deadline, a person needed, or a stalled run. The legend lists each card state with its glyph and each verdict chip class.
9. **A selection pans into view.** When the panel is open and the selected node, child or group card would sit under it (or off the map), the view pans until it shows right of the panel. A card already in view moves nothing. The zoom never changes. A closed panel moves nothing.
10. **The side panel shows a roster** for the group card and for any child: index, name, outcome mark, model, time, cost, turns. A click on a row selects that child.
11. **The panel never says "no error on this node"** for a node the record shows failed. The toast after a load is short and omits the folder name. The replay bar's height is kept clear when the graph is fitted.

## Considered options

Always show cards, only smaller: rejected, 26 cards still cover the page. A table docked on the canvas: rejected, it hides cards. A group by count (above 12): replaced, it hid a failure inside a pile of passes and grouped children that differ in outcome. A group at any size: rejected, a few children read best as cards.

## Consequences

A child in a collapsed group has no card of its own to drag or style. The minimum of 3 is a constant in the core (`MIN_GROUP`). A running child that finishes leaves its running group and joins the group of its outcome, which may be collapsed even if the running group was expanded.

## Guard

`tests/test_dashboard_groups.py` (grouping by outcome and by harness, the minimum of 3, expand and fold keys, double-press, one glyph per state, unique titles, clip path, roster, bus, layout, the pan math, the state CSS, the failed-node panel, the load toast, the replay-bar reserve); `tests/test_dashboard_groups_page.py` (headless Chrome: expand and fold by double-press, a glyph on every card, the pan into view, the keyboard stops, fit clear of the replay bar and the open panel); `tests/test_dashboard_legend_help.py` (the help row for the double-press, the legend glyphs, the chip legend and the "no verdict yet" color).

## Amendment, 2026-10-04: a redo is its own card, with no edge and no "2x" fold

Owner question: when an issue fails and is redone, does the graph show one node marked "2x", or a new node linked to the failed attempt? Settled by the panel, `AGREED 2–1`, a lean panel: Codex was at capacity on two tries, so Lens A′ (`agy`, Gemini 3.1 Pro High) stood in for it, and Lens C was `agy` at Claude Sonnet 5.5 (High), by owner instruction. The pinned Opus id no longer exists on this host, and the owner ruled against Opus for the tiebreak.

1. **A redo stays its own child card** with its own index ("10 · issue 7"). It is never folded into the failed attempt's card as a "2x" count. All three lenses agree. The engine records each attempt as its own node with its own outcome, landing and spend ([ADR-0102](0102-a-node-is-one-fresh-session-truth-is-the-run-record.md)), and a fold would hide a failed child inside a passed one, which this ADR already rejected for count grouping.
2. **The dashboard draws no "redo of" edge.** The record has no field that says which attempt a redo replaces. `task` is content, not identity ([ADR-0113](0113-a-dispatch-node-lets-an-agent-request-children-inside-an-author-fixed-envelope-and-code-validates-each-request.md) rules 4 and 11). A redo can also follow a child that landed with `held_out` `fail`. An edge would be inferred, not recorded. It would also point at a square when the failed attempt sits in a collapsed group, and it would cross the single bus of each column.
3. **Dissent, verbatim (Lens B):** "Draw the redo as a new child card with a derived, dashed 'redo of N' edge back to the failed attempt (same `definition.task`, earlier index, outcome fail or land fail) … the edge, not the counter, carries the lineage."
4. **Reopen** when the spawn record gains a code-assigned link on the redo (for example `redo_of`), so the edge reads a fact. Or reopen when the owner cannot tell which attempt a redo replaces from the index and the roster. The cheaper first step then is an "attempt k of max" line in the roster and tooltip, from the task count of ADR-0113 rule 10.

Guard: the unique-title and index tests in `tests/test_dashboard_groups.py` (a redo keeps its own index), and the no-link test in `tests/test_dashboard_rework.py` (point 5 of the amendment below).

## Amendment, 2026-10-04 (issue #134): how a redo reads

Owner request: make it clear that an issue that was reworked passed when it was redone. This keeps the amendment above (own card, no edge, no "2x" fold) and adds text that the dashboard derives from the record.

1. **The attempts of one task** are the children of one dispatch that share a task key: the spawn's `task`, else the issue its prompt names. An empty `task` counts as none. A child with no key has no attempts and no mark. The key is a reading of the record, not an identity ([ADR-0113](0113-a-dispatch-node-lets-an-agent-request-children-inside-an-author-fixed-envelope-and-code-validates-each-request.md) rules 4 and 11).
2. **A redo card** reads "↻ passed after N attempts" when it passed, else "attempt N" with its state. It passed when its call passed, its landing passed and its held-out command did not fail. The first attempt carries no mark.
3. **An earlier attempt** that did not pass stays failed (✗, its own cause). It reads "superseded by K · issue I, which passed" once a later attempt passed, and its card is dimmed. No card reads superseded while no later attempt passed.
4. **A collapsed group card** names the number of its children that a later attempt replaced ("route · 5 failed · 2 redone"), and a superseded square has a dashed frame. The roster and every tooltip carry the same text.
5. **No card draws a link to another card.** The text is the only lineage. This text replaces the "attempt k of max" first step of point 4 above. Reopen as before: the spawn record gains `redo_of`, or the owner cannot follow a retry.
6. **There is no separate issue-level status.** The status of an issue is the state of its latest attempt, which its card shows. "Attempt N of max" is not shown: the ceiling of [ADR-0113](0113-a-dispatch-node-lets-an-agent-request-children-inside-an-author-fixed-envelope-and-code-validates-each-request.md) rule 10 is not in the child record.

Guard: `tests/test_dashboard_rework.py` (passed after N attempts, superseded, no mark on a first pass, no supersede without a later pass, held-out failure, empty task key, the two key sources, the group count, and the no-link test: no link key on a child, and no line drawn where `rework` or `superseded_by` is read).
