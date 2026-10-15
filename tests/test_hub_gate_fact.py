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
    return [i for i in hub.foot_items(src, {"runs": 10, "children": 20}, NOW) if i["key"] == "gate"]


def test_a_passing_gate_reads_kind_result_sha_and_age(hub):
    (fact,) = _gate(hub)
    assert fact["text"] == "gate fast pass 0123456 4m ago"
    assert not fact["bad"]


def test_a_red_full_gate_reads_in_the_error_tone(hub):
    (fact,) = _gate(hub, kind="full", result="fail")
    assert fact["text"] == "gate full fail 0123456 4m ago"
    assert any(p["tone"] == "err" for p in fact["parts"])


def test_no_gate_reading_draws_no_block(hub):
    src = {"occ": {"live": 1, "agents": 1}, "quota": {"codex": None, "agy": None}}
    assert [i["key"] for i in hub.foot_items(src, {"runs": 10, "children": 20}, NOW) if i["key"] == "gate"] == []


def test_a_line_the_hub_cannot_read_is_a_question_mark_never_a_pass(hub):
    (fact,) = _gate(hub, result="maybe", sha="")
    assert "pass" not in fact["text"] and "?" in fact["text"]


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
