"""A chain entry whose upstream sends nothing ends at its no-progress window, and the chain moves on (#1289).

The engine-level guards: a hung router or coding child falls back to its next entry within the window, the record names the
no-progress end as it happens, and the second entry gets only what is left of the node deadline. The call-level rules (the
window, a beating call, an open tool, the node deadline winning) are in tests/test_session.py; the walk is in
tests/test_chain_fallback.py. The window is the project's limits.no_progress_seconds, so each test waits under a second.
"""
import pytest

from tests.helpers import node
from tests.test_dispatch import CHILD, ev, make, req, routed
from tests.test_transport_retry_record import chain_of, node_trail

pytestmark = pytest.mark.anyio

WINDOW = 0.3                 # seconds of silence one entry may keep; the project's limits.no_progress_seconds at time scale 1
HUNG = {"delay": 3}          # the upstream sends nothing: the scripted call sleeps and never beats (a fix ends it at WINDOW)


def window():
    return {"project": {"limits": {"no_progress_seconds": WINDOW}}}


def router_outcomes(tmp_path):
    return [e for e in ev(tmp_path, "outcome") if e["node"] == "router"]


async def test_a_hung_entry_falls_back_to_the_next_entry_and_the_node_completes(tmp_path):
    eng = make(tmp_path, {"router": [HUNG, routed(req())], "route.router.1": CHILD}, **window())
    v = await eng.run()
    first, second = chain_of(tmp_path, "router")[:2]
    assert v.verdict == "completed"
    assert node_trail(tmp_path, "router") == [("call_start", None), ("model_refused", first), ("call_start", None), ("outcome", None)]
    assert [e["model"] for e in router_outcomes(tmp_path)] == [second], "the outcome names the entry that answered"


async def test_the_record_names_the_no_progress_end_of_the_silent_entry(tmp_path):
    eng = make(tmp_path, {"router": [HUNG, routed(req())], "route.router.1": CHILD}, **window())
    await eng.run()
    refused = [e for e in ev(tmp_path, "model_refused") if e["node"] == "router"]
    assert len(refused) == 1 and refused[0]["error"].startswith("no_progress: no message for 0.3 s")
    assert refused[0]["model"] == chain_of(tmp_path, "router")[0]


async def test_the_second_entry_gets_what_is_left_of_the_node_deadline(tmp_path):
    def tweak(d):
        node(d, "router")["deadline"] = 1.0
    eng = make(tmp_path, {"router": [HUNG, routed(req())], "route.router.1": CHILD}, tweak=tweak, **window())
    await eng.run()
    first, second = [c for c in eng.adapter.calls if c.node == "router"]
    started = [e for e in ev(tmp_path, "call_start") if e["node"] == "router"][0]["ts"]
    refused = [e for e in ev(tmp_path, "model_refused") if e["node"] == "router"][0]["ts"]
    spent = refused - started   # the silent entry's real cost: its window, plus whatever the detecting tick cost under load
    assert first.deadline == pytest.approx(1.0, abs=0.05)
    assert second.deadline == pytest.approx(1.0 - spent, abs=0.1), \
        "the node deadline bounds the whole chain, not each entry"
    assert spent >= WINDOW - 0.05, "a silent entry ends at its window, never before it"


async def test_a_hung_coding_child_falls_back_to_its_next_entry(tmp_path):
    coding = ["claude-grok-4.7", "claude-opus-5-5"]
    eng = make(tmp_path, {"router": routed(req()), "route.router.1": [HUNG, CHILD]}, adapter={"repeat_last": True},
               kind_chains={"router": ["claude-opus-5-5"], "coding": coding, "review": ["claude-opus-5-5"]}, **window())
    v = await eng.run()
    assert node_trail(tmp_path, "route.router.1") == [("call_start", None), ("model_refused", coding[0]), ("call_start", None), ("outcome", None)]
    assert v.verdict == "completed"
