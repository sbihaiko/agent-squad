// Dashboard core module: A run still being written: the read delay, the calls in flight, the live state.. Moved from rest.js with no change.
// A factory: it takes what lower modules export and returns its own functions. No DOM here.

function liveModule({ lastOf, num, orNull }) {
  // The key one call is known by: its node, its entry, and its item. A call_start, the progress events of that call, and
  // its outcome all carry the same three, so the record's own fields pair them without a new index.
  const callKey = (e) => `${e.node}|${e.entry}|${orNull(e.item)}`;

  // Where each call in flight has got to, as the call itself last reported it. A running call records a
  // `progress` event now and then; the last one for a call wins. A call that reported nothing has no entry here, so its
  // figures stay unknown, never zero.
  function callProgress(events) {
    const out = new Map();
    for (const e of events) if (e.event === "progress") out.set(callKey(e), { messages: orNull(e.messages), context_tokens: orNull(e.context_tokens), tool: orNull(e.tool) });
    return out;
  }

  // The open call of each node, with the time it has run and what it last reported. A node runs one entry at a time, so
  // the map is keyed by the node; where two entries of one node are open, the longer-running one is the one shown.
  // A call whose call_start carries no start time has no duration, so it makes no entry: unknown time stays unknown.
  // A run with a verdict has nothing in flight (liveState says the same): a call_start left open by a killed call must
  // never read as running again.
  function openCalls(events, now) {
    const prog = callProgress(events), out = new Map();
    if (lastOf(events.filter((e) => e.event === "verdict"))) return out;
    for (const c of inFlight(events, now)) {
      const prev = out.get(c.node);
      if (num(c.since) && (!prev || c.since > prev.running_for)) out.set(c.node, { running_for: c.since, live: prog.get(callKey(c)) || null });
    }
    return out;
  }

  // ---- A live run ----
  // The reread interval is an engine setting that run_start records. A record made before it, or a bad value, gets one second.
  function readDelay(events) {
    const d = events && events[0] && events[0].defaults && events[0].defaults.engine && events[0].defaults.engine.dashboard;
    const r = d && d.reread_seconds;
    return num(r) && r > 0 ? r : 1;
  }

  // Stalled is a display state, not a gate: no new event for longer than the longest call in flight may take, plus a grace.
  // With no call in flight, a gate command or an effect can be quiet for a while, so the idle period is longer.
  const STALL_IDLE = 300, STALL_GRACE = 60;

  // A call in flight is a call_start with no outcome for the same node, entry and item. A gate_start opens a gate's
  // check the same way (#680); a record made before it has none, and its gates never read as in flight.
  const STARTS = new Set(["call_start", "gate_start"]);
  function inFlight(events, now) {
    const done = new Set(events.filter((e) => e.event === "outcome").map((e) => `${e.node}|${e.entry}|${orNull(e.item)}`));
    return events.filter((e) => STARTS.has(e.event) && !done.has(`${e.node}|${e.entry}|${orNull(e.item)}`))
      .map((e) => ({ node: e.node, entry: e.entry, item: orNull(e.item), since: num(e.started_at) ? now - e.started_at : null, limit: e.limit }));
  }

  function liveState(events, now) {
    const v = lastOf(events.filter((e) => e.event === "verdict"));
    const flight = v ? [] : inFlight(events, now);
    // A call with no deadline (limit null) may run as long as it likes, so the run is never called stalled while it is in flight.
    const limits = flight.map((c) => c.limit);
    const stallAfter = limits.some((l) => !num(l)) ? null : (limits.length ? Math.max(...limits) : STALL_IDLE) + STALL_GRACE;
    const end = lastOf(events), sinceLast = num(end.ts) ? now - end.ts : null;
    const state = v ? v.verdict : stallAfter !== null && sinceLast !== null && sinceLast > stallAfter ? "stalled" : "running";
    return { state, since_last: sinceLast, stall_after: stallAfter, in_progress: flight.map(({ node, entry, item, since }) => ({ node, entry, item, since })) };
  }

  return { readDelay, STALL_IDLE, inFlight, liveState, callKey, callProgress, openCalls };
}
