"""Hub board cards: an open blocker is named on the card; a closed one is not (#1506)."""
import importlib.util
import os
import re
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "sbihaiko/MesenAI")
    os.environ.setdefault("SG_OWNER", "sbihaiko")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_board_blockers", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _item(n, title, status, blockers=None, state="open"):
    it = {"n": n, "t": title, "s": state, "st": status, "l": ["gui"]}
    if blockers is not None:
        it["b"] = blockers
    return it


def test_card_with_open_blocker_exposes_blocked_by_numbers(hub):
    hub.board_cache["items"] = [
        _item(1506, "Hub: a board card shows the issues blocking it", "Blocked", [1496]),
        _item(1500, "already unblocked", "Todo", []),
        _item(1496, "the blocker", "In Progress"),
    ]
    data = hub.board()
    blocked = next(x for g in data["groups"] if g["name"] == "Blocked" for x in g["items"] if x["n"] == 1506)
    clear = next(x for g in data["groups"] if g["name"] == "Todo" for x in g["items"] if x["n"] == 1500)
    assert blocked["b"] == [1496]
    assert not clear.get("b")


def test_closed_blocker_is_dropped_so_the_card_has_no_row(hub):
    nodes = [
        {"number": 1506, "blockedBy": {"nodes": [{"number": 1496, "state": "CLOSED"}]}},
        {"number": 1507, "blockedBy": {"nodes": [{"number": 1498, "state": "OPEN"}]}},
        {"number": 1508, "blockedBy": {"nodes": []}},
    ]
    items = [_item(1506, "was blocked", "Todo"), _item(1507, "still blocked", "Blocked"), _item(1508, "never blocked", "Todo")]
    hub.apply_open_blockers(items, nodes)
    by = {i["n"]: i for i in items}
    assert "b" not in by[1506] or by[1506]["b"] == []
    assert by[1507]["b"] == [1498]
    assert "b" not in by[1508] or by[1508]["b"] == []


def test_page_renders_blocked_by_links_and_omits_an_empty_row(hub):
    page = hub.PAGE
    cards = page[page.index("const cards="): page.index("const vis=")]
    assert "blocked by" in cards
    assert 'href="https://github.com/__REPO__/issues/${' in cards or 'href="https://github.com/__REPO__/issues/' in cards
    assert "x.b" in cards
    assert re.search(r"x\.b\s*&&\s*x\.b\.length|x\.b\|\|\[\]\)\.length", cards)


def test_blocker_line_stays_inside_a_300px_card(hub):
    css = hub.PAGE[hub.PAGE.index("<style>"): hub.PAGE.index("</style>")]
    blk = re.search(r"#bbody \.it \.blk\{[^}]+\}", css)
    assert blk, "card blocker line (.blk) missing"
    rule = blk.group(0)
    assert "overflow:hidden" in rule and "text-overflow:ellipsis" in rule
    assert "white-space:nowrap" in rule
    assert "#side{width:300px" in css
