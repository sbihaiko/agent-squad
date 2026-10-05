# ADR-0124 — The dashboard side panel is an inspector that starts hidden; tools live in the top bar and on the canvas

## Status

Accepted, 2026-10-03. Owner request: the left panel was noisy and could not be hidden. Panel decisions under autonomy: Q1, Q2 and Q3 agreed (Codex and Fable). Q4 split, restyle in place against rewrite the shell, and the owner's left-panel focus settled it in favor of restructuring in place, because the tests pin the element ids. Amends none; extends [ADR-0117](0117-the-editor-prepares-a-run-and-never-launches-it-approval-is-a-person-s-act-outside-the-graph-and-the-page-makes-no-model-call.md).

## Context

The 360 px left column held about 20 controls and six text blocks, with no way to collapse it. On a read-only run most controls did nothing, the verdict sat below the fold, and registry and snapshot lists filled the rest. Review: `docs/ui-review/2026-10-03-dashboard-review.md`.

## Decision

1. **The side panel is an inspector.** It starts hidden, overlays the graph (the graph never shifts), and opens on a node press. A second press on the selected node, a press on empty canvas, the `[` key, or the Panel button closes it. A choice made with the button or `[` is remembered in browser storage; an automatic open is not.
2. **The verdict is in the top bar** as a chip (state and exit code; the reason is its tooltip). Run identity and the graph hash chip sit beside it.
3. **Two modes on one page.** View reads a run; Edit shows every control that changes the graph (save, export, review, add, remove, undo, redo, node fields). A page with a run opens in View; a bare page opens in Edit. The mode label is "View", never "Run", so no control reads as starting a run (ADR-0117).
4. **File actions are one File menu**; save items show in Edit mode only. The edit toolbar sits on the canvas. One delete button shows at a time.
5. **The status line is a toast.** A plain message fades; an error stays until clicked.
6. **Reference lists** (run record counts, served lists, registries) are collapsed under `details`.

## Considered options

Keep the mixed column and add a hide button: rejected, the noise stays. Two pages: rejected, it duplicates the shell and the id-pinned tests. Panel as a column that resizes the graph: rejected, opening it moved the node under the pointer.

## Consequences

The graph gets the full width by default. A node under the open panel is reachable after closing it.

## Guard

`tests/test_dashboard_chrome.py`; the help-row guard in `tests/test_dashboard_legend_help.py` covers the `[` key.
