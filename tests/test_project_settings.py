"""The project settings file overlays the plugin defaults.

Guards: a project value reaches the call request through `launch` with the scripted adapter; precedence holds and a node only narrows;
an unknown key, a credential key, an out-of-range value and a context window below 100000 are refused naming the key;
the run start record carries the file path and hash; a resume refuses when the file changed; the file lives in the project, so a plugin update leaves it.
"""
import json
from pathlib import Path

import pytest
import yaml

from squad import defaults, launch, project_settings, record
from squad.adapter import ScriptedAdapter
from squad.engine import ResumeRefused
from squad.graph import from_doc
from squad.options import OptionError
from tests.flows import agent, doc

OK = {"output": {"x": 1}, "spend": 0.1}


def project(tmp_path, data):
    f = tmp_path / "proj" / "docs" / "squad" / "defaults.yaml"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(yaml.safe_dump(data) if not isinstance(data, str) else data)
    return tmp_path / "proj"


def graph(**kw):
    return from_doc(doc([agent("a", output_key="x", next={"fail": "exit:fail"}, **kw)], ["a"]))


async def request(tmp_path, data, **kw):
    ad = ScriptedAdapter({"a": OK})
    eng = launch.launch(graph(**kw), tmp_path / "r", adapter=ad, target=project(tmp_path, data))
    await eng.run()
    return ad.calls[0], eng


@pytest.mark.anyio
async def test_a_project_value_for_output_tokens_context_and_a_call_cap_reaches_the_call_request(tmp_path):
    req, _ = await request(tmp_path, {"limits": {"max_output_tokens": 64000, "context_tokens": 300000, "max_stalls": 4}})
    assert (req.max_output_tokens, req.context_tokens, req.max_stalls) == (64000, 300000, 4)


@pytest.mark.anyio
async def test_no_project_file_leaves_the_request_to_the_plugin_defaults(tmp_path):
    ad = ScriptedAdapter({"a": OK})
    eng = launch.launch(graph(), tmp_path / "r", adapter=ad, target=tmp_path)
    await eng.run()
    req = ad.calls[0]
    assert (req.max_output_tokens, req.context_tokens, req.max_stalls) == (None, None, 10)
    assert "project_settings" not in record.read(tmp_path / "r" / "record.jsonl")[0]


@pytest.mark.anyio
async def test_a_node_can_only_narrow_a_project_cap(tmp_path):
    req, _ = await request(tmp_path, {"limits": {"max_stalls": 20, "max_budget_usd": 5}})
    assert req.max_stalls == 10 and req.max_spend <= 1.0           # the node's own 10 turns and 1.0 USD stay below the project's
    (tmp_path / "again").mkdir()
    req, _ = await request(tmp_path / "again", {"limits": {"max_stalls": 3}})
    assert req.max_stalls == 3                                     # a project cap below the node's narrows the node


@pytest.mark.anyio
async def test_the_project_run_cap_lowers_the_run_block_and_never_raises_it(tmp_path):
    _, eng = await request(tmp_path, {"run": {"spend_cap": 2.5, "max_parallel": 9}})
    assert eng.run_cfg["spend_cap"] == 2.5 and eng.run_cfg["max_parallel"] == 2


def test_the_project_file_overlays_the_plugin_defaults_and_roles_sit_above_it():
    cfg = defaults.load()
    merged, src = project_settings.overlay(cfg, {"limits": {"context_tokens": 400000}, "options": {"effort": "high"}, "roles": {"actor": {"effort": "max"}}})
    assert merged["limits"]["context_tokens"] == 400000 > cfg["limits"]["context_tokens"]       # the project raises a plugin default
    assert merged["options"]["effort"] == "high" and merged["roles"]["actor"]["effort"] == "max"
    assert merged["roles"]["actor"]["skills"] == ["tdd"]                                         # the plugin's role keys stay
    assert src["limits.context_tokens"] == "project" and src["limits.max_stalls"] == "plugin" and src["roles.actor.effort"] == "project"
    assert defaults.check(merged) == []


