"""Voice on the phone (SPEC 1596, frames 17 and 19, rule 10; #1603): holding the mic shows the recording row with its
timer, its waveform, the cancel zone, the three hints and the two-minute cap; release sends the recording, a slide into
the cancel zone cancels it, and reaching the cap stops it. A spoken answer is one Kore clip, played as the answer
streams, with one play or pause control and elapsed over total. The clip control is never one per sentence.

The rules are one client module (`hub-mobile-voice.js`) and the page binds them. The page is booted under node over the
stub DOM of `tests/test_hub_mobile_move.py`, whose `fetch` answers the routes here and records every write. A fake
recorder and a fake microphone stand in for the browser's, so no microphone and no browser is touched.
"""
import json

from tests.test_hub_mobile_move import SCRIPTS, hub, run_js, run_page  # noqa: F401  (hub is the page fixture)

VOICE_JS = SCRIPTS / "hub-mobile-voice.js"
SID = "11111111-1111-1111-1111-111111111111"

CONVERSATION = {"turns": [
    {"id": "a1", "k": "0:1", "role": "owner", "text": "Which scope?", "at": "", "m": ""},
    {"id": "a2", "k": "0:2", "role": "answer", "text": "All projects. The badge counts every project. One clip speaks it.",
     "at": "", "m": "claude"},
], "next": None, "activity": {}, "epoch": 9, "end": 0}

ROUTES = {
    "/api/sessions": [{"body": json.dumps({"waiting": 1, "groups": [
        {"project": "alpha", "sessions": [{"sid": "s1", "name": "run node one", "status": "waiting", "waiting": True,
                                           "last": "Which scope?", "t": 1_700_000_000, "run": ""}]}]})}],
    "/api/loop": [{"body": json.dumps({"projects": [{"slug": "alpha", "repo": "x/a", "state": "on", "live": {"runs": [], "gates": []}}],
                                       "changes": []})}],
    "/api/sessions/card": [{"body": json.dumps({"card": None})}],
    "/api/sessions/conversation": [{"body": json.dumps(CONVERSATION)}],
    "/api/sessions/voice": [{"body": json.dumps({"status": "sent"})}],
}

# The browser pieces the recorder needs, in the page's own context: a microphone that answers one stream, and a recorder
# that yields one audio chunk when it stops. The page resolves both at the gesture, never at load.
FAKES = """
navigator.mediaDevices = { getUserMedia: async () => ({ getTracks: () => [{ stop() { this.stopped = true; } }] }) };
globalThis.MediaRecorder = class {
  constructor(stream, opts) { this.stream = stream; this.opts = opts || {}; this.state = "inactive"; }
  start() { this.state = "recording"; }
  stop() {
    this.state = "inactive";
    const bytes = new Uint8Array([1, 2, 3, 4]);
    if (this.ondataavailable) this.ondataavailable({ data: { size: bytes.length, arrayBuffer: async () => bytes.buffer } });
    if (this.onstop) this.onstop();
  }
};
"""


def test_a_hold_starts_the_recording_and_the_row_names_the_two_hints_and_the_cap():
    src = VOICE_JS.read_text()
    out = run_js(src, """(() => {
        const r = voiceStart(1000);
        return { phase: r.phase, time: voiceTime(0), hints: voiceHints(), cap: VOICE_CAP_SECONDS,
                 row: voiceRow(voiceStart(1000), 1000) };
    })()""")
    assert out["phase"] == "recording", out
    assert out["time"] == "0:00", out
    assert out["hints"] == ["Release to send", "Slide left to cancel", "max 2:00"], out
    assert out["cap"] == 120, out
    assert out["row"]["phase"] == "recording" and out["row"]["time"] == "0:00", out
    assert out["row"]["hints"] == ["Release to send", "Slide left to cancel", "max 2:00"], out


def test_the_timer_reads_minutes_and_seconds_and_the_waveform_follows_the_mic():
    src = VOICE_JS.read_text()
    out = run_js(src, """(() => {
        const r = voiceStart(1000);
        return { t7: voiceRow(r, 8000).time, t59: voiceRow(r, 60000).time, t66: voiceRow(r, 67000).time,
                 wave: voiceWave(0, 5), wave2: voiceWave(1, 5), bars: voiceBarCount() };
    })()""")
    assert out["t7"] == "0:07", out
    assert out["t59"] == "0:59", out
    assert out["t66"] == "1:06", out
    assert len(out["wave"]) == 5 and len(out["wave2"]) == 5, out
    assert out["wave"] != out["wave2"], "the waveform moves with the mic: " + repr(out)
    assert out["bars"] >= 16, out


