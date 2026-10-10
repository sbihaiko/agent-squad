# ADR-0124: The dashboard side panel is an inspector that starts hidden; dispatch children group by status; a corrupt record shows its valid prefix

## Status

Accepted. Extends ADR-0117.

## Context

A 360 px left column held about 20 controls and could not collapse. A dispatch can spawn 26 or 150 children, and one card each fills the page. A count threshold hid a failed child among passed ones.

## Decision

### Chrome

1. **The side panel is an inspector.** It starts hidden, overlays the graph (the graph never shifts), and opens on a node press. A second press on the selected node, a press on empty canvas, the `[` key, or the Panel button closes it. A manual choice is remembered in browser storage; an automatic open is not.
2. **The verdict is a chip in the top bar** (state and exit code; the reason is its tooltip), beside the run identity and the graph hash.
3. **Two modes on one page.** View reads a run; Edit shows every control that changes the graph. A page with a run opens in View; a bare page opens in Edit. The label is "View", never "Run", so no control reads as starting a run (ADR-0117).
4. **File actions are one File menu**; the edit toolbar sits on the canvas. The status line is a toast: a plain message fades, an error stays until clicked. Reference lists are collapsed under `details`.

### Group cards

5. **Children of one dispatch group by status, with no count threshold.** Done children with the same outcome form one group card. Children in flight with the same harness (model, effort, skill) form one group card. A group needs at least 3 children; a smaller set stays as single cards. A queued child and a rejected request never group.
6. **A group card shows** its outcome and count, a summary line (time, cost, tokens; a missing figure reads as a dash), and one square per child. Fill and glyph both show the outcome, so color is never the only cue. A click on a square selects that child.
7. **A double-press on a group card expands it in place; a double-press on any card of an expanded group folds it.** The flag lives in page state, not in the record, keyed by a JSON array so a `:` in a name cannot merge two groups. An expanded fit never goes below 70%, and the toast says so.
8. **Every card and every square is a keyboard stop.** Enter selects. Space opens or folds a group.
9. **Every node and child card carries a state glyph.** One table gives the glyph, class, and legend row of each state. "No verdict yet" has its own color, apart from deadline, person-needed, or stalled. Nothing animates.
10. **Child cards clip their text** with the full text in the tooltip. A child title carries its index among its dispatch's children.
11. **A selection pans into view** when the open panel would cover it. The zoom never changes. The panel shows a roster for a group or a child.

### Redo

12. **A redo is its own card.** The engine records each attempt as its own node (ADR-0102). A fold into a "2x" count would hide a failure inside a pass.
13. **Attempts of one task** are children of one dispatch that share a task key: the spawn's `task`, else the issue its prompt names. The key is a reading of the record, not an identity. A child with no key has no mark.
14. **A redo that passed reads "passed after N attempts".** The first attempt carries no mark. An earlier attempt that failed stays failed, reads "superseded by K" once a later attempt passed, and is dimmed. A group card counts the children a later attempt replaced.
15. **No card draws a link to another card.** The record has no field that names which attempt a redo replaces. Reopen when the spawn record gains one.

### Corrupt record

16. **A bad line ends the record there.** The page shows the events before the first line that is not a JSON object, under a persistent alert banner with the line number, the parse message, and the count shown. It never reads past the line, because events after a gap paint a wrong run (ADR-0012). A half-written last line stays dropped with no banner.
17. **With no valid prefix, the page refuses** and says why. Line 1 bad, or a first event that is not `run_start`, has nothing true to show.

## Guard

`tests/test_dashboard_chrome.py`, `tests/test_dashboard_legend_help.py`, `tests/test_dashboard_groups.py`, `tests/test_dashboard_groups_page.py`, `tests/test_dashboard_rework.py`, `tests/test_dashboard_record.py`, `tests/test_dashboard_view.py`.
