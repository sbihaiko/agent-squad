"""GitHub issue #598: the hub WORKERS panel shows the childcc pass and the children it reviews.

The pass writes `auto/childcc-now.json`: its pid and start, and one entry per child it holds (run, child, issue, step, and
since when it waits). The hub reads that file, never the pass: a live pass is one row with its pid and age, one line under
it per child, and the deferred count with the oldest wait. With no live pass the row keeps the deferred count; with nothing
waiting there is no row.
"""
import importlib.util
import json
import os
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib" / "squad-goal" / "scripts" / "hub.py"
NOW = 1_800_000_000.0


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_childcc_row", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def state(hub, monkeypatch, tmp_path):
    (tmp_path / "auto").mkdir()
    monkeypatch.setattr(hub, "STATE", tmp_path)
    return tmp_path


def publish(state, pid, children, t0=NOW - 300):
    (state / "auto" / "childcc-now.json").write_text(json.dumps({"pid": pid, "t0": t0, "children": children}))


def child(run, name, issue, step, since=NOW - 60, rk=None):
    return {"run": run, "child": name, "issue": issue, "step": step, "since": since, "rk": rk}


def test_a_live_pass_is_one_row_with_pid_age_and_a_line_per_child(hub, state):
    (state / "review-gui_598.model").write_text("gpt-6.1-sol\n")
    publish(state, os.getpid(), [child("run-a", "c1", 598, "review", rk="gui_598"),
                                 child("run-a", "c2", 599, "enter"),
                                 child("run-b", "c1", 600, "deferred", since=NOW - 600),
                                 child("run-b", "c2", 601, "deferred", since=NOW - 120)])
    row = hub.childcc_row(NOW)
    assert row["kind"] == "childcc" and row["live"] is True and row["rank"] == 0
    assert row["pid"] == os.getpid() and row["age"] == 300
    assert row["lines"] == ["run-a c1 #598 · in review with gpt-6.1-sol",
                            "run-a c2 #599 · entering wave",
                            "run-b c1 #600 · deferred",
                            "run-b c2 #601 · deferred"]
    assert row["deferred"] == 2 and row["oldest_wait"] == 600
    assert row["label"] == f"childcc pass · pid {os.getpid()}"
    assert row["sub"] == "CHILDCC DEFERRED 2 · oldest wait 10 min"


def test_a_review_with_no_model_yet_reads_in_review(hub, state):
    publish(state, os.getpid(), [child("run-a", "c1", 598, "review", rk="gui_598")])
    assert hub.childcc_row(NOW)["lines"] == ["run-a c1 #598 · in review"]


def test_no_live_pass_shows_the_deferred_count_and_the_oldest_wait(hub, state):
    publish(state, None, [child("run-b", "c1", 600, "deferred", since=NOW - 900),
                          child("run-b", "c2", 601, "deferred", since=NOW - 60)])
    row = hub.childcc_row(NOW)
    assert row["live"] is False and row["pid"] is None and row["state"] == "waiting"
    assert row["label"] == "CHILDCC DEFERRED 2"
    assert row["sub"] == "CHILDCC DEFERRED 2 · oldest wait 15 min"
    assert row["deferred"] == 2 and row["oldest_wait"] == 900


def test_a_dead_pass_is_no_live_pass_and_its_children_still_wait(hub, state):
    publish(state, 2 ** 22 + 7, [child("run-a", "c1", 598, "review", since=NOW - 120)])
    row = hub.childcc_row(NOW)
    assert row["live"] is False and row["deferred"] == 1 and row["oldest_wait"] == 120


def test_nothing_waiting_shows_no_row(hub, state):
    assert hub.childcc_row(NOW) is None   # no file
    publish(state, None, [])
    assert hub.childcc_row(NOW) is None
    publish(state, os.getpid(), [])
    assert hub.childcc_row(NOW) is not None   # a live pass with nothing held is still a live pass
    assert hub.childcc_row(NOW)["lines"] == []


def test_the_row_is_not_a_run(hub, state):
    publish(state, None, [child("run-b", "c1", 600, "deferred")])
    assert hub.working([hub.childcc_row(NOW)]) == []
