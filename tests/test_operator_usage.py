"""Issue #1756: the hub Spend panel counts the operator session and its agents.

The Spend panel sums the run records and the token usage ledger of the loop scripts. The operator session (the Claude
Code session that drives the loop by hand) and the agents and workflows it starts write to neither, so the panel
understates a day of hand work. The hub reads a third source, the session transcripts of this checkout, folded by one
pure function in `squad/operator_usage.py`, and shows it as its own consumer, `operator`.

No test reads a real transcript: every case is built from fixture lines in a temporary folder.
"""
import datetime as dt
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from tests.test_hub_cost_usage_panel import _chart

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"
SRC = HUB_PY.read_text()
TOKENS_PY = ROOT / "contrib/squad-goal/scripts/tokens.py"
T = 1_800_000_000.0   # one fixture call time, in epoch seconds


@pytest.fixture(scope="module")
def OU():
    import squad.operator_usage as mod
    return mod


@pytest.fixture(scope="module")
def hub():
    """Bind a hub module to a throwaway environment. A module-scoped fixture outlives this file's tests, so every name
    it sets is restored at teardown: a leaked SG_REPO reaches a later file's hub and breaks its board pass (#2457)."""
    names = ("SG_REPO", "SG_OWNER", "SG_PROJECT", "SG_HUB_DIR", "SG_STATE")
    saved = {name: os.environ.get(name) for name in names}
    for k in ("SG_REPO", "SG_OWNER", "SG_PROJECT"):
        os.environ.setdefault(k, "x")
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    os.environ["SG_STATE"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_operator_usage", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    try:
        yield mod
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def test_the_hub_fixture_restores_the_environment_it_sets(monkeypatch):
    """#2457: the fixture runs at module scope, so a name it leaves behind reaches every later test in the process. A
    leaked SG_REPO with no owner made a later board pass drop the items it had read and carry no test cases."""
    names = ("SG_REPO", "SG_OWNER", "SG_PROJECT", "SG_HUB_DIR", "SG_STATE")
    for name in names:
        monkeypatch.delenv(name, raising=False)
    gen = hub.__wrapped__()
    has_teardown = hasattr(gen, "__next__")
    try:
        if has_teardown:
            next(gen)                          # the fixture's setup binds the throwaway environment
        assert os.environ["SG_REPO"] == "x"    # a fixture that never yields leaves this behind for good
    finally:
        if has_teardown:
            for _ in gen:                      # the fixture's teardown is the behavior under test
                pass
    leaked = [name for name in names if name in os.environ]
    assert leaked == [], f"{', '.join(leaked)} leaked into the process environment"


def _line(mid, model, inp, out, cr, cc=0, ts=T, text=None):
    """One assistant transcript line. The content carries a distinctive text, so a test can prove no text leaves the fold."""
    msg = {"id": mid, "model": model,
           "usage": {"input_tokens": inp, "output_tokens": out, "cache_read_input_tokens": cr, "cache_creation_input_tokens": cc},
           "content": [{"type": "text", "text": text if text is not None else "the private prompt of " + str(mid)}]}
    return json.dumps({"type": "assistant", "timestamp": dt.datetime.fromtimestamp(ts, dt.UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                       "message": msg}) + "\n"


def _setup(hub, tmp_path, monkeypatch):
    """A projects dir and this checkout's folder in it, with the hub's caches empty, so no test reads another's state."""
    projects = tmp_path / "projects"
    checkout = tmp_path / "co"
    (checkout).mkdir()
    monkeypatch.setattr(hub, "CLAUDE_PROJECTS", projects, raising=False)
    monkeypatch.setattr(hub, "CHECKOUT", checkout, raising=False)
    monkeypatch.setattr(hub, "OPERATOR", None, raising=False)
    monkeypatch.setattr(hub, "USAGE_FILES", {}, raising=False)
    monkeypatch.setattr(hub, "ledger_calls", lambda: [])
    monkeypatch.setattr(hub, "all_runs", lambda: [])
    folder = projects / re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(str(checkout)))
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _write(folder, name, *lines):
    p = folder / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(lines))
    return p


# --- the pure fold ---------------------------------------------------------------------------------------------------

