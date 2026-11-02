"""The gate result on the hub's line: gate.sh leaves one status line, $SG_STATE/gate-status (`<epoch> TAB <kind> TAB
<pass|fail> TAB <sha>`), and the footer reads it as one fact: the kind, the result, the sha it ran at and its age."""
import importlib.util
import os
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"
NOW = 1_000_000.0
SHA = "0123456789abcdef0123456789abcdef01234567"


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ["SG_CHECKOUT"] = tempfile.mkdtemp()
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_gate_fact", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _gate(hub, **over):
    src = {"occ": {"live": 1, "agents": 1}, "quota": {"codex": None, "agy": None},
           "gate": {"kind": "fast", "result": "pass", "sha": SHA, "ts": NOW - 240} | over}
    return [i for i in hub.foot_items(src, NOW) if i["key"] == "gate"]


def test_a_passing_gate_reads_kind_result_and_age_with_the_sha_on_the_tooltip(hub):
    """#455: the sha is a second number the reader matches against a list, never one read across the room, so it is the
    block's tooltip — MOVED off the row, never lost."""
    (fact,) = _gate(hub)
    assert fact["text"] == "gate fast pass 4m ago"
    assert fact["tip"] == "ran at 0123456"
    assert not fact["bad"]


def test_a_red_full_gate_reads_in_the_error_tone(hub):
    (fact,) = _gate(hub, kind="full", result="fail")
    assert fact["text"] == "gate full fail 4m ago" and fact["tip"] == "ran at 0123456"
    assert any(p["tone"] == "err" for p in fact["parts"])


def test_no_gate_reading_draws_no_block(hub):
    src = {"occ": {"live": 1, "agents": 1}, "quota": {"codex": None, "agy": None}}
    assert [i["key"] for i in hub.foot_items(src, NOW) if i["key"] == "gate"] == []


def test_a_line_the_hub_cannot_read_is_a_question_mark_never_a_pass(hub):
    (fact,) = _gate(hub, result="maybe", sha="")
    assert "pass" not in fact["text"] and "?" in fact["text"]


def test_a_fast_status_line_carries_the_test_selection(hub, tmp_path, monkeypatch):
    st = tmp_path / "st"
    st.mkdir()
    monkeypatch.setattr(hub, "STATE", st)
    (st / "gate-status").write_text(f"{int(NOW - 60)}\tfast\tpass\t{SHA}\tselected 2 files\n")
    assert hub.foot_sources(NOW)["gate"] == {
        "kind": "fast", "result": "pass", "sha": SHA, "ts": NOW - 60, "selection": "selected 2 files",
    }
    (st / "gate-status").write_text(f"{int(NOW - 60)}\tfast\tpass\t{SHA}\tall: no test imports squad/x.py\n")
    assert hub.foot_sources(NOW)["gate"]["selection"] == "all: no test imports squad/x.py"
    (st / "gate-status").write_text(f"{int(NOW - 60)}\tfast\tpass\t{SHA}\t\n")
    assert hub.foot_sources(NOW)["gate"] is None


def test_a_gate_line_carries_the_test_scope_beside_the_selection(hub, tmp_path, monkeypatch):
    """The sixth field is the scope the selection names: `delta` for the tests a change reaches, `full` for the battery.
    A line whose sixth field is empty or names no scope is not a gate line, so the footer draws no pass from it."""
    st = tmp_path / "st"
    st.mkdir()
    monkeypatch.setattr(hub, "STATE", st)
    (st / "gate-status").write_text(f"{int(NOW - 60)}\tfast\tpass\t{SHA}\tselected 2 files\tdelta\n")
    assert hub.foot_sources(NOW)["gate"] == {
        "kind": "fast", "result": "pass", "sha": SHA, "ts": NOW - 60,
        "selection": "selected 2 files", "scope": "delta",
    }
    (st / "gate-status").write_text(f"{int(NOW - 60)}\tfast\tpass\t{SHA}\tall: no test reaches docs/x.md\tfull\n")
    fact = hub.foot_sources(NOW)["gate"]
    assert fact["scope"] == "full" and fact["selection"].startswith("all: ")
    (st / "gate-status").write_text(f"{int(NOW - 60)}\tfast\tpass\t{SHA}\tselected 2 files\t\n")
    assert hub.foot_sources(NOW)["gate"] is None
    (st / "gate-status").write_text(f"{int(NOW - 60)}\tfast\tpass\t{SHA}\tselected 2 files\tsometimes\n")
    assert hub.foot_sources(NOW)["gate"] is None


def test_the_status_line_gate_sh_writes_is_the_one_the_footer_reads(hub, tmp_path, monkeypatch):
    st = tmp_path / "st"
    st.mkdir()
    (st / "gate-status").write_text(f"{int(NOW - 60)}\tfull\tpass\t{SHA}\n")
    monkeypatch.setattr(hub, "STATE", st)
    assert hub.foot_sources(NOW)["gate"] == {"kind": "full", "result": "pass", "sha": SHA, "ts": NOW - 60}
    (st / "gate-status").write_text("not a status line\n")
    assert hub.foot_sources(NOW)["gate"] is None
    (st / "gate-status").unlink()
    assert hub.foot_sources(NOW)["gate"] is None


def test_the_gate_times_come_from_the_history_of_gates_that_ran(hub, tmp_path, monkeypatch):
    """The owner: how long the last gate took and the mean of the recent ones. A line whose start equals its finish
    reused a verdict and ran nothing, so it is neither the last run nor in the mean; another kind is not in the mean."""
    rows = [(1000, "fast", 100), (5000, "full", 3000), (3000, "fast", 200), (4000, "fast", 0)]
    (tmp_path / "gate-history").write_text("".join(f"{f}\t{k}\tpass\t{SHA}\t{f - d}\n" for f, k, d in rows))
    monkeypatch.setattr(hub, "STATE", tmp_path)
    assert hub._gate_times("fast") == (200, 150)
    assert hub._gate_times("full") == (3000, 3000)
    assert hub._gate_times("none") == (None, None)


def test_the_gate_fact_carries_its_times_off_the_footer_text(hub):
    (fact,) = _gate(hub, took=127, avg=169)
    assert fact["text"] == "gate fast pass 4m ago"
    assert fact["parts"][0]["times"] == "took 2m07s · avg 2m49s"
    (plain,) = _gate(hub)
    assert "times" not in plain["parts"][0]


def test_a_gate_time_under_a_minute_reads_in_seconds(hub):
    """`avg 47s`, never `avg 0m47s`: the gate tile's times keep their own format whatever else formats a span."""
    part = hub._gate_parts({"kind": "fast", "result": "pass", "sha": "a" * 40, "ts": 1000.0, "took": 124, "avg": 47}, 1010.0)[0]
    assert part["times"] == "took 2m04s · avg 47s"
