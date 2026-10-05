// Dashboard core module: layout and view: auto layout, layout for save, fit, zoom, pan (ticket #117). A factory: it takes what it uses from the modules below it, and returns its functions. No DOM here.
// scripts/build_dashboard.py bundles every file of dashboard/core/ in dependency order; nothing loads one file alone.

function layoutModule() {
  // A node's children, by graph edge: `children` plus, for a dispatch, its agent entry and its check gates.
  function graphKids(n) {
    if (!n) return [];
    return [...(Array.isArray(n.children) ? n.children : []), ...(n.kind === "dispatch" ? [n.entry, ...(n.checks || [])] : [])];
  }

  // A layered, dependency-free DAG layout (ADR-0111 keeps the dashboard pure and Node-tested; a layout library would break
  // that, and a plain layered pass is enough for a graph this small). Two passes:
  //
  // 1. Layer: a node's column is the longest path from the root, by relaxation (as many rounds as there are nodes, which
  //    bounds any DAG's longest path). An edge then never points left, and a node reached two ways sits right of every
  //    parent, so growing the graph never pushes an existing node's column left.
  // 2. Order: within a column, nodes sort by the mean row of their parents in the column before (the barycenter
  //    heuristic), a few sweeps to settle. This keeps a node's row near its parent's, so the graph spreads out as
  //    siblings arrive instead of stacking in first-seen order, and keeps related nodes close, which cuts how often an
  //    edge crosses a third node on its way across the page.
  // The grid: a column is COL_STEP wide, a row ROW_STEP tall, and the first cell sits ORIGIN from the corner.
  const COL_STEP = 230, ROW_STEP = 96, ORIGIN = 20;
  function autoLayout(doc) {
    const nodes = Object.fromEntries(doc.nodes.map((n) => [n.name, n]));
    const names = Object.keys(nodes);
    const kidsOf = (nm) => graphKids(nodes[nm]).filter((c) => nodes[c]);
    const parentsOf = (nm) => names.filter((p) => kidsOf(p).includes(nm));

    const layer = Object.fromEntries(names.map((n) => [n, n === doc.root ? 0 : -Infinity]));
    for (let i = 0; i < names.length; i++) for (const n of names) if (layer[n] > -Infinity) for (const c of kidsOf(n)) layer[c] = Math.max(layer[c], layer[n] + 1);
    const maxLayer = Math.max(0, ...names.map((n) => layer[n]).filter((v) => v > -Infinity));
    for (const n of names) if (layer[n] === -Infinity) layer[n] = maxLayer + 1;   // unreachable from the root: past everything, not mixed in

    const order = Object.fromEntries(names.map((n, i) => [n, i]));   // the tiebreak for a column's first sweep: the order nodes appear in the graph
    for (let pass = 0; pass < 3; pass++) {
      for (let col = 1; col <= maxLayer + 1; col++) {
        const row = names.filter((n) => layer[n] === col);
        const score = (n) => { const ps = parentsOf(n).filter((p) => layer[p] < col); return ps.length ? ps.reduce((s, p) => s + order[p], 0) / ps.length : order[n]; };
        row.sort((a, b) => score(a) - score(b) || order[a] - order[b]);
        row.forEach((n, i) => { order[n] = i; });
      }
    }
    const pos = {};
    for (const n of names) pos[n] = { x: ORIGIN + layer[n] * COL_STEP, y: ORIGIN + order[n] * ROW_STEP };
    return pos;
  }

  // The layout file is a map from node name to position: a key for a node that is gone is dropped on the next layout save.
  function layoutForSave(layout, names) {
    const keep = new Set(names), out = {}, dropped = [];
    for (const k of Object.keys(layout)) { if (keep.has(k)) out[k] = layout[k]; else dropped.push(k); }
    return { layout: out, dropped };
  }

  // The view is {k, tx, ty}: a graph point (x, y) shows at (x*k + tx, y*k + ty). Pure, so node can test it (ticket #72).
  const VIEW_MIN = 0.1, VIEW_MAX = 3;
  const clampK = (k) => Math.min(VIEW_MAX, Math.max(VIEW_MIN, k));
  function viewFit(layout, box, size, pad) {
    const ps = Object.values(layout);
    if (!ps.length) return { k: 1, tx: pad, ty: pad };
    const x0 = Math.min(...ps.map((p) => p.x)), y0 = Math.min(...ps.map((p) => p.y));
    const w = Math.max(...ps.map((p) => p.x)) + box.w - x0, h = Math.max(...ps.map((p) => p.y)) + box.h - y0;
    const k = Math.min(1, clampK(Math.min((size.w - 2 * pad) / w, (size.h - 2 * pad) / h)));
    return { k, tx: (size.w - w * k) / 2 - x0 * k, ty: (size.h - h * k) / 2 - y0 * k };
  }
  // The fit, but never smaller than `minK`: a graph that would shrink below it is shown at `minK` instead, with the graph point `anchor` at the
  // top-left (by `pad`), so the part that matters stays readable and the rest is a pan away. Pure, so node can test it.
  function viewFitFloor(layout, box, size, pad, minK, anchor) {
    const v = viewFit(layout, box, size, pad);
    return v.k >= minK || !anchor ? v : { k: minK, tx: pad - anchor.x * minK, ty: pad - anchor.y * minK };
  }
  function viewZoomAt(v, cx, cy, factor) {
    const k = clampK(v.k * factor), r = k / v.k;
    return { k, tx: cx - (cx - v.tx) * r, ty: cy - (cy - v.ty) * r };
  }
  const viewPanBy = (v, dx, dy) => ({ k: v.k, tx: v.tx + dx, ty: v.ty + dy });

  // Pan, never zoom, so a graph rect {x, y, w, h} shows inside the map minus the insets {left, right, top, bottom}: the left inset is the
  // side panel, which overlays the map. A rect already inside leaves the view as it is. A rect wider than the free space keeps its
  // left edge in view. Pure, so node can test it.
  function viewReveal(v, rect, size, insets) {
    const l = rect.x * v.k + v.tx, r = l + rect.w * v.k, t = rect.y * v.k + v.ty, b = t + rect.h * v.k;
    const x0 = insets.left, x1 = size.w - insets.right, y0 = insets.top, y1 = size.h - insets.bottom;
    const fix = (lo, hi, a, z) => (a < lo ? lo - a : z > hi ? Math.max(hi - z, lo - a) : 0);
    const dx = fix(x0, x1, l, r), dy = fix(y0, y1, t, b);
    return dx || dy ? viewPanBy(v, dx, dy) : v;
  }

  return { autoLayout, graphKids, layoutForSave, viewFit, viewFitFloor, viewZoomAt, viewPanBy, viewReveal };
}
