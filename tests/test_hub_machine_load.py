"""Hub machine load (#1776): the left pane's footer strip reads CPU, RAM and GPU, and the card of the run that owns
the load says so. These pin the parts that carry a rule — the cell formatting, the `?` fallback when a source cannot
answer, the two-sample warn rule, the attribution by worktree path and the marker that clears with the load."""
import importlib.util
import json
import os
import re
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
    spec = importlib.util.spec_from_file_location("hub_machine_load", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------- the strip's cells

def test_a_cell_is_a_rounded_percentage(hub):
    assert hub.pct_cell(62.4) == "62%"
    assert hub.pct_cell(0) == "0%"          # an idle machine is a reading, not a missing one
    assert hub.pct_cell(100) == "100%"


def test_a_cell_is_a_question_mark_when_the_source_did_not_answer(hub):
    assert hub.pct_cell(None) == "?"        # never a stale or invented number
    assert hub.pct_cell("62%") == "?"
    assert hub.pct_cell(float("nan")) == "?"
    assert hub.pct_cell(-1) == "?"


def test_the_line_reads_cpu_ram_gpu_in_that_order(hub):
    assert hub.load_line(62.4, 71.2, 27.0) == "CPU 62% · RAM 71% · GPU 27%"
    assert hub.load_line(62.4, None, 27.0) == "CPU 62% · RAM ? · GPU 27%"


def test_the_cadence_and_the_thresholds_are_named_constants(hub):
    assert 0 < hub.LOAD_SAMPLE_SECS <= 5          # the page may not refresh slower than every 5 s
    assert isinstance(hub.LOAD_WARN_PCT, (int, float)) and 0 < hub.LOAD_WARN_PCT <= 100
    assert hub.LOAD_WARN_SAMPLES > 1              # above the threshold for MORE than one sample
    assert hub.LOAD_MIN_SHARE_PCT >= 0


# ---------------------------------------------------------------- the reading itself

def test_a_source_that_raises_is_unavailable_not_a_traceback(hub):
    def boom():
        raise RuntimeError("no such device")
    got = hub.sample_load({"cpu": lambda: 62.4, "ram": boom, "gpu": lambda: None})
    assert got == {"cpu": 62.4, "ram": None, "gpu": None}
    assert hub.load_line(**got) == "CPU 62% · RAM ? · GPU ?"


def test_a_source_answering_with_a_non_percentage_is_unavailable(hub):
    got = hub.sample_load({"cpu": lambda: "n/a", "ram": lambda: -1, "gpu": lambda: float("nan")})
    assert got == {"cpu": None, "ram": None, "gpu": None}


def test_the_gpu_reading_comes_from_the_accelerators_own_counters(hub):
    text = '  "PerformanceStatistics" = {"Tiler Utilization %"=29,"Device Utilization %"=30,"Renderer Utilization %"=29}'
    assert hub.parse_gpu(text) == 30.0
    assert hub.parse_gpu("") is None                    # no accelerator in the machine
    assert hub.parse_gpu('"PerformanceStatistics" = {}\n') is None
    assert hub.parse_gpu('"Device Utilization %"=abc') is None


# ---------------------------------------------------------------- the warn rule

def test_one_hot_sample_is_not_load(hub):
    assert hub.sustained([95.0]) is False


def test_two_samples_in_a_row_above_the_threshold_are_load(hub):
    assert hub.sustained([95.0, 93.0]) is True


def test_the_run_of_samples_has_to_be_unbroken(hub):
    assert hub.sustained([95.0, 10.0, 95.0]) is False
    assert hub.sustained([10.0, 95.0, 95.0]) is True
    assert hub.sustained([None, 95.0, 95.0]) is False   # an unreadable sample breaks the run: never a guess


def test_a_machine_under_the_threshold_is_never_warned(hub):
    assert hub.sustained([hub.LOAD_WARN_PCT - 1, hub.LOAD_WARN_PCT - 1]) is False


# ---------------------------------------------------------------- who owns the CPU

def test_a_process_tree_is_credited_to_the_worktree_its_argv_names(hub):
    prev = {10: (1, 100.0, "/bin/zsh"), 11: (10, 50.0, "claude --project /u/wt-as-1776"), 12: (11, 20.0, "dotnet test")}
    cur = {10: (1, 100.0, "/bin/zsh"), 11: (10, 52.0, "claude --project /u/wt-as-1776"), 12: (11, 26.0, "dotnet test")}
    assert hub.attribute(prev, cur, {"run-a": ["/u/wt-as-1776"]}) == {"run-a": 8.0}   # 2 s of agent + the 6 s of the test it spawned


def test_runs_are_told_apart_by_worktree_path_not_by_process_name(hub):
    # every child of every run is the same `claude` and every test host the same `dotnet`: only the path separates them
    prev = {1: (0, 0.0, "/sbin/launchd"), 2: (1, 0.0, "claude --project /u/wt-as-1776"), 3: (1, 0.0, "claude --project /u/wt-as-1775"),
            4: (2, 0.0, "dotnet test"), 5: (3, 0.0, "dotnet test")}
    cur = {1: (0, 0.0, "/sbin/launchd"), 2: (1, 3.0, "claude --project /u/wt-as-1776"), 3: (1, 5.0, "claude --project /u/wt-as-1775"),
           4: (2, 4.0, "dotnet test"), 5: (3, 1.0, "dotnet test")}
    assert hub.attribute(prev, cur, {"a": ["/u/wt-as-1776"], "b": ["/u/wt-as-1775"]}) == {"a": 7.0, "b": 6.0}


def test_a_run_that_owns_nothing_is_absent(hub):
    prev = {1: (0, 0.0, "/sbin/launchd"), 9: (1, 0.0, "node /srv/other")}
    cur = {1: (0, 0.0, "/sbin/launchd"), 9: (1, 9.0, "node /srv/other")}
    assert hub.attribute(prev, cur, {"a": ["/u/wt-as-1776"]}) == {}


def test_the_run_folder_owns_the_child_worktrees_inside_it(hub):
    owners = hub.owners_of([{"id": "run-1", "dir": "/r/runs/run-20261011-132053-54d24b", "wts": ["/u/wt-as-1776"]}])
    assert owners == {"run-1": ["/r/runs/run-20261011-132053-54d24b", "/u/wt-as-1776"]}
    prev = {1: (0, 0.0, "python -m squad.dashboard_server /r/runs/run-20261011-132053-54d24b"),
            2: (1, 0.0, "zsh /r/runs/run-20261011-132053-54d24b/ws/worktrees_child-route.router.7")}
    cur = {1: (0, 0.5, "python -m squad.dashboard_server /r/runs/run-20261011-132053-54d24b"),
           2: (1, 4.5, "zsh /r/runs/run-20261011-132053-54d24b/ws/worktrees_child-route.router.7")}
    assert hub.attribute(prev, cur, owners) == {"run-1": 4.0}


def test_a_share_is_a_percentage_of_the_whole_machine(hub):
    assert hub.share_pct(4.0, 5.0, 8) == 10.0    # four CPU-seconds in a five-second window on eight cores
    assert hub.share_pct(0.0, 5.0, 8) == 0.0
    assert hub.share_pct(40.0, 5.0, 8) == 100.0


def test_the_record_names_the_worktrees_a_run_works_in(hub):
    p = Path(tempfile.mkdtemp()) / "record.jsonl"
    ev = [{"event": "run_start", "ts": "2026-10-11T10:00:00Z"},
          {"event": "outcome", "node": "route.router.7", "outcome": "pass", "ts": "2026-10-11T10:01:00Z",
           "workspace": {"name": "child", "path": "/Users/bihaiko/wt-as-1776", "head": "abc123", "state": "clean"}},
          {"event": "outcome", "node": "verify", "outcome": "pass", "ts": "2026-10-11T10:02:00Z",
           "workspace": {"name": ".", "path": "/r/checkout", "head": "def456", "state": "clean"}}]
    p.write_text("".join(json.dumps(e) + "\n" for e in ev))
    assert hub.scan(str(p))["wts"] == ["/Users/bihaiko/wt-as-1776"]   # the target itself is not a worktree of its own


# ---------------------------------------------------------------- the card marker

def test_only_the_run_owning_the_cpu_carries_the_marker(hub):
    rows = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
    marked = hub.attach_load([dict(r) for r in rows], {"a": 41.0, "b": 0.0}, warn=True)
    assert marked[0]["cpu"] == 41
    assert "cpu" not in marked[1]                  # it owns nothing of this load
    assert "cpu" not in marked[2]                  # it owns no part of the machine at all


def test_the_marker_clears_when_the_load_does(hub):
    rows = [{"id": "a", "cpu": 41}]
    assert "cpu" not in hub.attach_load(rows, {"a": 41.0}, warn=False)[0]


def test_a_dab_of_cpu_on_a_busy_machine_blames_nobody(hub):
    assert "cpu" not in hub.attach_load([{"id": "a"}], {"a": hub.LOAD_MIN_SHARE_PCT / 2}, warn=True)[0]


# ---------------------------------------------------------------- what the page is served

def test_the_page_reads_the_cached_reading_and_never_samples_it(hub, monkeypatch):
    def no_sample(*a, **k):
        raise AssertionError("serving the page must not sample the machine")
    monkeypatch.setattr(hub, "sample_load", no_sample)
    hub.LOAD_STATE.update({"cpu": [12.0, 90.0, 91.0], "ram": 71.2, "gpu": None, "asof": 1.0, "runs": {"a": 41.0}})
    p = hub.load_payload()
    assert p["line"] == "CPU 91% · RAM 71% · GPU ?"
    assert p["cpu"] == 91 and p["warn"] is True and p["runs"] == {"a": 41.0}


def test_an_empty_history_reads_as_unavailable(hub):
    hub.LOAD_STATE.update({"cpu": [], "ram": None, "gpu": None, "asof": 0.0, "runs": {}})
    p = hub.load_payload()
    assert p["line"] == "CPU ? · RAM ? · GPU ?" and p["warn"] is False


def test_the_page_carries_a_footer_strip_and_polls_the_reading():
    src = HUB_PY.read_text()
    assert 'elif self.path.startswith("/api/load")' in src            # served, not sampled, per view
    assert "<div id=foot>" in src and "id=ldline" in src              # the strip and its line
    assert "const LOAD_MS=__LOADMS__;" in src
    assert '.replace("__LOADMS__", str(int(LOAD_SAMPLE_SECS * 1000)))' in src
    assert "setInterval(loadPoll,LOAD_MS)" in src


def _render_load_mark(hub, run):
    """The marker the page really paints, run by node (already a dependency of this suite)."""
    js = re.search(r"^function loadMark\(.*$", hub.PAGE, re.M).group(0)
    script = f"{js}\nconsole.log(loadMark({json.dumps(run)}))"
    out = subprocess.run(["node", "-"], input=script, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


def test_the_page_paints_the_marker_only_for_a_run_that_owns_the_load(hub):
    assert "cpu 41%" in _render_load_mark(hub, {"cpu": 41})
    assert _render_load_mark(hub, {"id": "b"}) == ""
