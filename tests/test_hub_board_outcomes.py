"""Hub board pane: filter the items by kind, and the closed items by outcome (ADR-0141, ADR-0137).

The outcome set is the shipped definition's, read through board_columns; the rule lives there and the pane sends the
reader's choice and paints the answer. Fixture style follows tests/test_hub_board_kinds.py.
"""
import importlib.util
import os
import re
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib/squad-goal/scripts"
OUTCOMES = ["Merged", "Abandoned", "Failed", "Reverted"]   # the shipped definition's outcomes, in its order


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def cols():
    return _load("board_columns_outcomes", SCRIPTS / "board_columns.py")


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "sbihaiko/MesenAI")
    os.environ.setdefault("SG_OWNER", "sbihaiko")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ.setdefault("SG_CHECKOUT", tempfile.mkdtemp())
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    return _load("hub_board_outcomes", SCRIPTS / "hub.py")


def _cache(hub, monkeypatch):
    def issue(n, kind, state, st, labels=()):
        return {"n": n, "t": f"{kind} {n}", "s": state, "c": "2026-10-01T00:00:00Z" if state == "closed" else None,
                "st": st, "l": list(labels), "k": kind}
    items = [issue(10, "bug", "open", "Todo"),
             issue(11, "bug", "closed", "Done"),
             issue(12, "bug", "closed", "Abandoned"),
             issue(41, "test case", "closed", "Done", ["outcome:failed"]),
             issue(42, "task", "closed", "Done", ["outcome:reverted"]),
             issue(43, "task", "closed", "Done", ["outcome:shelved"])]
    prs = [{"n": 50, "t": "fix/50-a-branch", "u": "2026-10-02T00:00:00Z", "r": hub.REPO, "br": True, "ps": "ahead 2"}]
    monkeypatch.setattr(hub, "board_cache", {"t": 1.0, "items": items, "prs": prs, "lc": {}, "err": None, "errs": []})
    monkeypatch.setattr(hub, "pr_state", lambda x: None)
    monkeypatch.setattr(hub, "gate_strip", lambda x, t=None: None)
    monkeypatch.setattr(hub, "pr_badge", lambda x: None)


def _keys(model):
    return sorted(x["key"] for g in model["kinds"] for x in g["items"])


def test_the_outcome_set_is_the_shipped_definitions(cols):
    assert cols.shipped_outcomes() == OUTCOMES


def test_each_outcome_filter_shows_only_the_closed_items_with_that_outcome(hub, monkeypatch):
    _cache(hub, monkeypatch)
    got = {o: _keys(hub.board(outcome=o)) for o in OUTCOMES}
    assert got["Merged"] == ["bug:11"]
    assert got["Abandoned"] == ["bug:12"]
    assert got["Failed"] == ["test case:41"]
    assert got["Reverted"] == ["task:42"]


def test_a_merged_issue_an_abandoned_one_and_a_failed_test_never_read_as_one_thing(hub, monkeypatch):
    _cache(hub, monkeypatch)
    oc = {x["key"]: x["oc"] for g in hub.board()["kinds"] for x in g["items"] if g["state"] == "closed"}
    assert len({oc["bug:11"], oc["bug:12"], oc["test case:41"]}) == 3


def test_an_outcome_filter_leaves_no_open_item(hub, monkeypatch):
    _cache(hub, monkeypatch)
    assert all(g["state"] == "closed" for g in hub.board(outcome="Merged")["kinds"] if g["items"])


def test_the_kind_filter_keeps_one_kind(hub, monkeypatch):
    _cache(hub, monkeypatch)
    assert _keys(hub.board(kind="bug")) == ["bug:10", "bug:11", "bug:12"]
    assert _keys(hub.board(kind="bug", outcome="Abandoned")) == ["bug:12"]


def test_an_item_outside_the_set_is_a_reported_gap_never_a_filter(hub, monkeypatch):
    _cache(hub, monkeypatch)
    b = hub.board()
    assert b["gaps"] == [{"key": "task:43", "oc": "shelved"}]
    assert b["filters"]["outcomes"] == OUTCOMES and "shelved" not in b["filters"]["outcomes"]
    assert "task:43" not in _keys(hub.board(outcome="Merged"))


def test_an_outcome_the_set_does_not_name_is_refused(cols):
    with pytest.raises(ValueError):
        cols.filter_groups([], outcome="shelved")


def test_the_pane_holds_no_outcome_list(hub):
    pane = hub.PAGE[hub.PAGE.index("async function pollBoard("): hub.PAGE.index('$("#lfl").onchange')]
    assert not re.search(r"Merged|Failed|Reverted", pane)   # a list needs more than one name; the pane reads the model's
    assert re.search(r"api/board\"\+", pane) and "b.filters.outcomes" in pane
