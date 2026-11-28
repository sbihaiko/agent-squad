"""The hand hold: a person's or an operator session's own work on a card, kept outside the loop (loop-bug #1664).

One file per issue, `$SG_STATE/auto/hold-<n>`, holds `by`, `since` and `until`. A hold is refused when a live run
holds the issue, when another carrier holds it, or when a `taken-<run>` marker names it. A batch is all or nothing.
Every write verb touches `board-dirty`. A release names its next column and records that decision, and the board pass
is the one writer of the column fact. These cases use a temp state folder, injected processes and a fixed clock: no
network, no gh, no live state.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from squad import handhold as H
from squad import run_liveness as RL

ROOT = Path(__file__).resolve().parents[1]
NOW = 1_800_000_000.0   # a fixed clock, in epoch seconds
RUN = "run-hand-a"
PID = 4242


@pytest.fixture
def env(tmp_path, monkeypatch):
    state = tmp_path / "state"
    (state / "auto").mkdir(parents=True)
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    monkeypatch.setenv("SG_STATE", str(state))
    monkeypatch.setenv("SG_CHECKOUT", str(checkout))
    monkeypatch.setenv("SG_REPO", "owner/name")
    monkeypatch.delenv("SG_HAND_HOLD_MINUTES", raising=False)
    monkeypatch.setattr(H, "_gh_label", lambda repo, n, label, add: None)   # the GitHub face is the network seam: no case reaches it
    return state


@pytest.fixture
def labels(env, monkeypatch):
    """Every holder label the verbs write, as `(repo, issue, label, add)`: add is True on a hold or a renew and False on a
    release, so a case reads the two faces of one act from the file and from this list."""
    calls = []

    def record(repo, n, label, add):
        calls.append((repo, n, label, add))

    monkeypatch.setattr(H, "_gh_label", record)
    return calls


def run_folder(tmp_path: Path, name: str, issue: int, *, live: bool, verdict: bool = False) -> Path:
    """One run folder: its request names `issue`; its record starts an engine (`call_start`, pid PID) that is live or dead,
    and a verdict when asked. A live engine has its process in the procs table, started before the call named it."""
    f = tmp_path / "runs" / name
    f.mkdir(parents=True)
    (f / "request.txt").write_text(f"CHILD a: GitHub issue #{issue}\n")
    events = [
        {"event": "run_start", "run_id": name, "pos": 1},
        {"event": "call_start", "pid": PID, "started_at": NOW - 100, "pos": 2},
    ]
    if verdict:
        events.append({"event": "verdict", "pos": 3, "verdict": "done"})
    (f / "record.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    return f


def listed(state: Path, *folders: Path) -> None:
    (state / "active-runs.txt").write_text("".join(f"{f}\n" for f in folders))


def live_procs(alive: bool) -> dict:
    """The process table the run's engine reads: the engine process runs as a squad run, or nothing does."""
    return {PID: RL.Proc(NOW - 200, "python3 -m squad start x")} if alive else {}


def read(state: Path, n: int) -> str:
    return (state / "auto" / f"hold-{n}").read_text()


def test_hold_writes_the_file_and_its_three_fields(env):
    rc = H.hold([1664], by="manual", minutes=60, state=env, now=NOW, procs={})
    assert rc == 0
    fields = dict(line.split(" ", 1) for line in read(env, 1664).splitlines())
    assert fields["by"] == "manual"
    assert fields["since"] == H.iso(NOW)
    assert fields["until"] == H.iso(NOW + 3600)


def test_a_manual_hold_writes_the_file_and_the_holder_label_and_both_faces_read_back(env, labels):
    """One act, two faces (#1678): the file the janitor reads and the label a dispatch pass reads are written together."""
    assert H.LABELS == {"manual": "ready-for-human", "hal9k": "ready-for-HAL9K"}
    rc = H.hold([1664], by="manual", minutes=60, state=env, now=NOW, procs={})
    assert rc == 0
    assert labels == [("owner/name", 1664, "ready-for-human", True)]
    row = H.holds(state=env, now=NOW)[0]
    assert row["issue"] == 1664 and row["carrier"] == "manual" and row["live"] is True   # the file's own read


