"""Hub run list: the RUNNING header shows occupancy (runs and agents over their caps) and moves the group spend to its tooltip (#1508)."""
import importlib.util
import json
import os
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ.setdefault("SG_CHECKOUT", tempfile.mkdtemp())
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_occupancy_header", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _with_checkout(checkout):
    """Point the hub's caps lookup at `checkout` for one test, and give the environment back untouched."""
    class _Ctx:
        def __enter__(self):
            self.old = os.environ.get("SG_CHECKOUT")
            os.environ["SG_CHECKOUT"] = str(checkout)

        def __exit__(self, *a):
            os.environ.pop("SG_CHECKOUT", None)
            if self.old is not None:
                os.environ["SG_CHECKOUT"] = self.old
    return _Ctx()


def test_running_group_header_shows_occupancy_not_spend(hub):
    rows = [{"state": "running", "live": True, "spend": s, "agents": 3} for s in (1.1, 2.2, 3.3)]
    h = hub.group_header("running", rows, {"runs": 10, "children": 20})
    assert h["text"] == "RUNS 3/10 · AGENTS 9/20"
    assert h["title"] == "$6.60"   # the spend the header no longer prints


def test_finished_group_header_keeps_count_and_cost(hub):
    rows = [{"state": "verified", "live": False, "spend": 35.72, "agents": 0}] + [{"spend": 0.0, "agents": 0}] * 16
    h = hub.group_header("1h", rows, {"runs": 10, "children": 20})
    assert h["text"] == "17 · $35.72"
    assert h["title"] == "$35.72"


def test_running_header_falls_back_to_plain_counts_when_the_graph_is_unreadable(hub):
    rows = [{"state": "running", "live": True, "spend": 1.0, "agents": 3}] * 3
    h = hub.group_header("running", rows, None)
    assert h["text"] == "3 · 9"
    assert "/" not in h["text"] and "runs" not in h["text"] and "agents" not in h["text"]
    assert h["title"] == "$3.00"


def _project(checkout, **pair):
    p = Path(checkout) / "docs/squad/defaults.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("caps:\n" + "".join(f"  {k}: {v}\n" for k, v in pair.items()))


def test_loop_caps_is_the_one_resolution_the_engine_enforces(hub):
    from squad import caps
    co = tempfile.mkdtemp()
    _project(co, runs=2, children=5)
    with _with_checkout(co):
        got = hub.loop_caps()
    enforced = caps.ceiling(co)
    assert {k: got[k] for k in ("runs", "children")} == {k: enforced.caps[k] for k in ("runs", "children")} == {"runs": 2, "children": 5}
    assert got["waiting"] == enforced.waiting() == []


def test_loop_caps_is_none_when_the_resolution_fails(hub):
    co = tempfile.mkdtemp()
    _project(co, runs=11)                       # a project may only lower the machine-wide pair
    with _with_checkout(co):
        assert hub.loop_caps() is None


def test_the_header_prints_runs_then_agents_over_the_ceiling_and_names_what_waits(hub):
    rows = [{"state": "running", "live": True, "spend": 1.0, "agents": a} for a in (2, 3)]
    caps = {"runs": 10, "children": 20, "waiting": [{"ceiling": "caps.children", "holder": "run-x:route.router.4"}]}
    h = hub.group_header("running", rows, caps)
    assert h["text"] == "RUNS 2/10 · AGENTS 5/20 · waiting: run-x:route.router.4 (caps.children)"
    assert hub.run_groups(rows, caps)[0]["text"] == h["text"]              # the runs answer carries the same line


def test_scan_counts_the_children_still_working(hub):
    p = Path(tempfile.mkdtemp()) / "record.jsonl"
    ev = [{"event": "run_start", "ts": "2026-10-11T10:00:00Z"}]
    for n in (1, 2, 3):
        ev.append({"event": "spawn_accepted", "child": f"route.router.{n}", "parent": "router", "ts": "2026-10-11T10:00:01Z"})
    ev.append({"event": "outcome", "node": "route.router.1", "outcome": "pass", "ts": "2026-10-11T10:05:00Z"})
    p.write_text("".join(json.dumps(e) + "\n" for e in ev))
    assert hub.scan(str(p))["agents"] == 2      # the two children with no outcome yet


