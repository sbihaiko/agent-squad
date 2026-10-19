"""Guards for ticket #46: the engine-level defaults load per invocation, reach the SDK options, and are recorded."""

import pytest

from squad import cli, defaults, record
from squad.sdk_adapter import SdkAdapter
from tests.test_cli import two_agents, write
from tests.test_sdk_adapter import Options, ResultMessage, request, stub

pytestmark = pytest.mark.anyio
CFG = defaults.load()


class Matcher:
    def __init__(self, matcher=None, hooks=(), timeout=None):    # the SDK HookMatcher's fields
        self.matcher, self.hooks, self.timeout = matcher, list(hooks), timeout


def built(sink=None):
    seen = []
    a = SdkAdapter.from_defaults(CFG, query=stub([ResultMessage(result="{}")], seen), options_cls=Options, hook_cls=Matcher)
    if sink:
        a.bind(sink)
    return a, seen


async def test_the_engine_level_keys_reach_the_sdk_options_and_the_node_keys_do_not_change():
    a, seen = built()
    await a.call(request())
    kw = seen[0][1].kw
    assert kw["permission_mode"] == "bypassPermissions" and kw["setting_sources"] == ["project"]
    assert kw["disallowed_tools"] == ["WebFetch", "WebSearch", "Task", "Agent"] and kw["strict_mcp_config"] is True
    assert kw["plugins"] == [{"type": "local", "path": str(defaults.ROOT / "vendor/skills")}]
    assert kw["system_prompt"]["preset"] == "claude_code" and kw["system_prompt"]["append"] == (defaults.ROOT / "prompts/_contract.md").read_text() + "\n" + defaults.AUTONOMY_FRAGMENT.read_text()
    assert kw["env"]["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "32000" and kw["env"]["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] == "200000"
    assert (kw["model"], kw["effort"], kw["max_budget_usd"], kw["tools"]) == ("claude-sonnet-5-5", "medium", 2.0, ["Read", "Edit"])
    assert "max_turns" not in kw and "max_stalls" not in kw


async def test_a_precompact_hook_logs_each_compaction_to_the_run_record_with_its_timeout():
    events = []
    a, seen = built(lambda kind, **f: events.append((kind, f)))
    await a.call(request())
    m = seen[0][1].kw["hooks"]["PreCompact"][0]
    assert m.timeout == 60
    await m.hooks[0]({"trigger": "auto"}, None, None)
    assert events == [("compaction", {"node": "actor", "entry": 1, "item": None, "session": "run-s1", "trigger": "auto"})]


async def test_no_compaction_hook_is_set_when_compaction_logging_is_off():
    """The sandbox wall (PreToolUse) is on every session; only the compaction log follows the setting (#659)."""
    a = SdkAdapter(query=stub([ResultMessage(result="{}")]), options_cls=Options)
    assert "PreCompact" not in a._hooks(request())


def test_a_compaction_event_needs_its_fields():
    with pytest.raises(record.RecordError):
        record.validate_event({"event": "compaction", "node": "a"})
    record.validate_event({"event": "compaction", "node": "a", "entry": 1, "session": "s", "trigger": "auto"})


