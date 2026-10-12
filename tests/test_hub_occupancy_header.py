"""Hub run list: the RUNNING header shows occupancy (runs and agents over their caps) and moves the group spend to its tooltip (#1508)."""
import importlib.util
import json
import os
import re
import shutil
import subprocess
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


def _graph(checkout, run, nodes=()):
    p = Path(checkout) / "docs/squad/workflows/dynamic.graph.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"schema_version": 1, "name": "dynamic", "run": run, "nodes": list(nodes)}))
    return p


def _with_checkout(checkout):
    """Point the hub's graph lookup at `checkout` for one test, and give the environment back untouched."""
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
    h = hub.group_header("running", rows, {"max_parallel": 10, "max_children": 20})
    assert h["text"] == "3/10 runs · 9/20 agents"
    assert h["title"] == "$6.60"   # the spend the header no longer prints


def test_finished_group_header_keeps_count_and_cost(hub):
    rows = [{"state": "verified", "live": False, "spend": 35.72, "agents": 0}] + [{"spend": 0.0, "agents": 0}] * 16
    h = hub.group_header("1h", rows, {"max_parallel": 10, "max_children": 20})
    assert h["text"] == "17 · $35.72"
    assert h["title"] == "$35.72"


def test_running_header_falls_back_to_plain_counts_when_the_graph_is_unreadable(hub):
    rows = [{"state": "running", "live": True, "spend": 1.0, "agents": 3}] * 3
    h = hub.group_header("running", rows, None)
    assert h["text"] == "3 · 9"
    assert "/" not in h["text"] and "runs" not in h["text"] and "agents" not in h["text"]
    assert h["title"] == "$3.00"


def test_loop_caps_reads_max_parallel_from_run_and_max_children_from_the_dispatch_node(hub):
    co = tempfile.mkdtemp()
    _graph(co, {"max_parallel": 10, "spend_cap": 168}, [{"name": "route", "kind": "dispatch", "max_children": 20}])
    with _with_checkout(co):
        assert hub.loop_caps() == {"max_parallel": 10, "max_children": 20}


def test_loop_caps_is_none_when_the_graph_cannot_be_read(hub):
    co = tempfile.mkdtemp()
    with _with_checkout(co):
        assert hub.loop_caps() is None          # no graph file at all
    _graph(co, {"max_parallel": 10}, [{"name": "route", "kind": "dispatch", "max_children": 20}])
    Path(co, "docs/squad/workflows/dynamic.graph.json").write_text("{not json")
    with _with_checkout(co):
        assert hub.loop_caps() is None          # an unparsable graph shows no cap either


def _owner_graph(checkout, name, run, nodes=()):
    p = Path(checkout) / f"docs/squad/workflows/{name}.graph.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"schema_version": 1, "name": name, "run": run, "nodes": list(nodes)}))
    return p


def test_loop_caps_reads_the_router_seed_when_the_checkout_has_no_workflows_folder(hub):
    co = tempfile.mkdtemp()      # no docs/squad/workflows/: the engine runs the router seed (squad/workflows.py DYNAMIC_SEED)
    with _with_checkout(co):
        assert hub.loop_caps() == {"max_parallel": 2, "max_children": 4, "dispatch_max_parallel": 2}


def test_loop_caps_reads_the_owner_default_graph_and_both_dispatch_numbers(hub):
    co = tempfile.mkdtemp()
    _owner_graph(co, "default", {"max_parallel": 7}, [{"name": "route", "kind": "dispatch", "max_children": 9, "max_parallel": 3}])
    with _with_checkout(co):
        assert hub.loop_caps() == {"max_parallel": 7, "max_children": 9, "dispatch_max_parallel": 3}


def test_loop_caps_prefers_default_over_the_old_dynamic_name(hub):
    co = tempfile.mkdtemp()
    _owner_graph(co, "dynamic", {"max_parallel": 5}, [{"name": "route", "kind": "dispatch", "max_children": 6}])
    _owner_graph(co, "default", {"max_parallel": 7}, [{"name": "route", "kind": "dispatch", "max_children": 9}])
    with _with_checkout(co):
        assert hub.loop_caps() == {"max_parallel": 7, "max_children": 9}


