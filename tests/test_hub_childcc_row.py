"""GitHub issue #598: the hub Runs tab (Live, #1770) shows the childcc pass and the children it reviews.

The pass writes `auto/childcc-now.json`: its pid and start, and one entry per child it holds (run, child, issue, step, and
since when it waits). The hub reads that file, never the pass: a live pass is one row with its pid and age, one line under
it per child, and the deferred count with the oldest wait. With no live pass the row keeps the deferred count; with nothing
waiting there is no row.

GitHub issue #689: the row names the run the pass holds now, and a click on it selects that run like a run row. The list
carries the held run even when no dashboard server serves it (it was orphaned from active-runs); the hub starts one when the
run is opened. The row itself is never a pane.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib" / "squad-goal" / "scripts" / "hub.py"
NOW = 1_800_000_000.0


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_childcc_row", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def state(hub, monkeypatch, tmp_path):
    (tmp_path / "auto").mkdir()
    monkeypatch.setattr(hub, "STATE", tmp_path)
    return tmp_path


def publish(state, pid, children, t0=NOW - 300):
    (state / "auto" / "childcc-now.json").write_text(json.dumps({"pid": pid, "t0": t0, "children": children}))


def child(run, name, issue, step, since=NOW - 60, rk=None):
    return {"run": run, "child": name, "issue": issue, "step": step, "since": since, "rk": rk}


def test_a_live_pass_is_one_row_with_pid_age_and_a_line_per_child(hub, state):
    (state / "review-gui_598.model").write_text("gpt-6.1-sol\n")
    publish(state, os.getpid(), [child("run-a", "c1", 598, "review", rk="gui_598"),
                                 child("run-a", "c2", 599, "enter"),
                                 child("run-b", "c1", 600, "deferred", since=NOW - 600),
                                 child("run-b", "c2", 601, "deferred", since=NOW - 120)])
    row = hub.childcc_row(NOW)
    assert row["kind"] == "childcc" and row["live"] is True and row["rank"] == 0
    assert row["pid"] == os.getpid() and row["age"] == 300
    assert row["lines"] == ["run-a c1 #598 · in review with gpt-6.1-sol",
                            "run-a c2 #599 · entering wave",
                            "run-b c1 #600 · deferred",
                            "run-b c2 #601 · deferred"]
    assert row["deferred"] == 2 and row["oldest_wait"] == 600
    assert row["label"] == f"childcc pass · pid {os.getpid()}"
    assert row["sub"] == "CHILDCC DEFERRED 2 · oldest wait 10m"


def test_a_review_with_no_model_yet_reads_in_review(hub, state):
    publish(state, os.getpid(), [child("run-a", "c1", 598, "review", rk="gui_598")])
    assert hub.childcc_row(NOW)["lines"] == ["run-a c1 #598 · in review"]


def test_no_live_pass_shows_the_deferred_count_and_the_oldest_wait(hub, state):
    publish(state, None, [child("run-b", "c1", 600, "deferred", since=NOW - 900),
                          child("run-b", "c2", 601, "deferred", since=NOW - 60)])
    row = hub.childcc_row(NOW)
    assert row["live"] is False and row["pid"] is None and row["state"] == "waiting"
    assert row["label"] == "CHILDCC DEFERRED 2"
    assert row["sub"] == "CHILDCC DEFERRED 2 · oldest wait 15m"
    assert row["deferred"] == 2 and row["oldest_wait"] == 900


def test_a_dead_pass_is_no_live_pass_and_its_children_still_wait(hub, state):
    publish(state, 2 ** 22 + 7, [child("run-a", "c1", 598, "review", since=NOW - 120)])
    row = hub.childcc_row(NOW)
    assert row["live"] is False and row["deferred"] == 1 and row["oldest_wait"] == 120


def test_nothing_waiting_shows_no_row(hub, state):
    assert hub.childcc_row(NOW) is None   # no file
    publish(state, None, [])
    assert hub.childcc_row(NOW) is None
    publish(state, os.getpid(), [])
    assert hub.childcc_row(NOW) is not None   # a live pass with nothing held is still a live pass
    assert hub.childcc_row(NOW)["lines"] == []


def test_the_row_is_not_a_run(hub, state):
    publish(state, None, [child("run-b", "c1", 600, "deferred")])
    assert hub.working([hub.childcc_row(NOW)]) == []


# --- #689: the row opens the run the pass holds, and the list carries that run though no dashboard server serves it ---


def test_the_row_names_the_run_the_pass_holds_now(hub, state):
    publish(state, os.getpid(), [child("run-b", "c1", 600, "deferred"), child("run-a", "c1", 544, "review")])
    assert hub.childcc_row(NOW)["held"] == "run-a"
    publish(state, None, [child("run-b", "c1", 600, "deferred")])
    assert hub.childcc_row(NOW)["held"] == "run-b"


@pytest.fixture
def held(hub, state, monkeypatch, tmp_path):
    """A held run folder under a child worktree's runs/, as the loop leaves it, and no dashboard server on this machine."""
    runs = tmp_path / "co" / "worktrees" / "child" / "gui-544" / "runs"
    run = runs / "run-20261020-192307-95bcfe"
    run.mkdir(parents=True)
    (run / "record.jsonl").write_text(json.dumps({"event": "run_start", "t": NOW, "run_id": run.name}) + "\n")
    monkeypatch.setattr(hub, "project_dirs", lambda: [runs])
    monkeypatch.setattr(hub, "sh", lambda *a, **k: "")
    publish(state, None, [child(run.name, "c1", 544, "review")])
    return run