def test_a_hal9k_hold_writes_the_hal9k_label_on_the_issue(env, labels):
    rc = H.hold([1664], by="hal9k", minutes=60, state=env, now=NOW, procs={})
    assert rc == 0
    assert labels == [("owner/name", 1664, "ready-for-HAL9K", True)]
    assert H.holds(state=env, now=NOW)[0]["carrier"] == "hal9k"


def test_a_refused_hold_writes_no_holder_label(env, labels):
    (env / "auto" / "taken-run-x").write_text("run run-x\nissues 1664\n")
    rc = H.hold([1664], by="manual", state=env, now=NOW, procs={})
    assert rc == 1
    assert labels == [], "a refused hold names no holder, so it writes no label"


def test_a_failed_label_write_leaves_no_file_that_says_held_and_names_the_failure(env, monkeypatch, capsys):
    def refused(repo, n, label, add):
        raise H.HandError(f"the write of {label} on #{n} was refused")

    monkeypatch.setattr(H, "_gh_label", refused)
    rc = H.hold([1664], by="manual", state=env, now=NOW, procs={})
    assert rc == 1
    assert not (env / "auto" / "hold-1664").exists(), "the label never landed, so no file may say the issue is held"
    err = capsys.readouterr().err
    assert "ready-for-human" in err and "#1664" in err


def test_a_batch_whose_later_label_write_fails_writes_no_file_and_undoes_the_labels_it_wrote(env, monkeypatch):
    calls = []

    def refused_on_two(repo, n, label, add):
        calls.append((n, add))
        if n == 2 and add:
            raise H.HandError("the second write was refused")

    monkeypatch.setattr(H, "_gh_label", refused_on_two)
    rc = H.hold([1, 2], by="manual", state=env, now=NOW, procs={})
    assert rc == 1
    assert calls == [(1, True), (2, True), (1, False)], "the label the first write added is cleared again"
    for n in (1, 2):
        assert not (env / "auto" / f"hold-{n}").exists()


def test_a_hold_with_no_repository_is_refused_and_writes_neither_face(env, monkeypatch, capsys):
    monkeypatch.delenv("SG_REPO")
    rc = H.hold([1664], by="manual", state=env, now=NOW, procs={})
    assert rc == 1
    assert not (env / "auto" / "hold-1664").exists()
    assert "SG_REPO" in capsys.readouterr().err


def test_hold_refused_when_a_live_run_holds_the_issue_and_writes_nothing(env, tmp_path, capsys):
    folder = run_folder(tmp_path, RUN, 1664, live=True)
    listed(env, folder)
    rc = H.hold([1664], by="manual", state=env, now=NOW, procs=live_procs(True))
    assert rc == 1
    assert not (env / "auto" / "hold-1664").exists()
    assert RUN in capsys.readouterr().err


def test_a_zsh_wrapper_whose_body_names_a_live_command_is_no_live_engine(env, tmp_path):
    """#1993: the record's engine pid is a `zsh -c` wrapper whose body names `squad start`: the wrapper is no engine, so the run is not live."""
    folder = run_folder(tmp_path, RUN, 1664, live=False)
    listed(env, folder)
    wrapper = {PID: RL.Proc(NOW - 200, "zsh -c sleep 540; python3 -m squad start x")}
    rc = H.hold([1664], by="manual", state=env, now=NOW, procs=wrapper)
    assert rc == 0


