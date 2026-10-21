# ADR-0146: A defect in the loop is fixed outside the loop

## Status

Accepted.

## Context

The loop's own machinery — dispatch, waves, the age cap, childcc, landing — cannot deliver its own fix. A broken dispatcher cannot dispatch the change that repairs it, and the loop cannot tell a landing from a discard. A loop that keeps dispatching children onto a broken path spends money and produces work it cannot deliver, while the fix waits.

The label `loop-bug` marks an issue as a defect in the loop itself. Only a person applies it. The loop never does.

## Decision

1. **A defect in the loop is never dispatched to the loop.** A person, or the person's own agents, fixes it, gates it and lands it outside the loop, like any other change to the loop's machinery.
2. **While any open issue carries `loop-bug`, the loop starts no new dispatch, autobug or review.** `autobug.sh` and `autofrontier.sh` stop before any candidate is taken. `childcc.py` enters no child.
3. **A wave whose children are all approved and gated still lands, and its cards move.** That work is done; holding it adds risk and delivers nothing. A `loop-bug` that names the landing path stops landing too.
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

`contrib/squad-goal/scripts/test-autobug.sh` and `contrib/squad-goal/scripts/test-autofrontier-one-run.sh` (a pass halts, names the issue, publishes the line, and dispatches nothing), and `contrib/squad-goal/scripts/test-childcc.sh` (no child enters while the stop is named, and closing the issue resumes the entry). The landing of an approved wave under a `loop-bug`, and the stop when the defect names the landing path, have no guard yet; that part is a draft.
