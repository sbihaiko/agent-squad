"""Who holds each run slot when the frontier refuses a startable card for "no slot" (#2286).

The frontier pass writes one record per refusal: the instant, the free slots, the startable Todo cards and the holder
of each slot. The record names one kind per holder, and the Loop tab folds the records into the minutes of Todo wait
per kind (`hub_profiler.slot_wait`). The classifier and the record's file hold no reader of GitHub and no process
table: the pass hands them the facts it already holds.
"""
import json

import pytest

from squad import slot_holders as SH


def lines(path):
    """The record's own lines: the writer promises one JSON object a line, and the reader of the page takes it from
    there (ADR-0030 keeps each write whole)."""
    return [json.loads(raw) for raw in path.read_text().splitlines() if raw.strip()]


def test_a_pass_records_the_kind_of_every_slot_it_holds(tmp_path, monkeypatch):
    """#2286: the pass folds the counters it already reads into one record: the gate waiters, the autobug run, the run
    with a card in Doing, and a holder it cannot explain."""
    from squad import caps as C
    co = tmp_path / "co"
    (co / "runs" / "run-a").mkdir(parents=True)
    (co / "runs" / "run-a" / "record.jsonl").write_text('{"event": "run_start"}\n')
    (co / "runs" / "run-b").mkdir(parents=True)
    (co / "runs" / "run-b" / "record.jsonl").write_text('{"event": "run_start"}\n')
    (co / "runs" / "run-b" / "request.txt").write_text("CHILD — GitHub issue(s) 77 (bug; one root cause, one branch):\n")
    (co / "runs" / "run-c").mkdir(parents=True)
    (co / "runs" / "run-c" / "record.jsonl").write_text('{"event": "run_start"}\n')
    (co / "runs" / "run-c" / "request.txt").write_text("CHILD — GitHub issue(s) 88 (feature):\n")
    st = tmp_path / "st"
    (st / "auto").mkdir(parents=True)
    (st / "auto" / "bug-77").write_text("autobug-20261118-101010\n")
    (st / "auto" / "disp-88").write_text("run-c\n")

    class Ceil:
        caps = {"runs": 3, "children": 6, "gates": 2, "reviews": 1}
        def run_holders(self, active_runs, root=".", now=None):
            return ["run-a", "run-b", "run-c", "run-d"]
        def waiting(self):
            return [{"ceiling": "gates", "holder": "run-a"}]
    monkeypatch.setattr(C, "ceiling", lambda checkout=None: Ceil())
    monkeypatch.setattr(C, "free_runs", lambda c, a, root=".": 0)
    rec = SH.record_pass(st, 4, str(co))
    assert {h["name"]: h["kind"] for h in rec["holders"]} == {
        "run-a": "gates", "run-b": "autobug", "run-c": "doing", "run-d": "unknown"}
    assert (rec["free"], rec["cap"], rec["todo"]) == (0, 3, 4)
    assert lines(st / SH.FILE) == [rec], "the pass writes one line, and it is the record it answers"


def test_a_pass_whose_ceiling_cannot_be_read_writes_no_guess(tmp_path, monkeypatch):
    """#2286: the record names facts the pass already holds, so a ceiling it cannot read writes no record and raises:
    the pass names the failure instead of recording an invented holder."""
    from squad import caps as C
    st = tmp_path / "st"
    st.mkdir()

    def broken(checkout=None):
        raise RuntimeError("no caps block")
    monkeypatch.setattr(C, "ceiling", broken)
    with pytest.raises(RuntimeError):
        SH.record_pass(st, 3, str(tmp_path))
    assert not (st / SH.FILE).exists(), "no record is written from a ceiling the pass could not read"


def test_an_autobug_marker_the_request_does_not_name_is_another_runs(tmp_path):
    """#2286: the marker set and the run's own request decide together, so a marker of another issue never names this
    run, and a request that names the issue names it whatever the text around the number."""
    co = tmp_path / "co"
    (co / "runs" / "run-a").mkdir(parents=True)
    (co / "runs" / "run-a" / "request.txt").write_text("CHILD — GitHub issue(s) 77 (bug; one root cause, one branch):\n")
    (co / "runs" / "run-b").mkdir(parents=True)
    (co / "runs" / "run-b" / "request.txt").write_text("CHILD — GitHub issue(s) 777 (bug; one root cause, one branch):\n")
    st = tmp_path / "st"
    (st / "auto").mkdir(parents=True)
    (st / "auto" / "bug-77").write_text("a\n")
    assert SH._autobug_holders(st, co, ["run-a", "run-b"]) == {"run-a"}, \
        "the digits of issue 777 are no match for the marker of issue 77"


