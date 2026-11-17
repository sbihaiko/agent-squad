"""The board column vocabulary has one host-free source of truth."""
import importlib.util
import json
import os
import re
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib" / "squad-goal" / "scripts"
COLUMNS_PY = SCRIPTS / "board_columns.py"
HUB_PY = SCRIPTS / "hub.py"
RECONCILE_PY = SCRIPTS / "reconcile.py"
JANITOR_PY = SCRIPTS / "janitor.py"
NAMES = ("No status", "Todo", "Open Specs", "In Progress", "Code Complete", "Blocked", "Done",
         "Open PRs")


def _module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def columns():
    return _module("board_columns_table", COLUMNS_PY).COLUMNS


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "sbihaiko/agent-squad")
    os.environ.setdefault("SG_OWNER", "sbihaiko")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    return _module("hub_columns", HUB_PY)


def test_each_column_declares_its_fact_actor_and_terminal_state(columns):
    assert {column["name"] for column in columns} == set(NAMES)
    assert all(isinstance(column["fact"], str) and column["fact"] for column in columns)
    assert all(isinstance(column["actor"], str) and column["actor"] for column in columns)
    assert all(isinstance(column["terminal"], bool) for column in columns)
    assert {column["name"] for column in columns if column["terminal"]} == {"Done"}


def test_every_column_has_a_distinct_fact(columns):
    facts = [column["fact"] for column in columns]

    assert len(facts) == len(set(facts))


@pytest.fixture(scope="module")
def table():
    return _module("board_columns_view", COLUMNS_PY)


def test_every_shipped_column_is_reachable_on_the_board(table, hub):
    """The hub draws the shipped definition (#247): a column it never draws, or a role it never reads, is dead."""
    names = [column["name"] for column in table.view_columns()]
    assert hub.ORDER == names, "the board's own order is not the definition's"
    flow = json.loads(re.search(r"FLOW=(\[[^;]*?\]),", hub.PAGE).group(1))
    assert flow == names, "the flow leaves a column out, so no reader can reach it"
    by_role = json.loads(re.search(r"const COLNAME=(\{.*?\});", hub.PAGE).group(1))
    assert set(by_role.values()) <= set(names) | {lane["name"] for lane in table.LANES}, \
        "the script reads a name by its role: a role naming no column or lane is a name it cannot draw"


def test_gating_reaches_the_lane_order_the_legend_the_phone_and_the_cfd_bands(table, hub, monkeypatch):
    """Every surface takes its order from the definition (spec #1218): the lane, the flow legend, the phone's served board
    and the cumulative flow bands all carry Gating between Landing and Closed, and no page names it itself."""
    names = [c["name"] for c in table.view_columns()]
    assert names.index("Landing") + 1 == names.index("Gating") == names.index("Closed") - 1, names
    assert hub.ORDER == names, "the lane order leaves Gating out"
    assert hub.FLOW_BANDS == names, "the CFD bands leave Gating out"
    flow = json.loads(re.search(r"FLOW=(\[[^;]*?\]),", hub.PAGE).group(1))
    assert flow == names, "the flow legend leaves Gating out"
    monkeypatch.setattr(hub, "board_cache", {"t": 1.0, "items": [], "prs": [], "err": None, "errs": []})
    assert [g["name"] for g in hub.board()["groups"]] == names, "the board the phone reads leaves Gating out"
    assert '"Gating"' not in hub.MOBILE_PAGE and "'Gating'" not in hub.MOBILE_PAGE, "the phone page names a column itself"


def test_a_card_that_reached_gating_leaves_it_only_forward(table):
    """#1257: a red or unproven attempt is a marker, not a move (ADR-0170). Gating sits between Landing
    and Closed on the shipped axis, so a write back to Landing is past and refused, and a card that left
    Gating forward never re-enters it for the same wave."""
    assert table.past("Gating", "Landing"), "a write back from Gating is not past"
    assert table.refused_for_automated("Gating", "Landing")
    assert table.past("Gating", "Code Complete"), "a version 1 spelling of Landing reads the same column (#2151)"
    assert not table.past("Gating", "Closed"), "the one forward move out of Gating reads as past"
    assert table.past("Closed", "Gating"), "a re-entry into Gating after the wave landed is past"


