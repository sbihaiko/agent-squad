"""Ticket #36: engine options and the command line (ADR-0106, ADR-0112)."""
import json

import pytest

from squad import cli
from squad.adapter import ScriptedAdapter
from squad.engine import Engine
from squad.graph import from_doc
from squad.options import OptionError, resolve
from squad.record import read
from tests.flows import agent, doc
from tests.test_resume import wait_doc

OK = {"output": {"x": 1, "y": 1}, "spend": 0.1}


def write(tmp_path, d, script=None):
    g = tmp_path / "g.graph.json"
    g.write_text(json.dumps(d))
    s = tmp_path / "script.json"
    s.write_text(json.dumps({"script": script or {"a": OK, "b": OK}, "repeat_last": True}))
    return str(g), str(s)


def two_agents():
    nodes = [agent("a", output_key="x", next={"fail": "exit:fail"}), agent("b", output_key="y", next={"fail": "exit:fail"})]
    return doc(nodes, ["a", "b"])


def events(run_dir, kind):
    return [e for e in read(run_dir / "record.jsonl") if e["event"] == kind]


def test_an_invalid_graph_prints_each_defect_on_its_own_line(tmp_path, capsys):
    """A defect found while probing on 2026-10-02: the message joined the error list's *text*, so it printed one letter per line."""
    d = doc([agent("a", output_key="x", next={"fail": "exit:fail"})], ["a", "b"])
    g, s = write(tmp_path, d)
    assert cli.main(["run", g, "--script", s, "--run-dir", str(tmp_path / "r")]) == cli.USAGE
    err = capsys.readouterr().err
    assert err.startswith("squad: invalid graph:\n  ") and "unknown child b" in err
    assert len(err.splitlines()) == 2 and err.splitlines()[1].startswith("  ")


def test_run_exits_with_the_verdict_code_and_prints_the_message(tmp_path, capsys):
    g, s = write(tmp_path, two_agents())
    code = cli.main(["run", g, "--script", s, "--run-dir", str(tmp_path / "r")])
    assert code == 0 and "verdict completed (exit 0)" in capsys.readouterr().out


def test_stop_after_truncates_to_stopped_exit_30_with_the_resume_command(tmp_path, capsys):
    g, s = write(tmp_path, two_agents())
    code = cli.main(["run", g, "--script", s, "--run-dir", str(tmp_path / "r"), "--stop-after", "a"])
    out = capsys.readouterr().out
    assert code == 30 and "stopped_after:a" in out and "resume: squad resume" in out
    assert events(tmp_path / "r", "run_start")[0]["engine_options"] == {"stop_after": "a"}
    assert [e["node"] for e in events(tmp_path / "r", "outcome")] == ["a"]


def test_a_stopped_run_resumes_to_completion_through_the_cli(tmp_path, capsys):
    g, s = write(tmp_path, two_agents())
    cli.main(["run", g, "--script", s, "--run-dir", str(tmp_path / "r"), "--stop-after", "a"])
    capsys.readouterr()
    code = cli.main(["resume", "x", "--run-dir", str(tmp_path / "r"), "--script", s])
    assert code == 0
    assert [e["node"] for e in events(tmp_path / "r", "outcome") if e["node"] in "ab"] == ["a", "b"]
    assert len(events(tmp_path / "r", "verdict")) == 2


def test_resume_from_a_node_and_an_answer_are_recorded(tmp_path, capsys):
    g, s = write(tmp_path, wait_doc())
    assert cli.main(["run", g, "--script", s, "--run-dir", str(tmp_path / "r")]) == 10
    assert cli.main(["resume", "x", "--run-dir", str(tmp_path / "r"), "--script", s, "--answer", "pass"]) == 0
    r = events(tmp_path / "r", "resume")[0]
    assert r["engine_options"] == {"answer": "pass"} and r["invoker"] == "cli"


def test_an_unknown_node_name_is_refused(tmp_path, capsys):
    g, s = write(tmp_path, two_agents())
    assert cli.main(["run", g, "--script", s, "--run-dir", str(tmp_path / "r"), "--stop-after", "nope"]) == 2
    assert "nope" in capsys.readouterr().err and not (tmp_path / "r").exists()
    cli.main(["run", g, "--script", s, "--run-dir", str(tmp_path / "r2")])
    assert cli.main(["resume", "x", "--run-dir", str(tmp_path / "r2"), "--script", s, "--from", "nope"]) == 2


def test_an_option_may_lower_a_run_value_and_never_raise_it(tmp_path):
    g = from_doc(two_agents())
    opts, cfg = resolve(g, {"spend_cap": 0.5, "deadline": 100, "max_parallel": 1})
    assert (cfg["spend_cap"], cfg["deadline"], cfg["max_parallel"]) == (0.5, 100, 1) and g.run["spend_cap"] == 10.0
    for bad in ({"spend_cap": 99}, {"deadline": 9999}, {"max_parallel": 9}, {"spend_cap": 0}, {"spend_cap": 0.001}):
        with pytest.raises(OptionError):
            resolve(g, bad)


