"""One module starts a run (squad.launch, ticket #151).

Guards: the module owns the run folder, the `graph.json` copy, the run id and the defaults record; an unsound defaults file
refuses the run before a folder exists; a router or subject run resumes by folder name with no `--run-dir`;
the CLI and both harness scripts call the module and never build an `Engine` themselves.
"""
import ast
import json
from pathlib import Path

import pytest

from squad import cli, defaults, launch, record
from squad.adapter import ScriptedAdapter
from squad.graph import canon_hash, from_doc
from squad.options import OptionError
from tests.flows import agent, doc, scope_gate

ROOT = Path(__file__).resolve().parent.parent
OK = {"output": {"x": 1}, "spend": 0.1}


def one_agent():
    return from_doc(doc([agent("a", output_key="x", next={"fail": "exit:fail"})], ["a"]))


def test_launch_sets_up_the_folder_the_graph_copy_one_run_id_and_the_defaults_record(tmp_path):
    g = one_agent()
    eng = launch.launch(g, tmp_path / "r", adapter=ScriptedAdapter({"a": OK}), defaults=defaults.PATH)
    assert json.loads((tmp_path / "r" / "graph.json").read_text()) == g.doc
    assert eng.defaults["sha256"] == defaults.digest(defaults.PATH) and "_cfg" not in eng.defaults
    assert eng.run_id.startswith("run-")


def test_the_run_id_has_one_source_and_names_the_folder_when_none_is_given(tmp_path, monkeypatch):
    monkeypatch.setattr(record, "new_run_id", lambda: "run-fixed")
    monkeypatch.setattr("squad.engine.new_run_id", lambda: "run-fixed")
    eng = launch.launch(one_agent(), None, runs_dir=tmp_path, adapter=ScriptedAdapter({"a": OK}))
    assert eng.run_id == "run-fixed" and eng.run_dir == tmp_path / "run-fixed" and (eng.run_dir / "graph.json").exists()
    from squad.engine import Engine
    assert Engine(one_agent(), ScriptedAdapter({"a": OK}), tmp_path / "e").run_id == "run-fixed"


