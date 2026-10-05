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


def test_the_manifest_and_the_five_commands_exist():
    m = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
    assert m["name"] == "agent-squad"
    for name, call in (("editor", "squad editor"), ("work", "squad start"), ("task", "squad start --task"), ("dashboard", "squad dashboard"),
                       ("skill-update", "python -m squad.skillpath --check")):
        text = (ROOT / "commands" / f"{name}.md").read_text()
        assert re.search(r"^description: .+", text, re.M) and call in text and "${CLAUDE_PLUGIN_ROOT}" in text


def test_no_id_means_the_dynamic_workflow(project):
    assert workflows.resolve() == workflows.DYNAMIC_SEED
    mine = workflows.for_editor()
    assert mine == project / "docs/squad/workflows/dynamic.graph.json" and json.loads(mine.read_text()) == SEED
    assert workflows.resolve() == mine   # the owner's copy now wins


def test_an_unknown_id_lists_the_known_ones(project):
    workflows.for_editor()
    with pytest.raises(workflows.WorkflowError, match=r"no workflow 'nope'.*known: dynamic"):
        workflows.resolve("nope")


@pytest.mark.parametrize("bad", ["../x", "a/b", ".hidden", ""])
def test_an_id_never_leaves_the_folder(project, bad):
    with pytest.raises(workflows.WorkflowError, match="not a workflow id"):
        workflows.resolve(bad)


@pytest.mark.parametrize("kind", ["work", "task"])
def test_the_request_reaches_the_entry_agent_and_the_graph_stays_valid(kind):
    g = workflows.with_request(SEED, "add a retry to the uploader", kind)
    assert "add a retry to the uploader" in workflows.entry_agent(g)["instructions"] and workflows.KINDS[kind] in workflows.entry_agent(g)["instructions"]
    assert "instructions" not in workflows.entry_agent(SEED)   # the source is not changed
    from_doc(g)


def test_an_empty_request_is_refused():
    with pytest.raises(workflows.WorkflowError, match="needs text"):
        workflows.with_request(SEED, "  ")


def test_editor_prints_the_file_and_the_save_folder(project, capsys):
    assert cli.main(["editor", "--no-open"]) == 0
    out = capsys.readouterr().out
    assert str(project / "docs/squad/workflows/dynamic.graph.json") in out and str(project / "docs/squad/workflows") in out
    assert cli.main(["editor", "nope", "--no-open"]) == cli.USAGE


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
