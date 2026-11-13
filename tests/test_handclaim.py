"""The hand claim: a person's or an operator session's own work on a card, kept outside the loop (loop-bug #1664).

One file per issue, `$SG_STATE/auto/hand-<n>`, holds `by`, `since` and `until`. A claim is refused when a live run holds
the issue, when another carrier holds it, or when a `taken-<run>` marker names it. A batch is all or nothing. Every write
verb touches `board-dirty`. These cases use a temp state folder, injected processes and a fixed clock: no network, no gh,
no live state.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from squad import handclaim as H
from squad import run_liveness as RL

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
    monkeypatch.delenv("SG_HAND_CLAIM_MINUTES", raising=False)
    return state


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
    return (state / "auto" / f"hand-{n}").read_text()


def test_claim_writes_the_file_and_its_three_fields(env):
    rc = H.claim([1664], by="manual", minutes=60, state=env, now=NOW, procs={}, cwd_procs=lambda: [])
    assert rc == 0
    fields = dict(line.split(" ", 1) for line in read(env, 1664).splitlines())
    assert fields["by"] == "manual"
    assert fields["since"] == H.iso(NOW)
    assert fields["until"] == H.iso(NOW + 3600)


def test_claim_refused_when_a_live_run_holds_the_issue_and_writes_nothing(env, tmp_path, capsys):
    folder = run_folder(tmp_path, RUN, 1664, live=True)
    listed(env, folder)
    rc = H.claim([1664], by="manual", state=env, now=NOW, procs=live_procs(True), cwd_procs=lambda: [])
    assert rc == 1
    assert not (env / "auto" / "hand-1664").exists()
    assert RUN in capsys.readouterr().err


def test_claim_of_an_issue_a_dead_run_held_is_not_refused(env, tmp_path):
    folder = run_folder(tmp_path, RUN, 1664, live=False, verdict=True)
    listed(env, folder)
    rc = H.claim([1664], by="manual", state=env, now=NOW, procs=live_procs(False), cwd_procs=lambda: [])
    assert rc == 0


def test_claim_refused_for_another_carrier_claim(env):
    H.claim([1664], by="hal9k", state=env, now=NOW, procs={}, cwd_procs=lambda: [])
    before = read(env, 1664)
    rc = H.claim([1664], by="manual", state=env, now=NOW + 5, procs={}, cwd_procs=lambda: [])
    assert rc == 1
    assert read(env, 1664) == before   # never overwritten


def test_claim_refused_when_a_taken_marker_names_the_issue(env, capsys):
    (env / "auto" / "taken-run-x").write_text(f"run run-x\nissues 1664 1663\n")
    rc = H.claim([1664], by="manual", state=env, now=NOW, procs={}, cwd_procs=lambda: [])
    assert rc == 1
    assert not (env / "auto" / "hand-1664").exists()
    assert "taken" in capsys.readouterr().err


def test_a_batch_with_one_refusal_writes_no_file_for_any_issue(env, capsys):
    (env / "auto" / "taken-run-x").write_text("run run-x\nissues 2\n")
    rc = H.main(["claim", "1", "2", "3", "--by", "manual"], state=env, now=NOW, procs={}, cwd_procs=lambda: [])
    assert rc == 1
    for n in (1, 2, 3):
        assert not (env / "auto" / f"hand-{n}").exists()
    err = capsys.readouterr().err.strip().splitlines()
    assert len(err) == 1 and "#2" in err[0]


def test_renew_refuses_another_carrier(env, capsys):
    H.claim([1664], by="hal9k", state=env, now=NOW, procs={}, cwd_procs=lambda: [])
    before = read(env, 1664)
    rc = H.renew([1664], by="manual", state=env, now=NOW + 60, procs={}, cwd_procs=lambda: [])
    assert rc == 1
    assert read(env, 1664) == before
    assert "hal9k" in capsys.readouterr().err


def test_renew_moves_until_forward_and_keeps_since(env):
    H.claim([1664], by="manual", minutes=60, state=env, now=NOW, procs={}, cwd_procs=lambda: [])
    rc = H.renew([1664], by="manual", minutes=120, state=env, now=NOW + 600, procs={}, cwd_procs=lambda: [])
    assert rc == 0
    fields = dict(line.split(" ", 1) for line in read(env, 1664).splitlines())
    assert fields["since"] == H.iso(NOW)
    assert fields["until"] == H.iso(NOW + 600 + 7200)


def test_release_removes_the_file_and_prints_the_named_column(env, capsys):
    H.claim([1664], by="manual", state=env, now=NOW, procs={}, cwd_procs=lambda: [])
    assert (env / "auto" / "hand-1664").exists()
    rc = H.release([1664], by="manual", to="Closed", state=env, now=NOW, procs={}, cwd_procs=lambda: [])
    assert rc == 0
    assert not (env / "auto" / "hand-1664").exists()
    assert "Closed" in capsys.readouterr().out


def test_release_without_a_column_says_the_card_stays(env, capsys):
    H.claim([1664], by="manual", state=env, now=NOW, procs={}, cwd_procs=lambda: [])
    H.release([1664], by="manual", state=env, now=NOW, procs={}, cwd_procs=lambda: [])
    assert "stays" in capsys.readouterr().out


def test_release_of_a_stale_claim_still_works(env):
    H.claim([1664], by="manual", minutes=1, state=env, now=NOW, procs={}, cwd_procs=lambda: [])
    rc = H.release([1664], by="manual", state=env, now=NOW + 86400, procs={}, cwd_procs=lambda: [])
    assert rc == 0
    assert not (env / "auto" / "hand-1664").exists()


def test_claims_reports_stale_past_until(env):
    H.claim([1664], by="hal9k", minutes=30, state=env, now=NOW, procs={}, cwd_procs=lambda: [])
    fresh = H.claims(state=env, now=NOW + 60)
    late = H.claims(state=env, now=NOW + 3600)
    assert fresh[0]["issue"] == 1664 and fresh[0]["carrier"] == "hal9k" and fresh[0]["stale"] is False
    assert late[0]["stale"] is True
    assert late[0]["until"] == H.iso(NOW + 1800)


def test_a_finished_run_holds_no_issue(env, tmp_path):
    folder = run_folder(tmp_path, RUN, 1664, live=True, verdict=True)
    listed(env, folder)
    rc = H.claim([1664], by="manual", state=env, now=NOW, procs=live_procs(True), cwd_procs=lambda: [])
    assert rc == 0


@pytest.mark.parametrize("verb", ["claim", "renew", "release"])
def test_every_write_verb_creates_board_dirty(env, verb):
    H.claim([1664], by="manual", state=env, now=NOW, procs={}, cwd_procs=lambda: [])
    dirty = env / "board-dirty"
    dirty.unlink(missing_ok=True)   # the setup claim touched it; the verb under test must touch it again
    if verb == "claim":
        H.claim([1700], by="manual", state=env, now=NOW, procs={}, cwd_procs=lambda: [])
    elif verb == "renew":
        H.renew([1664], by="manual", state=env, now=NOW, procs={}, cwd_procs=lambda: [])
    else:
        H.release([1664], by="manual", state=env, now=NOW, procs={}, cwd_procs=lambda: [])
    assert dirty.exists()