def test_an_unsound_defaults_file_refuses_the_run_before_a_folder_exists(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(defaults.PATH.read_text().replace("effort: medium", "effort: nonsense"))
    with pytest.raises(OptionError, match="unsound"):
        launch.launch(one_agent(), tmp_path / "r", adapter=ScriptedAdapter({"a": OK}), defaults=bad)
    assert not (tmp_path / "r").exists()


def test_no_adapter_means_the_sdk_adapter_built_from_the_defaults(tmp_path):
    eng = launch.launch(one_agent(), tmp_path / "r")
    assert eng.adapter.progress_seconds == defaults.load()["progress"]["seconds"] and eng.defaults["path"] == str(defaults.PATH.resolve())


@pytest.mark.anyio
async def test_a_subject_style_run_resumes_by_folder_name_with_no_run_dir(tmp_path):
    from tests.test_resume import wait_doc
    out = tmp_path / "runs" / "subject-01-x"
    (out / "target").mkdir(parents=True)          # the clone sits beside the record, as `subject.prepare` leaves it
    both = {"a": {"output": {"x": 1, "y": 1}, "spend": 0.1}, "b": {"output": {"x": 1, "y": 1}, "spend": 0.1}}
    eng = launch.launch(from_doc(wait_doc()), out, adapter=ScriptedAdapter(both, repeat_last=True))
    assert (await eng.run()).exit_code == 10
    assert (out / "record.jsonl").exists()
    script = tmp_path / "s.json"
    script.write_text(json.dumps({"script": both, "repeat_last": True}))
    argv = ["resume", "subject-01-x", "--runs-dir", str(tmp_path / "runs"), "--script", str(script), "--answer", "pass"]
    assert await cli._resume(cli.parser().parse_args(argv)) == 0


def _autonomy_file(tmp_path, name, **kw):
    import yaml
    cfg = defaults.load()
    cfg["autonomy"].update(kw)
    p = tmp_path / name
    p.write_text(yaml.safe_dump(cfg))
    return p


@pytest.mark.anyio
async def test_run_start_carries_the_autonomy_values_and_a_resume_with_a_changed_value_is_refused(tmp_path):
    from squad.engine import ResumeRefused
    from tests.test_resume import wait_doc
    both = {"a": {"output": {"x": 1, "y": 1}, "spend": 0.1}, "b": {"output": {"x": 1, "y": 1}, "spend": 0.1}}
    same, off = _autonomy_file(tmp_path, "same.yaml"), _autonomy_file(tmp_path, "off.yaml", enabled=False)
    eng = launch.launch(from_doc(wait_doc()), tmp_path / "r", adapter=ScriptedAdapter(both, repeat_last=True), defaults=same)
    assert (await eng.run()).exit_code == 10
    assert eng.record.events[0]["autonomy"] == {"enabled": True, "max_resumes": 3}
    g = from_doc(wait_doc())
    with pytest.raises(ResumeRefused, match="autonomy"):
        await launch.resume_engine(g, tmp_path / "r", adapter=ScriptedAdapter(both, repeat_last=True), defaults=off).resume(answer="pass")
    again = launch.resume_engine(g, tmp_path / "r", adapter=ScriptedAdapter(both, repeat_last=True), defaults=same)
    assert (await again.resume(answer="pass")).exit_code == 0


def test_a_run_with_no_defaults_file_records_the_plugin_default_autonomy(tmp_path):
    eng = launch.launch(one_agent(), tmp_path / "r", adapter=ScriptedAdapter({"a": OK}))
    assert eng.autonomy == {"enabled": True, "max_resumes": 3}


@pytest.mark.anyio
async def test_run_start_records_the_spent_models_and_a_resume_reads_the_option_again(tmp_path):
    """The spent models are an engine option (ADR-0106): `run_start` records them. A marker moves with the provider's window, so a
    resume takes the value its caller passes now and records it in its own `resume` event; the run_start value stays the first one."""
    from tests.test_resume import wait_doc
    both = {"a": {"output": {"x": 1, "y": 1}, "spend": 0.1}, "b": {"output": {"x": 1, "y": 1}, "spend": 0.1}}
    eng = launch.launch(from_doc(wait_doc()), tmp_path / "r", adapter=ScriptedAdapter(both, repeat_last=True), options={"spent_models": ["claude-gpt-5.5"]})
    assert (await eng.run()).exit_code == 10
    assert eng.record.events[0]["engine_options"]["spent_models"] == ["claude-gpt-5.5"]
    again = launch.resume_engine(from_doc(wait_doc()), tmp_path / "r", adapter=ScriptedAdapter(both, repeat_last=True), options={"spent_models": []})
    assert (await again.resume(answer="pass")).exit_code == 0
    assert record.read(tmp_path / "r" / "record.jsonl")[0]["engine_options"]["spent_models"] == ["claude-gpt-5.5"]
    assert [e["engine_options"]["spent_models"] for e in record.read(tmp_path / "r" / "record.jsonl") if e["event"] == "resume"] == [[]]


def test_a_spent_models_option_that_is_not_a_list_of_ids_is_refused(tmp_path):
    with pytest.raises(OptionError, match="spent_models"):
        launch.launch(one_agent(), tmp_path / "r", adapter=ScriptedAdapter({"a": OK}), options={"spent_models": "claude-gpt-5.5"})


def test_the_cli_turns_the_spent_models_flag_into_the_engine_option():
    a = cli.parser().parse_args(["start", "--spent-models", "claude-gpt-5.5,claude-gpt-5.6-terra", "x"])
    assert cli._opts(a)["spent_models"] == ["claude-gpt-5.5", "claude-gpt-5.6-terra"]
    assert "spent_models" not in cli._opts(cli.parser().parse_args(["start", "x"]))


def calls(path):
    tree = ast.parse(path.read_text())
    return {n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "") for n in ast.walk(tree) if isinstance(n, ast.Call)}


