"""Hub: a needs-human run reads 'ended · needs a decision', and 'handled' once it landed in the fork (#1498)."""
import importlib.util
import os
import tempfile
from pathlib import Path

import pytest

from tests.gitrepo import make_repo, sh

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_needs_human_handled", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _needs_human(head, merged=False):
    return {"verdict": "needs-human", "reasons": ["ask"], "land": False, "head": head, "merged": merged, "exit_code": 10}


def _fork_with_next(path):
    repo = make_repo(path)
    sh(repo, "git", "branch", "local/next")
    return repo


def test_needs_human_whose_head_is_on_local_next_is_handled(hub, tmp_path):
    repo = _fork_with_next(tmp_path / "fork")
    head = sh(repo, "git", "rev-parse", "HEAD")
    st = hub.handled_of(hub.state_of(_needs_human(head), 0), _needs_human(head), checkout=repo)
    assert st == "handled"
    badge = hub.badge_of(st, _needs_human(head))
    assert badge["text"] == "ended · handled (landed in fork)"
    assert badge["color"] != "warn"


def test_needs_human_whose_head_is_not_on_local_next_keeps_the_warning_badge(hub, tmp_path):
    repo = _fork_with_next(tmp_path / "fork")
    sh(repo, "git", "checkout", "-q", "-b", "other")
    (repo / "side.txt").write_text("side\n")
    sh(repo, "git", "add", "-A")
    sh(repo, "git", "commit", "-q", "-m", "other")
    head = sh(repo, "git", "rev-parse", "HEAD")
    rec = _needs_human(head)
    st = hub.handled_of(hub.state_of(rec, 0), rec, checkout=repo)
    assert st == "needs-human"
    badge = hub.badge_of(st, rec)
    assert badge["text"] == "ended · needs a decision"
    assert badge["color"] == "warn"
    assert "exit" not in badge["text"]
    assert badge["title"] == "exit 10"


def test_needs_human_badge_text_has_no_exit_n_tooltip_carries_it(hub):
    rec = _needs_human("deadbeef")
    badge = hub.badge_of("needs-human", rec)
    assert "exit" not in badge["text"]
    assert badge["title"] == "exit 10"
    assert "span class=\"b ${r.state}\">${r.state}</span>" not in hub.PAGE
    assert "r.badge" in hub.PAGE and "r.tip" in hub.PAGE
