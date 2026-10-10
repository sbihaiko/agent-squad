# Evidence for #1241 — the run command files its bug only on request, and a partial run records its selection

Fork-only work (agent-squad `local/next`, branch `gui/1241`): the run command and its cases live here, no MesenAI file
changes.

## What changed

- `scripts/gui_test_run_script.py`: the opt-out `--no-report-bug` is gone. Filing is **opt-in** (`--file-bug`, off by
  default), so a test, acceptance or CI run leaves the board alone; `bug.filed=false` still carries the reason (not
  asked, not the registered emulator adapter, no failed step, no helper, or a helper that failed) and a filing that
  raises is recorded, never propagated: the exit code reads the steps. `--batch <id>` and `--case <id>` narrow the run
  (a case keeps its batch's setup and teardown) and the results file records `selection` = `{kind, value}`; a full run
  carries no `selection`, and a selector the script does not have is refused before the adapter is asked anything.
- `tests/test_gui_test_e2e.py`: a `#1241` section of 9 cases driven through the command's own contract (`main(argv)`)
  with the shape fake adapter registered under a name, a `scripts/report-bug.sh` stand-in that logs every call, and the
  one existing `#1222` assertion updated (`bug.reason` now names the opt-in).
- `vendor/skills/skills/to-test-scenario/SKILL.md`: the Run step's prose names `--file-bug` and the selectors instead of
  the removed flag.

No case files a real bug: the default is off, and every filing case points `MESENAI_ROOT` at a temporary checkout whose
`scripts/report-bug.sh` is a stand-in.

## RED, before the change

```bash
cd ~/wt-as-1241 && uv run --extra dev pytest tests/test_gui_test_e2e.py -k "1241" -q --tb=line
# 9 failed
```

```
E   assert (True is False)
tests/test_gui_test_e2e.py:2111: assert (True is False)          # the red run filed a bug nobody asked for
E       SystemExit: 2
../.local/.../argparse.py:2758: SystemExit
E   gui_test_run_script.py: error: unrecognized arguments: --case b1.ghost
```

## GREEN, at this branch's head

```bash
cd ~/wt-as-1241 && uv run --extra dev pytest tests/test_gui_test_e2e.py -k "1241" -q --tb=line
# 9 passed, exit 0
cd ~/wt-as-1241 && MESENAI_REPO=~/wt-e2e MESENAI_ROOT=~/wt-e2e uv run --extra dev pytest tests/test_gui_test_e2e.py -q -m "not slow"
# one failure, pre-existing and environment-gated: test_a_missing_rom_fixture_or_a_wrong_sha1_fails_clearly needs
# MESEN_GUI_BINARY (a built launcher carrying the test hook), which this machine has not built
```

## Two runs proving the interface

```bash
# a red run nobody asked for a bug: it files none, and says why
python3 scripts/gui_test_run_script.py <script.json> --adapter mesen-gui --out /tmp/run.json
# {"status": "failed", ..., "bug": {"filed": false, "reason": "no bug was asked for: filing is opt-in (--file-bug)"}}

# the same run, asked, narrowed to one case: one bug for the whole run, and the selection it covered
python3 scripts/gui_test_run_script.py <script.json> --adapter mesen-gui --case home.open-library --file-bug --out /tmp/run.json
# {"selection": {"kind": "case", "value": "home.open-library"}, ..., "bug": {"filed": true, "url": "..."}}
```
