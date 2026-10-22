"""Guards for ticket #181: the output contract carries the autonomy fragment only when the setting is on."""
from pathlib import Path

import pytest

from squad import defaults, record
from squad.adapter import ScriptedAdapter
from squad.engine import Engine
from squad.graph import from_doc
from tests.flows import agent, doc, run_engine

CFG = defaults.load()
CONTRACT = (defaults.ROOT / "prompts/_contract.md").read_text()
FRAGMENT = defaults.AUTONOMY_FRAGMENT.read_text()


def append_of(cfg):
    return defaults.sdk_kwargs(cfg)["system_prompt"]["append"]


def test_default_on_puts_the_fragment_in_the_system_prompt_and_off_leaves_it_out():
    assert CFG["autonomy"]["enabled"] is True
    on = append_of(CFG)
    assert on.startswith(CONTRACT) and FRAGMENT in on and "provisional:" in on
    off = append_of({**CFG, "autonomy": {"enabled": False, "max_resumes": 3}})
    assert off == CONTRACT and "provisional:" not in off


def test_the_fragment_tells_the_agent_to_decide_alone_cheapest_to_reverse_and_keeps_needs_human():
    for phrase in ("Decide alone", "cheapest to reverse", "`provisional:`", "`needs-human`"):
        assert phrase in FRAGMENT


def test_the_owner_only_list_in_the_fragment_equals_the_list_in_the_project_claude_md():
    claude = (Path(__file__).resolve().parent.parent / "CLAUDE.md").read_text()
    assert defaults.owner_only(claude) and defaults.owner_only(claude) == defaults.owner_only()


@pytest.mark.anyio
async def test_provisional_reasons_on_a_pass_stay_in_the_record(tmp_path):
    out = {"x": 1, "status": "pass", "reasons": ["provisional: chose the older API"]}
    v = await run_engine(doc([agent("a", output_key="x")], ["a"]), {"a": {"output": out, "spend": 0.1}}, tmp_path).run()
    assert v.verdict == "completed"
    outs = [e for e in record.read(tmp_path / "record.jsonl") if e["event"] == "outcome" and e["node"] == "a"]
    assert outs[0]["outcome"] == "pass" and outs[0]["reasons"] == ["provisional: chose the older API"]


@pytest.mark.anyio
async def test_needs_human_still_ends_the_run_with_exit_10_when_autonomy_is_on(tmp_path):
    out = {"x": 1, "status": "needs-human", "reasons": ["provisional: kept the default", "which account pays?"]}
    eng = Engine(from_doc(doc([agent("a", output_key="x")], ["a"])), ScriptedAdapter({"a": {"output": out, "spend": 0.1}}), tmp_path, autonomy={"enabled": True, "max_resumes": 3})
    v = await eng.run()
    assert (v.verdict, v.exit_code) == ("needs-human", 10) and "which account pays?" in v.reasons


@pytest.mark.anyio
async def test_a_wait_node_makes_no_model_call_so_the_fragment_does_not_reach_it(tmp_path):
    w = {"name": "ask", "kind": "wait", "owner": "r", "deadline": 100, "default": "pass"}
    eng = Engine(from_doc(doc([w], ["ask"])), ScriptedAdapter({}), tmp_path, autonomy={"enabled": True, "max_resumes": 3})
    await eng.run()
    assert eng.adapter.calls == []


def test_the_fragment_and_claude_md_grant_issue_status_comments():
    claude = (Path(__file__).resolve().parent.parent / "CLAUDE.md").read_text()
    for text in (FRAGMENT, claude):
        assert "status comment" in text and "sbihaiko/agent-squad" in text and "not external publication" in text