def test_a_squad_process_the_record_does_not_name_does_not_make_the_run_live(env, tmp_path):
    """#1993: a process that shares the run folder but is not the engine the record names is no evidence of ownership."""
    folder = run_folder(tmp_path, RUN, 1664, live=False)
    listed(env, folder)
    other = {9002: RL.Proc(NOW - 200, "python3 -m squad start other-run")}
    rc = H.hold([1664], by="manual", state=env, now=NOW, procs=other)
    assert rc == 0


def test_hold_of_an_issue_a_dead_run_held_is_not_refused(env, tmp_path):
    folder = run_folder(tmp_path, RUN, 1664, live=False, verdict=True)
    listed(env, folder)
    rc = H.hold([1664], by="manual", state=env, now=NOW, procs=live_procs(False))
    assert rc == 0


def test_hold_refused_for_another_carrier_hold(env):
    H.hold([1664], by="hal9k", state=env, now=NOW, procs={})
    before = read(env, 1664)
    rc = H.hold([1664], by="manual", state=env, now=NOW + 5, procs={})
    assert rc == 1
    assert read(env, 1664) == before   # never overwritten


def test_hold_refused_when_a_taken_marker_names_the_issue(env, capsys):
    (env / "auto" / "taken-run-x").write_text("run run-x\nissues 1664 1663\n")
    rc = H.hold([1664], by="manual", state=env, now=NOW, procs={})
    assert rc == 1
    assert not (env / "auto" / "hold-1664").exists()
    assert "taken" in capsys.readouterr().err


def test_a_batch_with_one_refusal_writes_no_file_for_any_issue(env, capsys):
    (env / "auto" / "taken-run-x").write_text("run run-x\nissues 2\n")
    rc = H.main(["hold", "1", "2", "3", "--by", "manual"], state=env, now=NOW, procs={})
    assert rc == 1
    for n in (1, 2, 3):
        assert not (env / "auto" / f"hold-{n}").exists()
    err = capsys.readouterr().err.strip().splitlines()
    assert len(err) == 1 and "#2" in err[0]


def test_renew_refuses_another_carrier(env, capsys):
    H.hold([1664], by="hal9k", state=env, now=NOW, procs={})
    before = read(env, 1664)
    rc = H.renew([1664], by="manual", state=env, now=NOW + 60, procs={})
    assert rc == 1
    assert read(env, 1664) == before
    assert "hal9k" in capsys.readouterr().err


def test_renew_moves_until_forward_and_keeps_since(env):
    H.hold([1664], by="manual", minutes=60, state=env, now=NOW, procs={})
    rc = H.renew([1664], by="manual", minutes=120, state=env, now=NOW + 600, procs={})
    assert rc == 0
    fields = dict(line.split(" ", 1) for line in read(env, 1664).splitlines())
    assert fields["since"] == H.iso(NOW)
    assert fields["until"] == H.iso(NOW + 600 + 7200)


def test_a_renew_keeps_the_label_and_the_file_consistent(env, labels):
    H.hold([1664], by="manual", minutes=60, state=env, now=NOW, procs={})
    labels.clear()
    rc = H.renew([1664], by="manual", minutes=120, state=env, now=NOW + 600, procs={})
    assert rc == 0
    assert labels == [("owner/name", 1664, "ready-for-human", True)]
    fields = dict(line.split(" ", 1) for line in read(env, 1664).splitlines())
    assert fields["until"] == H.iso(NOW + 600 + 7200)
    row = H.holds(state=env, now=NOW + 600)[0]
    assert row["carrier"] == "manual" and row["stale"] is False


def test_a_failed_label_write_on_a_renew_leaves_the_hold_file_as_it_was(env, monkeypatch, capsys):
    H.hold([1664], by="manual", minutes=60, state=env, now=NOW, procs={})
    before = read(env, 1664)

    def refused(repo, n, label, add):
        raise H.HandError("the write was refused")

    monkeypatch.setattr(H, "_gh_label", refused)
    rc = H.renew([1664], by="manual", minutes=120, state=env, now=NOW + 600, procs={})
    assert rc == 1
    assert read(env, 1664) == before
    assert "refused" in capsys.readouterr().err


