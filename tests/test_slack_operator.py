"""The operator session's Slack write guard and one isolated session per inbox line (SPEC 2682, stories 48, 49 and 65;
#2691).

Two behaviours. The `PreToolUse` hook holds an allowlist of Slack tools: the connector's read tools always pass, and the
one write tool the policy decision names passes only with the decided channel, thread and text. A deny stands under
`bypassPermissions`, because the hook runs after the permission mode and its answer holds. The per-inbox-line pass starts
one fresh `claude -p` session for each new inbox line, in its own folder, with no session persistence, so a later session
holds no context from an earlier message.

Every case runs the hook as a script (`contrib/voice/operator_hook.py`) with an event JSON on stdin and a decision file
as its argument, so the test drives the one path production runs. One denial stands alone in its own sentence, so a
failure names the tool and the reason.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
VOICE = ROOT / "contrib" / "voice"
HOOK = VOICE / "operator_hook.py"

CHANNEL = "C0AAAAAA1"
OTHER = "C0OTHER01"
THREAD = "100.000100"
MARKED = "*Iara's Msg*\n_here is the answer_"

READ_ONLY = {"reply": "bot", "channel": CHANNEL, "thread_ts": THREAD, "text": ""}
DRAFTS = {"reply": "draft", "channel": CHANNEL, "thread_ts": THREAD, "text": MARKED}

FAKE_WRAPPER = """#!/usr/bin/env python3
import json, os, sys
with open(os.environ["FAKE_LOG"], "a") as f:
    f.write(json.dumps({"cwd": os.getcwd(), "argv": sys.argv[1:]}) + "\\n")
"""


def module():
    from squad import slackoperator
    return slackoperator


def line(mid, text, scope="dm"):
    """One inbox line, with the fields #2687 gives it."""
    return {"id": mid, "ts": THREAD, "user": "U0OWNER001", "channel": scope if scope != "dm" else "D0DIRECT01",
            "language": "pt-BR", "persona": "Iara", "role": "owner", "text": text}


def run_hook(tmp_path, decision, event, name="decision.json"):
    """The hook as a script: the decision as a file, the event JSON on stdin."""
    path = tmp_path / name
    path.write_text(json.dumps(decision))
    done = subprocess.run([sys.executable, str(HOOK), str(path)], input=json.dumps(event),
                          capture_output=True, text=True, timeout=30, check=False)
    return done


def answer(done):
    return (json.loads(done.stdout or "{}").get("hookSpecificOutput") or {})


@pytest.mark.heavy     # five Python starts that each import squad: the load pushes the call over the 1 s line
def test_the_hook_script_denies_each_call_the_decision_does_not_allow(tmp_path):
    """Criterion 6: run the hook as a script with an event JSON and a decision, and prove each denial."""
    # Criterion 1: a Slack tool outside the allowlist is denied with a reason, under a decision that names no reply.
    write = run_hook(tmp_path, READ_ONLY, {"tool_name": "mcp__claude_ai_Slack__slack_send_message",
                                           "tool_input": {"channel": CHANNEL, "text": "x"}})
    assert write.returncode == 0
    assert answer(write)["permissionDecision"] == "deny"
    assert "allowlist" in answer(write)["permissionDecisionReason"]
    # A draft tool is outside the allowlist too when the decision names no reply: the daemon posts the outbox line.
    draft = run_hook(tmp_path, READ_ONLY, {"tool_name": "mcp__slack__slack_send_message_draft",
                                           "tool_input": {"channel": CHANNEL, "text": "x"}})
    assert answer(draft)["permissionDecision"] == "deny"

    # Criterion 2: an allowed write whose arguments differ from the decision is denied, with the difference named.
    other = run_hook(tmp_path, DRAFTS, {"tool_name": "mcp__slack__slack_send_message_draft",
                                        "tool_input": {"channel": OTHER, "thread_ts": THREAD, "text": MARKED}})
    assert answer(other)["permissionDecision"] == "deny"
    assert "channel" in answer(other)["permissionDecisionReason"]
    text = run_hook(tmp_path, DRAFTS, {"tool_name": "mcp__slack__slack_send_message_draft",
                                       "tool_input": {"channel": CHANNEL, "thread_ts": THREAD, "text": "another"}})
    assert answer(text)["permissionDecision"] == "deny"
    assert "text" in answer(text)["permissionDecisionReason"]


@pytest.mark.heavy     # one Python start that imports squad
def test_both_denials_hold_under_bypass_permissions(tmp_path):
    """Criterion 3: the session runs under `bypassPermissions`, and the hook's deny still holds."""
    bypass = {"permission_mode": "bypassPermissions"}
    tool = run_hook(tmp_path, READ_ONLY, {**bypass, "tool_name": "mcp__claude_ai_Slack__slack_send_message",
                                          "tool_input": {"channel": CHANNEL, "text": "x"}}, "a.json")
    assert answer(tool)["permissionDecision"] == "deny"
    args = run_hook(tmp_path, DRAFTS, {**bypass, "tool_name": "mcp__slack__slack_send_message_draft",
                                       "tool_input": {"channel": OTHER, "text": MARKED}}, "b.json")
    assert answer(args)["permissionDecision"] == "deny"
    assert answer(args)["permissionDecisionReason"]


