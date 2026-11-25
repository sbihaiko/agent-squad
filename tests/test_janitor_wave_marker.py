"""A wave marker of a run that ended stops at the janitor's pass (#2140).

`auto/wave-<run>` stands for a run's landing pass: `watch.sh` lands a wave only for a run the listing names, and `wave.sh`
clears the marker when the wave lands. So a wave whose run left `active-runs.txt` without the clearing pass can never land,
and its marker stays for ever: the loop's `auto/` held 1325 such markers on 2026-11-17. The janitor drops the marker of a
run no line names and no live process works, and it keeps the wave of a live run.

The janitor runs end to end in the sandbox of test_janitor_card_done_base: a stub `gh`, a stub board.sh, a temp state dir.
"""

from __future__ import annotations

import os
import subprocess
import time

import pytest

from tests.test_janitor_card_done_base import janitor, sandbox


def aged(*markers) -> None:
    """Age each marker past the wave grace (#2847): only a marker older than the grace is read as an ended run's."""
    old = time.time() - 3600
    for m in markers:
        os.utime(m, (old, old))


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
    aged(marker)

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
def test_the_janitor_drops_the_landing_marker_of_an_ended_run(tmp_path):
    """The repro of #2185: `wland-run-old` stands, no line names it, no process works it, no queue waits; the pass drops it."""
    sandbox(tmp_path, "echo 0")
    (tmp_path / "st/active-runs.txt").write_text("")
    marker = tmp_path / "st/auto/wland-run-old"
    marker.write_text("")
    old = time.time() - 7 * 3600
    os.utime(marker, (old, old))

    janitor(tmp_path)

    assert not marker.exists(), f"the landing marker of an ended run stayed: {log_of(tmp_path)}"
    assert "WLAND-DROPPED wland-run-old" in log_of(tmp_path), log_of(tmp_path)


@pytest.mark.heavy
def test_a_live_runs_landing_marker_stays(tmp_path):
    """The half the drop must never reach: the listing names the run, so its landing marker still answers."""
    sandbox(tmp_path, "echo 0", wave="open")
    marker = tmp_path / "st/auto/wland-run-stacked"
    marker.write_text("")
    old = time.time() - 7 * 3600
    os.utime(marker, (old, old))

    janitor(tmp_path)

    assert marker.exists(), f"the landing marker of a listed run went: {log_of(tmp_path)}"


@pytest.mark.heavy
def test_a_young_landing_marker_stays(tmp_path):
    """A marker inside the grace window may belong to a landing in flight, so the pass leaves it."""
    sandbox(tmp_path, "echo 0")
    (tmp_path / "st/active-runs.txt").write_text("")
    marker = tmp_path / "st/auto/wland-run-fresh"
    marker.write_text("")
    fresh = time.time() - 3600
    os.utime(marker, (fresh, fresh))

    janitor(tmp_path)

    assert marker.exists(), f"a young landing marker went: {log_of(tmp_path)}"


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


@pytest.mark.heavy
def test_a_fresh_wave_marker_with_no_listing_line_stays(tmp_path):
    """The repro of #2847: `wave.sh start` writes the markers before the launch's line reaches the listing, so a pass in
    between sees a wave no line names. A marker younger than the grace stands; the #2140 cleanup waits for it to age."""
    sandbox(tmp_path, "echo 0")
    (tmp_path / "st/active-runs.txt").write_text("")
    wave = tmp_path / "st/auto/wave-run-new"
    start = tmp_path / "st/auto/wave-start-run-new"
    wave.write_text("wave/run-new\n")
    start.write_text("abc123\n")

    janitor(tmp_path)

    assert wave.exists(), f"a fresh wave marker went: {log_of(tmp_path)}"
    assert start.exists(), f"a fresh wave start marker went: {log_of(tmp_path)}"


@pytest.mark.heavy
def test_aged_wave_markers_with_no_line_and_no_process_drop(tmp_path):
    """Past the grace, a wave no line names and no process works is an ended run's, so both markers drop."""
    sandbox(tmp_path, "echo 0")
    (tmp_path / "st/active-runs.txt").write_text("")
    wave = tmp_path / "st/auto/wave-run-gone"
    start = tmp_path / "st/auto/wave-start-run-gone"
    wave.write_text("wave/run-gone\n")
    start.write_text("abc123\n")
    aged(wave, start)

    janitor(tmp_path)

    assert not wave.exists(), f"an aged wave marker stayed: {log_of(tmp_path)}"
    assert not start.exists(), f"an aged wave start marker stayed: {log_of(tmp_path)}"


@pytest.mark.heavy
def test_a_frontier_runs_wave_marker_whose_folder_works_a_live_process_stays(tmp_path):
    """A frontier run's folder is `worktrees/run/<stamp>/runs/<run>`, not `<checkout>/runs/<run>` (#2847). No line names the
    run and the marker is past the grace, but a live process works that folder, so the wave can still land."""
    sandbox(tmp_path, "echo 0")
    (tmp_path / "st/active-runs.txt").write_text("")
    folder = tmp_path / "co/worktrees/run/20261125-101010/runs/run-front"
    folder.mkdir(parents=True)
    wave = tmp_path / "st/auto/wave-run-front"
    start = tmp_path / "st/auto/wave-start-run-front"
    wave.write_text("wave/run-front\n")
    start.write_text("abc123\n")
    aged(wave, start)
    p = subprocess.Popen(["bash", "-c", "exec -a gate.sh sleep 30"], cwd=folder)   # a command line the pass reads as a run's gate
    try:
        janitor(tmp_path)
        assert wave.exists(), f"the wave of a frontier folder a live process works went: {log_of(tmp_path)}"
        assert start.exists(), f"the wave start of a frontier folder a live process works went: {log_of(tmp_path)}"
    finally:
        p.kill()
        p.wait()