@pytest.mark.parametrize("path", [ROOT / "squad" / "cli.py", ROOT / "scripts" / "run_router.py", ROOT / "scripts" / "run_subject.py"])
def test_the_cli_and_both_scripts_call_the_module_and_never_build_an_engine(path):
    c = calls(path)
    assert "launch" in c and "Engine" not in c, path.name
    tree = ast.parse(path.read_text())
    own = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "check"
           and isinstance(n.func.value, ast.Name) and n.func.value.id in ("D", "defaults")]
    assert own == [], f"{path.name} checks the defaults itself"   # the router script once skipped the check; the module owns it. `config check` and `setup --check` check other things


def test_the_folder_name_is_the_recorded_run_id_and_resume_finds_it_by_that_id(tmp_path):    # #162
    eng = launch.launch(one_agent(), None, runs_dir=tmp_path, adapter=ScriptedAdapter({"a": OK}))
    import anyio
    anyio.run(eng.run)
    assert eng.run_dir.name == eng.run_id == record.read(eng.run_dir / "record.jsonl")[0]["run_id"]
    assert (tmp_path / eng.run_id / "record.jsonl").exists()      # `squad resume <run id> --runs-dir` reads exactly this path


def test_a_run_folder_inside_the_target_repo_is_ignored_locally_and_no_tracked_file_changes(tmp_path):    # #163
    from tests.gitrepo import make_repo
    repo = tmp_path / "repo"
    make_repo(repo)
    launch.launch(one_agent(), None, runs_dir=repo / "runs", adapter=ScriptedAdapter({"a": OK}), target=str(repo))
    import subprocess
    assert subprocess.run(["git", "check-ignore", "-q", "runs/x"], cwd=repo).returncode == 0
    assert subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True).stdout.strip() == ""
    launch.launch(one_agent(), None, runs_dir=repo / "runs", adapter=ScriptedAdapter({"a": OK}), target=str(repo))
    assert (repo / ".git" / "info" / "exclude").read_text().count("/runs/") == 1


def test_a_run_folder_outside_the_target_is_left_alone(tmp_path):
    from tests.gitrepo import make_repo
    repo = tmp_path / "repo"
    make_repo(repo)
    assert launch.keep_runs_out_of_the_target(repo, tmp_path / "elsewhere" / "r") is None


def test_a_named_run_uses_the_name_for_id_and_folder(tmp_path):
    eng = launch.launch(one_agent(), None, runs_dir=tmp_path, adapter=ScriptedAdapter({"a": OK}), name="my-run.1")
    assert eng.run_id == "my-run.1" and eng.run_dir == tmp_path / "my-run.1" and (eng.run_dir / "graph.json").exists()


def test_an_unnamed_run_gets_a_random_id(tmp_path):
    eng = launch.launch(one_agent(), None, runs_dir=tmp_path, adapter=ScriptedAdapter({"a": OK}))
    assert eng.run_id.startswith("run-") and eng.run_dir.name == eng.run_id


@pytest.mark.parametrize("bad", ["", ".hidden", "a/b", "..", "a b", "-x"])
def test_an_unsafe_or_empty_name_is_refused_with_no_folder(tmp_path, bad):
    with pytest.raises(OptionError):
        launch.launch(one_agent(), None, runs_dir=tmp_path, adapter=ScriptedAdapter({"a": OK}), name=bad)
    assert list(tmp_path.iterdir()) == []


def test_a_duplicate_name_is_refused_and_writes_nothing(tmp_path):
    (tmp_path / "taken").mkdir()
    with pytest.raises(OptionError, match="taken"):
        launch.launch(one_agent(), None, runs_dir=tmp_path, adapter=ScriptedAdapter({"a": OK}), name="taken")
    assert list((tmp_path / "taken").iterdir()) == []


def test_the_cli_start_takes_name(tmp_path):
    assert cli.parser().parse_args(["start", "--name", "n1", "do it"]).name == "n1"
    assert cli.parser().parse_args(["start", "do it"]).name is None
    script = tmp_path / "s.json"
    script.write_text(json.dumps({"script": {"a": OK}, "repeat_last": True}))
    runs = tmp_path / "runs"
    rc = cli.main(["start", "--name", "bad/name", "do it", "--runs-dir", str(runs), "--script", str(script)])
    assert rc != 0 and not runs.exists()