def test_release_removes_the_hold_and_records_the_column_it_names(env, capsys):
    H.hold([1664], by="manual", state=env, now=NOW, procs={})
    assert (env / "auto" / "hold-1664").exists()
    rc = H.release([1664], by="manual", to="Closed", state=env, now=NOW, procs={})
    assert rc == 0
    assert not (env / "auto" / "hold-1664").exists()
    decision = dict(line.split(" ", 1) for line in (env / "auto" / "release-1664").read_text().splitlines())
    assert decision["to"] == "Closed"   # the release records the move; the board pass is its one writer
    assert "Closed" in capsys.readouterr().out


def test_a_release_clears_the_file_and_the_holder_label_in_one_act(env, labels):
    H.hold([1664], by="hal9k", state=env, now=NOW, procs={})
    labels.clear()
    rc = H.release([1664], by="hal9k", to="Closed", state=env, now=NOW, procs={})
    assert rc == 0
    assert labels == [("owner/name", 1664, "ready-for-HAL9K", False)]
    assert not (env / "auto" / "hold-1664").exists()
    assert H.holds(state=env, now=NOW) == []


def test_a_failed_label_clear_keeps_the_hold_file_and_names_the_failure(env, monkeypatch, capsys):
    H.hold([1664], by="manual", state=env, now=NOW, procs={})

    def refused(repo, n, label, add):
        raise H.HandError(f"the clear of {label} on #{n} was refused")

    monkeypatch.setattr(H, "_gh_label", refused)
    rc = H.release([1664], by="manual", to="Closed", state=env, now=NOW, procs={})
    assert rc == 1
    assert (env / "auto" / "hold-1664").exists(), "the label still names a holder, so the file keeps the hold"
    assert not (env / "auto" / "release-1664").exists(), "a release that did not clear both faces records no move"
    err = capsys.readouterr().err
    assert "ready-for-human" in err and "#1664" in err


def test_release_without_a_column_is_refused_and_keeps_the_hold(env):
    H.hold([1664], by="manual", state=env, now=NOW, procs={})
    with pytest.raises(ValueError, match="--to"):
        H.release([1664], by="manual", state=env, now=NOW, procs={})
    assert (env / "auto" / "hold-1664").exists(), "a release with no decision is refused, never a ghost card"
    rc = H.main(["release", "1664", "--by", "manual"], state=env, now=NOW, procs={})
    assert rc == 2
    assert (env / "auto" / "hold-1664").exists()


def test_release_of_a_stale_hold_still_works(env):
    H.hold([1664], by="manual", minutes=1, state=env, now=NOW, procs={})
    rc = H.release([1664], by="manual", to="Todo", state=env, now=NOW + 86400, procs={})
    assert rc == 0
    assert not (env / "auto" / "hold-1664").exists()


def test_holds_reports_stale_past_until(env):
    H.hold([1664], by="hal9k", minutes=30, state=env, now=NOW, procs={})
    fresh = H.holds(state=env, now=NOW + 60)
    late = H.holds(state=env, now=NOW + 3600)
    assert fresh[0]["issue"] == 1664 and fresh[0]["carrier"] == "hal9k" and fresh[0]["stale"] is False
    assert late[0]["stale"] is True
    assert late[0]["until"] == H.iso(NOW + 1800)


def test_holds_names_the_folder_activity_of_each_marker(env):
    H.hold([1664], by="manual", minutes=60, state=env, now=NOW, procs={})
    row = H.holds(state=env, now=NOW + 120)[0]
    assert row["activity"] >= 0 and row["live"] is True
    stale = H.holds(state=env, now=NOW + 7200)[0]
    assert stale["stale"] is True and stale["live"] is False


