# ADR-0146: A defect in the loop is fixed outside the loop

## Status

Accepted.

## Context

The loop's own machinery — dispatch, waves, the age cap, childcc, landing — cannot deliver its own fix. A broken dispatcher cannot dispatch the change that repairs it, and the loop cannot tell a landing from a discard. A loop that keeps dispatching children onto a broken path spends money and produces work it cannot deliver, while the fix waits.

The label `loop-bug` marks an issue as a defect in the loop itself. Only a person applies it. The loop never does.

## Decision

1. **A defect in the loop is never dispatched to the loop.** A person, or the person's own agents, fixes it, gates it and lands it outside the loop, like any other change to the loop's machinery.
2. **While any open issue carries `loop-bug`, the loop starts no new dispatch, autobug or review.** `autobug.sh` and `autofrontier.sh` stop before any candidate is taken. `childcc.py` enters no child.
3. **A wave whose children are all approved and gated still lands, and its cards move.** That work is done; holding it adds risk and delivers nothing. An entered child is approved and gated, so `childcc.py` exits 0 when every child of the wave entered, and `land_wave` lands it. A child not yet in its wave holds the landing (exit 3) until the wave's age cap, as it does with no stop; it is never entered under the stop.
   A `loop-bug` names the landing path when the issue also carries, or carries instead, the label `loop-bug:landing`. That label stops landing too: `land_wave` (`watch.sh`) and `branchland.sh` land nothing and print `LOOP STOP: #<n> is a loop defect in the landing path; no wave lands until it closes`. It is a `loop-bug` for every other rule here.
4. **Each stopped pass prints exactly `LOOP STOP: #<n> is a loop defect; the fix lands outside the loop`, names the issue, and exits 0.** A stop is not a failure.
5. **There is no marker file.** The stop derives from the open issues, so closing the issue resumes dispatch at the next pass with no other action, and nothing is left to forget.
6. **The read is cheap and bounded.** A pass that already reads the open issues reuses that read. A pass that does not reads the open issues once, through the one seam (`ghcall.sh`, `ghread.py`), never with a bare `gh api`.
7. **The stop reaches the loop log and the issue's line on the board, never only stdout.** The owner reads why the loop stopped, and the issue that holds it, without opening a terminal.

## Consequences

- Dispatch halts while a loop defect is open. Startable work waits; that wait is the cost of not feeding a broken path.
- Approved and gated work still lands, unless the defect is in the landing path.
- The stop clears itself when the issue closes. No operator action, no stale file, no restart.
- The loop never blocks on a fix it cannot perform, and the fix gets the review and the gates a change to the loop's machinery needs.

## Guard

`contrib/squad-goal/scripts/test-autobug.sh` and `contrib/squad-goal/scripts/test-autofrontier-one-run.sh` (a pass halts, names the issue, publishes the line, and dispatches nothing, for `loop-bug` and `loop-bug:landing`), `contrib/squad-goal/scripts/test-childcc.sh` (no child enters and no review starts while the stop is named, a wave whose children all entered exits 0, a child out of its wave exits 3, and closing the issue resumes the entry), `contrib/squad-goal/scripts/test-watch-land.sh` (an approved wave lands under `loop-bug`, and no wave lands under `loop-bug:landing` until it closes), and `contrib/squad-goal/scripts/test-branchland.sh` (no branch lands under `loop-bug:landing`).
