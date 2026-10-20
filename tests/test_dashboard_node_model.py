"""Each run-dashboard card names the real LLM it used, or says it runs none (#677).

One core function, `nodeModel`, decides the line (ADR-0137); the card only renders it. An agent node shows the model
the record says it used; with no record it shows the graph's model, marked "configured". A node that calls no model
(gate, action, wait, loop, each, dispatch) says "no LLM" and what it runs instead. `fmt.llm` names a model by its
registry label and marks an id the registry does not know "unregistered", where `fmt.model` keeps it raw.
"""
import pytest

from tests.dashboard_helpers import ROOT, run_node

PAGE = ROOT / "dashboard" / "page"


def _line(node, ns=None):
    return run_node([{"op": "nodemodel", "node": node, "ns": ns}])[0]


def _llm(v):
    return run_node([{"op": "fmt", "kind": "llm", "value": v}])[0]


def _used(model):
    return {"harness": {"configured": {"model": "sonnet"}, "used": {"model": model}}}


def test_an_agent_shows_the_model_the_record_says_it_used():
    assert _line({"name": "a", "kind": "agent", "model": "sonnet"}, _used("claude-grok-4.7")) == \
        {"llm": True, "source": "used", "registered": True, "text": "Grok 4.7"}


def test_an_agent_with_no_record_shows_its_configured_model_marked():
    assert _line({"name": "a", "kind": "agent", "model": "claude-sonnet-5-5"}) == \
        {"llm": True, "source": "configured", "registered": True, "text": "Sonnet 5.5 (configured)"}
    assert _line({"name": "a", "kind": "agent", "model": "opus"}, _used(None))["text"] == "Opus 5.5 (configured)"


def test_an_unregistered_model_says_so_plainly():
    got = _line({"name": "a", "kind": "agent", "model": "sonnet"}, _used("gpt-6-luna"))
    assert got == {"llm": True, "source": "used", "registered": False, "text": "gpt-6-luna · unregistered"}
    assert _line({"name": "a", "kind": "agent", "model": "claude-haiku-5-5"})["text"] == "claude-haiku-5-5 · unregistered (configured)"


def test_an_agent_with_no_model_at_all_says_none_is_set():
    assert _line({"name": "a", "kind": "agent"})["text"] == "model not set"


@pytest.mark.parametrize("node,text", [
    ({"name": "g", "kind": "gate", "check": "scope"}, "no LLM · scope"),
    ({"name": "g", "kind": "gate", "check": "ac-verify"}, "no LLM · ac-verify"),
    ({"name": "w", "kind": "action", "effect": "merge_land"}, "no LLM · merge_land"),
    ({"name": "m", "kind": "wait", "owner": "rafael"}, "no LLM · waits for rafael"),
    ({"name": "l", "kind": "loop"}, "no LLM"),
    ({"name": "e", "kind": "each"}, "no LLM"),
    ({"name": "d", "kind": "dispatch"}, "no LLM · children"),
])
def test_a_node_that_calls_no_model_says_no_llm_and_what_it_runs(node, text):
    assert _line(node) == {"llm": False, "source": None, "registered": None, "text": text}


def test_a_review_command_gate_names_its_command_and_claims_no_llm_of_its_own():
    got = _line({"name": "r", "kind": "gate", "check": "review-command", "params": {"argv": ["/x/review.sh", "--pr"]}})
    assert got == {"llm": None, "source": None, "registered": None, "text": "review.sh · own LLM"}
    shell = {"name": "r", "kind": "gate", "check": "review-command",
             "params": {"argv": ["sh", "-c", 'exec bash "$SG_RUN_REVIEW" "$@"', "runreview", "{run_id}"]}}
    assert _line(shell)["text"] == "runreview · own LLM"


def test_fmt_llm_reads_a_label_and_marks_an_unknown_id():
    assert _llm("claude-deepseek-flash") == "DeepSeek V4.1 Flash (no tools)"
    assert _llm("claude-gpt-5.6-terra medium tdd") == "GPT-5.6 Terra medium tdd"
    assert _llm("gpt-6-luna medium") == "gpt-6-luna · unregistered medium"
    assert _llm(None) == "—"


def test_the_cards_render_the_core_line_and_never_decide_it():
    src = (PAGE / "graph.js").read_text()
    assert "core.nodeModel(n, ns)" in src
    assert "f.llm(c.model)" in src and "core.fmt.llm(gr.harness)" in src
