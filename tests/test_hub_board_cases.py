"""Hub board pane: a test case item is a row beside the column axis, never a column (ADR-0141, ADR-0137).

A test case is a draft item on the board. The pane reads it in the one items read it already makes, and draws the row
with its result, its age and the merge commit the pass ran on. No call is added to a pass.

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
ENDED = "2026-10-14T01:00:00Z"
NOW = 1791946800.0   # 2026-10-14T03:00:00Z, two hours after the failed pass ended
FAILED = f"test case #306 | fail | abc1234 | {ENDED}"
PASSED = "test case #300 | pass | def5678 | 2026-10-14T01:30:00Z"


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
    monkeypatch.setattr(hub, "board_cache", {"t": 1.0, "items": items, "cases": cases, "prs": [], 
                                             "lc": {}, "err": None, "errs": []})
    monkeypatch.setattr(hub.time, "time", lambda: NOW)


def test_a_failed_pass_is_a_row_with_its_result_its_age_and_its_merge_commit(hub, monkeypatch):
    _cache(hub, monkeypatch, [FAILED, PASSED])
    rows = hub.board()["cases"]
    failed = next(r for r in rows if r["issue"] == 306)
    assert failed["result"] == "fail" and failed["commit"] == "abc1234"
    assert "age" not in failed and failed["ended"] == ENDED   # an absolute instant: the page counts the age (#973)
    assert [r["result"] for r in rows] == ["fail", "pass"]


def test_a_test_case_is_never_a_column(hub, monkeypatch):
    _cache(hub, monkeypatch, [FAILED])
    model = hub.board()
    _cache(hub, monkeypatch, [])
    assert [g["name"] for g in model["groups"]] == [g["name"] for g in hub.board()["groups"]]
    assert [n for g in model["groups"] for n in (x["n"] for x in g["items"])] == [10]


def test_the_pass_reads_a_test_case_in_the_one_items_read_it_already_makes(hub, monkeypatch):
    calls = []
    draft = json.dumps({"n": None, "t": FAILED, "s": None, "c": None, "st": "Todo", "k": "test case", "l": [], "si": {}})
    card = json.dumps({"n": 11, "t": "a card", "s": "OPEN", "c": None, "st": "Todo", "k": "task", "l": []})

    def run(cmd, **kw):
        calls.append(" ".join(cmd))
        out = "PVTSSF_status\n" if "/fields?" in " ".join(cmd) else ""
        if "projectsV2" in " ".join(cmd) and "/items" in " ".join(cmd):
            out = draft + "\n" + card + "\n" + json.dumps({"_page": 2}) + "\n"
        return subprocess.CompletedProcess(cmd, 0, out, "")

    monkeypatch.setattr(hub.subprocess, "run", run)
    # The REST lane is no part of this case: with no answer stubbed, whether it reads depends on the machine's quota and its
    # login, and a serial shard then fails where a lone run passes (#2455). The items read below is the one under test.
    monkeypatch.setattr(hub, "_rest_page", lambda route: [])
    monkeypatch.setattr(hub, "board_cache", {"t": 1.0, "items": [], "prs": [], "lc": {}, "err": None})
    hub.board_pass()
    reads = [c for c in calls if "/items" in c and "projectsV2" in c]
    assert len(reads) == 1 and "DraftIssue" in reads[0]
    held = {k: v for k, v in hub.board_cache.items() if k != "items"}
    assert hub.board_cache.get("cases") == [FAILED], f"the pass wrote no cases: {held}"
    assert [i["n"] for i in hub.board_cache["items"]] == [11]


def test_the_pane_draws_the_row_beside_the_axis_with_result_age_and_commit(hub):
    line = re.search(r"^const caseRows=.*$", hub.PAGE, re.M).group(0)
    rows = [{"issue": 306, "result": "fail", "commit": "abc1234", "ended": ENDED}]
    script = f'const esc=s=>String(s);Date.now=()=>{int(NOW * 1000)};\n' + line + f'\nconsole.log(caseRows({json.dumps(rows)}));\n'
    out = subprocess.run(["node", "-"], input=script, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert "fail" in out.stdout and "abc1234" in out.stdout and "2h" in out.stdout and "#306" in out.stdout
    assert "col" not in re.findall(r'class="?([\w -]+)', out.stdout)[0].split()
    assert 'id=bcases' in hub.PAGE   # a strip of its own, outside the columns' #bbody
