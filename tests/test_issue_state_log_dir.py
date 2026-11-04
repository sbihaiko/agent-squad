"""The board seam's api.log lines go to $SG_STATE, the loop's one call log, never to the seam's own state dir (#1328).

(a) A seam built with its own state_dir under a live SG_STATE logs its reads to SG_STATE, so the hub's quota spend stays in
    the loop's api.log (one log per quota, #847).
(b) A write the seam sends and GitHub takes is one line with outcome `done`, and adds no snapshot-eligible miss to the
    gh-miss-rate that ghspend.py reads.
The carrier is a stub gh; no test writes live.
"""
import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path

SCRIPTS = Path(__file__).parents[1] / "contrib" / "squad-goal" / "scripts"
SCRIPT = SCRIPTS / "issue_state.py"
SPEC = importlib.util.spec_from_file_location("issue_state_log_dir", SCRIPT)
issue_state = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(issue_state)
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import ghspend  # noqa: E402


def stub(cmd, input=None, **kw):
    """A gh stub that answers every call with an empty issue, and exits 0."""
    return subprocess.CompletedProcess(cmd, 0, json.dumps({"number": 10, "labels": []}), "")


def test_a_seam_read_under_an_inherited_loop_state_logs_to_the_loop_state(tmp_path, monkeypatch):
    loop_state = tmp_path / "live-loop"
    own_state = tmp_path / "test-seam"
    loop_state.mkdir()
    monkeypatch.setenv("SG_STATE", str(loop_state))

    state = issue_state.board_state("o", "4", "x/y", kind="users", state_dir=own_state, run=stub,
                                    env={"SG_BOARD_READ_BACKOFF": "0"})
    state.binding.api("repos/x/y/issues/10")

    assert not (own_state / "api.log").exists()    # the seam's own dir gets no call log line
    assert len((loop_state / "api.log").read_text().splitlines()) == 1   # the loop's log has the one read


def test_a_seam_write_is_one_done_line_and_no_snapshot_miss(tmp_path, monkeypatch):
    loop_state = tmp_path / "live-loop"
    loop_state.mkdir()
    monkeypatch.setenv("SG_STATE", str(loop_state))

    state = issue_state.board_state("o", "4", "x/y", kind="users", state_dir=tmp_path / "test-seam", run=stub,
                                    env={"SG_BOARD_READ_BACKOFF": "0"})
    for n in range(25):   # more writes than the miss rate's fewest-calls floor, so a counted write would raise the cause
        state.binding.api("-X", "POST", "repos/x/y/milestones", "--input", "-", inp=json.dumps({"title": f"m{n}"}))

    lines = (loop_state / "api.log").read_text().splitlines()
    assert len(lines) == 25                                  # one line per spent call (ADR-0277)
    assert all(line.endswith(" done unattributed") for line in lines)   # no SG_GH_PASS: the spend is unattributed
    assert ghspend._miss_rate(loop_state, time.time()) is None   # a write GitHub took is not a snapshot miss
