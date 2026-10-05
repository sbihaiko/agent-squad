// Dashboard core module: how the children of one dispatch are drawn: the group card, the roster, the bus edge (ADR-0125).
// A factory: it takes what lower modules export and returns its own functions. No DOM here.

function groupsModule({ OUTCOME_STATE, STATES, inFlight, isDone, isRunning, childAnchor, childLayout, fmt, freeSpot, num, sumOf }) {
  // Every state has its own glyph (the STATES table): the fill of a square or a card is never the only cue that tells two outcomes apart.
  const MARKS = Object.fromEntries(STATES.map((x) => [x.state, { glyph: x.glyph, label: x.label }]));
  // The glyph and label of a state a card reads, or null for a state with none.
  const stateMark = (state) => (MARKS[state] ? { state, glyph: MARKS[state].glyph, label: MARKS[state].label } : null);

  // The one state of a child: a call in flight reads running, else its outcome, else started, else queued.
  function childMark(c) {
    const state = isRunning(c) ? "running" : c.outcome ? OUTCOME_STATE[c.outcome] || c.outcome : "queued";
    const m = MARKS[state] || { glyph: "?", label: String(state) };
    return { state, glyph: m.glyph, label: m.label };
  }

  // A group needs this many children; fewer stay single cards.
  const MIN_GROUP = 3;

  // The harness of a child: model, effort and skill. Children that run the same harness read as one group while they run.
  const harnessOf = (c) => [c.model, c.effort, c.skillLabel].filter((x) => x !== null && x !== undefined && x !== "").join(" ");

  // The key of the group a child belongs to, or null when it has none: done children group by dispatch and outcome, children in
  // flight (a call running, or started with no outcome yet) by dispatch and harness. A queued child and a rejected request stay cards.
  // The key is a JSON array, so a dispatch name or a harness with a ':' in it cannot make two groups read as one.
  const groupKey = (dispatch, state, harness) => JSON.stringify(["group", dispatch, state, harness || ""]);
  const isGroupKey = (k) => typeof k === "string" && k.startsWith('["group",');
  function groupKeyOf(c) {
    if (c.rejected) return null;
    const s = childMark(c).state;
    if (isDone(s)) return groupKey(c.dispatch, s, "");
    if (inFlight(s)) return groupKey(c.dispatch, "running", harnessOf(c));
    return null;
  }

  // Split the children into group cards and single cards. `expanded` lists the keys the owner opened: those children come back as
  // cards, and `groupOf` says which group each of them came from, so a double-press on one collapses the group again.
  function splitChildren(children, expanded) {
    const open = new Set(expanded || []);
    const by = new Map();
    for (const c of children) { const k = groupKeyOf(c); if (k) by.set(k, (by.get(k) || []).concat([c])); }
    const groups = [], grouped = new Set(), groupOf = {};
    for (const [key, kids] of by) {
      if (kids.length < MIN_GROUP) continue;
      if (open.has(key)) { for (const c of kids) groupOf[c.name] = key; continue; }
      const first = kids[0], st = childMark(first).state, running = inFlight(st);
      const label = running ? "running" : MARKS[st].label;
      const time = sumOf(kids.map((c) => (num(c.elapsed) ? c.elapsed : c.running_for)));
      const cost = sumOf(kids.map((c) => c.spend)), tin = sumOf(kids.map((c) => c.tokens_in)), tout = sumOf(kids.map((c) => c.tokens_out));
      // The summary strings come from core.fmt, the one formatter, so the card, its tooltip and the panel read the same text; tests pin them.
      const lineTime = `${fmt.seconds(time)} of calls · ${fmt.usd(cost)}`, lineTokens = `${fmt.tokens(tin)} in · ${fmt.tokens(tout)} out`;
      for (const c of kids) grouped.add(c.name);
      // A failed group says how many of its children a later attempt replaced, so a collapsed card never reads as an open failure (issue #134).
      const redone = kids.filter((c) => c.superseded_by !== null && c.superseded_by !== undefined).length;
      groups.push({ key, dispatch: first.dispatch, state: running ? "running" : st, label, harness: running ? harnessOf(first) : "", redone, name: `${first.dispatch} · ${kids.length} ${label}${redone ? ` · ${redone} redone` : ""}`, kids, time, cost, tokens_in: tin, tokens_out: tout, lineTime, lineTokens, line: `${lineTime} · ${lineTokens}` });
    }
    return { groups, singles: children.filter((c) => !grouped.has(c.name)), groupOf };
  }

  // The next `expanded` list after a double-press on a group card (adds the key) or on a card of an expanded group (removes it).
  const toggleKey = (expanded, key) => (expanded.includes(key) ? expanded.filter((k) => k !== key) : expanded.concat([key]));

  // Two presses on one name less than DOUBLE_PRESS_MS apart. `last` is {name, t} of the press before, or null.
  const DOUBLE_PRESS_MS = 400;
  const isDoublePress = (last, name, t, within) => !!last && last.name === name && t - last.t < (within || DOUBLE_PRESS_MS);

  // The side panel's table for the children of one dispatch: one row each, in order.
  function childRoster(children, dispatch) {
    return children.filter((c) => c.dispatch === dispatch).map((c) => {
      const m = childMark(c);
      return { index: c.index, name: c.name, label: c.label, state: m.state, glyph: m.glyph, outcome: m.label, model: c.model === null || c.model === undefined ? fmt.count(null) : c.model,
        time: fmt.seconds(c.elapsed), cost: fmt.usd(c.spend), turns: fmt.count(c.turns), rework: c.rework || null };
    });
  }

  // Squares of a group card: eight to a row, so the card is as wide as a child card. The card is as tall as its rows need.
  const SQUARE_STEP = 23, SQUARES_PER_ROW = 8, GROUP_HEAD = 62;
  const groupBox = (n, w) => ({ w, h: GROUP_HEAD + Math.ceil(n / SQUARES_PER_ROW) * SQUARE_STEP + 4 });

  // Where each group card sits: right of its dispatch's entry, like the first child column, or right of the single cards of that
  // dispatch (`after[dispatch]` is the x past them). Two groups of one dispatch stack. `taken` holds the cards already placed; the
  // rows the group covers below its first cell are marked taken too, so a later card does not land on it. `pts` are the far corners,
  // in the shape fit() reads (a point plus the standard box reaches the card's far corner).
  function groupLayout(groups, layout, taken, box, gap, doc, after) {
    const used = { ...taken }, pos = {}, pts = {}, squares = {}, below = {};
    for (const g of groups) {
      const a = doc ? childAnchor(doc, g.dispatch, layout) : layout[g.dispatch];
      if (!a) continue;
      const gb = groupBox(g.kids.length, box.w);
      const x = after && num(after[g.dispatch]) ? after[g.dispatch] : a.x + (doc ? box.w + gap : 2 * (box.w + gap));
      const want = { x, y: Math.max(a.y, below[g.dispatch] === undefined ? a.y : below[g.dispatch]) };
      const p = freeSpot(used, box, want, gap);
      used[g.key] = p;
      let rows = 1;
      for (; rows * (box.h + gap) < gb.h; rows++) used[g.key + "#" + rows] = { x: p.x, y: p.y + rows * (box.h + gap) };
      below[g.dispatch] = p.y + rows * (box.h + gap);   // the next free cell under this card
      pos[g.key] = { ...p, ...gb };
      pts[g.key] = { x: p.x + gb.w - box.w, y: p.y + gb.h - box.h };
      squares[g.key] = g.kids.map((c, i) => ({ name: c.name, x: 10 + (i % SQUARES_PER_ROW) * SQUARE_STEP, y: GROUP_HEAD + Math.floor(i / SQUARES_PER_ROW) * SQUARE_STEP }));
    }
    return { pos, pts, squares };
  }

  // The whole drawing plan of the children: which are groups, which are cards, and where each sits. Single cards take their columns
  // first; a group card sits right of them. A page with no graph places nothing.
  function childPlan(children, expanded, layout, box, gap, doc) {
    const sp = splitChildren(children, expanded), laid = !!(doc && Array.isArray(doc.nodes));
    if (!laid) return { ...sp, childPos: {}, groupPos: {}, groupPts: {}, groupSq: {} };
    const childPos = childLayout(sp.singles, layout, box, gap, doc), after = {};
    for (const c of sp.singles) { const q = childPos[c.name]; if (q) after[c.dispatch] = Math.max(num(after[c.dispatch]) ? after[c.dispatch] : -Infinity, q.x + box.w + gap); }
    const gl = groupLayout(sp.groups, layout, { ...layout, ...childPos }, box, gap, doc, after);
    return { ...sp, childPos, groupPos: gl.pos, groupPts: gl.pts, groupSq: gl.squares };
  }

  // One bus per child column instead of a fan from the entry: a rail BUS_GUTTER px left of the column, a stub into each card, and the rails
  // chained along the gutter under the first row, so no line crosses a card. Segments are {x1, y1, x2, y2, arrow}.
  const BUS_GUTTER = 15;
  function childBus(a, pos, box) {
    const segs = [], seg = (x1, y1, x2, y2, arrow) => segs.push({ x1, y1, x2, y2, arrow: !!arrow });
    if (!a || !pos.length) return segs;
    const cols = {};
    for (const p of pos) (cols[p.x] = cols[p.x] || []).push(p);
    const xs = Object.keys(cols).map(Number).sort((p, q) => p - q);
    const gy = Math.min(...pos.map((p) => p.y)) + box.h + BUS_GUTTER, ay = a.y + box.h / 2;
    xs.forEach((x, i) => {
      const ps = cols[x], rail = x - BUS_GUTTER, ys = ps.map((p) => p.y + box.h / 2), via = i === 0 ? ay : gy;
      // the first rail also drops to the gutter line when a later column hangs off it
      seg(rail, Math.min(...ys, via), rail, Math.max(...ys, via, i === 0 && xs.length > 1 ? gy : -Infinity));
      for (const p of ps) seg(rail, p.y + box.h / 2, p.x - 3, p.y + box.h / 2, true);
      if (i === 0) seg(a.x + box.w, ay, rail, ay); else seg(xs[i - 1] - BUS_GUTTER, gy, rail, gy);
    });
    return segs;
  }

  return { stateMark, childMark, splitChildren, groupKey, isGroupKey, toggleKey, DOUBLE_PRESS_MS, isDoublePress, childRoster, groupLayout, childPlan, childBus };
}