@pytest.mark.parametrize("data,words", [
    ({"limits": {"nonsense": 1}}, ["limits.nonsense"]),
    ({"auth": {"source": "x"}}, ["auth.source", "credential"]),
    ({"options": {"api_key": "x"}}, ["options.api_key", "credential"]),
    ({"roles": {"actor": {"gateway_url": "x"}}}, ["roles.actor.gateway_url", "credential"]),
    ({"limits": {"max_stalls": -1}}, ["limits.max_stalls", "range"]),
    ({"limits": {"max_stalls": "many"}}, ["limits.max_stalls"]),
    ({"limits": {"context_tokens": 99999}}, ["limits.context_tokens", "100000", "range"]),
    ({"options": {"effort": "huge"}}, ["options.effort", "high, low, max, medium"]),
])
def test_an_unsound_key_or_value_is_refused_naming_the_key_before_a_folder_exists(tmp_path, data, words):
    with pytest.raises(OptionError) as err:
        launch.launch(graph(), tmp_path / "r", adapter=ScriptedAdapter({"a": OK}), target=project(tmp_path, data))
    assert all(w in str(err.value) for w in words), str(err.value)
    assert not (tmp_path / "r").exists()


def test_a_file_that_is_not_yaml_is_refused(tmp_path):
    with pytest.raises(OptionError, match="not valid YAML"):
        project_settings.load(project(tmp_path, "limits: [unclosed"))


@pytest.mark.anyio
async def test_the_run_start_record_carries_the_project_file_path_and_hash(tmp_path):
    _, eng = await request(tmp_path, {"limits": {"max_stalls": 4}})
    f = tmp_path / "proj" / "docs" / "squad" / "defaults.yaml"
    start = record.read(tmp_path / "r" / "record.jsonl")[0]
    assert start["project_settings"] == {"path": str(f.resolve()), "sha256": project_settings.digest(f)}


@pytest.mark.anyio
async def test_a_resume_refuses_after_the_project_file_changed_and_writes_no_event(tmp_path):
    from tests.test_resume import wait_doc
    proj = project(tmp_path, {"limits": {"max_stalls": 4}})
    both = {"a": {"output": {"x": 1, "y": 1}, "spend": 0.1}, "b": {"output": {"x": 1, "y": 1}, "spend": 0.1}}
    eng = launch.launch(from_doc(wait_doc()), tmp_path / "r", adapter=ScriptedAdapter(both, repeat_last=True), target=proj)
    assert (await eng.run()).exit_code == 10
    n = len(record.read(tmp_path / "r" / "record.jsonl"))
    same = launch.resume_engine(from_doc(wait_doc()), tmp_path / "r", adapter=ScriptedAdapter(both, repeat_last=True), target=proj)
    assert same.project["record"]["sha256"] == record.read(tmp_path / "r" / "record.jsonl")[0]["project_settings"]["sha256"]
    (proj / "docs" / "squad" / "defaults.yaml").write_text(yaml.safe_dump({"limits": {"max_stalls": 5}}))
    changed = launch.resume_engine(from_doc(wait_doc()), tmp_path / "r", adapter=ScriptedAdapter(both, repeat_last=True), target=proj)
    with pytest.raises(ResumeRefused, match="project settings file .* changed"):
        await changed.resume(answer="pass")
    assert len(record.read(tmp_path / "r" / "record.jsonl")) == n


def test_the_project_file_is_read_from_the_target_so_a_plugin_update_never_touches_it():
    plugin = Path(defaults.PATH).resolve().parent.parent
    assert project_settings.REL == Path("docs/squad/defaults.yaml")
    assert not (plugin / project_settings.REL).exists() or plugin == Path(__file__).resolve().parent.parent
    assert json.dumps(sorted(project_settings.CATALOG)).count("auth") == 0     # the catalog holds no credential key


# config check
def check(tmp_path, data, capsys):
    from squad import cli
    code = cli.main(["config", "--target", str(project(tmp_path, data)), "check"])
    c = capsys.readouterr()
    return code, c.out + c.err


def test_config_check_reports_each_defect_naming_the_key(tmp_path, capsys):
    code, out = check(tmp_path, {"limits": {"context_tokens": 5, "max_stalls": "x"}, "bogus": 1, "options": {"api_key": "k"},
                                 "roles": {"actor": {"effort": "huge"}}}, capsys)
    assert code != 0
    for key in ("limits.context_tokens", "limits.max_stalls", "bogus", "options.api_key", "roles.actor.effort"):
        assert key in out
    assert "credential" in out and "100000" in out


def test_config_check_exits_zero_on_a_clean_file(tmp_path, capsys):
    code, out = check(tmp_path, {"limits": {"context_tokens": 150000}}, capsys)
    assert code == 0 and "ok" in out


def test_config_check_exits_zero_with_no_file(tmp_path, capsys):
    from squad import cli
    assert cli.main(["config", "--target", str(tmp_path), "check"]) == 0


