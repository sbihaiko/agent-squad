"""Guard: issue #2721 (SPEC 2682, stage S4, story 70). A `guest` channel with `voice_reply: true` gets the reply also in
audio: a clip with the persona voice, uploaded through the connector, with the mark in the file comment. The write hook
allows the connector's two upload calls only with the channel, the thread and the comment the decision names.

The seams are the policy decision, the writer's voice plan and the writer's `PreToolUse` hook. A fake voice records the
voice and tone of each script, and a fake connector records every upload call, so no case reaches Gemini or Slack.
"""
import asyncio
import copy
import os
from datetime import datetime
from pathlib import Path

from squad import slackpolicy as S
from squad import slackpoller as P
from squad import slackwriter as W

NOW = datetime(2026, 11, 22, 12, 0, 0)
QUESTION = "100.000100"
CH = "C0CLISUP01"
OTHER = "C0OTHER0001"
TONE = "Warm and patient. Confirms what was asked before answering."
BODY = "Green. Then blue."
MARK = "*Iara's Msg*"

COMMITTED = {
    "version": 1,
    "project": {"language": "en-US", "persona": "iara"},
    "personas": [{"id": "iara", "name": "Iara", "voice": "Sulafat", "tone": TONE}],
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


def decision(voice=True):
    """The policy decision of one guest message, on a channel that asks for a voice reply or does not."""
    committed, local = copy.deepcopy(COMMITTED), copy.deepcopy(LOCAL)
    local["workspaces"][0]["channels"][0]["voice_reply"] = voice
    event = {"kind": "message", "workspace": "T0CLIENT01", "channel": CH, "user": "U0CLIENT01"}
    return S.decide(committed, local, event, NOW)


def text_plan(d):
    """The text write the same decision names, so the hook runs exactly as the writer session runs it."""
    return W.plan(d, CH, QUESTION, BODY)


class FakeVoice:
    """The one TTS seam: it records the script, the voice and the tone of each clip, and writes a small file."""

    def __init__(self, tmp_path):
        self.calls = []
        self.dir = Path(tmp_path) / "clips"
        self.dir.mkdir(exist_ok=True)

    def __call__(self, script, voice, tone):
        self.calls.append({"script": script, "voice": voice, "tone": tone})
        path = self.dir / f"clip-{len(self.calls)}.wav"
        path.write_bytes(b"RIFF")
        return str(path)


class FakeConnector:
    """The connector's upload calls, as the writer session makes them: one call per write the hook allowed, with the
    arguments the hook returned. It records the tool and the arguments, so a test sees every upload that leaves."""

    def __init__(self):
        self.calls = []

    async def call(self, tool, arguments):
        self.calls.append((tool, dict(arguments)))
        if tool == "get_file_upload_url":
            return {"upload_url": "https://files.example/upload", "file_id": "F0001"}
        return {"ok": True}


def run(hook, tool, args):
    """One call of the writer session through the hook: the connector's own tool name, and the model's arguments."""
    name = f"mcp__slack__slack_{tool}"
    return asyncio.run(hook({"tool_name": name, "tool_input": args}, "t1", None))


def answer(result):
    return result.get("hookSpecificOutput") or {}


def through(hook, connector, tool, args):
    """One upload call of the writer session: the hook decides, and only an allow reaches the fake connector."""
    decided = answer(run(hook, tool, args))
    if decided.get("permissionDecision") != "allow":
        return decided, None
    return decided, asyncio.run(connector.call(tool, decided["updatedInput"]))


def upload_of(tmp_path, voice=True):
    """The voice reply of one guest answer: the clip and the upload plan on the same decision as the text write."""
    d = decision(voice)
    fake = FakeVoice(tmp_path)
    return d, W.voice_answer(d, CH, QUESTION, BODY, fake), fake


# --- 1. a channel with voice_reply: true gets a clip with the persona voice ------------------------------------------

def test_a_channel_with_voice_reply_true_gets_a_clip_with_the_persona_voice(tmp_path):
    d, upload, fake = upload_of(tmp_path, voice=True)
    assert d.voice_reply is True
    assert upload is not None, "a voice reply channel got no clip"
    assert fake.calls == [{"script": BODY, "voice": "Sulafat", "tone": TONE}]
    assert os.path.exists(upload.path)
    assert upload.channel == CH
    assert upload.thread_ts == QUESTION


# --- 2. the connector uploads the clip -------------------------------------------------------------------------------

def test_the_connector_uploads_the_clip(tmp_path):
    d, upload, _ = upload_of(tmp_path)
    connector, hook = FakeConnector(), W.pre_write_tool_use(text_plan(d), upload=upload)

    url, _ = through(hook, connector, "get_file_upload_url",
                     {"filename": os.path.basename(upload.path), "length": os.path.getsize(upload.path)})
    assert url.get("permissionDecision") == "allow"
    done, _ = through(hook, connector, "complete_file_upload",
                      {"file_id": "F0001", "channel": CH, "thread_ts": QUESTION,
                       "initial_comment": upload.comment})

    assert done.get("permissionDecision") == "allow"
    assert [tool for tool, _ in connector.calls] == ["get_file_upload_url", "complete_file_upload"]
    assert connector.calls[1][1]["file_id"] == "F0001"
    assert connector.calls[1][1]["channel"] == CH
    assert connector.calls[1][1]["thread_ts"] == QUESTION


# --- 3. the file comment carries the mark ----------------------------------------------------------------------------

def test_the_file_comment_carries_the_mark(tmp_path):
    d, upload, _ = upload_of(tmp_path)
    assert upload.comment == P.mark("Iara") == MARK

    connector, hook = FakeConnector(), W.pre_write_tool_use(text_plan(d), upload=upload)
    done, sent = through(hook, connector, "complete_file_upload",
                         {"file_id": "F0001", "channel": CH, "thread_ts": QUESTION})
    assert done.get("permissionDecision") == "allow"
    assert sent is not None, "the connector received no upload"
    assert connector.calls[-1][1]["initial_comment"] == MARK


# --- 4. the hook allows the two upload calls only with the decided channel, thread and comment ----------------------

def test_the_hook_allows_the_two_upload_calls_only_with_the_decided_channel_thread_and_comment(tmp_path):
    d, upload, _ = upload_of(tmp_path)
    hook = W.pre_write_tool_use(text_plan(d), upload=upload)

    url = answer(run(hook, "get_file_upload_url", {"filename": os.path.basename(upload.path),
                                                   "length": os.path.getsize(upload.path)}))
    assert url.get("permissionDecision") == "allow"
    assert url["updatedInput"] == {"filename": os.path.basename(upload.path),
                                   "length": os.path.getsize(upload.path)}

    done = answer(run(hook, "complete_file_upload", {"file_id": "F0001", "channel": CH, "thread_ts": QUESTION,
                                                     "initial_comment": MARK}))
    assert done.get("permissionDecision") == "allow"
    assert done["updatedInput"] == {"file_id": "F0001", "channel": CH, "thread_ts": QUESTION,
                                    "initial_comment": MARK}


# --- 5. the hook denies an upload call with other arguments ----------------------------------------------------------

def test_the_hook_denies_an_upload_call_with_other_arguments(tmp_path):
    d, upload, _ = upload_of(tmp_path)
    hook = W.pre_write_tool_use(text_plan(d), upload=upload)

    other_channel = answer(run(hook, "complete_file_upload", {"file_id": "F0001", "channel": OTHER}))
    assert other_channel.get("permissionDecision") == "deny"
    other_thread = answer(run(hook, "complete_file_upload", {"file_id": "F0001", "thread_ts": "999.000999"}))
    assert other_thread.get("permissionDecision") == "deny"
    other_comment = answer(run(hook, "complete_file_upload", {"file_id": "F0001", "initial_comment": "a plain text"}))
    assert other_comment.get("permissionDecision") == "deny"
    no_file = answer(run(hook, "complete_file_upload", {"channel": CH, "thread_ts": QUESTION}))
    assert no_file.get("permissionDecision") == "deny"
    other_file = answer(run(hook, "get_file_upload_url", {"filename": "elsewhere.wav",
                                                          "length": os.path.getsize(upload.path)}))
    assert other_file.get("permissionDecision") == "deny"
    other_length = answer(run(hook, "get_file_upload_url", {"filename": os.path.basename(upload.path),
                                                            "length": os.path.getsize(upload.path) + 1}))
    assert other_length.get("permissionDecision") == "deny"


# --- 6. a channel with voice_reply: false gets no clip ---------------------------------------------------------------

def test_a_channel_with_voice_reply_false_gets_no_clip(tmp_path):
    d, upload, fake = upload_of(tmp_path, voice=False)
    assert d.voice_reply is False
    assert upload is None, "a channel without voice reply got a clip"
    assert fake.calls == []

    hook = W.pre_write_tool_use(text_plan(d))
    assert answer(run(hook, "get_file_upload_url", {"filename": "clip.wav"})).get("permissionDecision") == "deny"
    assert answer(run(hook, "complete_file_upload", {"file_id": "F0001"})).get("permissionDecision") == "deny"


# --- 7. a test with a fake connector proves each rule ----------------------------------------------------------------

def test_a_fake_connector_proves_each_upload_rule(tmp_path):
    d, upload, _ = upload_of(tmp_path, voice=True)
    connector, hook = FakeConnector(), W.pre_write_tool_use(text_plan(d), upload=upload)
    through(hook, connector, "get_file_upload_url", {"filename": os.path.basename(upload.path)})
    through(hook, connector, "complete_file_upload", {"file_id": "F0001"})
    assert [tool for tool, _ in connector.calls] == ["get_file_upload_url", "complete_file_upload"]
    assert connector.calls[-1][1]["initial_comment"] == MARK

    quiet = decision(voice=False)
    silent = FakeConnector()
    quiet_hook = W.pre_write_tool_use(text_plan(quiet), upload=None)
    through(quiet_hook, silent, "get_file_upload_url", {"filename": "clip.wav"})
    through(quiet_hook, silent, "complete_file_upload", {"file_id": "F0001"})
    assert silent.calls == [], "a channel without voice reply uploaded a clip"