def test_the_runs_answer_binds_the_header_tooltip_to_the_group_spend(hub):
    """Re-anchored (#1277): the caps no longer reach the page. The runs answer reads them and carries the tooltip."""
    src = HUB_PY.read_text()
    assert "const CAPS=" not in src and "__CAPS__" not in src                   # no caps are substituted into the page
    assert "function groupHeader(" not in src                                   # the page holds no copy of the rule
    rows = [{"state": "running", "live": True, "spend": s, "agents": 3} for s in (1.1, 2.2, 3.3)]
    h = hub.run_groups(rows, {"runs": 10, "children": 20})[0]
    assert h["text"] == "RUNS 3/10 · AGENTS 9/20"
    assert h["title"] == "$6.60"                                                # spend in the tooltip, occupancy as the text
    assert 'title="${esc(g.title)}"' in hub.PAGE


def test_the_runs_answer_carries_the_occupancy_header_with_the_graphs_caps(hub):
    rows = [{"state": "completed", "live": False, "age": 600, "spend": 1.0, "agents": 0}]
    rows += [{"state": "running", "live": True, "spend": s, "agents": 3} for s in (1.1, 2.2, 3.3)]
    h = hub.run_groups(rows, {"runs": 10, "children": 20})[0]
    assert h["text"] == "RUNS 3/10 · AGENTS 9/20"
    assert h["title"] == "$6.60"


def test_the_runs_answer_keeps_plain_counts_when_the_graph_is_unreadable(hub):
    rows = [{"state": "running", "live": True, "spend": 1.0, "agents": 3}] * 3
    h = hub.run_groups(rows, None)[0]   # json null: what loop_caps() returns for an unreadable graph
    assert h["text"] == "3 · 9"
    assert h["title"] == "$3.00"


def _graph(path, run_parallel, children, parallel):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"run": {"max_parallel": run_parallel},
                                "nodes": [{"name": "route", "kind": "dispatch", "max_children": children, "max_parallel": parallel}]}))


def test_loop_caps_names_the_seeds_per_run_ceilings_on_a_fresh_checkout(hub):
    co = tempfile.mkdtemp()                                 # no docs/squad/workflows/ at all
    seed = json.loads((ROOT / "docs/design/router.graph.json").read_text())
    node = next(n for n in seed["nodes"] if n["kind"] == "dispatch")
    with _with_checkout(co):
        got = hub.loop_caps()
    assert (got["runs"], got["children"]) == (10, 20)       # the machine-wide pair is untouched
    assert got["per_run"]["max_parallel"] == seed["run"]["max_parallel"]
    assert (got["per_run"]["dispatch"][0]["max_children"], got["per_run"]["dispatch"][0]["max_parallel"]) == (node["max_children"], node["max_parallel"])


def test_loop_caps_names_the_owner_defaults_per_run_ceilings(hub):
    co = Path(tempfile.mkdtemp())
    _graph(co / "docs/squad/workflows/default.graph.json", 9, 8, 5)
    with _with_checkout(co):
        got = hub.loop_caps()
    assert got["per_run"]["max_parallel"] == 9
    assert [(d["max_children"], d["max_parallel"]) for d in got["per_run"]["dispatch"]] == [(8, 5)]


def test_the_running_header_tooltip_labels_the_per_run_pair_beside_the_spend(hub):
    rows = [{"state": "running", "live": True, "spend": 1.0, "agents": 1}]
    caps = {"runs": 10, "children": 20, "per_run": {"label": "per run: max_parallel 2 · route: max_children 4, max_parallel 2"}}
    h = hub.group_header("running", rows, caps)
    assert h["text"] == "RUNS 1/10 · AGENTS 1/20"          # the machine-wide pair stays the text
    assert h["title"] == "$1.00 · per run: max_parallel 2 · route: max_children 4, max_parallel 2"
    assert hub.run_groups(rows, caps)[0] == {**h, "key": "running", "label": "WORKERS", "empty": False}   # the answer carries the same line
