"""GitHub issue #2697: the persona editor of the Communication theme in the hub Settings page.

The owner edits a persona name, voice and tone, hears a voice sample, and sees the reply of each channel before saving.
The `voice` list holds only the Gemini voices, and a persona carries no language and no permission. The sample calls
Gemini TTS once per voice and tone pair, and the hub keeps the WAV in a cache, so a second request for the same pair
plays the cached file and makes no call. Every resolved value and the mark come from the policy module (ADR-0177): the
hub holds no copy of a rule, and the view constructs and binds (ADR-0137).

The seams under test are the hub's `slack_overview` (the voice list and the reply previews), `slack_preview`,
`slack_sample` with a fake Gemini client, and the theme's renderer run in node. No browser runs here.
"""
import base64
import copy
import http.client
import importlib.util
import json
import os
import pathlib
import re
import subprocess
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib" / "squad-goal" / "scripts" / "hub.py"
POLICY = ROOT / "squad" / "slackpolicy.py"

COMMITTED = {
    "version": 1,
    "project": {"language": "en-US", "persona": "hal"},
    "apps": [{"id": "hal9k", "keychain": {"bot": "agent-squad-slack-bot", "app": "agent-squad-slack-app"}}],
    "personas": [
        {"id": "hal", "name": "HAL9K", "voice": "Kore", "tone": "Calm and direct."},
        {"id": "iara", "name": "Iara", "voice": "Sulafat", "tone": "Warm and patient."},
    ],
    "events": [{"id": "stg_release", "template": "Deployed to STG: {items}."}],
    "roles": [{"id": "manager", "actions": ["status", "logs"]}],
    "workspaces": [
        {"id": "T0TEAM0001", "label": "team", "class": "owned", "app": "hal9k",
         "channels": [
             {"id": "C0PROJ0001", "name": "proj-agent-squad", "mode": "listen+speak", "language": "pt-BR",
              "persona": "hal", "roles": ["manager"]},
             {"id": "C0ALERT001", "name": "squad-alerts", "mode": "listen"},
         ]},
    ],
}

LOCAL = {
    "version": 1,
    "workspaces": [
        {"id": "T0CLIENT01", "label": "client-a", "class": "guest", "language": "en-US", "persona": "iara",
         "channels": [{"id": "C0CLISUP01", "name": "support", "mode": "listen"}]},
    ],
}


