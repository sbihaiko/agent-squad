# Evidence for #1286 — every case of `play-pad-only.gui-test` runs on the squad's infrastructure

Issue #1286 is the scenario parent of the pad-only pilot: 26 batches, 281 cases, of which 14 are `automated` and 267
are `manual` (a person runs them and records a verdict). Its acceptance is that **every listed case can be run** on the
squad's GUI test infrastructure.

Two things were missing, and both are now in the fork (issue #1286, branch `gui/1286`, never pushed):

- `squad/gui_test_scenario.py` — run the **whole** scenario in one call: one adapter per batch, one row per case of the
  script in script order, each row carrying the case's `blocked_by` edge (the "Blocked by" column of the scenario's own
  case issues). A batch the roster cannot serve is `not executed` with the reason and makes the run red — never a
  dropped case, never a silent green.
- `tests/gui_test_pilot_fake_1286.py` — the roster of the committed pilot: `launch`, `home` and `library` run on
  `ScenarioFake`, the Play door the scenario's own `settings.profile: fresh` describes; the other 23 batches are
  manual-only and run on an adapter that only launches.

## The gap the RED case shows: the Home of a fresh profile

The committed `home` batch (7 automated cases) could not be run on the adapter the suite had. `PilotFake`'s Home keeps
four tiles whatever the settings profile; a fresh profile has no play history and no favorites, so the Home holds Open
a ROM alone and a D-pad press stays on it. Up therefore escaped to Recent:

```
$ MESENAI_REPO=~/wt-e2e uv run --extra dev pytest tests/test_gui_test_e2e.py -k 1286 -q
FAILED tests/test_gui_test_e2e.py::test_1286_the_home_keeps_its_ring_on_a_fresh_profile_so_the_hold_cases_run
FAILED tests/test_gui_test_e2e.py::test_1286_the_committed_home_batch_runs_end_to_end_on_the_roster
FAILED tests/test_gui_test_e2e.py::test_1286_every_case_of_the_committed_pilot_gets_a_verdict_and_the_automated_ones_pass
FAILED tests/test_gui_test_e2e.py::test_1286_a_failed_case_makes_the_scenario_red
E   AssertionError: {'home.first-focus': 'passed',
                     'home.focus-holds-up': 'failed (expected ui.focused == play.home.open-rom, observed play.home.recent)',
                     'home.focus-holds-down': 'passed', 'home.focus-holds-left': 'passed', ...}
```

One escape is enough to make the whole `home` batch unrunnable, and with it 7 of the scenario's 14 automated cases.

`ScenarioFake._home_order` reads the profile instead: no history, no favorites → `["play.home.open-rom"]`; history adds
Continue and Recent; favorites adds Favorites. The Home the fake answers with is then the Home the scenario declares,
which is also the one the real emulator has (`test_three_pilot_steps_run_on_the_real_emulator_with_no_pad_attached`
asserts `home.focus-holds-up` passes on the built app).

## The whole scenario, run

```
$ MESENAI_REPO=~/wt-e2e uv run --extra dev python -c '
import json, os, sys, tempfile; from pathlib import Path
sys.path.insert(0, ".")
from squad.gui_test_scenario import run_scenario
from tests.gui_test_pilot_fake_1286 import ADAPTERS
d = json.loads(Path(os.environ["MESENAI_REPO"], "docs/validation/process/play-pad-only.gui-test.json").read_text())
out = run_scenario(d, ADAPTERS, out_root=tempfile.mkdtemp())
print("status", out["status"], "reasons", out["reasons"]); print("summary", json.dumps(out["summary"]))
print("batches", len(out["batches"])); print("passed:", [r["id"] for r in out["rows"] if r["verdict"] == "passed"])'
status passed reasons []
summary {"passed": 14, "failed": 0, "pending": 267, "not executed": 0, "cases": 281, "run": 14}
batches 26
passed: ['launch.first-run-home', 'launch.open-rom-focused', 'home.first-focus', 'home.focus-holds-up',
         'home.focus-holds-down', 'home.focus-holds-left', 'home.focus-holds-right', 'home.open-library',
         'home.back-to-home', 'lib.home-ring', 'lib.reach-library', 'lib.open-screen', 'lib.back-to-home',
         'lib.back-ring-on-opener']
```

281 rows for 281 cases of the committed script, in script order, none dropped and none added; the 14 automated cases
the scenario's own table marks `automated` pass, and the other 267 are `pending` for a person. `run` counts what a
step behind it decided, so a scenario nothing ran is red.

## The e2e cases added

```
$ MESENAI_REPO=~/wt-e2e uv run --extra dev pytest tests/test_gui_test_e2e.py -k 1286 -q
7 passed
```

- `test_1286_the_home_keeps_its_ring_on_a_fresh_profile_so_the_hold_cases_run` — the RED above, now green.
- `test_1286_the_committed_home_batch_runs_end_to_end_on_the_roster` — the batch's seven automated cases pass.
- `test_1286_every_batch_of_the_committed_pilot_is_wired_to_an_adapter` — no batch is left without a target, and a
  batch the roster calls manual-only has no automated case behind it.
- `test_1286_every_case_of_the_committed_pilot_gets_a_verdict_and_the_automated_ones_pass` — the 281 rows above.
- `test_1286_a_case_names_the_case_that_blocks_it` — `home.focus-holds-up <- home.first-focus`,
  `lib.open-screen <- lib.reach-library`, `pause-pack-multi.pack-focus <- pause-pack-multi.chord-opens`.
- `test_1286_a_batch_the_roster_cannot_serve_is_not_executed_and_never_dropped` — a roster gap is visible in the rows
  and red.
- `test_1286_a_failed_case_makes_the_scenario_red`.

The suite as a whole: 155 cases, 148 passed, 1 skipped, 6 failed — the same six that need a built emulator, a native
core or ROM fixtures and fail identically on `local/next` without this branch (`-k 1286` is the honest narrowing for
this issue).

## Reproducing

```bash
cd ~/wt-as-1286
MESENAI_REPO=~/wt-e2e uv run --extra dev pytest tests/test_gui_test_e2e.py -k 1286 -q
```

A full run of the scenario against the built application is the run command's, one adapter for every batch:

```bash
python3 scripts/gui_test_run_script.py ~/wt-e2e/docs/validation/process/play-pad-only.gui-test.json --adapter mesen-gui
```

with `MESENAI_ROOT`, `MESEN_GUI_BINARY` and `MESEN_CORE_LIB` set to a build of MesenAI main. `run_scenario` is the
per-batch door a run needs when the batches do not share one target.
