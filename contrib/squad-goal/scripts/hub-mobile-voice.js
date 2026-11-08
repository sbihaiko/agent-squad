// Voice rules of the phone page (SPEC 1596, frames 17 and 19, rule 10; #1603). Pure: no DOM and no network. The hub
// inlines this text into hub-mobile.html. Holding the mic records with the timer, the waveform, the cancel zone, the three
// hints and the two-minute cap; a release sends, a slide into the cancel zone cancels, and the cap stops the recording.
// A spoken answer is one Kore clip that plays as the answer streams, and its control is one play or pause with elapsed
// over total: the clip is the whole answer, never its first sentence (owner, #1603).
const VOICE_CAP_SECONDS = 120;   // the two-minute cap: the recording stops and sends there, whatever the thumb does
const VOICE_CANCEL_PX = 96;      // how far left the thumb slides to enter the cancel zone
const VOICE_BARS = 24;           // the waveform's bars: a fixed width, so the row never jumps while the mic moves
// "0:07", "1:06": minutes and seconds, never a bare count of seconds.
const voiceTime = s => {
  const n = Math.max(0, Math.floor(Number(s) || 0));
  return Math.floor(n / 60) + ":" + String(n % 60).padStart(2, "0");
};
// The recording starts at the hold's own clock; the thumb's slide and the cancel zone are the row's, not the page's.
const voiceStart = now => ({ phase: "recording", at: Number(now) || 0 });
// A slide left past the zone arms cancel and a slide back out disarms it, so the zone is never a blind guess.
const voiceSlide = (r, dx) => Object.assign({}, r, { dx: Number(dx) || 0, into: (Number(dx) || 0) <= -VOICE_CANCEL_PX });
const voiceRelease = r => ({ phase: r && r.into ? "cancel" : "send" });
// The elapsed seconds, capped at the two-minute bound, so the timer never reads past the cap it stops on.
const voiceElapsed = (r, now) => Math.min(VOICE_CAP_SECONDS, Math.max(0, ((Number(now) || 0) - r.at) / 1000));
// One tick: below the cap the recording goes on; at the cap it stops and sends.
const voiceTick = (r, now) => ({ phase: voiceElapsed(r, now) >= VOICE_CAP_SECONDS ? "send" : "recording" });
const voiceHints = () => ["Release to send", "Slide left to cancel", "max " + voiceTime(VOICE_CAP_SECONDS)];
// What the row shows for one moment: the phase, the elapsed time, whether the zone is armed and the three hints.
const voiceRow = (r, now) => ({ phase: r.phase, time: voiceTime(voiceElapsed(r, now)), into: !!r.into, hints: voiceHints() });
const voiceBarCount = () => VOICE_BARS;
// The waveform is a function of the measured level, the tick and the bar: no clock and no microphone here, so the row
// redraws the same bars for the same reading and the test reads it without a browser. A silent microphone draws one
// flat line, and the bars rise with the level an AnalyserNode measures (rule 10; #1603).
const voiceWave = (level, tick, bars) => {
  const amp = Math.min(1, Math.max(0, Number(level) || 0));
  return Array.from({ length: bars === undefined ? VOICE_BARS : bars },
    (_, i) => Math.round(4 + 10 * amp * Math.abs(Math.sin((i + 1) * 0.7 + (Number(tick) || 0) * 0.9))));
};
// One clip per answer: the answer's whole text reads as one clip in Kore's voice. A tool line, the owner's own words and
// an empty answer carry no clip; a sentence boundary never splits one (#1603).
const voiceClip = (turn, voice) => ({ k: turn.k, voice: voice || "Kore" });
const voiceClips = turns => (turns || [])
  .filter(t => t && t.role === "answer" && String(t.text || "").trim())
  .map(t => voiceClip(t));
// The one control's two words, and the elapsed over the total the clip fills in once it knows its length.
const clipLabel = playing => (playing ? "Pause" : "Play");
const clipTime = (elapsed, total) => voiceTime(elapsed) + " / " + voiceTime(total);
