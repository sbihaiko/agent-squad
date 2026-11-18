"""The board pass reads a hand hold and a takeover marker, and moves the card once (loop-bug #1664).

An issue a live `auto/taken-<run>` marker or an `auto/hold-<n>` names moves Todo to Doing once, through `board_write`, and
the pass never moves it back while the marker or hold lives. An `auto/release-<n>` record moves the card to the column it
names, once, and the pass then removes the record. An issue with no marker, no hold and no run is never moved. The hold is
written by `python3 -m squad.handhold`, the janitor reads it through the same module.

These cases drive janitor.py with the sandbox of test_janitor_card_done_base: a stub `gh`, a stub board.sh and a temp state
dir. No case touches the network, the live loop state or the live board.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.test_janitor_card_done_base import N, janitor, sandbox, writes

ROOT = Path(__file__).resolve().parents[1]
SHIPPED = {"SG_BOARD_VOCAB": "shipped"}


def bare(tmp_path: Path, card: str, **extra) -> Path:
    """The sandbox with its run gone: one open issue, its card in `card`, no run, no dispatch marker."""
    sandbox(tmp_path, "echo 0", card=card, **extra)
    (tmp_path / "st/active-runs.txt").write_text("")
    (tmp_path / f"st/auto/disp-{N}").unlink()
    return tmp_path / "st"


FORWARD_ONLY = """#!/bin/bash
case "$1" in
  --add) echo "ADD $2 $3 $4 $5" >> "@CALLS@";;
  --run-fields|--clear-run) :;;
  *) echo "STATUS $1 $2 $3 $4" >> "@CALLS@"; case "$3" in Todo|Backlog) exit 3;; esac;;
esac
"""


def forward_only(tmp_path: Path) -> None:
    """The board stub refuses a move back to Todo (`exit 3`, as board.py does, ADR-0143), so a pass that asks one is
    named in `calls` and refused there."""
    p = tmp_path / "board.sh"
    p.write_text(FORWARD_ONLY.replace("@CALLS@", str(tmp_path / "calls")))
    p.chmod(0o755)


def hand(tmp_path: Path, *argv: str) -> subprocess.CompletedProcess[str]:
    """The hold CLI in the sandbox: the stub `gh` on PATH answers the holder label write and records it in `calls`, and no
    live SG_* setting leaks in."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("SG_")}
    env.update({
        "SG_STATE": str(tmp_path / "st"), "SG_CHECKOUT": str(tmp_path / "co"), "SG_REPO": "x/y", "PYTHONPATH": str(ROOT),
        "PATH": f"{tmp_path / 'bin'}:{env.get('PATH', '')}",
    })
    return subprocess.run([sys.executable, "-m", "squad.handhold", *argv], capture_output=True, text=True, env=env, cwd=ROOT, check=False)


def moves(tmp_path: Path) -> list[str]:
    return [line for line in writes(tmp_path).splitlines() if line.startswith(("STATUS", "ADD"))]


@pytest.mark.heavy
@pytest.mark.parametrize("by", ["manual", "hal9k"])
def test_a_hand_hold_moves_its_todo_card_to_doing_once(tmp_path, by):
    bare(tmp_path, "Todo")
    assert hand(tmp_path, "hold", str(N), "--by", by).returncode == 0
    janitor(tmp_path, **SHIPPED)
    assert moves(tmp_path) == [f"STATUS o 4 Doing {N}"], writes(tmp_path)


@pytest.mark.heavy
@pytest.mark.parametrize("card", ["Doing", "Review", "Landing"])
def test_a_pass_over_a_live_hold_writes_nothing_and_never_moves_the_card_back(tmp_path, card):
    bare(tmp_path, card)
    assert hand(tmp_path, "hold", str(N)).returncode == 0
    janitor(tmp_path, **SHIPPED)
    janitor(tmp_path, **SHIPPED)
    assert moves(tmp_path) == [], writes(tmp_path)


@pytest.mark.heavy
def test_a_taken_marker_moves_its_todo_card_to_doing_once(tmp_path):
    st = bare(tmp_path, "Todo")
    (st / "auto/taken-run-gone").write_text(f"run run-gone\nissues {N}\n")
    janitor(tmp_path, **SHIPPED)
    assert moves(tmp_path) == [f"STATUS o 4 Doing {N}"], writes(tmp_path)


