// Chat rules of the phone page (SPEC 1596, rules 9 and 10; #1601). Pure: no DOM and no network. The hub inlines this
// text into hub-mobile.html. One conversation per project: the Chat root draws one row per project the session list
// groups, the Chat badge counts the rows that wait, and Now › Needs you lists those rows inside the scope. The loop dot
// and the live runs come from the hub's loop fact, joined by the project name, never from the session list (owner, #1601).
const CHAT_ALL = "All projects";
const chatLoop = (name, projects) => (projects || []).find(p => p
  && (p.slug === name || String(p.repo || "").split("/").pop() === name)) || null;
// The transcript's mtime is the row's time (owner, #1601): read as UTC so a row never moves with the reader's clock.
const chatTime = t => {
  const n = Number(t);
  if (!n) return "";
  const d = new Date(n * 1000);
  return isNaN(d.getTime()) ? "" : d.toISOString().slice(0, 16).replace("T", " ");
};
// One row per project: its waiting session leads (that is the conversation the row opens), the last message and the time
// are the project's own, and the loop dot and live runs are the loop fact's.
const chatRows = (payload, projects) => {
  const groups = payload && Array.isArray(payload.groups) ? payload.groups : [];
  const rows = groups.map(g => {
    const ss = (g.sessions || []).filter(s => s && s.sid);
    const lead = ss.slice().sort((a, b) => (!!b.waiting - !!a.waiting) || ((b.t || 0) - (a.t || 0))
      || String(a.name || "").localeCompare(String(b.name || "")))[0] || {};
    const lp = chatLoop(g.project, projects);
    return { project: g.project || "", sid: lead.sid || "", name: lead.name || "", last: lead.last || "",
             run: lead.run || "", t: ss.reduce((m, s) => Math.max(m, Number(s.t) || 0), 0),
             waiting: ss.filter(s => s.waiting).length,
             loop: (lp && lp.state) || "", lives: ((lp && lp.live) || {}).runs || [] };
  });
  return rows.sort((a, b) => (b.waiting - a.waiting) || String(a.project).localeCompare(String(b.project)));
};
// One number for the Chat tab: the waiting rows of the scope. "All projects" counts every project, so on that scope the
// badge is the hub's whole waiting count (rule 10; owner, #1601).
const chatBadge = (rows, scope) => (rows || [])
  .filter(r => !scope || scope === CHAT_ALL || r.project === scope)
  .reduce((n, r) => n + (Number(r.waiting) || 0), 0);
const chatRowLine = r => [r.last || "No message yet", chatTime(r.t)].filter(Boolean).join(" · ");
const chatWaiting = r => (r.waiting ? "Waiting for you" + (r.waiting > 1 ? " (" + r.waiting + ")" : "") : "");
const chatHeader = r => [r.loop ? "Loop " + r.loop : "Loop unknown",
  r.lives.length ? r.lives.length + (r.lives.length === 1 ? " live run" : " live runs") : "No live run"].join(" · ");
// The run the session belongs to, when the hub served one: the page links it and shows no link otherwise (owner, #1601).
const chatActedOn = r => (r && r.run ? { run: r.run, label: "Acted on · run " + r.run.slice(-6) } : null);
