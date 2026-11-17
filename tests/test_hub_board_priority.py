"""The hub's board rows carry each card's priority mark (#1981, ADR-0137).

A card with a Priority value wears that level's mark. A card with no value wears the normal mark.
"""
import importlib.util
import pathlib
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib" / "squad-goal" / "scripts" / "hub.py"


@pytest.fixture
def hub(monkeypatch, tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    for k, v in (("SG_REPO", "x/y"), ("SG_OWNER", "x"), ("SG_PROJECT", "4"), ("HOME", str(tmp_path)),
                 ("SG_CHECKOUT", str(tmp_path)), ("SG_HUB_DIR", str(tmp_path / "hub")), ("SG_STATE", str(state))):
        monkeypatch.setenv(k, v)
    for k in ("SG_STATE_BASE", "SG_SHARED", "SG_PROFILE"):
        monkeypatch.delenv(k, raising=False)
    spec = importlib.util.spec_from_file_location("hub_board_priority", HUB_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def card_row(board, n):
    return next(x for g in board["groups"] for x in g["items"] if x["n"] == n)


def test_a_card_with_a_priority_value_carries_its_mark(hub):
    now = time.time()
    hub.board_cache = {"t": now, "err": None, "errs": [], "prs": [], "lc": {}, "cases": [],
                       "items": [{"n": 7, "r": "x/y", "t": "card 7", "s": "OPEN", "st": "Todo", "l": [], "i": "ITEM7",
                                  "ot": int(now), "p": "urgent"}]}
    assert card_row(hub.board(), 7)["pr"]["level"] == "urgent"


def test_a_card_with_no_priority_value_carries_the_normal_mark(hub):
    now = time.time()
    hub.board_cache = {"t": now, "err": None, "errs": [], "prs": [], "lc": {}, "cases": [],
                       "items": [{"n": 8, "r": "x/y", "t": "card 8", "s": "OPEN", "st": "Todo", "l": [], "i": "ITEM8",
                                  "ot": int(now)}]}
    assert card_row(hub.board(), 8)["pr"]["level"] == "normal"
