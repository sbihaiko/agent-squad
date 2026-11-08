// Pause and resume of the phone's run screen (#831). Pure: no DOM and no network. The hub inlines this text into
// hub-mobile.html. Every request goes to the run's own control route through the hub's /dash/ forward route (ADR-0118,
// ADR-0152): the phone names no loopback port and no tailnet host, so the tailnet entry stays disabled until #713 proves
// the forwarded headers, and only squad/tailnet_access.py changes if it disagrees.
const RUN_LIMITS = "A pause lets a running call finish. A resume does not restart a run whose process ended.";
// The four paths of one run, all under the hub's forward route: the page, the one write, and the two reads it decides on.
const runURLs = id => {
  const run = "/dash/" + encodeURIComponent(id);
  return { page: run + "/", control: run + "/run/control", state: run + "/run/control.json", info: run + "/run/info.json",
           record: run + "/run/record.jsonl" };
};
// The body of one request, the desktop page's own shape. No nodes is the whole run, and the phone asks for nothing else.
const controlBody = (action, nodes) => JSON.stringify(nodes && nodes.length ? { action, nodes } : { action });
// What the screen offers, from the server's facts and no rule of its own: `ended` is the run server's answer
// (ADR-0118 rule 19), and a hold already asked is the control file's answer. The resume the phone sends releases every
// hold, as the desktop page's whole-run resume does. An ended run and a dashboard that does not answer are offered no
// request at all.
const runControlView = (info, state, live) => {
  if (!info) return { act: null, label: "", note: "This run's dashboard does not answer. The screen cannot say whether the run takes a pause or a resume." };
  if (info.ended || live === false) return { act: null, label: "", note: "The run has ended. It takes no pause and no resume." };
  if (state && (state.whole || (state.nodes || []).length)) return { act: "resume", label: "Ask to resume", note: "The run is asked to pause. A resume releases the hold; the run continues when squad resume runs." };
  return { act: "pause", label: "Pause the run", note: "The run stops before its next task. A call that is already running finishes first." };
};
// The age the screen shows, and the word that says what it measures: the time since start while live, since end once over.
const runSpan = s => s < 3600 ? Math.floor(s / 60) + " min" : (s / 3600).toFixed(1) + " h";
const runAgeLabel = (row, now) => {
  if (row.running) return row.started ? "running for " + runSpan(Math.max(0, now - row.started)) : "no start recorded";
  return row.ended ? "ended " + runSpan(Math.max(0, now - row.ended)) + " ago" : "no end recorded";
};
// The one primary of the run's action row follows the run's own state: a question the coder waits on comes first, then the
// session the run last worked in, and a run that offers neither keeps the dashboard it always has. The pause and its
// resume stay the row's one secondary, so the primary never repeats it.
const runPrimary = (row, card, sid) => {
  if (card) return { act: "session", label: "Answer the coder" };
  if (row && row.running !== false && sid) return { act: "session", label: "Open the session" };
  return { act: "desk", label: "Desktop dashboard" };
};
// The session the run last worked in, named by the newest record line that carries one; a line that is no JSON is skipped.
const runSessionId = text => {
  let sid = "";
  for (const line of String(text || "").split("\n")) {
    if (!line.trim()) continue;
    let ev;
    try { ev = JSON.parse(line); } catch (e) { continue; }
    if (ev && typeof ev.session === "string" && ev.session) sid = ev.session;
  }
  return sid;
};
// The last lines of the session's conversation, with who spoke: the record of the run, cut to what one phone screen shows.
const RUN_TAIL = 6, RUN_WHO = { owner: "You", answer: "Coder", system: "System" };
const runTailLines = (turns, n) => (turns || [])
  .filter(t => t && typeof t.text === "string" && t.text.trim())
  .slice(-(n || RUN_TAIL))
  .map(t => (RUN_WHO[t.role] || "Coder") + ": " + t.text.trim().replace(/\s+/g, " "));
// The verdict an ended run shows, and the issues the overview lists, one line each with the title the row carries.
const runVerdict = row => row.running ? "" : row.verdict || (row.running === false ? "killed, no verdict" : "");
const runIssueLines = row => (row.issues || []).map(n => "#" + n + (row.titles && row.titles[n] ? " " + row.titles[n] : "")).join("\n");
// What the answer says. The control route takes one line and starts nothing (ADR-0118 rule 18), so a resume says so.
const runControlReply = (act, status) => {
  if (status === 200) return { ok: true, text: act === "resume"
    ? "Resume requested. The hold is released; the run continues when squad resume runs, because this screen never starts a process."
    : "Pause requested. A call that is already running finishes first, and the run stops before its next task." };
  if (status === 409) return { ok: false, text: "The run has ended. A resume does not restart a run whose process ended." };
  return { ok: false, text: "The hub refused the request (" + status + ")." };
};
