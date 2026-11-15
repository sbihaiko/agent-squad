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
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"
SRC = HUB_PY.read_text()
T = 1_800_000_000.0   # one fixture call time, in epoch seconds


@pytest.fixture(scope="module")
def OU():
    import squad.operator_usage as mod
    return mod


@pytest.fixture(scope="module")
def hub():
    for k in ("SG_REPO", "SG_OWNER", "SG_PROJECT"):
        os.environ.setdefault(k, "x")
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    os.environ["SG_STATE"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_operator_usage", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


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


def test_a_rewritten_transcript_folds_like_a_fresh_read(OU, tmp_path):
    folder = tmp_path / "p"
    p = _write(folder, "a.jsonl", _line("m1", "m", 1, 1, 0))
    c = OU.Cache()
    c.read(folder)
    p.write_text(_line("m9", "m", 9, 9, 0))   # the same path now holds another line, as a resumed session's rewrite does
    assert [r["id"] for r in c.read(folder)] == ["m9"]


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


def test_the_token_guard_and_the_caps_read_the_loop_sources_only(hub):
    """The operator source changes no stop and no pause: it is read in one place, the Spend panel's stats."""
    for f in ("contrib/squad-goal/scripts/token-guard.sh", "contrib/squad-goal/scripts/tokens.py"):
        t = (ROOT / f).read_text()
        assert "operator_usage" not in t and "CLAUDE_PROJECTS" not in t
    assert SRC.count("operator_calls(") == 2   # its definition and one call, in the Spend panel's stats; no guard reads it


def test_the_pie_labels_the_operator_slice_and_the_chart_draws_its_segment(hub):
    pie = SRC[SRC.index("function pie(ms){"):SRC.index("function hlPie(")]
    bars = SRC[SRC.index("function barChart("):SRC.index("function cmp(")]
    assert '"operator · "' in pie or "operator · " in pie          # the slice name tells the consumer
    assert "consumer" in pie
    assert "op" in bars and "operator" in bars and "loop" in bars     # both segments are drawn and named
