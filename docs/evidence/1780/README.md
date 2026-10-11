# Evidence for #1780 — the adapter and the runner drive the hook's verbs from #1779

What this repo owns of #1780 is the **fork-only half**: `squad/gui_test_emulator.py` (the adapter),
`squad/gui_test_run.py` (the runner), the predicate seam in `squad/gui_test_checks.py`, and the tests that hold them
(`tests/test_gui_test_e2e.py`, `tests/gui_test_contract.py`, `tests/fake_hook_app.py`). The other half — the pilot
script's own `mode` stamps and the `mesen-gui` adapter they are stamped against — lives in the MesenAI checkout, so
nothing here writes to it. This file is the measurement of where that half stands, taken read-only against the
committed pilot at `~/wt-e2e/docs/validation/process/play-pad-only.gui-test.json` (2026-10-11).

## 1. What the fork half does now

| Verb | Was | Is |
| --- | --- | --- |
| `pad.press` | advertised, injected | unchanged |
| `pad.release` | not advertised | advertised; injected with its `button` |
| `pad.chord` | not advertised | advertised; injected with its `buttons` |
| `key.press` | not advertised | advertised; injected with its `key` |
| `nav.goal` | not advertised | advertised, and **asked as the read it is**: the hook answers `{goal, state, family, moves}`, the adapter keeps the reply, `moves(family)` hands the six `PadNavAction` presses to the Navigator, and `advance()` waits one application tick. Nothing is injected to ask it. It is also declared under `reads` beside the actions it was added to, which is how the runner tells this adapter from one that performs the goal itself. |

The runner (`squad/gui_test_run.py`) asks the adapter for that read **before** the Navigator chooses, and a goal step
with no Navigator available ends `pending` — never a fabricated pass, which is ADR-0271 Decision 6 A (local only, no
key in CI) saying itself in the result. Which of the two kinds of adapter a step is on is `goal_route`: an adapter that
answers the read has the goal handed to the Navigator, while one that lists `nav.goal` among the actions it runs and
answers no read drives the goal itself, and the step's own wait and check decide it — the kind the fakes behind the
#1193–#1197 fixtures are, whose `*.reach-*` setups must resolve for those batches' `pending` set to stay the fixture's
`manual` one. The derivation (`squad/gui_test_subset.runnable`) keeps a step naming any of those five verbs `automated`
for an adapter that advertises them.

## 2. Where the pilot's manual cases stand, measured

`runnable` only ever *narrows*: an `automated` step the target cannot run becomes `manual`, and one already carrying
`mode: "manual"` in the file keeps it. So a capability change alone cannot flip the committed script's stamps — the
flip comes from **re-publishing** the scenario, which is a write to the MesenAI checkout. Two rules decide a stamp:
this adapter's capabilities (which `runnable` models) and the publisher's own reasons for a step no run can reach
(which it does not). Measured on a copy of the pilot with the stamps stripped, `runnable` against the five verbs
leaves `home` at **1** manual — `launch.port-lamp-p1`, needing `pad.connect`, a verb this slice does not add — and
`home-after-game` at **0**. Everything else the published pilot still stamps manual is the publisher's rule: a ROM,
a profile fixture, an on-screen keyboard, box art, bug #1485. That is why the table below reads as it does: the
reason each stamped step carries today, and whether it is a verb this fork now drives or the behaviour itself.

### `home` — 13 steps, 3 manual

| Step | Stamped reason | After a re-publish |
| --- | --- | --- |
| `home.chord-opens-nothing` | needs action `pad.chord` | **automated** — the adapter advertises and injects it |
| `home.chord-focus-unchanged` | needs action `pad.chord` | **automated** — same |
| `home.settings-b-returns` | the hook reads `ui.surface == play.pause` after B with no game loaded, not `none`; judged by eye until bug #1485 is fixed | manual — the behaviour (ADR-0269), not a verb |

### `home-after-game` — 12 steps (all 12 manual) + a manual `setup`

| Step | Stamped reason | After a re-publish |
| --- | --- | --- |
| `setup after-game.reach-home` | needs action `nav.goal` | **automated** — the adapter asks the hook's read |
| `after-game.contained-up-continue`, `…-open-rom`, `…-sideways-left`, `…-sideways-right` | batch setup `after-game.reach-home` is manual, so nothing established the state this step asserts | **automated** — the setup they depend on flips with it |
| `after-game.continue-first`, `after-game.contained-other-controls-gap` | a real ROM path and its No-Intro sha1 are supplied by hand; no ROM is committed | manual — the behaviour needs a committed ROM |
| `after-game.order-favorites`, `…-order-recent`, `…-order-open-rom`, `…-contained-up-recent`, `…-contained-up-favorites` | needs variant `settings.profile=history-one-favorite` — the shelf's ids exist, but only that seeded profile puts a game on them | manual — the fixture, not a verb |
| `after-game.one-ring` | how many rings are drawn and the shelf order are judged by eye | manual — by nature |

So the batch stands at 12 manual of 12 today and lands at **8** once the scenario is re-published: 1 manual `setup`
and 4 steps it was holding back become automated, and the remaining 8 are two ROM fixtures, five profile-fixture
steps and one read by eye.

### `library` — 36 steps, 31 manual

| Class | Count | Why it cannot be automated |
| --- | --- | --- |
| needs a control id (`play.library.tile`, …) | 25 | the hook reports `controls`, but the id the step names is not in the target's advertised list — a capability-list entry, not one of the five verbs |
| no 100+ ROM library is committed and the repeat rate is judged by timing | 2 | the behaviour needs a library fixture that does not exist |
| the header text and the scanning indicator are read by eye | 1 | the hook reads the action bar's ids, not its words |
| the typed text is read from the on-screen keyboard | 1 | the hook does not read the keyboard's text |
| the keyboard is a sheet over the library that the hook does not read | 1 | same |
| box art is a visual check | 1 | by nature |

## 3. The end-to-end run the issue asks for

`DONE = a run of the home and after-game batches proves it end to end, through the runner`. That run needs the pilot
script re-published (a write to the MesenAI checkout), a committed ROM, and a seeded `history-one-favorite` profile —
none of which this fork-only slice owns. What is proven here instead is the runner driving the same verbs over the
hook: `tests/test_gui_test_e2e.py` runs a goal batch through the runner against the stub application, and
`test_the_runner_keeps_a_home_step_naming_one_of_those_verbs_automated` holds the derivation side.
