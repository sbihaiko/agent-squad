# ADR-0146: A defect in the loop is fixed outside the loop

## Status

Accepted. The owner's decision says: "the loop-bug drain classes are written in ADR-0146 decision 10 and ADR-0171 decision 2, and no run needs to quote a comment to build them."

## Context

The loop's own machinery — dispatch, waves, the age cap, childcc, landing — cannot deliver its own fix. A broken dispatcher cannot dispatch the change that repairs it, and the loop cannot tell a landing from a discard. A loop that keeps dispatching children onto a broken path spends money and produces work it cannot deliver, while the fix waits.

An issue that carries `loop-bug`, or a label that begins with `loop-bug:`, is a defect in the loop itself. The prefix
names the path: `loop-bug:dispatch` the dispatch path, `loop-bug:landing` the landing path, `loop-bug:infra` the loop's
infrastructure. A person applies the label, and so does the agent that finds the defect.

A label that only stopped the dispatch passes was not enough. The live runs went on, the drivers stayed up, and the loop
stopped only when an operator ran `watchd.sh stop`. A loop defect stayed open for an hour beside a running loop because
no session stopped it. A bare `loop-bug` stopped nothing at all, and the loop built on beside a defect in its own
machinery. So the loop now stops itself for any open loop-bug whose class holds dispatch, and starts itself when none does.

## Decision

1. **A defect in the dispatch path, the landing path or the loop's infrastructure is never dispatched to the loop.** A person, or the person's own agents, fixes it, gates it and lands it outside the loop, like any other change to the loop's machinery. No loop-bug is dispatched, bare or prefixed: the plan review of ADR-0171 releases the hand build, never a loop run.
2. **An open loop-bug whose class holds dispatch drains the loop.** A dispatch pass that reads the open issues (`autobug.sh`, `autofrontier.sh`, through `gh_loop_stop`) asks `watchd.sh` for a drain when the class policy of decision 10 holds dispatch for one: `loop-bug`, `loop-bug:landing`, or any class the table does not name. The drain pauses dispatch at once, lets every live run and held gate finish, kills no process, and then ends the loop. One mark, `auto/loop-bug-drain`, holds the issue number; one audit line names the issue and the pass (ADR-0162), and the hub shows the loop as draining for that issue. A second request is a no-op. A read that fails, a body that is no list and a pull request ask for nothing.
   The same pass stops before any candidate is taken. With the drain that halt is redundant for a running loop; it stays as the stop that needs no supervisor. `childcc.py` reads the same map (decision 10): under a class that holds the child entry it starts no review and enters no child. Only `loop-bug:publish` leaves the child entry open, so then the work in flight reaches its review and its wave. The issue stays open until its fix is published, so the stop survives a restart, a crashed session and an agent that forgets.
3. **A plain `loop-bug` holds the landing too; a wave whose children are all approved waits, with its run unread.** The drain holds every restriction, so no approved wave lands beside an open defect. A child not yet in its wave holds the landing (exit 3) until the wave's age cap, as it does with no stop; it is never entered under the stop.
   The `loop-bug:landing` label names the landing path and stops landing too: `land_wave` (`watch.sh`) and `branchland.sh` land nothing and print `LOOP STOP: #<n> is a loop defect in the landing path; no wave lands until it closes`. A bare `loop-bug` holds the landing as it holds every restriction: `drainpolicy.py` maps each unlisted prefix, `loop-bug:infra` included, to every restriction.