def test_loop_caps_prefers_the_graph_the_live_run_was_launched_with(hub):
    co, run = tempfile.mkdtemp(), Path(tempfile.mkdtemp())
    _owner_graph(co, "default", {"max_parallel": 7}, [{"name": "route", "kind": "dispatch", "max_children": 9}])
    (run / "graph.json").write_text(json.dumps({"run": {"max_parallel": 3}, "nodes": [{"name": "route", "kind": "dispatch", "max_children": 5, "max_parallel": 2}]}))
    with _with_checkout(co):
        assert hub.loop_caps([str(run)]) == {"max_parallel": 3, "max_children": 5, "dispatch_max_parallel": 2}
        assert hub.loop_caps([str(run / "no-such-run")]) == {"max_parallel": 7, "max_children": 9}   # no run graph: the checkout's


def test_the_header_labels_the_dispatch_parallel_limit(hub):
    rows = [{"state": "running", "live": True, "spend": 1.0, "agents": 3}] * 2
    h = hub.group_header("running", rows, {"max_parallel": 2, "max_children": 4, "dispatch_max_parallel": 2})
    assert h["text"] == "2/2 runs · 6/4 agents · 2 parallel"


def test_the_page_renders_the_dispatch_parallel_limit(hub):
    h = _render_in_the_browser(hub, {"max_parallel": 2, "max_children": 4, "dispatch_max_parallel": 2}, [{"spend": 1.0, "agents": 3}] * 2)
    assert h["text"] == "2/2 runs · 6/4 agents · 2 parallel"


def test_scan_counts_the_children_still_working(hub):
    p = Path(tempfile.mkdtemp()) / "record.jsonl"
    ev = [{"event": "run_start", "ts": "2026-10-11T10:00:00Z"}]
    for n in (1, 2, 3):
        ev.append({"event": "spawn_accepted", "child": f"route.router.{n}", "parent": "router", "ts": "2026-10-11T10:00:01Z"})
    ev.append({"event": "outcome", "node": "route.router.1", "outcome": "pass", "ts": "2026-10-11T10:05:00Z"})
    p.write_text("".join(json.dumps(e) + "\n" for e in ev))
    assert hub.scan(str(p))["agents"] == 2      # the two children with no outcome yet


def test_the_page_binds_the_header_tooltip_to_the_group_spend():
    src = HUB_PY.read_text()
    assert "const CAPS=__CAPS__;" in src                                        # the caps reach the page
    assert '.replace("__CAPS__", json.dumps(loop_caps()))' in src               # ... from the graph
    assert 'title="${esc(h.title)}">${h.text}' in src                           # spend in the tooltip, occupancy as the text


def _render_in_the_browser(hub, caps, rows):
    """The header the page really paints: the served function, run by node. node is already a hard dependency of this suite."""
    assert shutil.which("node"), "node is not installed: the page's own groupHeader cannot be run"
    page = hub.PAGE
    js = "\n".join(re.search(rf"^{header}.*$", page, re.M).group(0) for header in (r"const usd=", r"function groupHeader\("))
    script = f"const CAPS={json.dumps(caps)};\n{js}\nconsole.log(JSON.stringify(groupHeader('running',{json.dumps(rows)})))"
    out = subprocess.run(["node", "-"], input=script, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_the_page_renders_the_occupancy_header_with_the_graphs_caps(hub):
    rows = [{"spend": s, "agents": 3} for s in (1.1, 2.2, 3.3)]
    h = _render_in_the_browser(hub, {"max_parallel": 10, "max_children": 20}, rows)
    assert h["text"] == "3/10 runs · 9/20 agents"
    assert h["title"] == "$6.60"


def test_the_page_renders_plain_counts_when_the_graph_is_unreadable(hub):
    rows = [{"spend": 1.0, "agents": 3}] * 3
    h = _render_in_the_browser(hub, None, rows)   # json null: what loop_caps() returns for an unreadable graph
    assert h["text"] == "3 · 9"
    assert h["title"] == "$3.00"
