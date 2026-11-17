"""A pass that rewrites `active-runs.txt` keeps a line a launch appended during it (#1862, ADR-0030, ADR-0149).

The list is the loop's own fact of live work: the dashboard's live list, the janitor's HELD-BY-WAVE, `squad.handhold` and
the frontier's accounting read it. The janitor read the list once at the start of a pass and wrote back the copy it had
read, so a run the launcher appended in between lost its line: on 2026-11-15 the file read empty while four run leases
were held and alive, and every reader saw no work.

An atomic rename alone does not answer that: an append that lands between the pass's read and its rename writes the old
inode, and the rename drops it. So the two writers exclude each other on one lock, `active-runs.txt.lock`: the launcher's
append holds it while it adds its line, and the pass re-reads the list under it, keeps every line it did not name, writes
the whole list to a temporary file beside it and renames it into place (ADR-0030).

The end-to-end cases drive janitor.py with the sandboxes of the card and zombie-line suites: a stub `gh`, a stub board.sh,
a temp state dir. The append lands at the pass's own first `gh` call, which is after its read and before its write; the
stub copies the list there, so a case proves the pass had not rewritten it yet.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import threading
import time

import pytest

from squad import caps, record
from tests.test_janitor_card_done_base import janitor, sandbox
from tests.test_janitor_zombie_line import dead_pid, listed

RUN = "run-stacked"
RACE = "run-race"


def dead_zombie(folder: pathlib.Path, pid: int) -> None:
    """The dead run of the zombie suite, under its own run id: the open wave of `run-stacked` never stands for it."""
    rec = folder / "record.jsonl"
    r = record.Record(rec)
    r.append("run_start", run_id=folder.name, graph_name="g", graph_hash="h", engine_options={}, root="main", plan=["main"])
    r.append("call_start", node="main", entry=1, session="s", pid=pid, limit=None, started_at=time.time() - 3600)
    old = time.time() - 3600
    for p in (rec, folder / "request.txt"):
        os.utime(p, (old, old))


def appended_at_the_first_gh_call(tmp_path: pathlib.Path) -> pathlib.Path:
    """A stub `gh` that adds `RACE` to the list once, before it answers anything, and copies the list as it saw it (#1862).

    The pass reads the list before its first `gh` call, so the copy proves the append landed between that read and the
    pass's own write."""
    gh = tmp_path / "bin/gh"
    hook = (
        'if [ ! -e "$SG_STATE/race.once" ]; then\n'
        '  : > "$SG_STATE/race.once"\n'
        '  cp "$SG_STATE/active-runs.txt" "$SG_STATE/race.seen"\n'
        '  printf \'%s\\n\' "$SG_STATE/co/runs/run-race" >> "$SG_STATE/active-runs.txt"\n'
        "fi\n"
    )
    first, rest = gh.read_text().split("\n", 1)
    gh.write_text(first + "\n" + hook + rest)
    return gh


@pytest.mark.heavy
def test_a_line_appended_during_the_pass_survives_its_rewrite(tmp_path):
    """Both listed runs leave the list, and the launched line stays: `run-stacked` ended with a verdict and no wave stands for
    it (FINISHED-RUN), and `run-zombie` is the run the pass drops. So the appended `run-race` is the one line the rewrite
    must carry over. The pass reads its issue through `gh` inside the run loop: the first such call is after the pass read
    the list and before it rewrites it."""
    sandbox(tmp_path, "echo 0")
    zombie = tmp_path / "co/runs/run-zombie"
    zombie.mkdir(parents=True)
    (zombie / "request.txt").write_text("plain request of run-zombie\n")
    dead_zombie(zombie, dead_pid())
    act = tmp_path / "st/active-runs.txt"
    act.write_text(f"{RUN}\nrun-zombie\n")
    appended_at_the_first_gh_call(tmp_path)

    janitor(tmp_path, SG_SQUAD=str(pathlib.Path(__file__).resolve().parents[1]),
            SG_TAKEOVER_SECONDS="1000000000000", SQUAD_CAPS_DIR=str(tmp_path / "caps"))

    seen = (tmp_path / "st/race.seen").read_text()
    assert seen.split() == [RUN, "run-zombie"], f"the append did not land after the pass read the list: {seen!r}"
    got = listed(act)
    assert got == [RACE], f"the pass rewrote the list from its own stale read and lost the launched run: {got}"


@pytest.mark.heavy
def test_a_kept_line_is_retained_beside_the_appended_one(tmp_path):
    """A run whose wave still stands for it keeps its line, and the rewrite writes it beside the line the launch appended.

    The reader that keeps every line it did not name is what carries `run-race` over; this case proves it never trades a
    line it read for the appended one. Both lines answer, so the pass kept a line it read and lost nothing else."""
    sandbox(tmp_path, "echo 0", wave="open")
    zombie = tmp_path / "co/runs/run-zombie"
    zombie.mkdir(parents=True)
    (zombie / "request.txt").write_text("plain request of run-zombie\n")
    dead_zombie(zombie, dead_pid())
    act = tmp_path / "st/active-runs.txt"
    act.write_text(f"{RUN}\nrun-zombie\n")
    appended_at_the_first_gh_call(tmp_path)

    janitor(tmp_path, SG_SQUAD=str(pathlib.Path(__file__).resolve().parents[1]),
            SG_TAKEOVER_SECONDS="1000000000000", SQUAD_CAPS_DIR=str(tmp_path / "caps"))

    seen = (tmp_path / "st/race.seen").read_text()
    assert seen.split() == [RUN, "run-zombie"], f"the append did not land after the pass read the list: {seen!r}"
    got = listed(act)
    assert got == [RUN, RACE], f"the kept line or the appended one left the rewrite: {got}"


@pytest.mark.heavy
def test_a_finished_run_line_leaves_the_list_on_the_pass_that_reads_it(tmp_path):
    """#2138: a run whose verdict is final, with no wave standing for it and no process or lease naming it, leaves
    active-runs on the pass that reads it. The line is gone from the rewrite, never merely from the pass's own listing."""
    sandbox(tmp_path, "echo 0")
    act = tmp_path / "st/active-runs.txt"
    assert listed(act) == [RUN], f"the sandbox wrote no line for the finished run: {listed(act)}"
    janitor(tmp_path, SG_SQUAD=str(pathlib.Path(__file__).resolve().parents[1]),
            SG_TAKEOVER_SECONDS="1000000000000", SQUAD_CAPS_DIR=str(tmp_path / "caps"))
    journal = (tmp_path / "st/cycle.log").read_text()
    assert f"FINISHED-RUN {RUN}" in journal, f"the pass never named the finished-run drop: {journal}"
    got = listed(act)
    assert got == [], f"a finished run kept its active-runs line, so it still holds a child room (#2138): {got}"


@pytest.mark.heavy
def test_the_run_loop_drop_paths_each_leave_their_line_out_of_the_rewrite(tmp_path):
    """#2144, the drop paths: a run the run loop leaves out of active-runs leaves it under every rule that drops a line.

    The pass names each drop, and the rewrite writes no line for the run: NEVER-STARTED (no record after its bound) and
    CLOSED-RUN (every issue it names is closed) meet here; the zombie, the killed run and FINISHED-RUN have their own
    cases in the zombie-line suite and this file."""
    never = tmp_path / "never"
    never.mkdir()
    sandbox(never, "echo 0")
    orphan = never / "co/runs/run-never-started"
    orphan.mkdir(parents=True)
    (orphan / "request.txt").write_text("plain request of run-never-started\n")
    old = time.time() - 3600
    os.utime(orphan / "request.txt", (old, old))
    os.utime(orphan, (old, old))
    (never / "st/active-runs.txt").write_text("run-never-started\n")

    janitor(never, SG_SQUAD=str(pathlib.Path(__file__).resolve().parents[1]),
            SG_TAKEOVER_SECONDS="1000000000000", SQUAD_CAPS_DIR=str(never / "caps"))
    journal = (never / "st/cycle.log").read_text()
    assert "NEVER-STARTED run run-never-started" in journal, f"the pass never named the never-started drop: {journal}"
    assert listed(never / "st/active-runs.txt") == [], listed(never / "st/active-runs.txt")

    closed = tmp_path / "closed"
    closed.mkdir()
    sandbox(closed, "echo 0", closed=True)
    janitor(closed, SG_SQUAD=str(pathlib.Path(__file__).resolve().parents[1]),
            SG_TAKEOVER_SECONDS="1000000000000", SQUAD_CAPS_DIR=str(closed / "caps"))
    journal = (closed / "st/cycle.log").read_text()
    assert f"CLOSED-RUN {RUN}" in journal, f"the pass never named the closed-run drop: {journal}"
    assert listed(closed / "st/active-runs.txt") == [], listed(closed / "st/active-runs.txt")


@pytest.mark.heavy
def test_one_pass_with_four_live_runs_leaves_the_listing_and_the_leases_agreeing(tmp_path):
    """The observed shape: four live runs held leases while the file read empty (#1862). One pass leaves all four listed
    and all four leases held, so the listing and the leases answer the same way."""
    sandbox(tmp_path, "echo 0", live=True)
    gate = tmp_path / "gate.sh"
    gate.write_text("sleep 300\n")
    runs = [RUN, "run-b", "run-c", "run-d"]
    procs = []
    try:
        listed_now = []
        for name in runs:
            f = tmp_path / "co/runs" / name
            f.mkdir(parents=True, exist_ok=True)
            (f / "request.txt").write_text(f"plain request of {name}\n")
            now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            (f / "record.jsonl").write_text(
                json.dumps({"event": "run_start", "run_id": name, "ts": now, "graph_name": "g", "graph_hash": "h",
                            "engine_options": {}, "root": "main", "plan": ["main"]}) + "\n"
                + json.dumps({"event": "call_start", "node": "main", "entry": 1, "session": "s", "pid": None,
                              "limit": 3600, "started_at": time.time()}) + "\n")
            p = subprocess.Popen(["bash", str(gate)], cwd=f)
            procs.append(p)
            lease = tmp_path / "caps/runs" / f"{name}.json"
            lease.parent.mkdir(parents=True, exist_ok=True)
            lease.write_text(json.dumps({"holder": name, "pid": p.pid, "since": time.time()}))
            listed_now.append(str(f))
        (tmp_path / "st/active-runs.txt").write_text("\n".join(listed_now) + "\n")

        janitor(tmp_path, SG_SQUAD=str(pathlib.Path(__file__).resolve().parents[1]),
                SG_TAKEOVER_SECONDS="1000000000000", SQUAD_CAPS_DIR=str(tmp_path / "caps"))

        assert listed(tmp_path / "st/active-runs.txt") == runs, listed(tmp_path / "st/active-runs.txt")
        ceiling = caps.Ceiling({k: 10 for k in caps.KINDS}, tmp_path / "caps")
        assert ceiling.occupancy()["runs"] == len(runs), "a lease of a live run did not survive the pass"
        assert sorted(ceiling.holders("runs")) == sorted(runs), ceiling.holders("runs")
    finally:
        for p in procs:
            p.terminate()
            p.wait(timeout=10)


def test_the_rewrite_renames_a_finished_file_and_never_truncates_the_list(tmp_path, monkeypatch):
    """ADR-0030: the list goes to a temporary file beside it and is renamed over it, so no reader sees a partial list."""
    from squad import active_runs

    act = tmp_path / "active-runs.txt"
    act.write_text("run-a\nrun-b\n")
    real, seen = active_runs.os.replace, {}

    def replace(src, dst):
        seen["at_replace"] = pathlib.Path(dst).read_text()
        seen["same_dir"] = pathlib.Path(src).parent == pathlib.Path(dst).parent
        real(src, dst)

    monkeypatch.setattr(active_runs.os, "replace", replace)
    assert active_runs.rewrite(act, ["run-a", "run-b"], ["run-a"]) == ["run-a"]
    assert seen["at_replace"] == "run-a\nrun-b\n", "the destination was truncated before the rename"
    assert seen["same_dir"], "the temporary file was not beside the list"
    assert act.read_text() == "run-a\n"


def test_a_failed_rename_leaves_the_old_list_and_no_temporary_file(tmp_path, monkeypatch):
    from squad import active_runs

    act = tmp_path / "active-runs.txt"
    act.write_text("run-a\nrun-b\n")

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(active_runs.os, "replace", boom)
    with pytest.raises(OSError):
        active_runs.rewrite(act, ["run-a", "run-b"], ["run-a"])
    assert act.read_text() == "run-a\nrun-b\n", "a failed rename took the old list with it"
    assert not list(tmp_path.glob("active-runs.*.tmp")), "a failed rename left its temporary file behind"


@pytest.mark.heavy
def test_a_line_appended_after_the_pass_read_survives_the_rewrite(tmp_path):
    from squad import active_runs

    act = tmp_path / "active-runs.txt"
    act.write_text("run-a\nrun-b\n")
    names = act.read_text().split()                     # the pass's read, at the start of its pass
    active_runs.append(act, str(tmp_path / "run-c"))    # a launch during that pass, under the writer lock
    final = active_runs.rewrite(act, names, [n for n in names if n != "run-b"])
    assert final == ["run-a", str(tmp_path / "run-c")], final
    assert act.read_text().split() == final


@pytest.mark.heavy
def test_an_append_waits_for_the_pass_that_holds_the_writer_lock(tmp_path):
    """The launcher's append and the pass's rewrite exclude each other on one lock, so neither reads a half-written list."""
    from squad import active_runs

    act = tmp_path / "active-runs.txt"
    act.write_text("")
    done = threading.Event()

    def launch():
        active_runs.append(act, "run-x")
        done.set()

    with active_runs.locked(act):
        t = threading.Thread(target=launch)
        t.start()
        assert not done.wait(0.3), "an append wrote the list while a pass held the writer lock"
    assert done.wait(10), "the append never reached the list after the lock went"
    t.join()
    assert act.read_text().split() == ["run-x"]