def test_the_fold_reads_model_counts_and_call_time(OU):
    rows = OU.usage_rows([_line("m1", "claude-opus-5-5", 120, 45, 900, 30)])
    assert rows == [{"id": "m1", "model": "claude-opus-5-5", "in": 150, "out": 45, "cache": 900, "t0": T}]


def test_a_synthetic_line_and_a_line_without_usage_fold_to_nothing(OU):
    synthetic = json.dumps({"type": "assistant", "message": {"id": "s", "model": "<synthetic>", "usage": {"input_tokens": 1, "output_tokens": 1}}})
    user = json.dumps({"type": "user", "message": {"id": "u", "content": "hi"}})
    assert OU.usage_rows([synthetic, user, "not json", _line("m1", "m", 1, 1, 0)]) == [
        {"id": "m1", "model": "m", "in": 1, "out": 1, "cache": 0, "t0": T}]


def test_a_line_read_twice_counts_once(OU):
    line = _line("m1", "claude-opus-5-5", 10, 2, 0)
    assert [r["id"] for r in OU.usage_rows([line, line])] == ["m1"]


def test_no_transcript_text_leaves_the_fold(OU):
    rows = OU.usage_rows([_line("m1", "claude-opus-5-5", 10, 2, 0, text="SECRET-PROMPT-TEXT")])
    assert set(rows[0]) == {"id", "model", "in", "out", "cache", "t0"}
    assert "SECRET-PROMPT-TEXT" not in json.dumps(rows)


def test_a_ledger_call_and_a_run_outcome_hide_their_transcript_call(OU):
    rows = [{"id": "a", "model": "claude-opus-5-5", "in": 100, "out": 20, "cache": 900, "t0": T},
            {"id": "b", "model": "claude-opus-5-5", "in": 40, "out": 5, "cache": 0, "t0": T + 60},
            {"id": "c", "model": "claude-opus-5-5", "in": 7, "out": 7, "cache": 0, "t0": T + 120}]
    ledger = [{"t0": T - 30, "model": "claude-opus-5-5", "in": 1000, "out": 20}]      # a ledger line counts cache reads in `in`
    outcome = [{"t0": T + 60, "model": "claude-opus-5-5", "tokens_in": 40, "tokens_out": 5}]   # a record that carries no cache_read
    assert [r["id"] for r in OU.calls(rows, ledger + outcome, canon=lambda m: m)] == ["c"]
    assert len(OU.calls(rows, [], canon=lambda m: m)) == 3


def test_one_loop_call_hides_at_most_one_transcript_call(OU):
    """#1756: the match is a heuristic, so a loop call may swallow only one row. Two distinct operator calls that share a
    model and its counts both stay; the loop's own twin is the first one."""
    rows = [{"id": "op1", "model": "m", "in": 10, "out": 5, "cache": 0, "t0": T},
            {"id": "op2", "model": "m", "in": 10, "out": 5, "cache": 0, "t0": T + 60}]
    loop = [{"t0": T, "model": "m", "tokens_in": 10, "tokens_out": 5}]
    assert [r["id"] for r in OU.calls(rows, loop)] == ["op2"]


def test_a_loop_call_is_matched_against_its_own_span_not_one_point(OU):
    """#1756: a long call's transcript message lands anywhere in the call, so the match reads the span (`t0` to `t1`)."""
    rows = [{"id": "a", "model": "m", "in": 10, "out": 5, "cache": 0, "t0": T + 900},
            {"id": "b", "model": "m", "in": 10, "out": 5, "cache": 0, "t0": T + 1500}]
    loop = [{"t0": T, "t1": T + 1200, "model": "m", "tokens_in": 10, "tokens_out": 5}]
    assert [r["id"] for r in OU.calls(rows, loop)] == ["b"]


def test_the_spend_read_answers_a_window_and_never_walks_the_past(OU):
    """#1897: the panel reads a window, so the matching may not scale with the history. A row older than the window is
    not a candidate, a loop call that ended before it is not a mark, and the count of both is what the cost is made of."""
    since = T
    rows = [{"id": f"old{i}", "model": "m", "in": 10, "out": 5, "cache": 0, "t0": T - 86_400 - i} for i in range(20_000)]
    rows += [{"id": "now1", "model": "m", "in": 10, "out": 5, "cache": 0, "t0": T + 10},
             {"id": "now2", "model": "m", "in": 99, "out": 9, "cache": 0, "t0": T + 20}]
    loop = [{"t0": T - 86_400 - i, "model": "m", "tokens_in": 10, "tokens_out": 5} for i in range(2_000)]
    loop += [{"t0": T, "model": "m", "tokens_in": 10, "tokens_out": 5}]
    t = time.perf_counter()
    got = OU.calls(rows, loop, since=since)
    spent = time.perf_counter() - t
    assert [r["id"] for r in got] == ["now2"], "the old rows are outside the window and the recent twin is hidden"
    assert spent < 1.0, f"the window bounds the match: {spent:.2f}s for a 20 000-row history"


