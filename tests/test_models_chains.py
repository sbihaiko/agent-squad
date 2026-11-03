"""The repository chains and the live chains are compared on request, and applied only by an explicit command (#1261).

A hand-run publish and the hub report each chain whose head or order differs; the per-cycle pass compares nothing.
The apply copies the repository chains into the live file atomically, keeps a backup, and keeps every other key.
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "contrib" / "squad-goal" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import models_chains  # noqa: E402


def doc(router_head, **extra):
    return {"schema_version": 2, "chains": {
        "coding": [{"id": "a", "route": "gateway"}, {"id": "b", "route": "gateway"}],
        "router": [{"id": router_head, "route": "gateway"}, {"id": "z", "route": "gateway"}]}, **extra}


def test_a_differing_router_head_is_reported_naming_both_chains():
    out = models_chains.diff(doc("x"), doc("y"))
    assert [d["chain"] for d in out] == ["router"]
    line = models_chains.line(out[0])
    assert "router" in line and "x, z" in line and "y, z" in line


def test_a_changed_order_is_reported():
    live = doc("x")
    live["chains"]["coding"].reverse()
    assert [d["chain"] for d in models_chains.diff(doc("x"), live)] == ["coding"]


def test_identical_chains_report_nothing():
    assert models_chains.diff(doc("x", other=1), doc("x", other=2)) == []


def test_apply_replaces_only_chains_keeps_a_backup_and_leaves_no_temp(tmp_path):
    repo, live = tmp_path / "repo.json", tmp_path / "live" / "scripts" / "models.json"
    live.parent.mkdir(parents=True)
    repo.write_text(json.dumps(doc("x", review_timeout_seconds=1)))
    old = doc("y", review_timeout_seconds=999, mine="keep")
    live.write_text(json.dumps(old))
    backup = models_chains.apply(repo, live)
    new = json.loads(live.read_text())
    assert new["chains"] == doc("x")["chains"]
    assert new["review_timeout_seconds"] == 999 and new["mine"] == "keep"
    assert json.loads(backup.read_text()) == old
    assert backup.parent != live.parent, "a .bak in live scripts/ is clutter to live-guard"
    assert [p.name for p in live.parent.iterdir()] == ["models.json"]


def test_apply_refuses_when_a_file_is_missing(tmp_path):
    repo = tmp_path / "repo.json"
    repo.write_text(json.dumps(doc("x")))
    with pytest.raises(SystemExit):
        models_chains.apply(repo, tmp_path / "none.json")


def test_the_apply_command_runs_from_the_shell(tmp_path):
    repo, live = tmp_path / "repo.json", tmp_path / "scripts" / "models.json"
    live.parent.mkdir()
    repo.write_text(json.dumps(doc("x")))
    live.write_text(json.dumps(doc("y")))
    r = subprocess.run(["bash", str(SCRIPTS / "models-apply.sh"), str(repo), str(live)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert json.loads(live.read_text())["chains"]["router"][0]["id"] == "x"


@pytest.fixture
def hub(tmp_path, monkeypatch):
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_chain_diff", SCRIPTS / "hub.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    live = tmp_path / "live.json"
    monkeypatch.setattr(mod, "MODELS", live)
    monkeypatch.setattr(mod, "CHECKOUT", str(tmp_path / "co"))
    return mod, live, tmp_path / "co" / "contrib" / "squad-goal" / "scripts" / "models.json"


def test_the_hub_computes_the_difference_for_the_page(hub):
    mod, live, repo = hub
    repo.parent.mkdir(parents=True)
    repo.write_text(json.dumps(doc("x")))
    live.write_text(json.dumps(doc("y")))
    out = mod.repo_chain_diff()
    assert out == [{"chain": "router", "repo": ["x", "z"], "live": ["y", "z"]}]
    assert "repo_diff" in mod.READONLY


def test_the_hub_shows_nothing_when_the_chains_match_or_the_repo_copy_is_missing(hub):
    mod, live, repo = hub
    live.write_text(json.dumps(doc("y")))
    assert mod.repo_chain_diff() == []
    repo.parent.mkdir(parents=True)
    repo.write_text(json.dumps(doc("y")))
    assert mod.repo_chain_diff() == []
