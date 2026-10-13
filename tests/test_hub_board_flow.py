"""The board draws its columns in the development flow's order, and Open Specs sits immediately
right of Open PRs (#154).

The page carries one list of the flow, left to right, and one sort that reads it. These tests run
that sort through node against the page's own script and read the drawn order back. The decision is
the list below -- what the issue states -- and never a restatement of what the page happens to hold.
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

# The decision, left to right: Open Specs is the first thing the loop has to work, so it stands
# immediately right of the live PR queue, before the tasks the loop cuts from a spec.
FLOW_ORDER = ["Open PRs", "Open Specs", "Closed PRs", "No status", "Blocked", "Todo",
              "In Progress", "Code Complete", "Testing", "Done"]

ARCHIVE = "Done"   # the pile nobody works: it closes the board, whatever else is drawn


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "sbihaiko/MesenAI")
    os.environ.setdefault("SG_OWNER", "sbihaiko")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_board_flow", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _flow(page):
    """The page's own flow literal, as the browser script writes it."""
    m = re.search(r"FLOW=\[[^\]]*\]", page)
    assert m, "the board's script carries no flow list"
    return m.group(0)


def _sort(page):
    """The one statement that orders the columns: every group the board draws passes through it."""
    m = re.search(r"all=\[b\.prs,b\.prev,\.\.\.b\.groups\]\.sort\(.*(?=,f=q\.trim)", page)
    assert m, "the board no longer orders its columns through one sort over the flow list"
    return m.group(0)


def _node(script):
    out = subprocess.run(["node", "-"], input=script, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return out.stdout


def _col(name, n=0):
    """A group as the server hands it to the page: a name, and a count that must never matter."""
    return {"name": name, "n": n, "items": []}


def _drawn(hub, groups, prs=None, prev=None):
    """The order the page's own sort puts the columns in, run by node."""
    script = ("const b={prs:%s,prev:%s,groups:%s};%s;var all;%s;"
              "console.log(JSON.stringify(all.map(g=>g.name)))"
              % (json.dumps(prs or {"name": "Open PRs", "n": 0}),
                 json.dumps(prev or {"name": "Closed PRs", "n": 0}),
                 json.dumps(groups), _flow(hub.PAGE), _sort(hub.PAGE)))
    return json.loads(_node(script))


def _item(n, title, status, state="open"):
    it = {"n": n, "t": title, "s": state, "st": status, "l": ["gui"]}
    return it


def test_open_specs_is_named_immediately_after_open_prs(hub):
    """The done condition, read off the list the page sorts by."""
    flow = json.loads(_flow(hub.PAGE).split("=", 1)[1])
    assert flow == FLOW_ORDER, f"the board's flow list is {flow}"
    assert flow.index("Open Specs") == flow.index("Open PRs") + 1, \
        "Open Specs is not the column immediately right of Open PRs"


def test_the_board_draws_its_columns_in_the_flow_order(hub):
    """The seven Project statuses and the two PR columns, handed over scrambled."""
    groups = [_col("Testing"), _col("Blocked"), _col("In Progress"), _col("Todo"),
              _col("Open Specs"), _col("No status"), _col("Code Complete"), _col("Done")]
    assert _drawn(hub, groups) == FLOW_ORDER


def test_the_order_is_the_flows_and_never_the_counts(hub):
    """At zero cards as at four: the same names, under counts that would flip a sort by magnitude."""
    names = [n for n in FLOW_ORDER if n not in ("Open PRs", "Closed PRs")]
    for counts in ([0] * len(names), list(range(len(names))), list(range(len(names), 0, -1))):
        groups = [_col(n, c) for n, c in zip(list(reversed(names)), counts)]
        got = _drawn(hub, groups)
        assert got == FLOW_ORDER, f"counts {counts} drew {got}, not the flow's order"


def test_every_column_the_board_draws_is_named_in_the_flow(hub):
    """No column is placed by falling after the named ones: what the server draws, the list names."""
    hub.board_cache["items"] = [_item(n, "card", status, state)
                                for n, (status, state) in enumerate(
                                    [("Open Specs", "open"), ("Blocked", "open"), ("Todo", "open"),
                                     ("In Progress", "open"), ("Code Complete", "open"),
                                     ("Testing", "open"), ("Done", "closed")], start=101)]
    names = [g["name"] for g in hub.board()["groups"]]
    script = ("%s;console.log(JSON.stringify(%s.map(n=>FLOW.indexOf(n))))"
              % (_flow(hub.PAGE), json.dumps(names)))
    idx = json.loads(_node(script))
    unnamed = [n for n, i in zip(names, idx) if i < 0]
    assert not unnamed, f"the board draws {unnamed}, which the flow list does not name"


def test_a_column_the_flow_does_not_know_still_draws_before_the_archive(hub):
    """It is never dropped: it takes the place the list leaves for it, just short of the archive."""
    drawn = _drawn(hub, [_col("Waiting on review"), _col("Testing"), _col("Blocked"), _col(ARCHIVE)])
    assert "Waiting on review" in drawn, "a column the list does not name was dropped"
    assert drawn.index("Testing") < drawn.index("Waiting on review") < drawn.index(ARCHIVE), \
        f"an unnamed column landed at {drawn.index('Waiting on review')} of {drawn}"


def test_a_hidden_column_keeps_its_place_in_the_flow(hub):
    """The user's hidden set takes a column off the board; it never reorders one."""
    stmt = _sort(hub.PAGE)
    assert "hid" not in stmt and "hidden" not in stmt, \
        "the order reads the user's hidden set: a hidden column would move"
    assert re.search(r"vis=all\.filter\(", hub.PAGE), \
        "the visible columns must be all's own order, filtered, so a column shown again returns to its place"
    drawn = _drawn(hub, [_col("Open Specs"), _col("Testing"), _col(ARCHIVE)])
    assert drawn == ["Open PRs", "Open Specs", "Closed PRs", "Testing", "Done"]


def test_the_rule_is_the_flow_list_and_nothing_else(hub):
    """No sort by count, by name or by age: one list decides the reading order."""
    stmt = _sort(hub.PAGE)
    assert re.search(r"FLOW\.indexOf\(", stmt), "the sort no longer reads the flow list"
    assert not re.search(r"\b[xy]\.n\b", stmt), "the sort reads a card count"
    assert "localeCompare" not in stmt, "the sort reads a name"
    assert "Date" not in stmt and "age" not in stmt, "the sort reads an age"
    assert hub.PAGE.count("FLOW=[") == 1, "the flow is not one list written in one place"