# --- ticket #183: the watch opens the dashboard on a new run ---

class Opener:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def __call__(self, folder, watch_seconds=None, *, reuse=False):
        self.calls.append((Path(folder), reuse))
        if self.fail:
            raise RuntimeError("no browser")


def test_default_on_opens_the_page_once_for_the_new_folder(tmp_path):
    op = Opener()
    launch.launch(one_agent(), tmp_path / "r", adapter=ScriptedAdapter({"a": OK}), defaults=defaults.PATH, opener=op)
    assert op.calls == [(tmp_path / "r", False)]


def test_watch_dashboard_off_opens_no_page(tmp_path):
    op, off = Opener(), tmp_path / "off.yaml"
    off.write_text(defaults.PATH.read_text().replace("dashboard: true", "dashboard: false"))
    launch.launch(one_agent(), tmp_path / "r", adapter=ScriptedAdapter({"a": OK}), defaults=off, opener=op)
    launch.launch(one_agent(), tmp_path / "s", adapter=ScriptedAdapter({"a": OK}), defaults=defaults.PATH, watch=False, opener=op)
    launch.launch(one_agent(), tmp_path / "t", adapter=ScriptedAdapter({"a": OK}), opener=op)   # no defaults file, no page
    assert op.calls == []


@pytest.mark.anyio
async def test_a_failing_opener_is_a_warning_and_the_run_completes(tmp_path, capsys):
    eng = launch.launch(one_agent(), tmp_path / "r", adapter=ScriptedAdapter({"a": OK}), defaults=defaults.PATH, opener=Opener(fail=True))
    v = await eng.run()
    assert v.exit_code == 0 and "warning: the dashboard did not open: no browser" in capsys.readouterr().err


def test_a_headless_environment_skips_the_page_and_prints_the_url(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SQUAD_HEADLESS", "1")
    opened = []
    url = launch.open_dashboard(tmp_path, server=lambda folder, **kw: 4242, browser=opened.append)
    assert opened == [] and url == "http://127.0.0.1:4242/" and url in capsys.readouterr().out


def test_a_display_opens_the_browser_once_and_a_resume_reuses_the_open_page(tmp_path, monkeypatch):
    from squad import dashboard_server
    monkeypatch.setattr(launch, "headless", lambda: False)
    opened = []
    launch.open_dashboard(tmp_path, server=lambda folder, **kw: 4242, browser=opened.append)
    assert opened == ["http://127.0.0.1:4242/"]
    monkeypatch.setattr(dashboard_server, "running", lambda folder: 4242)
    launch.open_dashboard(tmp_path, reuse=True, server=lambda folder, **kw: 4242, browser=opened.append)
    assert len(opened) == 1                                  # the page stays open: no second tab
    monkeypatch.setattr(dashboard_server, "running", lambda folder: None)
    launch.open_dashboard(tmp_path, reuse=True, server=lambda folder, **kw: 4242, browser=opened.append)
    assert len(opened) == 2                                  # no page open: a resume opens one


def test_a_resume_asks_the_opener_to_reuse(tmp_path):
    op = Opener()
    launch.resume_engine(one_agent(), tmp_path / "r", adapter=ScriptedAdapter({"a": OK}), defaults=defaults.PATH, opener=op)
    assert op.calls == [(tmp_path / "r", True)]


def test_the_run_start_record_carries_the_watch_setting(tmp_path):
    on = launch.launch(one_agent(), tmp_path / "a", adapter=ScriptedAdapter({"a": OK}), defaults=defaults.PATH, opener=Opener())
    off = launch.launch(one_agent(), tmp_path / "b", adapter=ScriptedAdapter({"a": OK}), defaults=defaults.PATH, watch=False)
    assert on.defaults["watch"] == {"dashboard": True, "monitor": True, "monitor_seconds": 30}
    assert off.defaults["watch"] == {"dashboard": False, "monitor": False, "monitor_seconds": 30}


def test_an_unsound_watch_block_is_a_defect():
    cfg = defaults.load()
    cfg["watch"] = {"dashboard": "yes"}
    assert "watch.dashboard" in defaults.check(cfg)


def test_the_harness_scripts_for_many_runs_pass_watch_off():
    for name in ("run_router.py", "run_subject.py"):
        calls = [n for n in ast.walk(ast.parse((ROOT / "scripts" / name).read_text())) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute) and n.func.attr == "launch" and getattr(n.func.value, "id", "") == "launch"]
        assert calls and all(any(k.arg == "watch" and getattr(k.value, "value", None) is False for k in c.keywords) for c in calls), name


