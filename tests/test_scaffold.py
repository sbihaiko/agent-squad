"""Guards for tickets #45 and #46: the scaffold copies node-level defaults and the role prompt into the graph; the adapter appends the role text."""
import json

import pytest

from squad import cli, defaults, graph, scaffold
from tests.flows import doc
from tests.test_sdk_adapter import Options, ResultMessage, request, stub

CFG = defaults.load()
ROLES = sorted(CFG["roles"])


def in_graph(node):
    return graph.from_doc(doc([{**node, "next": {"fail": "repeat"}}], [node["name"]]))


@pytest.mark.parametrize("role", ROLES)
def test_every_role_scaffolds_a_node_the_validator_accepts(role):
    n = scaffold.agent_node(CFG, role)
    assert in_graph(n) and n["model"] == "claude-sonnet-5-5" and n["effort"] == "medium" and n["instructions"].startswith("# Role:")


def test_a_read_only_role_gets_no_write_tool_and_the_actor_does():
    assert not {"Edit", "Write", "NotebookEdit"} & set(scaffold.agent_node(CFG, "critic")["tools"])
    assert {"Edit", "Write"} <= set(scaffold.agent_node(CFG, "actor")["tools"])


def test_caps_come_from_the_limits_block_and_the_name_can_differ_from_the_role():
    n = scaffold.agent_node(CFG, "actor", "actor_2")
    assert n["name"] == "actor_2" and n["budget"] == {"stalls": {"min": 1, "max": 5}, "spend": {"min": 0.05, "max": 2.0}} and n["deadline"] == 600


def test_the_graph_hash_covers_the_copied_values_so_a_later_defaults_edit_changes_no_graph():
    n = scaffold.agent_node(CFG, "scout")
    h = in_graph(n).hash
    CFG2 = json.loads(json.dumps(CFG))
    CFG2["options"]["model"] = "claude-haiku-4-5-20251001"
    assert in_graph(n).hash == h and in_graph(scaffold.agent_node(CFG2, "scout")).hash != h


def test_an_unknown_role_is_refused():
    with pytest.raises(scaffold.ScaffoldError):
        scaffold.agent_node(CFG, "wizard")


def test_the_cli_prints_a_node_and_refuses_an_unknown_role(capsys):
    assert cli.main(["new-node", "critic", "--name", "review"]) == 0
    assert json.loads(capsys.readouterr().out)["name"] == "review"
    assert cli.main(["new-node", "wizard"]) == cli.USAGE


@pytest.mark.anyio
async def test_the_adapter_appends_the_role_text_after_the_shared_contract():
    from squad.sdk_adapter import SdkAdapter
    seen = []
    a = SdkAdapter.from_defaults(CFG, query=stub([ResultMessage(result="{}")], seen), options_cls=Options, hook_cls=lambda hooks, timeout: (hooks, timeout))
    await a.call(request(instructions="ROLE TEXT"))
    shared = (defaults.ROOT / "prompts/_contract.md").read_text() + "\n" + defaults.AUTONOMY_FRAGMENT.read_text()   # autonomy is on by default
    sp = seen[0][1].kw["system_prompt"]
    assert sp["preset"] == "claude_code" and sp["append"] == shared + "\n\nROLE TEXT"
    seen.clear()
    await a.call(request())
    assert seen[0][1].kw["system_prompt"]["append"] == shared
