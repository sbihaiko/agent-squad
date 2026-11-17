# The landing's gate is first in the queue

A run ends with a delivery: the wave's landing pushes the tip and closes the cards. The landing's own gate — the tip
check — competes for the same machine-wide gate lock as every child's fast gate, and a wave finishes its children
together, so the landing arrives exactly when the queue is longest. The gate lock has no order: a waiter is whoever
calls `mkdir` first after the holder leaves, and a child that retries every second wins that race more often than a
landing that asked once. So the landing waits behind the work its own delivery produced.

The lock gives the queue an order. A waiter that carries `SG_SERIAL_PRIORITY=1` says so in its queue marker, and a
waiter that carries none stands aside for that round while a live priority waiter is queued for the same lock. The
landing's gate is the only priority waiter: it is the delivery, and delivery outranks the proof (ADR-0164). A child
stands aside; it does not leave. It keeps its own wait budget, and it records the same marker. A try-mode gate answers
73 at once instead of running a battery ahead of a queued landing, which is what try mode already means for a held lock.

A priority marker names nobody once its pid is gone or its marker is older than `SERIAL_PRIORITY_MAX`, because a killed
waiter leaves its marker behind and a landing that is no longer waiting must never hold the queue. A marker whose
reading cannot be trusted is the same as no marker: the queue never stalls on it.

The priority orders the queue; it never widens a bound. A landing still gives up at its own `SERIAL_WAIT_SECONDS`, and
the wait it spends queued is gate wait (ADR-0166): the gate starts its clock when it holds the lock and its slots, so
no run time is charged for a queue it does not control.

Guard: `contrib/squad-goal/scripts/test-serial.sh` proves the order by state, never by timing: two waiters queue behind
one holder, the holder is released only after both markers exist, and the waiter that runs first is the one the rule
chose.

## The child's slot

A child's gate used to take the whole budget. `env.sh` exports `SG_GATE_WORKERS` for every process the loop starts, and
the child's own count was written as a default, `${SG_GATE_WORKERS:-2}`, which never applies to a name the environment
already set. So every gate asked for six, the ceiling lowered it to the machine's budget, and two gates never overlapped.
The queue the landing competes in was therefore one gate long, and a run could wait at its own bound for a budget that
never freed.

One slot per child makes the ceiling mean what it says: three children gate inside the same three-slot budget, and the
worker total does not rise, because the ceiling is the machine's budget and it is not raised. The count never rides on
the inherited name; a caller that wants another count names `SG_GATE_WORKERS_CHILD`. The landing is not starved by the
rotation: while its gate waits, the children stand aside at the lock, and a suite a child starts takes one slot through
`child-gate.sh` with no lock at all. So the waiting tip check or landing check writes a priority waiting marker in the
ceiling's `waiting/` folder, which names it as the next holder, and no other holder takes a gate slot while that
marker's pid lives (`squad/caps.py`). The freed slots drain to it; a dead waiter names nobody and never blocks.

Guard: `tests/test_caps_partial.py` and `contrib/squad-goal/scripts/test-gate-lock.sh` hold every slot, start a tip
check, and start a fourth child gate: the tip check takes the freed slots, and the child gate waits until it gives
them back.

Guard: `contrib/squad-goal/scripts/test-fast-gate.sh` runs a child gate in the loop's own environment (the inherited
count already set) and reads the count that reached the lease, never a stub that accepts any argument.

## The scope decides the count

One slot is the right count for a delta and the wrong one for a full battery. The selector answers `all` for a third of
the gates — a change to a test helper, a shared module, an uncommitted tree — and a battery of three thousand tests at
one worker passes the proof's own bound (ADR-0163). The gate then ends unproven, which is a gate that measured nothing
(ADR-0018), and the run spends its attempt on the machine rather than on the tree.

So the scope decides: a delta takes one slot, and a full battery takes the whole budget. The selector is asked once
before the slot take, so the count is known when the lease is written, and the battery asks it again over the same tree
and answers the same. A caller that wants another count names `SG_GATE_WORKERS_CHILD_ALL`. The worker total never passes
the ceiling: three one-slot gates or one full-battery gate are the same budget.

A full battery needs every slot, and a run of one-slot gates is always leaving one free. So this gate waits for its
slots while it holds the gate lock, bounded by `SG_GATE_SLOT_WAIT_SECONDS`. No new gate takes the lock while it waits,
and the gates already running end, so the slots drain to it, unless a suite a child starts takes a freed slot first:
only a tip check or a landing check keeps the freed slots for itself. The wait is
gate wait, never run time (ADR-0166), because the gate's clock starts in `--run`, once the slots are held. Every other
gate keeps the one-try take and the 73 it answers.

Guard: `contrib/squad-goal/scripts/test-fast-gate.sh` runs a delta child, an `all` child and an entry gate through a stub
`gate.sh`, and reads the count and the slot-wait flag that reached the lock.
