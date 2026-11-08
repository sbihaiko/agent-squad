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

    def fake_spoken(text, out, **kw):   # the one synthesis, written where the caller asked: no Gemini, no key
        calls["synth"].append(text)
        pathlib.Path(out).write_bytes(WAV)
        return out

    def fake_heard(raw, mime, **kw):
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
    assert sorted(p.suffix for p in clip.iterdir()) == [".txt", ".wav"], sorted(p.name for p in clip.iterdir())
    said = next(p for p in clip.iterdir() if p.suffix == ".txt")
    assert said.read_text() == ANSWER, "the clip keeps the text it was made from"


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


def test_the_clip_route_refuses_a_cross_origin_reader(served):
    """A cross-site page must not make this hub synthesize and write a clip: the route keeps the stream's origin rule."""
    mod, state, port, calls = served
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("GET", f"/api/sessions/clip?sid={SID}&k=0%3A2", headers={"Origin": "https://evil.example"})
    r = conn.getresponse()
    data = r.read()
    conn.close()
    assert r.status == 403, data
    assert calls["synth"] == [], calls
    assert not (state / "voice" / "clips").exists(), "a refused clip writes nothing"


def test_the_clip_route_refuses_a_request_the_seam_denies(served, monkeypatch):
    mod, state, port, calls = served
    monkeypatch.setattr(mod, "tailnet_seam", lambda: None)
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("GET", f"/api/sessions/clip?sid={SID}&k=0%3A2", headers={"Tailscale-User-Login": "mallory"})
    r = conn.getresponse()
    r.read()
    conn.close()
    assert r.status == 403, "an unauthenticated reader gets no clip"
    assert calls["synth"] == [] and not (state / "voice" / "clips").exists(), calls


def test_a_voice_send_the_seam_denies_is_refused_and_writes_nothing(served, monkeypatch):
    mod, state, port, calls = served
    monkeypatch.setattr(mod, "tailnet_seam", lambda: None)
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("POST", f"/api/sessions/voice?sid={SID}&client_id={CID}&mime=audio/webm", body=b"\x1aE\xdf\xa3-webm",
                 headers={"Content-Type": "audio/webm", "Tailscale-User-Login": "mallory"})
    r = conn.getresponse()
    r.read()
    conn.close()
    assert r.status == 403, "an unauthenticated send is refused"
    assert calls["heard"] == [] and calls["sent"] == [], calls


def test_a_body_over_the_voice_cap_is_refused_before_it_is_read(served):
    """A Content-Length of gigabytes must not block the handler: the cap is answered first, and the connection ends."""
    mod, state, port, calls = served
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.putrequest("POST", f"/api/sessions/voice?sid={SID}&client_id={CID}&mime=audio/webm")
    conn.putheader("Content-Type", "audio/webm")
    conn.putheader("Origin", f"http://127.0.0.1:{port}")
    conn.putheader("Content-Length", str(10 * 1024 ** 3))
    conn.endheaders()
    r = conn.getresponse()
    data = r.read()
    conn.close()
    assert r.status == 413 and json.loads(data) == {"err": "too large"}, data
    assert calls["heard"] == [], calls


def test_one_answer_keeps_one_clip_keyed_on_its_turn_and_sid(served, monkeypatch):
    """The clip is named for the sid and the turn only. A streamed page that reads the answer slightly differently must not
    buy a second synthesis under the same key, and a refresh is the one way to ask for a new one."""
    mod, state, port, calls = served
    status, data, _ = get(port, f"/api/sessions/clip?sid={SID}&k=0%3A2")
    assert (status, data) == (200, WAV), data
    mod.conversation_payload = lambda q: {"turns": [
        {"id": "a2", "k": "0:2", "role": "answer", "text": ANSWER + " A trailing clause.", "at": "", "m": "claude"},
    ], "next": None}
    status2, data2, _ = get(port, f"/api/sessions/clip?sid={SID}&k=0%3A2")
    assert (status2, data2) == (200, WAV), "the same turn serves the clip it already has"
    assert calls["synth"] == [ANSWER], "the text a clip was made from never renames it: " + repr(calls)
    status3, _, _ = get(port, f"/api/sessions/clip?sid={SID}&k=0%3A2&refresh=1")
    assert status3 == 200, status3
    assert calls["synth"] == [ANSWER, ANSWER + " A trailing clause."], "only a refresh synthesizes again: " + repr(calls)
    clip = state / "voice" / "clips" / SID
    assert [p.suffix for p in sorted(clip.iterdir())] == [".txt", ".wav"], sorted(p.name for p in clip.iterdir())


def test_the_key_is_found_for_the_hubs_own_project_and_checkout(hub, monkeypatch):
    """A hub for another project reads that project's keychain entry and its own checkout's .env, never agent-squad's."""
    mod, state = hub
    seen = {}

    class TTS:
        def gemini_key(self, project, service, sibling):
            seen.update(project=project, service=service, sibling=sibling)
            return "k"

    monkeypatch.setattr(mod.hub_voice, "pipeline", lambda: (TTS(), None))
    assert mod.hub_voice.key(project="other", checkout="/tmp/other") == "k"
    assert seen == {"project": "other", "service": None, "sibling": "/tmp/other"}, seen


def test_the_routes_ask_for_the_hubs_own_project_and_checkout(served, monkeypatch):
    mod, state, port, calls = served
    seen = []

    def spy_spoken(text, out, project=None, checkout=None):
        seen.append((project, checkout))
        pathlib.Path(out).write_bytes(WAV)
        return out

    def spy_heard(raw, mime, project=None, checkout=None):
        seen.append((project, checkout))
        return "heard"

    monkeypatch.setattr(mod.hub_voice, "spoken", spy_spoken)
    monkeypatch.setattr(mod.hub_voice, "heard", spy_heard)
    get(port, f"/api/sessions/clip?sid={SID}&k=0%3A2")
    post(port, f"/api/sessions/voice?sid={SID}&client_id={CID}&mime=audio/webm", b"\x1aE\xdf\xa3-webm")
    assert seen == [("y", str(state.parent))] * 2, "both calls name this hub's project and checkout, never agent-squad's: " + repr(seen)


def test_a_clip_for_memory_only_is_never_synthesized_on_the_tailnet(served, monkeypatch):
    """A tailnet reader with no loop write may have a cached clip but never spends a synthesis or writes state."""
    mod, state, port, calls = served
    monkeypatch.setattr(mod, "access", lambda h, action: mod.Access(True, "", "someone", True))
    monkeypatch.setattr(mod, "tailnet_block", lambda checkout=None: {"host": "hub.example.ts.net", "loop_write": False,
                                                                     "terminals_send": True, "allow": ["someone"]})
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("GET", f"/api/sessions/clip?sid={SID}&k=0%3A2", headers={"Origin": "https://hub.example.ts.net"})
    r = conn.getresponse()
    data = r.read()
    conn.close()
    assert r.status == 404, data
    assert calls["synth"] == [] and not (state / "voice" / "clips").exists(), calls
