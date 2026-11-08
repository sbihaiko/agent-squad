// Loop rules of the phone page (SPEC 1596, rule 8). Pure: no DOM and no network. The hub inlines this text into
// hub-mobile.html. One loop belongs to one project: the strip aggregates the fact the hub serves, the Loops list names
// every project, and one loop page writes to one project. The states are the six the action row already carries, and
// the refusal names the rule (the open loop-bugs) that holds it.
const LOOP_WORDS = { on: "On", off: "Off", draining: "Draining", starting: "Starting" };
const loopStateWord = s => LOOP_WORDS[s] || String(s || "");
const loopCounts = projects => {
  const c = { on: 0, off: 0, draining: 0, starting: 0 };
  for (const p of projects || []) c[p.state] = (c[p.state] || 0) + 1;
  return c;
};
// "2 on · 1 off", and " · 1 draining" while a project drains. A hub that serves no project says so; it never reads as a
// single unnamed switch.
const loopAggregate = projects => {
  const list = projects || [];
  if (!list.length) return "No project";
  const c = loopCounts(list), parts = [];
  if (c.on) parts.push(c.on + " on");
  if (c.off) parts.push(c.off + " off");
  if (c.draining) parts.push(c.draining + " draining");
  if (c.starting) parts.push(c.starting + " starting");
  return parts.join(" · ");
};
const loopWhen = at => { const m = String(at || "").match(/^(\d{4}-\d\d-\d\d)T(\d\d:\d\d)/); return m ? m[1] + " " + m[2] : String(at || ""); };
// The last change of one project: who, from which page, when, and what. The transport is the fallback source when the
// writer named no page, so a change always states a source.
const loopChangeLine = e => e
  ? [e.who, e.client || e.from, loopWhen(e.at), String(e.action || "")].filter(Boolean).join(" · ")
  : "no change yet";
const loopRun = r => String(r || "").split("/").filter(Boolean).pop() || String(r || "");
const loopLiveNames = live => ((live || {}).runs || []).map(r => "run " + loopRun(r)).concat((live || {}).gates || []);
// The row under the strip and in the Loops list: the state, the live work, the last landing and the last change, so one
// row answers everything the owner opens the list for.
const loopRowLine = p => {
  const live = ((p && p.live) || {}).runs || [];
  const out = [loopStateWord(p && p.state)];
  out.push(live.length ? live.length + " live" : "no live run");
  if (p && p.landing) out.push("landed #" + p.landing.issue);
  out.push(loopChangeLine(p && p.change));
  return out.join(" · ");
};
// The one primary of a loop page, in one of the six states. Drain is the default stop, so a running loop offers it; a
// draining loop offers Cancel drain, because the primary never does the opposite of the state. Stop now is never the
// primary: it sits under the dots menu. An off loop with open loop-bugs refuses the start and names the count, and the
// secondary opens those bugs.
const loopPrimary = (p, write, reason, now) => {
  if (!write) return { state: "refused", label: "Refused", sub: reason || "a write is refused here", disabled: true };
  const state = (p && p.state) || "off";
  if (state === "starting") return { state: "requested", label: "Starting", sub: "waiting for the hub", disabled: true };
  if (state === "draining") return { state: "available", label: "Cancel drain", sub: "the loop stays up when the work ends", disabled: false, act: "start" };
  if (state === "on") return { state: "available", label: "Drain", sub: "stops after the live work", disabled: false, act: "drain" };
  const bugs = (p && p.bugs) || 0;
  if (bugs) return { state: "refused", label: "Start", sub: "refused · " + bugs + " loop-bug" + (bugs === 1 ? "" : "s") + " open", disabled: true, secondary: "Loop-bugs", secondary_act: "bugs" };
  return { state: "available", label: "Start", sub: "", disabled: false, act: "start" };
};
// Stop now stays under the dots menu, and only while the loop runs: a stopped loop has nothing to kill.
const loopStopNowOffered = (p, write) => !!write && !!p && (p.state === "on" || p.state === "draining");
