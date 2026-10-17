// Dashboard core module: The run record: parse, the live reread, and the adaptation overlay.. Moved from rest.js with no change.
// modelreg: seam-clean
// A factory: it takes what lower modules export and returns its own functions. No DOM here.

function recordModule({ graphHash, models }) {
  const REG = models || {}, ALIASES = REG.aliases || {}, PRICES = REG.prices || {}, PROVIDERS = REG.providers || {};
  // The registry id a spelling names. An alias resolves; an unknown spelling keeps its raw form.
  const canonical = (m) => ALIASES[m] || m;

  // ---- Run record (read-only) and the overlay ----
  // A line that is not JSON ends the record there: the lines before it are shown, the line and the lines after it are not, and
  // `bad` names the line and what was wrong. The page never skips the bad line to read on: events after a gap would be a wrong run.
  // With no valid prefix (line 1 is bad, or the first event is not run_start) there is nothing to show, and `error` says why.
  // The SDK prices a model it does not know at Claude rates, so a gateway call is re-priced from its tokens at the
  // registry's display price. The registry holds one input rate, so a cache read is priced at that rate too. A Claude
  // call keeps the SDK figure, and an id the registry does not know is never re-priced.
  function repriced(ev) {
    if (ev.event !== "outcome" || typeof ev.model !== "string" || typeof ev.tokens_in !== "number") return ev;
    const id = canonical(ev.model), price = PRICES[id];
    if (!price || typeof price.input !== "number" || typeof price.output !== "number" || PROVIDERS[id] === "claude") return ev;
    return { ...ev, spend_sdk: ev.spend, spend: (ev.tokens_in * price.input + (ev.tokens_out || 0) * price.output) / 1e6 };
  }
  function parseRecord(text) {
    const events = [];
    const lines = text.split("\n");
    let bad = null;
    for (let n = 0; n < lines.length && !bad; n++) {
      if (!lines[n].trim()) continue;
      try {
        const ev = JSON.parse(lines[n]);
        if (!ev || typeof ev !== "object" || Array.isArray(ev)) bad = { line: n + 1, message: "the line is not a JSON object" }; else events.push(repriced(ev));
      } catch (e) { bad = { line: n + 1, message: e.message }; }
    }
    if (!events.length) return { error: bad ? `Record line ${bad.line} is not JSON: ${bad.message}` : "A run record starts with a run_start event." };
    if (!events[0] || events[0].event !== "run_start") return { error: "A run record starts with a run_start event." };
    return bad ? { events, bad: { ...bad, shown: events.length } } : { events };
  }

  // A reread of a record the engine is still writing can catch a half-written last line. Keep the complete lines only.
  function parseLive(text) {
    const cut = text.lastIndexOf("\n");
    return parseRecord(cut < 0 ? "" : text.slice(0, cut + 1));
  }

  const REPORT_NOTE = "one observation, not a measurement: each entry is a fresh session, so the two entries may be different tasks";

  const REPORT_INSTRUMENT = "code: outcome events of the run record; spend is the measured SDK result; elapsed is wall-clock";

  const FIELDS = ["outcome", "spend", "turns", "elapsed"];

  const entryOf = (e) => e === undefined ? null : { entry: e.entry, pos: e.pos, reasons: e.reasons, ...Object.fromEntries(FIELDS.map((k) => [k, e[k] === undefined ? null : e[k]])) };

  function deltaOf(prev, next) {
    if (!prev || !next) return Object.fromEntries(FIELDS.map((k) => [k, null]));
    const out = { outcome: `${prev.outcome} -> ${next.outcome}` };
    for (const k of ["spend", "turns", "elapsed"]) out[k] = prev[k] === null || next[k] === null ? null : Math.round((next[k] - prev[k]) * 1e6) / 1e6;
    return out;
  }

  function overlayRows(events) {
    const outs = events.filter((e) => e.event === "outcome");
    return events.filter((e) => e.event === "applied").map((a) => {
      const mine = outs.filter((o) => o.node === a.target);
      const prev = [...mine].reverse().find((o) => o.pos < a.pos);
      const next = mine.find((o) => o.pos > a.pos);
      const p = entryOf(prev), n = entryOf(next);
      return { proposal_id: a.proposal_id, kind: a.kind, target: a.target, old: a.old, requested: a.requested, range: a.range, facts: a.facts,
        proposer: a.proposer, previous: p, next: n, delta: deltaOf(p, n), instrument: REPORT_INSTRUMENT, note: REPORT_NOTE };
    });
  }

  // The overlay shows only when the record's graph hash equals the loaded graph's hash.
  async function overlayFor(graphText, events) {
    const start = events.find((e) => e.event === "run_start");
    let hash;
    try { hash = await graphHash(graphText); } catch (e) { return { shown: false, reason: "The loaded graph text has no canonical form: " + e.message }; }
    if (start.graph_hash !== hash) return { shown: false, reason: `The record's graph hash (${String(start.graph_hash).slice(0, 12)}…) differs from the loaded graph's (${hash.slice(0, 12)}…). No overlay.`, hash };
    return { shown: true, hash, rows: overlayRows(events) };
  }

  return { parseRecord, parseLive, REPORT_NOTE, REPORT_INSTRUMENT, FIELDS, entryOf, deltaOf, overlayRows, overlayFor };
}