@pytest.mark.heavy
def test_a_taken_marker_never_lets_a_card_go_back_to_todo(tmp_path):
    """The reoffer marker a run-end path wrote asks for Todo; the live marker outranks it."""
    st = bare(tmp_path, "Doing")
    (st / "auto/taken-run-gone").write_text(f"run run-gone\nissues {N}\n")
    (st / f"auto/reoffer-{N}").write_text("run-gone\n")
    janitor(tmp_path, **SHIPPED)
    assert moves(tmp_path) == [], writes(tmp_path)


@pytest.mark.heavy
@pytest.mark.parametrize(("column", "card"), [("Landing", "Doing"), ("Todo", "Doing")])
def test_a_release_moves_the_card_once_and_its_record_goes(tmp_path, column, card):
    st = bare(tmp_path, card)
    assert hand(tmp_path, "hold", str(N)).returncode == 0
    assert hand(tmp_path, "release", str(N), "--to", column).returncode == 0
    assert (st / f"auto/release-{N}").exists()
    janitor(tmp_path, **SHIPPED)
    assert moves(tmp_path) == [f"STATUS o 4 {column} {N}"], writes(tmp_path)
    assert not (st / f"auto/release-{N}").exists(), "the record stayed: it would apply twice"


def gh_answer(tmp_path: Path, *argv: str) -> str:
    """The sandbox's stub `gh` answer to one read, asked through the stub's own PATH: the pass reads the issue and the
    board this way, so a case states what the board answers before the pass runs."""
    env = {**os.environ, "PATH": f"{tmp_path / 'bin'}:{os.environ.get('PATH', '')}"}
    return subprocess.run(["gh", *argv], capture_output=True, text=True, env=env, check=False).stdout


def card_column(tmp_path: Path) -> str:
    """The column the board answers for the issue's card: the stub's one item line is `<n>\\t<column>\\t<url>`."""
    line = next((row for row in gh_answer(tmp_path, "api", "projectsV2/1/items").splitlines()
                 if row.startswith(f"{N}\t")), "")
    return line.split("\t")[1] if line.count("\t") >= 2 else ""


@pytest.mark.heavy
def test_a_release_record_for_a_closed_issue_is_dropped_never_refused(tmp_path):
    """#1869: the release record outranks the closed-issue rule, so it asked for Todo on a card a closed issue holds. The
    board refuses a move back (ADR-0143), and the record stayed for every pass to refuse again. A release for a closed
    issue is dropped: it writes nothing, and it goes."""
    st = bare(tmp_path, "Closed", closed=True)
    assert gh_answer(tmp_path, "api", f"repos/x/y/issues/{N}", "-q", ".state").strip() == "closed", "the issue is not closed"
    assert card_column(tmp_path) == "Closed", "the card is not in the Closed column before the pass runs"
    assert hand(tmp_path, "hold", str(N)).returncode == 0
    assert hand(tmp_path, "release", str(N), "--to", "Todo").returncode == 0
    janitor(tmp_path, **SHIPPED)
    janitor(tmp_path, **SHIPPED)
    assert moves(tmp_path) == [], writes(tmp_path)
    assert not (st / f"auto/release-{N}").exists(), "the record stayed: every pass asks for the move again"
    log = (st / "cycle.log").read_text()
    assert "HAND-RELEASE-DROPPED" in log, f"the pass did not say it dropped the record: {log}"


@pytest.mark.heavy
def test_a_release_record_for_an_open_issue_still_moves_its_card(tmp_path):
    """The drop is the closed issue's: a release for an open issue keeps its one move."""
    st = bare(tmp_path, "Doing")
    assert hand(tmp_path, "hold", str(N)).returncode == 0
    assert hand(tmp_path, "release", str(N), "--to", "Landing").returncode == 0
    janitor(tmp_path, **SHIPPED)
    assert moves(tmp_path) == [f"STATUS o 4 Landing {N}"], writes(tmp_path)
    assert not (st / f"auto/release-{N}").exists()


@pytest.mark.heavy
def test_a_released_todo_card_is_never_moved_again_by_the_pass(tmp_path):
    """After the release's own move there is no hold and no record: a second pass over a Todo card writes nothing."""
    st = bare(tmp_path, "Todo")
    assert hand(tmp_path, "hold", str(N)).returncode == 0
    assert hand(tmp_path, "release", str(N), "--to", "Todo").returncode == 0
    janitor(tmp_path, **SHIPPED)
    janitor(tmp_path, **SHIPPED)
    assert moves(tmp_path) == [], writes(tmp_path)
    assert not (st / f"auto/release-{N}").exists()


