"""Hub board: a card wears how far its own tasks got, `completed/total`, beside its label pills (#153).

The counter is a value read off the summary the items route already returns: the pass keeps the
summary on its one read (never a second query, never one per card), board() turns the pair into the
string, and the page paints what the row carries. A card whose issue has no tasks, and a card whose
summary the hub cannot read, draw nothing -- never an invented `0/0`.

Fixture style follows tests/test_hub_board_blockers.py (hub.py loaded by path, the cache under a temp
dir) and tests/test_hub_board_refresh_button.py (the gh CLI stubbed at hub.subprocess.run).
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

# The markup a card with a label and no counter renders: frozen from the page before the counter
# existed, so a change that leaks into a card with no tasks fails here. The `data-*` attributes are
# the card data the ticket sheet reads (#919): an empty one is the empty string, never a missing key.
NO_COUNT_CARD = (
    '<div class=it data-n="1" data-r="__REPO__" data-col="Todo" data-pm="" data-b="%5B%5D" '
    'data-bl="%5B%5D" data-ho="" data-hr="" data-prf="%5B%5D" style="--cc:#000000;--lc:var(--s2)">'   # no spec: its own number's slot, never grey (#1640)
    '<a class="id ghl" target=_blank rel=noopener title="open issue #1 on GitHub" '
    'aria-label="open issue #1 on GitHub" href="https://github.com/__REPO__/issues/1">#1 {ghm}</a>'   # the card names its issue number, and the number opens the issue like the mark (#1268)
    '<span class=tt title="a spec">a spec</span>'   # the whole title is in the tooltip, because the lane clamps it to two lines (#1994)
    '<button class=syncb data-n="1" title="re-read only this card from GitHub now: its column, labels, holder, blockers and sub-issue count">Sync</button>'   # every issue card carries the one-card sync (#1668)
    '<span class=lbs><span class="">gui</span></span></div>'
).replace("{ghm}", re.search(r"const GHI='(.*?)';", HUB_PY.read_text()).group(1))


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "sbihaiko/MesenAI")
    os.environ.setdefault("SG_OWNER", "sbihaiko")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ.setdefault("SG_CHECKOUT", tempfile.mkdtemp())
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()   # the board cache lives here, never in the live hub's
    spec = importlib.util.spec_from_file_location("hub_board_task_count", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _cache(items):
    """One board cache as board_pass leaves it."""
    return {"t": 1.0, "items": items, "prs": [], "lc": {}, "err": None}


def _item(n, title, status, si=None, state="open"):
    it = {"n": n, "t": title, "s": state, "st": status, "l": ["gui"]}
    if si is not None:
        it["si"] = si
    return it


def _row(data, n):
    """The board row for issue `n`, out of whatever column it landed in."""
    for g in data["groups"]:
        for x in g["items"]:
            if x["n"] == n:
                return x
    raise AssertionError(f"issue {n} is on no column of the served board")


# ---- the value, off the cache (board()) -------------------------------------------------------------

def test_a_row_carries_the_count_of_its_own_tasks(hub, monkeypatch):
    monkeypatch.setattr(hub, "board_cache", _cache([
        _item(153, "a spec", "Todo", {"total": 5, "completed": 3}),
        _item(150, "an issue with no tasks", "Todo", {"total": 0, "completed": 0}),
    ]))
    data = hub.board()
    row = _row(data, 153)
    assert row["sc"] == "3/5", f"the row does not carry the count it was given: {row}"
    assert {"n", "t", "c", "l"} <= set(row), "the keys the page already reads keep their names"
    assert "sc" not in _row(data, 150), "an issue with no tasks draws nothing, not an invented 0/0"


def test_a_summary_the_hub_cannot_read_exposes_no_counter(hub, monkeypatch):
    monkeypatch.setattr(hub, "board_cache", _cache([
        _item(141, "the summary never arrived", "Todo"),                             # a cache written before this change
        _item(142, "the route gave nulls", "Todo", {"total": None, "completed": None}),
        _item(143, "a total that is not a number", "Todo", {"total": "five", "completed": 1}),
        _item(144, "only one half of the pair", "Todo", {"total": 4}),
    ]))
    data = hub.board()
    for n in (141, 142, 143, 144):
        assert "sc" not in _row(data, n), f"issue {n} draws a counter out of a summary nothing gave"


# ---- the fact, off the route the pass already reads (board_pass()) -----------------------------------

def _rest_issue(i):
    """An open issue as the REST list answers it: the summary is one of GitHub's own fields."""
    return {"number": i["n"], "title": i["t"], "state": i["s"].lower(), "labels": [{"name": x} for x in i["l"]],
            "created_at": None, "sub_issues_summary": i["si"]}


