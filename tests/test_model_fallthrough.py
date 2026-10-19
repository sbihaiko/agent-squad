"""GitHub issue #1512: a provider failure on one model must fall through to the next entry of the chain.

An upstream HTTP status (402, 429, 5xx) is the provider refusing to serve: an outage, not a defect of the work.
The chain is the dispatch envelope's `allow.models`, led by the model the node or the request picked. Before this
change the first `adapter_error` ended the node, and five runs on 2026-10-11 died in three seconds on a 402 from
an exhausted provider.

The order these cases name is the seed's own (docs/design/router.graph.json): the gateway model first, Anthropic last as
reserve (the owner's call, to spend the Anthropic quota last), so the chain's next entry after a gateway pick is the second
gateway model and never `opus`.
"""
import time

import pytest

from squad.adapter import ScriptedAdapter
from squad.engine import Engine
from squad.graph import from_doc
from squad.record import read
from squad.session import model_chain
from tests.gitrepo import sibling_repo
from tests.helpers import load_doc, node, router

pytestmark = pytest.mark.anyio

# the diagnostic the real adapter raised on run-20261011-074446-8c4998 and its four siblings
PROVIDER_ERROR = ("ResultError: Claude Code returned an error result: "
                  "API Error: 402 Grok upstream rejected the request (exit code: 1)")
# the same failure as Grok's own CLI reports it, without the CLI's wrapper
PROVIDER_JSON = '{"error": "Grok Build usage balance exhausted", "status": 402}'
# The subscription's own refusal, as the CLI prints it: no HTTP status anywhere in the sentence. The router node died on
# this text on 2026-10-13, 116 times, while the second entry of its chain answered at once.
SESSION_LIMIT = ("ResultError: Claude Code returned an error result: "
                 "You've hit your session limit · resets 7:27am (America/Sao_Paulo) (exit code: 1)")
# a pool's refusal, and a provider's, in the same shape
USAGE_LIMIT = "ResultError: Claude Code returned an error result: API Error: The usage limit has been reached (exit code: 1)"

# The shipped envelope, read and not copied: a chain this file hardcodes goes stale the day the envelope moves
# (2026-10-13). The expected order is the one the engine builds, `[pick] + allow.models` without the pick.
_DOC = load_doc("router")
CHAIN = node(_DOC, "route")["allow"]["models"]
ROUTER_CHAIN = model_chain(CHAIN, node(_DOC, "router")["model"])
PICK, NEXT = CHAIN[0], CHAIN[1]
CHILD_CHAIN = model_chain(CHAIN, PICK)
REQUEST = {"prompt": "Move the counter into its own module.", "skill": "tdd", "model": PICK, "effort": "medium",
           "tools": ["Read", "Edit"], "stalls": 4, "spend": 1.0, "deadline": 100, "workspace": "worktrees/child"}
SPAWNS = {"output": {"spawns": [REQUEST]}, "spend": 0.1}
CHILD = {"output": {"diff": {"files": [{"path": "a.py"}]}, "ac_commands": ["true"]}, "spend": 0.2, "turns": 3}


def engine(tmp_path, script, **kw):
    d = router()
    d["run"]["deadline"] = 7200
    kw.setdefault("target", sibling_repo(tmp_path))
    return Engine(from_doc(d), ScriptedAdapter(script, **kw.pop("adapter", {})), tmp_path, **kw)


def outcomes(tmp_path, node):
    return [e for e in read(tmp_path / "record.jsonl") if e["event"] == "outcome" and e["node"] == node]


async def test_a_provider_error_on_the_router_falls_through_to_the_next_model(tmp_path):
    eng = engine(tmp_path, {"router": [{"error": PROVIDER_ERROR}, SPAWNS], "route.router.1": CHILD})
    v = await eng.run()
    assert [c.model for c in eng.adapter.calls if c.node == "router"] == ROUTER_CHAIN[:2]   # the node's pick, then the allow list's next entry, and it answers
    assert v.verdict == "completed" and outcomes(tmp_path, "router")[0]["outcome"] == "pass"


async def test_a_provider_error_on_a_child_falls_through_to_the_next_model(tmp_path):
    eng = engine(tmp_path, {"router": SPAWNS, "route.router.1": [{"error": PROVIDER_ERROR}, CHILD]})
    v = await eng.run()
    assert [c.model for c in eng.adapter.calls if c.node == "route.router.1"] == CHILD_CHAIN[:2]   # the request's pick, then the next entry, which answers
    assert v.verdict == "completed" and outcomes(tmp_path, "route.router.1")[0]["outcome"] == "pass"


async def test_a_provider_error_named_by_a_status_without_the_api_error_prefix_falls_through(tmp_path):
    eng = engine(tmp_path, {"router": [{"error": PROVIDER_JSON}, SPAWNS], "route.router.1": CHILD})
    await eng.run()
    assert [c.model for c in eng.adapter.calls if c.node == "router"] == ROUTER_CHAIN[:2]


