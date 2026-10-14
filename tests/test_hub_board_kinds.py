"""Hub board pane: one open and one closed group for each kind, keyed by (kind, number) (ADR-0141, ADR-0137).

The groups come from the column table and the kinds; the pane holds no group name. board() builds the groups off
the local board cache and the page paints what they carry, so no call is added to a pass.

Fixture style follows tests/test_hub_board_holder.py.
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
KINDS = ["pr", "bug", "task", "spec", "test case"]


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "sbihaiko/MesenAI")
    os.environ.setdefault("SG_OWNER", "sbihaiko")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ.setdefault("SG_CHECKOUT", tempfile.mkdtemp())
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_board_kinds", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _cache(hub, monkeypatch):
    """A stubbed board cache that holds every kind, open and closed, and a PR sharing an issue's number."""
    def issue(n, kind, state, st, labels=()):
        return {"n": n, "t": f"{kind} {n}", "s": state, "c": "2026-10-01T00:00:00Z" if state == "closed" else None,
                "st": st, "l": list(labels), "k": kind}
    items = [issue(10, "bug", "open", "Todo"), issue(11, "bug", "closed", "Done"),
             issue(20, "task", "open", "In Progress"), issue(21, "task", "closed", "Done"),
             issue(30, "spec", "open", "Todo", ["spec"]), issue(31, "spec", "closed", "Done", ["spec"]),
             issue(40, "test case", "open", "Todo"), issue(41, "test case", "closed", "Done"),
             issue(50, "task", "open", "Todo")]
    pr = {"n": 50, "t": "pr 50", "u": "2026-10-02T00:00:00Z", "r": hub.REPO}
    prev = {"n": 12, "t": "pr 12", "c": "2026-10-01T00:00:00Z", "m": True, "r": hub.REPO}
    monkeypatch.setattr(hub, "board_cache", {"t": 1.0, "items": items, "prs": [pr], "prev": [prev], "prev_n": 1, "lc": {}, "err": None, "errs": []})
    monkeypatch.setattr(hub, "pr_state", lambda x: None)
    monkeypatch.setattr(hub, "gate_strip", lambda x, t=None: None)
    monkeypatch.setattr(hub, "pr_badge", lambda x: None)


def test_the_page_model_holds_an_open_and_a_closed_group_for_each_kind(hub, monkeypatch):
    _cache(hub, monkeypatch)
    groups = hub.board()["kinds"]
    assert [(g["kind"], g["state"]) for g in groups] == [(k, s) for k in KINDS for s in ("open", "closed")]
    by = {(g["kind"], g["state"]): [x["n"] for x in g["items"]] for g in groups}
    assert by[("bug", "open")] == [10] and by[("bug", "closed")] == [11]
    assert by[("task", "open")] == [20, 50] and by[("task", "closed")] == [21]
    assert by[("spec", "open")] == [30] and by[("spec", "closed")] == [31]
    assert by[("test case", "open")] == [40] and by[("test case", "closed")] == [41]
    assert by[("pr", "open")] == [50] and by[("pr", "closed")] == [12]


def test_a_pr_and_an_issue_with_one_number_are_two_items(hub, monkeypatch):
    _cache(hub, monkeypatch)
    keys = [x["key"] for g in hub.board()["kinds"] for x in g["items"]]
    assert "pr:50" in keys and "task:50" in keys
    assert len(keys) == len(set(keys))


def test_a_group_is_named_by_the_table_and_the_kind(hub, monkeypatch):
    _cache(hub, monkeypatch)
    names = {(g["kind"], g["state"]): g["name"] for g in hub.board()["kinds"]}
    assert names[("pr", "open")] == hub.COLUMN["pr_open"] and names[("pr", "closed")] == hub.COLUMN["pr_closed"]
    assert names[("test case", "closed")] == "Closed Test Cases"
    assert names[("bug", "open")] == "Open Bugs"


def test_the_page_script_holds_no_group_name(hub):
    names = {f"{s} {p}" for s in ("Open", "Closed") for p in ("PRs", "Bugs", "Tasks", "Specs", "Test Cases")}
    pane = hub.PAGE[hub.PAGE.index("async function pollBoard("): hub.PAGE.index('$("#lfl").onchange')]
    assert not [n for n in names if n in pane]


def test_the_pane_paints_the_groups_it_is_given_keyed_by_kind_and_number(hub):
    line = re.search(r"^const kindGroups=.*$", hub.PAGE, re.M).group(0)
    groups = [{"kind": "pr", "state": "open", "name": "G1", "n": 1, "items": [{"key": "pr:50", "k": "pr", "n": 50, "t": "p"}]},
              {"kind": "task", "state": "open", "name": "G2", "n": 1, "items": [{"key": "task:50", "k": "task", "n": 50, "t": "t"}]}]
    script = ('const esc=s=>String(s);\n' + line + f'\nconsole.log(kindGroups({json.dumps(groups)}));\n')
    out = subprocess.run(["node", "-"], input=script, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert 'data-g="G1"' in out.stdout and 'data-g="G2"' in out.stdout
    assert 'data-key="pr:50"' in out.stdout and 'data-key="task:50"' in out.stdout