def test_an_option_never_overrides_a_node_model_or_budget():
    g = from_doc(two_agents())
    for k in ("model", "budget", "turns", "tools", "effort", "a.model"):
        with pytest.raises(OptionError, match="not an engine option"):
            resolve(g, {k: "x"})


def test_tier_pin_is_a_fact_sourced_from_the_run_start_position(tmp_path):
    g, s = write(tmp_path, two_agents())
    eng = Engine(from_doc(two_agents()), ScriptedAdapter({"a": OK, "b": OK}), tmp_path / "r", options={"tier_pin": "large"})
    import anyio
    anyio.run(eng.run)
    f = eng.facts.get("tier_pin")
    assert f.value == "large" and f.source["kind"] == "run_record"
    with pytest.raises(OptionError):
        resolve(from_doc(two_agents()), {"tier_pin": "huge"})


def test_a_lowered_run_cap_stops_the_run_with_the_cap_reason(tmp_path, capsys):
    g, s = write(tmp_path, two_agents(), {"a": OK, "b": {"output": {"z": 1}, "spend": 0.05, "stop_reason": "spend_cap"}})
    code = cli.main(["run", g, "--script", s, "--run-dir", str(tmp_path / "r"), "--spend-cap", "0.15"])
    assert code == 1 and events(tmp_path / "r", "run_start")[0]["engine_options"] == {"spend_cap": 0.15}
    assert "run_spend_cap" in capsys.readouterr().out


def test_a_graph_error_and_a_missing_adapter_exit_2(tmp_path, capsys):
    bad = tmp_path / "bad.graph.json"
    bad.write_text("{}")
    assert cli.main(["run", str(bad), "--script", "none"]) == 2
    g, _ = write(tmp_path, two_agents())
    assert cli.main(["run", g, "--run-dir", str(tmp_path / "r")]) == 2


def test_adapter_sdk_builds_the_real_adapter_without_calling_it():
    from squad.sdk_adapter import SdkAdapter
    a = cli.parser().parse_args(["run", "g.json", "--adapter", "sdk"])
    assert isinstance(cli._adapter(a), SdkAdapter)



# Ticket #68: --expect-graph-hash binds a reviewed hash to the run (ADR-0106 rule 8, ADR-0111 rule 8).
def test_a_matching_expected_graph_hash_runs_and_run_start_holds_it(tmp_path):
    d = two_agents()
    g, s = write(tmp_path, d)
    h = from_doc(d).hash
    assert cli.main(["run", g, "--script", s, "--run-dir", str(tmp_path / "r"), "--expect-graph-hash", h]) == 0
    assert events(tmp_path / "r", "run_start")[0]["engine_options"] == {"expect_graph_hash": h}


def test_a_mismatched_expected_graph_hash_exits_2_names_both_hashes_and_writes_no_record(tmp_path, capsys):
    d = two_agents()
    g, s = write(tmp_path, d)
    h = from_doc(d).hash
    code = cli.main(["run", g, "--script", s, "--run-dir", str(tmp_path / "r"), "--expect-graph-hash", "0" * 64])
    err = capsys.readouterr().err
    assert code == 2 and h in err and "0" * 64 in err
    assert not (tmp_path / "r" / "record.jsonl").exists()


def test_a_layout_move_still_matches_the_expected_graph_hash(tmp_path):
    d = two_agents()
    h = from_doc(d).hash
    g, s = write(tmp_path, d)
    (tmp_path / "g.layout.json").write_text(json.dumps({"a": {"x": 999, "y": 5}}))
    assert cli.main(["run", g, "--script", s, "--run-dir", str(tmp_path / "r"), "--expect-graph-hash", h]) == 0


def test_resume_refuses_a_mismatched_expected_graph_hash(tmp_path):
    g, s = write(tmp_path, two_agents())
    assert cli.main(["run", g, "--script", s, "--run-dir", str(tmp_path / "r"), "--stop-after", "a"]) == 30
    n = len(read(tmp_path / "r" / "record.jsonl"))
    assert cli.main(["resume", "x", "--run-dir", str(tmp_path / "r"), "--script", s, "--expect-graph-hash", "f" * 64]) == 2
    assert len(read(tmp_path / "r" / "record.jsonl")) == n


def test_dashboard_prints_the_page_url_for_the_latest_run_and_opens_nothing_with_no_open(tmp_path, capsys):
    for name, ts in (("a", 100.0), ("b", 200.0)):
        d = tmp_path / name
        d.mkdir()
        (d / "record.jsonl").write_text(json.dumps({"event": "run_start", "run_id": f"run-{name}", "ts": ts, "pos": 1}) + "\n")
    assert cli.main(["dashboard", "--runs-dir", str(tmp_path), "--no-open", "--file"]) == 0
    out = capsys.readouterr().out
    assert "#run=" in out and "run 2: run-b" in out and "run-a" not in out
    assert cli.main(["dashboard", "1", "--runs-dir", str(tmp_path), "--no-open", "--file"]) == 0
    assert str((tmp_path / "a").resolve()) in capsys.readouterr().out