def test_the_resolver_runs_once_per_row_and_once_per_mark(OU):
    """#1897: `canon` is a resolver, not a per-comparison check. Two rows against three marks resolve five times."""
    rows = [{"id": "a", "model": "m", "in": 10, "out": 5, "cache": 0, "t0": T},
            {"id": "b", "model": "m", "in": 10, "out": 5, "cache": 0, "t0": T + 60}]
    loop = [{"t0": T, "model": "m", "tokens_in": 10, "tokens_out": 5},
            {"t0": T, "model": "other", "tokens_in": 1, "tokens_out": 1},
            {"t0": T, "model": "third", "tokens_in": 2, "tokens_out": 2}]
    seen = []
    def canon(m):
        seen.append(m)
        return m
    OU.calls(rows, loop, canon=canon)
    assert len(seen) == 5, f"two rows and three marks resolve five times, not {len(seen)}"


def test_a_loop_call_under_another_spelling_and_a_far_in_time_call_hide_nothing(OU):
    rows = [{"id": "a", "model": "claude-opus-5-5[1m]", "in": 100, "out": 20, "cache": 0, "t0": T},
            {"id": "b", "model": "claude-opus-5-5", "in": 100, "out": 20, "cache": 0, "t0": T + 7200}]
    ledger = [{"t0": T, "model": "claude:claude-opus-5-5", "in": 100, "out": 20}]
    def canon(m):
        return "claude-opus-5-5" if m.startswith(("claude-opus-5-5", "claude:claude-opus-5-5")) else m
    assert [r["id"] for r in OU.calls(rows, ledger, canon=canon)] == ["b"]


# --- the bounded read ------------------------------------------------------------------------------------------------

def test_only_a_changed_file_is_parsed_and_the_rows_are_kept(OU, tmp_path):
    folder = tmp_path / "p"
    _write(folder, "a.jsonl", _line("m1", "m", 1, 1, 0))
    _write(folder, "sub/b.jsonl", _line("m2", "m", 2, 2, 0))
    c = OU.Cache()
    first = c.read(folder)
    assert sorted(r["id"] for r in first) == ["m1", "m2"] and c.parsed == 2
    assert c.read(folder) == first and c.parsed == 2   # an unchanged folder parses nothing and keeps its rows
    _write(folder, "a.jsonl", _line("m1", "m", 1, 1, 0), _line("m3", "m", 3, 3, 0))
    assert sorted(r["id"] for r in c.read(folder)) == ["m1", "m2", "m3"] and c.parsed == 3


def test_a_resumed_and_a_compacted_session_add_nothing_twice(OU, tmp_path):
    """A resumed session copies its earlier messages into a new file, and a compaction copies them again; a subagent's
    transcript sits in a subfolder. One message id is one call, whatever file carries it."""
    folder = tmp_path / "p"
    _write(folder, "s1.jsonl", _line("m1", "m", 1, 1, 0), _line("m2", "m", 2, 2, 0))
    _write(folder, "s2.jsonl", _line("m2", "m", 2, 2, 0), _line("m3", "m", 3, 3, 0))
    _write(folder, "sub/agent.jsonl", _line("m3", "m", 3, 3, 0), _line("m4", "m", 4, 4, 0))
    assert [r["id"] for r in OU.Cache().read(folder)] == ["m1", "m2", "m3", "m4"]


