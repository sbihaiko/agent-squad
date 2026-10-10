"""The repo is a Claude Code plugin: an editor, a dashboard and a skill-update command, and a work and a task command that start a run (workflow ids in docs/squad/workflows/)."""
import json
import re
from pathlib import Path

import pytest

from squad import cli, workflows
from squad.graph import from_doc
from tests.test_cli import OK, two_agents

ROOT = Path(__file__).resolve().parent.parent
SEED = json.loads(workflows.DYNAMIC_SEED.read_text())


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_the_manifest_and_the_commands_exist():
    m = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
    assert m["name"] == "agent-squad"
    for name, call in (("editor", "squad editor"), ("work", "squad start"), ("task", "squad start --task"), ("dashboard", "squad dashboard"),
                       ("skill-update", "python -m squad.skillpath --check"), ("setup", "squad setup"), ("config", "squad config")):
        text = (ROOT / "commands" / f"{name}.md").read_text()
        assert re.search(r"^description: .+", text, re.M) and call in text and "${CLAUDE_PLUGIN_ROOT}" in text


def test_no_id_means_the_default_workflow(project):
    assert workflows.resolve() == workflows.DYNAMIC_SEED
    mine = workflows.for_editor()
    assert mine == project / "docs/squad/workflows/default.graph.json" and json.loads(mine.read_text()) == SEED
    assert workflows.resolve() == mine   # the owner's copy now wins
    assert workflows.resolve("default") == mine and workflows.resolve("dynamic") == mine   # dynamic is an alias


def test_dynamic_resolves_to_default_with_no_file(project):
    assert workflows.resolve("default") == workflows.DYNAMIC_SEED == workflows.resolve("dynamic")


def test_a_project_dynamic_file_wins_when_no_default_file_exists(project):
    old = project / "docs/squad/workflows/dynamic.graph.json"
    old.parent.mkdir(parents=True)
    old.write_text(json.dumps(SEED))
    assert workflows.resolve() == old and workflows.resolve("default") == old and workflows.for_editor() == old
    new = project / "docs/squad/workflows/default.graph.json"
    new.write_text(json.dumps(SEED))
    assert workflows.resolve() == new and workflows.resolve("dynamic") == new   # a default file wins over dynamic


def test_the_editor_creates_a_new_id_from_the_seed(project):
    path = workflows.for_editor("fresh")
    assert path == project / "docs/squad/workflows/fresh.graph.json" and json.loads(path.read_text()) == SEED


def test_an_unknown_id_lists_the_known_ones(project):
    workflows.for_editor()
    with pytest.raises(workflows.WorkflowError, match=r"no workflow 'nope'.*known: default"):
        workflows.resolve("nope")


@pytest.mark.parametrize("bad", ["../x", "a/b", ".hidden", ""])
def test_an_id_never_leaves_the_folder(project, bad):
    with pytest.raises(workflows.WorkflowError, match="not a workflow id"):
        workflows.resolve(bad)


@pytest.mark.parametrize("kind", ["work", "task"])
def test_the_request_reaches_the_entry_agent_and_the_graph_stays_valid(kind):
    g = workflows.with_request(SEED, "add a retry to the uploader", kind)
    assert "add a retry to the uploader" in workflows.entry_agent(g)["instructions"] and workflows.KINDS[kind] in workflows.entry_agent(g)["instructions"]
    assert "add a retry to the uploader" not in workflows.entry_agent(SEED)["instructions"]   # the source is not changed
    from_doc(g)


def test_an_empty_request_is_refused():
    with pytest.raises(workflows.WorkflowError, match="needs text"):
        workflows.with_request(SEED, "  ")


def test_editor_prints_the_file_and_the_save_folder(project, capsys):
    assert cli.main(["editor", "--no-open"]) == 0
    out = capsys.readouterr().out
    assert str(project / "docs/squad/workflows/default.graph.json") in out and str(project / "docs/squad/workflows") in out
    assert cli.main(["editor", "fresh", "--no-open"]) == 0 and (project / "docs/squad/workflows/fresh.graph.json").is_file()
    assert cli.main(["editor", "../x", "--no-open"]) == cli.USAGE


