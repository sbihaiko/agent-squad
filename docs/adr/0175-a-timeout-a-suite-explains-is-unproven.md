# A timeout that a suite of the same tree explains is unproven

An acceptance command runs under a cap. When the cap ends it, the command measured nothing, so the timeout is unproven
([ADR-0164](0164-delivery-outranks-the-proof.md)). A second timeout of the same tree and command is a measured red, so a
hang the change caused cannot pass by omission. That rule assumes the cap is long enough for the command. A suite the
command starts can need the whole cap on its own. Then the second timeout says the cap is short, not that the change hangs.

A command that holds gate slots must not take them twice. The runner takes the slots before the cap clock starts. A suite
the command starts then takes one more slot inside the clock, and that wait spends the cap. A wait for a gate slot is no
run time ([ADR-0166](0166-a-run-past-thirty-minutes-is-taken-over.md)).

## Decision

1. The runner names the slots it holds to the command: `SG_GATE_SLOTS_HELD` carries the count. The runner sets the
   variable only when it holds slots. A suite that `child-gate.sh` starts under the variable takes no slot, and its
   history row records a wait of zero. Without the variable the suite takes its own slot, as before.
2. The cap clock does not stop. No file, process or variable the command writes moves its deadline.
3. Each row of `child-suite-history` ends with the tree sha. The writer and the reader use one path,
   `${SG_STATE:-/tmp/sg-state}/child-suite-history`.
4. On a timeout where the (tree, command) marker exists, the runner reads the rows whose tree sha and suite path match
   the command. A row whose run time, finish minus start, is equal to or longer than the cap makes the timeout unproven,
   on every attempt. Otherwise the second timeout is a measured red. No factor and no slack apply.

## Consequences

- A suite that needs the whole cap keeps the timeouts of its command unproven. The record names the cap as the cause, and
  the cap is the owner's to raise.
- A suite the runner starts without slots, as when no budget exists or the bounded wait has expired, still takes its slot
  inside the cap clock. That wait still spends the cap.
- The history file sits in the state directory, which a command can write. A command can therefore write a row of its own
  that names a long suite. The tree and suite match narrows that claim but does not stop it.

Guard: `tests/test_workcmd.py`, `contrib/squad-goal/scripts/test-child-gate.sh`.
