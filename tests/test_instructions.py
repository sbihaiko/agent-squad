"""An agent node may carry `instructions`: the role text the model reads before its inputs.

Guard: the graph hash covers it; the schema and the validator accept it.
"""
import json

import pytest

from squad.graph import from_doc
from tests.flows import agent, doc, run_engine

pytestmark = pytest.mark.anyio


async def test_instructions_ride_the_request_beside_the_prompt_and_the_user_message_is_only_the_json(tmp_path):
    d = doc([agent("a", output_key="x", instructions="Write the thing.")], ["a"])
    eng = run_engine(d, {"a": {"output": {"x": 1}}}, tmp_path)
    await eng.run()
    call = eng.adapter.calls[0]
    assert call.instructions == "Write the thing." and json.loads(call.prompt)["node"] == "a"


async def test_no_instructions_keeps_the_prompt_unchanged(tmp_path):
    eng = run_engine(doc([agent("a", output_key="x")], ["a"]), {"a": {"output": {"x": 1}}}, tmp_path)
    await eng.run()
    assert json.loads(eng.adapter.calls[0].prompt)["node"] == "a"


def test_instructions_change_the_graph_hash():
    a = doc([agent("a")], ["a"])
    b = doc([agent("a", instructions="x")], ["a"])
    assert from_doc(a).hash != from_doc(b).hash
