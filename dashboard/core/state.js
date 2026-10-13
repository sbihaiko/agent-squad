// Dashboard core module: The one state a card shows, and the class it paints.. Moved from rest.js with no change.
// A factory: it takes what lower modules export and returns its own functions. No DOM here.

function stateModule({ num }) {
  // ---- The one state a node's card shows ----
  // Every node the run has touched reads one definite state, so a root or a structural node is never an ambiguous
  // flat grey. A node that never ran (after the verdict) reads "not-run", not a plain card. The states reuse the
  // legend's: passed, failed, deadline, needs-human, running, started, and not-run.
  const OUTCOME_STATE = { pass: "passed", fail: "failed", deadline: "deadline", "needs-human": "needs-human" };

  // One table for every state a card reads: its class, glyph, label and legend row. The card class table, the mark table and the legend
  // are all derived from it, so a state added here shows in all three. `queued` has no class: a child not started stays unmarked.
  // The order is the legend's: what the reader does, not what the engine does. Nothing (passed, not run), look (failed, deadline,
  // started with no outcome), wait (running), act (needs a decision). A legend row names the record's state and the reader's move, in one line.
  // `started` is a call_start with no outcome and no live call: the run ended inside the call, or the replay cursor sits inside it.
  const STATES = [
    { state: "passed", cls: "o-pass", glyph: "✓", label: "passed", legend: "Passed" },
    { state: "not-run", cls: "o-not-run", glyph: "∅", label: "not run", legend: "Not run: the run ended first" },
    { state: "failed", cls: "o-fail", glyph: "✗", label: "failed", legend: "Failed: read the cause" },
    { state: "deadline", cls: "o-deadline", glyph: "◷", label: "deadline", legend: "Deadline hit: counts as failed" },
    { state: "started", cls: "o-started", glyph: "…", label: "started", legend: "Started, no outcome recorded" },
    { state: "running", cls: "running", glyph: "▶", label: "running", legend: "Running now: wait" },
    { state: "needs-human", cls: "o-needs-human", glyph: "!", label: "needs-human", legend: "Needs your decision" },
    { state: "queued", cls: null, glyph: "–", label: "not started", legend: null },
  ];
  const STATE_CLASS = Object.fromEntries(STATES.filter((x) => x.cls).map((x) => [x.state, x.cls]));
  // A call in flight reads running; a call started with no outcome yet reads started. Both are "in flight" for grouping and the group card.
  const inFlight = (state) => state === "running" || state === "started";
  // A state with an outcome: the call is over.
  const DONE = new Set(Object.values(OUTCOME_STATE));
  const isDone = (state) => DONE.has(state);
  // A node or a child whose call is running now (its stats carry the time it has run).
  const isRunning = (x) => !!x && num(x.running_for);

  // The primary state of a node's card. `running` wins over an earlier outcome, since a node running now is running now.
  // A missing stats entry (ns null) is not a state: the node is not in the record and its card stays unmarked.
  function nodeState(ns) {
    if (!ns) return null;
    if (isRunning(ns)) return "running";
    if (ns.not_run) return "not-run";
    if (ns.outcome) return OUTCOME_STATE[ns.outcome] || ns.outcome;
    if (ns.started) return "started";
    return "not-run";
  }

  // The card class for a node's state, or null when the node is not in the record.
  const stateClass = (ns) => { const s = nodeState(ns); return s ? STATE_CLASS[s] || "o-" + s : null; };

  // The class a child card paints. A child reads the same states a node reads, except the never-ran
  // marker: a child whose call has not started is queued, not done, so its card stays unmarked. Without this a child
  // in flight kept the static `started` outline for the whole call and showed a result only at the end.
  function childStateClass(c) {
    if (!c) return null;
    if (isRunning(c)) return "running";
    return c.outcome ? STATE_CLASS[OUTCOME_STATE[c.outcome] || c.outcome] || null : null;
  }

  return { OUTCOME_STATE, STATES, STATE_CLASS, inFlight, isDone, isRunning, nodeState, stateClass, childStateClass };
}