def _fake_gh(calls, items, fallback=()):
    """The gh CLI as board_pass uses it: every call is recorded, and the two issue routes answer from the lists given."""
    def run(cmd, **kw):
        calls.append(cmd)
        s = " ".join(cmd)
        if "projectsV2" in s and "/items" in s:
            out = "".join(json.dumps(i) + "\n" for i in items) + json.dumps({"_page": len(items)}) + "\n"
        elif "issues?state=open" in s:
            out = json.dumps([_rest_issue(i) for i in fallback])
        elif "/fields?" in s:
            out = "PVTSSF_status\n"
        elif "blockedBy" in s:
            out = json.dumps({"data": {"repository": {"issues": {"nodes": [], "pageInfo": {"hasNextPage": False}}}}})
        elif "/labels?" in s:
            out = ""
        elif "pulls?state=closed" in s:
            out = ""
        elif "search/issues" in s:
            out = "0\n"
        elif "pullRequests(first:100" in s:
            out = json.dumps({"data": {"repository": {"pullRequests": {"nodes": []}}}})
        else:
            out = ""
        return subprocess.CompletedProcess(cmd, 0, out, "")
    return run


def _jq(cmd):
    """The projection a gh call was given, or "" when it made no --jq read."""
    return cmd[cmd.index("--jq") + 1] if "--jq" in cmd else ""


def _pass_calls(hub, monkeypatch, items, fallback=()):
    """One board_pass over the stubbed gh, returning the calls it made."""
    calls = []
    monkeypatch.setattr(hub.board_seam().binding, "status_field", lambda: {"id": "PVTSSF_status", "opt": {}})   # the Status field is cached by the binding, not read per pass (#1810)
    monkeypatch.setattr(hub, "board_cache", _cache([]))
    monkeypatch.setattr(hub.subprocess, "run", _fake_gh(calls, items, fallback))
    hub.board_pass()
    return calls


def test_the_summary_rides_the_one_items_read_and_costs_no_query(hub, monkeypatch):
    one = _pass_calls(hub, monkeypatch, [_item(153, "a spec", "Todo", {"total": 5, "completed": 3})])
    reads = [c for c in one if "projectsV2" in " ".join(c) and "/items" in " ".join(c)]
    assert len(reads) == 1, "the items route is read once, not once per card"
    assert "sub_issues_summary" in _jq(reads[0]), \
        f"the projection drops the fact the card needs: {_jq(reads[0])}"
    assert hub.board_cache["items"][0]["si"] == {"total": 5, "completed": 3}, \
        "the pass did not keep the summary it read"
    many = _pass_calls(hub, monkeypatch, [_item(n, f"card {n}", "Todo", {"total": n, "completed": 1}) for n in (1, 2, 3)])
    assert len(many) == len(one), \
        "the count rides on the read the pass already makes: three cards must cost what one card costs"


def test_a_card_that_only_the_fallback_read_returned_gets_its_count_too(hub, monkeypatch):
    fallback = [{"n": 153, "t": "an issue that never reached the board", "s": "OPEN", "c": None, "st": None,
                 "l": ["gui"], "si": {"total": 5, "completed": 3}}]
    calls = _pass_calls(hub, monkeypatch, [], fallback)
    read = [c for c in calls if "issues?state=open" in " ".join(c)]
    assert len(read) == 1
    assert hub.board_cache["items"][0]["si"] == {"total": 5, "completed": 3}
    assert _row(hub.board(), 153)["sc"] == "3/5"


def test_an_issue_the_board_does_not_carry_is_marked_not_a_card(hub, monkeypatch):
    """#1429: the fallback row is an open issue, not a card. It says so with `nc`, so a reader of board cards can tell
    an issue with no card from a card with no column -- the two used to look the same, both carrying `st` None."""
    fallback = [{"n": 153, "t": "an issue that never reached the board", "s": "OPEN", "c": None, "st": None,
                 "l": ["gui"], "si": {"total": 5, "completed": 3}}]
    _pass_calls(hub, monkeypatch, [], fallback)
    row = next(i for i in hub.board_cache["items"] if i["n"] == 153)
    assert row.get("nc") is True, "a fallback row claims to be a board card"