def test_config_check_exits_non_zero_on_bad_yaml(tmp_path, capsys):
    code, out = check(tmp_path, "limits: [", capsys)
    assert code != 0 and "YAML" in out


def test_the_defaults_checker_and_config_check_read_one_catalog():
    cfg = defaults.load()
    cfg["limits"]["context_tokens"] = 5
    assert defaults.check(cfg) == ["limits.context_tokens"]
    assert [k for k, _ in project_settings.defects({"limits": {"context_tokens": 5}})] == ["limits.context_tokens"]
    cfg = defaults.load()
    cfg["limits"]["max_stalls"] = 10 ** 9          # past the catalog's upper bound: the plugin file is held to the same range
    assert defaults.check(cfg) == ["limits.max_stalls"]


def test_this_repository_overlay_caps_runs_and_lowers_the_gate_slots():
    overlay = yaml.safe_load((Path(__file__).resolve().parents[1] / "docs" / "squad" / "defaults.yaml").read_text())
    # Owner ruling: four runs and twelve children. Four runs outran the three gate slots, so a child's entry gate can be
    # queued behind its siblings and reach its bound unproven; the run records that as a wait, never as work lost.
    # The gate slots go below the plugin's twelve: at twelve, three gates held the machine between them, each kept its slots
    # while it waited for the lock, and the queue outlived the takeover bound (#1191). A project may only lower a machine-wide
    # cap, and this one lowers it. Owner 2026-11-19: four slots, because the loop's gate asks two and a run's command asks two,
    # so a landing and a child gate run together; at three the landing took the whole budget and 18 runs waited in one day.
    # Owner 2026-11-19, later: six runs, because the Doing count carries cards the loop never dispatches, and two such
    # cards cost half the dispatch. The gate slots went to five and then back to three: at five two full gates ran at once
    # and every suite took three to four times longer, so two gates together delivered less than one gate alone. Three holds
    # one full gate of two workers and one child gate beside it, and the loop gate never takes the whole budget.
    # Owner 2026-11-20: five runs; six ran ahead of the three gate slots, and a wave check came back unproven. Owner
    # 2026-11-20, later: four gate slots, so the landing's two-slot gate runs beside a hand or child gate.
    assert overlay["caps"] == {"runs": 5, "children": 12, "gates": 4}
    assert overlay["limits"]["max_budget_usd"] == 30


# --- A save changes only the key it sets (#2543). The file is hand-edited: its comments hold the owner's ruling on each
# cap, so a writer that dumps the whole mapping destroys the ruling and copies the personal `.env` tailnet block into a
# tracked, public file.
COMMENTED = """\
# Owner ruling: the caps below are the owner's, written by hand.
caps:
  runs: 5    # five runs: six ran ahead of the three gate slots
  children: 12
# A closing note that belongs to no key.
"""


def test_a_save_changes_only_the_key_it_sets_and_keeps_every_other_line(tmp_path):
    path = Path(tmp_path) / "docs" / "squad" / "defaults.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(COMMENTED)
    assert project_settings.set_key(tmp_path, "caps.runs", "4") is True
    before, after = COMMENTED.splitlines(), path.read_text().splitlines()
    assert len(after) == len(before), path.read_text()
    assert [i for i in range(len(before)) if before[i] != after[i]] == [2], path.read_text()
    assert after[2].startswith("  runs: 4") and "# five runs: six ran ahead of the three gate slots" in after[2]
    assert yaml.safe_load(path.read_text()) == {"caps": {"runs": 4, "children": 12}}


def test_a_save_never_writes_a_tailnet_key_that_came_from_the_env_file(tmp_path):
    (tmp_path / ".env").write_text("SQUAD_TAILNET_HOST=my-mac.example-tailnet.ts.net\nSQUAD_TAILNET_ALLOW=owner@example.com\n")
    path = Path(tmp_path) / "docs" / "squad" / "defaults.yaml"
    path.parent.mkdir(parents=True)
    path.write_text("# Owner ruling: the caps below are the owner's.\ncaps:\n  runs: 5\n")
    assert project_settings.set_key(tmp_path, "caps.runs", "4") is True
    text = path.read_text()
    assert "my-mac.example-tailnet.ts.net" not in text and "owner@example.com" not in text, text
    assert "tailnet" not in text, text
    assert text.startswith("# Owner ruling: the caps below are the owner's.\ncaps:\n  runs: 4\n"), text
