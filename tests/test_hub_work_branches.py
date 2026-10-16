"""Hub board: the lane beside the axis shows open work branches, and it is for tracking only (#358, #384).

An open work branch is a remote `fix|gui/<n>-...` branch with commits the landing branch (dev) lacks. A landed branch is
one `branchland.sh` put in dev, and it is read from its issue card (Done = in dev): the board draws no lane for it, so
the hub keeps the ledger rows only for its own last-landing fact. Every assertion names its seam: the lane names, the
read, and the payload `board()` serves.
"""
import importlib.util
import json
import os
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"
REPO = "sbihaiko/agent-squad"


@pytest.fixture(scope="module")
def hub():
    pinned = {"SG_REPO": REPO, "SG_OWNER": "sbihaiko", "SG_PROJECT": "4", "SG_CHECKOUT": tempfile.mkdtemp(),
              "SG_HUB_DIR": tempfile.mkdtemp(), "SG_LAND_BRANCH": "dev"}
    before = {k: os.environ.get(k) for k in pinned}
    os.environ.update(pinned)
    spec = importlib.util.spec_from_file_location("hub_work_branches", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    yield mod
    for k, v in before.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _door(answers):
    def read(*args):
        route = next(a for a in args if "repos/" in a)
        for part, body in answers.items():
            if part in route:
                return json.dumps(body)
        raise RuntimeError(f"unexpected read {route}")
    return read


def test_the_open_lane_is_named_open_prs_and_the_two_kinds_carry_their_labels(hub):
    assert hub.COLUMN["pr_open"] == "Open PRs", "the pull-request lane is not the one the fact names"
    assert "br_open" not in hub.COLUMN and "pr_closed" not in hub.COLUMN, "the hub still reads a lane it should not"
    cm = hub._columns_module
    assert cm.kind_label(cm.PR_KIND) == "Open PRs" and cm.kind_label(cm.BRANCH_KIND) == "Branches"
    names = {(g["kind"], g["state"]): g["name"] for g in cm.kind_groups(hub.KINDS, [])}
    assert names[("pr", "open")] == "Open PRs" and names[("branch", "open")] == "Branches", \
        "the kinds view does not name a pull request and a work branch apart"
    assert not [n for n in names.values() if "Landed" in n or "Closed Branches" in n], names


def test_open_branches_are_the_work_branches_ahead_of_dev(hub, monkeypatch):
    monkeypatch.setattr(hub, "gh_door_read", _door({
        "branches?per_page": [
            {"name": "fix/358-board", "commit": {"sha": "a1"}},
            {"name": "gui/360-late", "commit": {"sha": "b2"}},
            {"name": "main", "commit": {"sha": "c3"}},
            {"name": "dev", "commit": {"sha": "d4"}}],
        "compare/dev...a1": {"status": "ahead", "ahead_by": 2, "commits": [{"commit": {"committer": {"date": "2026-10-14T09:00:00Z"}}}]},
        "compare/dev...b2": {"status": "behind", "ahead_by": 0, "commits": []}}))
    rows = hub.open_branches_of(REPO)
    assert [(r["n"], r["t"], r["r"]) for r in rows] == [(358, "fix/358-board", REPO)], \
        "the work branch ahead of dev is a row; a branch in dev, main and dev itself are not"
    assert rows[0]["ps"] == "ahead 2" and rows[0]["u"] == "2026-10-14T09:00:00Z"


def test_landed_branches_come_from_the_landing_ledger(hub, monkeypatch, tmp_path):
    monkeypatch.setattr(hub, "STATE", tmp_path)
    (tmp_path / "landed.tsv").write_text("1760432400\tfix/355-lander\tabc1234567\t355\n1760436000\tgui/358-board\tdef7654321\t358\n")
    rows = hub.landed_branches_of(REPO)
    assert [(r["n"], r["m"]) for r in rows] == [(355, True), (358, True)]
    assert "fix/355-lander" in rows[0]["t"] and "abc1234" in rows[0]["t"], "a row names the branch and the landed sha"


def _stub_pr_tools(hub, monkeypatch):
    monkeypatch.setattr(hub, "pr_state", lambda x: None)
    monkeypatch.setattr(hub, "gate_strip", lambda x, t=None: None)
    monkeypatch.setattr(hub, "pr_badge", lambda x: None)


def test_the_board_serves_no_work_branch_and_the_lane_carries_pull_requests_only(hub, monkeypatch):
    """A work branch has no lane and, the Kinds view having gone (owner, 2026-10-16), no other view of the board: the open
    lane carries pull requests only and says 0."""
    row = {"n": 358, "t": "fix/358-board", "u": "2026-10-14T09:00:00Z", "o": "", "r": REPO, "ps": "ahead 2", "br": True}
    landed = {"n": 355, "t": "fix/355-lander · abc1234", "c": "2026-10-14T08:00:00Z", "m": True, "r": REPO}
    monkeypatch.setattr(hub, "board_cache", {"t": 0, "items": [], "prs": [row], "landed": [landed], "errs": [], "err": None, "lc": {}})
    served = hub.board()
    assert served["prs"]["name"] == "Open PRs" and "prev" not in served, "the open lane is not the one the table names"
    assert served["prs"]["n"] == 0 and served["prs"]["items"] == [], "the lane carries pull requests only, and says 0"
    assert "kinds" not in served, "the board still serves a view that reaches a work branch"


def test_every_row_carries_its_own_kind(hub, monkeypatch):
    """A pull-request row is `pr` and a work branch row is `branch`: each row names what it is, so the page never reads the
    lane's name to tell them apart."""
    branch = {"n": 358, "t": "fix/358-board", "u": "2026-10-14T09:00:00Z", "o": "", "r": REPO, "ps": "ahead 2", "br": True}
    pr = {"n": 1491, "t": "The OPEN PRS card", "u": "2026-10-14T10:00:00Z", "o": "2026-10-14T08:00:00Z", "r": REPO, "l": [],
          "h": "a" * 40, "ms": "clean", "ci": []}
    monkeypatch.setattr(hub, "board_cache", {"t": 0, "items": [], "prs": [branch, pr], "landed": [], "errs": [], "err": None, "lc": {}})
    _stub_pr_tools(hub, monkeypatch)
    served = hub.board()
    assert [x["kind"] for x in served["prs"]["items"]] == ["pr"], "the lane carries the pull request only"
    assert all(x.get("kind") for x in served["prs"]["items"]), "a lane row carries no kind"


def test_a_lane_row_carries_the_chip_strip_badge_rollup_author_and_closing_issues(hub, monkeypatch):
    """All four row facts ride on the row: the chip and the review strip, the badge and the CI rollup, the author and the
    age, and the issues the pull request closes."""
    pr = {"n": 1491, "t": "Hub: a row", "u": "2026-10-14T10:00:00Z", "o": "2026-10-14T08:00:00Z", "r": REPO, "l": [],
          "h": "a" * 40, "ms": "clean", "ci": ["success"], "rollup": "pass", "by": "sbihaiko", "cl": [440, 441]}
    monkeypatch.setattr(hub, "board_cache", {"t": 0, "items": [], "prs": [pr], "landed": [], "errs": [], "err": None, "lc": {}})
    monkeypatch.setattr(hub, "pr_state", lambda x: ["ready", "ready to merge"])
    monkeypatch.setattr(hub, "gate_strip", lambda x, t=None: {"cells": []})
    monkeypatch.setattr(hub, "pr_badge", lambda x: "green")
    item = hub.board()["prs"]["items"][0]
    assert item["ps"] == ["ready", "ready to merge"] and item["gs"] == {"cells": []} and item["pb"] == "green"
    assert item["rollup"] == "pass" and item["ci"] == ["success"] and item["by"] == "sbihaiko" and item["cl"] == [440, 441]
    assert item["kind"] == "pr"
