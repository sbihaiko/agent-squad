"""Late board writes are reported by actor, and a verified correction keeps both instants (#1022, ADR-0164, ADR-0149).

board_lag.py reads the board facts of the issue ledger. A write that landed after its event is a late write; every positive
lag is recorded and shown. A correction names the transition it repairs, both instants and its evidence; an ambiguous match
is a conflict. Corrections live in a versioned file with a backup, and a lost file marks the late instants approximate.
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "contrib" / "squad-goal" / "scripts"
KEYS = ("repo", "issue", "project", "field", "transition")


@pytest.fixture
def bl(tmp_path, monkeypatch):
    monkeypatch.setenv("SG_STATE", str(tmp_path / "st"))
    monkeypatch.setenv("SG_REPO", "x/y")
    monkeypatch.delenv("SG_PROJECT", raising=False)
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location("board_lag", SCRIPTS / "board_lag.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    yield mod
    sys.path.remove(str(SCRIPTS))


def write(bl, n, instant, landed, actor="autobug dispatch", transition="run-a/Doing"):
    return {"repo": "x/y", "issue": str(n), "project": "4", "field": "Status", "transition": transition,
            "actor": actor, "instant": instant, "landed": landed}


def test_only_a_positive_lag_is_a_late_write(bl):
    assert bl.lag(100.0, 100.0) is None
    assert bl.lag(100.0, 99.0) is None
    assert bl.lag(100.0, 100.001) == pytest.approx(0.001)
    assert bl.lag(None, 100.0) is None
    assert bl.lag(100.0, None) is None


def test_a_tiny_positive_lag_is_recorded_and_shown(bl):
    lates = bl.late_writes([write(bl, 1, 100.0, 100.002)])
    assert len(lates) == 1 and lates[0]["lag"] == pytest.approx(0.002)
    rows = bl.monitor(lates, [], "ok")["rows"]
    assert [r["actor"] for r in rows] == ["autobug dispatch"]
    assert "0.002 s" in rows[0]["text"]


def test_lags_are_grouped_by_actor(bl):
    lates = bl.late_writes([
        write(bl, 1, 100.0, 105.0, actor="autobug dispatch"),
        write(bl, 2, 100.0, 103.0, actor="autobug dispatch"),
        write(bl, 3, 200.0, 201.0, actor="autobug resume"),
        write(bl, 4, 300.0, 300.0, actor="autobug resume"),
    ])
    by = {r["actor"]: r for r in bl.by_actor(lates)}
    assert by["autobug dispatch"]["count"] == 2
    assert by["autobug dispatch"]["max"] == pytest.approx(5.0)
    assert by["autobug dispatch"]["total"] == pytest.approx(8.0)
    assert by["autobug resume"]["count"] == 1


def test_a_correction_needs_every_key_its_instants_and_evidence(bl):
    lates = bl.late_writes([write(bl, 1, 100.0, 105.0)])
    base = {k: lates[0][k] for k in KEYS}
    with pytest.raises(bl.Conflict):
        bl.make_correction({**base, "field": ""}, 105.0, 100.0, "pending event", lates)
    with pytest.raises(bl.Conflict):
        bl.make_correction(base, 105.0, 100.0, "  ", lates)


def test_a_correction_cannot_move_an_instant_after_its_write(bl):
    lates = bl.late_writes([write(bl, 1, 100.0, 105.0)])
    base = {k: lates[0][k] for k in KEYS}
    with pytest.raises(bl.Conflict):
        bl.make_correction(base, 105.0, 110.0, "ledger", lates)


def test_an_ambiguous_match_is_a_conflict_and_never_guessed(bl):
    lates = bl.late_writes([write(bl, 1, 100.0, 105.0), write(bl, 1, 100.0, 105.0, transition="run-b/Doing")])
    key = {k: lates[0][k] for k in KEYS}
    twice = lates + [write(bl, 1, 100.0, 105.0)]
    with pytest.raises(bl.Conflict, match="2 candidates"):
        bl.make_correction(key, 105.0, 100.0, "ledger", twice)
    with pytest.raises(bl.Conflict, match="no candidate"):
        bl.make_correction({**key, "issue": "9"}, 105.0, 100.0, "ledger", lates)


def test_one_match_is_a_correction_with_both_instants(bl):
    lates = bl.late_writes([write(bl, 1, 100.0, 105.0)])
    key = {k: lates[0][k] for k in KEYS}
    c = bl.make_correction(key, 105.0, 100.0, "pending event in the ledger", lates)
    assert c["landed"] == 105.0 and c["effective"] == 100.0 and c["evidence"] == "pending event in the ledger"
    assert all(c[k] == key[k] for k in KEYS)


def test_the_store_round_trips_and_writes_a_backup(bl, tmp_path):
    p = tmp_path / "corr.json"
    lates = bl.late_writes([write(bl, 1, 100.0, 105.0)])
    key = {k: lates[0][k] for k in KEYS}
    c = bl.make_correction(key, 105.0, 100.0, "ledger", lates)
    bl.write_store(p, [c])
    assert json.loads(p.read_text())["version"] == bl.VERSION
    assert bl.read_store(p) == ([c], "ok")
    assert bl.read_store(bl.backup_path(p)) == ([c], "ok")
    p.write_text("not json")
    assert bl.read_store(p) == ([c], "backup")


def test_a_lost_store_reads_lost_and_a_missing_one_absent(bl, tmp_path):
    p = tmp_path / "corr.json"
    assert bl.read_store(p) == ([], "absent")
    p.write_text("{")
    bl.backup_path(p).write_text("{")
    assert bl.read_store(p) == ([], "lost")


def test_a_wrong_version_is_a_corrupt_store(bl, tmp_path):
    p = tmp_path / "corr.json"
    p.write_text(json.dumps({"version": 99, "corrections": []}))
    assert bl.read_store(p) == ([], "lost")


def test_an_interrupted_store_write_leaves_the_old_file_whole(bl, tmp_path, monkeypatch):
    p = tmp_path / "corr.json"
    lates = bl.late_writes([write(bl, 1, 100.0, 105.0)])
    key = {k: lates[0][k] for k in KEYS}
    c = bl.make_correction(key, 105.0, 100.0, "ledger", lates)
    bl.write_store(p, [c])
    before = p.read_text()

    def boom(*a, **k):
        raise OSError("killed between the temp file and the rename")
    monkeypatch.setattr(bl.os, "replace", boom)
    with pytest.raises(OSError):
        bl.write_store(p, [])
    assert p.read_text() == before
    assert not [x for x in tmp_path.iterdir() if x.name.endswith(".tmp")]


def test_a_correction_repairs_its_late_write_and_others_stay_approximate(bl):
    lates = bl.late_writes([write(bl, 1, 100.0, 105.0), write(bl, 2, 100.0, 104.0)])
    key = {k: lates[0][k] for k in KEYS}
    c = bl.make_correction(key, 105.0, 100.0, "ledger", lates)
    out = {r["issue"]: r for r in bl.resolve(lates, [c])}
    assert out["1"]["effective"] == 100.0 and out["1"]["approximate"] is False
    assert out["2"]["effective"] == 104.0 and out["2"]["approximate"] is True


def test_a_lost_store_marks_every_late_instant_approximate(bl):
    lates = bl.late_writes([write(bl, 1, 100.0, 105.0)])
    out = bl.resolve(lates, [])
    assert out[0]["approximate"] is True and out[0]["effective"] == 105.0


def test_a_correction_whose_write_instant_changed_does_not_apply(bl):
    lates = bl.late_writes([write(bl, 1, 100.0, 105.0)])
    key = {k: lates[0][k] for k in KEYS}
    c = bl.make_correction(key, 105.0, 100.0, "ledger", lates)
    later = bl.late_writes([write(bl, 1, 100.0, 106.0)])
    assert bl.resolve(later, [c])[0]["approximate"] is True


def test_the_payload_reads_the_ledger_and_the_store(bl):
    import issue_ledger
    issue_ledger.board("x/y#1", "run-a", "Doing", "/req", instant=100.0)
    issue_ledger.boardw("x/y#1", "run-a", "Doing", actor="autobug dispatch")
    got = bl.payload("x/y")
    assert got["store"] == "absent"
    assert got["late"] == 1 and got["approximate"] == 1
    assert got["rows"][0]["actor"] == "autobug dispatch"


def test_the_ledger_records_when_and_by_whom_a_board_write_landed(bl):
    import issue_ledger
    issue_ledger.board("x/y#2", "run-a", "Doing", "/req", instant=100.0)
    issue_ledger.boardw("x/y#2", "run-a", "Doing", actor="autobug dispatch")
    row = [r for r in issue_ledger.board_writes("x/y") if r["issue"] == "2"][0]
    assert row["instant"] == 100.0 and row["actor"] == "autobug dispatch" and row["landed"] >= 100.0
    assert row["transition"] == "run-a/Doing" and row["project"] == "" and row["field"] == "Status"