@pytest.mark.heavy
def test_a_release_record_for_a_closed_issue_writes_nothing_and_is_dropped(tmp_path):
    """#1869: the closed-issue rule names the card column (ADR-0143 rule 2), so a release record for a closed issue has
    no move to make. The record goes and the pass writes nothing: a record that outranked the closure asked for Todo on a
    Closed card, the board refused the backward move, and every pass asked it again."""
    st = bare(tmp_path, "Closed", closed=True)
    forward_only(tmp_path)
    assert hand(tmp_path, "hold", str(N)).returncode == 0
    assert hand(tmp_path, "release", str(N), "--to", "Todo").returncode == 0
    assert (st / f"auto/release-{N}").exists()
    janitor(tmp_path, **SHIPPED)
    assert moves(tmp_path) == [], writes(tmp_path)
    assert not (st / f"auto/release-{N}").exists(), "the record stayed: every pass asks the same refused move"
    janitor(tmp_path, **SHIPPED)
    assert moves(tmp_path) == [], writes(tmp_path)


@pytest.mark.heavy
def test_an_issue_with_no_marker_no_hold_and_no_run_is_never_moved(tmp_path):
    bare(tmp_path, "Todo")
    janitor(tmp_path, **SHIPPED)
    assert moves(tmp_path) == [], writes(tmp_path)


@pytest.mark.heavy
def test_a_hold_on_an_issue_a_live_run_holds_is_refused_and_the_run_moves_its_card_once(tmp_path):
    sandbox(tmp_path, "echo 0", card="Todo", live=True)
    run = tmp_path / "co/runs/run-stacked"
    (run / "record.jsonl").write_text(json.dumps({"event": "run_start", "run_id": "run-stacked", "ts": time.time()}) + "\n")
    for p in (run / "record.jsonl", run, tmp_path / f"st/auto/disp-{N}", tmp_path / "st/auto"):   # the run just started
        os.utime(p, None)
    done = hand(tmp_path, "hold", str(N))
    assert done.returncode == 1 and "run-stacked" in done.stderr, (done.stdout, done.stderr)
    assert not (tmp_path / f"st/auto/hold-{N}").exists()
    janitor(tmp_path, **SHIPPED)
    assert moves(tmp_path) == [f"STATUS o 4 Doing {N}"], writes(tmp_path)


@pytest.mark.heavy
def test_the_hold_cli_writes_the_holder_label_and_the_pass_never_removes_it(tmp_path):
    """#1678: one act writes the file and the label, and no pass or janitor rule removes a holder label."""
    bare(tmp_path, "Todo")
    assert hand(tmp_path, "hold", str(N), "--by", "hal9k").returncode == 0
    log = writes(tmp_path)
    assert f"POST repos/x/y/issues/{N}/labels" in log and "ready-for-HAL9K" in log, log
    janitor(tmp_path, **SHIPPED)
    clears = [line for line in writes(tmp_path).splitlines() if "DELETE" in line and "/labels" in line]
    assert clears == [], f"a pass removed a holder label: {clears}"


@pytest.mark.heavy
def test_the_release_cli_clears_the_holder_label_and_the_file(tmp_path):
    st = bare(tmp_path, "Closed")
    assert hand(tmp_path, "hold", str(N)).returncode == 0
    assert hand(tmp_path, "release", str(N), "--to", "Closed").returncode == 0
    log = writes(tmp_path)
    assert f"DELETE repos/x/y/issues/{N}/labels/ready-for-human" in log, log
    assert not (st / f"auto/hold-{N}").exists()


@pytest.mark.heavy
def test_a_stale_hold_on_a_closed_issue_is_released_to_closed_by_the_pass_with_one_log_line(tmp_path):
    st = bare(tmp_path, "Closed", closed=True)
    (st / f"auto/hold-{N}").write_text("by hal9k\nsince 2020-01-01T00:00:00Z\nuntil 2020-01-01T01:00:00Z\n")
    janitor(tmp_path, **SHIPPED)
    janitor(tmp_path, **SHIPPED)
    assert not (st / f"auto/hold-{N}").exists(), "the stale hold stayed on a closed issue"
    assert not (st / f"auto/release-{N}").exists(), "the release record stayed: the pass would name it twice"
    log = (st / "cycle.log").read_text()
    assert log.count(f"HOLD-RELEASED #{N}:") == 1, log
    assert f"DELETE repos/x/y/issues/{N}/labels/ready-for-HAL9K" in writes(tmp_path)
