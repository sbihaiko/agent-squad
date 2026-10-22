"""The hub board lists only the cards of the profile's own repository.

One project can hold cards of several repositories, and an issue number repeats between them. The board read keeps an
Issue item only when its repository is the profile's repository; a draft item has no repository and stays.
"""
import importlib.util
import json
import os
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib" / "squad-goal" / "scripts" / "hub.py"


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_board_repo_scope", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def item(n, repo, kind="Issue"):
    content = {"number": n, "title": f"t{n}", "state": "open", "labels": [], "created_at": "2026-10-21T00:00:00Z",
               "sub_issues_summary": {"total": 0, "completed": 0}}
    if repo:
        content["repository_url"] = f"https://api.github.com/repos/{repo}"
    return {"id": f"i{n}", "content_type": kind, "content": content, "fields": [{"name": "Status", "value": {"name": {"raw": "Todo"}}}]}


def test_the_board_read_drops_a_card_of_another_repository_with_the_same_number(hub):
    items = [item(635, "mine/app"), item(635, "other/app"), item(7, "other/app"), item(None, None, "DraftIssue")]
    out = subprocess.run(["jq", "-c", hub.board_items_jq("mine/app")], input=json.dumps(items), capture_output=True, text=True, check=True).stdout
    rows = [json.loads(line) for line in out.splitlines()]
    assert [r["n"] for r in rows] == [635, None]
    assert [r["i"] for r in rows if r["n"] == 635] == ["i635"]
    assert 7 not in [r["n"] for r in rows]