def test_alerts_names_a_stale_hold_once(env):
    H.hold([1664], by="manual", minutes=1, state=env, now=NOW, procs={})
    lines = H.alerts(state=env, now=NOW + 86400)
    assert len(lines) == 1 and "#1664" in lines[0] and "STALE" in lines[0]
    assert "HOLD STALE #1664" in (env / "alerts.log").read_text()
    assert H.alerts(state=env, now=NOW + 86401) == [], "one alert per episode, never one per pass"


def test_alerts_names_an_orphan_hold_on_a_closed_issue(env):
    H.hold([1664], by="manual", state=env, now=NOW, procs={})
    lines = H.alerts(state=env, closed={1664}, now=NOW)
    assert len(lines) == 1 and "ORPHAN" in lines[0] and "#1664" in lines[0]
    assert "HOLD ORPHAN #1664" in (env / "alerts.log").read_text()


def test_an_orphan_hold_is_never_live_work(env):
    H.hold([1664], by="manual", state=env, now=NOW, procs={})
    row = H.holds(state=env, closed={1664}, now=NOW)[0]
    assert row["orphan"] is True and row["live"] is False


def test_a_finished_run_holds_no_issue(env, tmp_path):
    folder = run_folder(tmp_path, RUN, 1664, live=True, verdict=True)
    listed(env, folder)
    rc = H.hold([1664], by="manual", state=env, now=NOW, procs=live_procs(True))
    assert rc == 0


@pytest.mark.parametrize("verb", ["hold", "renew", "release"])
def test_every_write_verb_creates_board_dirty(env, verb):
    H.hold([1664], by="manual", state=env, now=NOW, procs={})
    dirty = env / "board-dirty"
    dirty.unlink(missing_ok=True)   # the setup hold touched it; the verb under test must touch it again
    if verb == "hold":
        H.hold([1700], by="manual", state=env, now=NOW, procs={})
    elif verb == "renew":
        H.renew([1664], by="manual", state=env, now=NOW, procs={})
    else:
        H.release([1664], by="manual", to="Closed", state=env, now=NOW, procs={})
    assert dirty.exists()


def test_the_vocabulary_is_hold_renew_release_never_claim_or_job():
    source = Path(H.__file__).read_text().lower()
    for word in ("claim", "job"):
        assert word not in source, f"the settled vocabulary never says {word!r}"
    for verb in ("hold", "renew", "release"):
        assert verb in source


# ---- the gate marker (#2563): a hand gate in flight names its issue, so the card moves to Testing
GATE_START = NOW - 120.0


def alive(pid=PID, start=GATE_START):
    return {pid: RL.Proc(start, "bash hand-gate.sh")}


def test_gate_writes_a_marker_with_the_pid_and_the_start_time(env):
    assert H.hold([7], by="manual", minutes=60, state=env, now=NOW, procs={}) == 0
    assert H.gate(7, PID, state=env, now=NOW, procs=alive()) == 0
    fields = dict(line.split(" ", 1) for line in (env / "auto" / "gate-7").read_text().splitlines())
    assert fields["pid"] == str(PID)
    assert int(fields["start"]) == int(GATE_START)


def test_gate_leaves_the_three_line_hold_file_as_it_was(env):
    assert H.hold([7], by="manual", minutes=60, state=env, now=NOW, procs={}) == 0
    before = read(env, 7)
    assert H.gate(7, PID, state=env, now=NOW, procs=alive()) == 0
    assert read(env, 7) == before
    assert len(before.splitlines()) == 3


def test_gate_refuses_an_issue_with_no_hold_and_writes_no_marker(env, capsys):
    assert H.gate(7, PID, state=env, now=NOW, procs=alive()) == 1
    assert "no live hold" in capsys.readouterr().err
    assert not (env / "auto" / "gate-7").exists()


def test_gate_refuses_a_stale_hold(env, capsys):
    assert H.hold([7], by="manual", minutes=1, state=env, now=NOW - 7200, procs={}) == 0
    assert H.gate(7, PID, state=env, now=NOW, procs=alive()) == 1
    assert not (env / "auto" / "gate-7").exists()


