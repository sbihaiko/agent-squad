// Card detail rules of the phone page (SPEC 1596, rule 7). Pure: no DOM and no network. The hub inlines this text into
// hub-mobile.html. The primary's six states come from the move facts the board row already carries (`pm`, `pmt`, `pd`,
// `mr`) and the page's own request record, never from a fact of their own.
const ACTION_STATES = ["available", "requested", "confirmed", "failed", "unknown", "refused"];
const twoDigit = n => String(n).padStart(2, "0");
const hhmm = at => { const d = new Date(at); return twoDigit(d.getHours()) + ":" + twoDigit(d.getMinutes()); };
// One primary, one of six true states. `f` is the row's move facts plus the page's request record; `now` is the instant
// the view draws at, so an unknown state names how long it has checked. Requested, unknown and refused never send again.
const primaryView = (f, now) => {
  const to = (f && f.to) || "";
  const base = "Move to " + to;
  const r = (f && f.request) || {};
  if (r.phase === "confirmed") return { state: "confirmed", label: "Moved to " + to, sub: "", disabled: true };
  if (r.phase === "unknown") {
    const secs = Math.max(0, Math.round(((now == null ? r.at : now) - r.at) / 1000));
    return { state: "unknown", label: "Board unreachable", sub: "checking · " + secs + " s", disabled: true };
  }
  if (r.phase === "refused") {
    return { state: "refused", label: base, sub: "refused · " + (r.rule || "a rule forbids it"), disabled: true, secondary: r.rule || "" };
  }
  if (r.phase === "requested" || (f && f.pending)) {
    return { state: "requested", label: base, sub: "requested" + (r.at ? " " + hhmm(r.at) : "") + " · board pending", disabled: true };
  }
  if (r.phase === "failed" || (f && f.refused)) {
    return { state: "failed", label: "Move failed · Retry", sub: r.cause || (f && f.refused) || "", disabled: false };
  }
  return { state: "available", label: base, sub: "", disabled: false };
};
// The card's next step from the facts its row and issue read already carry: a blocker first, then the pull request's CI,
// then the freeze, then the column the board draws it in. No column rule and no wording of its own.
const nextStepOf = (x, column) => {
  const blocked = x.b || [];
  if (blocked.length) return "Blocked by " + blocked.map(n => "#" + n).join(", ") + ".";
  const prs = x.prf || [];
  const failed = prs.find(p => p.ci === "fail");
  if (failed) return "Fix the failing CI on #" + failed.n + ".";
  const waiting = prs.find(p => p.ci && p.ci !== "pass");
  if (waiting) return "Wait for CI on #" + waiting.n + ".";
  if (x.fz) return String(x.fz) + ".";
  return "The card is in " + (column || "no column") + ".";
};
// The latest update the issue read serves: the last comment's author, time and first line, or the last comment's author
// and time alone. A read that carried no comment says so.
const latestUpdateOf = issue => {
  const comments = (issue && issue.comments) || [];
  const c = comments[comments.length - 1];
  if (c) return (c.by || "someone") + " · " + String(c.at || "").slice(0, 16).replace("T", " ") + ": " + String(c.body || "").split("\n")[0];
  if (issue && issue.last) return (issue.last.by || "someone") + " · " + String(issue.last.at || "").slice(0, 16).replace("T", " ");
  return "No update yet.";
};
// The related objects the row and issue name, and the two rows of the card that open on their own: Description and
// Activity. The activity count is the issue read's own `total`, never a count this page keeps.
const relatedOf = (x, issue) => {
  const out = [];
  if (x.pa != null) out.push({ kind: "spec", text: "SPEC " + x.pa });
  if (x.hr) out.push({ kind: "run", text: String(x.hr) });
  for (const p of x.prf || []) out.push({ kind: "pr", n: p.n, text: "#" + p.n + (p.ci ? " · CI " + p.ci : "") });
  out.push({ kind: "description", text: "Description" });
  out.push({ kind: "activity", text: "Activity" + (issue && issue.total ? " · " + issue.total : "") });
  return out;
};
