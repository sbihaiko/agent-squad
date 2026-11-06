"""A chain entry's declared context window skips a prompt larger than it (issue #1470).

A coding chain entry may declare its context window as `ctx` in models.json (for example `"ctx": "128k"`). The engine
estimates the prompt it would send, and an entry whose window is under that size is skipped: the record names the entry,
its window and the prompt size, and the chain moves to the next entry. An entry with no `ctx` behaves exactly as before.

The estimate is one token per four characters of the prompt, rounded up: the code holds no tokenizer, and this is the
simplest estimate it already has.
"""
import pytest

from squad import cli, modelreg, session
from squad.adapter import ScriptedAdapter
from squad.engine import Engine
from squad.graph import from_doc
from squad.options import OptionError, resolve
from squad.record import read
from tests.gitrepo import sibling_repo
from tests.helpers import load_doc, router

pytestmark = pytest.mark.anyio

_DOC = load_doc("router")
CHAINS = {
    "router": ["claude-opus-5-5", "claude-sonnet-5-5", "claude-sonnet-4-6"],
    "coding": ["claude-grok-4.7", "claude-deepseek-flash[1m]", "claude-opus-5-5", "claude-gpt-5.6-terra",
               "claude-sonnet-5-5", "claude-sonnet-4-6", "claude-haiku-5-5"],
    "review": ["claude-opus-5-5"],
    "vision": ["claude-sonnet-5-5"],
}
CHAIN = CHAINS["coding"]
PICK, NEXT = CHAIN[0], CHAIN[1]
CHILD_CHAIN = session.model_chain(CHAIN, PICK)
REQUEST = {"prompt": "Move the counter into its own module.", "skill": "tdd", "kind": "coding", "effort": "medium",
           "tools": ["Read", "Edit"], "stalls": 4, "spend": 1.0, "deadline": 100, "workspace": "worktrees/child"}
SPAWNS = {"output": {"spawns": [REQUEST]}, "spend": 0.1}
CHILD = {"output": {"diff": {"files": [{"path": "a.py"}]}, "ac_commands": ["true"]}, "spend": 0.2, "turns": 3}
CHILD_PROMPT = f"/{REQUEST['skill']} {REQUEST['prompt']}"

GRAPH = type("G", (), {"run": {"spend_cap": None, "deadline": None, "max_parallel": None}, "nodes": {}, "doc": {}, "hash": "h"})()


# --- the window declaration and the estimate -------------------------------------------------------------------

@pytest.mark.parametrize("text, tokens", [("128k", 128_000), ("1m", 1_000_000), ("32768", 32_768), ("200K", 200_000)])
def test_a_declared_window_reads_in_decimal_units(text, tokens):
    assert modelreg.ctx_tokens(text) == tokens


@pytest.mark.parametrize("text", [None, "", "junk", "12x", -1])
def test_no_readable_window_has_no_ceiling(text):
    assert modelreg.ctx_tokens(text) is None


def test_the_prompt_estimate_is_one_token_per_four_characters():
    assert session.estimate_tokens("") == 0
    assert session.estimate_tokens("abcd") == 1
    assert session.estimate_tokens("abcde") == 2
    assert session.estimate_tokens("x" * 800) == 200


# --- the chain skips an entry whose window is under the prompt ---------------------------------------------------

def test_an_entry_under_the_prompt_is_skipped_and_the_skip_names_its_window_and_the_prompt_size():
    kept, skipped = session.ctx_skipped(["kimi", "opus"], 200_000, {"kimi": "128k"})
    assert kept == ["opus"]
    assert skipped == [("kimi", "context_window:window=128000,prompt=200000")]


def test_an_entry_with_no_ctx_is_kept():
    assert session.ctx_skipped(["kimi", "opus"], 200_000, {}) == (["kimi", "opus"], [])
    assert session.ctx_skipped(["kimi", "opus"], 200_000, {"opus": "200k"}) == (["kimi", "opus"], [])


def test_every_entry_skipped_keeps_the_chain_with_no_skip():
    assert session.ctx_skipped(["kimi"], 200_000, {"kimi": "128k"}) == (["kimi"], [])


# --- the option and its CLI spelling ----------------------------------------------------------------------------

def test_the_engine_option_takes_a_window_map_and_refuses_a_non_string():
    assert resolve(GRAPH, {"model_ctx": {"kimi": "128k"}})[0]["model_ctx"] == {"kimi": "128k"}
    with pytest.raises(OptionError, match="model_ctx"):
        resolve(GRAPH, {"model_ctx": {"kimi": 128}})


def test_the_cli_reads_the_window_pairs():
    assert cli.model_ctx_arg("kimi=128k,opus=200k") == {"kimi": "128k", "opus": "200k"}
    assert cli.model_ctx_arg("") == {}


# --- the run: the record and the next entry ---------------------------------------------------------------------

def engine(tmp_path, script, **kw):
    d = router()
    d["run"]["deadline"] = 7200
    d["run"]["spend_cap"] = 8 * (len(CHAIN) + 2)
    kw.setdefault("target", sibling_repo(tmp_path))
    kw.setdefault("kind_chains", CHAINS)
    return Engine(from_doc(d), ScriptedAdapter(script, **kw.pop("adapter", {})), tmp_path, **kw)


async def test_a_child_entry_under_the_prompt_is_skipped_with_its_size_and_the_next_entry_answers(tmp_path):
    eng = engine(tmp_path, {"router": SPAWNS, "route.router.1": CHILD}, options={"model_ctx": {CHILD_CHAIN[0]: "1"}})
    v = await eng.run()
    assert [c.model for c in eng.adapter.calls if c.node == "route.router.1"] == CHILD_CHAIN[1:2]
    skipped = [e for e in read(tmp_path / "record.jsonl") if e["event"] == "model_skipped" and e["node"] == "route.router.1"]
    prompt = session.estimate_tokens(CHILD_PROMPT)
    assert [(e["model"], e["reason"]) for e in skipped] == [(CHILD_CHAIN[0], f"context_window:window=1,prompt={prompt}")]
    assert v.verdict == "completed"


async def test_a_child_entry_with_no_ctx_behaves_as_today(tmp_path):
    eng = engine(tmp_path, {"router": SPAWNS, "route.router.1": CHILD}, options={"model_ctx": {}})
    v = await eng.run()
    assert [c.model for c in eng.adapter.calls if c.node == "route.router.1"] == CHILD_CHAIN[:1]
    assert not [e for e in read(tmp_path / "record.jsonl") if e["event"] == "model_skipped" and e["node"] == "route.router.1"]
    assert v.verdict == "completed"
