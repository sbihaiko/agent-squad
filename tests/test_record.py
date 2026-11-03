"""Guards for the run record and its atomic write."""
import json
import signal
import subprocess
import sys
import textwrap
import time

import pytest

from squad import record
from tests.helpers import DESIGN

START = dict(run_id="r1", graph_name="g", graph_hash="h", engine_options={}, root="main", plan=["main"])
OUTCOME = dict(node="a", entry=1, outcome="pass", reasons=[], artifact="a.json", spend=0.1, turns=3, cap=1.0, elapsed=2.0)


def test_append_writes_one_event_with_its_record_position(tmp_path):
    r = record.Record(tmp_path / "run" / "record.jsonl")
    assert r.append("run_start", **START)["pos"] == 1
    assert r.append("outcome", **OUTCOME)["pos"] == 2
    assert [e["pos"] for e in record.read(r.path)] == [1, 2]


def test_every_event_kind_keeps_its_contract_fields(tmp_path):
    r = record.Record(tmp_path / "r.jsonl")
    r.append("run_start", **START)
    out = r.append("outcome", **OUTCOME)
    for f in ("node", "entry", "outcome", "reasons", "artifact", "spend", "turns", "cap", "elapsed", "pos"):
        assert f in out
    assert set(record.REQUIRED) >= {"run_start", "outcome", "proposed", "validated", "applied", "rejected",
                                    "spawn_requested", "spawn_accepted", "spawn_rejected", "resume", "verdict"}


def test_a_progress_event_carries_where_its_call_is_and_refuses_without_it(tmp_path):
    """A call in flight records where it has got to. The reader refuses one that names no turn."""
    r = record.Record(tmp_path / "r.jsonl")
    r.append("run_start", **START)
    ev = {"node": "actor", "entry": 1, "item": None, "session": "s1", "messages": 4, "context_tokens": 61209, "tool": "Bash"}
    assert r.append("progress", **ev)["pos"] == 2
    assert "progress" in record.REQUIRED and set(record.REQUIRED["progress"]) == set(ev)
    with pytest.raises(record.RecordError, match="lacks messages"):
        r.append("progress", **{k: v for k, v in ev.items() if k != "messages"})


def test_the_reader_of_a_call_chain_answers_the_ids_and_hash_the_call_recorded():
    """SPEC 1035: a call_start states the chain as it was at the call, and a reader takes that value as written."""
    ev = {"event": "call_start", "chain": ["claude-opus-5-5", "claude-sonnet-5-5"], "chain_id": "5a1b"}
    assert record.chain_of(ev) == {"ids": ["claude-opus-5-5", "claude-sonnet-5-5"], "sha256": "5a1b"}


def test_the_reader_of_an_old_call_chain_answers_unknown_and_never_rebuilds_it():
    """A record written before the field stays readable: the chain reads `unknown`, never the configuration in force now."""
    ev = {"event": "call_start", "node": "a", "entry": 1, "item": None, "session": "s1", "pid": 1, "limit": 10.0, "started_at": 1000.0}
    assert record.chain_of(ev) == {"ids": "unknown", "sha256": "unknown"}


def test_an_old_record_with_no_chain_field_stays_readable(tmp_path):
    """The fixture is a run record written before the field existed: it reads whole, and each call reads `unknown`."""
    fresh = tmp_path / "r.jsonl"
    r = record.Record(fresh)
    r.append("run_start", **START)
    r.append("call_start", node="a", entry=1, item=None, session="s1", pid=1, limit=10.0, started_at=1000.0)
    events = record.read(fresh)
    assert [e["event"] for e in events] == ["run_start", "call_start"]
    assert record.chain_of(events[1]) == {"ids": "unknown", "sha256": "unknown"}
    audit = DESIGN.parent.parent / "tests" / "fixtures" / "run-router-waves" / "run" / "record.jsonl"
    calls = [e for e in record.read(audit) if e["event"] == "call_start"]
    assert calls and all(record.chain_of(e)["ids"] == "unknown" for e in calls)
    p = tmp_path / "r.jsonl"
    bad = {k: v for k, v in OUTCOME.items() if k != "reasons"}
    p.write_text(json.dumps({"event": "outcome", "pos": 1, **bad}) + "\n")
    with pytest.raises(record.RecordError, match="lacks reasons"):
        record.read(p)
    r = record.Record(tmp_path / "w.jsonl")
    with pytest.raises(record.RecordError):
        r.append("outcome", **bad)
    assert r.events == [] and not (tmp_path / "w.jsonl").exists()