@pytest.mark.heavy     # one Python start that imports squad
def test_a_read_tool_and_the_decided_write_pass(tmp_path):
    """The allowlist lets the connector's read tools through, and the decided write with the exact arguments."""
    read = run_hook(tmp_path, READ_ONLY, {"tool_name": "mcp__claude_ai_Slack__slack_read_channel",
                                          "tool_input": {"channel": CHANNEL}}, "read.json")
    assert answer(read) == {}
    passed = run_hook(tmp_path, DRAFTS, {"tool_name": "mcp__slack__slack_send_message_draft",
                                         "tool_input": {"channel": CHANNEL, "thread_ts": THREAD, "text": MARKED}},
                      "pass.json")
    assert answer(passed)["permissionDecision"] == "allow"
    assert answer(passed)["updatedInput"]["text"] == MARKED, "the model's text reached the tool"
    # A write that names no channel, thread or text is filled with the decided ones, never refused for the omission.
    filled = run_hook(tmp_path, DRAFTS, {"tool_name": "mcp__slack__slack_send_message_draft", "tool_input": {}},
                      "filled.json")
    assert answer(filled)["permissionDecision"] == "allow"
    assert answer(filled)["updatedInput"] == {"channel": CHANNEL, "thread_ts": THREAD, "text": MARKED}


def wrapped(tmp_path, monkeypatch):
    """The module, with a fake wrapper that logs the cwd and the argv of each session it starts."""
    mod = module()
    wrap = tmp_path / "wrap.py"
    wrap.write_text(FAKE_WRAPPER)
    wrap.chmod(0o755)
    log = tmp_path / "runs.jsonl"
    monkeypatch.setenv("FAKE_LOG", str(log))
    return mod, wrap, log


def runs(log):
    return [json.loads(row) for row in log.read_text().splitlines()] if log.exists() else []


def test_the_session_prompt_carries_the_combined_tone():
    """#2927 criterion 7: the session reads the tone the policy composed, from the line, so an answer sounds like the
    persona and the sender's role. The persona name alone is not enough."""
    mod = module()
    brief = mod.line_prompt("dm", dict(line("m1", "hello"), tone="Calm and direct. Short sentences."),
                            str(ROOT), str(ROOT / ".scratch" / "state"))
    assert "in this tone: Calm and direct. Short sentences." in brief, brief


def test_a_line_with_no_tone_asks_for_the_persona_tone():
    """#2927: a line written before a policy carries a tone still gives the session an instruction."""
    mod = module()
    brief = mod.line_prompt("dm", line("m1", "hello"), str(ROOT), str(ROOT / ".scratch" / "state"))
    assert "in this tone: the project persona's tone" in brief, brief


def test_each_inbox_line_starts_one_isolated_session(tmp_path, monkeypatch):
    """Criterion 4: three inbox lines start three sessions, and a later pass starts none for the same lines."""
    mod, wrap, log = wrapped(tmp_path, monkeypatch)
    state = tmp_path / "state"
    inbox = state / "inboxes"
    inbox.mkdir(parents=True)
    (inbox / "dm.jsonl").write_text("".join(json.dumps(line(f"m{n}", f"message {n}")) + "\n" for n in (1, 2, 3)))
    (inbox / "C0AAAAAA1.jsonl").write_text(json.dumps(line("c1", "channel message", "C0AAAAAA1")) + "\n")
    assert mod.run_pass(str(state), str(ROOT), wrapper=str(wrap), cli="claude") == 4
    started = runs(log)
    assert len(started) == 4, f"{len(started)} sessions started for four inbox lines"
    assert mod.run_pass(str(state), str(ROOT), wrapper=str(wrap), cli="claude") == 0
    assert len(runs(log)) == 4, "a later pass started a session for a line it had already started"


def test_a_later_session_holds_no_context_from_an_earlier_message(tmp_path, monkeypatch):
    """Criterion 5: each session gets its own folder, no persisted session, no resume, and only its own line."""
    mod, wrap, log = wrapped(tmp_path, monkeypatch)
    state = tmp_path / "state"
    inbox = state / "inboxes"
    inbox.mkdir(parents=True)
    (inbox / "dm.jsonl").write_text("".join(json.dumps(line(f"m{n}", f"only-{n}")) + "\n" for n in (1, 2)))
    assert mod.run_pass(str(state), str(ROOT), wrapper=str(wrap), cli="claude") == 2
    first, later = runs(log)
    for one in (first, later):
        argv = one["argv"]
        assert "--resume" not in argv and "--continue" not in argv, "the session could carry an earlier conversation"
        assert "--no-session-persistence" in argv, "the session was written to the session store"
        assert "--setting-sources" in argv and argv[argv.index("--setting-sources") + 1] == ""
    assert first["cwd"] != later["cwd"], "two messages shared one working folder"
    assert "m1" in first["cwd"] and "m2" in later["cwd"]
    assert "only-1" not in json.dumps(later), "the later session was handed the earlier message"
    assert "only-2" not in json.dumps(first)
    first_decision = (Path(first["cwd"]) / "decision.json").read_text()
    later_decision = (Path(later["cwd"]) / "decision.json").read_text()
    assert "only-1" not in later_decision and "only-2" not in first_decision