async def test_the_subscriptions_own_refusal_falls_through_to_the_next_model(tmp_path):
    """The refusal the CLI prints for an exhausted subscription names no HTTP status, and it is still the provider
    refusing to serve: before this case the run died on its first call (116 runs on 2026-10-13)."""
    eng = engine(tmp_path, {"router": [{"error": SESSION_LIMIT}, SPAWNS], "route.router.1": CHILD})
    v = await eng.run()
    assert [c.model for c in eng.adapter.calls if c.node == "router"] == ROUTER_CHAIN[:2]
    assert v.verdict == "completed" and outcomes(tmp_path, "router")[0]["outcome"] == "pass"


async def test_a_pools_usage_limit_falls_through_to_the_next_model(tmp_path):
    """The gateway answers a spent pool with `The usage limit has been reached`: the same refusal from another speaker."""
    eng = engine(tmp_path, {"router": [{"error": USAGE_LIMIT}, SPAWNS], "route.router.1": CHILD})
    v = await eng.run()
    assert [c.model for c in eng.adapter.calls if c.node == "router"] == ROUTER_CHAIN[:2]
    assert v.verdict == "completed"


async def test_a_chain_whose_every_entry_is_exhausted_fails_naming_each_entry(tmp_path):
    eng = engine(tmp_path, {"router": {"error": PROVIDER_ERROR}}, adapter={"repeat_last": True})
    v = await eng.run()
    assert [c.model for c in eng.adapter.calls if c.node == "router"] == ROUTER_CHAIN
    e = outcomes(tmp_path, "router")[0]
    assert e["outcome"] == "fail" and "adapter_error" in e["reasons"] and v.verdict == "failed"
    msg = e["diagnostic"]["error"]
    assert msg.count("402 Grok upstream rejected the request") == len(ROUTER_CHAIN) and all(m in msg for m in ROUTER_CHAIN)


async def test_an_error_that_is_not_a_provider_failure_still_fails_at_once(tmp_path):
    eng = engine(tmp_path, {"router": {"error": "socket closed"}})
    v = await eng.run()
    assert [c.model for c in eng.adapter.calls if c.node == "router"] == ROUTER_CHAIN[:1]   # no provider status: the node fails as before
    e = outcomes(tmp_path, "router")[0]
    assert e["reasons"] == ["adapter_error"] and e["diagnostic"]["error"] == "socket closed" and v.verdict == "failed"


# A subscription the shared marker closes (`codex-limited` for `gpt-`) is known to be spent before the run starts: the
# launcher passes its model ids as the engine option `spent_models`, and the chain never starts a call on one of them.
SPENT = [m for m in [node(_DOC, "router")["model"], *CHAIN] if m.startswith("claude-gpt-")]


def test_model_chain_drops_the_spent_ids_and_keeps_the_order_of_the_rest():
    assert model_chain(CHAIN, node(_DOC, "router")["model"], SPENT) == [m for m in ROUTER_CHAIN if m not in SPENT]


def test_model_chain_keeps_the_chain_when_every_model_is_spent():
    assert model_chain(CHAIN, PICK, spent=ROUTER_CHAIN) == CHILD_CHAIN   # a closed marker is a hint: with no model left, the chain tries them in order


def test_model_chain_with_no_spent_ids_is_the_chain_as_before():
    assert model_chain(CHAIN, PICK, []) == model_chain(CHAIN, PICK) == CHILD_CHAIN


async def test_a_run_with_a_spent_gpt_marker_never_starts_a_call_on_a_gpt_model_and_the_router_answers_on_the_next(tmp_path):
    eng = engine(tmp_path, {"router": SPAWNS, "route.router.1": CHILD}, options={"spent_models": SPENT})
    v = await eng.run()
    assert not [c.model for c in eng.adapter.calls if c.model.startswith("claude-gpt-")]
    assert [c.model for c in eng.adapter.calls if c.node == "router"] == [m for m in ROUTER_CHAIN if m not in SPENT][:1]   # the router starts on the next entry, with no wait for a refusal
    assert v.verdict == "completed" and outcomes(tmp_path, "router")[0]["outcome"] == "pass"


# A model outside its local `hours` window never starts a call, in the run as at the launch: the launcher passes the window
# map as the engine option `model_hours` and the chain skips the entry, with a `model_skipped` event naming the window.
NIGHT = time.mktime((2026, 10, 19, 22, 0, 0, 0, 0, -1))    # 22h local: outside "07-19"


async def test_a_fallback_outside_its_hours_is_skipped_with_a_reason_and_the_next_model_answers(tmp_path):
    shut = ROUTER_CHAIN[1]
    eng = engine(tmp_path, {"router": [{"error": PROVIDER_ERROR}, SPAWNS], "route.router.1": CHILD}, wall=lambda: NIGHT,
                 options={"model_hours": {shut: "07-19"}})
    v = await eng.run()
    assert [c.model for c in eng.adapter.calls if c.node == "router"] == [ROUTER_CHAIN[0], ROUTER_CHAIN[2]]   # first refused, second shut, third taken
    skipped = [e for e in read(tmp_path / "record.jsonl") if e["event"] == "model_skipped" and e["node"] == "router"]
    assert [(e["model"], e["reason"]) for e in skipped] == [(shut, "outside_hours:07-19")]
    assert v.verdict == "completed"


def test_model_chain_keeps_a_model_inside_its_hours():
    assert model_chain(CHAIN, PICK, hours={CHAIN[1]: "07-19"}, hour=10) == CHILD_CHAIN