def test_an_unchanged_folder_is_not_walked_again(OU, tmp_path):
    """#1756: the read is bounded -- a poll that finds nothing changed walks no directory, and a new folder is seen."""
    folder = tmp_path / "p"
    _write(folder, "a.jsonl", _line("m1", "m", 1, 1, 0))
    c = OU.Cache()
    assert [r["id"] for r in c.read(folder)] == ["m1"] and c.walks == 1
    assert [r["id"] for r in c.read(folder)] == ["m1"] and c.walks == 1 and c.parsed == 1
    _write(folder, "sub/b.jsonl", _line("m2", "m", 2, 2, 0))   # a new folder under the root moves the root's stamp
    assert sorted(r["id"] for r in c.read(folder)) == ["m1", "m2"] and c.walks == 2


def test_a_rewritten_transcript_folds_like_a_fresh_read(OU, tmp_path):
    folder = tmp_path / "p"
    p = _write(folder, "a.jsonl", _line("m1", "m", 1, 1, 0))
    c = OU.Cache()
    c.read(folder)
    p.write_text(_line("m9", "m", 9, 9, 0))   # the same path now holds another line, as a resumed session's rewrite does
    assert [r["id"] for r in c.read(folder)] == ["m9"]


# --- the fold that outlives the process (#1888) ----------------------------------------------------------------------

def test_a_state_path_keeps_the_fold_for_a_new_cache(OU, tmp_path):
    """#1888: a hub restart starts with an empty Cache, so the first read re-parsed every transcript. The fold is saved to
    the state path, and a second Cache on the same folder and path parses nothing and returns the same rows."""
    folder = tmp_path / "p"
    _write(folder, "a.jsonl", _line("m1", "m", 1, 1, 0))
    _write(folder, "sub/b.jsonl", _line("m2", "m", 2, 2, 0))
    state = tmp_path / "state" / "operator-usage.json"
    c = OU.Cache(state)
    first = c.read(folder)
    assert sorted(r["id"] for r in first) == ["m1", "m2"] and c.parsed == 2
    d = OU.Cache(state)
    assert d.read(folder) == first and d.parsed == 0   # the new process reads the fold, not the transcripts


def test_a_changed_transcript_is_parsed_again_from_the_state(OU, tmp_path):
    """#1888: the saved fold keeps each file's stamp, so a changed transcript is read again and an unchanged one is not."""
    folder = tmp_path / "p"
    p = _write(folder, "a.jsonl", _line("m1", "m", 1, 1, 0))
    _write(folder, "b.jsonl", _line("m2", "m", 2, 2, 0))
    state = tmp_path / "operator-usage.json"
    assert OU.Cache(state).read(folder)
    p.write_text(_line("m1", "m", 1, 1, 0) + _line("m9", "m", 9, 9, 0))   # a resumed session rewrites the file
    c = OU.Cache(state)
    assert [r["id"] for r in c.read(folder)] == ["m1", "m9", "m2"] and c.parsed == 1


def test_a_transcript_that_vanished_leaves_the_saved_fold(OU, tmp_path):
    """#1888: the fold drops a file that is gone, and the saved document follows it."""
    folder = tmp_path / "p"
    p = _write(folder, "a.jsonl", _line("m1", "m", 1, 1, 0))
    _write(folder, "b.jsonl", _line("m2", "m", 2, 2, 0))
    state = tmp_path / "operator-usage.json"
    OU.Cache(state).read(folder)
    p.unlink()
    c = OU.Cache(state)
    assert [r["id"] for r in c.read(folder)] == ["m2"] and c.parsed == 0
    assert "a.jsonl" not in json.loads(state.read_text())["files"]


def test_a_corrupt_state_document_starts_empty(OU, tmp_path):
    """#1888: a truncated or half-written document is no fold: the read parses the folder and raises nothing."""
    folder = tmp_path / "p"
    _write(folder, "a.jsonl", _line("m1", "m", 1, 1, 0))
    state = tmp_path / "operator-usage.json"
    state.write_text("{not json")
    c = OU.Cache(state)
    assert [r["id"] for r in c.read(folder)] == ["m1"] and c.parsed == 1


def test_a_state_of_another_root_is_no_fold(OU, tmp_path):
    """#1888: a document names the folder it folded; a Cache on another folder never adopts it."""
    a, b = tmp_path / "a", tmp_path / "b"
    _write(a, "x.jsonl", _line("m1", "m", 1, 1, 0))
    _write(b, "y.jsonl", _line("m2", "m", 2, 2, 0))
    state = tmp_path / "operator-usage.json"
    OU.Cache(state).read(a)
    c = OU.Cache(state)
    assert [r["id"] for r in c.read(b)] == ["m2"] and c.parsed == 1


