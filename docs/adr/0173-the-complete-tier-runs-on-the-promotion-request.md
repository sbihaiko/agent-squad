# The complete tier runs on the promotion request

Two pull requests carry CI, and each carries one tier. The dev-to-stg pull request carries the fast tier, which proves a
landing cheaply and moves `stg`. The stg-to-main pull request carries the complete tier, which proves the full suite and
moves `main`. The complete tier on every landing spent its minutes again on each push and held `stg` behind a proof of
an hour; on the stg tip it runs once for a burst of promotions.

## The two tiers

- The fast tier runs on every event of the dev-to-stg pull request but a label: the three jobs `ruff`, `mypy` and
  `fast-battery`. The dev-to-stg promotion reads the fast tier alone as its proof and asks for no label.
- The complete tier runs on the stg-to-main pull request, on the `promote` label alone. The label is the promotion
  request. No push starts the tier, on either pull request.
- A guard job starts the tier once per tip. It reads the tip's own check runs and answers whether a complete-tier run
  already exists; a second label event on the same tip starts nothing. The guard reads the tier's job names from the
  workflow itself, so the names have one source.
- A check run another event skipped is no evidence. Both readers take, for each job name, the newest completed run that
  is not skipped.
- A run that ended `cancelled` or `timed_out` measured nothing. The guard does not count it, so a new label starts the
  tier again. The guard and the pass read that set of conclusions from one place.

## The workflow file

A `pull_request` run executes the workflow of the pull request's merge commit, so the head branch supplies the file. The
stg-to-main pull request therefore reads `ci.yml` from `stg`: the `promote` label starts the complete tier from `stg`'s
copy, and `main` takes that same file in the fast-forward that moves it. The first request needs no copy of the tier on
`main`. The trigger reads the base branch, `main`, to decide that the event starts a run.

## The stg-to-main pass

The watch cycle runs `promote-main-pass.sh` every cycle, detached, under its own single-flight lock and outside the quiet
window of the dev-to-stg step. A profile that promotes nothing makes it exit at once.

- **The request.** The pass asks when `stg` is ahead of `main` and either the newest first-parent commit of `stg` is
  older than the quiet window, or the oldest one `main` does not hold is older than the ceiling. A burst of promotions
  earns one run; a stream that never quiets is still proven inside the ceiling. The pass opens or reuses the stg-to-main
  pull request, removes the label, sends it again, and records the head sha the pull request answered. The record is
  written only after GitHub accepts the label.
- **No request.** The pass asks for nothing when `main` is not an ancestor of `stg`, which is an alert, when the pull
  request head is the sha of the last red verdict, or when a hold is younger than the quiet window.
- **In flight.** While a job of the recorded sha has not completed, and the request is younger than the flight bound, the
  pass changes nothing. A request with no run yet is in flight for a short grace. Past the bound the tip is unproven.
- **Green.** Every complete-tier job of the recorded sha is green: `promote-main.sh <sha>` moves `main` to that sha by a
  fast-forward. `stg` may already be ahead; `main` takes the proven sha and never the tip. The pull request is never
  merged, so `main` takes no merge commit, and `promote-stg.sh` refuses its merge route when the base is the main branch.
  The script proves the sha itself before it pushes, through the pass's own reader: it trusts no caller.
- **Red.** A job ran a step and failed. `main` stays. The pass keeps one open bug: it files it with `bug` and
  `ready-for-agent`, never as a loop defect, or comments on the open one. It records the hold time and the red sha. A red
  sha is never asked again; a new `stg` tip is needed.
- **Unproven.** The runner cancelled the tier, the tier timed out, the label started no run, or the workflow names no
  complete-tier job. `main` stays, one alert names the cause, and no bug is filed. The pass records the hold time, and the
  next request waits for the quiet window. An unproven tip never moves `main`: the fast-forward needs a measured green.
- **Done.** When `main` holds the `stg` tip, the pass clears the request and does nothing.

`promote-main.sh <sha>` refuses a sha that is not on the first-parent chain of `stg`, and a sha `main` is not an ancestor
of. It reads the sha's complete-tier check runs before it pushes, through the one reader `mainproof.sh`, which the pass
also uses. The move is refused, with the missing, red or unproven job named, unless every job is green. A read that fails
proves nothing and holds `main` the same way. The reader takes the tier's job names from the pass's one resolver, and the
pass hands its own resolved names and unproven set to the same row rule, so the pass names the tier once and neither reader
re-derives it. A run that names no checkout reads the workflow of the tree the script lives in, so the owner's hand command
needs no environment; a workflow file that cannot be read refuses the move and names its path. `SG_PROMOTE_UNPROVEN=owner`
is the only override, and the script prints the line that says it used it. With no argument it moves `main` to the `stg`
tip, which is the owner's command, and reads no tier.

## Settings

`SG_PROMOTE_MAIN_QUIET_S` (300), `SG_PROMOTE_MAIN_MAX_S` (14400) and `SG_PROMOTE_MAIN_FLIGHT_S` (3600) are the quiet
window, the ceiling and the flight bound. The quiet window stays short: a longer window starts fewer complete tiers, but it
adds its wait to every promotion and holds `main` longer, so the saving never pays for the delay. `SG_MAIN_BRANCH` and
`SG_STG_BRANCH` name the pair; no script names a branch as a literal.

Guard: `tests/test_tooling.py`, `contrib/squad-goal/scripts/test-promote-main-pass.sh`,
`contrib/squad-goal/scripts/test-promote-main-proof.sh`,
`contrib/squad-goal/scripts/test-promote.sh`, `contrib/squad-goal/scripts/test-promote-branch.sh`,
`contrib/squad-goal/scripts/test-watch-cycle.sh`.
