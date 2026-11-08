"""The hub's voice routes (#1603): one answer is served as one Kore clip, synthesized once and cached, and one recorded
clip is transcribed and sent to the session as one message.

The seams under test are the two routes on the hub's own server and `hub_voice`'s two pipeline calls, which stand in for
Gemini here: no network, no key and no audio device is touched. The answer is the session's own turn, named by the key
the conversation route served, so the route never speaks text a caller named.
"""
import http.client
import importlib.util
import json
import pathlib
import threading
from http.server import ThreadingHTTPServer

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib" / "squad-goal" / "scripts"
HUB_PY = SCRIPTS / "hub.py"
SID = "11111111-1111-1111-1111-111111111111"
CID = "22222222-2222-2222-2222-222222222222"
WAV = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 32
ANSWER = "All projects. The badge counts every project. One clip speaks it."


@pytest.fixture
def hub(monkeypatch, tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    for k, v in (("SG_REPO", "x/y"), ("SG_OWNER", "x"), ("SG_PROJECT", "4"), ("HOME", str(tmp_path)),
                 ("SG_CHECKOUT", str(tmp_path)), ("SG_HUB_DIR", str(tmp_path / "hub")), ("SG_STATE", str(state))):
        monkeypatch.setenv(k, v)
    for k in ("SG_STATE_BASE", "SG_SHARED", "SG_PROFILE", "SQUAD_HUB_STREAM"):
        monkeypatch.delenv(k, raising=False)
    spec = importlib.util.spec_from_file_location("hub_voice_routes", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, state


@pytest.fixture
def served(hub, monkeypatch):
    mod, state = hub
    calls = {"synth": [], "heard": [], "sent": []}

    def fake_spoken(text, out):   # the one synthesis, written where the caller asked: no Gemini, no key
        calls["synth"].append(text)
        pathlib.Path(out).write_bytes(WAV)
        return out

    def fake_heard(raw, mime):
        calls["heard"].append((raw, mime))
        return "Why did the token chart spike at two?"

    def fake_send(profile, sid, text, cid, **kw):
        calls["sent"].append((sid, text, cid))
        return {"status": "sent"}

    monkeypatch.setattr(mod.hub_voice, "spoken", fake_spoken)
    monkeypatch.setattr(mod.hub_voice, "heard", fake_heard)
    monkeypatch.setattr(mod.session_send, "send", fake_send)
    monkeypatch.setattr(mod, "conversation_payload", lambda q: {"turns": [
        {"id": "a1", "k": "0:1", "role": "owner", "text": "Which scope?", "at": "", "m": ""},
        {"id": "a2", "k": "0:2", "role": "answer", "text": ANSWER, "at": "", "m": "claude"},
    ], "next": None})
    srv = ThreadingHTTPServer(("127.0.0.1", 0), mod.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield mod, state, srv.server_port, calls
    srv.shutdown()
    srv.server_close()


def get(port, path):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
    conn.request("GET", path, headers={"Origin": f"http://127.0.0.1:{port}"})
    r = conn.getresponse()
    data, ct = r.read(), r.getheader("Content-Type")
    conn.close()
    return r.status, data, ct


def post(port, path, body, ctype="audio/webm"):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
    conn.request("POST", path, body=body, headers={"Content-Type": ctype, "Origin": f"http://127.0.0.1:{port}"})
    r = conn.getresponse()
    data = r.read()
    conn.close()
    return r.status, data


def test_an_answer_is_served_as_one_wav_clip_and_synthesized_once(served):
    mod, state, port, calls = served
    status, data, ct = get(port, f"/api/sessions/clip?sid={SID}&k=0%3A2")
    assert status == 200, data
    assert ct == "audio/wav" and data == WAV, ct
    assert calls["synth"] == [ANSWER], "one clip is the answer's whole text, never its first sentence: " + repr(calls)
    status2, data2, _ = get(port, f"/api/sessions/clip?sid={SID}&k=0%3A2")
    assert (status2, data2) == (200, WAV), "a cached clip is served again without a second synthesis"
    assert len(calls["synth"]) == 1, calls
    clip = state / "voice" / "clips" / SID
    assert [p.suffix for p in clip.iterdir()] == [".wav"], sorted(p.name for p in clip.iterdir())


def test_a_turn_that_is_no_answer_serves_no_clip(served):
    mod, state, port, calls = served
    status, _, _ = get(port, f"/api/sessions/clip?sid={SID}&k=0%3A1")
    assert status == 404, "the owner's own words are not spoken back as a Kore clip"
    assert calls["synth"] == [], calls


def test_a_recording_is_transcribed_and_sent_as_one_message(served):
    mod, state, port, calls = served
    status, data = post(port, f"/api/sessions/voice?sid={SID}&client_id={CID}&mime=audio/webm", b"\x1aE\xdf\xa3-webm")
    assert status == 200, data
    assert json.loads(data) == {"status": "sent"}, data
    assert calls["heard"][0][1] == "audio/webm", calls
    assert calls["sent"] == [(SID, "Why did the token chart spike at two?", CID)], calls


def test_a_type_the_route_does_not_admit_is_refused_and_nothing_is_sent(served):
    mod, state, port, calls = served
    status, data = post(port, f"/api/sessions/voice?sid={SID}&client_id={CID}&mime=text/plain", b"hello", ctype="text/plain")
    assert status == 415 and json.loads(data) == {"err": "type not allowed"}, data
    assert calls["heard"] == [] and calls["sent"] == [], calls


def test_a_transcription_the_pipeline_refuses_sends_nothing(served, monkeypatch):
    mod, state, port, calls = served

    def refuse(raw, mime):
        raise RuntimeError("no Gemini key for the phone's voice")

    monkeypatch.setattr(mod.hub_voice, "heard", refuse)
    status, data = post(port, f"/api/sessions/voice?sid={SID}&client_id={CID}&mime=audio/webm", b"\x1aE\xdf\xa3-webm")
    assert status == 502, data
    assert json.loads(data)["err"] == "no transcript", data
    assert "Gemini key" not in data.decode(), "a refusal never carries the key's story to the phone"
    assert calls["sent"] == [], calls