4. **Each stopped pass prints exactly `LOOP STOP: #<n> is a loop defect; the fix lands outside the loop`, names the issue, and exits 0.** A stop is not a failure.
5. **The loop starts again by itself when no loop-bug is open.** A watcher in `health.sh` runs only while the drain mark exists, at most every `SG_LOOPBUG_WATCH_S` (300 s), through the one seam. An open loop-bug holds; an unknown read holds; none open runs `watchd.sh start --if-bug-drain`, which checks the mark again before it starts, removes it, and writes one audit line that names the issue. The watcher never writes the mark. A loop-bug is closed only after its fix is live, so no open loop-bug is the whole restart condition. While a token pause holds dispatch, no pass reads the issues, so no drain is asked for; dispatch is already held then.
   **A loop the owner turned off stays off.** Every owner stop and drain (`watchd.sh stop`, `stop --force`, `drain`, and the hub's two stops) writes `auto/owner-stop` and removes the drain mark, on a running loop and on a stopped one. No start but the owner's runs under that mark: the watcher's, the restart waiter's and the capacity edit's all refuse, and `health.sh` never relights. A successful owner start removes it, and a stop that fails leaves none, so the mark never stands beside a running loop. An internal stop (the capacity edit, the restart waiter) is no owner stop and writes no mark; a restart refused for an open loop-bug becomes a loop-bug drain.
6. **The read is cheap and bounded.** A pass that already reads the open issues reuses that read. A pass that does not reads the open issues once, through the one seam (`ghcall.sh`, `ghread.py`), never with a bare `gh api`.
7. **The stop reaches the loop log and the issue's line on the board, never only stdout.** The owner reads why the loop stopped, and the issue that holds it, without opening a shell.
8. **A start is refused while a loop-bug whose class holds dispatch is open.** `watchd.sh start` reads the open issues once, through the one seam, and refuses with the issue's number and a non-zero exit while the class policy of decision 10 holds dispatch for an open issue. Backlog is included. A start on an already-running loop is not a start, so it still cancels an owner drain and succeeds; under the loop-bug drain mark it refuses while the issue is open. A read that fails proves nothing: with no drain mark the loop starts and one line on stderr says the check did not run (decision 6); with the mark the start refuses and the mark stays. Closing the issue after its publish is the clearing action.
9. **The agent that finds a defect in the loop labels the issue and builds nothing in the loop.** The label is the switch: the next pass drains the loop, and closing the issue after the publish starts it. `watchd.sh drain` by hand is the owner's stop and stays off until the owner starts it. A defect in what the page shows is not a loop defect and takes `bug`: a display defect that once stopped all dispatch cost the owner the delivery it was meant to protect (owner answer B1).
10. **The label names the class, and the class names the restrictions it holds.** `drainpolicy.py` is the one map, and every caller reads it: a dispatch pass, the child entry, the landing and the publish. The table below is that map. A class stops what its defect can corrupt and leaves the rest of the loop working, so one broken path costs the delivery of that path and no other. `loop-bug:publish` holds the publish alone, because a publish that carries a broken copy is the defect itself and the copy a later publish reads is the one that stands.

    | label | dispatch | child entry | landing | publish |
    |---|---|---|---|---|
    | `loop-bug` | held | held | held | held |
    | `loop-bug:landing` | held | held | held | open |
    | `loop-bug:publish` | open | open | open | held |
    | any other `loop-bug:<class>` | held | held | held | held |

    The table is the rule for each class. A bare `loop-bug` holds the four restrictions. `loop-bug:landing` holds dispatch, the child entry and the landing, and leaves the publish open. `loop-bug:publish` holds the publish alone, and leaves dispatch, the child entry and the landing open. Any other class holds the four. Several open issues combine as the union of their restrictions. A class the table does not name takes the full drain: a new class is safe before its entry is written, never after. The publish restriction reads the issue's `reviewed-sha:` lines and refuses a sha the review did not cover.

    The owner set this rule: "A class the table does not name takes the full drain. The publish restriction refuses a sha the issue's reviewed-sha: lines do not name."

## Consequences

- The loop is off while a loop-bug whose class holds dispatch is open. Startable work waits; that wait is the cost of not feeding a broken path. A `loop-bug:publish` leaves the loop running and holds only the publish. So the label is kept for defects in the loop's machinery, and a label left on a lesser issue stops delivery.
- Live runs and held gates finish under the drain. A wave that is approved and gated lands while the drain waits; after the loop ends, nothing lands until the restart.
- The loop restarts with no person in the session when the last loop-bug closes, unless the owner turned it off.
- The restart needs the health agent: with no watcher, the loop stays off until a start by hand.
- The loop never blocks on a fix it cannot perform, and the fix gets the review and the gates a change to the loop's machinery needs.

## Guard

- `contrib/squad-goal/scripts/test-watchd-drain.sh`: the label rule (bare, prefixed, lookalike, pull request, empty body, object body); a loop-bug drain writes the mark and one audit line and kills no run; a second request is a no-op; every owner stop and drain leaves the owner mark and removes the drain mark; the watcher start refuses under the owner mark, with an open issue and on an unknown read, and starts the loop when none is open; a stop that fails leaves no owner mark.
- `contrib/squad-goal/scripts/test-health.sh`: with the real `watchd.sh`, the loop restarts when the issue closes and stays off after an owner stop.
- `contrib/squad-goal/scripts/test-watchd-restart.sh`: a restart refused for a loop-bug becomes a loop-bug drain; a failed halt leaves no owner mark.
- `contrib/squad-goal/scripts/test-autobug.sh`, `contrib/squad-goal/scripts/test-autofrontier-one-run.sh`: a pass halts, names the issue, asks for the drain and dispatches nothing, for a bare and a prefixed loop-bug; a lookalike label stops nothing.
- `contrib/squad-goal/scripts/test-childcc.sh`: no child enters and no review starts under a prefixed label, and a bare `loop-bug` stops no entry; a wave whose children all entered exits 0; closing the issue resumes the entry.
- `contrib/squad-goal/scripts/test-watch-land.sh`, `contrib/squad-goal/scripts/test-branchland.sh`: nothing lands under a bare `loop-bug` or under `loop-bug:landing`.
- `contrib/squad-goal/scripts/test-cap-edit.sh`: a capacity edit stops and starts with the internal verbs.
- `contrib/squad-goal/scripts/test-watchd-start.sh`: a start is refused and names the issue; a failed read with no drain mark starts the loop.
- `tests/test_hub_loop_switch_page.py`: the hub names the issue beside the state, and a refused start lifts no pause.