def test_run_start_records_the_defaults_file_hash_and_the_engine_level_keys(tmp_path):
    g, s = write(tmp_path, two_agents())
    assert cli.main(["run", g, "--script", s, "--run-dir", str(tmp_path / "r"), "--defaults", str(defaults.PATH)]) == 0
    start = record.read(tmp_path / "r" / "record.jsonl")[0]
    assert start["defaults"]["sha256"] == defaults.digest() and start["defaults"]["path"] == str(defaults.PATH)
    assert start["defaults"]["engine"]["options"]["permission_mode"] == "bypassPermissions"
    assert start["defaults"]["engine"]["env"]["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] == "200000"
    assert "model" not in start["defaults"]["engine"]["options"]       # a node-level key never rides the engine load


@pytest.mark.heavy
def test_run_start_carries_the_dashboard_reread_interval_from_the_defaults_file(tmp_path):
    assert CFG["dashboard"]["reread_seconds"] == 1
    g, s = write(tmp_path, two_agents())
    assert cli.main(["run", g, "--script", s, "--run-dir", str(tmp_path / "r"), "--defaults", str(defaults.PATH)]) == 0
    start = record.read(tmp_path / "r" / "record.jsonl")[0]
    assert start["defaults"]["engine"]["dashboard"] == {"reread_seconds": 1, "watch_seconds": 0.2}
    slow = tmp_path / "slow.yaml"
    slow.write_text(defaults.PATH.read_text().replace("reread_seconds: 1 ", "reread_seconds: 5 "))
    (tmp_path / "two").mkdir()
    g2, s2 = write(tmp_path / "two", two_agents())
    assert cli.main(["run", g2, "--script", s2, "--run-dir", str(tmp_path / "r2"), "--defaults", str(slow)]) == 0
    assert record.read(tmp_path / "r2" / "record.jsonl")[0]["defaults"]["engine"]["dashboard"] == {"reread_seconds": 5, "watch_seconds": 0.2}


@pytest.mark.parametrize("bad_value", ["0", "-1", "null", '"fast"'])
def test_an_unsound_watch_step_refuses_the_run(tmp_path, bad_value):
    """Ticket #105: the server's watch step is a setting, and a step that is not a positive number refuses the run."""
    bad = tmp_path / "bad.yaml"
    bad.write_text(defaults.PATH.read_text().replace("watch_seconds: 0.2 ", f"watch_seconds: {bad_value} "))
    assert "dashboard.watch_seconds" in defaults.check(defaults.load(bad))
    g, s = write(tmp_path, two_agents())
    assert cli.main(["run", g, "--script", s, "--run-dir", str(tmp_path / "r"), "--defaults", str(bad)]) == cli.USAGE


@pytest.mark.parametrize("bad_value", ["0", "-1", "null", '"fast"'])
def test_an_unsound_reread_interval_refuses_the_run(tmp_path, bad_value):
    bad = tmp_path / "bad.yaml"
    bad.write_text(defaults.PATH.read_text().replace("reread_seconds: 1 ", f"reread_seconds: {bad_value} "))
    assert "dashboard.reread_seconds" in defaults.check(defaults.load(bad))
    g, s = write(tmp_path, two_agents())
    assert cli.main(["run", g, "--script", s, "--run-dir", str(tmp_path / "r"), "--defaults", str(bad)]) == cli.USAGE


def test_the_progress_interval_is_a_setting_and_an_unsound_one_refuses_the_run(tmp_path):
    """How often a call in flight tells the record where it is is a setting, checked like the reread interval."""
    assert CFG["progress"]["seconds"] == 10
    assert defaults.check(CFG) == []
    for bad_value in ["-1", "-0.5", '"often"', "true"]:
        bad = tmp_path / "bad.yaml"
        bad.write_text(defaults.PATH.read_text().replace("seconds: 10 ", f"seconds: {bad_value} "))
        assert "progress.seconds" in defaults.check(defaults.load(bad)), bad_value
    off = tmp_path / "off.yaml"
    off.write_text(defaults.PATH.read_text().replace("seconds: 10 ", "seconds: null "))
    assert defaults.check(defaults.load(off)) == []                   # null is the setting: write no progress event at all
    no_progress = {k: v for k, v in CFG.items() if k != "progress"}
    assert "progress.seconds" not in defaults.check(no_progress)      # an older file still loads


def test_a_defaults_file_without_the_dashboard_block_still_loads_and_records_no_interval():
    cfg = {k: v for k, v in CFG.items() if k != "dashboard"}
    assert "dashboard.reread_seconds" not in defaults.check(cfg)
    assert "dashboard" not in defaults.engine_view(cfg)


def test_a_run_with_no_defaults_flag_and_no_sdk_records_none(tmp_path):
    g, s = write(tmp_path, two_agents())
    assert cli.main(["run", g, "--script", s, "--run-dir", str(tmp_path / "r")]) == 0
    assert "defaults" not in record.read(tmp_path / "r" / "record.jsonl")[0]


def test_an_unsound_defaults_file_refuses_the_run(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(defaults.PATH.read_text().replace("effort: medium", "effort: nonsense"))
    g, s = write(tmp_path, two_agents())
    assert cli.main(["run", g, "--script", s, "--run-dir", str(tmp_path / "r"), "--defaults", str(bad)]) == cli.USAGE
    assert not (tmp_path / "r" / "record.jsonl").exists()


def test_the_engine_hands_its_record_to_an_adapter_so_a_compaction_lands_in_the_run_record(tmp_path):
    from squad.engine import Engine
    from squad.graph import from_doc
    a, _ = built()
    Engine(from_doc(two_agents()), a, tmp_path)
    a.sink("compaction", node="a", entry=1, item=None, session="s", trigger="auto")
    assert [e["event"] for e in record.read(tmp_path / "record.jsonl")] == ["compaction"]
