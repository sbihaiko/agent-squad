# Loop monitor

The mandate for a session that watches the loop until the board's Todo column is empty.
The owner starts it with `/goal /autonomy` and this document as the task; the goal hook keeps it running across turns.

Monitor continuously until every Todo item is implemented and landed. Watch for stalls, slowness, CPU, kanban sync, token
spend and wave age.

## Priority rule: a defect in the loop is not the loop's work

Classify the problem before any other action. If it sits in the loop's own machinery — `dispatch`, `autobug`,
`autofrontier`, `childcc`, `wave.sh`, the age cap, `landing`, the review chain or `janitor` — it is never delegated to the
loop.

1. Stop the loop and keep dispatch off.
2. Open an issue with the evidence, apply the label `loop-bug`, and move its card to Doing.
3. Fix it yourself or with your own agents, test-first.
4. Run the fast gate and push the fix to `dev`.
5. Validate the fix, then close the issue.

While any `loop-bug` issue is open, the loop stays stopped: no dispatch. Close the issue only after the fix is validated;
dispatch may restart only when no `loop-bug` issue is open.

A product defect — repository code outside the loop's machinery — may go to the loop as usual.

## On any problem

1. Stop the loop and reproduce the defect.
2. Open an issue with verifiable evidence: the run, the logs, the markers.
3. Classify it, and follow the priority rule when it applies.
4. Make sure the fix is implemented, validated and pushed to `dev`.
5. Update the loop state and the kanban, and remove every worktree you can remove safely.
6. Review the whole state, and when no `loop-bug` issue is open, restart the loop and the monitoring.

## The same problem three times

When one problem repeats three times or more, stop before another hand fix. Summarize the observed facts, never a
hypothesis as a conclusion, and ask GPT-6-Astra or Grok 4.7 for an analysis. When neither answers, ask the owner.

The cycle ends only when every Todo item is implemented and landed, with no pending work that blocks the conclusion.