def test_the_hub_vocabulary_is_the_table(table, hub):
    assert hub.BOARD_COLUMNS == table.view_columns(), "the hub holds a vocabulary of its own"
    colours = json.loads(re.search(r"const COLORS=(\{.*?\});", hub.PAGE).group(1))
    names = [c["name"] for c in [*table.view_columns(), *table.LANES]]
    assert list(colours) == names, "the chart's column roles are not in the table's order"
    assert all(re.fullmatch(r"var\(--chart-[\w-]+\)", colours[name]) for name in names), \
        "the column chart copies a palette value instead of reading a chart role"


def test_a_card_closed_as_not_planned_draws_abandoned_whatever_its_status(table):
    """#921: the issue closed as NOT_PLANNED or as a duplicate was not delivered, so the board draws it Abandoned (the
    fact the definition names for that column), never Closed. The carrier answers lowercase; the GraphQL enum's
    uppercase spelling is read too, and `reopened` is a real reason that names no close verdict."""
    assert table.view_column("Closed", True, reason="not_planned") == ("Abandoned", None)
    assert table.view_column("Closed", True, reason="NOT_PLANNED") == ("Abandoned", None)
    assert table.view_column("Closed", True, reason="duplicate") == ("Abandoned", None), "a duplicate is no delivery (#517)"
    assert table.view_column("Closed", True, reason="DUPLICATE") == ("Abandoned", None), "the GraphQL enum's spelling"
    assert table.view_column("Closed", True, reason="completed") == ("Closed", None)
    assert table.view_column("Closed", True, reason="reopened") == ("Closed", None)
    assert table.view_column("Closed", True) == ("Closed", None), "a read that carries no reason keeps the Status's word"


def test_the_terminal_column_follows_the_close_reason_and_both_passes_read_it(table):
    """#1932 (spec #132): a closed issue's card goes to Abandoned when the close reason says the issue was not delivered,
    and to Closed only for a completed close. The janitor's terminal rule and the reconciler both derive it from this one
    function, so the two passes can never disagree about where a closed issue's card belongs."""
    assert table.terminal_column("closed", "not_planned") == "Abandoned"
    assert table.terminal_column("closed", "duplicate") == "Abandoned"
    assert table.terminal_column("closed", "completed") == "Closed"
    assert table.terminal_column("closed", None) == "Closed"
    assert table.terminal_column("open", None) is None
    for path in (RECONCILE_PY, JANITOR_PY):
        assert "terminal_column" in path.read_text(), f"{path.name} derives the terminal column on its own"


def test_the_hub_names_no_column_literal_of_its_own(columns):
    source = HUB_PY.read_text()

    for column in columns:
        assert f'"{column["name"]}"' not in source, \
            f"hub.py names the column {column['name']!r} itself; the name comes from the table"


def test_the_reconciler_names_no_column_literal_of_its_own(columns):
    source = RECONCILE_PY.read_text()

    for column in columns:
        assert f'"{column["name"]}"' not in source, \
            f"reconcile.py names the column {column['name']!r} itself; the name comes from the table"


def test_the_table_names_one_open_lane_and_no_closed_one(table):
    """One open tracking lane -- Open PRs -- and no closed one: a work branch has no lane of its own (its kind reaches the
    kinds view), and a landed branch is read from its issue card (Done = in dev)."""
    assert [lane["name"] for lane in table.LANES] == ["Open PRs"], "the table does not name the one open lane"
    assert all(lane["key"] != "pr_closed" for lane in table.LANES), "the table still holds a closed-branch lane"
    assert "Landed branches" not in {column["name"] for column in table.COLUMNS}, \
        "the column table still holds the landed-branch column"
    states = [g["state"] for g in table.kind_groups(("bug",), []) if g["kind"] == table.PR_KIND]
    assert states == ["open"], "the pull request kind still carries a closed group"