def test_start_runs_the_named_workflow_with_the_request_in_the_run_folder(project, capsys):
    d = two_agents()
    agent_a = next(n for n in d["nodes"] if n["name"] == "a")
    agent_a["instructions"] = "role text"
    (project / "docs/squad/workflows").mkdir(parents=True)
    (project / "docs/squad/workflows/duo.graph.json").write_text(json.dumps(d))
    script = project / "s.json"
    script.write_text(json.dumps({"script": {"a": OK, "b": OK}, "repeat_last": True}))
    run = project / "r"
    assert cli.main(["start", "ship", "it", "--task", "--workflow", "duo", "--script", str(script), "--run-dir", str(run)]) == 0
    ran = json.loads((run / "graph.json").read_text())
    text = next(n for n in ran["nodes"] if n["name"] == "a")["instructions"]
    assert text.startswith("role text\n\n") and text.endswith("Task:\nship it")
    assert cli.main(["start", "x", "--workflow", "nope"]) == cli.USAGE


@pytest.mark.parametrize("name", ["work", "task"])
def test_a_start_command_installs_the_sdk_extra(name):
    # `uv run --project` installs only the base deps; a run dies on its first agent call without the sdk extra.
    line = next(x for x in (ROOT / "commands" / f"{name}.md").read_text().splitlines() if "squad start" in x)
    assert "--extra sdk" in line


@pytest.mark.parametrize("name", ["work", "task"])
def test_a_start_command_passes_the_request_in_a_quoted_here_document(name):    # #164: no character of the request reaches the shell
    text = (ROOT / "commands" / f"{name}.md").read_text()
    assert "--stdin <<'SQUAD_REQUEST'\n$ARGUMENTS\nSQUAD_REQUEST" in text and 'target "$PWD" $ARGUMENTS' not in text


def test_start_reads_a_request_with_quotes_and_shell_characters_from_stdin(project, monkeypatch):
    import io
    d = two_agents()
    (project / "docs/squad/workflows").mkdir(parents=True)
    (project / "docs/squad/workflows/duo.graph.json").write_text(json.dumps(d))
    script = project / "s.json"
    script.write_text(json.dumps({"script": {"a": OK, "b": OK}, "repeat_last": True}))
    run = project / "r"
    monkeypatch.setattr("sys.stdin", io.StringIO("fix \"it\"; echo $HOME & more --workflow duo\n"))
    assert cli.main(["start", "--stdin", "--task", "--script", str(script), "--run-dir", str(run)]) == 0
    ran = json.loads((run / "graph.json").read_text())
    assert next(n for n in ran["nodes"] if n["name"] == "a")["instructions"].endswith('fix "it"; echo $HOME & more')


def test_the_plugin_ships_a_spec_to_build_workflow_that_runs_the_skills_the_owner_names(project):    # #160
    assert "spec-to-build" in workflows.ids()
    path = workflows.resolve("spec-to-build")
    doc = workflows.load(path)
    from squad.skills import graph_skills
    assert {"to-spec", "to-tickets", "tdd"} <= graph_skills(doc)
    from_doc(doc)
    assert workflows.unrunnable_skills(doc, "start by /to-spec, then /to-tickets") == []


def test_a_request_that_names_a_skill_the_workflow_cannot_run_gets_a_warning(project, capsys):
    assert workflows.unrunnable_skills(SEED, "run /to-spec and /to-tickets in /tmp/x") == ["to-spec", "to-tickets"]
    assert workflows.unrunnable_skills(SEED, "use /tdd for it") == []


@pytest.mark.parametrize("text", ["implement-spec", "/to-spec", " /Implement-Spec "])
def test_a_request_that_is_only_a_skill_name_is_refused_before_any_call(project, capsys, text):    # #165
    assert workflows.names_only_a_skill("implement-spec for #20") is False
    assert cli.main(["start", text]) == cli.USAGE
    assert "names a skill and no work" in capsys.readouterr().err
    assert not (project / "runs").exists()


def test_the_record_and_the_design_readme_say_cap_is_a_spend_in_dollars():    # #166
    assert "spend granted to the call" in (ROOT / "squad" / "record.py").read_text()
    assert "not a turn cap" in (ROOT / "docs" / "design" / "README.md").read_text()


def test_the_run_list_and_the_open_line_show_the_request(project, capsys):    # #167
    from squad import runs
    d = two_agents()
    (project / "docs/squad/workflows").mkdir(parents=True)
    (project / "docs/squad/workflows/duo.graph.json").write_text(json.dumps(d))
    script = project / "s.json"
    script.write_text(json.dumps({"script": {"a": OK, "b": OK}, "repeat_last": True}))
    assert cli.main(["start", "ship", "the", "thing", "--workflow", "duo", "--script", str(script), "--run-dir", str(project / "runs" / "r1")]) == 0
    capsys.readouterr()
    assert runs.request_of(project / "runs" / "r1") == "ship the thing"
    assert cli.main(["dashboard", "--runs-dir", str(project / "runs"), "--list"]) == 0
    assert capsys.readouterr().out.rstrip().endswith("ship the thing")
    assert runs.request_of(project / "none") == ""


