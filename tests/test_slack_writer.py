"""The guest draft writer through the `PreToolUse` hook (SPEC 2682, stories 34 to 37; #2712).

A short session writes one draft in the thread of the question. The hook pins the tool, the channel, the thread and the
exact text, so the model never copies the text and never redirects the write. Every id and every `ts` here is a
placeholder.
"""
import asyncio
import copy
import json
from datetime import datetime

from squad import slackpolicy as S
from squad import slackpoller as P
from squad import slackwriter as W

NOW = datetime(2026, 11, 22, 12, 0, 0)
QUESTION = "100.000100"
CH = "C0CLISUP01"

COMMITTED = {
    "version": 1,
    "project": {"language": "en-US", "persona": "iara"},
    "personas": [{"id": "iara", "name": "Iara", "voice": "Sulafat", "tone": "Warm and patient."}],
    "roles": [{"id": "colleague", "actions": ["status"]}],
    "workspaces": [],
}

LOCAL = {
    "version": 1,
    "workspaces": [
        {"id": "T0CLIENT01", "label": "client-a", "class": "guest", "persona": "iara",
         "channels": [{"id": CH, "name": "support", "mode": "listen+speak", "speak": "draft",
                       "roles": ["colleague"],
                       "poll": {"mode": "two_state", "fast": "1m", "slow": "10m", "quiet_after": "10m"}}]},
    ],
    "members": [{"workspace": "T0CLIENT01", "channel": CH, "role": "colleague", "users": ["U0CLIENT01"]}],
}

BODY = "Here is the answer to your question."


def decision(speak=None, grant=None):
    committed, local = copy.deepcopy(COMMITTED), copy.deepcopy(LOCAL)
    channel = local["workspaces"][0]["channels"][0]
    if speak is not None:
        channel["speak"] = speak
    if grant is not None:
        channel["send_grant"] = grant
    event = {"kind": "message", "workspace": "T0CLIENT01", "channel": CH, "user": "U0CLIENT01"}
    return S.decide(committed, local, event, NOW)


def draft(**over):
    d = decision(**over)
    return d, W.plan(d, CH, QUESTION, BODY)


def run(hook, tool_input, tool_name=None):
    name = tool_name or f"mcp__slack__slack_{tool_input.get('tool', W.DRAFT_WORD)}"
    payload = {k: v for k, v in tool_input.items() if k != "tool"}
    return asyncio.run(hook({"tool_name": name, "tool_input": payload}, "t1", None))


def answer(result):
    return result.get("hookSpecificOutput") or {}


# --- 1. only the decided tool, channel and thread_ts ---------------------------------------------------------------

def test_the_hook_accepts_only_the_tool_channel_and_thread_ts_the_decision_names():
    _, d = draft()
    hook = W.pre_write_tool_use(d)
    ok = answer(run(hook, {"tool": W.DRAFT_WORD, "channel": CH, "thread_ts": QUESTION, "text": "wrong copy"}))
    assert ok.get("permissionDecision") == "allow"
    wrong_tool = answer(run(hook, {"tool": W.SEND_WORD, "channel": CH, "thread_ts": QUESTION}))
    assert wrong_tool.get("permissionDecision") == "deny"
    wrong_channel = answer(run(hook, {"tool": W.DRAFT_WORD, "channel": "C0OTHER0001", "thread_ts": QUESTION}))
    assert wrong_channel.get("permissionDecision") == "deny"
    wrong_thread = answer(run(hook, {"tool": W.DRAFT_WORD, "channel": CH, "thread_ts": "999.000999"}))
    assert wrong_thread.get("permissionDecision") == "deny"


# --- 2. updatedInput carries the exact decided text, mark included -------------------------------------------------

def test_the_hook_returns_updated_input_with_the_exact_decided_text():
    _, d = draft()
    updated = answer(run(W.pre_write_tool_use(d), {"tool": W.DRAFT_WORD, "channel": CH, "thread_ts": QUESTION,
                                                    "text": "a loose copy with a space "}))
    assert updated["updatedInput"]["text"] == d.text
    assert d.text.startswith("*Iara's Msg*\n")
    assert BODY in d.text


# --- 3. the mark line, bold and alone, with an italic body ---------------------------------------------------------

def test_the_mark_is_the_persona_line_alone_in_bold_with_an_italic_body():
    assert P.mark("Iara") == "*Iara's Msg*"
    body = W.compose("Iara", "Short answer.")
    lines = body.splitlines()
    assert lines[0] == "*Iara's Msg*"
    assert lines[1] == "_Short answer._"


# --- 4. a write without the mark is denied -------------------------------------------------------------------------

def test_the_hook_denies_a_write_without_the_mark():
    assert W.write_denial("a plain text", ("*Iara's Msg*",)) is not None
    _, d = draft()
    unmarked = W.Draft(d.tool, d.channel, d.thread_ts, "a plain text", d.name)
    result = answer(run(W.pre_write_tool_use(unmarked), {"tool": W.DRAFT_WORD, "channel": CH, "thread_ts": QUESTION}))
    assert result.get("permissionDecision") == "deny"


