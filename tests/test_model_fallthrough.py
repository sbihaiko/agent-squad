"""GitHub issue #1512: a provider failure on one model must fall through to the next entry of the chain.

An upstream HTTP status (402, 429, 5xx) is the provider refusing to serve: an outage, not a defect of the work.
The chain is the dispatch envelope's `allow.models`, led by the model the node or the request picked. Before this
change the first `adapter_error` ended the node, and five runs on 2026-10-11 died in three seconds on a 402 from
an exhausted provider.
"""
import pytest

from squad.adapter import ScriptedAdapter
from squad.engine import Engine
from squad.graph import from_doc
from squad.record import read
from tests.gitrepo import sibling_repo
from tests.helpers import router

pytestmark = pytest.mark.anyio

# the diagnostic the real adapter raised on run-20261011-074446-8c4998 and its four siblings
PROVIDER_ERROR = ("ResultError: Claude Code returned an error result: "
                  "API Error: 402 Grok upstream rejected the request (exit code: 1)")
# the same failure as Grok's own CLI reports it, without the CLI's wrapper
PROVIDER_JSON = '{"error": "Grok Build usage balance exhausted", "status": 402}'

REQUEST = {"prompt": "Move the counter into its own module.", "skill": "tdd", "model": "sonnet", "effort": "medium",
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
    assert [c.model for c in eng.adapter.calls if c.node == "router"] == ["opus", "sonnet"]   # the allow list's next entry
    assert v.verdict == "completed" and outcomes(tmp_path, "router")[0]["outcome"] == "pass"


async def test_a_provider_error_on_a_child_falls_through_to_the_next_model(tmp_path):
    eng = engine(tmp_path, {"router": SPAWNS, "route.router.1": [{"error": PROVIDER_ERROR}, CHILD]})
    v = await eng.run()
    assert [c.model for c in eng.adapter.calls if c.node == "route.router.1"] == ["sonnet", "opus"]   # the request's pick, then the chain
    assert v.verdict == "completed" and outcomes(tmp_path, "route.router.1")[0]["outcome"] == "pass"


async def test_a_provider_error_named_by_a_status_without_the_api_error_prefix_falls_through(tmp_path):
    eng = engine(tmp_path, {"router": [{"error": PROVIDER_JSON}, SPAWNS], "route.router.1": CHILD})
    await eng.run()
    assert [c.model for c in eng.adapter.calls if c.node == "router"] == ["opus", "sonnet"]


async def test_a_chain_whose_every_entry_is_exhausted_fails_naming_each_entry(tmp_path):
    eng = engine(tmp_path, {"router": {"error": PROVIDER_ERROR}}, adapter={"repeat_last": True})
    v = await eng.run()
    assert [c.model for c in eng.adapter.calls if c.node == "router"] == ["opus", "sonnet", "haiku"]
    e = outcomes(tmp_path, "router")[0]
    assert e["outcome"] == "fail" and "adapter_error" in e["reasons"] and v.verdict == "failed"
    msg = e["diagnostic"]["error"]
    assert msg.count("402 Grok upstream rejected the request") == 3 and all(m in msg for m in ("opus", "sonnet", "haiku"))


async def test_an_error_that_is_not_a_provider_failure_still_fails_at_once(tmp_path):
    eng = engine(tmp_path, {"router": {"error": "socket closed"}})
    v = await eng.run()
    assert [c.model for c in eng.adapter.calls if c.node == "router"] == ["opus"]   # no provider status: the node fails as before
    e = outcomes(tmp_path, "router")[0]
    assert e["reasons"] == ["adapter_error"] and e["diagnostic"]["error"] == "socket closed" and v.verdict == "failed"
