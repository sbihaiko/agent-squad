"""#524: the hub read one compare per work branch on every board pass, 60 a minute, and spent the REST quota. A compare
answer is a function of the landing-branch sha and the branch sha, so the hub reads it again only when one of them moves."""
import importlib.util
import json
import os
import tempfile
from pathlib import Path

import pytest

HUB_PY = Path(__file__).resolve().parent.parent / "contrib/squad-goal/scripts/hub.py"


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ["SG_CHECKOUT"] = tempfile.mkdtemp()
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_compare_cache", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _gh(branches, calls):
    def read(*args):
        path = args[-1]
        calls.append(path)
        if "/branches?" in path:
            return json.dumps([{"name": n, "commit": {"sha": s}} for n, s in branches.items()])
        return json.dumps({"status": "ahead", "ahead_by": 1, "commits": [{"commit": {"committer": {"date": "2026-10-17T10:00:00Z"}}}]})
    return read


def test_a_second_pass_with_no_tip_moved_reads_no_compare(hub, monkeypatch):
    branches = {"dev": "d1", "fix/7-a": "a1", "gui/8": "b1"}
    calls = []
    monkeypatch.setattr(hub, "gh_door_read", _gh(branches, calls))
    first = hub.open_branches_of("x/y")
    assert sum("/compare/" in c for c in calls) == 2
    calls.clear()
    assert hub.open_branches_of("x/y") == first, "the kept answer gives the same rows"
    assert [c for c in calls if "/compare/" in c] == [], "no tip moved: no compare is read again"


def test_a_moved_tip_reads_its_compare_again(hub, monkeypatch):
    branches = {"dev": "d1", "fix/7-a": "a1", "gui/8": "b1"}
    calls = []
    monkeypatch.setattr(hub, "gh_door_read", _gh(branches, calls))
    hub.open_branches_of("x/y")
    branches["gui/8"] = "b2"
    calls.clear()
    hub.open_branches_of("x/y")
    assert [c for c in calls if "/compare/" in c] == ["repos/x/y/compare/dev...b2"]
    branches["dev"] = "d2"
    calls.clear()
    hub.open_branches_of("x/y")
    assert sum("/compare/" in c for c in calls) == 2, "the landing branch moved: every compare is read again"
