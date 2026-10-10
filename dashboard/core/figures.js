// Dashboard core module: The numbers every read module sums, and the token and timeline figures of a record.. Moved from rest.js with no change.
// A factory: it takes what lower modules export and returns its own functions. No DOM here.

function figuresModule() {
  // ---- A finished run: statistics, harness, and the hash rule ----
  // Every figure comes from a field the record has today. A field the record lacks is null, never zero (a sum of nulls is null).
  const num = (v) => typeof v === "number" && Number.isFinite(v);

  const sumOf = (vs) => { const xs = vs.filter(num); return xs.length ? xs.reduce((a, b) => a + b, 0) : null; };

  const orNull = (v) => v === undefined ? null : v;

  const lastOf = (xs) => xs.length ? xs[xs.length - 1] : undefined;

  function runTimeline(events) {
    const t0 = events[0].ts;
    return events.map((e) => ({ pos: orNull(e.pos), at: num(e.ts) && num(t0) ? e.ts - t0 : null, event: e.event, node: orNull(e.node), entry: orNull(e.entry),
      outcome: e.event === "verdict" ? e.verdict : orNull(e.outcome) }));
  }

  // Tokens per second is tokens out over elapsed, counted only over the calls that have both and a positive elapsed.
  // A call with no tokens, or with no time, adds nothing to the rate: unknown stays null, never zero.
  function tokenFigures(outs) {
    const timed = outs.filter((o) => num(o.tokens_out) && num(o.elapsed) && o.elapsed > 0);
    const secs = sumOf(timed.map((o) => o.elapsed));
    return { tokens_in: sumOf(outs.map((o) => o.tokens_in)), tokens_out: sumOf(outs.map((o) => o.tokens_out)), tps: secs ? sumOf(timed.map((o) => o.tokens_out)) / secs : null };
  }

  return { num, sumOf, orNull, lastOf, tokenFigures, runTimeline };
}
