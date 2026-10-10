# Evidence for #1243 — the run command runs one batch, one case or one variant, and records the selection

Fork-only work (agent-squad `local/next`, branch `gui/1243`): the run command, the supervised runner and their cases
live here. **No MesenAI file changes**, so no PR against sbihaiko/MesenAI: the in-app hook, the emulator adapter and
the pilot script are untouched, and the fork commit is the deliverable.

## What changed

- `scripts/gui_test_run_script.py`: the third selector, `--variant <axis>=<value>`, in the same mutually exclusive
  group as `--batch` and `--case`. `_narrow_variant` keeps the steps the value applies to (an untagged step runs in
  every value, a step tagged for other values of the axis is dropped, a step tagged for several is now the one under
  test) and drops a batch left with no step, exactly as `squad.gui_test_variants.run_variants` defines a variant. The
  results file records `selection` = `{kind: "variant", value: "<axis>=<value>"}`; a full run still carries no
  `selection`. A variant the script does not declare, and a `--variant` that is not an `<axis>=<value>` pair, are
  refused by `narrow` before the adapter is asked anything, naming the value and what the script does have.
- `scripts/gui_test_run_script.py`, `required_switch`: an axis the adapter advertises **one way only** is the mode the
  run is already in (mesen-gui advertises `window.mode: [windowed]`), so selecting that value narrows the steps and
  switches nothing; an axis advertised several ways is switched through the adapter's optional `set_variant` hook.
- `squad/gui_test_supervise.py`: `run_supervised(..., variant=(axis, value))` switches each batch's fresh process
  through that hook **before it launches** — the same optional hook `squad.gui_test_variants` already drives in
  process — and an adapter without the hook raises `VariantUnsupported`, which the command reports as
  `nothing was launched: ...`, naming the variant. Without this, a run whose cases were tagged for a window mode
  nothing switched would report a mode it never entered: the reason #1241 deferred `--variant` to this ticket.
