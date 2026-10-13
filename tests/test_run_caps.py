"""The per-run ceilings the hub shows: the graph the engine resolves (`squad/workflows.py`), or the newest live run's own graph.json.
They are not the machine-wide pair (`squad/caps.py`); the panel names which is which."""
import json
from pathlib import Path

from squad import run_caps, workflows

ROOT = Path(__file__).resolve().parent.parent


def _graph(path, run_parallel, children, parallel, name="route"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"run": {"max_parallel": run_parallel},
                                "nodes": [{"name": "router", "kind": "agent"},
                                          {"name": name, "kind": "dispatch", "max_children": children, "max_parallel": parallel}]}))
    return path


def test_a_checkout_with_no_workflows_folder_shows_the_seeds_ceilings(tmp_path):
    seed = _graph(tmp_path / "seed" / "router.graph.json", 6, 7, 3)
    got = run_caps.read(tmp_path / "checkout", seed=seed)
    assert got["max_parallel"] == 6
    assert got["dispatch"] == [{"name": "route", "max_children": 7, "max_parallel": 3}]
    assert got["source"] == str(seed)


def test_the_shipped_seed_is_what_a_fresh_checkout_reads(tmp_path):
    got = run_caps.read(tmp_path)                      # no docs/squad/workflows/ here: the engine runs the router seed
    seed = json.loads((ROOT / "docs" / "design" / "router.graph.json").read_text())
    node = next(n for n in seed["nodes"] if n["kind"] == "dispatch")
    assert got["max_parallel"] == seed["run"]["max_parallel"]
    assert (got["dispatch"][0]["max_children"], got["dispatch"][0]["max_parallel"]) == (node["max_children"], node["max_parallel"])


def test_an_owner_default_wins_over_the_seed(tmp_path):
    seed = _graph(tmp_path / "seed" / "router.graph.json", 6, 7, 3)
    mine = _graph(tmp_path / "checkout" / workflows.SUBDIR / "default.graph.json", 9, 8, 5)
    got = run_caps.read(tmp_path / "checkout", seed=seed)
    assert (got["max_parallel"], got["dispatch"][0]["max_children"], got["dispatch"][0]["max_parallel"]) == (9, 8, 5)
    assert got["source"] == str(mine)


def test_the_old_dynamic_name_is_read_when_there_is_no_default(tmp_path):
    seed = _graph(tmp_path / "seed" / "router.graph.json", 6, 7, 3)
    _graph(tmp_path / "checkout" / workflows.SUBDIR / "dynamic.graph.json", 11, 12, 13)
    got = run_caps.read(tmp_path / "checkout", seed=seed)
    assert (got["max_parallel"], got["dispatch"][0]["max_children"], got["dispatch"][0]["max_parallel"]) == (11, 12, 13)


def test_the_newest_live_runs_own_graph_wins(tmp_path):
    seed = _graph(tmp_path / "seed" / "router.graph.json", 6, 7, 3)
    old = _graph(tmp_path / "runs" / "a" / "graph.json", 1, 1, 1)
    new = _graph(tmp_path / "runs" / "b" / "graph.json", 2, 4, 2)
    import os
    os.utime(old, (1, 1))
    got = run_caps.read(tmp_path / "checkout", [old.parent, new.parent], seed=seed)
    assert (got["max_parallel"], got["dispatch"][0]["max_children"], got["dispatch"][0]["max_parallel"]) == (2, 4, 2)
    assert got["source"] == str(new)


def test_a_live_run_with_no_graph_falls_back_to_the_resolved_workflow(tmp_path):
    seed = _graph(tmp_path / "seed" / "router.graph.json", 6, 7, 3)
    (tmp_path / "runs" / "c").mkdir(parents=True)
    got = run_caps.read(tmp_path / "checkout", [tmp_path / "runs" / "c"], seed=seed)
    assert got["max_parallel"] == 6


def test_the_label_names_which_number_is_which(tmp_path):
    seed = _graph(tmp_path / "seed" / "router.graph.json", 2, 4, 2)
    assert run_caps.read(tmp_path, seed=seed)["label"] == "per run: max_parallel 2 · route: max_children 4, max_parallel 2"


def test_an_unreadable_graph_is_none_never_a_guess(tmp_path):
    bad = tmp_path / "seed.graph.json"
    bad.write_text("{not json")
    assert run_caps.read(tmp_path, seed=bad) is None
    assert run_caps.read(tmp_path, seed=tmp_path / "missing.graph.json") is None