def test_the_reader_rejects_a_position_gap_an_unknown_kind_and_a_bad_outcome(tmp_path):
    p = tmp_path / "r.jsonl"
    p.write_text(json.dumps({"event": "run_start", "pos": 2, **START}) + "\n")
    with pytest.raises(record.RecordError, match="pos"):
        record.read(p)
    p.write_text(json.dumps({"event": "mystery", "pos": 1}) + "\n")
    with pytest.raises(record.RecordError, match="unknown event kind"):
        record.read(p)
    p.write_text(json.dumps({"event": "outcome", "pos": 1, **{**OUTCOME, "outcome": "maybe"}}) + "\n")
    with pytest.raises(record.RecordError, match="not one of"):
        record.read(p)


def test_a_write_interrupted_before_the_rename_leaves_the_old_file(tmp_path, monkeypatch):
    r = record.Record(tmp_path / "r.jsonl")
    r.append("run_start", **START)
    before = r.path.read_text()

    def boom(src, dst):
        raise KeyboardInterrupt("killed mid-write")
    monkeypatch.setattr(record.os, "replace", boom)
    with pytest.raises(KeyboardInterrupt):
        r.append("outcome", **OUTCOME)
    assert r.path.read_text() == before
    assert [p.name for p in tmp_path.iterdir()] == ["r.jsonl"]
    assert len(r.events) == 1


def test_a_writer_killed_mid_run_leaves_only_whole_events(tmp_path):
    """Kill the generator while it writes and read what is left."""
    path = tmp_path / "r.jsonl"
    code = textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {str(DESIGN.parent.parent)!r})
        from squad.record import Record
        r = Record({str(path)!r})
        r.append("run_start", **{START!r})
        print("ready", flush=True)
        while True:
            r.append("outcome", **{OUTCOME!r})
    """)
    p = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    assert p.stdout.readline().strip() == "ready"
    time.sleep(0.4)
    p.send_signal(signal.SIGKILL)
    p.wait()
    events = record.read(path)
    assert len(events) >= 2 and events[0]["event"] == "run_start"
    assert [e["pos"] for e in events] == list(range(1, len(events) + 1))


@pytest.mark.anyio
async def test_a_run_keeps_the_raw_graph_spelling_in_model_and_the_registry_id_apart(tmp_path):
    """#587: a resolving run keeps the graph spelling in `model`; the registry id goes in its own field.

    Driven through `Engine` as `tests/test_resume.py` drives it, so the format cannot change under a green record test.
    """
    from squad.adapter import ScriptedAdapter
    from squad.engine import Engine
    from squad.graph import from_doc
    from tests.flows import agent, doc
    reg = tmp_path / "registry.json"
    reg.write_text(json.dumps({"subscriptions": {"claude": {"label": "Claude"}},
                               "models": [{"id": "claude-sonnet-5-5", "label": "Sonnet 5.5", "provider": "claude",
                                           "subscription": "claude", "aliases": ["sonnet"],
                                           "routes": {"gateway": {"auth": "subscription", "model": "sonnet", "served": ["sonnet"]}}}]}))
    d = doc([agent("a", output_key="x", next={"fail": "exit:fail"})], ["a"])
    d["models"] = {"resolve": True}          # the graph opts in, so the outcome names the id beside the raw spelling
    eng = Engine(from_doc(d), ScriptedAdapter({"a": {"output": {"x": 1}, "spend": 0.1}}), tmp_path / "run", registry=str(reg))
    await eng.run()
    out = [e for e in record.read(tmp_path / "run" / "record.jsonl") if e["event"] == "outcome"][0]
    assert out["model"] == "sonnet"
    assert out["model_id"] == "claude-sonnet-5-5"


@pytest.mark.anyio
async def test_a_run_that_does_not_resolve_records_only_the_graph_spelling(tmp_path):
    """#587: with no opt-in, an outcome carries the graph spelling and no registry field at all."""
    from squad.adapter import ScriptedAdapter
    from squad.engine import Engine
    from squad.graph import from_doc
    from tests.flows import agent, doc
    d = doc([agent("a", output_key="x", next={"fail": "exit:fail"})], ["a"])
    eng = Engine(from_doc(d), ScriptedAdapter({"a": {"output": {"x": 1}, "spend": 0.1}}), tmp_path / "run")
    await eng.run()
    out = [e for e in record.read(tmp_path / "run" / "record.jsonl") if e["event"] == "outcome"][0]
    assert out["model"] == "sonnet"
    assert "model_id" not in out and "route_model" not in out


