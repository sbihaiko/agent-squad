"""A card is Done over a merged pull request on the base, or a closed issue, and nothing else.

A pull request that names the issue and is merged into a *parent* branch delivers nothing to the base: the merge lands on
that branch alone, and the change is stranded once the parent is squash-merged (#125). The pass read any merge naming the
issue as delivery, so the card left Code Complete for Done over work the base never took (#127). The merged-PR hold is
scoped to the base: only a merge whose base is the loop's base counts.

A run's own landing is not a merge into the base (#97), so it no longer makes the card Done (#239); the landed-branch
marker does not either. The two remaining holds are a merged PR on the base and a closed issue.

These cases drive janitor.py end to end with a stub `gh` on PATH and a temp state dir. No case touches the network, the
live loop state or the live board.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "contrib/squad-goal/scripts"
N = 2030  # the issue a finished run delivered, whose pull request names it

# The stub gh answers the two PR searches the pass makes. The merge scoped to the base answers @SCOPED@ and the same
# merge read without the qualifier answers 1, so a case picks whether the base carried the merge.
STUB = """#!/bin/bash
a="$*"; echo "$a" >> "@CALLS@"
case "$a" in
  *graphql*) printf '%s' '{"data":{"repository":{"issues":{"pageInfo":{"hasNextPage":false},"nodes":[]}}}}';;
  *search/issues*)
    case "$a" in *"-X GET"*|*"--method GET"*) ;; *) echo '{"message":"Not Found"}'; exit 1;; esac
    case "$a" in
      *"base:"*"Refs #@N@"*) @SCOPED@;;
      *"is:merged"*"Refs #@N@"*) echo 1;;
      *"Refs #@N@"*) echo 1;;
      *) echo 0;; esac;;
  *projectsV2/*/fields*) echo FID4;;
  *projectsV2/*/items*) @ITEMS@;;
  *issues/*"-q .state"*) echo @STATE@;;
  *issues/*) echo -e '@STATE@\\t\\tIssue';;
esac
"""

BOARD = """#!/bin/bash
case "$1" in
  --add) echo "ADD $2 $3 $4 $5" >> "@CALLS@";;
  --run-fields) :;;
  --clear-run) :;;
  *) echo "STATUS $1 $2 $3 $4" >> "@CALLS@";;
esac
"""


def _write(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def sandbox(tmp_path: Path, scoped: str, *, landed: bool = False, wave: str = "", entered: bool = False, card: str = "",
            live: bool = False, fact: bool = True, closed: bool = False) -> Path:
    """The janitor with the throttle and the reader beside it, a stub `gh` (the merge scoped to the base answers
    `scoped`), a stub board.sh, and one finished run for issue #2030. `wave` is "open" (the run's wave is not in the base
    yet) or "closed" (it landed); `entered` leaves the record that the issue's child entered that wave. `card` is the column
    the board answers the issue's card in, none when empty; `live` leaves the run with no verdict; `closed` makes the
    issue read closed."""
    for name in ("janitor.py", "ghthrottle.py", "ghread.py", "issue_state.py", "board_columns.py", "board-definition.json",
                 "board_facts.py", "spec_fold.py", "issue_ledger.py", "wip_snapshot.py"):
        shutil.copy(SCRIPTS / name, tmp_path / name)
    (tmp_path / "bin").mkdir()
    calls = tmp_path / "calls"
    items = f"printf '%s\\t%s\\thttps://api.github.com/repos/x/y\\n' {N} '{card}'" if card else ":"
    _write(tmp_path / "bin/gh", STUB.replace("@N@", str(N)).replace("@SCOPED@", scoped).replace("@CALLS@", str(calls)).replace("@ITEMS@", items).replace("@STATE@", "closed" if closed else "open"))
    _write(tmp_path / "board.sh", BOARD.replace("@CALLS@", str(calls)))
    run = tmp_path / "co/runs/run-stacked"
    run.mkdir(parents=True)
    (run / "request.txt").write_text(f"CHILD — GitHub issue(s) {N}:\n")
    events = '{"event":"run_start","run_id":"run-stacked","ts":"2026-10-13T08:12:00Z"}\n'
    if landed:
        events += '{"event":"land","outcome":"pass"}\n'
    if not live:
        events += '{"event":"verdict","verdict":"pass","reasons":[],"exit_code":0}\n'
    (run / "record.jsonl").write_text(events)
    (tmp_path / "st/auto").mkdir(parents=True)
    (tmp_path / "st/active-runs.txt").write_text("run-stacked\n")
    auto = tmp_path / "st/auto"
    if wave:
        (auto / "waved-run-stacked").write_text("wave/run-stacked\n")
    if wave == "open":
        (auto / "wave-run-stacked").write_text("wave/run-stacked\n")
    if landed and fact:   # the landing pass's `landed` fact (ADR-0149, #695): rule R reads it, never the record's land heads
        subprocess.run([sys.executable, str(tmp_path / "issue_ledger.py"), "landed", f"x/y#{N}", "run-stacked", "run", "abc"],
                       env={**os.environ, "SG_STATE": str(tmp_path / "st")}, check=True, capture_output=True)
    if entered:   # the entry is a fact in the issue's ledger (ADR-0149), written by its one module
        subprocess.run([sys.executable, str(tmp_path / "issue_ledger.py"), "wave", f"x/y#{N}", "run-stacked", "entered", "entered wave/run-stacked"],
                       env={**os.environ, "SG_STATE": str(tmp_path / "st")}, check=True, capture_output=True)
    (auto / f"disp-{N}").write_text("run-stacked\n")
    old = time.time() - 3600   # the run ended an hour ago: past every settle window, so its markers are the pass's to read
    for p in [*run.iterdir(), run, *auto.iterdir()]:
        os.utime(p, (old, old))
    return tmp_path


def janitor(tmp_path: Path, **extra: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("SG_", "FG_"))}  # a live loop's SG_* never leaks in: the pass reads only this sandbox
    env.update(
        {
            "PATH": f"{tmp_path / 'bin'}:{env.get('PATH', '')}",
            "SG_STATE": str(tmp_path / "st"),
            "SG_CHECKOUT": str(tmp_path / "co"),
            "SG_REPO": "x/y",
            "SG_OWNER": "o",
            "SG_PROJECT": "4",
            "SG_BASE": "main",
            "SG_HUB_DIR": str(tmp_path / "st"),
            "SG_SQUAD": str(Path(__file__).resolve().parents[1]),   # the janitor reads squad/procs.py from the repository (#1508)
            **extra,
        }
    )
    return subprocess.run(["python3", "-I", str(tmp_path / "janitor.py")], capture_output=True, text=True, env=env, check=False)


def writes(tmp_path: Path) -> str:
    f = tmp_path / "calls"
    return f.read_text() if f.exists() else ""


def landing(log: str) -> str:
    """The write lines that name the Landing column of #N, in either spelling (ADR-0170, #2155): the definition names it
    `Landing`, and a version 1 board spells the same column `Code Complete`. A reader folds the two, so a guard that
    pinned one spelling would pass over a write in the other."""
    return "\n".join(line for line in log.splitlines() if f" Landing {N}" in line or f" Code Complete {N}" in line)


@pytest.mark.heavy
def test_a_locally_landed_run_without_a_merged_pr_stops_at_code_complete(tmp_path):
    """A run's own landing is not a merge into the base, so it does not make an open issue Done."""
    sandbox(tmp_path, "echo 0", landed=True)
    janitor(tmp_path)
    log = writes(tmp_path)
    assert f"ADD o 4 Landing {N}" in landing(log), f"the card did not stop at Landing: {log}"
    assert f"Done {N}" not in log, f"the card read Done from a local landing: {log}"


@pytest.mark.heavy
def test_a_record_land_head_with_no_landing_fact_delivers_nothing(tmp_path):
    """#695: rule R reads the landing pass's `landed` fact, never a head the run's record names. The child entered its wave
    and the run recorded a land event, but no landing pass wrote the fact, so the wave never reached the base: nothing is
    delivered, and the card goes back to the queue, as #691 requires."""
    sandbox(tmp_path, "echo 0", landed=True, wave="closed", entered=True, fact=False)
    janitor(tmp_path)
    log = writes(tmp_path)
    assert landing(log) == "", f"the card read Landing over a landing no pass recorded: {log}"
    assert f"ADD o 4 Todo {N}" in log, f"the card did not go back to the queue: {log}"


@pytest.mark.heavy
def test_the_landing_pass_fact_is_what_makes_the_entered_child_code_complete(tmp_path):
    """#695: the same run, with the `landed` fact its landing pass writes, is delivered work."""
    sandbox(tmp_path, "echo 0", landed=True, wave="closed", entered=True)
    janitor(tmp_path)
    log = writes(tmp_path)
    assert f"ADD o 4 Landing {N}" in landing(log), f"the landing fact did not reach Landing: {log}"


@pytest.mark.heavy
def test_a_merge_into_a_parent_branch_does_not_move_the_card_to_done(tmp_path):
    """The measured defect (#127, the #125 strand): the PR names the issue but its base is a parent branch, so the base
    never carries the change. The card is not Done, and it is not Code Complete either: the run recorded no landing, so
    nothing of it is in the wave branch the column means (#464, ADR-0145 rule 12), and the issue goes back to the queue."""
    sandbox(tmp_path, "echo 0")
    janitor(tmp_path)
    log = writes(tmp_path)
    assert f"ADD o 4 Todo {N}" in log, f"the card did not go back to the queue: {log}"
    assert f"Done {N}" not in log, f"the card read Done over a merge the base never took: {log}"


@pytest.mark.heavy
def test_a_landed_child_that_never_entered_a_landed_wave_goes_back_to_the_queue(tmp_path):
    """The measured defect (#467): a child's own land is in its tree, not in the wave. The run cut a wave, the wave landed,
    and the child never entered it, so nothing of it is in the base (ADR-0145 rule 12): the card is Todo, never Code
    Complete, and the dispatch marker goes, so the frontier builds the issue again."""
    sandbox(tmp_path, "echo 0", landed=True, wave="closed")
    janitor(tmp_path)
    log = writes(tmp_path)
    assert landing(log) == "", f"the card read Landing over a child that never entered the wave: {log}"
    assert f"ADD o 4 Todo {N}" in log, f"the card did not go back to the queue: {log}"
    assert not (tmp_path / f"st/auto/disp-{N}").exists(), "the dispatch marker held an issue no wave carries"


@pytest.mark.heavy
def test_a_landed_child_that_has_not_entered_an_open_wave_stays_in_progress(tmp_path):
    """The wave is still open: the child may enter it on a later pass, so the card stays in progress and the marker stays."""
    sandbox(tmp_path, "echo 0", landed=True, wave="open")
    janitor(tmp_path)
    log = writes(tmp_path)
    assert landing(log) == "", f"the card read Landing before the child entered the wave: {log}"
    assert f"ADD o 4 In Progress {N}" in log, f"the card did not stay in progress: {log}"
    assert (tmp_path / f"st/auto/disp-{N}").exists(), "the dispatch marker went while the wave could still take the child"


@pytest.mark.heavy
def test_a_child_that_entered_the_wave_reads_code_complete(tmp_path):
    """The entry record is what Code Complete means for a run that cut a wave."""
    sandbox(tmp_path, "echo 0", landed=True, wave="open", entered=True)
    janitor(tmp_path)
    log = writes(tmp_path)
    assert f"ADD o 4 Landing {N}" in landing(log), f"the card did not read Landing after the entry: {log}"


# Code Review (#603): with the shipped vocabulary, a child of an ended run whose code is done and that has not entered its
# open wave is in Code Review, the column of the review that holds it. The reconcile never moves a card back from a later
# column; a run that delivered nothing still sends the card to Todo, and the entry still means Code Complete.
SHIPPED = {"SG_BOARD_VOCAB": "shipped"}


@pytest.mark.heavy
def test_a_landed_child_waiting_for_its_open_wave_is_in_code_review(tmp_path):
    sandbox(tmp_path, "echo 0", landed=True, wave="open", card="Doing")
    janitor(tmp_path, **SHIPPED)
    log = writes(tmp_path)
    assert f"STATUS o 4 Review {N}" in log, f"the card did not move to Review: {log}"
    assert (tmp_path / f"st/auto/disp-{N}").exists(), "the dispatch marker went while the wave could still take the child"


@pytest.mark.heavy
@pytest.mark.parametrize("later", ["Code Complete", "Closed"])
def test_the_reconcile_never_moves_a_later_card_back_to_code_review(tmp_path, later):
    sandbox(tmp_path, "echo 0", landed=True, wave="open", card=later)
    janitor(tmp_path, **SHIPPED)
    log = writes(tmp_path)
    assert f"Code Review {N}" not in log and f"Doing {N}" not in log, f"a {later} card moved back: {log}"


@pytest.mark.heavy
def test_a_live_run_never_moves_a_code_review_card_back_to_doing(tmp_path):
    sandbox(tmp_path, "echo 0", wave="open", card="Code Review", live=True)
    janitor(tmp_path, **SHIPPED)
    log = writes(tmp_path)
    assert f"Doing {N}" not in log, f"a Code Review card went back to Doing: {log}"


@pytest.mark.heavy
def test_a_run_that_delivered_nothing_still_sends_a_code_review_card_to_todo(tmp_path):
    sandbox(tmp_path, "echo 0", card="Code Review")
    janitor(tmp_path, **SHIPPED)
    log = writes(tmp_path)
    assert f"STATUS o 4 Todo {N}" in log, f"the requeue did not send the card to Todo: {log}"


@pytest.mark.heavy
def test_the_wave_entry_still_moves_a_code_review_card_to_code_complete(tmp_path):
    sandbox(tmp_path, "echo 0", landed=True, wave="open", entered=True, card="Code Review")
    janitor(tmp_path, **SHIPPED)
    log = writes(tmp_path)
    assert f"STATUS o 4 Landing {N}" in landing(log), f"the entry did not move the card to Landing: {log}"


@pytest.mark.heavy
def test_a_finished_run_still_reads_its_card_on_the_pass_that_drops_its_line(tmp_path):
    """#2144: a run whose verdict is final leaves active-runs, and the same pass still reads it for its card.

    The drop ran before the card pass read the run, so the issue sat at no column and nothing moved it again. The journal
    proves the order: the pass names the drop, the card pass names the run it read after that drop, and one invocation is
    one pass, so the card read the run this pass dropped rather than a later pass's."""
    sandbox(tmp_path, "echo 0", landed=True)
    janitor(tmp_path)
    journal = (tmp_path / "st/cycle.log").read_text()
    log = writes(tmp_path)
    lines = journal.splitlines()
    drop = [k for k, line in enumerate(lines) if "FINISHED-RUN run-stacked" in line]
    card = [k for k, line in enumerate(lines) if "FINISHED-RUN-CARD run-stacked" in line]
    assert len(drop) == 1, f"the finished run never left active-runs on this pass, once: {journal}"
    assert len(card) == 1, (
        f"the card pass did not read the run whose line this pass dropped, so its card stays at no column: {journal}")
    assert drop[0] < card[0], f"the card read stands before the drop that pass made: {journal}"
    between = lines[drop[0] + 1:card[0]]
    assert not [line for line in between if " janitor pass " in line], (
        f"a pass boundary stands between the drop and the card read: the card pass read a run a later pass dropped: {journal}")
    assert f"ADD o 4 Landing {N}" in landing(log), f"the card did not reach Landing: {log}"
    assert (tmp_path / "st/active-runs.txt").read_text().split() == [], (
        "the finished run kept its active-runs line, so it still holds the child room rule #2138 won")
