// Dashboard core module: Per-node statistics, the harness a node used, and the run statistics.. Moved from rest.js with no change.
// A factory: it takes what lower modules export and returns its own functions. No DOM here.

function statsModule({ causeSummary, dispatchChecks, failureCauses, lastOf, liveState, num, openCalls, orNull, runCap, runChildren, runTimeline, runTotals, skillLabel, sumOf, tokenFigures }) {
  function nodeStats(name, outs, starts, doc) {
    const last = lastOf(outs);
    const ratios = outs.filter((o) => num(o.spend) && num(o.cap) && o.cap > 0).map((o) => o.spend / o.cap);
    const gn = doc && Array.isArray(doc.nodes) ? doc.nodes.find((n) => n && n.name === name) : undefined;
    const usedOf = (k) => { const o = [...outs].reverse().find((x) => x[k] !== undefined && x[k] !== null); return o ? o[k] : null; };
    return {
      entries: outs.length, started: starts > outs.length,
      outcome: last ? last.outcome : null, routed: last ? orNull(last.routed) : null, reasons: last ? last.reasons || [] : [],
      cause: last && last.outcome !== "pass" ? causeSummary(last.reasons) : null, evidence: last && last.evidence ? last.evidence : null,
      elapsed: sumOf(outs.map((o) => o.elapsed)), spend: sumOf(outs.map((o) => o.spend)), turns: sumOf(outs.map((o) => o.turns)),
      cap_pct: ratios.length ? Math.max(...ratios) : null, ...tokenFigures(outs),
      harness: gn && gn.kind === "agent" ? { configured: { model: orNull(gn.model), effort: orNull(gn.effort), tools: orNull(gn.tools) }, used: { model: usedOf("model"), effort: usedOf("effort") } } : null,
    };
  }

  // The engine-level harness the run recorded in run_start: what no node sets.
  function engineHarness(start) {
    const d = (start.defaults && start.defaults.engine) || {}, o = d.options || {}, l = d.limits || {};
    return { permission_mode: orNull(o.permission_mode), setting_sources: orNull(o.setting_sources), disallowed_tools: orNull(o.disallowed_tools),
      max_output_tokens: orNull(l.max_output_tokens), context_tokens: orNull(l.context_tokens), auth: d.auth ? orNull(d.auth.source) : null,
      // Each loadable skill's hash and the vendor commit. A record made before it has neither: null.
      skills: start.skills && typeof start.skills === "object" ? Object.keys(start.skills).sort().map((k) => ({ name: k, hash: start.skills[k].hash, source: start.skills[k].source })) : null,
      skills_commit: orNull(start.skills_commit) };
  }

  function nodeHarness(doc, events, name) {
    if (!name) return null;
    const nodes = doc && Array.isArray(doc.nodes) ? doc.nodes : [];
    const accepted = events.filter((e) => e.event === "spawn_accepted" && e.child === name);
    if (accepted.length) {
      const a = lastOf(accepted), d = a.definition || {};
      const o = lastOf(events.filter((e) => e.event === "outcome" && e.node === name));
      const disp = nodes.find((n) => n && n.kind === "dispatch" && n.name === a.envelope);
      const skill = skillLabel(d.skill);
      const allow = disp ? { ...disp.allow } : null;
      return {
        kind: "child", name, dispatch: a.envelope,
        available: { skill, model: orNull(d.model), effort: orNull(d.effort), tools: Array.isArray(d.tools) ? d.tools : null,
          stalls: orNull(d.stalls), spend: orNull(d.spend), deadline: orNull(d.deadline) },
        allow: allow ? { tools: Array.isArray(allow.tools) ? allow.tools : null, skills: Array.isArray(allow.skills) ? allow.skills.map(skillLabel) : null,
          models: Array.isArray(allow.models) ? allow.models : null, efforts: Array.isArray(allow.efforts) ? allow.efforts : null,
          stalls: disp.budget && disp.budget.stalls ? disp.budget.stalls : null, spend: disp.budget && disp.budget.spend ? disp.budget.spend : null,
          deadline: orNull(disp.deadline) } : null,
        used: { skill, model: o ? orNull(o.model) : null, effort: o ? orNull(o.effort) : null,
          turns: o ? orNull(o.turns) : null, tokens_in: o ? orNull(o.tokens_in) : null, tokens_out: o ? orNull(o.tokens_out) : null,
          spend: o ? orNull(o.spend) : null, elapsed: o ? orNull(o.elapsed) : null },
      };
    }
    const gn = nodes.find((n) => n && n.name === name);
    if (gn && gn.kind === "agent") {
      const outs = events.filter((e) => e.event === "outcome" && e.node === name);
      const usedOf = (k) => { const o = [...outs].reverse().find((x) => x[k] !== undefined && x[k] !== null); return o ? o[k] : null; };
      return {
        kind: "agent", name,
        available: { skill: orNull(gn.skill), model: orNull(gn.model), effort: orNull(gn.effort), tools: Array.isArray(gn.tools) ? gn.tools : null,
          stalls: gn.budget && gn.budget.stalls ? gn.budget.stalls : null, spend: gn.budget && gn.budget.spend ? gn.budget.spend : null, deadline: orNull(gn.deadline) },
        used: { skill: orNull(gn.skill), model: usedOf("model"), effort: usedOf("effort"), turns: sumOf(outs.map((o) => o.turns)),
          tokens_in: sumOf(outs.map((o) => o.tokens_in)), tokens_out: sumOf(outs.map((o) => o.tokens_out)),
          spend: sumOf(outs.map((o) => o.spend)), elapsed: sumOf(outs.map((o) => o.elapsed)) },
      };
    }
    // A gate, a loop, an each, an action, a wait, or a dispatch carries no harness of its own.
    return { kind: "none", name, nodeKind: gn ? gn.kind : null };
  }

  // shown is the overlay's rule: the record's graph hash equals the loaded graph's. When it is false, only the timeline and the verdict stay.
  // now (epoch seconds) makes the state live: running, stalled, or the verdict. Without it the state is a snapshot: the verdict, or "no verdict yet".
  function runStats(events, doc, shown, now) {
    const start = events[0], v = lastOf(events.filter((e) => e.event === "verdict"));
    const live = num(now) ? liveState(events, now) : null;
    const base = { run_id: start.run_id, graph_name: start.graph_name, state: live ? live.state : v ? v.verdict : "no verdict yet",
      verdict: v ? { verdict: v.verdict, exit_code: v.exit_code, reasons: v.reasons || [] } : null, timeline: runTimeline(events), ...(live ? { live } : {}) };
    if (!shown) return base;
    const outs = events.filter((e) => e.event === "outcome"), starts = events.filter((e) => e.event === "call_start");
    const names = [...new Set([...starts, ...outs].map((e) => e.node))];
    const nodes = Object.fromEntries(names.map((nm) => [nm, nodeStats(nm, outs.filter((o) => o.node === nm), starts.filter((c) => c.node === nm).length, doc)]));
    const open = live ? openCalls(events, now) : new Map();
    for (const nm of names) { const c = open.get(nm); nodes[nm].running_for = c ? c.running_for : null; nodes[nm].live = c ? c.live : null; }
    // A gate a dispatch runs on its children has no call of its own: it passes when every child check passed, and fails when one failed.
    for (const [g, c] of Object.entries(dispatchChecks(events, doc))) {
      if (nodes[g]) continue;
      nodes[g] = { ...nodeStats(g, [], 0, doc), outcome: c.fail ? "fail" : "pass", via: c, cause: c.fail ? "check failed" : null, running_for: null };
    }
    // After a verdict, a graph node with no call and no checks never ran. Before it, a node may still run, so nothing is said.
    if (v && doc && Array.isArray(doc.nodes)) for (const n of doc.nodes) if (n && typeof n.name === "string" && !nodes[n.name]) nodes[n.name] = { ...nodeStats(n.name, [], 0, doc), not_run: true, running_for: null };
    return { ...base, nodes, engine: engineHarness(start), totals: runTotals(events, runCap(start, doc), now), causes: failureCauses(events), children: runChildren(events, now) };
  }

  return { nodeStats, engineHarness, nodeHarness, runStats };
}
