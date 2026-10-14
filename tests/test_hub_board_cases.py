"""Hub board pane: a test case item is a row beside the axis, never a column (ADR-0141, ADR-0137).

The row shows the pass's result, its age and the merge commit it ran on. It is drawn from the one items read the pass
already makes: the read also carries the draft items, so no call is added.

Fixture style follows tests/test_hub_board_kinds.py.
"""
import importlib.util
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"
SHA = "1641bc94f52afca78acdf6a915a200cccff3e5e9"
FAILED = f"test case #300 | fail | {SHA} | 2026-10-01T00:00:00Z"
GREEN = "test case #301 | pass | 9caabae0000000000000000000000000000000aa | 2026-10-02T00:00:00Z"
NOW = "2026-10-01T02:00:00Z"


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "sbihaiko/MesenAI")
    os.environ.setdefault("SG_OWNER", "sbihaiko")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ.setdefault("SG_CHECKOUT", tempfile.mkdtemp())
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_board_cases", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _cache(hub, monkeypatch, cases):
    items = [{"n": 10, "t": "a task", "s": "open", "c": None, "st": "Todo", "l": [], "k": "task"}]
    monkeypatch.setattr(hub, "board_cache", {"t": 1.0, "items": items, "cases": cases, "prs": [], "prev": [], "prev_n": 0, "lc": {}, "err": None, "errs": []})
    monkeypatch.setattr(hub, "pr_state", lambda x: None)
    monkeypatch.setattr(hub, "gate_strip", lambda x, t=None: None)
    monkeypatch.setattr(hub, "pr_badge", lambda x: None)
    monkeypatch.setattr(hub.time, "time", lambda: 1.0)


def test_a_failed_pass_is_a_row_with_its_result_age_and_merge_commit(hub, monkeypatch):
    _cache(hub, monkeypatch, [FAILED, GREEN])
    monkeypatch.setattr(hub, "_now_stamp", lambda: NOW)
    rows = hub.board()["cases"]
    failed = next(r for r in rows if r["issue"] == 300)
    assert failed["result"] == "fail" and failed["commit"] == SHA and failed["age"] == 7200
    assert [r["result"] for r in rows if r["issue"] == 301] == ["pass"]


def test_a_test_case_is_never_a_column(hub, monkeypatch):
    _cache(hub, monkeypatch, [FAILED, GREEN])
    b = hub.board()
    assert [g["name"] for g in b["groups"]] == [g["name"] for g in (_cache(hub, monkeypatch, []), hub.board())[1]["groups"]]
    assert not [g for g in b["groups"] if "test case" in g["name"].lower() or "300" in g["name"]]
    assert not [x for g in b["groups"] for x in g["items"] if x["n"] in (300, 301)]


def test_the_row_is_drawn_from_the_items_the_pass_already_reads(hub, monkeypatch):
    items_reads = []

    def run(cmd, **kw):
        s = " ".join(cmd)
        out = ""
        if "/fields?" in s:
            out = "PVTSSF_status\n"
        elif "projectsV2" in s and "/items" in s:
            items_reads.append(s)
            out = json.dumps({"n": 11, "t": "a card", "s": "OPEN", "c": None, "st": "Todo", "l": []}) + "\n" + json.dumps({"dt": FAILED, "k": "test case"}) + "\n"
        elif "blockedBy" in s:
            out = json.dumps({"data": {"repository": {"issues": {"nodes": []}}}})
        return subprocess.CompletedProcess(cmd, 0, out, "")

    monkeypatch.setattr(hub.subprocess, "run", run)
    monkeypatch.setattr(hub, "board_cache", {"t": 1.0, "items": [], "prs": [], "prev": [], "prev_n": 0, "lc": {}, "err": None})
    hub.board_pass()
    assert len(items_reads) == 1, "the draft items ride in the one items read"
    assert "dt:" in items_reads[0], "the one read is widened to carry the draft items"
    assert hub.board_cache["cases"] == [FAILED]
    assert [i["n"] for i in hub.board_cache["items"]] == [11], "a draft item is no card"


def test_the_page_draws_a_row_beside_the_axis_and_no_column(hub):
    line = re.search(r"^const caseRows=.*$", hub.PAGE, re.M).group(0)
    cases = [{"issue": 300, "result": "fail", "commit": SHA, "ended": "2026-10-01T00:00:00Z", "age": 7200}]
    script = ('const esc=s=>String(s);\n' + line + f'\nconsole.log(caseRows({json.dumps(cases)}));\n')
    out = subprocess.run(["node", "-"], input=script, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert "fail" in out.stdout and SHA[:7] in out.stdout and "2h" in out.stdout and "#300" in out.stdout
    assert 'class="col' not in out.stdout and 'class="acc' not in out.stdout


def test_the_pane_script_calls_no_new_endpoint_for_the_rows(hub):
    pane = hub.PAGE[hub.PAGE.index("async function pollBoard("): hub.PAGE.index('$("#lfl").onchange')]
    assert pane.count("fetch(") == 1