# --- the hub's third source, as its own consumer ---------------------------------------------------------------------

def test_the_hub_reads_this_checkouts_project_folder(hub, tmp_path, monkeypatch):
    folder = _setup(hub, tmp_path, monkeypatch)
    _write(folder, "s.jsonl", _line("m1", "claude-opus-5-5", 10, 5, 0))
    _write(folder, "sub/agent.jsonl", _line("m2", "claude-haiku-5-5", 3, 1, 0))
    calls = hub.operator_calls()
    assert sorted(c["id"] for c in calls) == ["m1", "m2"]


def test_a_loop_call_is_never_taken_from_a_transcript(hub, tmp_path, monkeypatch):
    folder = _setup(hub, tmp_path, monkeypatch)
    _write(folder, "s.jsonl", _line("m1", "claude-opus-5-5", 10, 5, 0, ts=1_800_000_100))
    monkeypatch.setattr(hub, "all_runs", lambda: [{"t0": 1_800_000_000, "calls": [
        {"t0": 1_800_000_100, "model": "claude-opus-5-5", "tokens_in": 10, "tokens_out": 5}]}])
    assert hub.operator_calls() == []


def test_the_chart_and_the_pie_include_operator_as_its_own_consumer(hub, tmp_path, monkeypatch):
    now = hub.time.time()   # the stats' own clock: its today, month and bucket are the local time of this reading
    folder = _setup(hub, tmp_path, monkeypatch)
    _write(folder, "s.jsonl", _line("m1", "claude-opus-5-5", 100, 50, 900, ts=now - 30))
    run = {"id": "r", "t0": now - 600, "t1": now - 60, "spend": 0.0, "sub": 0.0, "api": 0.0, "dur": 9.0, "done": True,
           "mod": {"claude-sonnet-5-5": {"in": 10, "out": 0, "cache": 0, "spend": 0.0, "calls": 1}}, "calls": []}
    monkeypatch.setattr(hub, "all_runs", lambda: [run])
    s = hub.stats("day")
    bucket = s["buckets"][-1]
    assert bucket["tokens"] == 10 and bucket["op"] == 150     # the loop's tokens stay the loop's; operator rides beside them
    consumers = {(m["model"], m["consumer"]) for m in s["models"]}
    assert ("claude-opus-5-5", "operator") in consumers and ("claude-sonnet-5-5", "loop") in consumers
    assert s["operator"]["tokens_today"] == 150 and s["operator"]["calls"] == 1
    assert s["totals"]["tok_today"] == 10                     # the loop-only totals are unchanged by the operator source


def test_the_token_guard_reads_the_loop_sources_only(tmp_path):
    """#1756: a day of operator calls changes no stop and no pause. The guard's own burn computation answers the same
    verdict, to the byte, whether or not this checkout holds a huge operator transcript."""
    state = tmp_path / "state"
    state.mkdir()
    checkout = tmp_path / "co"
    (checkout / "runs").mkdir(parents=True)
    ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 60))
    (state / "token-usage.tsv").write_text(f"{ts}\treview:x\tclaude-opus-5-5\t2000000\t0\t0\n")

    def verdict(projects):
        p = subprocess.run([sys.executable, str(TOKENS_PY), "verdict", str(state), "--checkout", str(checkout),
                            "--hours", "2"], capture_output=True, text=True, timeout=30,
                           env={**os.environ, "SG_CLAUDE_PROJECTS": str(projects)})
        return p.returncode, p.stdout

    before = verdict(tmp_path / "no-projects")
    folder = tmp_path / "projects" / "-co"
    folder.mkdir(parents=True)
    (folder / "s.jsonl").write_text("".join(_line(f"m{i}", "claude-opus-5-5", 100000, 50000, 0,
                                                  ts=time.time() - 60) for i in range(50)))
    after = verdict(tmp_path / "projects")
    assert before == after, (before, after)
    assert before[0] == 1, before   # the loop's own review call is over the cap and is the top burner