@pytest.mark.parametrize("name", ["work", "task"])
def test_a_start_command_passes_the_run_name_through_the_here_document(name):    # #189
    text = (ROOT / "commands" / f"{name}.md").read_text()
    assert "--name <name>" in text and 'target "$PWD" --name' not in text
    assert "--stdin <<'SQUAD_REQUEST'\n$ARGUMENTS\nSQUAD_REQUEST" in text


def test_start_reads_the_name_from_stdin_and_refuses_a_bad_one(project, monkeypatch, capsys):    # #189
    import io
    (project / "docs/squad/workflows").mkdir(parents=True)
    (project / "docs/squad/workflows/duo.graph.json").write_text(json.dumps(two_agents()))
    script = project / "s.json"
    script.write_text(json.dumps({"script": {"a": OK, "b": OK}, "repeat_last": True}))
    runs_dir = project / "runs"
    monkeypatch.setattr("sys.stdin", io.StringIO("fix it --workflow duo --name my-run.1\n"))
    assert cli.main(["start", "--stdin", "--script", str(script), "--runs-dir", str(runs_dir)]) == 0
    assert (runs_dir / "my-run.1").is_dir()
    for bad in ("$(touch$IFS/tmp/x)", "a;b"):
        monkeypatch.setattr("sys.stdin", io.StringIO(f"fix it --name {bad}\n"))
        assert cli.main(["start", "--stdin", "--script", str(script), "--runs-dir", str(runs_dir)]) == cli.USAGE
        assert "is not a run name" in capsys.readouterr().err


@pytest.mark.parametrize("name", ["work", "task"])
def test_a_command_resumes_a_needs_human_run_through_the_autonomy_skill(name):
    text = (ROOT / "commands" / f"{name}.md").read_text()
    assert "`autonomy` skill" in text and "exit code 10" in text.lower() and "max_resumes" in text and "--auto-source autonomy" in text
    assert re.search(r"^allowed-tools: .*\bSkill\b", text, re.M)   # ticket #182: the skill call itself is never run in tests


def test_the_setup_command_names_the_cli_the_door_and_the_lookup_and_copies_no_skill_text():    # #197
    text = (ROOT / "commands" / "setup.md").read_text()
    for part in ("squad setup --target", "--apply", "agent-setup-matt-pocock-skills", "python3 -m squad.skillpath setup-matt-pocock-skills", "autonomy:panel", "Commit once", "leave them staged"):
        assert part in text, part
    assert "~/.claude/skills" not in text and len(text.splitlines()) < 30   # a pointer, not a copy of the skill
    assert (ROOT / ".claude/skills/agent-setup-matt-pocock-skills").is_symlink()


@pytest.mark.parametrize("name", ["work", "task"])
def test_work_and_task_start_in_the_background_and_name_the_watch(name):   # #186
    text = (ROOT / "commands" / f"{name}.md").read_text()
    assert "run_in_background" in text and "monitor.log" in text and "dashboard: <page URL>" in text and "run folder:" in text
    assert "exit code" in text and "Do not retry a failed run" in text and "--name" in text   # the old rules stay


def test_the_glossary_defines_watch():
    assert re.search(r"^\*\*Watch\*\*:", (ROOT / "GLOSSARY.md").read_text(), re.M)


def test_the_config_command_names_each_subcommand_and_passes_them_in_a_quoted_here_document():    # #179
    text = (ROOT / "commands" / "config.md").read_text()
    for sub in ("show", "set", "reset", "init", "check"):
        assert f"`{sub}" in text
    assert '--target "$PWD" --stdin <<\'SQUAD_CONFIG\'\n$ARGUMENTS\nSQUAD_CONFIG' in text
    assert "/agent-squad:config" in (ROOT / "README.md").read_text() and "/agent-squad:config" in (ROOT / "CLAUDE.md").read_text()


def test_config_reads_its_subcommand_from_stdin(project, monkeypatch, capsys):    # #179
    import io
    monkeypatch.setattr("sys.stdin", io.StringIO("set limits.max_stalls 40\n"))
    assert cli.main(["config", "--target", str(project), "--stdin"]) == 0
    assert "set: limits.max_stalls" in capsys.readouterr().out
    monkeypatch.setattr("sys.stdin", io.StringIO("check\n"))
    assert cli.main(["config", "--target", str(project), "--stdin"]) == 0