def test_a_request_that_cites_an_issue_in_passing_is_no_autobug_holder(tmp_path):
    """#2286: only the claim line the dispatch writes names the issue a run works, so a request that cites issue 77 as
    a reference in passing is no proof that this run is the autobug run of issue 77."""
    co = tmp_path / "co"
    (co / "runs" / "run-a").mkdir(parents=True)
    (co / "runs" / "run-a" / "request.txt").write_text(
        "CHILD — GitHub issue(s) 501 (bug; one root cause, one branch):\n"
        "see GitHub issue #77 as a reference\n")
    st = tmp_path / "st"
    (st / "auto").mkdir(parents=True)
    (st / "auto" / "bug-77").write_text("a\n")
    assert SH._autobug_holders(st, co, ["run-a"]) == set(), \
        "a cited issue in the prose is no claim of the dispatch"


def test_a_claim_line_names_every_issue_of_its_child(tmp_path):
    """#2286: the dispatch line names a cluster's whole issue list, so each number of that line is the run's own."""
    co = tmp_path / "co"
    (co / "runs" / "run-a").mkdir(parents=True)
    (co / "runs" / "run-a" / "request.txt").write_text(
        "CHILD — GitHub issue(s) 77, 78 (bug; one root cause, one branch):\n--- issue #77\n")
    st = tmp_path / "st"
    (st / "auto").mkdir(parents=True)
    (st / "auto" / "bug-77").write_text("a\n")
    (st / "auto" / "bug-78").write_text("a\n")
    assert SH._autobug_holders(st, co, ["run-a"]) == {"run-a"}, \
        "both issues of the claim line are the run's own"


def test_a_holder_is_classified_by_the_slot_it_holds():
    """#2286: a run waiting for a gate slot, an autobug run, a finished run waiting for its landing, a live run, and a
    holder the pass cannot explain, each answer their own kind, in that order of evidence."""
    assert SH.classify("run-a", '{"event": "run_start"}', waiting={"run-a"}, autobug=set()) == "gates"
    assert SH.classify("run-b", '{"event": "run_start"}', waiting=set(), autobug={"run-b"}) == "autobug"
    assert SH.classify("run-c", '{"event": "verdict"}', waiting=set(), autobug=set()) == "landing"
    assert SH.classify("run-d", '{"event": "run_start"}', waiting=set(), autobug=set(), card={"run-d"}) == "doing"
    assert SH.classify("run-e", '{"event": "run_start"}', waiting=set(), autobug=set()) == "unknown", \
        "a live run that names no card is unknown"
    assert SH.classify("run-f", None, waiting=set(), autobug=set()) == "unknown"


def test_a_slot_record_round_trips_through_its_file(tmp_path):
    """#2286: the pass writes one line per refusal, and each line answers every fact the fold reads."""
    path = tmp_path / SH.FILE
    SH.write(path, at=1000.0, free=0, cap=3, todo=4,
             holders=[{"name": "run-a", "kind": "doing"}, {"name": "run-b", "kind": "gates"}])
    SH.write(path, at=1060.0, free=1, cap=3, todo=2, holders=[{"name": "run-c", "kind": "landing"}])
    recs = lines(path)
    assert [r["at"] for r in recs] == [1000.0, 1060.0]
    assert recs[0]["todo"] == 4 and recs[0]["cap"] == 3 and recs[0]["free"] == 0
    assert recs[0]["holders"] == [{"name": "run-a", "kind": "doing"}, {"name": "run-b", "kind": "gates"}]
    assert recs[1]["holders"] == [{"name": "run-c", "kind": "landing"}]


def test_a_written_record_names_every_holder_once(tmp_path):
    """#2286: the record holds one entry per slot, so a holder seen twice is written once, and the kinds stay the five."""
    path = tmp_path / SH.FILE
    SH.write(path, at=1.0, free=0, cap=3, todo=3,
             holders=[{"name": "run-a", "kind": "doing"}, {"name": "run-a", "kind": "doing"},
                      {"name": "run-b", "kind": "unknown"}])
    rec = lines(path)[0]
    assert [h["name"] for h in rec["holders"]] == ["run-a", "run-b"]
    assert {h["kind"] for h in rec["holders"]} <= set(SH.KINDS)


def test_the_file_keeps_its_newest_records_only(tmp_path, monkeypatch):
    """#2286: the record grows on every refusal, so it keeps a bounded tail: the oldest lines leave first."""
    monkeypatch.setattr(SH, "MAX_RECORDS", 5)
    path = tmp_path / SH.FILE
    for i in range(8):
        SH.write(path, at=1000.0 + i, free=0, cap=3, todo=1, holders=[])
    recs = lines(path)
    assert [r["at"] for r in recs] == [1003.0, 1004.0, 1005.0, 1006.0, 1007.0]