@pytest.fixture(scope="module")
def hub():
    """The hub module under a pinned environment, so the policy module comes from this tree and no caller's SG_CHECKOUT
    answers for it. Every value is undone when the module ends."""
    mp = pytest.MonkeyPatch()
    for k, v in (("SG_REPO", "x/y"), ("SG_OWNER", "x"), ("SG_PROJECT", "4"), ("SG_SQUAD", str(ROOT))):
        mp.setenv(k, v)
    for k in ("SG_HUB_DIR", "SG_STATE", "SG_CHECKOUT", "SG_SHARED", "SG_PROFILES_DIR", "SG_SLACK_WORKTREE"):
        mp.setenv(k, tempfile.mkdtemp())
    spec = importlib.util.spec_from_file_location("hub_slack_persona", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    yield mod
    mp.undo()


@pytest.fixture
def state(hub, tmp_path, monkeypatch):
    """One state directory per test, so the sample cache of one case never answers another."""
    d = tmp_path / "state"
    monkeypatch.setattr(hub, "STATE", str(d))
    return d


def files(tmp_path, committed=None, local=None):
    c = tmp_path / "docs" / "squad" / "slack.yaml"
    c.parent.mkdir(parents=True, exist_ok=True)
    c.write_text(yaml.safe_dump(COMMITTED if committed is None else committed, sort_keys=False))
    loc = tmp_path / "4.slack.local.yaml"
    loc.write_text(yaml.safe_dump(LOCAL if local is None else local, sort_keys=False))
    return c, loc


def overview(hub, tmp_path, committed=None, local=None):
    c, loc = files(tmp_path, committed, local)
    return hub.slack_overview(c, loc)


# ---------------------------------------------------------------- the hub: voices, preview, sample

def test_the_voice_list_holds_only_the_gemini_voices(hub, tmp_path):
    """Criterion (1): the view offers the voice list the policy module owns, and no other name."""
    out = overview(hub, tmp_path)
    assert out["voices"] == sorted(hub.slack_policy().GEMINI_VOICES)
    assert "Hal" not in out["voices"]


def test_each_channel_reply_preview_names_the_mark_language_tone_and_voice(hub, tmp_path):
    """Criterion (6): every channel carries the reply of the persona it resolves to, in its own language. The mark is
    the policy rule, `*<name>'s Msg*`. The team channel names hal in pt-BR, the alert channel inherits hal and en-US,
    and the guest channel resolves iara through its workspace."""
    out = overview(hub, tmp_path)
    got = [(p["channel"], p["mark"], p["language"], p["tone"], p["voice"]) for p in out["previews"]]
    assert got == [
        ("team/#proj-agent-squad", "*HAL9K's Msg*", "pt-BR", "Calm and direct.", "Kore"),
        ("team/#squad-alerts", "*HAL9K's Msg*", "en-US", "Calm and direct.", "Kore"),
        ("client-a/#support", "*Iara's Msg*", "en-US", "Warm and patient.", "Sulafat"),
    ], got


def test_a_channel_with_no_persona_carries_an_empty_preview(hub, tmp_path):
    """A project that names no persona draws the channel with no mark, no tone and no voice, never a guessed identity."""
    committed = {k: v for k, v in COMMITTED.items() if k != "personas"}
    committed["project"] = {"language": "en-US"}
    committed["workspaces"] = [{k: v for k, v in COMMITTED["workspaces"][0].items() if k != "channels"} | {
        "channels": [{k: v for k, v in COMMITTED["workspaces"][0]["channels"][0].items() if k != "persona"}]}]
    local = {"version": 1, "workspaces": []}
    out = overview(hub, tmp_path, committed, local)
    assert out["previews"] == [{"channel": "team/#proj-agent-squad", "mark": "", "language": "pt-BR",
                                "tone": "", "voice": ""}], out["previews"]


def test_the_preview_follows_the_draft_documents_before_a_save(hub, tmp_path):
    """Criterion (6): the preview checks the edit before the save. `slack_preview` resolves the documents the view
    hands in, so a changed name and tone show at once."""
    committed = json.loads(json.dumps(COMMITTED))
    committed["personas"][0].update({"name": "Hal 9000", "tone": "Short sentences."})
    out = hub.slack_preview(committed, LOCAL)
    assert out["err"] is None
    first = out["previews"][0]
    assert (first["mark"], first["tone"]) == ("*Hal 9000's Msg*", "Short sentences."), first


def test_the_preview_accepts_a_draft_with_one_document(hub):
    """Criterion (6): a draft that carries only one document is previewed, not refused, so a local override shows
    before a save. A value that is neither a mapping nor None is still refused."""
    local_only = {"version": 1, "workspaces": LOCAL["workspaces"],
                  "personas": [{"id": "iara", "name": "Iara", "voice": "Sulafat", "tone": "Warm and patient."}]}
    only_local = hub.slack_preview(None, local_only)
    assert only_local["err"] is None, only_local
    assert [(p["channel"], p["mark"]) for p in only_local["previews"]] == [("client-a/#support", "*Iara's Msg*")]
    only_committed = hub.slack_preview(COMMITTED, None)
    assert only_committed["err"] is None, only_committed
    assert [p["channel"] for p in only_committed["previews"]] == ["team/#proj-agent-squad", "team/#squad-alerts"]
    assert hub.slack_preview(None, None)["err"] is None
    assert hub.slack_preview([], None)["err"], "a list is not a document mapping"


def test_two_idless_channels_across_the_two_documents_are_two_rows(hub):
    """Criterion (6): a channel with no id in each document is a different channel, so it previews twice. The dedupe key
    falls back on the document and the index, as the workspace key does, never on the index alone."""
    committed = {"version": 1, "personas": [{"id": "hal", "name": "HAL9K", "voice": "Kore", "tone": "Calm."}],
                 "workspaces": [{"id": "T0TEAM0001", "label": "team", "persona": "hal",
                                 "channels": [{"name": "alpha"}]}]}
    local = {"version": 1, "workspaces": [{"id": "T0TEAM0001", "label": "team", "persona": "hal",
                                           "channels": [{"name": "beta"}]}]}
    out = hub.slack_preview(committed, local)
    rows = [p["channel"] for p in out["previews"]]
    assert rows == ["team/#alpha", "team/#beta"], rows


def test_the_preview_shows_one_row_per_effective_channel(hub):
    """Criterion (6): a channel the local file repeats is previewed once, and the commit wins, as at run time."""
    committed = json.loads(json.dumps(COMMITTED))
    local = {"version": 1, "workspaces": [
        {"id": "T0TEAM0001", "label": "team", "class": "owned", "language": "en-US",
         "channels": [{"id": "C0PROJ0001", "name": "proj-agent-squad", "persona": "iara"},
                      {"id": "C0NEW00001", "name": "extra", "persona": "iara"}]}]}
    out = hub.slack_preview(committed, local)
    rows = [(p["channel"], p["mark"]) for p in out["previews"]]
    assert rows == [("team/#proj-agent-squad", "*HAL9K's Msg*"), ("team/#squad-alerts", "*HAL9K's Msg*"),
                    ("team/#extra", "*Iara's Msg*")], rows


class FakeGemini:
    """The fake Gemini client: it records every synthesis request and writes a small WAV, so a cache hit is visible as
    a missing call."""

    def __init__(self, payload=b"RIFF....WAVEfake"):
        self.calls = []
        self.payload = payload

    def __call__(self, voice, tone, out):
        self.calls.append({"voice": voice, "tone": tone, "out": str(out)})
        pathlib.Path(out).write_bytes(self.payload)
        return str(out), None


def test_the_sample_calls_gemini_once_for_a_voice_and_tone_pair(hub, state):
    """Criteria (4) and (5): the first request synthesizes and caches; the second plays the cached WAV and makes no
    call. The answer carries the audio as a data URL either way."""
    gemini = FakeGemini()
    first = hub.slack_sample({"voice": "Kore", "tone": "Calm and direct."}, client=gemini)
    second = hub.slack_sample({"voice": "Kore", "tone": "Calm and direct."}, client=gemini)

    assert first["cached"] is False and second["cached"] is True, (first["cached"], second["cached"])
    assert len(gemini.calls) == 1, gemini.calls
    assert gemini.calls[0]["voice"] == "Kore" and gemini.calls[0]["tone"] == "Calm and direct."
    assert first["url"] == second["url"] and first["url"].startswith("data:audio/wav;base64,")
    assert base64.b64decode(first["url"].split(",", 1)[1]) == gemini.payload
    assert list((state / "slack-samples").glob("*.wav")), "the WAV is kept in the cache"


def test_the_sample_makes_a_new_call_for_a_new_tone(hub, state):
    """The cache key is the pair: the same voice with another tone is another sample."""
    gemini = FakeGemini()
    hub.slack_sample({"voice": "Kore", "tone": "Calm."}, client=gemini)
    second = hub.slack_sample({"voice": "Kore", "tone": "Bright."}, client=gemini)
    assert second["cached"] is False and len(gemini.calls) == 2, gemini.calls


def test_the_sample_refuses_a_voice_outside_the_gemini_list(hub, state):
    gemini = FakeGemini()
    out = hub.slack_sample({"voice": "Hal", "tone": "Calm."}, client=gemini)
    assert "voice" in out["err"] and "Hal" in out["err"], out
    assert gemini.calls == []


def test_the_sample_refuses_a_tone_over_the_limit(hub, state):
    gemini = FakeGemini()
    out = hub.slack_sample({"voice": "Kore", "tone": "x" * 301}, client=gemini)
    assert "tone" in out["err"], out
    assert gemini.calls == []


def test_a_client_error_is_a_message_and_leaves_no_cache_entry(hub, state):
    """A failed synthesis never leaves a half entry: the next request tries again."""
    calls = []

    def broken(voice, tone, out):
        calls.append((voice, tone))
        return None, "no TTS model answered"

    out = hub.slack_sample({"voice": "Kore", "tone": "Calm."}, client=broken)
    assert out["err"] and "no TTS model answered" in out["err"], out
    assert not list((state / "slack-samples").glob("*.wav")), "a failed synthesis leaves no WAV"
    retry = FakeGemini()
    assert hub.slack_sample({"voice": "Kore", "tone": "Calm."}, client=retry)["cached"] is False
    assert len(calls) == 1 and len(retry.calls) == 1


def test_a_partial_wav_in_the_cache_is_not_served(hub, state):
    """A truncated file left by a killed synthesis is no cache hit: the next request makes the sample again."""
    d = state / "slack-samples"
    d.mkdir(parents=True)
    (d / f"{hub.slack_sample_key('Kore', 'Calm.')}.wav").write_bytes(b"RIFF")
    gemini = FakeGemini()
    out = hub.slack_sample({"voice": "Kore", "tone": "Calm."}, client=gemini)
    assert out["err"] is None and out["cached"] is False, out
    assert len(gemini.calls) == 1, gemini.calls


def test_a_changed_sample_text_makes_a_new_sample(hub, state, monkeypatch):
    """The cache key holds the sample text: a new script is spoken, not the old cached sentence."""
    gemini = FakeGemini()
    hub.slack_sample({"voice": "Kore", "tone": "Calm."}, client=gemini)
    monkeypatch.setattr(hub, "SLACK_SAMPLE_TEXT", "A different sentence now.")
    out = hub.slack_sample({"voice": "Kore", "tone": "Calm."}, client=gemini)
    assert out["cached"] is False and len(gemini.calls) == 2, gemini.calls


def test_two_concurrent_requests_make_one_call(hub, state):
    """Criteria (4) and (5) under concurrency: two simultaneous Sample presses on one pair make one synthesis."""
    import threading

    calls = []

    def slow(voice, tone, out):
        calls.append((voice, tone))
        threading.Event().wait(0.3)
        pathlib.Path(out).write_bytes(b"RIFF....WAVEslow")
        return str(out), None

    started = threading.Barrier(2)
    results = []

    def run():
        started.wait()
        results.append(hub.slack_sample({"voice": "Kore", "tone": "Calm."}, client=slow))

    threads = [threading.Thread(target=run) for _ in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(calls) == 1, calls
    assert all(r["err"] is None for r in results), results


def test_a_lock_left_by_a_killed_request_is_reclaimed(hub, state):
    """Criteria (4) and (5): a lock older than a synthesis plus its margin belongs to a killed request, so the next
    request takes it and synthesizes instead of waiting on a pair that no one is making."""
    d = state / "slack-samples"
    d.mkdir(parents=True)
    key = hub.slack_sample_key("Kore", "Calm.")
    lock = d / f"{key}.lock"
    lock.write_text("")
    old = time.time() - hub.SLACK_SAMPLE_LOCK_STALE - 5
    os.utime(lock, (old, old))
    gemini = FakeGemini()
    out = hub.slack_sample({"voice": "Kore", "tone": "Calm."}, client=gemini)
    assert out["err"] is None and out["cached"] is False, out
    assert len(gemini.calls) == 1, gemini.calls
    assert not lock.exists(), "the reclaimed lock is gone"


def test_a_waiter_retries_after_the_first_request_fails(hub, state, monkeypatch):
    """Criteria (4) and (5): a request that waits on another request's lock calls no client. When that first request
    dies without a WAV, the lock goes and the next request synthesizes cleanly."""
    monkeypatch.setattr(hub, "SLACK_SAMPLE_TIMEOUT", 0.2)
    d = state / "slack-samples"
    d.mkdir(parents=True)
    key = hub.slack_sample_key("Kore", "Calm.")
    lock = d / f"{key}.lock"
    lock.write_text("")   # the first request is in flight
    gemini = FakeGemini()
    waited = hub.slack_sample({"voice": "Kore", "tone": "Calm."}, client=gemini)
    assert "another request" in waited["err"], waited
    assert gemini.calls == [], "a waiter never calls the client"
    lock.unlink()   # the first request died and left no WAV
    out = hub.slack_sample({"voice": "Kore", "tone": "Calm."}, client=gemini)
    assert out["err"] is None and out["cached"] is False, out
    assert len(gemini.calls) == 1, gemini.calls


# ---------------------------------------------------------------- the save path of a staged persona edit

def test_a_staged_persona_edit_names_its_field_in_the_save_diff(hub, tmp_path, monkeypatch):
    """Criterion (1): the editor stages the edit in the two raw documents, and the Save with confirm false answers the
    diff of the committed file. The diff names the persona field and carries both the old and the new value."""
    monkeypatch.setattr(hub, "STATE", tmp_path / "state")
    c = tmp_path / "checkout" / "docs" / "squad" / "slack.yaml"
    c.parent.mkdir(parents=True)
    c.write_text(yaml.safe_dump(COMMITTED, sort_keys=False))
    loc = tmp_path / "4.slack.local.yaml"
    loc.write_text(yaml.safe_dump(LOCAL, sort_keys=False))
    edited = copy.deepcopy(COMMITTED)
    edited["personas"][0]["tone"] = "Short sentences."
    out = hub.slack_files_set({"committed": edited, "local": LOCAL, "confirm": False}, committed=c, local=loc)
    lines = (out.get("diff") or {}).get("committed") or []
    assert any("tone" in line and "Short sentences." in line for line in lines), out
    assert any("Calm and direct." in line for line in lines), out


# ---------------------------------------------------------------- the write gate of the two persona routes

@pytest.fixture
def serve(hub):
    """The hub's own handler on a loopback port. The module fixture pins an empty checkout, so the tailnet entry is off."""
    srv = ThreadingHTTPServer(("127.0.0.1", 0), hub.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()
    srv.server_close()


def post(port, path, *, host, origin, body, login=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {"Host": host, "Origin": origin, "Content-Type": "application/json"}
    if login is not None:
        headers["Tailscale-User-Login"] = login
    c.request("POST", path, body=json.dumps(body), headers=headers)
    r = c.getresponse()
    text = r.read().decode()
    c.close()
    return r.status, text


PERSONA_ROUTES = ("/api/slack/preview", "/api/slack/sample", "/api/slack")


@pytest.mark.parametrize("path", PERSONA_ROUTES)
def test_a_foreign_origin_is_refused_on_the_persona_routes(hub, serve, monkeypatch, path):
    """The preview and the sample join the write gate of /api/slack: a cross-site page never reaches their handler. The
    hub answers its one refusal and calls no policy, no client and no cache."""
    reached = []
    monkeypatch.setattr(hub, "slack_preview", lambda *a: (reached.append("preview"), {"err": None})[1])
    monkeypatch.setattr(hub, "slack_sample", lambda *a, **k: (reached.append("sample"), {"err": None})[1])
    monkeypatch.setattr(hub, "slack_set", lambda *a: (reached.append("set"), {"err": None})[1])
    port = serve.server_port
    status, text = post(port, path, host=f"127.0.0.1:{port}", origin="https://evil.example",
                        body={"voice": "Kore", "tone": "Calm."})
    assert (status, json.loads(text)) == (400, {"err": "forbidden"}), (status, text)
    assert reached == [], reached


@pytest.mark.parametrize("path", PERSONA_ROUTES)
def test_the_persona_routes_are_refused_while_the_tailnet_entry_is_off(hub, serve, monkeypatch, path):
    """With no tailnet host in the project file, a tailnet request on the preview or the sample writes nothing, as
    /api/slack does. The seam refuses it before any handler."""
    reached = []
    monkeypatch.setattr(hub, "slack_preview", lambda *a: (reached.append("preview"), {"err": None})[1])
    monkeypatch.setattr(hub, "slack_sample", lambda *a, **k: (reached.append("sample"), {"err": None})[1])
    monkeypatch.setattr(hub, "slack_set", lambda *a: (reached.append("set"), {"err": None})[1])
    host = "hub.tail1234.ts.net"
    status, text = post(serve.server_port, path, host=host, origin=f"https://{host}",
                        body={"voice": "Kore", "tone": "Calm."}, login="ana@example.com")
    assert status == 403, (status, text)
    assert reached == [], reached


# ---------------------------------------------------------------- the renderer

def _node(body):
    p = subprocess.run(["node", "-"], input=body, capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    return p.stdout


def _slices(page):
    esc = re.search(r"^const esc=.*$", page, re.M).group(0)
    sett = page[page.index("// SETT{"):page.index("// }SETT")]
    sl = page[page.index("let slS=null"):page.index("async function slLoad()")]
    stub = 'const STORE={};const $=s=>STORE[s]||(STORE[s]={innerHTML:"",textContent:""});'
    return "\n".join([stub, esc, sett, sl])


def render(hub, out, open_id=None):
    view = hub.SLACK_DEFAULT
    body = (_slices(hub.PAGE) + f"\nslS={json.dumps(view)};slDraft=slClone(slS);slR={json.dumps(out)};"
            "slDocs=slClone((slR&&slR.docs)||{});slSavedDocs=slClone(slDocs);"
            f"slOpen={json.dumps(open_id)};slRender();console.log(JSON.stringify(STORE['#sbody'].innerHTML))")
    return json.loads(_node(body))


def detail(html, pid):
    """The open persona's detail alone: it ends where the Add persona row begins."""
    i = html.index(f'data-sle="{pid}"')
    j = html.find("<div class=slad>", i)
    return html[i:j if j >= 0 else len(html)]


def test_the_persona_editor_edits_name_voice_and_tone_and_carries_no_language_or_permission(hub, tmp_path):
    """Criteria (1) and (2): the detail holds a name field, a voice select and a tone field, and it holds no language
    field and no permission field. The persona carries no language and no permission, so the editor draws none."""
    out = overview(hub, tmp_path)
    d = detail(render(hub, out, open_id="iara"), "iara")
    for field in ("name:iara", "voice:iara", "tone:iara"):
        assert f'data-slef="{field}"' in d, (field, d)
    assert 'data-slsample="iara"' in d, d
    assert "language" not in d.lower() and "permission" not in d.lower(), d
    for tag in ("pt-BR", "en-US", "es-ES"):
        assert tag not in d, (tag, d)


def test_the_voice_select_offers_only_the_gemini_voices(hub, tmp_path):
    """Criterion (1): the select draws the hub's list and nothing else."""
    out = overview(hub, tmp_path)
    d = detail(render(hub, out, open_id="iara"), "iara")
    select = re.search(r'data-slef="voice:iara"[^>]*>(.*?)</select>', d, re.S).group(1)
    options = re.findall(r"<option[^>]*>(.*?)</option>", select)
    assert options == out["voices"], options
    assert "Sulafat" in options


def test_the_editor_sets_the_default_persona_and_an_override_per_workspace_and_channel(hub, tmp_path):
    """Criterion (3): one select sets the project's default persona, and one select sets the persona of each Slack
    workspace and of each channel."""
    out = overview(hub, tmp_path)
    html = render(hub, out)
    assert "data-sldefault" in html, html
    assert len(re.findall(r"data-slow=", html)) == 2, "one override select per Slack workspace"
    assert len(re.findall(r"data-slcw=", html)) == 3, "one override select per channel"


def test_the_preview_shows_the_mark_language_tone_and_voice_of_each_channel(hub, tmp_path):
    """Criterion (6): the page draws one reply row per channel, with the four facts and no control."""
    out = overview(hub, tmp_path)
    html = render(hub, out)
    i = html.index(">Reply preview<")
    block = html[i:html.index("</table>", i)]
    assert "HAL9K's Msg" in block and "Iara's Msg" in block, block
    for fact in ("pt-BR", "Calm and direct.", "Kore", "Warm and patient.", "Sulafat"):
        assert fact in block, (fact, block)
    assert "<select" not in block and "<input" not in block, block


def test_a_refused_autoplay_leaves_a_visible_play_control(hub, tmp_path):
    """Criterion (5): a browser that refuses to play the sample keeps its URL, and the page draws a control bound to it,
    so the owner hears the sample on a press instead of losing it."""
    out = overview(hub, tmp_path)
    audio = "data:audio/wav;base64,QUJD"
    body = (_slices(hub.PAGE) + f"\nslS={json.dumps(hub.SLACK_DEFAULT)};slDraft=slClone(slS);"
            f"slR={json.dumps(out)};slDocs=slClone((slR&&slR.docs)||{{}});slSavedDocs=slClone(slDocs);"
            f"slOpen='iara';globalThis.window={{__slAudio:{json.dumps(audio)}}};"
            "slRender();console.log(JSON.stringify(STORE['#sbody'].innerHTML))")
    html = json.loads(_node(body))
    assert "<audio" in html and "controls" in html, html
    assert audio in html, "the control points at the kept URL"


def test_an_edit_sets_the_draft_document_and_marks_the_theme_dirty(hub):
    """Criterion (1): the editor writes the two raw files the Save reads (#2694), so the change is staged and the Save
    shows its diff. A field outside name, voice and tone is refused."""
    body = (_slices(hub.PAGE) + f"\nslR={json.dumps({'voices': ['Kore', 'Puck']})};"
            "slS={connection:'connected',people:[]};slDraft=slClone(slS);"
            "slDocs={committed:{version:1,project:{},personas:[{id:'iara',name:'Iara',voice:'Sulafat',tone:'Warm.'}]},local:{}};"
            "slSavedDocs=slClone(slDocs);"
            "const ok=slPersonaSet(slDocs,'iara','voice','Puck');"
            "const bad=slPersonaSet(slDocs,'iara','language','pt-BR');"
            "const added=slPersonaAdd(slDocs,'Nova');"
            "console.log(JSON.stringify({ok,bad,added,voice:slDocs.committed.personas[0].voice,"
            "ids:slDocs.committed.personas.map(p=>p.id),dirty:slDirty()}))")
    got = json.loads(_node(body))
    assert got == {"ok": True, "bad": False, "added": True, "voice": "Puck", "ids": ["iara", "Nova"], "dirty": True}, got


def test_an_added_persona_takes_a_gemini_voice(hub):
    """Criterion (1): a new persona is born with a voice of the hub's list, so the file it stages is valid."""
    body = (_slices(hub.PAGE) + f"\nslR={json.dumps({'voices': ['Kore', 'Puck']})};"
            "slDocs={committed:{version:1,project:{},personas:[]},local:{}};"
            "const ok=slPersonaAdd(slDocs,'Nova');"
            "console.log(JSON.stringify({ok,p:slDocs.committed.personas[0]}))")
    got = json.loads(_node(body))
    assert got["ok"] is True and got["p"]["voice"] in ("Kore", "Puck"), got


def test_an_added_persona_id_follows_the_declared_name_rule(hub):
    """Criterion (1): a name outside the declared id rule stages no persona, so the save never reports it late."""
    body = (_slices(hub.PAGE) + f"\nslR={json.dumps({'voices': ['Kore', 'Puck']})};"
            "slDocs={committed:{version:1,project:{},personas:[]},local:{}};"
            "const bad=slPersonaAdd(slDocs,'my/persona#1');"
            "const long=slPersonaAdd(slDocs,'x'.repeat(33));"
            "const ok=slPersonaAdd(slDocs,'Nova 2');"
            "console.log(JSON.stringify({bad,long,ok,ids:slDocs.committed.personas.map(p=>p.id)}))")
    got = json.loads(_node(body))
    assert got == {"bad": False, "long": False, "ok": True, "ids": ["Nova 2"]}, got
