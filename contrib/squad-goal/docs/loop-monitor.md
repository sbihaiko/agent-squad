# Loop monitor

The mandate for a session that watches the loop until the board's Todo column is empty.
The owner starts it with `/goal /autonomy` and this document as the task; the goal hook keeps it running across turns.

Monitor continuously until every Todo item is implemented, landed, promoted into `stg` and published.
Watch parallelism, delivery speed, runs against the cards in Doing, CPU, kanban sync, token spend and wave age — the
20-minute check of `SKILL.md` §10 lists each number and where it is read.

## Priority rule: a defect in the loop is not the loop's work

Classify the problem before any other action. If it sits in the loop's own machinery — `dispatch`, `autobug`,
`autofrontier`, `childcc`, `wave.sh`, the age cap, `landing`, the review chain, `janitor`, the hub or the live copy — it is
never delegated to the loop (ADR-0146).

1. Open an issue with the evidence: the run, the log lines, the markers, the timestamps. Label it `loop-bug` plus the one label that names the defect: `loop-bug:dispatch` for the dispatch path, `loop-bug:landing` for the landing path, `loop-bug:infra` for the loop's infrastructure — the wave lifecycle, a gate, the engine. The second label is what stops dispatch; a bare `loop-bug` stops nothing. Card to Doing.
2. Run the adversarial review with Fable before the first edit (Agent tool, model `fable`, stance challenger). It must name what else explains the evidence and which test separates the two. Record the ruling on the issue; a fix it refutes is not started.
3. Stop the loop: `scripts/watchd.sh stop`, after checking for live work. It drains: a live run finishes before the loop stops, and `stop --force` is the hard stop that snapshots the live runs' work first (#1662). Land first any finished run whose children entered its wave (`wave.sh land <run>`).
4. Fix it yourself or with your own agents, test-first, in a worktree. Fast gate, not the full one. Land in `dev` with `Closes #n`.
5. Promote `dev` into `stg` by one pull request, then publish the live copy from a tree on `stg` (`SKILL.md` §8 steps 4 and 5). The publish closes the issue — never close it by hand.
6. Restart the loop (`scripts/watchd.sh restart`, which drains the live runs first; with the loop already stopped by step 3 it only starts it), and confirm the first cycle prints no `LOOP STOP` and no `DISPATCH HOLD` for that issue.

While an issue carrying `loop-bug:dispatch`, `loop-bug:landing` or `loop-bug:infra` is open, the loop stays stopped: no
dispatch. A bare `loop-bug` label stops nothing; it does not stop landings either, and no label stops the live runs.

A product defect — repository code outside the loop's machinery — may go to the loop as usual, and the goal protection
says to file it at once with the `bug` label and its card in Todo.

## A trap is not a slow pass

A trap is a line the loop prints unchanged for three cycles while no fact moves: the same `WATCH: <run> waits`,
`BRANCHLAND WAIT`, `DISPATCH HOLD` or `LOOP STOP` naming the same subject, a `CCDEFER` or `dwait` counter that only grows,
or a run whose `record.jsonl` mtime never moves. On a trap: file it, stop the loop, unwind what holds it — the marker in
`auto/`, the lock in `$SG_STATE/locks`, the lease in `~/.cache/agent-squad/caps` — and requeue the issue with
`wave.sh requeue <issue> <why>`. Name every marker you delete on the issue.

## On any problem

1. Stop the loop and reproduce the defect.
2. Open an issue with verifiable evidence: the run, the logs, the markers.
3. Classify it, and follow the priority rule when it applies.
4. Make sure the fix is implemented, validated and landed in `dev`.
5. Update the loop state and the kanban, and remove every worktree you can remove safely.
6. Review the whole state, and when no `loop-bug` issue is open, restart the loop and the monitoring.

## The same problem three times

When one problem repeats three times or more, stop before another hand fix. Summarize the observed facts, never a
hypothesis as a conclusion, and put them to the Fable adversarial review before the next edit. When the review cannot
settle it, ask the owner.

The cycle ends only when every Todo item is implemented and landed, with no pending work that blocks the conclusion.
