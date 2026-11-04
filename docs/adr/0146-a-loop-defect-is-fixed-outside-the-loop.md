# ADR-0146: A defect in the loop is fixed outside the loop

## Status

Accepted.

## Context

The loop's own machinery — dispatch, waves, the age cap, childcc, landing — cannot deliver its own fix. A broken dispatcher cannot dispatch the change that repairs it, and the loop cannot tell a landing from a discard. A loop that keeps dispatching children onto a broken path spends money and produces work it cannot deliver, while the fix waits.

Three labels mark an issue as a defect in the loop itself: `loop-bug:dispatch` names the dispatch path,
`loop-bug:landing` names the landing path, and `loop-bug:infra` names the loop's infrastructure. A person applies any of
them, and the agent that finds an infrastructure defect applies that one. A bare `loop-bug` names a defect the loop works
beside. The label alone halts nothing, and an agent that carried on dispatching while it repaired one fed the very path it
was repairing. A display defect stopped all dispatch for hours, and that cost the owner the delivery it was meant to
protect (owner answer B1).

## Decision

1. **A defect in the dispatch path, the landing path or the loop's infrastructure is never dispatched to the loop.** A person, or the person's own agents, fixes it, gates it and lands it outside the loop, like any other change to the loop's machinery. A loop-bug that names a defect elsewhere is dispatched only after a plan review approves its current body (ADR-0171): the review is the gate that keeps the loop from building against a plan nobody read.
2. **While any open issue carries `loop-bug:dispatch`, `loop-bug:landing` or `loop-bug:infra`, the loop starts no new dispatch, autobug or review.** `autobug.sh` and `autofrontier.sh` stop before any candidate is taken. `childcc.py` enters no child. A bare `loop-bug` stops none of them. `loop-bug:infra` names a defect in the loop's infrastructure — the wave lifecycle, a gate, the engine — and it is the durable switch: the issue stays open until its fix is published, so the stop survives a restart, a crashed session and an agent that forgets.
3. **A wave whose children are all approved and gated still lands, and its cards move.** That work is done; holding it adds risk and delivers nothing. An entered child is approved and gated, so `childcc.py` exits 0 when every child of the wave entered, and `land_wave` lands it. A child not yet in its wave holds the landing (exit 3) until the wave's age cap, as it does with no stop; it is never entered under the stop.
   The `loop-bug:landing` label names the landing path and stops landing too: `land_wave` (`watch.sh`) and `branchland.sh` land nothing and print `LOOP STOP: #<n> is a loop defect in the landing path; no wave lands until it closes`. It is a `loop-bug` for every other rule here. `loop-bug:infra` holds dispatch and stops no landing.
4. **Each stopped pass prints exactly `LOOP STOP: #<n> is a loop defect; the fix lands outside the loop`, names the issue, and exits 0.** A stop is not a failure. A bare `loop-bug` prints no stop line and holds nothing.
5. **There is no marker file.** The stop derives from the open issues, so closing the issue resumes dispatch at the next pass with no other action, and nothing is left to forget.
6. **The read is cheap and bounded.** A pass that already reads the open issues reuses that read. A pass that does not reads the open issues once, through the one seam (`ghcall.sh`, `ghread.py`), never with a bare `gh api`.
7. **The stop reaches the loop log and the issue's line on the board, never only stdout.** The owner reads why the loop stopped, and the issue that holds it, without opening a terminal.
8. **A start is refused while any loop-bug issue is open.** The three halt labels stop the passes, and a bare `loop-bug` stops none of them; neither stops a start. So `watchd.sh start` reads the open issues once, through the one seam, and refuses with the issue's number and a non-zero exit while any open issue carries `loop-bug` or a label that begins with `loop-bug:`. Backlog is included: a defect the loop works beside is still a defect it must not be restarted onto before the owner or the agent has read the plan (ADR-0171). A start on an already-running loop is not a start, so it still cancels a drain and succeeds. A read that fails proves nothing and refuses nothing: the loop starts and one line on stderr says the check did not run (decision 6). Closing the issue after its publish is the clearing action, as it is for the halt labels.
9. **The agent that finds an infrastructure defect in the loop labels the issue `loop-bug:infra` and drains the loop before it builds the fix.** The label is the switch that endures: every stop reader asks it of the open issues at every pass, so a restart, a crashed session or a forgotten drain cannot lift it, and closing the issue is the whole clearing action. `watchd.sh drain` is the same-minute stop for the work already in flight: it pauses the dispatch passes at once (they read `auto/off`), lets a live run and a held gate finish so no child is killed, and ends the loop when none remains. It does not by itself stop a review or a child entry; the label does. `watchd.sh start` cancels a drain, so a restart resumes the passes — and the label still holds them. A defect in what the page shows is not an infrastructure defect, and the display defect that once stopped all dispatch cost the owner the delivery it was meant to protect (owner answer B1).

## Consequences

- Dispatch halts while a defect in the dispatch path, the landing path or the loop's infrastructure is open. Startable work waits; that wait is the cost of not feeding a broken path. A defect elsewhere in the loop is fixed beside delivery, and the loop keeps delivering.
- Approved and gated work still lands, unless the defect is in the landing path.
- The stop clears itself when the issue closes. No operator action, no stale file, no restart.
- A restart waits for the open loop-bug to be fixed and published. The order is the one the owner asks for: the loop is never relit onto a defect it cannot deliver itself.
- The loop never blocks on a fix it cannot perform, and the fix gets the review and the gates a change to the loop's machinery needs.

## Guard

`contrib/squad-goal/scripts/test-autobug.sh` and `contrib/squad-goal/scripts/test-autofrontier-one-run.sh` (a pass halts, names the issue, publishes the line, and dispatches nothing, for `loop-bug:dispatch`, `loop-bug:landing` and `loop-bug:infra`, and a bare `loop-bug` halts nothing), `contrib/squad-goal/scripts/test-childcc.sh` (no child enters and no review starts while the stop is named, the same three labels each stop the entry, a bare `loop-bug` stops nothing, a wave whose children all entered exits 0, a child out of its wave exits 3, and closing the issue resumes the entry), `contrib/squad-goal/scripts/test-watch-land.sh` (an approved wave lands under `loop-bug`, and no wave lands under `loop-bug:landing` until it closes), `contrib/squad-goal/scripts/test-branchland.sh` (no branch lands under `loop-bug:landing`), and `contrib/squad-goal/scripts/test-watchd-drain.sh` (the drain stops new work at once and lets live work finish), and `contrib/squad-goal/scripts/test-watchd-start.sh` (a start is refused and names the issue for a bare and for a prefixed `loop-bug`, a label that only resembles one stops nothing, no loop-bug starts the loop, a start on a running loop succeeds, and a failed read starts the loop with one line on stderr).
