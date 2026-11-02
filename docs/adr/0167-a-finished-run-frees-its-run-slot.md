# A finished run frees its run slot

`caps.runs` bounds the runs the machine works at once. The loop read a run as live until its issue closed, so a finished
run held a slot that new work needed. A finished run frees its slot at once; the ceiling never widens to make room.

The free slots come from one counter, `Ceiling.run_holders`: every live lease machine-wide, and every run the loop's active
list names, each run once. A run whose last verdict follows its last `run_start` or `resume` is over. That is the reading
the dashboard, `wave.sh` and `wtclean.sh` use. A verdict before a later start is history: a resumed run is live again and
keeps its slot.

- The counter is a reader. The active list keeps the finished run's line, so the janitor and `childcc` still find the run
  where they look and close its issues.
- A listed entry with no readable record holds its slot only through its launch window: its `request.txt` is younger than
  the janitor's never-started bound. Past that bound the janitor drops the entry, so a launch that never started never pins
  a slot. A listed folder that is gone holds no slot at all.
- A live lease always counts, even when the run it names is finished. The engine releases its lease before it writes the
  verdict, so a finished run normally holds none. A lingering lease is counted by both the dispatcher and the engine, and
  the dispatcher never starts a run the engine refuses at `try_take`.
- A run with a record and no final verdict keeps its slot. The heartbeat and the janitor handle a dead run and write its
  killed verdict.
- The lease read takes the ceiling lock, as `occupancy` and `try_take` do, so a lease mid-write is never read as absent.
- The engine's admission still counts raw leases (`try_take`, `_live`). This decision covers the dispatcher's side. The two
  agree on every lease, and the dispatcher also counts a listed run before its engine takes its lease, so its count stays
  behind the engine and never ahead of it.

Guard: `tests/test_machine_caps.py`.
