// Dashboard core module: Where a pause leaves the run (ticket #107, ADR-0128).
// A factory: it takes what lower modules export and returns its own functions. No DOM here.

function controlModule({ lastOf }) {
  // The page shows what the engine recorded and never works the frontier out (ADR-0117 rule 10): the `paused` event is the resume point,
  // shown as the record holds it. A pause counts for the last start of the run only (`run_start`, or a `resume`), and only while the run
  // is without a verdict, or its verdict is the `stopped` that a pause writes.
  //   state "live"   : a run with no verdict yet. A request may still pause it.
  //   state "paused" : the run ended on a pause. `held` and `completed` are the engine's own lists.
  //   state "ended"  : any other verdict. The server takes no request for it.
  function pauseView(events) {
    const verdict = lastOf(events.filter((e) => e.event === "verdict"));
    const start = lastOf(events.filter((e) => e.event === "run_start" || e.event === "resume"));
    const since = start ? start.pos : 0;
    const pause = lastOf(events.filter((e) => e.event === "paused" && e.pos > since));
    const run = events.length ? events[0].run_id : null;
    if (verdict && verdict.pos > since) {
      const byPause = verdict.verdict === "stopped" && (verdict.reasons || []).some((r) => String(r).startsWith("paused:"));
      if (!byPause) return { state: "ended", node: null, held: [], completed: [], resume: null };
    }
    if (!pause) return { state: "live", node: null, held: [], completed: [], resume: null };
    return { state: "paused", node: pause.node, held: pause.held.slice(), completed: pause.completed.slice(), resume: `squad resume ${run}` };
  }

  // The body of one request. `nodes` empty or null means the whole run.
  function controlBody(action, nodes) {
    return JSON.stringify(nodes && nodes.length ? { action, nodes } : { action });
  }

  return { pauseView, controlBody };
}
