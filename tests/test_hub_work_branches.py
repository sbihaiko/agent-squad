"""Hub board: the lanes beside the axis show work branches, never pull requests (#358).

An open work branch is a remote `fix|gui/<n>-...` branch with commits the landing branch (dev) lacks. A landed branch is
one `branchland.sh` put in dev: its pass keeps a ledger row (`landed.tsv`: epoch, branch, landed sha, issue). Every
assertion names its seam: the lane names, the two reads, and the payload `board()` serves.
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


def test_the_lanes_are_named_for_branches_and_no_name_says_pull_request(hub):
    assert hub.COLUMN["pr_open"] == "Open branches"
    assert hub.COLUMN["pr_closed"] == "Landed branches"
    names = [g["name"] for g in hub._columns_module.kind_groups(hub.KINDS, [])]
    assert not [n for n in names if "PR" in n or "pull" in n.lower()], names


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
    assert hub.landed_total_of(REPO) == 2


def test_the_board_serves_branch_rows_without_a_pull_request_state(hub, monkeypatch):
    row = {"n": 358, "t": "fix/358-board", "u": "2026-10-14T09:00:00Z", "o": "", "r": REPO, "ps": "ahead 2", "br": True}
    monkeypatch.setattr(hub, "board_cache", {"t": 0, "items": [], "prs": [row], "prev": [], "prev_n": 0, "errs": [], "err": None, "lc": {}})
    served = hub.board()
    assert served["prs"]["name"] == "Open branches" and served["prev"]["name"] == "Landed branches"
    item = served["prs"]["items"][0]
    assert item["ps"] == "ahead 2" and not item.get("gs") and not item.get("pb"), "no review strip or badge: it is a branch"