def test_dashboard_serves_the_run_and_prints_a_loopback_url_the_page_loads_with_no_click(tmp_path, capsys, monkeypatch):
    from squad import dashboard_server
    for name, ts in (("a", 100.0), ("b", 200.0)):
        d = tmp_path / name
        d.mkdir()
        (d / "record.jsonl").write_text(json.dumps({"event": "run_start", "run_id": f"run-{name}", "ts": ts, "pos": 1}) + "\n")
    started, opened = [], []
    monkeypatch.setattr(dashboard_server, "start", lambda folder, **kw: started.append((folder, kw)) or 4242)
    import webbrowser
    monkeypatch.setattr(webbrowser, "open", lambda u: opened.append(u))
    assert cli.main(["dashboard", "1", "--runs-dir", str(tmp_path), "--idle", "30"]) == 0
    out = capsys.readouterr().out
    assert started == [((tmp_path / "a").resolve(), {"watch": 0.2, "idle": 30.0})] and opened == ["http://127.0.0.1:4242/"]
    assert "http://127.0.0.1:4242/" in out and "#run=" not in out and "connect this folder" not in out


def test_dashboard_takes_the_stream_watch_step_from_the_defaults_file(tmp_path, monkeypatch):
    """Ticket #105: the server's watch step is a setting, so `squad dashboard` reads it and passes it on."""
    from squad import dashboard_server, defaults
    d = tmp_path / "a"
    d.mkdir()
    (d / "record.jsonl").write_text(json.dumps({"event": "run_start", "run_id": "run-a", "ts": 1.0, "pos": 1}) + "\n")
    cfg = defaults.load(defaults.PATH)
    cfg["dashboard"]["watch_seconds"] = 0.05
    f = tmp_path / "defaults.yaml"
    f.write_text(json.dumps(cfg))
    started = []
    monkeypatch.setattr(dashboard_server, "start", lambda folder, **kw: started.append(kw) or 4242)
    assert cli.main(["dashboard", "1", "--runs-dir", str(tmp_path), "--defaults", str(f), "--no-open"]) == 0
    assert started[0]["watch"] == 0.05


def test_dashboard_list_shows_every_run_with_its_number(tmp_path, capsys):
    for name, ts in (("a", 100.0), ("b", 200.0)):
        d = tmp_path / name
        d.mkdir()
        (d / "record.jsonl").write_text(json.dumps({"event": "run_start", "run_id": f"run-{name}", "ts": ts, "pos": 1}) + "\n")
    assert cli.main(["dashboard", "--runs-dir", str(tmp_path), "--list"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("1 ") and "run-a" in lines[0] and lines[1].startswith("2 ") and "run-b" in lines[1]


def test_dashboard_list_of_an_empty_runs_dir_says_so_on_stderr(tmp_path, capsys):
    assert cli.main(["dashboard", "--runs-dir", str(tmp_path), "--list"]) == 0
    out = capsys.readouterr()
    assert out.out == "" and "no runs" in out.err


def test_dashboard_unknown_run_exits_2_with_a_clear_message(tmp_path, capsys):
    assert cli.main(["dashboard", "7", "--runs-dir", str(tmp_path), "--no-open"]) == 2
    assert "no runs" in capsys.readouterr().err


def test_dashboard_never_starts_a_run(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(cli, "_run", lambda a: called.append("run"))
    monkeypatch.setattr(cli, "_resume", lambda a: called.append("resume"))
    from squad import dashboard_server
    monkeypatch.setattr(dashboard_server, "start", lambda folder, **kw: 4242)
    cli.main(["dashboard", "--runs-dir", str(tmp_path), "--no-open"])
    cli.main(["dashboard", "--runs-dir", str(tmp_path), "--no-open", "--file"])
    assert called == []


# The dashboard reads <run folder>/graph.json; a plain `squad run` writes the graph it ran there (ADR-0127).
def test_a_plain_run_leaves_the_graph_it_ran_in_its_run_folder_and_the_dashboard_serves_it(tmp_path):
    import http.client
    import threading

    from squad import dashboard_server as ds
    d = two_agents()
    g, s = write(tmp_path, d)
    r = tmp_path / "r"
    assert cli.main(["run", g, "--script", s, "--run-dir", str(r)]) == 0
    copy = json.loads((r / "graph.json").read_text())
    assert from_doc(copy).hash == events(r, "run_start")[0]["graph_hash"] == from_doc(d).hash
    page = tmp_path / "page.html"
    page.write_text("<html></html>")
    srv = ds.RunServer(r, page=page, idle=60)
    threading.Thread(target=srv.handle_request, daemon=True).start()
    c = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=5)
    c.request("GET", "/run/graph.json")
    resp = c.getresponse()
    body = resp.read()
    srv.server_close()
    assert resp.status == 200 and [n["name"] for n in json.loads(body)["nodes"]] == [n["name"] for n in two_agents()["nodes"]]