def test_a_release_outside_the_zone_sends_and_a_slide_into_it_cancels():
    src = VOICE_JS.read_text()
    out = run_js(src, """(() => {
        const r = voiceStart(1000);
        return { send: voiceRelease(voiceSlide(r, -10)).phase,
                 cancel: voiceRelease(voiceSlide(r, -400)).phase,
                 armed: voiceRow(voiceSlide(r, -400), 2000).into,
                 disarmed: voiceRow(voiceSlide(voiceSlide(r, -400), 0), 2000).into };
    })()""")
    assert out["send"] == "send", out
    assert out["cancel"] == "cancel", out
    assert out["armed"] is True and out["disarmed"] is False, "sliding back out disarms the zone: " + repr(out)


def test_the_two_minute_cap_stops_the_recording():
    src = VOICE_JS.read_text()
    out = run_js(src, """(() => {
        const r = voiceStart(1000);
        return { early: voiceTick(r, 119000).phase, at: voiceTick(r, 121000).phase,
                 capped: voiceRow(r, 121000).time, elapsed: voiceElapsed(r, 121000) };
    })()""")
    assert out["early"] == "recording", out
    assert out["at"] == "send", "the cap stops the recording and sends it: " + repr(out)
    assert out["capped"] == "2:00", out
    assert out["elapsed"] == 120, out


def test_a_spoken_answer_is_one_kore_clip_never_one_per_sentence():
    src = VOICE_JS.read_text()
    out = run_js(src, """(() => {
        const turns = [
          { k: "0:1", role: "owner", text: "Which scope?" },
          { k: "0:2", role: "answer", text: "All projects. The badge counts every project. One clip speaks it." },
          { k: "0:3", role: "answer", text: "Second answer." },
        ];
        const clips = voiceClips(turns);
        return { n: clips.length, keys: clips.map(c => c.k), voice: clips[0].voice,
                 time: clipTime(6, 21), play: clipLabel(false), pause: clipLabel(true) };
    })()""")
    assert out["n"] == 2, "one clip per answer, never one per sentence: " + repr(out)
    assert out["keys"] == ["0:2", "0:3"], out
    assert out["voice"] == "Kore", out
    assert out["time"] == "0:06 / 0:21", out
    assert out["play"] == "Play" and out["pause"] == "Pause", out


def _voice_page(hub, body, routes=None, page=None):  # noqa: F811
    return run_page(page or hub.MOBILE_PAGE, FAKES + """
        openConversation("s1", "alpha");
        await __tick(30);
        """ + body, routes=routes or ROUTES)


def test_holding_the_mic_shows_the_recording_row_and_a_release_sends_the_recording(hub):  # noqa: F811
    routes = dict(ROUTES, **{"/api/sessions/voice": [{"body": json.dumps({"status": "sent"})}]})
    out = _voice_page(hub, """
        const mic = $("termview-mic");
        mic.listeners.pointerdown[0]({ clientX: 300, clientY: 700, preventDefault() {} });
        await __tick(30);
        __skip(7000);
        window.__intervals.filter(i => i.ms === 250).forEach(i => i.fn());
        const rec = $("termview-rec");
        const shown = { hidden: rec.hidden, time: $("voice-time").textContent,
                        hints: $("voice-hints").querySelectorAll(".voicehint").map(h => h.textContent),
                        bars: $("voice-wave").children.length, cancel: !!$("voice-cancel") };
        __dispatch("pointerup", { clientX: 300, clientY: 700 });
        await __tick(50);
        const post = __state.posts.find(p => p.path === "/api/sessions/voice");
        return { shown, after: rec.hidden, url: post ? post.url : "", receipt: $("termview-sent").textContent };
    """, routes=routes)
    shown = out["shown"]
    assert shown["hidden"] is False, "a hold shows the recording row: " + repr(out)
    assert shown["time"] == "0:07", out
    assert shown["hints"] == ["Release to send", "Slide left to cancel", "max 2:00"], out
    assert shown["bars"] >= 16 and shown["cancel"] is True, out
    assert out["after"] is True, "the release takes the row away: " + repr(out)
    assert "/api/sessions/voice" in out["url"] and "sid=s1" in out["url"] and "client_id=" in out["url"], out
    assert out["receipt"].startswith("Sent"), out