def test_the_two_machine_kinds_carry_their_own_labels(table):
    """The pull request kind reads Open PRs and a work branch reads Branches: the two kinds never share a word, and neither
    carries a closed group (a work branch has no lane, and a landed one is read from its issue card)."""
    assert table.kind_label(table.PR_KIND) == "Open PRs" and table.kind_label(table.BRANCH_KIND) == "Branches"
    names = {(g["kind"], g["state"]): g["name"] for g in table.kind_groups(("bug",), [])}
    assert names[("pr", "open")] == "Open PRs" and names[("branch", "open")] == "Branches"
    assert {s for (k, s) in names if k in (table.PR_KIND, table.BRANCH_KIND)} == {"open"}


def test_the_open_lanes_are_tracking_only(table):
    """The lane is drawn beside the axis and read by no writer as state: no card moves because a PR or a branch is open."""
    for lane in table.LANES:
        assert lane.get("tracking") is True, "the table does not say the lane is for tracking only"
        for name in ("janitor.py", "reconcile.py", "branchland.sh", "ffland.sh", "wtclean.sh"):
            source = (SCRIPTS / name).read_text()
            assert lane["name"] not in source and lane["key"] not in source, \
                f"{name} reads or writes the tracking lane; the lane moves no card and gates nothing"


def test_two_repositories_sharing_a_pull_request_number_keep_both_rows(table):
    rows = [{"kd": "pr", "n": 5, "r": "sbihaiko/agent-squad", "t": "one"},
            {"kd": "pr", "n": 5, "r": "sbihaiko/squad-board", "t": "two"}]
    open_prs = next(g for g in table.kind_groups(("bug",), rows) if g["kind"] == table.PR_KIND and g["state"] == "open")

    assert [x["t"] for x in open_prs["items"]] == ["one", "two"], "a pull request of one repository hides the other's"
    assert [x["key"] for x in open_prs["items"]] == ["pr:sbihaiko/agent-squad#5", "pr:sbihaiko/squad-board#5"], \
        "the key is not the kind, the repository and the number"


def test_a_row_without_a_repository_keys_on_the_profile_default(table):
    rows = [{"kd": "pr", "n": 5, "t": "older"}]
    open_prs = next(g for g in table.kind_groups(("bug",), rows, "sbihaiko/agent-squad")
                    if g["kind"] == table.PR_KIND and g["state"] == "open")

    assert [x["key"] for x in open_prs["items"]] == ["pr:sbihaiko/agent-squad#5"], \
        "a row with no repository does not take the profile default in its key"


def test_the_hub_renders_no_closed_branch_lane(table, hub, monkeypatch):
    monkeypatch.setattr(hub, "board_cache", {"t": 1.0, "items": [], "prs": [], "err": None, "errs": []})
    board = hub.board()

    assert tuple(hub.BOARD_LANES) == tuple(table.LANES), "the hub draws a lane the table does not name"
    assert "prev" not in board, "the board still serves a closed-branch lane"
    assert "pr_closed" not in hub.COLUMN, "the hub still reads a lane by a role the table does not name"
    assert '"pr_closed"' not in hub.PAGE, "the page still holds a name for the closed-branch lane"


def test_a_reopened_issue_returns_its_card_from_closed_to_todo_only(table):
    """#1420 (ADR-0143 rule 2): the reopen is the door from Closed to Todo; every other reopened move is refused."""
    assert not table.refused_for_automated("Closed", "Todo", "reopened")
    assert table.refused_for_automated("Closed", "Doing", "reopened")
    assert table.refused_for_automated("Landing", "Todo", "reopened")
    assert table.refused_for_automated("Closed", "Todo")


def test_only_the_owner_moves_a_backlog_card_to_todo(table):
    """#1909 (ADR-0142, ADR-0143 rule 1): an automated writer never moves a Backlog card to Todo, whatever the reason; the
    other moves out of Backlog keep their answer."""
    for reason in (None, "no-landing", "reopened"):
        assert table.refused_for_automated("Backlog", "Todo", reason), reason
    assert not table.refused_for_automated("Backlog", "Closed")
    assert not table.refused_for_automated("Backlog", "Abandoned")
