// Dashboard core module: The children a dispatch spawned: their rows, layout and checks.. Moved from rest.js with no change.
// A factory: it takes what lower modules export and returns its own functions. No DOM here.

function childrenModule({ num, openCalls, orNull, skillName }) {
  // The issue a spawn ran, read from its own prompt ("issues/14-slug.md" -> 14). The record does not carry the number
  // apart; the prompt names the issue file. A request with no such prompt names none.
  const issueNumber = (prompt) => { const m = /(\d\d)-[\w-]+\.md/.exec(typeof prompt === "string" ? prompt : ""); return m ? Number(m[1]) : null; };

  // A dispatch child's short name for display: route.router.1 -> router.1. The full name stays in the tooltip.
  const shortChildName = (name, dispatch) => (typeof name === "string" && typeof dispatch === "string" && name.startsWith(dispatch + ".") ? name.slice(dispatch.length + 1) : name);

  // The children of every dispatch, in request order. Each carries what its call recorded and what landing did to it.
  // An outcome that is not pass after landing is fail, so a landing failure shows on the child, not only in its own event.
  // `now` (epoch seconds) makes a child that is running now read as running, with its time and its live figures; without
  // it a child in flight keeps the static `started` state.
  function runChildren(events, now) {
    const last = (kind) => new Map(events.filter((e) => e.event === kind).map((e) => [e.node, e]));
    const outs = last("outcome"), lands = last("land"), started = new Set(events.filter((e) => e.event === "call_start").map((e) => e.node));
    const open = num(now) ? openCalls(events, now) : new Map();
    const kids = events.filter((e) => e.event === "spawn_accepted").map((a) => {
      const o = outs.get(a.child), l = lands.get(a.child), d = a.definition || {}, run = open.get(a.child) || null;
      const landFailed = !!(l && l.outcome !== "pass");
      const outcome = landFailed ? "fail" : o ? o.outcome : started.has(a.child) ? "started" : null;
      const reasons = landFailed ? l.reasons || [] : o ? o.reasons || [] : [];
      const evidence = landFailed && l.effect && l.effect.stderr ? Object.fromEntries(reasons.map((r) => [r, l.effect.stderr])) : o && o.evidence ? o.evidence : null;
      const checks = o && o.checks ? o.checks : null;
      const short = shortChildName(a.child, a.envelope);   // the display name: route.router.1 -> router.1
      const issue = issueNumber(d.prompt), skill = skillName(d.skill);
      return { name: a.child, dispatch: a.envelope, proposer: orNull(a.parent), depth: orNull(a.depth), rejected: false, outcome, reasons, evidence, checks, issue, task: orNull(d.task),
        running_for: run ? run.running_for : null, live: run ? run.live : null,   // a child in flight: how long it has run, and what it last reported
        skill: orNull(d.skill), skillLabel: skill,   // the bare skill name, for the detail line
        // The task first: every child of one dispatch usually runs the same skill, so the skill alone tells no two children
        // apart, while the issue does. A child with no task falls back to its skill, then to its own name.
        label: issue === null ? skill || short : `issue ${issue}`,
        model: o && o.model ? o.model : orNull(d.model), effort: o && o.effort ? o.effort : orNull(d.effort), after: Array.isArray(d.after) ? d.after : [],
        workspace: orNull(d.workspace), spend: o ? orNull(o.spend) : null, turns: o ? orNull(o.turns) : null, elapsed: o ? orNull(o.elapsed) : null,
        tokens_in: o ? orNull(o.tokens_in) : null, tokens_out: o ? orNull(o.tokens_out) : null, tps: o && num(o.tokens_out) && num(o.elapsed) && o.elapsed > 0 ? o.tokens_out / o.elapsed : null,
        land: l ? { outcome: l.outcome, head: l.effect ? orNull(l.effect.head) : null, held_out: l.effect ? orNull(l.effect.held_out) : null, reasons: l.reasons || [] } : null };
    });
    const bad = events.filter((e) => e.event === "spawn_rejected").map((e) => ({ name: `${e.envelope} request ${e.request_key}`, dispatch: e.envelope, proposer: orNull(e.parent), depth: null,
      rejected: true, outcome: "fail", reasons: e.reasons || [e.reason], evidence: null, checks: null, skill: null, skillLabel: null, label: `${e.envelope} request ${e.request_key}`,
      model: null, effort: null, after: [], workspace: null, spend: null, turns: null, running_for: null, live: null,
      elapsed: null, tokens_in: null, tokens_out: null, tps: null, land: null, issue: null }));
    // The position of each child among the children of its dispatch, and a title no two children share: "issue 11" can
    // repeat across waves, "3 · issue 11" cannot.
    const seen = {};
    const all = [...kids, ...bad];
    for (const c of all) { c.index = seen[c.dispatch] = (seen[c.dispatch] || 0) + 1; c.title = `${c.index} · ${c.label}`; }
    markRework(all);
    return all;
  }

  // Which children are attempts at one task. Attempts are the children of one dispatch that share a task
  // key: the spawn's own `task`, else the issue its prompt names. The marks are derived text. The record holds no "redo of" fact, so
  // no edge is drawn, and a failed attempt stays failed. It reads superseded once a later attempt of the same task passed.
  // A child passed when its call and its landing passed and its held-out command did not fail.
  const passedClean = (c) => c.outcome === "pass" && !(c.land && c.land.held_out === "fail");

  function reworkText(c, winner) {
    if (winner) return `superseded by ${winner.index} · ${winner.label}, which passed`;
    if (c.attempt === 1) return null;
    if (passedClean(c)) return `passed after ${c.attempt} attempts`;
    const why = c.outcome === "pass" ? "held-out test failed" : c.outcome === "fail" ? "failed" : c.outcome && c.outcome !== "started" ? c.outcome : "";
    return `attempt ${c.attempt}${why ? ", " + why : ""}`;
  }

  // The tasks one child worked: its `task` split at commas, else the issue its prompt names. A router may fold two issues into one child
  // ("13, 21"), so a child can belong to more than one task.
  const taskKeys = (c) => {
    if (c.rejected) return [];
    const t = c.task === null || c.task === undefined ? "" : String(c.task);
    const parts = t.split(/[,\s]+/).filter(Boolean);
    return parts.length ? parts : c.issue === null ? [] : [String(c.issue)];
  };

  function markRework(all) {
    const keys = new Map(all.map((c) => [c, taskKeys(c)]));
    for (const c of all) Object.assign(c, { attempt: null, superseded_by: null, rework: null });
    const same = (c) => all.filter((o) => o.dispatch === c.dispatch && keys.get(o).some((t) => keys.get(c).includes(t)));
    for (const c of all) {
      const mine = keys.get(c);
      if (!mine.length) continue;
      const related = same(c);
      if (related.length < 2) continue;
      const i = related.indexOf(c);
      c.attempt = i + 1;
      // superseded: every task of this child has a later clean attempt. The first such attempt is the one named.
      const later = related.slice(i + 1).filter(passedClean);
      const covered = !passedClean(c) && mine.every((t) => later.some((o) => keys.get(o).includes(t)));
      const winner = covered ? later.find((o) => keys.get(o).some((t) => mine.includes(t))) : null;
      c.superseded_by = winner ? winner.index : null;
      c.rework = reworkText(c, winner);
    }
  }

  // The available-vs-used harness of one selected node, read from the record and the graph, never authored.
  // A dispatch child resolves its own harness in `definition` (squad/envelope.py) and inherits the allow ceiling from its
  // dispatch envelope. A plain agent declares its own fields. A gate or a loop carries no harness, so the
  // caller says so plainly instead of showing an empty list. A missing field stays null, never zero.
  const skillLabel = (s) => (typeof s === "string" ? s : s && typeof s.name === "string" ? (s.version ? `${s.name}@${s.version}` : s.name) : null);

  // A column of child cards past this many rows wraps into a new column, so a wide fan-out (#94: 18 children on a real
  // 24-issue run) spreads across the page instead of running one column far past the viewport.
  const CHILD_COLUMN_HEIGHT = 7;

  // Where each child card sits: right of its dispatch's agent entry, one row each, wrapping into a new column every
  // CHILD_COLUMN_HEIGHT rows, on the nearest free cell. Never saved in a layout file. `doc` is optional, for a caller
  // with no graph to hand (a test fixture); without it a child anchors on the dispatch node itself, one column further
  // out, matching where it sat before #92.
  function childLayout(children, layout, box, gap, doc) {
    const taken = { ...layout }, pos = {}, row = {};
    for (const c of children) {
      const p = doc ? childAnchor(doc, c.dispatch, layout) : layout[c.dispatch];
      if (!p) continue;
      const i = row[c.dispatch] || 0;
      row[c.dispatch] = i + 1;
      const col = Math.floor(i / CHILD_COLUMN_HEIGHT), r = i % CHILD_COLUMN_HEIGHT;
      const stepX = (doc ? box.w + gap : 2 * (box.w + gap)) + col * (box.w + gap);
      pos[c.name] = taken[c.name] = freeSpot(taken, box, { x: p.x + stepX, y: p.y + r * (box.h + gap) }, gap);
    }
    return pos;
  }

  // A gate that a dispatch runs as a `checks` gate has no outcome of its own: its state is what the children's checks said.
  function dispatchChecks(events, doc) {
    const nodes = doc && Array.isArray(doc.nodes) ? doc.nodes : [];
    const kids = runChildren(events).filter((c) => c.checks);
    const out = {};
    for (const d of nodes.filter((n) => n && n.kind === "dispatch" && Array.isArray(n.checks))) {
      for (const g of d.checks) {
        const seen = kids.filter((c) => c.dispatch === d.name && c.checks[g] !== undefined);
        if (seen.length) out[g] = { dispatch: d.name, pass: seen.filter((c) => c.checks[g] === "pass").length, fail: seen.filter((c) => c.checks[g] !== "pass").length, of: seen.length };
      }
    }
    return out;
  }

  // The free place nearest to `want`: `want` itself, else the closest cell of a grid of the node's size (a ring around it, growing).
  function freeSpot(layout, box, want, gap) {
    const taken = Object.values(layout), sw = box.w + gap, sh = box.h + gap;
    const free = (x, y) => !taken.some((p) => Math.abs(p.x - x) < sw && Math.abs(p.y - y) < sh);
    for (let r = 0; r < 60; r++) {
      const ring = [];
      for (let i = -r; i <= r; i++) for (let j = -r; j <= r; j++) if (Math.max(Math.abs(i), Math.abs(j)) === r) ring.push([i, j]);
      ring.sort((p, q) => p[0] * p[0] + p[1] * p[1] - q[0] * q[0] - q[1] * q[1] || p[1] - q[1]);
      for (const [i, j] of ring) if (free(want.x + i * sw, want.y + j * sh)) return { x: want.x + i * sw, y: want.y + j * sh };
    }
    return { ...want };
  }

  // Where a dispatch's own edges and children anchor: the position of its agent entry, one layer right of the
  // dispatch itself, so a child two layers out draws from an adjacent column instead of past the entry and its checks.
  function childAnchor(doc, dispatchName, layout) {
    const nodes = doc && Array.isArray(doc.nodes) ? Object.fromEntries(doc.nodes.map((n) => [n.name, n])) : {};
    const dn = nodes[dispatchName];
    return (dn && dn.entry && layout[dn.entry]) || layout[dispatchName] || null;
  }

  return { issueNumber, shortChildName, runChildren, skillLabel, CHILD_COLUMN_HEIGHT, childLayout, dispatchChecks, freeSpot, childAnchor };
}