# --- SPEC 505: the router chain's on/hrs overlay, applied once before the graph copy ---

def _window(offset):
    """A one-hour local window starting `offset` hours from now: 0 holds this hour, 1 does not."""
    import time
    h = (time.localtime().tm_hour + offset) % 24
    return f"{h:02d}-{(h + 1) % 24:02d}"


def _router_models(tmp_path, entries, v1=False):
    """A models.json whose router chain carries `entries` (id, on, hrs), in the v1 or v2 shape."""
    if v1:
        doc = {"router_models": [e["id"] for e in entries],
               "off": {"router_models": {e["id"]: True for e in entries if e.get("on") is False}},
               "hours": {"router_models": {e["id"]: e["hrs"] for e in entries if e.get("hrs")}}}
    else:
        doc = {"schema_version": 2, "chains": {"router": [dict(e) for e in entries]}}
    path = tmp_path / "models.json"
    path.write_text(json.dumps(doc))
    return path


def _dispatch_node(models, efforts=("low",), when=None):
    node = {"name": "route", "kind": "dispatch", "entry": "router", "max_depth": 1, "max_children": 2,
            "max_parallel": 1, "deadline": 60, "join": "all",
            "allow": {"models": list(models), "efforts": list(efforts), "tools": ["Read"],
                      "skills": [{"name": "tdd"}], "workspaces": ["ws"]},
            "budget": {"stalls": {"min": 1, "max": 10}, "spend": {"min": 0.01, "max": 1.0}},
            "checks": ["gate"]}
    if when:
        node["when"] = when
    return node


def _router_agent():
    node = agent("router", may_spawn=True, output_key="spawns")
    del node["next"]          # a dispatch entry is not a loop child, so `repeat` is invalid there
    return node


def _dispatch_graph(models, efforts=("low",)):
    return from_doc(doc([agent("a", output_key="x"), _dispatch_node(models, efforts), _router_agent(),
                         scope_gate(fail="exit:fail")], ["a", "route"]))


def _allow(doc):
    return next(n for n in doc["nodes"] if n["kind"] == "dispatch")["allow"]["models"]


def test_the_router_chain_narrows_the_envelope_and_keeps_the_authored_copy(tmp_path):    # SPEC 505
    models = _router_models(tmp_path, [{"id": "opus"}, {"id": "sonnet", "on": False}])
    g = _dispatch_graph(["opus", "sonnet"], efforts=["low", "high"])
    eng = launch.launch(g, tmp_path / "r", adapter=ScriptedAdapter({"a": OK}), models=models)
    copy = json.loads((tmp_path / "r" / "graph.json").read_text())
    assert copy == g.doc and _allow(copy) == ["opus", "sonnet"]
    assert eng.router_overlay == {"route": ["opus"]}
    route = next(n for n in copy["nodes"] if n["kind"] == "dispatch")
    assert route["allow"]["efforts"] == ["low", "high"] and all(isinstance(m, str) for m in route["allow"]["models"])
    assert eng.g.hash == canon_hash(copy)


def test_a_v1_router_chain_filters_by_the_same_on_and_hours_rule(tmp_path):    # SPEC 505
    models = _router_models(tmp_path, [{"id": "opus"}, {"id": "sonnet", "hrs": _window(1)}], v1=True)
    eng = launch.launch(_dispatch_graph(["opus", "sonnet"]), tmp_path / "r", adapter=ScriptedAdapter({"a": OK}), models=models)
    assert eng.router_overlay == {"route": ["opus"]}


