"""#1293 on the record: a transport failure is retried once on the same chain entry, and both runners write the retry through
a real Record. The walk itself is in tests/test_chain_fallback.py; these tests read what the engine left on the record.
"""
import pytest

from squad import session
from squad.record import read
from tests.test_dispatch import CHILD, ev, make, req, routed

# A gateway that refuses every connection: the transport did not deliver, and the model never answered (served False).
REFUSED = {"error": "API Error: Connection refused (ECONNREFUSED)", "served": False}
NODE_KINDS = ("call_start", "model_retried", "model_refused", "outcome")


@pytest.fixture
def no_backoff(monkeypatch):
    """The walk waits TRANSPORT_BACKOFF seconds before each retry; the record's order does not depend on the wait."""
    monkeypatch.setattr(session, "TRANSPORT_BACKOFF", 0.0)


def node_trail(tmp_path, node):
    """One node's events in record order as (kind, model). A `call_start` and an `outcome` name no model here: the chain is
    the order, and each call's model is on the retry and refusal events beside it."""
    events = read(tmp_path / "record.jsonl")
    return [(e["event"], None if e["event"] in ("call_start", "outcome") else e.get("model"))
            for e in events if e.get("node") == node and e["event"] in NODE_KINDS]


def chain_of(tmp_path, node):
    """The chain ids the node's first call stated (SPEC 1035): the order every later event refers to."""
    return [e for e in ev(tmp_path, "call_start") if e["node"] == node][0]["chain"]


def all_fail_trail(chain):
    """Every entry is called twice and fails: call_start, model_retried, call_start per entry. The refusals follow the walk,
    one per entry, and one outcome closes the node."""
    out = []
    for m in chain:
        out += [("call_start", None), ("model_retried", m), ("call_start", None)]
    return out + [("model_refused", m) for m in chain] + [("outcome", None)]


@pytest.mark.anyio
async def test_a_router_refused_by_the_transport_on_every_entry_is_on_the_record(tmp_path, no_backoff):
    eng = make(tmp_path, {"router": [REFUSED], "route.router.1": CHILD}, adapter={"repeat_last": True})
    v = await eng.run()
    chain = chain_of(tmp_path, "router")
    assert len(chain) == 2, "the router chain has two entries, so each is called twice"
    assert node_trail(tmp_path, "router") == all_fail_trail(chain)
    retried = [e for e in ev(tmp_path, "model_retried") if e["node"] == "router"]
    assert [e["model"] for e in retried] == chain and {e["error"] for e in retried} == {REFUSED["error"]}
    assert v.verdict != "completed" and v.exit_code != 0
    assert len(ev(tmp_path, "verdict")) == 1


@pytest.mark.anyio
async def test_a_router_whose_retry_answers_is_retried_once_and_completes(tmp_path, no_backoff):
    eng = make(tmp_path, {"router": [REFUSED, routed(req())], "route.router.1": CHILD})
    v = await eng.run()
    assert v.verdict == "completed"
    first = chain_of(tmp_path, "router")[0]
    assert node_trail(tmp_path, "router") == [("call_start", None), ("model_retried", first), ("call_start", None),
                                              ("outcome", None)]
    assert ev(tmp_path, "model_refused") == [], "a retry that answers is not a refusal"
    assert len(ev(tmp_path, "verdict")) == 1


@pytest.mark.anyio
async def test_a_child_refused_by_the_transport_on_every_entry_is_on_the_record(tmp_path, no_backoff):
    """The children runner writes the same trail for a dispatch child: two calls per entry, the retry before the second,
    one refusal per entry, one outcome for the child, and one verdict for the run."""
    coding = ["claude-grok-4.7", "claude-opus-5-5"]
    eng = make(tmp_path, {"router": routed(req()), "route.router.1": [REFUSED]}, adapter={"repeat_last": True},
               kind_chains={"router": ["claude-opus-5-5"], "coding": coding, "review": ["claude-opus-5-5"]})
    v = await eng.run()
    assert len(chain_of(tmp_path, "route.router.1")) == 2, "the coding chain has two entries here"
    assert node_trail(tmp_path, "route.router.1") == all_fail_trail(coding)    # the retry events name the raw spelling
    assert v.verdict != "completed"
    assert len(ev(tmp_path, "verdict")) == 1
