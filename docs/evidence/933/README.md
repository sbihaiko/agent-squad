# SPEC 933: the ladder baseline window

This is the baseline window for the run-slot step (#938). It is a record of the state before any cap change, not a recommendation.

## Window

`2026-10-31 12:00:00` to `2026-10-31 16:00:00` UTC (09:00 to 13:00 local time), four hours.

## Caps in force

Every run that lived in the window recorded the same caps at `run_start`: runs 6, children 20, gates 12, reviews 4. No cap changed in the window.

## Settings hashes

- `config/agent-defaults.yaml`: `ada6c1e9a57b9314` (all runs).
- `docs/squad/defaults.yaml`: `ed16a0e68d9e` (one hash; no FLAG line). That file is commit `a01776c6`: `caps.runs: 6`, `limits.max_budget_usd: 4`.

The project file changed later, at 19:10 UTC (`70d20b88`, `max_budget_usd` 4 to 30, hash `3f9e983427a3`). That change is after the window and does not touch a cap.

## How it was read

`tune-report.sh` over the live loop state, read only: `SG_STATE=~/.cache/squad-goal/agent-squad`. The output is in `baseline-report.txt`.

`SG_CHECKOUT` was a scratch folder of links to every `runs/run-*` folder found under the checkout and its `worktrees/`. The loop writes its run records in the run worktrees, not in `<checkout>/runs`. With `SG_CHECKOUT` set to the checkout, the report finds no run: `live-run hours: 0.00`, `caps in force: none`, `settings hashes seen: none`.

## Limits of this record

- A run whose worktree was removed has lost its record, so live-run hours (2.54) is a lower bound. The landings and gate numbers come from the state files and are complete.
- The `caps in force` line prints one entry per run, because each `caps` record also holds its file paths and hashes. The cap values are the same in every entry.
