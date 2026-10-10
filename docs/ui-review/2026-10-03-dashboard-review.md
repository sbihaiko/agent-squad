# Dashboard UI review (2026-10-03)

Run reviewed: run 51 (done, `needs-human`), plus the empty state. Screenshots in `/tmp/ui-review/` (not committed).

## Problems

1. **Reading and editing share one column.** The 360px left column holds 14 editor buttons (Open graph, Save layout, Add node, Undo...) above the run state. On a read-only run, most are dead or irrelevant, and the run verdict sits below the fold.
2. **The verdict is buried.** `needs-human` and its reason are a long monospace paragraph, mid-column, at 13px. The first question ("did it pass, what do I do?") has no headline.
3. **Weak hierarchy.** One size (13px), one weight, mono for all data. The tool title, section labels and values look the same. Card text is about 8px and unreadable at 75% zoom.
4. **Error text overruns the graph.** Failure messages on `main`, `waves` and `route` print as a red line across neighboring cards.
5. **Template defaults.** Near-black slate with one blue accent, all-caps tracked section labels (GRAPH, RUN, START), identical rounded rectangles for every node kind.
6. **Status encoded by color alone.** Green, amber, red outlines with no shape or icon. The legend takes two rows.
7. **Empty state is bare.** A blank canvas with a small text block; "Reconnect subject-21-..." is a raw folder name.
8. **Redundant chrome.** "Download save / hash e93c..." and "record e93c... matches" repeat the hash twice; "Rereading the record every 1 s" is shown while a server pushes.

## Constraints (do not break)

- Read-only on a run. Follow by push.
- Dashboard tests: `tests/test_dashboard*.py`, `test_runs.py`, `test_dashboard_server.py`.

## Running and failed states (after the panel and group-card change)

Reviewed: run 51 at replay events 8 and 40 (running), run 46 (failed, exit 1) with the panel hidden and with `ac-verify` selected. Screenshots in `/tmp/ui-review/state-running.png`, `state-running-early.png`, `state-failed.png`, `state-failed-selected.png` (not committed).

1. **Running has no live signal.** At event 40 the pill reads "replay - no verdict yet" in amber, the same amber as "deadline hit, or a person is needed" (`page.css:100`, `.state-none` and `.state-stalled` share `--warn`). A run that is progressing looks like a run that needs a person. Cause: `page/chrome.js:31` maps both "running" (`v-live`) and "no verdict yet" (`v-wait`) apart, but a replay snapshot always yields "no verdict yet" (`core/stats.js:68-72`).
2. **The in-progress node is the weakest card.** At event 8 `issue 11` ("call started, no outcome yet") is a dashed blue outline that is nearly the page background; at event 40 `issue 4` shows only `- . - . -` placeholders. The thing to watch is the quietest thing on screen. No "Running now" teal appears in either frame.
3. **Failed state is one color and one weight.** Red fill and red stroke on `main`, `ac-verify`, 3 issue cards (`page.css:93`). The failing reason (`ac_failed x2`, `adapter_error`) is 10px red on dark red (`page.css:84,87`): low contrast and the only text that says why.
4. **Failed headline lacks the reason.** The pill says "failed - exit 1". The cause (`ac_failed`, 2 gates, plus 2 `adapter_error`, 1 `merge_conflict`) is only visible by finding red cards or opening the panel. No count, no "first failure" jump.
5. **Panel title is wrong for the selection.** Selecting `ac-verify` opens "NODE FINAL_GATE (GATE)", and the same panel prints a green "no error on this node" under a "Why it failed" block. Contradiction on the one screen meant to explain failure (`page/panel.js:142-145`; the green message ignores the child/gate reasons above it).
6. **Panel opens over the graph and hides the selection.** The 360px inspector covers the left of the canvas, so the selected `ac-verify` and `main` are behind it (`state-failed-selected.png`). No pan or shift on open.
7. **Traceback evidence starts mid-line and is clipped.** The `.runbox` blocks begin at "ue)" and "ever_by_the_discount..." and cut off at the box edge; two boxes have inner scrolling inside an outer scrolling panel (`page.css:98`). The assertion line, the useful part, is not first.
8. **Cards still overflow.** On the failed run the `issue 2` card prints "landed" / "not landed" past its right edge, and `ask-matt` prints a clipped "x9" at the edge; 50+ edges fan through the cards at the `issue` column. Card text is 10-11px (`page.css:81-87`).
9. **Replay bar and toast cover content.** The bar sits over the bottom cards (`issue 8`, `issue 23` are half hidden at 80% zoom), and the "Opened run ... Rereading the record every 1 s" toast (`page.css:152`, `page/run.js:21`) stays after load, overlapping the legend and, with the panel open, the bar. It repeats the full folder name already known from the top bar.
10. **Legend carries the whole status code.** Eight swatches in two rows at 11px are the only key to fill-and-stroke color; "Passed / Failed" differ by hue only, no icon or text mark on the card itself (the earlier problem 6 stands).

## Final state (after the corrupt-record banner)

Reviewed with ego-browser on a 120-child synthetic run at 800, 1024 and 1440 px wide, and on a real run of six parallel haiku calls, live and in replay. Screenshots in `/tmp/ui-review/` (not committed).

- **120 children, expanded.** The view stays at 70%. Card text is 12px at that zoom and readable; 28, 42 and 56 cards show at 800, 1024 and 1440 px, and a pan reaches the rest. The first row starts at the dispatch. No change needed.
- **Six running children, live.** One group card, "route - 6 running", six running squares; it turns into "6 failed" with six failed squares when the calls end, and a double-press expands it to six cards. In replay a call that only started reads "started" on its square while the card still says "running" (a started call counts as in flight).
- **Fixes from the review.** The group card says "of calls" after the time, because the children run side by side and the sum can exceed the run's time. At 900px or less the toast sits above the replay bar, not over its buttons. The failure line of a card sits one pixel higher, inside the card edge.
- **Corrupt record while live.** A half-written last line shows no banner; a completed bad line plus later lines shows the banner and keeps the valid prefix (`tests/test_dashboard_groups_page.py`).
- **Closed.** A plain `squad run` now writes `graph.json` into its run folder, so the page opens with nodes.
