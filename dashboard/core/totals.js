// Dashboard core module: The totals bar and the top bar of issue progress.. Moved from rest.js with no change.
// modelreg: seam-clean
// A factory: it takes what lower modules export and returns its own functions. No DOM here.

function totalsModule({ lastOf, num, sumOf, tokenFigures, models }) {
  const REG = models || {};
  // ---- The totals bar (owner request after #86) ----
  // The cap comes from the run's own options, else from the loaded graph. No cap is null.
  function runCap(start, doc) {
    return num((start.engine_options || {}).spend_cap) ? start.engine_options.spend_cap : doc && doc.run && num(doc.run.spend_cap) ? doc.run.spend_cap : null;
  }

  // The run totals over `events`: a whole record, or the cut a replay cursor makes, so a cursor at pos P shows what the run had spent by P.
  // Tokens are null when no call recorded them (never zero). `partial_tokens` is true when an agent call has a spend but no tokens: the sum is a floor.
  // `tps` is tokenFigures' estimate: output tokens over the summed elapsed time of the calls that have both. Parallel calls add their times,
  // so the rate is per call-second, not per wall second. `wall` counts on from run_start while the run is live (now given, no verdict).
  function runTotals(events, cap, now) {
    const start = events[0], end = events[events.length - 1], v = lastOf(events.filter((e) => e.event === "verdict"));
    const outs = events.filter((e) => e.event === "outcome"), spend = sumOf(outs.map((o) => o.spend)), tok = tokenFigures(outs);
    const partial = tok.tokens_out !== null && outs.some((o) => num(o.spend) && (!num(o.tokens_in) || !num(o.tokens_out)));
    return { spend, turns: sumOf(outs.map((o) => o.turns)), ...tok, partial_tokens: partial, cap, cap_pct: spend !== null && cap ? spend / cap : null,
      wall: num(start.ts) ? (num(now) && !v ? now - start.ts : num(end.ts) ? end.ts - start.ts : null) : null };
  }

  // The bar's cells as data: a label, the text, and a tooltip that names the rule. Unknown tokens read "unknown", never 0.
  function totalsCells(t) {
    const f = fmt, unk = "unknown", floor = t.partial_tokens ? "≥ " : "";
    return [
      { id: "cost", label: "Cost", text: t.spend === null ? unk : `${f.usd(t.spend)}${t.cap === null ? "" : ` of $${t.cap} (${f.pct(t.cap_pct)})`}`,
        title: "Sum of the spend on every outcome so far. The cap is the run's spend cap." },
      { id: "time", label: "Time", text: t.wall === null ? unk : f.seconds(t.wall),
        title: "Run time. While the run is live it counts on from run_start; otherwise it is the span from run_start to the last event shown." },
      { id: "in", label: "Tokens in", text: t.tokens_in === null ? unk : floor + f.tokens(t.tokens_in),
        title: t.partial_tokens ? "Sum over the calls that recorded tokens. Some agent call has no tokens, so this is a floor." : "Sum of tokens in over every call that recorded them." },
      { id: "out", label: "Tokens out", text: t.tokens_out === null ? unk : floor + f.tokens(t.tokens_out),
        title: t.partial_tokens ? "Sum over the calls that recorded tokens. Some agent call has no tokens, so this is a floor." : "Sum of tokens out over every call that recorded them." },
      { id: "tps", label: "tk/s (estimate)", text: t.tps === null ? unk : "~" + f.rate(t.tps),
        title: "Estimate: tokens out divided by the summed elapsed time of the calls that have both. Calls that overlap in time add their times, so this is a rate per call-second, not per wall-clock second." },
    ];
  }

  // ---- The top bar: the issues the run landed, of the issue set ----
  // The record carries no issue-set size, so `total` comes from the run folder the page was given (the page counts
  // `target/issues/*.md` and passes the number in). `done` is the distinct issues a `land` event landed, read from
  // the record's own ledger: no new event type. Both figures stay null-ish when the record shows nothing.
  function issueProgress(events, total, held) {
    const lands = new Set(events.filter((e) => e.event === "land" && e.outcome === "pass").map((e) => e.node));
    // A one-issue run lands through the merge_land action, which logs no `land` event. Its issue is delivered when
    // the merge passed and the run completed, so the gate after the merge held; the set is that one issue, not the folder's.
    const merged = events.some((e) => e.event === "outcome" && e.outcome === "pass" && e.effect && e.effect.name === "merge_land");
    if (lands.size === 0 && (merged || (num(held) && held > 0))) {
      const done = merged && events.some((e) => e.event === "verdict" && e.verdict === "completed") ? 1 : 0;
      // A chained run starts from a tree that already holds `held` issues: the strip counts them, of the folder's set.
      if (num(held) && held > 0 && num(total)) return { done: held + done, total, text: `${held + done} of ${total} issues` };
      return { done, total: 1, text: `${done} of 1 issue` };
    }
    const done = lands.size;
    const t = num(total) ? total : null;
    return { done, total: t, text: t === null ? `${done} landed` : `${done} of ${t} issue${t === 1 ? "" : "s"}` };
  }

  // The top bar's cells, the same shape as totalsCells: a label, the text, and a tooltip that names the rule.
  // With no issue set (a single-run folder, or no run folder at all) the bar is empty and the page hides it.
  function progressCells(p) {
    if (p.total === null) return [];
    return [{ id: "issues", label: "Issues", text: p.text,
      title: "Issues the run landed, of the issue set in the run folder. A land event marks one landed issue, and a one-issue run counts its passing merge once the run completes; the run folder's target/issues holds the set of a multi-issue run. The record itself carries no issue count." }];
  }

  // One place for the unit and the dash. A missing value is a dash, never a zero.
  const DASH = "\u2014";

  const fmt = {
    seconds: (v) => !num(v) ? DASH : v === 0 ? "0s" : v < 1 ? v.toFixed(2) + "s" : v < 60 ? v.toFixed(1) + "s" : `${Math.floor(v / 60)}m ${String(Math.round(v % 60)).padStart(2, "0")}s`,
    usd: (v) => !num(v) ? DASH : "$" + v.toFixed(v < 10 ? 3 : 2),
    pct: (v) => !num(v) ? DASH : v === 0 ? "0%" : v * 100 < 1 ? "<1%" : Math.round(v * 100) + "%",
    count: (v) => !num(v) ? DASH : String(v),
    tokens: (v) => !num(v) ? DASH : v < 1000 ? String(v) : v < 9950 ? (v / 1e3).toFixed(1) + "k" : v < 1e6 ? Math.round(v / 1e3) + "k" : (v / 1e6).toFixed(1) + "M",
    rate: (v) => !num(v) ? DASH : (v === 0 ? "0" : v < 10 ? v.toFixed(1) : String(Math.round(v))) + " tk/s",
    // A model reads as its registry label. An alias resolves to its canonical id first, so an old record still shows the
    // right name. A harness phrase (`model effort skill`) starts with the id and keeps the rest. An id the registry does
    // not know renders raw: display never guesses. The record, the graph and an editable field keep the id whole.
    // An empty value is a dash, like every other missing figure here.
    model: (m) => {
      if (m === null || m === undefined || m === "") return DASH;
      const text = String(m), head = text.split(/\s+/)[0];
      const id = (REG.aliases || {})[head] || head, label = (REG.labels || {})[id];
      return label ? label + text.slice(head.length) : text;
    },
    // A box that stands for a model (#677): the registry label, as `model` reads it, and an id the registry does not
    // know marked "unregistered" in plain words. Every spelling a route serves is in the alias map, so no parse here.
    llm: (m) => {
      if (m === null || m === undefined || m === "") return DASH;
      const text = String(m), head = text.split(/\s+/)[0], label = labelOf(head);
      return (label || head + " \u00b7 unregistered") + text.slice(head.length);
    },
  };

  function labelOf(head) {
    const id = (REG.aliases || {})[head] || (REG.spellings || {})[head] || head;
    return (REG.labels || {})[id] || null;
  }

  // What a node card says about its LLM (#677, ADR-0137): the card renders this and decides nothing. An agent shows
  // the model the record says it used, else the graph's model marked "configured". A node that calls no model says
  // "no LLM" and what it runs instead; a loop or an each is control only. A review-command gate runs a command that
  // picks its own LLM, so it claims none. The card prefixes the kind, so the text stays short enough for the card.
  // `node` is the graph node, `ns` its run statistics (runStats), or null before the record touches it.
  function nodeModel(node, ns) {
    const n = node || {}, kind = n.kind;
    if (kind === "agent") {
      const used = ns && ns.harness && ns.harness.used ? ns.harness.used.model : null;
      const m = used || n.model || null;
      if (!m) return { llm: true, source: null, registered: null, text: "model not set" };
      return { llm: true, source: used ? "used" : "configured", registered: !!labelOf(m), text: fmt.llm(m) + (used ? "" : " (configured)") };
    }
    const none = (text) => ({ llm: false, source: null, registered: null, text: "no LLM \u00b7 " + text });
    if (kind === "gate") {
      const argv = n.params && Array.isArray(n.params.argv) ? n.params.argv : null;
      if (n.check === "review-command") {
        // `sh -c 'body' name ...` names its program in the slot after the body, as $0.
        const shell = argv && /^(?:.*\/)?(?:ba|z)?sh$/.test(String(argv[0])) && argv[1] === "-c";
        const prog = shell ? (argv[3] || "a shell command") : argv && argv.length ? argv[0] : "a command";
        return { llm: null, source: null, registered: null, text: `${String(prog).split("/").pop()} \u00b7 own LLM` };
      }
      return none(n.check || "a check");
    }
    if (kind === "action") return none(n.effect || "an action");
    if (kind === "wait") return none(`waits for ${n.owner || "a person"}`);
    if (kind === "dispatch") return none("children");
    if (kind === "loop" || kind === "each") return { llm: false, source: null, registered: null, text: "no LLM" };
    return none(kind || "unknown kind");
  }

  return { runCap, runTotals, totalsCells, issueProgress, progressCells, DASH, fmt, nodeModel };
}