def test_run_list_carries_the_held_run_that_no_dashboard_lists(hub, held):
    rows = {r["id"]: r for r in hub.runs()}
    assert held.name in rows, "the run the pass holds is not in the list"
    r = rows[held.name]
    assert r["kind"] != "childcc" and r["url"] == "/held/" + held.name
    assert rows["childcc"]["held"] == held.name


def test_a_held_run_opens_through_a_dashboard_server_started_for_its_folder(hub, held):
    seen = []
    assert hub.open_held(held.name, start=lambda f: seen.append(f) or 8123) == 8123
    assert seen == [str(held)]


def test_a_run_the_pass_does_not_hold_never_opens(hub, held):
    seen = []
    assert hub.open_held("run-20261020-000000-other", start=lambda f: seen.append(f) or 1) is None
    assert hub.open_held("../etc", start=lambda f: seen.append(f) or 1) is None
    assert seen == []


NODE = shutil.which("node")


def _draw_main(hub):
    src = hub.PAGE
    return src[src.index("function drawMain(") : src.index("function refresh(")]   # the Runs tab's one list and its row forms (#1770)


def _page(hub, all_runs):
    js = """
const HTML={main:""};
const $=s=>({set innerHTML(v){HTML[s.slice(1)]=v},get innerHTML(){return HTML[s.slice(1)]}});
const esc=s=>String(s),iq=s=>String(s),age=s=>s+"s",usd=v=>"$"+v,name=r=>r.id,labelTip=r=>"",loadMark=r=>"",sub=r=>"";
let TAB="runs",ENDED=[],GROUPS=[],slots=[],pins=new Set(),allRuns=__RUNS__,runs=allRuns.filter(r=>r.kind!=="childcc");
__DRAW__
drawMain();
const ids=[...HTML.main.matchAll(/data-rid="([^"]+)"/g)].map(m=>m[1]);
console.log(JSON.stringify({ids,cc:/<div class="r cc[^>]*>/.exec(HTML.main)[0]}));
""".replace("__RUNS__", json.dumps(all_runs)).replace("__DRAW__", _draw_main(hub))
    assert NODE, "node is not installed: the hub page's own drawMain is run here"
    p = subprocess.run([NODE, "-"], input=js, capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


HELD = {"id": "run-20261020-192307-95bcfe", "kind": "code", "rank": 0, "state": "running", "age": 5, "spend": 1, "dur": 3, "port": "", "url": "/held/run-20261020-192307-95bcfe"}


def _cc(held):
    return {"id": "childcc", "kind": "childcc", "rank": 0, "state": "running", "badge": "childcc", "label": "childcc pass", "live": True,
            "age": 9, "lines": ["run-20261020-192307-95bcfe c1 #544 · in review"], "held": held}


def test_a_click_on_the_childcc_row_selects_the_run_it_holds(hub):
    """The Runs tab opens a row through its `data-rid` (#984, #1770): the childcc row carries the run it holds."""
    out = _page(hub, [_cc(HELD["id"]), HELD])
    assert f'data-rid="{HELD["id"]}"' in out["cc"], "the childcc row carries no id of the run it holds"
    src = hub.PAGE
    assert '$("#main").addEventListener("click",e=>{const r=e.target.closest(".r[data-rid]")' in src and "openRun(id)" in src


def test_the_childcc_row_is_never_a_pane(hub):
    out = _page(hub, [_cc(None), HELD])
    assert "childcc" not in out["ids"] and "data-rid" not in out["cc"]
    out = _page(hub, [_cc("run-not-listed")])
    assert "data-rid" not in out["cc"], "a held run the list does not carry cannot be opened"
