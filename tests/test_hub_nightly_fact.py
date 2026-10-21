"""The nightly full suite on the hub's line (#704): nightly.sh leaves one result record, $SG_STATE/nightly-result
(`<epoch> TAB <pass|fail> TAB <sha> TAB <parallel> TAB <serial> TAB <ids>`), and the footer reads it as one fact: the
result, both modes, the sha it ran at, and the tests that failed."""
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
    spec = importlib.util.spec_from_file_location("hub_nightly_fact", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _nightly(hub, **over):
    src = {"occ": {"live": 1, "agents": 1}, "quota": {"codex": None, "agy": None},
           "nightly": {"result": "pass", "sha": SHA, "parallel": "pass", "serial": "pass", "ids": [], "ts": NOW - 240} | over}
    return [i for i in hub.foot_items(src, NOW) if i["key"] == "nightly"]


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


def test_the_tooltip_names_five_failing_tests_and_counts_the_rest(hub):
    ids = [f"tests/test_{n}.py::test_a" for n in "abcdefg"]
    (fact,) = _nightly(hub, result="fail", serial="fail", ids=ids)
    assert fact["tip"].endswith("7 failed: " + ", ".join(ids[:5]) + " +2 more")


def test_no_nightly_reading_draws_no_block(hub):
    src = {"occ": {"live": 1, "agents": 1}, "quota": {"codex": None, "agy": None}}
    assert [i["key"] for i in hub.foot_items(src, NOW) if i["key"] == "nightly"] == []


def test_a_record_the_hub_cannot_read_is_a_question_mark_never_a_pass(hub):
    (fact,) = _nightly(hub, result="maybe", sha="")
    assert "pass" not in fact["text"] and "?" in fact["text"]


def test_the_record_nightly_sh_writes_is_the_one_the_footer_reads(hub, tmp_path, monkeypatch):
    st = tmp_path / "st"
    st.mkdir()
    (st / "nightly-result").write_text(f"{int(NOW - 60)}\tpass\t{SHA}\tpass\tpass\t-\n")
    monkeypatch.setattr(hub, "STATE", st)
    assert hub.foot_sources(NOW)["nightly"] == {"result": "pass", "sha": SHA, "parallel": "pass", "serial": "pass",
                                                "ids": [], "ts": NOW - 60}
    (st / "nightly-result").write_text(f"{int(NOW - 60)}\tfail\t{SHA}\tpass\tfail\ttests/test_x.py::test_a\n")
    assert hub.foot_sources(NOW)["nightly"]["ids"] == ["tests/test_x.py::test_a"]
    (st / "nightly-result").write_text("not a result line\n")
    assert hub.foot_sources(NOW)["nightly"] is None
    (st / "nightly-result").unlink()
    assert hub.foot_sources(NOW)["nightly"] is None
