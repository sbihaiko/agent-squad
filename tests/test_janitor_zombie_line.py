"""Rule C and the health pass's reap leave the same active-runs state for the same dead run (#1230, ADR-0149).

Both passes write the killed verdict for a run whose process is gone. The readers of `active-runs.txt` need the finished
line after that verdict: `watch.sh` lands the wave of a run whose listed record carries a verdict, `childcc.py` enters its
finished children into that wave, and janitor rules O and R release the run's issues and rank their cards from it. A line
dropped with the verdict hides the run from all of them. `caps.run_holders` frees the slot from the verdict, so the line
costs no slot. One writer removes a finished line: `wtclean.sh` rule A, once the verdict is an hour old and the wave is
closed. Rule C keeps the line, as `squad/reap.py` does.

The janitor runs end to end in the sandbox of test_janitor_card_done_base: a stub `gh`, a stub board.sh, a temp state dir.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from squad import caps, record
from tests.test_janitor_card_done_base import janitor, sandbox

ROOT = Path(__file__).resolve().parents[1]
RUN = "run-stacked"


def dead_pid() -> int:
    p = subprocess.Popen(["true"])
    p.wait()
    return p.pid


def dead_run(tmp_path: Path, folder: Path, pid: int) -> None:
    """The same dead run in any folder: started, one call by engine `pid` an hour ago, no verdict, record idle an hour."""
    rec = folder / "record.jsonl"
    rec.unlink(missing_ok=True)
    r = record.Record(rec)
    r.append("run_start", run_id=RUN, graph_name="g", graph_hash="h", engine_options={}, root="main", plan=["main"])
    r.append("call_start", node="main", entry=1, session="s", pid=pid, limit=None, started_at=time.time() - 3600)
    old = time.time() - 3600
    for p in (rec, folder / "request.txt"):
        os.utime(p, (old, old))


def listed(act: Path) -> list[str]:
    return [Path(r).name for r in act.read_text().split()]


@pytest.mark.heavy
def test_rule_c_and_reap_leave_the_same_active_runs_state_for_the_same_dead_run(tmp_path):
    pid = dead_pid()
    jan = tmp_path / "janitor"
    jan.mkdir()
    sandbox(jan, "echo 0", live=True)
    dead_run(jan, jan / "co/runs" / RUN, pid)
    rp = tmp_path / "reap"
    (rp / "runs" / RUN).mkdir(parents=True)
    (rp / "runs" / RUN / "request.txt").write_text((jan / "co/runs" / RUN / "request.txt").read_text())
    dead_run(rp, rp / "runs" / RUN, pid)
    (rp / "st").mkdir()
    (rp / "st/active-runs.txt").write_text(f"{rp / 'runs' / RUN}\n")

    r = janitor(jan, SG_SQUAD=str(ROOT), SG_TAKEOVER_SECONDS="1000000000000")
    log = (jan / "st/cycle.log").read_text() if (jan / "st/cycle.log").exists() else ""
    assert f"zombie run {RUN}" in log, r.stdout + r.stderr + log
    from squad import reap
    assert reap.reap(rp / "st/active-runs.txt", rp, ceiling=caps.Ceiling({k: 1 for k in caps.KINDS}, rp / "leases"),
                     procs=[], say=lambda _: None) == [RUN]

    j_act, r_act = jan / "st/active-runs.txt", rp / "st/active-runs.txt"
    assert listed(j_act) == listed(r_act) == [RUN], f"rule C {listed(j_act)} and reap {listed(r_act)} disagree on the line"
    for rec in (jan / "co/runs" / RUN / "record.jsonl", rp / "runs" / RUN / "record.jsonl"):
        ev = json.loads(rec.read_text().splitlines()[-1])
        assert ev["event"] == "verdict" and ev["reasons"] == ["killed/no-process"], ev
    janitor(jan, SG_SQUAD=str(ROOT), SG_TAKEOVER_SECONDS="1000000000000")   # the next pass reads a finished run: the line stays
    assert listed(j_act) == [RUN], f"the next janitor pass dropped the finished line: {listed(j_act)}"


@pytest.mark.heavy
def test_a_killed_run_whose_wave_is_open_prints_one_held_by_wave_line(tmp_path):
    """Rule O leaves a killed run's issue to its open wave (ADR-0166) and says so: one line, no drop, no board write (#1423)."""
    from tests.test_janitor_card_done_base import N, writes
    sandbox(tmp_path, "echo 0", wave="open", card="Doing")
    rec = tmp_path / "co/runs/run-stacked/record.jsonl"
    rec.write_text(rec.read_text().replace('"verdict":"pass","reasons":[],"exit_code":0', '"verdict":"failed","reasons":["killed/no-process"],"exit_code":1'))
    old = time.time() - 3600
    os.utime(rec, (old, old))
    janitor(tmp_path, SG_SQUAD=str(ROOT))
    cl = tmp_path / "st/cycle.log"
    log = cl.read_text() if cl.exists() else ""
    want = f"HELD-BY-WAVE #{N}: run run-stacked ended failed, its wave is open; the landing pass owns the issue"
    assert log.count(want) == 1, log
    assert (tmp_path / "st/auto" / f"disp-{N}").exists()
    assert "board" not in writes(tmp_path), writes(tmp_path)
