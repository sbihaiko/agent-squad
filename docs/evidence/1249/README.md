# Evidence for #1249 — the bug a red run files names the failure, and the same failure is never filed twice

Fork-only work (agent-squad `local/next`, branch `gui/1249`): the report path and the run command live here, no MesenAI
file changes.

## What changed

- `squad/gui_test_report.py`: the bug's title is the script, the first failed step and that failure in plain words
  (`bug_title`, `failure_key`, `failure_words`) — `run-script-smoke: smoke.still-home — expected play.home, observed
  play.library (+1 more steps)`, never `N failed step(s)`. Before filing, `find_open_bug_with` asks GitHub's search for
  an **open** issue whose title carries that key and `comment_on_bug_with` comments on it with the run id, the build SHA
  and the same evidence instead of opening a second issue. `report_failures_detail` returns
  `{title, key, url, existing, lookup_failed}`; `report_failures` (#1190) is the same call and still returns the URL. A
  look-up that cannot run (no `gh`, no network) is recorded in `lookup_failed` and the run files its bug: a failure
  nobody recorded is worse than a bug that may repeat.
- `scripts/gui_test_run_script.py`: `file_one_bug` takes the adapter's repo slug and the run's identity and returns that
  detail; the results file carries `bug = {filed, url, title, existing, reason}`, so the run names the issue it belongs
  to, new or existing. `--github-runner module:callable` swaps the shell the bug's GitHub calls go through — the
  acceptance suite points it at `tests/gui_test_github_fake`, so no test reaches the network.
- `tests/gui_test_github_fake.py` (new): a fake GitHub runner that answers the search and comment calls itself and files
  through the caller's `report-bug.sh` stand-in.
- `tests/test_gui_test_e2e.py`: a `#1249` section of 5 cases (title shape, attach-instead-of-file, a different failure is
  a second bug, a look-up that cannot run, and the run command's bug entry) plus `_run_cmd` pointed at the fake GitHub
  and the two `#1222`/`#1241` assertions that pinned the old boilerplate title.

No case files a real bug: every filing goes through a temporary checkout's `report-bug.sh` stand-in, and the GitHub
doors are the fake runner.

## RED, before the change

```bash
cd ~/wt-as-1249 && MESENAI_REPO=~/wt-e2e uv run --extra dev pytest tests/test_gui_test_e2e.py -k 1249 -q
# ImportError: cannot import name 'bug_title' from 'squad.gui_test_report'
```

```
tests/test_gui_test_e2e.py:2221: in <module>
    from squad.gui_test_report import (bug_title, comment_on_bug_with, failure_key, failure_words,
E   ImportError: cannot import name 'bug_title' from 'squad.gui_test_report'
ERROR tests/test_gui_test_e2e.py
```

## GREEN, at this branch's head

```bash
cd ~/wt-as-1249 && MESENAI_REPO=~/wt-e2e uv run --extra dev pytest tests/test_gui_test_e2e.py -k 1249 -q
# 5 passed, exit 0
cd ~/wt-as-1249 && MESENAI_REPO=~/wt-e2e uv run --extra dev pytest tests/test_gui_test_e2e.py -p no:randomly -k "1190 or 1241 or 1222"
# 1 failed, 24 passed: the one failure is environment-gated (test_1222_the_pilot_home_batch_runs_through_the_command_
# against_the_native_core asserts MESENAI_ROOT, a MesenAI checkout holding scripts/gui_test), and is pre-existing
```

## Two runs proving the interface

Two red runs of the same failure through the production filing door (`file_one_bug`) against the fake GitHub:

```
run 1: existing=False url=https://example.invalid/bugs/1
  title: run-script-smoke: smoke.still-home — expected play.home, observed play.library (+1 more steps)
run 2: existing=True url=https://example.invalid/bugs/1
  title: run-script-smoke: smoke.still-home — expected play.home, observed play.library (+1 more steps)
open bugs: [(1001, 'run-script-smoke: smoke.still-home — expected play.home, observed play.library (+1 more steps)')]
comments: [(1001, '- Run: `run-2` on `darwin-arm64` (build `abc123`)')]
```

One open bug, one comment carrying the second run's id and build; two *different* failures are two issues with two
different titles (the `#1249` cases above assert both).