def test_a_slide_into_the_cancel_zone_arms_it_and_the_release_cancels_with_no_send(hub):  # noqa: F811
    out = _voice_page(hub, """
        $("termview-mic").listeners.pointerdown[0]({ clientX: 300, clientY: 700, preventDefault() {} });
        await __tick(30);
        __dispatch("pointermove", { clientX: 120, clientY: 700 });
        const armed = { into: $("voice-cancel").dataset.into, cls: $("voice-cancel").className };
        __dispatch("pointerup", { clientX: 120, clientY: 700 });
        await __tick(50);
        return { armed, hidden: $("termview-rec").hidden,
                 posts: __state.posts.filter(p => p.path === "/api/sessions/voice").length };
    """)
    assert out["armed"]["into"] == "1" and "into" in out["armed"]["cls"], out
    assert out["hidden"] is True, out
    assert out["posts"] == 0, "a cancelled recording is never sent: " + repr(out)


def test_the_two_minute_cap_stops_the_recording_and_sends_it(hub):  # noqa: F811
    out = _voice_page(hub, """
        $("termview-mic").listeners.pointerdown[0]({ clientX: 300, clientY: 700, preventDefault() {} });
        await __tick(30);
        __skip(121000);
        window.__intervals.filter(i => i.ms === 250).forEach(i => i.fn());
        await __tick(50);
        return { hidden: $("termview-rec").hidden,
                 posts: __state.posts.filter(p => p.path === "/api/sessions/voice").length };
    """)
    assert out["hidden"] is True, "the cap stops the recording: " + repr(out)
    assert out["posts"] == 1, "and the capped recording is sent: " + repr(out)


def test_an_answer_carries_one_kore_clip_with_one_play_or_pause_and_elapsed_over_total(hub):  # noqa: F811
    out = _voice_page(hub, """
        const turn = $("termview-turns").querySelectorAll(".turn").find(t => t.dataset.k === "0:2");
        const box = turn.querySelectorAll(".clip");
        const play = box[0].querySelector(".clip-play");
        const audio = box[0].__audio;
        audio.currentTime = 6; audio.duration = 21;
        audio.listeners.timeupdate[0]({});
        const reading = { time: box[0].querySelector(".clip-time").textContent, label: play.textContent,
                          voice: box[0].querySelector(".clip-voice").textContent };
        play.listeners.click[0]({});
        await __tick(30);
        const playing = { label: play.textContent, state: play.dataset.playing, src: audio.src };
        play.listeners.click[0]({});
        return { clips: box.length, reading, playing, paused: play.textContent, owner: turn.dataset.k };
    """)
    assert out["clips"] == 1, "one clip per answer, never one per sentence: " + repr(out)
    assert out["reading"] == {"time": "0:06 / 0:21", "label": "Play", "voice": "Kore"}, out
    assert out["playing"]["label"] == "Pause" and out["playing"]["state"] == "1", out
    assert "/api/sessions/clip" in out["playing"]["src"], out
    assert out["paused"] == "Play", out


def test_a_streamed_answer_plays_its_one_clip_as_it_arrives(hub, monkeypatch):  # noqa: F811
    monkeypatch.setenv("SQUAD_HUB_STREAM", "1")   # the pushed page names its stream, as the served page does
    out = _voice_page(hub, page=hub.mobile_html().decode(), body="""
        const es = __state.streams[__state.streams.length - 1];
        const line = JSON.stringify({ id: "a9", k: "0:9", role: "answer", text: "Live answer. Two sentences.", at: "", m: "claude" });
        es.handlers.conversation[0]({ data: JSON.stringify({ sid: "s1", epoch: 9, end: 2, lines: line }) });
        await __tick(30);
        const turn = $("termview-turns").querySelectorAll(".turn").find(t => t.dataset.k === "0:9");
        const box = turn.querySelectorAll(".clip");
        return { clips: box.length, playing: box.length ? box[0].dataset.playing : "",
                 label: box.length ? box[0].querySelector(".clip-play").textContent : "",
                 src: box.length ? box[0].__audio.src : "" };
    """)
    assert out["clips"] == 1, "a streamed answer draws one clip: " + repr(out)
    assert out["playing"] == "1" and out["label"] == "Pause", "the clip plays as the answer streams: " + repr(out)
    assert "/api/sessions/clip" in out["src"] and "k=0%3A9" in out["src"], out
