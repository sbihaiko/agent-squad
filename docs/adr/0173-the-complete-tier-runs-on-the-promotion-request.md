# The complete tier runs on the promotion request

The dev-to-stg pull request carries both CI tiers, and the promotion reads them as its proof. The fast tier proves a push
cheaply; the complete tier proves the full suite. Running the complete tier on every push spends its minutes again on every
landing, while the landing is proven once, on the tip the promotion carries.

- The fast tier runs on every event of the pull request: the three jobs `ruff`, `mypy` and `fast-battery`. A push starts
  those alone; no complete-tier job starts.
- The complete tier runs on the `promote` label alone. The label is the promotion request, and the promotion pass applies
  it once per tip.
- A guard job starts the tier once per tip. It reads the tip's own check runs and answers whether a complete-tier run
  already exists; a second label event on the same tip starts nothing. The guard reads the tier's job names from the
  workflow itself, so the names have one source.
- A check run another event skipped is no evidence. A push reports the complete tier as `skipped`, and the labeled run
  reports the fast tier that way, so both readers take, for each job name, the newest completed run that is not skipped.
- The proof keeps its shape: a green complete tier on the promoted tip is still the proof, and only its trigger changed.
  `promote-stg.sh` reads the complete tier's check runs on the tip it promotes and refuses a tip with no green run of it,
  naming the tier and the jobs it misses. A green fast tier alone is half the proof.
- The tier's job names are read from the workflow by one structural reader, never kept as a list in a script. A workflow
  that names no complete-tier job requires none, and the tooling guard refuses a workflow that stops starting the tier on
  the label.

Guard: `tests/test_tooling.py`, `contrib/squad-goal/scripts/test-promote.sh`, `contrib/squad-goal/scripts/test-promote-pass.sh`.