def test_a_router_set_that_closes_every_model_leaves_the_authored_envelope(tmp_path):    # SPEC 505
    """A graph that already carries a valid envelope starts as it did before the overlay existed (ADR-0147)."""
    models = _router_models(tmp_path, [{"id": "opus", "on": False}])
    g = _dispatch_graph(["opus"])
    eng = launch.launch(g, tmp_path / "r", adapter=ScriptedAdapter({"a": OK}), models=models)
    assert eng.router_overlay == {}
    assert json.loads((tmp_path / "r" / "graph.json").read_text()) == g.doc


def test_a_dispatch_without_an_envelope_refuses_the_start_by_name(tmp_path):    # SPEC 505
    models = _router_models(tmp_path, [{"id": "opus", "on": False}])
    g = _dispatch_graph(["opus"])
    next(n for n in g.doc["nodes"] if n["kind"] == "dispatch")["allow"]["models"] = []
    with pytest.raises(OptionError) as exc:
        launch.router_overlay(g, models)
    assert "route" in str(exc.value) and "allow.models" in str(exc.value)


def test_the_shipped_models_file_carries_no_router_chain_and_narrows_nothing(tmp_path):    # SPEC 505
    """The loop ships a models.json with no `router_models`: a start with it is a start as before the overlay."""
    shipped = ROOT / "contrib" / "squad-goal" / "scripts" / "models.json"
    assert "router_models" not in json.loads(shipped.read_text())
    g = _dispatch_graph(["opus"])
    assert launch.router_overlay(g, shipped) == {}
    eng = launch.launch(g, tmp_path / "r", adapter=ScriptedAdapter({"a": OK}), models=shipped)
    assert eng.router_overlay == {}


def test_a_router_entry_outside_the_envelope_changes_nothing(tmp_path):    # SPEC 505
    models = _router_models(tmp_path, [{"id": "sonnet", "on": False}])
    assert launch.router_overlay(_dispatch_graph(["opus"]), models) == {}


@pytest.mark.anyio
async def test_a_router_entry_off_keeps_the_authored_graph_hash(tmp_path):    # #588
    """The router overlay never changes the authored graph hash: `--expect-graph-hash` still matches."""
    models = _router_models(tmp_path, [{"id": "opus"}, {"id": "sonnet", "on": False}])
    g = _dispatch_graph(["opus", "sonnet"])
    h = canon_hash(g.doc)
    router = {"output": {"spawns": [], "done": True}}
    eng = launch.launch(g, tmp_path / "r", adapter=ScriptedAdapter({"a": OK, "router": router}, repeat_last=True),
                        models=models, options={"expect_graph_hash": h})
    assert eng.g.hash == h
    assert json.loads((tmp_path / "r" / "graph.json").read_text()) == g.doc
    assert eng.router_overlay == {"route": ["opus"]}
    await eng.run()
    start = record.read(tmp_path / "r" / "record.jsonl")[0]
    assert start["graph_hash"] == h and start["router_overlay"] == {"route": ["opus"]}


@pytest.mark.anyio
async def test_a_resume_after_the_router_window_closes_reads_the_copy_and_completes(tmp_path):    # SPEC 505
    models = _router_models(tmp_path, [{"id": "opus"}, {"id": "sonnet", "hrs": _window(0)}])
    nodes = [agent("a", output_key="x"), {"name": "ask", "kind": "wait", "owner": "rafael", "deadline": 100, "default": "pass"},
             _dispatch_node(["opus", "sonnet"], when={"fact": "flag", "op": "eq", "value": True}),
             _router_agent(), scope_gate(fail="exit:fail")]
    eng = launch.launch(from_doc(doc(nodes, ["a", "ask", "route"])), tmp_path / "r",
                        adapter=ScriptedAdapter({"a": OK}, repeat_last=True), models=models)
    assert (await eng.run()).exit_code == 10
    frozen = json.loads((tmp_path / "r" / "graph.json").read_text())
    assert _allow(frozen) == ["opus", "sonnet"]
    # The clock moves past sonnet's window: the same file now excludes it. The resume reads the run folder copy,
    # so neither the clock nor a recomputed overlay can move the graph hash out from under the record.
    models = _router_models(tmp_path, [{"id": "opus"}, {"id": "sonnet", "hrs": _window(1)}])
    script = tmp_path / "s.json"
    script.write_text(json.dumps({"script": {"a": OK}, "repeat_last": True}))
    argv = ["resume", "r", "--run-dir", str(tmp_path / "r"), "--script", str(script), "--answer", "pass"]
    assert await cli._resume(cli.parser().parse_args(argv)) == 0
    assert _allow(json.loads((tmp_path / "r" / "graph.json").read_text())) == ["opus", "sonnet"]
    assert record.read(tmp_path / "r" / "record.jsonl")[0]["graph_hash"] == canon_hash(frozen)


