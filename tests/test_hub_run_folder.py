"""The board answer finds the folder of each holding run without a walk of every run root per card (#1771).

`/api/board` asks `holder_fact` for every held card, and each asked `run_folder`, which globbed every worktree of the
checkout and statted each root's record. A found folder is now read back with one stat, and the root list is read again
only after a short bound, so a new worktree still shows up.
"""
import importlib.util
import os
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"


@pytest.fixture()
def hub(tmp_path, monkeypatch):
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ.setdefault("SG_CHECKOUT", tempfile.mkdtemp())
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_run_folder", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    co = tmp_path / "co"
    (co / "runs").mkdir(parents=True)
    monkeypatch.setattr(mod, "CHECKOUT", str(co))
    monkeypatch.setattr(mod, "PROJECTS", "")
    monkeypatch.setattr(mod, "FORK_DIR", "")
    return mod, co


def _run(root, name):
    d = root / name
    d.mkdir(parents=True)
    (d / "record.jsonl").write_text("{}\n")
    return d


def _count_walks(mod, monkeypatch):
    calls = []
    real = mod.run_roots
    monkeypatch.setattr(mod, "run_roots", lambda co: calls.append(co) or real(co))
    return calls


def test_a_found_run_folder_is_read_again_without_a_walk_of_the_roots(hub, monkeypatch):
    mod, co = hub
    folder = _run(co / "worktrees" / "run" / "a" / "runs", "run-1")
    walks = _count_walks(mod, monkeypatch)
    assert mod.run_folder("run-1") == folder
    first = len(walks)
    for _ in range(20):
        assert mod.run_folder("run-1") == folder
    assert len(walks) == first, "each held card walks every run root again"


def test_the_roots_are_walked_once_for_many_cards_inside_the_bound(hub, monkeypatch):
    mod, co = hub
    for i in range(5):
        _run(co / "runs", f"run-{i}")
    walks = _count_walks(mod, monkeypatch)
    for i in range(5):
        mod.run_folder(f"run-{i}")
        mod.run_folder("run-missing")
    assert len(walks) <= 1, f"the roots were walked {len(walks)} times for one board answer"


def test_a_run_folder_that_is_gone_is_looked_up_again(hub):
    mod, co = hub
    old = _run(co / "runs", "run-9")
    assert mod.run_folder("run-9") == old
    (old / "record.jsonl").unlink()
    assert mod.run_folder("run-9") is None, "a folder whose record is gone is still named"


def test_a_run_in_a_new_worktree_is_found_after_the_bound(hub, monkeypatch):
    mod, co = hub
    assert mod.run_folder("run-new") is None
    folder = _run(co / "worktrees" / "child" / "b" / "runs", "run-new")
    monkeypatch.setattr(mod, "ROOTS_TTL", 0.0)
    assert mod.run_folder("run-new") == folder, "a run in a worktree made after the last walk is never found"


def test_cards_of_one_run_share_one_read_of_its_record(hub, monkeypatch):
    """The board's key and its body both ask holder_fact for every held card, and each call read the run's whole record
    and scanned for its process. A real board carries more than a thousand cards, four hundred of them held by a handful
    of runs, so the answer cost seconds per request (#1790). One run is one read inside the bound."""
    mod, co = hub
    folder = _run(co / "runs", "run-7")
    reads = []

    class _R:
        NO_PID_IDLE_S = 60.0   # the reaper's own bound, which the holder reads beside `run_live`

        def run_live(self, f, now=None):
            reads.append(1)
            return True
    monkeypatch.setattr(mod, "reaper", lambda: _R())
    monkeypatch.setattr(mod, "run_folder", lambda r: folder)
    for i in range(50):
        assert mod.holder_fact({"n": i, "h": {"run": "run-7", "started": "1700000000"}})[1] == "running"
    assert len(reads) == 1, f"one run's record was read {len(reads)} times for one board answer"
    mod.HOLDER_CHECK_SECS = 0.0
    mod.holder_fact({"n": 0, "h": {"run": "run-7", "started": "1700000000"}})
    assert len(reads) == 2, "a fact past the bound is read again"


def test_a_board_build_never_answers_a_holder_from_an_earlier_build(hub, monkeypatch):
    """The fact is the run's liveness, and a run starts or dies with no board read. A memo that outlived its build answered
    a later one with a liveness it no longer had: a build that followed one where the run had ended read `ended` for a
    live run (#1790). One board build is the memo's bound."""
    mod, co = hub
    folder = _run(co / "runs", "run-7")
    live = {"v": False}

    class _R:
        NO_PID_IDLE_S = 60.0   # the reaper's own bound, which the holder reads beside `run_live`

        def run_live(self, f, now=None):
            return live["v"]
    monkeypatch.setattr(mod, "reaper", lambda: _R())
    monkeypatch.setattr(mod, "run_folder", lambda r: folder)
    card = {"n": 0, "h": {"run": "run-7", "started": "1700000000"}}
    assert mod.holder_fact(card)[1] == "ended"
    live["v"] = True
    assert mod.holder_fact(card)[1] == "ended", "the memo answers inside one build"
    mod.board_served()
    assert mod.holder_fact(card)[1] == "running", "a later build asks again instead of answering from the memo"
