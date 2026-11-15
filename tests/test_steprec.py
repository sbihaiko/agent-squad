"""The step records both lanes write: one fold turns the file's lines into steps with a state (#1750).

A record is one tab-separated line. A start holds the kind, the step's id, its name, the epoch it began at and two
placeholders; a finish holds the same first four fields, the state it ended in (`done` or `skipped`) and the detail: the
exit status a done step carried, or the reason a skipped one gives. A finish with no start is no step, and a line that is
no record at all -- the fragment a size cut leaves -- is skipped, never guessed at.
"""
import importlib.util
import pathlib

SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "contrib" / "squad-goal" / "scripts"
_spec = importlib.util.spec_from_file_location("steprec", SCRIPTS / "steprec.py")
sr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sr)


def start(rid, name, epoch):
    return f"start\t{rid}\t{name}\t{epoch}\t-\t-"


def end(rid, name, epoch, state="done", detail="0"):
    return f"end\t{rid}\t{name}\t{epoch}\t{state}\t{detail}"


def test_a_start_and_its_finish_fold_into_one_done_step_with_its_exit_status():
    steps = sr.fold([start("a", "janitor", 1_800_000_000), end("a", "janitor", 1_800_000_042)])
    assert steps == [{"id": "a", "name": "janitor", "start": 1_800_000_000, "end": 1_800_000_042,
                      "state": "done", "rc": 0, "reason": None}]


def test_a_done_step_keeps_the_exit_status_it_carried():
    steps = sr.fold([start("a", "branchland", 1_800_000_000), end("a", "branchland", 1_800_000_003, "done", "1")])
    assert (steps[0]["state"], steps[0]["rc"]) == ("done", 1)


def test_a_step_whose_finish_is_missing_reads_as_running():
    """The record that proves a detached pass is in flight: its start is there and its finish is not."""
    steps = sr.fold([start("b", "autofrontier", 1_800_000_000)])
    assert [(s["name"], s["state"], s["end"]) for s in steps] == [("autofrontier", "running", None)]


def test_a_skipped_step_keeps_its_reason():
    steps = sr.fold([start("c", "promote", 1_800_000_000),
                     end("c", "promote", 1_800_000_001, "skipped", "a pass in flight holds the promote lock")])
    assert (steps[0]["state"], steps[0]["reason"], steps[0]["rc"]) == \
        ("skipped", "a pass in flight holds the promote lock", None)


def test_a_file_cut_in_two_loses_only_the_lines_the_cut_broke():
    """The cut keeps the newest half: the first line is the fragment of the record it split, and that record's start is gone."""
    lines = ["promote\t1_800_000_001\tdone",
             start("d", "janitor", 1_800_000_300), end("d", "janitor", 1_800_000_301),
             start("e", "autofrontier", 1_800_000_302)]
    steps = sr.fold(lines)
    assert [(s["name"], s["state"]) for s in steps] == [("janitor", "done"), ("autofrontier", "running")]


def test_a_finish_whose_start_the_cut_removed_is_no_step():
    steps = sr.fold([end("gone", "nightly", 1_800_000_400), start("f", "janitor", 1_800_000_401)])
    assert [s["id"] for s in steps] == ["f"]


def test_a_line_that_is_no_record_is_skipped():
    steps = sr.fold(["", "junk", start("g", "janitor", "not-an-epoch"), start("h", "heartbeat.sh", 1_800_000_500)])
    assert [s["name"] for s in steps] == ["heartbeat.sh"]


def test_the_steps_come_in_the_order_their_starts_were_written():
    steps = sr.fold([start("1", "first", 1_800_000_900), start("2", "second", 1_800_000_100),
                     end("2", "second", 1_800_000_101)])
    assert [s["name"] for s in steps] == ["first", "second"]
