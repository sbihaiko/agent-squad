"""A run records its issue-state binding and resumes only with that binding."""
import pytest

from squad.adapter import ScriptedAdapter
from squad.engine import Engine, ResumeRefused
from squad.graph import from_doc
from squad.record import read
from tests.flows import agent, doc


@pytest.mark.anyio
async def test_run_records_its_issue_state_binding_and_refuses_binding_drift(tmp_path):
    graph = from_doc(doc([agent("a", output_key="x", next={"fail": "exit:fail"})], ["a"]))
    first = Engine(graph, ScriptedAdapter({"a": {"output": {"x": 1}, "spend": 0.1}}), tmp_path,
                   state_binding="fake-v1")
    await first.run()

    assert read(tmp_path / "record.jsonl")[0]["state_binding"] == "fake-v1"

    changed = Engine(graph, ScriptedAdapter({}), tmp_path, state_binding="fake-v2")
    with pytest.raises(ResumeRefused, match="issue-state binding changed"):
        await changed.resume()
