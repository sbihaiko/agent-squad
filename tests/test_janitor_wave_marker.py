"""A wave marker of a run that ended stops at the janitor's pass (#2140).

`auto/wave-<run>` stands for a run's landing pass: `watch.sh` lands a wave only for a run the listing names, and `wave.sh`
clears the marker when the wave lands. So a wave whose run left `active-runs.txt` without the clearing pass can never land,
and its marker stays for ever: the loop's `auto/` held 1325 such markers on 2026-11-17. The janitor drops the marker of a
run no line names and no live process works, and it keeps the wave of a live run.

The janitor runs end to end in the sandbox of test_janitor_card_done_base: a stub `gh`, a stub board.sh, a temp state dir.
"""

from __future__ import annotations

import subprocess

import pytest

from tests.test_janitor_card_done_base import janitor, sandbox


def log_of(tmp_path) -> str:
    f = tmp_path / "st/cycle.log"
    return f.read_text() if f.exists() else ""


@pytest.mark.heavy
def test_the_janitor_drops_the_wave_marker_of_an_ended_run(tmp_path):
    """The repro of #2140: `wave-run-old` stands, no `run-old` line is in the listing, no folder works it; the pass drops it."""
    sandbox(tmp_path, "echo 0")
    (tmp_path / "st/active-runs.txt").write_text("")
    marker = tmp_path / "st/auto/wave-run-old"
    marker.write_text("wave/run-old\n")

    janitor(tmp_path)

    assert not marker.exists(), f"the wave marker of an ended run stayed: {log_of(tmp_path)}"
    assert "WAVE-DROPPED wave-run-old" in log_of(tmp_path), log_of(tmp_path)


@pytest.mark.heavy
def test_a_live_runs_wave_marker_stays(tmp_path):
    """The half the drop must never reach: the run's line is in the listing, so its wave still lands."""
    sandbox(tmp_path, "echo 0", wave="open")
    marker = tmp_path / "st/auto/wave-run-stacked"

    janitor(tmp_path)

    assert marker.exists(), f"the wave of a listed run went: {log_of(tmp_path)}"


@pytest.mark.heavy
def test_a_wave_marker_whose_folder_works_a_live_process_stays(tmp_path):
    """No line names the run, but a live loop process works its folder: the wave can still land, so the marker stays."""
    sandbox(tmp_path, "echo 0")
    (tmp_path / "st/active-runs.txt").write_text("")
    folder = tmp_path / "co/runs/run-busy"
    folder.mkdir()
    marker = tmp_path / "st/auto/wave-run-busy"
    marker.write_text("wave/run-busy\n")
    p = subprocess.Popen(["bash", "-c", "exec -a gate.sh sleep 30"], cwd=folder)   # a command line the pass reads as a run's gate
    try:
        janitor(tmp_path)
        assert marker.exists(), f"the wave of a folder a live process works went: {log_of(tmp_path)}"
    finally:
        p.kill()
        p.wait()
