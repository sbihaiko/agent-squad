"""The nightly full suite on the hub's line (#704): nightly.sh leaves one result record, $SG_STATE/nightly-result
(`<epoch> TAB <pass|fail> TAB <sha> TAB <parallel> TAB <serial> TAB <ids>`), and the footer reads it as one fact: the
result, both modes, the sha it ran at, and the tests that failed."""
import importlib.util
import json
import os
import re
import shutil
import subprocess
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
    spec = importlib.util.spec_from_file_location("hub_nightly_fact", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


GREEN = {"result": "pass", "sha": SHA, "local": "pass", "github": "pass", "github_sha": SHA}


def _nightly(hub, **over):
    """The nightly fact for one local record; the GitHub half passed at the same sha unless `verdict` says otherwise."""
    src = {"occ": {"live": 1, "agents": 1}, "quota": {"codex": None, "agy": None},
           "nightly": {"result": "pass", "sha": SHA, "parallel": "pass", "serial": "pass", "ids": [], "ts": NOW - 240,
                       "verdict": GREEN} | over}
    return [i for i in hub.foot_items(src, NOW) if i["key"] == "nightly"]


def _paint(hub, items):
    """The tiles the page really paints for these facts, run through the instruments card's own functions in node (#983)."""
    if shutil.which("node") is None:
        pytest.skip("node is required")
    block = re.search(r"^// INST\{.*?^// \}INST$", hub.PAGE, re.M | re.S)
    assert block, "the page carries no instruments block"
    esc = re.search(r"^const esc=.*$", hub.PAGE, re.M)
    cells = re.findall(r"^function ld(?:Num|Round|Cell|Cells)\(.*$", hub.PAGE, re.M)
    script = "\n".join(["const LOAD_ERR=95;", esc.group(0), *cells, block.group(0)]) \
        + f"\nconsole.log(instTiles({json.dumps(items)}).map(tileHtml).join(''))"
    out = subprocess.run(["node", "-"], input=script, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return out.stdout.rstrip("\n")


def test_a_green_nightly_reads_its_result_and_age_with_both_modes_on_the_tooltip(hub):
    (fact,) = _nightly(hub)
    assert fact["text"] == "nightly pass 4m ago"
    assert fact["tip"] == "parallel pass · serial pass · ran at 0123456"
    assert not fact["bad"]


def test_a_red_nightly_reads_in_the_error_tone_and_names_the_tests_that_failed(hub):
    ids = ["tests/test_x.py::test_a", "tests/test_y.py::test_b"]
    (fact,) = _nightly(hub, result="fail", parallel="fail", ids=ids)
    assert fact["text"] == "nightly fail 4m ago"
    assert any(p["tone"] == "err" for p in fact["parts"])
    assert fact["tip"] == "parallel fail · serial pass · ran at 0123456 · 2 failed: " + ", ".join(ids)


def test_the_fact_carries_every_failing_test_while_the_line_stays_compact(hub):
    """The operator acts on the ids, so a red run carries ALL of them; the line keeps its one compact reading and the
    ids live in the expandable detail and the tooltip (#704, ADR-0153)."""
    ids = [f"tests/test_{n}.py::test_a" for n in "abcdefg"]
    (fact,) = _nightly(hub, result="fail", serial="fail", ids=ids)
    assert fact["text"] == "nightly fail 4m ago"
    assert fact["detail"] == ids
    assert all(i in fact["tip"] for i in ids)


def test_the_fact_lists_every_failing_test_and_the_page_draws_no_nightly_tile(hub):
    """The owner removed the Nightly tile and the alert strip; the fact keeps every failing id for /api/status."""
    ids = [f"tests/test_{n}.py::test_a" for n in "abcdefg"]
    (fact,) = _nightly(hub, result="fail", serial="fail", ids=ids)
    assert fact["detail"] == ids
    assert "Nightly" not in _paint(hub, [fact])


def test_a_leg_stopped_by_a_signal_reads_bounded_and_names_it_apart_from_the_finished_leg(hub):
    """A nightly leg that ended non-zero without naming a failing test measured nothing (#1183): the record says which mode
    finished and which was stopped, carries the signal it died of, and reads as unproven, never as a red (ADR-0164)."""
    (fact,) = _nightly(hub, result="bounded", serial="bounded:signal-15")
    assert fact["text"] == "nightly bounded 4m ago"
    assert not fact["bad"]
    assert fact["tip"] == "parallel pass · serial bounded:signal-15 · ran at 0123456"


def test_a_red_leg_a_signal_also_stopped_stays_red_and_names_the_signal(hub):
    """A leg that named a failing test and was then killed is still a red the suite measured (#1183): the result stays
    fail, the error tone stays, and the mode names the signal beside it, so the stop reason is not lost."""
    ids = ["tests/test_x.py::test_a"]
    (fact,) = _nightly(hub, result="fail", serial="fail:signal-15", ids=ids)
    assert fact["text"] == "nightly fail 4m ago"
    assert any(p["tone"] == "err" for p in fact["parts"])
    assert fact["tip"] == "parallel pass · serial fail:signal-15 · ran at 0123456 · 1 failed: " + ids[0]
    assert fact["detail"] == ids


def test_red_and_bounded_are_told_apart_in_the_record_alone(hub, tmp_path, monkeypatch):
    """The two cases stay apart in the record alone (ADR-0164, #1183/#1201): a red carries its failing ids, a bounded leg
    carries the kill and no id, and a bounded result over a red or a finished leg -- or a reason that is neither a signal
    nor an exit -- is no reading at all."""
    st = tmp_path / "st"
    st.mkdir()
    monkeypatch.setattr(hub, "STATE", st)
    (st / "nightly-result").write_text(f"{int(NOW - 60)}\tbounded\t{SHA}\tpass\tbounded:signal-15\t[]\n")
    assert hub.foot_sources(NOW)["nightly"]["result"] == "bounded"
    (st / "nightly-result").write_text(f'{int(NOW - 60)}\tfail\t{SHA}\tpass\tfail\t["tests/test_x.py::test_a"]\n')
    red = hub.foot_sources(NOW)["nightly"]
    assert red["result"] == "fail" and red["ids"] == ["tests/test_x.py::test_a"]
    (fact,) = [i for i in hub.foot_items({"nightly": red}, NOW) if i["key"] == "nightly"]
    assert fact["bad"] and fact["text"] == "nightly fail 60s ago"
    # a bounded result over two finished legs, over a red leg, with an unreadable reason, or with a failing id is no line
    for record in (f"{int(NOW - 60)}\tbounded\t{SHA}\tpass\tpass\t[]\n",
                   f"{int(NOW - 60)}\tbounded\t{SHA}\tpass\tfail\t[]\n",
                   f"{int(NOW - 60)}\tbounded\t{SHA}\tpass\tbounded:15\t[]\n",
                   f'{int(NOW - 60)}\tbounded\t{SHA}\tpass\tbounded:signal-15\t["tests/test_x.py::test_a"]\n',
                   f"{int(NOW - 60)}\tfail\t{SHA}\tpass\tpass\t[]\n"):
        (st / "nightly-result").write_text(record)
        assert hub.foot_sources(NOW)["nightly"] is None, record


def test_a_red_leg_with_a_signal_is_a_record_the_footer_reads(hub, tmp_path, monkeypatch):
    st = tmp_path / "st"
    st.mkdir()
    (st / "nightly-result").write_text(
        f'{int(NOW - 60)}\tfail\t{SHA}\tpass\tfail:signal-15\t["tests/test_x.py::test_a"]\n')
    monkeypatch.setattr(hub, "STATE", st)
    src = hub.foot_sources(NOW)["nightly"]
    assert src["result"] == "fail" and src["serial"] == "fail:signal-15"
    # a red leg stays a red result: a bounded or a pass result over it is still no reading
    (st / "nightly-result").write_text(
        f'{int(NOW - 60)}\tbounded\t{SHA}\tpass\tfail:signal-15\t["tests/test_x.py::test_a"]\n')
    assert hub.foot_sources(NOW)["nightly"] is None
    (st / "nightly-result").write_text(f"{int(NOW - 60)}\tpass\t{SHA}\tpass\tfail:signal-15\t[]\n")
    assert hub.foot_sources(NOW)["nightly"] is None


def test_a_green_nightly_carries_no_detail_list(hub):
    assert _nightly(hub)[0]["detail"] is None


def test_no_nightly_reading_draws_no_block(hub):
    src = {"occ": {"live": 1, "agents": 1}, "quota": {"codex": None, "agy": None}}
    assert [i["key"] for i in hub.foot_items(src, NOW) if i["key"] == "nightly"] == []


def test_a_record_the_hub_cannot_read_is_a_question_mark_never_a_pass(hub):
    (fact,) = _nightly(hub, result="maybe", sha="")
    assert "pass" not in fact["text"] and "?" in fact["text"]


def test_the_record_nightly_sh_writes_is_the_one_the_footer_reads(hub, tmp_path, monkeypatch):
    st = tmp_path / "st"
    st.mkdir()
    (st / "nightly-result").write_text(f"{int(NOW - 60)}\tpass\t{SHA}\tpass\tpass\t[]\n")
    monkeypatch.setattr(hub, "STATE", st)
    assert hub.foot_sources(NOW)["nightly"] == {"result": "pass", "sha": SHA, "parallel": "pass", "serial": "pass",
                                                "ids": [], "ts": NOW - 60}
    (st / "nightly-result").write_text(f'{int(NOW - 60)}\tfail\t{SHA}\tpass\tfail\t["tests/test_x.py::test_a"]\n')
    assert hub.foot_sources(NOW)["nightly"]["ids"] == ["tests/test_x.py::test_a"]
    # the ids are one JSON array, never a whitespace-separated list: a node id may carry a space, and a field that is
    # not one array of strings is no reading (#704)
    (st / "nightly-result").write_text(f"{int(NOW - 60)}\tfail\t{SHA}\tpass\tfail\ttests/test_x.py::test_a\n")
    assert hub.foot_sources(NOW)["nightly"] is None
    (st / "nightly-result").write_text(f'{int(NOW - 60)}\tfail\t{SHA}\tpass\tfail\t[1]\n')
    assert hub.foot_sources(NOW)["nightly"] is None
    (st / "nightly-result").write_text(f'{int(NOW - 60)}\tfail\t{SHA}\tpass\tfail\t{{"a":1}}\n')
    assert hub.foot_sources(NOW)["nightly"] is None
    # a record whose result contradicts its modes is no reading: the hub never draws a pass over a red mode
    (st / "nightly-result").write_text(f"{int(NOW - 60)}\tpass\t{SHA}\tfail\tpass\t[]\n")
    assert hub.foot_sources(NOW)["nightly"] is None
    (st / "nightly-result").write_text(f"{int(NOW - 60)}\tfail\t{SHA}\tpass\tmaybe\t[]\n")
    assert hub.foot_sources(NOW)["nightly"] is None
    (st / "nightly-result").write_text("not a result line\n")
    assert hub.foot_sources(NOW)["nightly"] is None
    # the record is one line: a valid first line with anything after it is no reading, and the hold drops the same file
    (st / "nightly-result").write_text(
        f'{int(NOW - 60)}\tfail\t{SHA}\tpass\tfail\t["tests/test_x.py::test_a"]\ncorrupt\n')
    assert hub.foot_sources(NOW)["nightly"] is None
    (st / "nightly-result").unlink()
    assert hub.foot_sources(NOW)["nightly"] is None


def test_a_failing_test_whose_id_carries_a_space_is_read_whole(hub, tmp_path, monkeypatch):
    """pytest node ids may carry spaces (a parametrized case id): the record encodes them as one JSON array, so the hub
    names every test whole and never splits an id at a space (#704, ADR-0153)."""
    st = tmp_path / "st"
    st.mkdir()
    ids = ["tests/test_x.py::test_x[case with space]", "tests/test_y.py::test_b"]
    (st / "nightly-result").write_text(f"{int(NOW - 60)}\tfail\t{SHA}\tpass\tfail\t{json.dumps(ids)}\n")
    monkeypatch.setattr(hub, "STATE", st)
    (fact,) = [i for i in hub.foot_items({"nightly": hub.foot_sources(NOW)["nightly"]}, NOW) if i["key"] == "nightly"]
    assert fact["detail"] == ids
    assert "tests/test_x.py::test_x[case with space]" in fact["tip"]


def test_a_local_pass_shows_pass_only_when_the_github_half_passed_at_the_same_sha(hub):
    """Guard for the one verdict (#1975, ADR-0153 rule 1d): the line reads the combined verdict, never the local record alone.
    A local pass beside a GitHub red is fail, beside an unproven or missing GitHub half it is unproven, and a verdict at
    another sha proves nothing about this record."""
    (fact,) = _nightly(hub, verdict=GREEN | {"result": "fail", "github": "fail"})
    assert fact["text"] == "nightly fail 4m ago" and fact["bad"]
    assert "github fail at 0123456" in fact["tip"]
    (fact,) = _nightly(hub, verdict=GREEN | {"result": "unproven", "github": "unfinished"})
    assert fact["text"] == "nightly unproven 4m ago" and fact["parts"][0]["tone"] == "warn"
    (fact,) = _nightly(hub, verdict=GREEN | {"sha": "f" * 40, "github_sha": "f" * 40})
    assert fact["text"] == "nightly unproven 4m ago"
    (fact,) = _nightly(hub, verdict=None)
    assert "pass" not in fact["text"], "a local pass with no verdict shows as pass"


def test_the_footer_reads_the_verdict_nightly_sh_writes(hub, tmp_path, monkeypatch):
    st = tmp_path / "st"
    st.mkdir()
    monkeypatch.setattr(hub, "STATE", st)
    (st / "nightly-result").write_text(f"{int(NOW - 60)}\tpass\t{SHA}\tpass\tpass\t[]\n")
    (st / "nightly-verdict").write_text(f"{int(NOW - 30)}\tfail\t{SHA}\tpass\tfail\t{SHA}\n")
    src = hub.foot_sources(NOW)["nightly"]
    assert src["verdict"] == {"result": "fail", "sha": SHA, "local": "pass", "github": "fail", "github_sha": SHA}
    (fact,) = [i for i in hub.foot_items({"nightly": src}, NOW) if i["key"] == "nightly"]
    assert fact["bad"] and fact["text"] == "nightly fail 60s ago"
    (st / "nightly-verdict").write_text("not a verdict\n")
    assert hub.foot_sources(NOW)["nightly"]["verdict"] is None


def test_a_closed_window_record_is_a_bounded_reading(hub, tmp_path, monkeypatch):
    """The record of an hour that closed with no run (#1975) is a bounded attempt the hub reads, never a question mark."""
    st = tmp_path / "st"
    st.mkdir()
    monkeypatch.setattr(hub, "STATE", st)
    (st / "nightly-result").write_text(f"{int(NOW - 60)}\tbounded\t{SHA}\tbounded:window\tbounded:window\t[]\n")
    assert hub.foot_sources(NOW)["nightly"]["result"] == "bounded"