def test_gate_refuses_a_pid_that_is_not_running(env):
    assert H.hold([7], by="manual", minutes=60, state=env, now=NOW, procs={}) == 0
    assert H.gate(7, PID, state=env, now=NOW, procs={}) == 1
    assert not (env / "auto" / "gate-7").exists()


def test_gates_names_the_issue_while_the_gate_process_lives(env):
    assert H.hold([7], by="manual", minutes=60, state=env, now=NOW, procs={}) == 0
    assert H.gate(7, PID, state=env, now=NOW, procs=alive()) == 0
    assert H.gates(state=env, procs=alive()) == {"7"}


def test_gates_drops_a_marker_whose_pid_is_gone(env):
    assert H.hold([7], by="manual", minutes=60, state=env, now=NOW, procs={}) == 0
    assert H.gate(7, PID, state=env, now=NOW, procs=alive()) == 0
    assert H.gates(state=env, procs={}) == set()


def test_gates_drops_a_marker_whose_pid_was_reused_by_a_later_process(env):
    assert H.hold([7], by="manual", minutes=60, state=env, now=NOW, procs={}) == 0
    assert H.gate(7, PID, state=env, now=NOW, procs=alive()) == 0
    assert H.gates(state=env, procs=alive(start=GATE_START + 500)) == set()


def test_gates_skips_a_malformed_marker(env):
    (env / "auto" / "gate-7").write_text("garbage\n")
    assert H.gates(state=env, procs=alive()) == set()


def test_a_release_removes_the_gate_marker(env):
    assert H.hold([7], by="manual", minutes=60, state=env, now=NOW, procs={}) == 0
    assert H.gate(7, PID, state=env, now=NOW, procs=alive()) == 0
    assert H.release([7], by="manual", to="Todo", state=env, now=NOW, procs={}) == 0
    assert not (env / "auto" / "gate-7").exists()


def test_the_gate_verb_is_a_cli_verb(env):
    assert H.hold([7], by="manual", minutes=60, state=env, now=NOW, procs={}) == 0
    assert H.main(["gate", "7", "--pid", str(PID)], state=env, now=NOW, procs=alive()) == 0
    assert H.gates(state=env, procs=alive()) == {"7"}


def test_gate_refuses_a_process_with_no_start_time(env, capsys):
    assert H.hold([7], by="manual", minutes=60, state=env, now=NOW, procs={}) == 0
    assert H.gate(7, PID, state=env, now=NOW, procs={PID: RL.Proc(None, "bash")}) == 1
    assert not (env / "auto" / "gate-7").exists()


@pytest.mark.parametrize("text", ["pid 4242\nstart \n", "pid 4242\n", "pid 4242\nstart x\n"])
def test_gates_drops_a_marker_whose_start_is_not_a_number(env, text):
    (env / "auto" / "gate-7").write_text(text)
    assert H.gates(state=env, procs=alive()) == set()


def test_gates_drops_a_marker_when_the_process_has_no_start_time(env):
    (env / "auto" / "gate-7").write_text(f"pid {PID}\nstart {int(GATE_START)}\n")
    assert H.gates(state=env, procs={PID: RL.Proc(None, "bash")}) == set()


def test_the_module_imports_where_datetime_utc_is_absent():
    """The janitor's board pass imports this module in-process (`janitor._handhold_module`), and the daily shell-suite
    pass can hand the janitor an interpreter older than 3.11 (#3058). `datetime.UTC` exists only from Python 3.11, so
    the module must import with that name absent, as Python 3.9 has it; a newer stdlib name on the import path would
    kill the whole board pass with a traceback."""
    code = "import datetime\ndel datetime.UTC\nfrom squad import handhold\nprint('ok')\n"
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "ok"