def test_a_write_appends_its_line_instead_of_rewriting_the_file(tmp_path):
    """#2286: two overlapping passes write one record each. A write that reads the whole file and rewrites it drops the
    line another pass appended in that window, so the write appends its own line (`O_APPEND`) and only the tail prune
    replaces the file. The test proves it through the file: the inode the writer opens stays the same, and a line
    another pass wrote stays whole."""
    path = tmp_path / SH.FILE
    SH.write(path, at=1.0, free=0, cap=3, todo=2, holders=[])
    ino = path.stat().st_ino
    with open(path, "a", encoding="utf-8") as other:   # a second pass's line, appended while the file stands
        other.write(json.dumps({"at": 2.0, "free": 1, "cap": 3, "todo": 1, "holders": []}) + "\n")
    SH.write(path, at=3.0, free=0, cap=3, todo=2, holders=[])
    assert [r["at"] for r in lines(path)] == [1.0, 2.0, 3.0], "every line the file held stands, in its order"
    assert path.stat().st_ino == ino, \
        "the write appends its line to the file; it never replaces the file another pass holds open"


def test_a_write_leaves_no_partial_file_behind(tmp_path):
    """#2286: an interruptible write is atomic (ADR-0030), so the file always parses whole for the page that reads it."""
    path = tmp_path / SH.FILE
    SH.write(path, at=1.0, free=0, cap=3, todo=1, holders=[])
    assert sorted(p.name for p in tmp_path.iterdir()) == [SH.FILE], "the write's own temp file is gone"


def test_each_holder_kind_is_named_from_the_state_directory(tmp_path, monkeypatch):
    """#2363: a fixture state directory names the kind of every holder: a gate waiter, an autobug run, a wave, a finished
    run, a card dispatch with its issue, and a run that names no card with the age of its last progress."""
    import os
    import time
    from pathlib import Path

    from squad import caps as C
    co, st = tmp_path / "co", tmp_path / "st"

    def run(name, record, request=None):
        folder = co / "runs" / name
        folder.mkdir(parents=True)
        (folder / "record.jsonl").write_text(record)
        if request is not None:
            (folder / "request.txt").write_text(request)
        return folder

    started, finished = '{"event": "run_start"}\n', '{"event": "verdict"}\n'
    folders = [
        run("run-gate", started),
        run("run-bug", started, "CHILD — GitHub issue(s) 77 (bug; one root cause, one branch):\n"),
        run("run-wave", started),
        run("run-card", started, "CHILD — GitHub issue(s) 88 (feature):\n"),
        run("run-done", finished),
        run("run-idle", started),
    ]
    idle = folders[-1] / "record.jsonl"
    stamp = time.time() - 600
    os.utime(idle, (stamp, stamp))
    st.mkdir()
    (st / "active-runs.txt").write_text("\n".join(str(f) for f in folders) + "\n")
    (st / "auto").mkdir()
    (st / "auto" / "bug-77").write_text("autobug-20261118-101010\n")
    (st / "auto" / "disp-88").write_text("run-card\n")
    (st / "auto" / "wave-run-wave").write_text("wave/run-wave\n")

    class Ceil:
        caps = {"runs": 6, "children": 6, "gates": 2, "reviews": 1}
        def run_holders(self, active_runs, root=".", now=None):
            return [Path(r).name for r in Path(active_runs).read_text().split()]
        def waiting(self):
            return [{"ceiling": "gates", "holder": "run-gate"}]
    monkeypatch.setattr(C, "ceiling", lambda checkout=None: Ceil())
    monkeypatch.setattr(C, "free_runs", lambda c, a, root=".": 0)
    by = {h["name"]: h for h in SH.record_pass(st, 2, str(co))["holders"]}
    assert {n: h["kind"] for n, h in by.items()} == {
        "run-gate": "gates", "run-bug": "autobug", "run-wave": "wave", "run-card": "doing",
        "run-done": "landing", "run-idle": "unknown"}
    assert by["run-card"]["issue"] == 88, "a card dispatch names its issue"
    assert by["run-wave"]["wave"] == "wave/run-wave", "a wave names the wave it runs"
    assert by["run-idle"]["age_s"] == pytest.approx(600, abs=5), "an unknown holder carries its last progress age"
    assert "issue" not in by["run-gate"] and "age_s" not in by["run-gate"], "only the kinds that carry detail carry it"