def test_a_late_outcome_still_names_the_calls_own_time(hub, tmp_path, monkeypatch):
    """#1756: the record writes an outcome after the call, so the call's own span (`t0` = ts - elapsed, `t1` = ts) is
    what a transcript call is matched against; a gate that runs between the call and the write changes nothing."""
    folder = _setup(hub, tmp_path, monkeypatch)
    _write(folder, "s.jsonl", _line("m1", "claude-opus-5-5", 10, 5, 0, ts=T + 1))
    rec = tmp_path / "runs" / "run-1" / "record.jsonl"
    rec.parent.mkdir(parents=True)
    rec.write_text(json.dumps({"ts": T, "event": "run_start", "node": "router", "entry": 1}) + "\n"
                   + json.dumps({"ts": T + 600, "event": "outcome", "node": "router", "entry": 1, "outcome": "pass",
                                 "model": "claude-opus-5-5", "spend": 0.0, "tokens_in": 10, "tokens_out": 5,
                                 "elapsed": 600.0}) + "\n")
    r = hub.scan(rec)
    assert (r["calls"][0]["t0"], r["calls"][0]["t1"]) == (T, T + 600)
    monkeypatch.setattr(hub, "all_runs", lambda: [r])
    assert hub.operator_calls() == []


def test_the_loop_match_set_follows_the_panels_billing_filter(hub, tmp_path, monkeypatch):
    """#1756: a loop call the current src filter drops must not hide its transcript twin, or the operator side of the
    panel understates the day."""
    now = hub.time.time()
    folder = _setup(hub, tmp_path, monkeypatch)
    ts = now - 120
    _write(folder, "s.jsonl", _line("m1", "claude-opus-5-5", 10, 5, 0, ts=ts))
    run = {"id": "r", "t0": now - 600, "t1": now - 60, "spend": 0.0, "sub": 0.0, "api": 0.0, "dur": 9.0, "done": True,
           "mod": {}, "calls": [{"t0": ts, "model": "claude-opus-5-5", "tokens_in": 10, "tokens_out": 5}]}
    monkeypatch.setattr(hub, "all_runs", lambda: [run])
    assert hub.stats("day")["operator"]["calls"] == 0          # the record already holds this call: never twice
    assert hub.stats("day", src="api")["operator"]["calls"] == 1   # filtered out of the panel, so its twin is operator


def test_the_operator_summary_prices_its_calls(hub, tmp_path, monkeypatch):
    """#1756: the operator tokens carry a list price, so a token count beside a loop-only figure never reads as free."""
    now = hub.time.time()
    folder = _setup(hub, tmp_path, monkeypatch)
    _write(folder, "s.jsonl", _line("m1", "claude-opus-5-5", 100, 50, 900, ts=now - 30))
    s = hub.stats("day")
    assert s["operator"]["cost_today"] > 0 and s["operator"]["cost_month"] > 0
    assert s["operator"]["unpriced"] == 0


def test_an_operator_only_bucket_keeps_the_loops_zero():
    """#1756: a bucket the loop did not work still draws the loop's own zero under the operator segment."""
    out = _chart("""
const rows=[{t:'2026-10-09',tokens:0,op:500,runs:0},{t:'2026-10-10',tokens:null,op:0,runs:0}];
const c=barChart(rows);
const out={bar:c.bars[0],html:bars(rows)};
""")
    assert out["bar"]["kind"] == "bar" and out["bar"]["zeroLoop"], out
    assert "<rect class=zero" in out["html"], out["html"]
    assert 'fill="var(--chart-series-2)"' in out["html"], out["html"]


def test_the_legend_names_operator_only_when_a_bucket_draws_it():
    """#1756: the legend never claims a series the chart does not draw."""
    out = _chart("""
const out={plain:bars([{t:'2026-10-09',tokens:500,runs:1}]),
  absent:bars([{t:'2026-10-09',tokens:null,runs:0}]),
  withop:bars([{t:'2026-10-09',tokens:500,op:100,runs:1}])};
""")
    assert "operator" not in out["plain"] and "operator" not in out["absent"]
    assert "operator" in out["withop"]


def test_the_pie_labels_the_operator_slice_and_the_chart_draws_its_segment(hub):
    pie = SRC[SRC.index("function pie(ms){"):SRC.index("function hlPie(")]
    bars = SRC[SRC.index("function barChart("):SRC.index("function cmp(")]
    assert '"operator · "' in pie or "operator · " in pie          # the slice name tells the consumer
    assert "consumer" in pie
    assert "op" in bars and "operator" in bars and "loop" in bars     # both segments are drawn and named
