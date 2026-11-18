"""Two profiles on one owner's project share one board cache file, and each profile's /api/load reads its own rows (#1015).

address.sh keys the board cache by owner and project, so two repositories on one project write the same file. The mux
imports one hub.py per profile, and each reads that file at import: the rows the other repository's hub wrote last must
never stand as its own board until its first pass. Fixture profiles over temp checkouts; no network.
"""
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "contrib/squad-goal/scripts"


def _spec(n, title, repo_done, total):
    return {"n": n, "t": title, "s": "OPEN", "st": "Doing", "l": ["spec"], "si": {"total": total, "completed": repo_done}}


def _ticket(n, title):
    return {"n": n, "t": title, "s": "OPEN", "st": "Todo", "l": []}


@pytest.fixture
def shared_board(tmp_path, monkeypatch):
    prof, base = tmp_path / "profiles", tmp_path / "state"
    prof.mkdir()
    for name in ("alpha", "beta"):
        (tmp_path / f"co-{name}" / "runs").mkdir(parents=True)
        (prof / f"{name}.sh").write_text(
            f': "${{SG_REPO:=x/{name}}}"; : "${{SG_OWNER:=x}}"; : "${{SG_PROJECT:=5}}"; '
            f': "${{SG_CHECKOUT:={tmp_path}/co-{name}}}"\n')
    monkeypatch.setenv("SG_PROFILES_DIR", str(prof))
    monkeypatch.setenv("SG_STATE_BASE", str(base))
    cache = base / "_boards" / "x_5" / "hub-board.json"   # the one file both profiles name (address.sh sg_board_cache)
    cache.parent.mkdir(parents=True)
    # beta's hub wrote the shared file last: its rows carry no repository, the file names beta
    cache.write_text(json.dumps({"t": 1.0, "rt": 1, "rv": 2, "repo": "x/beta", "err": None, "errs": [],   # rv = hub.BOARD_READ
                                 "items": [_spec(1, "Beta spec", 4, 10), _ticket(2, "Beta ticket")],
                                 "prs": [], "landed": [], "cases": []}))
    spec = importlib.util.spec_from_file_location("hubmux_shared_board", SCRIPTS / "hubmux.py")
    mux = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mux)
    mux.PORT = mux.LIVE_PORT   # the live hub pins each profile's board cache, so both name the shared file
    apps = mux.load_apps()
    assert "err" not in apps["alpha"] and "err" not in apps["beta"], apps
    return apps, cache


def test_both_profiles_name_one_board_cache(shared_board):
    apps, cache = shared_board
    assert apps["alpha"]["mod"].BOARD == apps["beta"]["mod"].BOARD == cache


def test_a_profile_never_serves_the_other_repos_specs_from_the_shared_cache(shared_board):
    apps, _ = shared_board
    payload = apps["alpha"]["mod"].load_payload()
    assert payload["speed"] is None, "alpha holds no row, so it draws no Throughput card"
    alpha = payload["specs"]
    assert (alpha["open"], alpha["done"], alpha["total"]) == (0, 0, 0), "beta's specs reach alpha's row"
    assert "Beta spec" not in alpha["tip"]


def test_the_repo_that_wrote_the_cache_keeps_its_rows(shared_board):
    apps, _ = shared_board
    beta = apps["beta"]["mod"].load_payload()["specs"]
    assert "Beta spec" in beta["tip"]
    assert (beta["open"], beta["done"], beta["total"]) == (1, 4, 10)   # the row sums the open spec alone, never the No spec group
    assert beta["value"] == "4 of 10 closed - 40%"


def test_each_profile_counts_its_own_specs_after_both_passes_write_the_one_file(shared_board):
    apps, cache = shared_board
    a, b = apps["alpha"]["mod"], apps["beta"]["mod"]
    a.board_cache.update(repo="x/alpha", items=[_spec(7, "Alpha spec", 1, 2)]); a.save_board()   # alpha's pass writes last
    assert json.loads(cache.read_text())["repo"] == "x/alpha"
    assert "Alpha spec" in a.load_payload()["specs"]["tip"]
    beta = b.load_payload()["specs"]
    assert "Beta spec" in beta["tip"] and "Alpha spec" not in beta["tip"]