def test_the_loop_launch_passes_the_models_path_and_never_the_filtering_rule():    # SPEC 505
    src = (ROOT / "contrib" / "squad-goal" / "scripts" / "launch.py").read_text()
    assert '"--models"' in src and 'here / "models.json"' in src
    assert "modelreg" not in src          # the overlay lives in the one module that starts a run, never in the script


# A resume refuses a missing or replaced run target (#582, ADR-0107).

BOTH = {"a": {"output": {"x": 1, "y": 1}, "spend": 0.1}, "b": {"output": {"x": 1, "y": 1}, "spend": 0.1}}

def _started_in(tmp_path, target):
    """A run that waited with `target` as its target: the record names it, and the resume has work left."""
    from tests.test_resume import wait_doc
    eng = launch.launch(from_doc(wait_doc()), tmp_path / "r", adapter=ScriptedAdapter(BOTH, repeat_last=True), target=target)
    return eng, from_doc(wait_doc())


async def _wait(tmp_path, target):
    eng, g = _started_in(tmp_path, target)
    assert (await eng.run()).exit_code == 10
    return g


def _own_worktree(tmp_path):
    from tests.gitrepo import make_repo, sh
    main = make_repo(tmp_path / "owner")
    wt = tmp_path / "own"
    sh(main, "git", "worktree", "add", "-q", "-b", "squad/run-x", str(wt))
    return main, wt


def _resume(tmp_path, g, **kw):
    return launch.resume_engine(g, tmp_path / "r", adapter=ScriptedAdapter(BOTH, repeat_last=True), **kw)


@pytest.mark.anyio
async def test_a_resume_in_the_run_s_own_worktree_on_its_branch_goes_on(tmp_path):
    _, wt = _own_worktree(tmp_path)
    g = await _wait(tmp_path, wt)
    assert (await _resume(tmp_path, g, target=wt).resume(answer="pass")).exit_code == 0


@pytest.mark.anyio
async def test_a_resume_refuses_a_target_that_is_missing(tmp_path):
    import shutil

    from squad.engine import ResumeRefused
    _, wt = _own_worktree(tmp_path)
    g = await _wait(tmp_path, wt)
    shutil.rmtree(wt)
    with pytest.raises(ResumeRefused, match="missing"):
        _resume(tmp_path, g, target=wt)


@pytest.mark.anyio
async def test_a_resume_refuses_a_target_on_another_branch(tmp_path):
    from squad.engine import ResumeRefused
    from tests.gitrepo import sh
    _, wt = _own_worktree(tmp_path)
    g = await _wait(tmp_path, wt)
    sh(wt, "git", "checkout", "-q", "-b", "other")
    with pytest.raises(ResumeRefused, match="branch"):
        _resume(tmp_path, g, target=wt)


@pytest.mark.anyio
async def test_a_resume_refuses_a_record_that_names_the_owner_s_checkout(tmp_path):
    from squad.engine import ResumeRefused
    main, _ = _own_worktree(tmp_path)
    g = await _wait(tmp_path, main)
    with pytest.raises(ResumeRefused, match="owner's checkout"):
        _resume(tmp_path, g, target=main)
    with pytest.raises(ResumeRefused, match="owner's checkout"):
        _resume(tmp_path, g)    # the CLI hands the recorded target when none is named
