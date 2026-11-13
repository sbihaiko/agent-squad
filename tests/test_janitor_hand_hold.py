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


def bare(tmp_path: Path, card: str) -> Path:
    """The sandbox with its run gone: one open issue, its card in `card`, no run, no dispatch marker."""
    sandbox(tmp_path, "echo 0", card=card)
    (tmp_path / "st/active-runs.txt").write_text("")
    (tmp_path / f"st/auto/disp-{N}").unlink()
    return tmp_path / "st"


def hand(tmp_path: Path, *argv: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "SG_STATE": str(tmp_path / "st"), "SG_CHECKOUT": str(tmp_path / "co"), "PYTHONPATH": str(ROOT)}
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
@pytest.mark.parametrize("card", ["Doing", "Code Review", "Code Complete"])
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
@pytest.mark.parametrize(("column", "card"), [("Code Complete", "Doing"), ("Todo", "Doing")])
def test_a_release_moves_the_card_once_and_its_record_goes(tmp_path, column, card):
    st = bare(tmp_path, card)
    assert hand(tmp_path, "hold", str(N)).returncode == 0
    assert hand(tmp_path, "release", str(N), "--to", column).returncode == 0
    assert (st / f"auto/release-{N}").exists()
    janitor(tmp_path, **SHIPPED)
    assert moves(tmp_path) == [f"STATUS o 4 {column} {N}"], writes(tmp_path)
    assert not (st / f"auto/release-{N}").exists(), "the record stayed: it would apply twice"


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
