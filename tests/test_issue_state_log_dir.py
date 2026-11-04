"""The board seam logs its api reads to its own state dir, never to an inherited loop state (#1328).

A seam built with an explicit state_dir keeps that dir for every line it writes. A test that runs under a live
SG_STATE must not put its stub reads into the live loop's api.log, where the gh-miss-rate alert counts them.
The carrier is a stub gh; no test writes live.
"""
import importlib.util
import json
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "contrib" / "squad-goal" / "scripts" / "issue_state.py"
SPEC = importlib.util.spec_from_file_location("issue_state_log_dir", SCRIPT)
issue_state = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(issue_state)


def stub(cmd, input=None, **kw):
    """A gh stub that answers every read with an empty issue."""
    return subprocess.CompletedProcess(cmd, 0, json.dumps({"number": 10, "labels": []}), "")


def test_a_seam_read_under_an_inherited_loop_state_logs_to_its_own_dir(tmp_path, monkeypatch):
    loop_state = tmp_path / "live-loop"
    own_state = tmp_path / "test-seam"
    loop_state.mkdir()
    monkeypatch.setenv("SG_STATE", str(loop_state))

    state = issue_state.board_state("o", "4", "x/y", kind="users", state_dir=own_state, run=stub,
                                    env={"SG_BOARD_READ_BACKOFF": "0"})
    state.binding.api("repos/x/y/issues/10")

    assert not (loop_state / "api.log").exists()   # the live loop's log stays clean
    assert (own_state / "api.log").exists()        # the seam's own dir has the line
    assert len((own_state / "api.log").read_text().splitlines()) == 1   # one read, one line