def test_an_outcome_keeps_the_model_id_and_the_wire_string(tmp_path):
    """A resolving run keeps the string it sent in `route_model`, apart from the graph spelling in `model`."""
    r = record.Record(tmp_path / "r.jsonl")
    r.append("run_start", **START)
    ev = r.append("outcome", **{**OUTCOME, "model": "claude-sonnet-5-5", "route_model": "sonnet"})
    assert (ev["model"], ev["route_model"]) == ("claude-sonnet-5-5", "sonnet")
    assert record.read(r.path)[1]["route_model"] == "sonnet"


def test_a_record_reopens_and_continues_its_positions(tmp_path):
    r = record.Record(tmp_path / "r.jsonl")
    r.append("run_start", **START)
    r2 = record.Record(tmp_path / "r.jsonl")
    assert r2.append("outcome", **OUTCOME)["pos"] == 2


def test_the_router_fixture_reads_clean():
    events = record.read(DESIGN / "router.record.jsonl")
    assert events[0]["event"] == "run_start" and events[-1]["event"] == "verdict"


def test_one_outcome_event_per_node_exit_holds_the_fold_fields(tmp_path):
    """An outcome event holds node, entry, outcome, reasons list, artifact, position."""
    r = record.Record(tmp_path / "r.jsonl")
    r.append("run_start", **START)
    ev = r.append("outcome", **{**OUTCOME, "reasons": ["loop_max_exhausted"]})
    assert ev["reasons"] == ["loop_max_exhausted"] and ev["entry"] == 1 and ev["pos"] == 2


RETRIED = dict(node="router", entry=1, model="claude-opus-5-5", error="API Error: Connection refused (ECONNREFUSED)")


def test_a_model_retried_event_is_registered_and_written_before_the_retry(tmp_path):
    """#1293: a transport failure is retried once and the retry is on the record. The kind must be registered, or the
    append raises before the retry and the run dies on its own bookkeeping."""
    assert record.REQUIRED["model_retried"] == ["node", "entry", "model", "error"]
    r = record.Record(tmp_path / "r.jsonl")
    r.append("run_start", **START)
    assert r.append("model_retried", **RETRIED)["pos"] == 2
    assert record.read(r.path)[1]["event"] == "model_retried"
    with pytest.raises(record.RecordError, match="lacks model"):
        r.append("model_retried", **{k: v for k, v in RETRIED.items() if k != "model"})


def test_every_kind_squad_writes_to_the_record_is_registered():
    """The reverse of test_two_flows: every literal kind a squad module appends to a record, or hands to the adapter's sink
    (which the engine binds to Record.append), is in REQUIRED. A kind passed in a variable is the sink's forwarding, and
    each sink caller names its kind as a literal, so this scan sees it."""
    import ast
    root = DESIGN.parent.parent / "squad"
    written = {}
    for path in sorted(root.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
            if not (isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)):
                continue
            fn = node.func
            if isinstance(fn, ast.Attribute) and fn.attr == "sink" or isinstance(fn, ast.Name) and fn.id == "sink":
                writes = True
            elif isinstance(fn, ast.Attribute) and fn.attr == "append":
                recv = fn.value
                name = recv.id if isinstance(recv, ast.Name) else recv.attr if isinstance(recv, ast.Attribute) else None
                writes = name in ("record", "rec", "r")
            else:
                writes = False
            if writes:
                written.setdefault(node.args[0].value, f"{path.relative_to(DESIGN.parent.parent)}:{node.lineno}")
    assert {"call_start", "outcome", "verdict", "model_retried", "model_refused", "progress", "sandbox_deny",
            "compaction"} <= set(written), "the scan must see the kinds the engine writes, or it guards nothing"
    unregistered = {k: where for k, where in written.items() if k not in record.REQUIRED}
    assert unregistered == {}, f"a kind is written but not registered in record.REQUIRED: {unregistered}"
