# Evidence for #1222 — `scripts/gui_test_run_script.py`

The command that runs a whole `gui-test/1` script end to end (ADR-0272 items 6–8), plus the real run behind its
acceptance criterion "run for real against `docs/validation/process/play-pad-only.gui-test.json` (the Home batch) with
the native core loaded". These files are evidence, not fixtures: nothing in the suite reads them.

| File | What it is |
| --- | --- |
| `play-pad-only-committed-script.rejected.json` | The results file of running the committed pilot script against the real `mesen-gui` adapter: rejected **before launch**, naming every action, check and variant the adapter does not advertise. |
| `play-pad-only-home.gui-test.json` | The Home batch's advertised steps, extracted from that same pilot script — the only subset the adapter can be asked to run today (see the gap below). |
| `play-pad-only-home.results.json` | The results file of running that subset against the real core: 6 passed (each with a capture), 1 failed, 1 `skipped (batch failed)`, 3 `pending` (manual), exit 1, one bug filed for the run. |

## The gap between the pilot script and the adapter

ADR-0272 item 6 requires the command to reject a script naming something the adapter does not advertise, *before*
launch. The committed pilot script uses `pad.release`, `pad.chord`, `nav.goal`, `nav.sweep` and the `fullscreen`
window-mode variant; the `mesen-gui` adapter advertises `pad.press` and `ui.{screen,focused,visible,dialogs}`
(windowed only). So the committed script as a whole cannot start: `rejected.json` lists 53 problems, one per offending step. That is
the command doing what the ADR binds it to do, and it is a real mismatch between the issue's acceptance criterion
("run the Home batch") and the pilot script as committed — flagged, not fixed here, because the pilot script and its
step ids are #1183/#1177's (and the `play.library` id it checks is provisional until #1182).

The Home batch alone has 11 steps: 8 use only advertised names, 3 use `pad.release`/`pad.chord`/… and were dropped for
this run. Of the 8, `home.back-to-home` is the teardown of `home.open-library`: with that step failing, the supervised
runner reports it as `skipped (batch failed)` rather than running it out of order.

## The failure, and the bug

`home.open-library` fails with `timeout waiting for ui.screen == play.library`. The adapter is asked for a screen id
the application does not have: the Home host's ids are `play.home`, `play.home.continue` and `play.home.open-rom`, and
the pilot's own `precondition` says the ROM fixture is the `<no-intro sha1 ...>` placeholder the adapter refuses. This
is the known gap of the pilot, filed as sbihaiko/MesenAI#1228. The re-run for this evidence filed #1230 for the same
step; #1230 was closed as a duplicate of #1228.

## Reproducing

```bash
export MESENAI_ROOT=<MesenAI checkout>            # holds scripts/gui_test/mesen_gui_adapter.py
export MESEN_GUI_BINARY=<built launcher>          # a build carrying the in-app test hook
export MESEN_CORE_LIB=<MesenCore next to it>      # the library of that same build
python3 scripts/gui_test_run_script.py docs/evidence/1222/play-pad-only-home.gui-test.json \
    --adapter mesen-gui --out /tmp/home-run.json
```

The run writes its captures and batch logs to `<out>.artifacts`; they are not committed here (ADR-0135 keeps
screenshots out of the tree — they reach an issue only through the target repo's evidence branch).
