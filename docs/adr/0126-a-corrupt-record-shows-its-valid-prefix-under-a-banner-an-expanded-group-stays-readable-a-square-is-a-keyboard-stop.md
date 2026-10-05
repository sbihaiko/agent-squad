# ADR-0126 — A corrupt record shows its valid prefix under a banner; an expanded group stays readable; a square is a keyboard stop

## Status

Accepted, 2026-10-03. Extends [ADR-0125](0125-the-dashboard-groups-the-children-of-one-dispatch-by-status-into-group-cards.md). Closes the gaps the owner named after the group-card review.

## Context

A record with one bad line mid-file gave a blank page and one toast. A fan-out of 150 children, once expanded, fitted at 23% and no text could be read. A square on a group card could be clicked but not reached by keyboard.

## Decision

1. **A bad line ends the record there.** The page shows the events before the first line that is not a JSON object, and a persistent banner (`#recbad`, `role=alert`) that names the line number, the parse message, and how many events show. The bad line and every line after it are never shown, and the page never reads past it: events after a gap would paint a wrong run (ADR-0012). The same rule holds for a live reread. A half-written last line (no newline yet) stays dropped silently and shows no banner, as before (ADR-0122); the banner shows only once that line is complete and still not JSON.
2. **With no valid prefix, the page still refuses.** Line 1 bad, or a first event that is not `run_start`, shows no run and says why in the toast. There is nothing true to show.
3. **A double-press that expands a group fits no smaller than 70%.** When the whole graph would fit below that, the view stays at 70% with the dispatch and the first row of cards at the top-left, and the toast says so. The Fit button still fits everything. The fit is off after such an expand (a live update does not shrink it), and folding the group fits again.
4. **Every square on a group card is a keyboard stop** (`tabindex=0`, `role=button`, a label of the child title and outcome). Enter or Space selects that child and the focus stays on its square. The focus ring is an outer white ring, so a focused selected square differs from a selected one.

## Considered options

Skip the bad line and read on: rejected, it hides a gap. Show nothing: rejected, it is the defect. Paginate or wrap a large group in rows: not needed, a floor on the zoom keeps the cards readable and a pan reaches the rest. A roving tabindex on squares: later, if 150 tab stops hurt.

## Consequences

The first bad line hides later good lines; the banner says so. A live run whose record has a bad line stays on the prefix until the record is fixed. 150 squares are 150 tab stops.

## Guard

`tests/test_dashboard_record.py` (prefix and bad line, no valid prefix), `tests/test_dashboard_view.py` (the zoom floor), `tests/test_dashboard_groups_page.py` (the banner, the live reread: a half-written line shows none and a completed bad line shows it, the square stops and ring, 150 children expanded, zero children, all failed).
