"""A card the loop stranded past Todo goes back where a dispatch takes it, and a stopped issue is named as stopped (#1958).

Three causes stranded cards, one case group each:

- #1837: a requeue wrote Todo and dropped the marker, then the pass read an older `(#N)` commit in the landing branch and
  wrote Code Complete over it. A no-landing requeue or a run end that is newer than the named commit wins over it; a hand
  fix with no later requeue or run keeps Code Complete (#519).
- #1754: rule O said the issue "dispatches again" for an issue its ledger had stopped, and an open wave over a stopped
  issue read HELD-BY-WAVE. The line names the stop instead.
- The class remover: an open issue whose card sits in Code Review or Code Complete with no live run, no marker, no
  remote branch ahead of the landing branch, no freeze and no landing goes back to Todo with `no-landing` and one STRANDED
  line (the #1034 recovery, widened).

These cases drive janitor.py end to end: a stub `gh` on PATH, a temp state dir, and a real checkout with a bare origin.
No case touches the network, the live loop state or the live board.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import time
from pathlib import Path

import pytest

from tests.gitrepo import make_repo, sh

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "contrib/squad-goal/scripts"
N = 2201

STUB = """#!/bin/bash
a="$*"; echo "$a" >> "@CALLS@"
labels=$(cat "@LABELS@" 2>/dev/null)
case "$a" in
  *graphql*)
    nodes=""
    for l in $labels; do nodes="$nodes${nodes:+,}{\\"name\\":\\"$l\\"}"; done
    printf '{"data":{"repository":{"issues":{"pageInfo":{"hasNextPage":false},"nodes":[{"number":@N@,"title":"Issue","body":"","state":"OPEN","labels":{"nodes":[%s]},"subIssuesSummary":{"total":0,"completed":0},"subIssues":{"nodes":[]},"comments":{"nodes":[]}}]}}}}' "$nodes";;
  *search/issues*)
    case "$a" in *"-X GET"*|*"--method GET"*) ;; *) echo '{"message":"Not Found"}'; exit 1;; esac
    echo 0;;
  *projectsV2/*/fields*) echo FID4;;
  *projectsV2/*/items*) printf '%s\\t%s\\thttps://api.github.com/repos/x/y\\n' @N@ "$(cat "@CARD@")";;
  *issues/*"@tsv"*) printf 'open\\t\\tIssue\\t%s\\t\\n' "$(echo $labels | tr ' ' ,)";;
  *issues/*"-q .state"*) echo open;;
  *issues/*) echo -e 'open\\t\\tIssue';;
esac
"""

BOARD = """#!/bin/bash
case "$1" in
  --add) echo "ADD $2 $3 $4 $5" >> "@CALLS@";;
  --run-fields|--clear-run) :;;
  *) echo "STATUS $1 $2 $3 $4 reason=${SG_MOVE_REASON:-}" >> "@CALLS@";;
esac
"""

HOUR = 3600


def _write(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def sandbox(tmp_path: Path, *, card: str, named: bool = False, labels: str = "") -> Path:
    """The janitor beside its modules, a stub `gh` and board, and a checkout with a bare origin that holds `main`.
    `card` is the column the board reads for #2201. `named` puts a fix commit `(#2201)`, two hours old, in origin/main."""
    for name in ("janitor.py", "ghcall.sh", "ghthrottle.py", "ghread.py", "issue_state.py", "board_columns.py",
                 "board-definition.json", "board_facts.py", "spec_fold.py", "issue_ledger.py", "wip_snapshot.py",
                 "skiplabels.sh"):
        shutil.copy(SCRIPTS / name, tmp_path / name)
    (tmp_path / "bin").mkdir()
    calls = tmp_path / "calls"
    (tmp_path / "card").write_text(card)
    (tmp_path / "labels").write_text(labels)
    _write(tmp_path / "bin/gh", STUB.replace("@N@", str(N)).replace("@CALLS@", str(calls))
           .replace("@CARD@", str(tmp_path / "card")).replace("@LABELS@", str(tmp_path / "labels")))
    _write(tmp_path / "board.sh", BOARD.replace("@CALLS@", str(calls)))
    co = make_repo(tmp_path / "co")
    if named:
        (co / "fix.txt").write_text("a part of the fix\n")
        sh(co, "git", "add", "fix.txt")
        stamp = f"@{int(time.time()) - 2 * HOUR} +0000"
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", f"a part of the fix (#{N})"],
                       cwd=co, check=True, env={**os.environ, "GIT_COMMITTER_DATE": stamp, "GIT_AUTHOR_DATE": stamp})
    origin = tmp_path / "origin.git"
    sh(tmp_path, "git", "init", "-q", "--bare", str(origin))
    sh(co, "git", "remote", "add", "origin", str(origin))
    sh(co, "git", "push", "-q", "origin", "main")
    sh(co, "git", "fetch", "-q", "origin")
    (tmp_path / "st/auto").mkdir(parents=True)
    (tmp_path / "st/active-runs.txt").write_text("")
    return tmp_path


def finished_run(tmp_path: Path, *, verdict: str = "failed", wave: bool = False, marker: bool = True) -> Path:
    """One run that ended now with nothing landed for #2201: its verdict is newer than any fixture commit."""
    run = tmp_path / "co/runs/run-end"
    run.mkdir(parents=True)
    (run / "request.txt").write_text(f"CHILD — GitHub issue(s) {N}:\n")
    now = time.time() - 600
    (run / "record.jsonl").write_text(
        json.dumps({"event": "run_start", "run_id": "run-end", "ts": now - 60}) + "\n"
        + json.dumps({"event": "verdict", "verdict": verdict, "reasons": ["a child failed"], "exit_code": 1, "ts": now}) + "\n"
    )
    for line in (run / "record.jsonl").read_text().splitlines():
        assert json.loads(line)["event"] in ("run_start", "verdict"), f"a record line drifted: {line}"
    (tmp_path / "st/active-runs.txt").write_text("run-end\n")
    auto = tmp_path / "st/auto"
    if marker:
        (auto / f"disp-{N}").write_text("run-end\n")
    if wave:
        (auto / "wave-run-end").write_text("wave/run-end\n")
        (auto / "waved-run-end").write_text("wave/run-end\n")
    old = time.time() - HOUR   # past every settle window
    for p in [*run.rglob("*"), run, *auto.iterdir()]:
        os.utime(p, (old, old))
    return run


def ledger(tmp_path: Path, *args: str) -> str:
    env = {**os.environ, "SG_STATE": str(tmp_path / "st"), "SG_REPO": "x/y"}
    r = subprocess.run(["python3", "-I", str(tmp_path / "issue_ledger.py"), *args], capture_output=True, text=True,
                       env=env, check=True)
    return r.stdout.strip()


def janitor(tmp_path: Path, **extra: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("SG_", "FG_"))}
    env.update(
        {
            "PATH": f"{tmp_path / 'bin'}:{env.get('PATH', '')}",
            "SG_STATE": str(tmp_path / "st"),
            "SG_CHECKOUT": str(tmp_path / "co"),
            "SG_REPO": "x/y",
            "SG_OWNER": "o",
            "SG_PROJECT": "4",
            "SG_BASE": "main",
            "SG_LAND_BRANCH": "main",
            "SG_HUB_DIR": str(tmp_path / "st"),
            "SG_GH_AGGREGATE_WINDOW": "0",
            "SG_GH_CACHE_WINDOW": "0",
            "SG_SQUAD": str(ROOT),
            **extra,
        }
    )
    return subprocess.run(["python3", "-I", str(tmp_path / "janitor.py")], capture_output=True, text=True, env=env,
                          check=False)


def writes(tmp_path: Path) -> str:
    f = tmp_path / "calls"
    return "".join(x + "\n" for x in (f.read_text().splitlines() if f.exists() else []) if x.startswith(("STATUS", "ADD")))


def landing(log: str) -> str:
    """The write lines that name the Landing column of #N, in either spelling (ADR-0170, #2155): the definition names it
    `Landing`, and a version 1 board spells the same column `Code Complete`. A reader folds the two, so a guard that
    pinned one spelling would pass over a write in the other."""
    return "\n".join(line for line in log.splitlines() if f" Landing {N}" in line or f" Code Complete {N}" in line)


def decided(tmp_path: Path) -> str:
    f = tmp_path / "st/cycle.log"
    return f.read_text() if f.exists() else ""


# --- #1837: the named commit yields to a newer requeue or run end ---------------------------------------------------


@pytest.mark.heavy
def test_a_hand_fix_with_no_later_requeue_keeps_code_complete(tmp_path):
    """#519 stands: a `(#N)` commit in the landing branch with no later requeue or run is Code Complete."""
    sandbox(tmp_path, card="Todo", named=True)
    janitor(tmp_path)
    assert f"STATUS o 4 Landing {N}" in landing(writes(tmp_path)), f"a hand fix lost Landing: {writes(tmp_path)}"


@pytest.mark.heavy
def test_a_requeue_newer_than_the_named_commit_keeps_the_card_in_todo(tmp_path):
    """#1837: the requeue wrote Todo after the named commit; the older commit never writes Code Complete over it."""
    sandbox(tmp_path, card="Todo", named=True)
    assert ledger(tmp_path, "requeue", f"x/y#{N}", "run-requeued", "abc123") == "recorded"
    janitor(tmp_path)
    assert landing(writes(tmp_path)) == "", f"an older named commit overwrote the requeue: {writes(tmp_path)}"


@pytest.mark.heavy
def test_a_run_end_newer_than_the_named_commit_wins_over_it(tmp_path):
    """A run that ended with nothing landed after the named commit returns the card to Todo, never Code Complete."""
    sandbox(tmp_path, card="Doing", named=True)
    finished_run(tmp_path)
    janitor(tmp_path)
    log = writes(tmp_path)
    assert landing(log) == "", f"an older named commit overwrote the run end: {log}"
    assert f"STATUS o 4 Todo {N} reason=no-landing" in log, f"the run end did not return the card: {log}"


# --- #1754: rule O names the stop ------------------------------------------------------------------------------------


@pytest.mark.heavy
def test_a_stopped_issue_is_not_said_to_dispatch_again(tmp_path):
    """The ledger stopped the issue: rule O drops the marker and says the issue is stopped, never that it dispatches."""
    sandbox(tmp_path, card="Doing")
    finished_run(tmp_path)
    ledger(tmp_path, "stop", f"x/y#{N}", "three failed dispatches with no change", "own")
    janitor(tmp_path)
    line = next((x for x in decided(tmp_path).splitlines() if f"DISPATCH-FAILED #{N}" in x), "")
    assert line, f"rule O did not decide the run: {decided(tmp_path)}"
    assert "dispatches again" not in line, f"a stopped issue was said to dispatch again: {line}"
    assert "stopped" in line, f"the line does not name the stop: {line}"
    notes = "".join(p.read_text() for p in (tmp_path / "st/cq").glob("*dispatchfail*"))
    assert notes and "dispatches the issue again" not in notes, f"the issue comment says it dispatches again: {notes}"


@pytest.mark.heavy
def test_an_open_wave_over_a_stopped_issue_names_the_stop(tmp_path):
    """The issue is stopped and an old run's wave is still open: the line names the stop, not HELD-BY-WAVE."""
    sandbox(tmp_path, card="Landing")
    finished_run(tmp_path, wave=True)
    ledger(tmp_path, "stop", f"x/y#{N}", "three failed dispatches with no change", "own")
    janitor(tmp_path)
    log = decided(tmp_path)
    assert f"HELD-BY-WAVE #{N}" not in log, f"a stopped issue read as held by its wave: {log}"
    assert f"HELD-STOPPED #{N}" in log, f"the stop is not named: {log}"


@pytest.mark.heavy
def test_an_open_wave_over_a_live_issue_still_reads_held_by_wave(tmp_path):
    """The other side: an issue no ledger stopped keeps its HELD-BY-WAVE line."""
    sandbox(tmp_path, card="Landing")
    finished_run(tmp_path, wave=True)
    janitor(tmp_path)
    assert f"HELD-BY-WAVE #{N}" in decided(tmp_path), decided(tmp_path)


# --- #1987: the stale-Doing recovery reads recorded dispatch evidence, never a wave label ---------------------------


@pytest.mark.heavy
def test_a_doing_card_with_recorded_dispatch_returns_to_todo(tmp_path):
    """The issue's ledger records that a run dispatched it: the card goes back to Todo with the STALE-CARD line."""
    sandbox(tmp_path, card="Doing")
    ledger(tmp_path, "wave", f"x/y#{N}", "run-old", "entered", "the child entered the wave")
    janitor(tmp_path, SG_BOARD_VOCAB="shipped")
    assert f"STATUS o 4 Todo {N}" in writes(tmp_path), writes(tmp_path) + decided(tmp_path)
    assert f"STALE-CARD #{N}" in decided(tmp_path), decided(tmp_path)


@pytest.mark.heavy
def test_a_doing_card_with_a_wave_label_and_no_recorded_dispatch_is_left_alone(tmp_path):
    """The label is no longer proof that the loop owns the card: with no recorded dispatch the card stays."""
    sandbox(tmp_path, card="Doing", labels="wave:3")
    janitor(tmp_path, SG_BOARD_VOCAB="shipped")
    assert f"Todo {N}" not in writes(tmp_path), writes(tmp_path) + decided(tmp_path)
    assert f"DOING-NO-RUN #{N}" in decided(tmp_path), decided(tmp_path)


@pytest.mark.heavy
def test_a_doing_card_with_no_dispatch_evidence_is_left_alone(tmp_path):
    """A hand placement looks the same: no label and no recorded dispatch keeps the card where the owner put it."""
    sandbox(tmp_path, card="Doing")
    janitor(tmp_path, SG_BOARD_VOCAB="shipped")
    assert f"Todo {N}" not in writes(tmp_path), writes(tmp_path) + decided(tmp_path)
    assert f"DOING-NO-RUN #{N}" in decided(tmp_path), decided(tmp_path)


# --- the class remover: a stranded Code Review or Code Complete card ------------------------------------------------


@pytest.mark.heavy
def test_a_stranded_code_complete_card_returns_to_todo(tmp_path):
    """No run, no marker, no branch, no freeze, no landing: the card goes back to Todo, named no-landing and STRANDED."""
    sandbox(tmp_path, card="Landing")
    janitor(tmp_path)
    assert f"STATUS o 4 Todo {N} reason=no-landing" in writes(tmp_path), writes(tmp_path) + decided(tmp_path)
    assert f"STRANDED #{N}" in decided(tmp_path), decided(tmp_path)


@pytest.mark.heavy
def test_a_stranded_code_review_card_returns_to_todo(tmp_path):
    """The shipped column Review strands the same way."""
    sandbox(tmp_path, card="Review")
    janitor(tmp_path, SG_BOARD_VOCAB="shipped")
    assert f"STATUS o 4 Todo {N} reason=no-landing" in writes(tmp_path), writes(tmp_path) + decided(tmp_path)
    assert f"STRANDED #{N}" in decided(tmp_path), decided(tmp_path)


@pytest.mark.heavy
def test_a_frozen_card_is_not_stranded(tmp_path):
    sandbox(tmp_path, card="Landing", labels="frozen:owner")
    janitor(tmp_path)
    assert f"Todo {N}" not in writes(tmp_path), writes(tmp_path)
    assert f"STRANDED #{N}" not in decided(tmp_path)


@pytest.mark.heavy
def test_a_card_with_a_hand_fix_in_the_landing_branch_is_not_stranded(tmp_path):
    sandbox(tmp_path, card="Landing", named=True)
    janitor(tmp_path)
    assert f"Todo {N}" not in writes(tmp_path), writes(tmp_path)
    assert f"STRANDED #{N}" not in decided(tmp_path)


@pytest.mark.heavy
def test_a_card_with_a_kept_marker_is_not_stranded(tmp_path):
    sandbox(tmp_path, card="Landing")
    (tmp_path / f"st/auto/kept-fix_{N}-rebase-run-x").write_text("origin/wave/run-x\n")
    janitor(tmp_path)
    assert f"Todo {N}" not in writes(tmp_path), writes(tmp_path)
    assert f"STRANDED #{N}" not in decided(tmp_path)


# --- #2352: the stale-card rule reads the skip labels the frontier reads ---------------------------------------------


@pytest.mark.heavy
def test_a_stale_card_whose_issue_carries_a_skip_label_moves_nothing(tmp_path):
    """A Doing card with a recorded dispatch, no live run, no marker and nothing landed moves nothing when the frontier
    refuses its issue for a skip label, and the rule names that label once."""
    sandbox(tmp_path, card="Doing", labels="ready-for-HAL9K")
    ledger(tmp_path, "wave", f"x/y#{N}", "run-old", "entered", "the child entered the wave")
    janitor(tmp_path, SG_BOARD_VOCAB="shipped")
    assert str(N) not in writes(tmp_path), writes(tmp_path)
    assert f"STALE-CARD #{N}" not in decided(tmp_path), decided(tmp_path)
    skipped = [x for x in decided(tmp_path).splitlines() if f"SKIP-LABEL #{N}" in x]
    assert len(skipped) == 1 and "ready-for-HAL9K" in skipped[0], decided(tmp_path)
