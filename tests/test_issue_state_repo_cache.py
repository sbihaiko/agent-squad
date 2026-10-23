"""One board, two repositories: the item map a binding caches is its own repository's (#770).
board.py binds the repository SG_REPO names, so a card of another repository with the same number is never moved."""
import importlib.util
import json
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "contrib" / "squad-goal" / "scripts" / "issue_state.py"
SPEC = importlib.util.spec_from_file_location("issue_state_repo_cache", SCRIPT)
issue_state = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(issue_state)

ROWS = [[5, "ITEM_A5", "https://api.github.com/repos/acme/a"], [5, "ITEM_B5", "https://api.github.com/repos/acme/b"]]


def fake_run(argv, **kw):
    out = "\n".join(json.dumps(r) for r in ROWS) if any("items?per_page=100" in a for a in argv) else "[]"
    return subprocess.CompletedProcess(argv, 0, out, "")


def binding(repo, st):
    return issue_state.BoardBinding("acme", 3, repo, kind="users", state_dir=st, run=fake_run, env={})


def test_each_repository_reads_its_own_card_from_a_shared_board(tmp_path):
    assert binding("acme/a", tmp_path).item_id(5) == "ITEM_A5"
    assert binding("acme/b", tmp_path).item_id(5) == "ITEM_B5"


def test_a_repository_never_reads_the_map_an_unfiltered_binding_cached(tmp_path):
    assert binding("", tmp_path).item_id(5) in ("ITEM_A5", "ITEM_B5")
    assert binding("acme/a", tmp_path).item_id(5) == "ITEM_A5"
    assert binding("acme/b", tmp_path).item_id(5) == "ITEM_B5"
