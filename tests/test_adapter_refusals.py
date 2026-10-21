"""GitHub issue #698: refusals the SDK adapter met on the router node.

A weekly limit and an unknown model id are the provider refusing to serve, so the chain moves on, as on a session
limit. A chain entry the SDK adapter cannot reach (an agy-only id) is refused before the run starts. An opaque
ProcessError keeps the tail of the CLI's stderr, so the record says why the CLI died.
"""
import copy

import pytest

from squad import launch, modelreg
from squad.adapter import AdapterError, ScriptedAdapter
from squad.envelope import validate_request
from squad.graph import from_doc
from squad.options import OptionError
from squad.sdk_adapter import SdkAdapter
from squad.session import provider_error
from tests.flows import agent, doc
from tests.helpers import node, router
from tests.test_sdk_adapter import Options, request

AGY_ONLY = "gemini-3.8-flash-high"    # registry id gemini-3.8-flash: its one route is the agy CLI
WEEKLY = ("ResultError: Claude Code returned an error result: "
          "You've hit your weekly limit · resets Oct 24 at 5:46pm (America/Sao_Paulo) (exit code: 1)")
BAD_MODEL = ("ResultError: Claude Code returned an error result: "
             "There's an issue with the selected model (gemini-3.8-flash-high). It may not exist or you may not have access to it.")
OPAQUE = "ProcessError: Command failed with exit code 1 (exit code: 1)\nError output: Check stderr output for details"


def test_a_weekly_limit_is_a_provider_refusal():
    assert provider_error(WEEKLY)


def test_an_issue_with_the_selected_model_is_a_provider_refusal():
    assert provider_error(BAD_MODEL)


def test_an_opaque_process_error_is_not_a_provider_refusal():
    assert not provider_error(OPAQUE)


def test_the_registry_says_an_agy_only_id_has_no_sdk_route():
    assert modelreg.no_sdk_route(AGY_ONLY)
    for served in ("claude-sonnet-5-5", "claude-gpt-5.6-terra", "claude-grok-4.7", "scripted-model"):
        assert not modelreg.no_sdk_route(served), served


def test_the_envelope_refuses_a_model_with_no_sdk_route_even_when_the_allow_list_names_it():
    disp = copy.deepcopy(node(router(), "route"))
    disp["allow"]["models"] = [AGY_ONLY, "claude-sonnet-5-5"]
    raw = {"prompt": "Run it.", "skill": disp["allow"]["skills"][0]["name"], "model": AGY_ONLY, "effort": "medium", "tools": ["Read"]}
    assert f"model_has_no_sdk_route:{AGY_ONLY}" in validate_request(disp, raw, 1, None, 100.0)[1]


def test_a_launch_refuses_an_allow_list_entry_with_no_sdk_route(tmp_path):
    g = router()
    node(g, "route")["allow"]["models"] = ["claude-sonnet-5-5", AGY_ONLY]
    with pytest.raises(OptionError) as exc:
        launch.launch(from_doc(g), tmp_path / "r", adapter=ScriptedAdapter({}))
    assert AGY_ONLY in str(exc.value) and "route" in str(exc.value)
    assert not (tmp_path / "r").exists()


def test_a_launch_refuses_an_agent_model_with_no_sdk_route(tmp_path):
    g = from_doc(doc([agent("a", output_key="x", model=AGY_ONLY)], ["a"]))
    with pytest.raises(OptionError) as exc:
        launch.launch(g, tmp_path / "r", adapter=ScriptedAdapter({}))
    assert AGY_ONLY in str(exc.value)
    assert not (tmp_path / "r").exists()


def _dying(lines):
    async def query(*, prompt, options):
        for line in lines:
            options.kw["stderr"](line)
        raise RuntimeError(OPAQUE)
        yield    # an async generator, as the SDK's query is
    return query


@pytest.mark.anyio
async def test_an_opaque_process_error_keeps_the_stderr_tail():
    a = SdkAdapter(query=_dying(["starting\n", "You've hit your weekly limit · resets Oct 24 at 5:46pm\n"]), options_cls=Options)
    with pytest.raises(AdapterError) as exc:
        await a.call(request())
    assert "weekly limit" in str(exc.value) and provider_error(exc.value)


@pytest.mark.anyio
async def test_the_stderr_tail_is_bounded():
    a = SdkAdapter(query=_dying(["x" * 5000 + "\n", "the last line\n"]), options_cls=Options)
    with pytest.raises(AdapterError) as exc:
        await a.call(request())
    text = str(exc.value)
    assert "the last line" in text and len(text) < len(OPAQUE) + 2048 + 200