- `tests/gui_test_run_script_fake.py`: `VariantAdapter` (the shape fake plus the optional hook, logging the axis and
  value it was given next to the run's artifacts) and `OneModeAdapter` (a one-way window axis). Both additive.
- `tests/test_gui_test_e2e.py`: a `#1243` section of 7 cases driven through the command's own contract — `main(argv)`
  in, one results file out — with the fake registered under a name, so no case can launch anything or touch GitHub.

No ADR is amended. ADR-0272 item 6 fixes the adapter's seven session operations (capabilities, launch, inject, check,
wait, capture, teardown) and none of them switches a variant; `set_variant` is an *optional adapter method* like
`moves` (ADR-0271's navigation) and `advance`/`observe`, used only when the adapter has it, so the emulator adapter
and the hook are unchanged and a second target needs no runner change. The fullscreen/real-window mode stays
local-only, as ADR-0272 says.

## The acceptance criteria

- **Each selector narrows the run to exactly the named steps (plus required setup) and records the selection.** A
  variant keeps every untagged step (they run in every value) and the steps tagged for the value, in every batch, and
  the case and batch selectors are unchanged; the `selection` it records is the third `{kind, value}` shape.
- **An unknown selector is rejected before any launch, naming it.** No variant, no axis, no `axis=value` shape: each
  raises out of `narrow` before `capabilities()` is even called
  (`test_1243_an_unknown_variant_is_refused_before_anything_is_launched`,
  `test_1243_a_variant_that_is_not_an_axis_value_pair_is_refused`), and each case asserts no artifacts directory is
  left behind.
- **A full run records no selection.** Unchanged by this ticket and still asserted by
  `test_1241_a_full_run_records_no_selection_and_a_narrowed_one_names_it`, which runs the same script the variant
  cases run; `test_1243_a_variant_selection_runs_that_variant_only_and_records_it`'s full-run sibling in the demo
  below shows `"selection" not in r` with all four steps run.
- **Tested through the run command's contract with the fake adapter.** Every case calls `main(argv)` and reads the
  results file; the adapter is `tests.gui_test_run_script_fake:VariantAdapter` / `:OneModeAdapter` / `:ShapeAdapter`,
  never a built application.

## RED, before the change

```bash
cd ~/wt-as-1243 && uv run --extra dev pytest tests/test_gui_test_e2e.py -k "1243" -q --tb=line
# 5 failed, 1 passed (the mutual-exclusion case passes in RED because argparse refuses --variant for the wrong reason)
```

```
gui_test_run_script.py: error: unrecognized arguments: --variant fullscreen
/Users/bihaiko/.local/share/uv/python/cpython-3.14.0-macos-aarch64-none/lib/python3.14/argparse.py:2758: SystemExit: 2
E   SystemExit: 2
```

Full output kept: `docs/evidence/1243/red.txt`.

## GREEN, at this branch's head

```bash
cd ~/wt-as-1243 && uv run --extra dev pytest tests/test_gui_test_e2e.py -k "1243 or 1241" -q --tb=line
# ................  (16 passed: this ticket's 7 and #1241's 9, exit 0)
```

```bash
cd ~/wt-as-1243 && MESENAI_REPO=~/wt-e2e MESENAI_ROOT=~/wt-e2e uv run --extra dev pytest tests/test_gui_test_e2e.py -q --tb=line -m "not slow"
# 1 failed: test_a_missing_rom_fixture_or_a_wrong_sha1_fails_clearly — pre-existing and environment-gated (it needs
# MESEN_GUI_BINARY, the built launcher carrying the test hook, which this machine has not built)
```

`~/wt-e2e` (the squad loop's own detached e2e worktree, `SG_E2E_DIR`) was removed by a concurrent loop cycle part-way
through this session, which takes the pilot-reading cases down with it. The same command run again with the checkout
absent, at this head and at `local/next` (`ff9241d`) in a detached worktree, fails the **same 8 cases**, none of them
this ticket's:

```bash
cd ~/wt-as-1243 && uv run --extra dev pytest tests/test_gui_test_e2e.py -q --tb=no -m "not slow"   # 8 failed
# reverse_drift_committed_markdown_equals_live_render, reverse_drift_vendored_renderer_equals_fork_file_at_pinned_commit,
# pilot_script_opens_as_a_diagram_and_every_node_shows_its_text, 1200_the_pilot_dialogs_no_trap_sweep…,
# a_missing_rom_fixture_or_a_wrong_sha1_fails_clearly, 1224_the_pilot_script_covers_every_case_id_of_its_scenario,
# 1223_the_plan_covers_every_committed_pilot_step_and_labels_none_of_them_ready_for_agent,
# 1223_publishing_the_pilot_twice_updates_and_never_duplicates_a_case
# the identical set at local/next: only in mine [] / only in base []
```

## Three runs proving the interface

The demo script is two batches: `b1.any` (untagged), `b1.win` (`window.mode=windowed`), `b1.fs`
(`window.mode=fullscreen`), `b2.a` (untagged); the adapter is `tests.gui_test_run_script_fake:VariantAdapter`.

```bash
# a full run: every step, and no selection
python3 scripts/gui_test_run_script.py /tmp/variant-demo.json --adapter tests.gui_test_run_script_fake:VariantAdapter --out /tmp/v-full.json
# steps ['b1.any', 'b1.win', 'b1.fs', 'b2.a'], status passed, "selection" in record -> False

# one variant: the steps it applies to, the selection, and each batch's process switched before launch
python3 scripts/gui_test_run_script.py /tmp/variant-demo.json --adapter …:VariantAdapter --out /tmp/v-fs.json --variant window.mode=fullscreen
# {'kind': 'variant', 'value': 'window.mode=fullscreen'} ['b1.any', 'b1.fs', 'b2.a'] passed
# /tmp/v-fs.json.artifacts/variant.log == ['window.mode=fullscreen', 'window.mode=fullscreen'] (one per batch process)

# an unknown variant: refused, nothing launched, and it names what the script has
python3 scripts/gui_test_run_script.py /tmp/variant-demo.json --adapter …:VariantAdapter --out /tmp/v-x.json --variant window.mode=huge
# status failed: "nothing was launched: no variant window.mode=huge in tiny: window.mode has windowed, fullscreen"

# an adapter that cannot switch, and a --variant that is not a pair
python3 scripts/gui_test_run_script.py /tmp/variant-demo.json --adapter …:ShapeAdapter --out /tmp/v-nh.json --variant window.mode=fullscreen
# "nothing was launched: the adapter cannot switch to variant window.mode=fullscreen: AttributeError: 'ShapeAdapter'
#  object has no attribute 'set_variant'" — steps [], batches []
python3 scripts/gui_test_run_script.py /tmp/variant-demo.json --adapter …:VariantAdapter --out /tmp/v-m.json --variant fullscreen
# "nothing was launched: no variant 'fullscreen': --variant takes <axis>=<value>, as the script declares it"
```

## Observed, not filed

The committed pilot (`docs/validation/process/play-pad-only.gui-test.json` in MesenAI) declares
`variants: {window.mode: [windowed, fullscreen]}` while `mesen-gui` advertises `window.mode: [windowed]` only, so a
*full* supervised run of it is refused by `validate(script, caps)` before launch. That is the mode split ADR-0272
names (fullscreen is the local-only real-window mode) and the derivation in #1242 already selects the runnable
subset, so this is not filed as a bug. It does mean `--variant window.mode=windowed` is now a second, cheaper way to
run that script's windowed cases: the narrowing declares only the advertised value and drops the fullscreen case.