def test_the_summary_survives_the_board_cache(hub, monkeypatch):
    items = [_item(153, "a spec", "Todo", {"total": 5, "completed": 3})]
    _pass_calls(hub, monkeypatch, items)
    on_disk = json.loads(hub.BOARD.read_text())             # what the next poll reads back off the disk
    assert on_disk["items"][0]["si"] == {"total": 5, "completed": 3}, \
        "the cache write drops the summary, so the counter would blink off on the next pass"
    monkeypatch.setattr(hub, "board_cache", on_disk)
    assert _row(hub.board(), 153)["sc"] == "3/5", "the counter is drawn from the cache like every other card fact"


# ---- what the page paints (cards()) ------------------------------------------------------------------

def _render(hub, rows):
    """The page's own cards() run by node over `rows`, with the page's surroundings stubbed."""
    line = re.search(r"^const cards=.*$", hub.PAGE, re.M)
    assert line, "the card renderer is gone"
    script = (Path(__file__).resolve().parent.parent.joinpath("contrib", "squad-goal", "scripts", "board-lanes.js").read_text() + "const SLOTS=specSlots([]);\n" +   # the lane rules the page inlines: spec color, wave chip (#917)
              'const esc=s=>String(s);const isPR=n=>false;const colOf=()=>"#000000";const tint=()=>"";'
              'const lbc=()=>"";const gstrip=()=>"";const age=()=>"";const ageC=()=>"";'
              'const bo={labels:[]};const CN=["Todo"];\n' + line.group(0) +
              f"\nconst ITEMS={json.dumps(rows)};\nconsole.log(cards({{name:'Todo'}},ITEMS,ITEMS));\n")
    out = subprocess.run(["node", "-"], input=script, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return out.stdout.rstrip("\n")


def test_the_card_draws_the_count_in_its_bottom_right_footer(hub):
    """The count sits in the card's footer, bottom right, as a PR card's age pill does: the last thing on the card."""
    html = _render(hub, [{"n": 1, "t": "a spec", "l": ["gui"], "sc": "3/5"}])
    assert re.search(r'<div class=ft><span class=tsk[^>]*>3/5</span></div></div>$', html), \
        f"the counter is not the card's bottom-right footer: {html}"
    assert "<span class=lbs><span class=\"\">gui</span></span>" in html, "the label pills keep their own row, without the counter"
    assert re.search(r'<span class=tsk[^>]*>7/9</span>', _render(hub, [{"n": 1, "t": "a spec", "l": ["gui"], "sc": "7/9"}])), \
        "the page must paint the value the row carries and compute none of its own"
    bare = _render(hub, [{"n": 1, "t": "a card with tasks and no label", "l": [], "sc": "3/5"}])
    assert "<div class=ft><span class=tsk" in bare and "lbs" not in bare, \
        "a card whose issue has tasks wears the counter even with no label, and draws no empty label row"


def test_a_card_with_no_count_is_the_markup_it_always_was(hub):
    assert _render(hub, [{"n": 1, "t": "a spec", "l": ["gui"]}]) == NO_COUNT_CARD, \
        "a card with no count changed shape"
    assert "tsk" not in _render(hub, [{"n": 1, "t": "a spec", "l": ["gui"]}])
    assert "lbs" not in _render(hub, [{"n": 1, "t": "a card with neither labels nor tasks", "l": []}]), \
        "nothing at all is drawn for a card with no count and no label"


def test_the_counter_wears_the_age_pill_treatment_of_a_pr_card(hub):
    css = hub.PAGE[hub.PAGE.index("<style>"): hub.PAGE.index("</style>")]
    rule = re.search(r"#bbody \.age,#bbody \.tsk\{([^}]*)\}", css)
    assert rule, "the counter does not share the age pill's rule, so it would not read as the PR card's footer pill"
    for part in ("border-radius:var(--radius-lg)", "color:var(--mute)", "border:1px solid var(--line)", "font-variant-numeric:tabular-nums"):
        assert part in rule.group(1), f"the shared pill rule lost {part}: {rule.group(1)}"
    assert re.search(r"#bbody \.it \.ft\{[^}]*justify-content:flex-end", css), "the footer no longer pushes its pills to the right"
