"""A run whose process is gone leaves `active-runs.txt` on the pass that ends it (#1854, ADR-0149, ADR-0167).

The listing and the run leases read the same fact two ways: a listed run whose process is gone and which no lease counts is
no live work, and keeping its line made the janitor say HELD-BY-WAVE and the hub show a dead run as the loop's work. The
line leaves on the pass that ends the run, and the log names it, so a listing that outlives its run is visible in
`cycle.log` rather than inferred from a count.

Rule C ends the run itself (`squad.killed`, the one writer), frees its leases and drops its line in one pass. The health
pass's reap ends a run before the janitor reaches it, and keeps the line: rules O and R read the run from the listing on the
pass after the reap, so dropping it there would starve the run's dispatch markers. The janitor's next pass sees the killed
verdict, no process and no lease, and drops the line then.

The line stays while one of the run's waves is open: `childcc` enters its children from the listing until the wave lands,
the reason `wtclean.sh` rule A holds a finished run's line while `wave-<run>` stands.

The janitor runs end to end in the sandbox of test_janitor_card_done_base: a stub `gh`, a stub board.sh, a temp state dir.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from squad import caps, reap, record
from tests.test_janitor_card_done_base import janitor, sandbox

ROOT = Path(__file__).resolve().parents[1]
RUN = "run-stacked"


def dead_pid() -> int:
    p = subprocess.Popen(["true"])
    p.wait()
    return p.pid


def dead_run(tmp_path: Path, folder: Path, pid: int, run_id: str = RUN) -> None:
    """The same dead run in any folder: started, one call by engine `pid` an hour ago, no verdict, record idle an hour.
    `run_id` names the run the record claims, so a run whose open wave must not stand for a sibling's says its own."""
    rec = folder / "record.jsonl"
    rec.unlink(missing_ok=True)
    r = record.Record(rec)
    r.append("run_start", run_id=run_id, graph_name="g", graph_hash="h", engine_options={}, root="main", plan=["main"])
    r.append("call_start", node="main", entry=1, session="s", pid=pid, limit=None, started_at=time.time() - 3600)
    old = time.time() - 3600
    for p in (rec, folder / "request.txt"):
        os.utime(p, (old, old))


def listed(act: Path) -> list[str]:
    return [Path(r).name for r in act.read_text().split()]


def last_event(rec: Path) -> dict:
    return json.loads(rec.read_text().splitlines()[-1])


@pytest.mark.heavy
def test_rule_c_drops_the_line_of_the_run_it_reaps(tmp_path):
    pid = dead_pid()
    sandbox(tmp_path, "echo 0", live=True)
    dead_run(tmp_path, tmp_path / "co/runs" / RUN, pid)

    r = janitor(tmp_path, SG_SQUAD=str(ROOT), SG_TAKEOVER_SECONDS="1000000000000", SQUAD_CAPS_DIR=str(tmp_path / "caps"))
    log = (tmp_path / "st/cycle.log").read_text() if (tmp_path / "st/cycle.log").exists() else ""
    assert f"zombie run {RUN} " in log and "dropped from active-runs" in log, r.stdout + r.stderr + log
    assert listed(tmp_path / "st/active-runs.txt") == [], listed(tmp_path / "st/active-runs.txt")
    assert last_event(tmp_path / "co/runs" / RUN / "record.jsonl")["reasons"] == ["killed/no-process"]


@pytest.mark.heavy
def test_the_janitor_drops_the_line_of_a_run_the_reap_ended(tmp_path):
    pid = dead_pid()
    sandbox(tmp_path, "echo 0", live=True)
    dead_run(tmp_path, tmp_path / "co/runs" / RUN, pid)
    act = tmp_path / "st/active-runs.txt"
    assert reap.reap(act, tmp_path / "co", ceiling=caps.Ceiling({k: 1 for k in caps.KINDS}, tmp_path / "leases"),
                     procs=[], say=lambda _: None) == [RUN]
    assert listed(act) == [RUN], f"the health pass dropped the line before the janitor's rules read it: {listed(act)}"

    r = janitor(tmp_path, SG_SQUAD=str(ROOT), SG_TAKEOVER_SECONDS="1000000000000", SQUAD_CAPS_DIR=str(tmp_path / "caps"))
    log = (tmp_path / "st/cycle.log").read_text() if (tmp_path / "st/cycle.log").exists() else ""
    assert f"killed run {RUN} " in log and "dropped from active-runs" in log, r.stdout + r.stderr + log
    assert listed(act) == [], f"a run gone from the leases kept its listing line: {listed(act)}"


@pytest.mark.heavy
def test_a_reaped_run_whose_wave_is_open_keeps_its_line(tmp_path):
    pid = dead_pid()
    sandbox(tmp_path, "echo 0", live=True)
    dead_run(tmp_path, tmp_path / "co/runs" / RUN, pid)
    (tmp_path / "st/auto" / f"wave-{RUN}").touch()

    janitor(tmp_path, SG_SQUAD=str(ROOT), SG_TAKEOVER_SECONDS="1000000000000", SQUAD_CAPS_DIR=str(tmp_path / "caps"))
    assert listed(tmp_path / "st/active-runs.txt") == [RUN], listed(tmp_path / "st/active-runs.txt")


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