# --- 3b. a body that is not one italic run is denied --------------------------------------------------------------

def test_a_body_that_is_not_one_italic_run_is_denied():
    # The mark is alone and bold, but the body is plain text between two separate italic runs, so it is not fully italic.
    assert W.write_denial("*Iara's Msg*\n_plain_ text _more_", ("*Iara's Msg*",)) is not None
    assert W.write_denial("*Iara's Msg*\n_a_ and _b_", ("*Iara's Msg*",)) is not None
    # An empty italic run (`__`) carries no body.
    assert W.write_denial("*Iara's Msg*\n__", ("*Iara's Msg*",)) is not None
    # One italic run over the whole body passes, with its inner plain text kept.
    assert W.write_denial("*Iara's Msg*\n_plain text stays in one run_", ("*Iara's Msg*",)) is None
    # A Draft whose decided text is not fully italic is denied by the hook.
    _, d = draft()
    lying = W.Draft(d.tool, d.channel, d.thread_ts, "*Iara's Msg*\n_plain_ text _more_", d.name)
    result = answer(run(W.pre_write_tool_use(lying), {"tool": W.DRAFT_WORD, "channel": CH, "thread_ts": QUESTION}))
    assert result.get("permissionDecision") == "deny"


# --- 5. send is denied without speak: send and a grant -------------------------------------------------------------

def test_the_hook_denies_send_without_speak_send_and_a_grant():
    # `speak: send` with no grant still decides a draft, so a send tool call is denied.
    d, planned = draft(speak="send")
    assert d.reply == "draft"
    assert planned.tool == W.DRAFT_WORD
    assert answer(run(W.pre_write_tool_use(planned), {"tool": W.SEND_WORD, "channel": CH,
                                                      "thread_ts": QUESTION})).get("permissionDecision") == "deny"
    # The grant makes the decision a send, and only then does the send tool pass.
    granted, sent = draft(speak="send", grant="2026-11-20")
    assert granted.reply == "send"
    assert sent.tool == W.SEND_WORD
    assert answer(run(W.pre_write_tool_use(sent), {"tool": W.SEND_WORD, "channel": CH,
                                                   "thread_ts": QUESTION})).get("permissionDecision") == "allow"


# --- 6. a listen+speak guest channel produces only drafts in this stage ---------------------------------------------

def test_a_listen_speak_guest_channel_produces_only_a_draft():
    _, d = draft()
    assert d.tool == W.DRAFT_WORD
    # Even a channel that asks for a direct send produces a draft while it holds no grant (Stage 3).
    _, asking = draft(speak="send")
    assert asking.tool == W.DRAFT_WORD
    hook = W.pre_write_tool_use(d)
    assert answer(run(hook, {"tool": W.SEND_WORD, "channel": CH, "thread_ts": QUESTION})).get("permissionDecision") == "deny"


# --- 7. the draft lands in the thread of the question --------------------------------------------------------------

def test_the_draft_lands_in_the_thread_of_the_question():
    _, d = draft()
    assert d.thread_ts == QUESTION
    updated = answer(run(W.pre_write_tool_use(d), {"tool": W.DRAFT_WORD, "channel": CH, "thread_ts": QUESTION}))
    assert updated["updatedInput"]["thread_ts"] == QUESTION
    assert updated["updatedInput"]["channel"] == CH


# --- 8. the writer records the ts of each draft in the own list ----------------------------------------------------

def test_the_writer_records_the_ts_of_each_draft_in_the_own_list(tmp_path):
    own = P.OwnTs(tmp_path / "own.json")
    hook = W.result_hook(own)
    response = {"ok": True, "channel": CH, "ts": "900.000900"}
    asyncio.run(hook({"tool_name": "mcp__slack__slack_send_message_draft",
                      "tool_input": {"channel": CH, "thread_ts": QUESTION}, "tool_response": response}, "t1", None))
    assert own.has("900.000900")
    # A result the reader cannot understand records nothing and never raises.
    asyncio.run(hook({"tool_name": "mcp__slack__slack_send_message_draft",
                      "tool_input": {"channel": CH}, "tool_response": "not json"}, "t2", None))
    assert own.values() == ("900.000900",)


# --- the JSON text of a result is read too -----------------------------------------------------------------------

def test_a_result_given_as_json_text_records_its_ts(tmp_path):
    own = P.OwnTs(tmp_path / "own.json")
    hook = W.result_hook(own)
    asyncio.run(hook({"tool_name": "mcp__slack__slack_send_message_draft",
                      "tool_input": {"channel": CH}, "tool_response": json.dumps({"ts": "900.000901"})}, "t1", None))
    assert own.has("900.000901")
