// --- the graph area: an SVG with one group that pan and zoom move ---
function applyView() {
  const v = S.view;
  $("vp").setAttribute("transform", `translate(${v.tx} ${v.ty}) scale(${v.k})`);
  $("zoom").textContent = Math.round(v.k * 100) + "%";
}
function mapSize() { const r = $("map").getBoundingClientRect(); return { w: r.width, h: r.height }; }
// What sits on the map's bottom edge and the graph must clear: #footer, the row that carries the hint and the replay bar (#1813).
// Measured from the map's own bottom edge to the row's top, so a layout that lifts the row — the bottom sheet under 900px — lifts
// the reserve with it, and no constant has to be kept in step with the stylesheet.
function bottomReserve() {
  const f = $("footer"); if (!f) return FOOTER_PAD;
  const m = $("map").getBoundingClientRect();
  return Math.max(0, m.bottom - f.getBoundingClientRect().top) + FOOTER_PAD;
}
// The side panel is a drawer at the right edge, so an open panel takes its width off the area the graph is fitted into and the
// fitted view stays at the map's left edge, unshifted.
// `floorKey` (the key of a group just expanded) keeps the fit from shrinking below READABLE_K: the view then starts at the group's dispatch,
// and the fit stays off (S.fitted false) so a live update does not shrink it again. The Fit button fits all.
function fit(floorKey) {
  const s = mapSize(), side = sideOpen() ? $("left").offsetWidth : 0;
  const all = { ...S.layout, ...S.childPos, ...S.groupPos, ...S.groupPts }, size = { w: Math.max(MIN_FIT, s.w - side), h: Math.max(MIN_FIT, s.h - bottomReserve()) };
  const anchor = floorKey ? floorAnchor(floorKey) : null, v = core.viewFitFloor(all, BOX, size, PAD, anchor ? READABLE_K : 0, anchor);
  S.view = { ...v };
  S.floored = !!(anchor && v.k === READABLE_K); S.fitted = !S.floored; applyView();
}
// The top-left of what an expanded group shows: its dispatch's entry and the first row of its cards.
function floorAnchor(key) {
  const d = JSON.parse(key)[1], a = core.childAnchor(S.doc, d, S.layout), ps = S.children.filter((c) => c.dispatch === d && S.childPos[c.name]).map((c) => S.childPos[c.name]);
  return a && ps.length ? { x: a.x, y: Math.min(a.y, ...ps.map((p) => p.y)) } : null;
}
function zoomBy(factor, cx, cy) { const s = mapSize(); S.view = core.viewZoomAt(S.view, cx === undefined ? s.w / 2 : cx, cy === undefined ? s.h / 2 : cy, factor); S.fitted = false; applyView(); }

// The side panel covers the right of the map. A selection that would sit under it, or off the map, pans the view (never the zoom)
// until it shows left of the drawer; one already in view moves nothing, and a closed panel moves nothing.
function revealSelected() {
  if (!sideOpen() || !S.selected) return;
  const name = S.selected, grp = S.groups.find((g) => g.key === name || g.kids.some((c) => c.name === name));
  const r = S.groupPos[name] || (grp && S.groupPos[grp.key]) || S.childPos[name] || S.layout[name];
  if (!r) return;
  const v = core.viewReveal(S.view, { x: r.x, y: r.y, w: r.w || BOX.w, h: r.h || BOX.h }, mapSize(), { left: REVEAL_INSET, right: $("left").offsetWidth + REVEAL_INSET, top: REVEAL_INSET, bottom: Math.max(bottomReserve(), REVEAL_INSET) });
  if (v !== S.view) { S.view = v; applyView(); }
}

function edgeLines() {
  const lines = [];
  for (const n of nodesOf()) {
    for (const c of core.graphKids(n)) {
      const a = S.layout[n.name], b = S.layout[c];
      if (!a || !b) continue;
      const sel = S.link && S.link.from === n.name && S.link.to === c;
      const co = `x1="${a.x + BOX.w}" y1="${a.y + BOX.h / 2}" x2="${b.x - 3}" y2="${b.y + BOX.h / 2}"`;
      lines.push(`<g class="edge${sel ? " sel" : ""}" data-from="${esc(n.name)}" data-to="${esc(c)}"><line class="hit" ${co}/><line class="ln" ${co} marker-end="url(#arrow)"/></g>`);
    }
  }
  // One bus per dispatch: one line leaves the entry, a rail serves each column of single cards and group cards.
  for (const d of [...new Set([...S.children.map((c) => c.dispatch), ...S.groups.map((g) => g.dispatch)])]) {
    const a = core.childAnchor(S.doc, d, S.layout);
    const pos = [...S.children.filter((c) => c.dispatch === d && S.childPos[c.name]).map((c) => S.childPos[c.name]), ...S.groups.filter((g) => g.dispatch === d && S.groupPos[g.key]).map((g) => S.groupPos[g.key])];
    const segs = a ? core.childBus(a, pos, BOX) : [];
    if (segs.length) lines.push(`<g class="edge child">${segs.map((q) => `<line class="ln" x1="${q.x1}" y1="${q.y1}" x2="${q.x2}" y2="${q.y2}"${q.arrow ? ' marker-end="url(#arrow)"' : ""}/>`).join("")}</g>`);
  }
  return lines.join("");
}

// The note a redo carries, joined to a label or a title by `sep`, or nothing.
const reworkNote = (c, sep) => (c.rework ? sep + esc(c.rework) : "");

// The cards for the children of a dispatch. They select and show detail; they do not drag, since no layout file holds them.
function renderChildren(cards) {
  for (const c of S.children) {
    const p = S.childPos[c.name];
    if (!p) continue;
    const g = document.createElementNS(SVGNS, "g");
    const running = core.isRunning(c);
    const live = c.live || null;
    const oc = running ? "running" : c.outcome;
    const sc = core.childStateClass(c);   // running, passed, failed, deadline, needs-human, started
    g.setAttribute("class", "card child" + (S.selected === c.name ? " sel" : "") + (sc ? " " + sc : "") + (S.groupOf[c.name] ? " ingroup" : "") + (c.superseded_by ? " superseded" : ""));
    g.setAttribute("transform", `translate(${p.x} ${p.y})`);
    g.dataset.name = c.name; g.dataset.child = "1";
    focusable(g, `${c.title}, ${core.childMark(c).label}${c.rework ? ", " + c.rework : ""}`);
    const f = core.fmt, landed = c.land ? (c.land.outcome === "pass" ? " · landed" : " · not landed") : "";
    // The task is the card's title, so the line under it names the role and the harness instead.
    const role = c.skillLabel ? `${c.skillLabel} · ` : "";
    const kind = c.rejected ? "rejected request" : `${role}${f.model(c.model)}${c.effort ? " " + c.effort : ""}${landed}`;
    // A call in flight has no outcome yet, so its own figures are all unknown: the card shows how long it has run and
    // what the call itself reported. Without this the card showed three dashes for the whole call.
    const stat = c.rejected ? "" : running ? runningHead(c.running_for, live)
      : [f.seconds(c.elapsed), f.usd(c.spend), f.count(c.turns) + (c.turns === null ? "" : "t")].join(" · ");
    const tok = c.rejected ? "" : live ? progressLine(live) : `${f.tokens(c.tokens_in)} in · ${f.tokens(c.tokens_out)} out · ${f.rate(c.tps)}`;
    const cause = c.outcome === "fail" ? core.causeSummary(c.reasons) : null;
    const mark = core.childMark(c);
    // A redo: the last line says which attempt this is. A retried issue that passed reads as passed, and the failed attempt
    // it replaced reads as superseded. The text is derived and no edge joins the two cards.
    const failLine = cause && (c.superseded_by ? `superseded by ${c.superseded_by} · ${cause}` : cause);
    const line2 = c.superseded_by ? { cls: "bd-c", y: TEXT.cause, text: clipText(failLine || `superseded by ${c.superseded_by}`, CLIP.cause) }
      : cause ? { cls: "bd-c", y: TEXT.cause, text: clipText(cause, CLIP.cause) }
      : c.rework && !live ? { cls: "rw", y: TEXT.cause, text: clipText(`↻ ${c.rework}`, CLIP.cause) }
      : tok ? { cls: "st", y: TEXT.line2, text: tok } : null;
    const tip = (running ? runningTitle(c) : `${oc || "no outcome"}${cause ? ", " + cause : ""}${c.rejected ? "" : ", " + stat + ", tokens " + tok}`) + (c.rework ? ` | ${c.rework}` : "");
    // The text is clipped to the card and the full text stays in the tooltip; the title carries the child's index, so two "issue #11" cards differ.
    g.innerHTML = `<title>${esc(c.title)} | ${esc(c.name)} | ${esc(tip)}</title><rect width="${BOX.w}" height="${BOX.h}" rx="6"/><g clip-path="url(#card-clip)"><text class="nm" x="${TEXT.x}" y="${TEXT.name}">${esc(clipText(c.title, CLIP.name))}</text><text class="kd" x="${TEXT.x}" y="${TEXT.kind}">${esc(kind)}</text>${stat ? `<text class="st" x="${TEXT.x}" y="${TEXT.line1}">${esc(stat)}</text>` : ""}${line2 ? `<text class="${line2.cls}" x="${TEXT.x}" y="${line2.y}">${esc(line2.text)}</text>` : ""}</g><text class="gl" x="${BOX.w - GLYPH.right}" y="${GLYPH.top}" text-anchor="end">${mark.glyph}</text><circle class="port in" cx="0" cy="${BOX.h / 2}" r="5"/>`;
    g.addEventListener("pointerdown", (ev) => {
      ev.stopPropagation();
      // A second press on a card of an expanded group folds the group back into its group card.
      const again = tapTwice(c.name, ev);
      if (again && S.groupOf[c.name]) { toggleGroup(S.groupOf[c.name], again); return; }
      pick(c.name);
    });
    cards.appendChild(g);
  }
}

// Nothing selected, no link selected, the panel closed, the map redrawn: what a press on the selected card or on empty map does.
function clearSelection() { S.selected = null; S.link = null; setSide(false, true); render(); }
// One press on a card, a square or a group card: it selects the name and shows it; a press on the one already selected clears the selection and closes the panel.
function pick(name) {
  S.link = null;
  if (S.selected === name) { clearSelection(); return; }
  S.selected = name; render(); // the drawer takes the right edge: render() reveals the selection left of it
}
// The cards in reading order (top to bottom, then left to right), for the panel's previous / next.
function cardOrder() {
  return [...$("cards").children].filter((c) => c.dataset.name).map((c) => ({ n: c.dataset.name, r: c.getBoundingClientRect() }))
    .sort((a, b) => Math.round(a.r.top) - Math.round(b.r.top) || a.r.left - b.r.left).map((c) => c.n);
}
// Previous / next card without closing the panel: the ‹ › buttons, or ← → while the panel is open.
function stepCard(k) {
  const o = cardOrder(), i = o.indexOf(S.selected) + k;
  if (i < 0 || i >= o.length) return;
  S.selected = o[i]; S.link = null; render();
}
document.addEventListener("click", (e) => { const b = e.target.closest && e.target.closest("[data-step]"); if (b && !b.disabled) { e.stopPropagation(); stepCard(+b.dataset.step); } });
window.addEventListener("keydown", (e) => {
  if ((e.key !== "ArrowLeft" && e.key !== "ArrowRight") || !sideOpen() || !S.selected || e.metaKey || e.ctrlKey || e.altKey || e.shiftKey) return;
  if (["TEXTAREA", "INPUT", "SELECT"].includes(e.target.tagName)) return;
  e.preventDefault(); e.stopImmediatePropagation(); stepCard(e.key === "ArrowLeft" ? -1 : 1);
}, true);

// A card is a keyboard stop: Tab reaches it, Enter selects it, Space opens or folds a group. The cards are rebuilt on every render, so the key
// handler puts the focus back on the card it acted on.
function focusable(g, label) { g.setAttribute("tabindex", "0"); g.setAttribute("role", "button"); g.setAttribute("aria-label", label); }
function focusCard(name) { const el = [...$("cards").children].find((c) => c.dataset.name === name); if (el) el.focus(); }
// A square of a group card is a stop too: Enter or Space selects that child, and the focus stays on its square.
function focusSquare(name) { const el = [...$("cards").querySelectorAll("g.sq")].find((q) => q.dataset.sq === name); if (el) el.focus(); }
$("cards").addEventListener("keydown", (e) => {
  const q = e.target.closest ? e.target.closest("g.sq") : null;
  if (q && e.target === q && !e.metaKey && !e.ctrlKey && !e.altKey && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); pick(q.dataset.sq); focusSquare(q.dataset.sq); return; }
  const g = e.target.closest ? e.target.closest(".card[data-name]") : null;
  if (!g || e.target !== g || e.metaKey || e.ctrlKey || e.altKey) return;
  const name = g.dataset.name, key = g.dataset.group ? name : S.groupOf[name];
  if (e.key === "Enter") { e.preventDefault(); pick(name); focusCard(name); return; }
  if (e.key === " " && key) {
    e.preventDefault();
    const was = S.groups.some((x) => x.key === key), first = S.groups.find((x) => x.key === key);
    toggleGroup(key, { open: sideOpen() }).then(() => focusCard(was ? (first ? first.kids[0].name : name) : key));
  }
});

// Text cut to `n` characters with an ellipsis; the full text stays in the card's tooltip.
function clipText(t, n) { const x = String(t); return x.length > n ? x.slice(0, n - 1) + "…" : x; }

// Two presses on one card or group card within DOUBLE_PRESS_MS. The cards are rebuilt on every press, so a dblclick event would be lost (see startDrag).
// Returns the first press ({name, t, open}: whether the panel was open before it) on a second press, else null.
function tapTwice(name, ev) {
  const prev = S.lastTap, twice = core.isDoublePress(prev, name, ev.timeStamp);
  S.lastTap = twice ? null : { name, t: ev.timeStamp, open: sideOpen() };
  return twice ? prev : null;
}

// Expand a group card into its cards, or fold the cards back. The flag is page state, keyed by dispatch + outcome (+ harness). The
// first press of the double-press selected something and may have opened the panel: both are undone, so the view stays as it was.
function toggleGroup(key, first) {
  const opening = !S.expanded.includes(key);
  S.expanded = core.toggleKey(S.expanded, key);
  S.selected = null; S.link = null;
  if (first && !first.open) setSide(false, true);
  return recompute().then(() => { if ((S.fitted || (!opening && S.floored)) && S.doc) fit(opening ? key : null); if (opening && S.floored) setStatus(`Opened at ${Math.round(READABLE_K * 100)}% so the cards stay readable. Drag to pan, or press Fit to see them all.`); });
}

// The group card of a set of three or more children of one dispatch: "dispatch · N outcome", a summary line, and one square
// per child. A square's fill and its glyph both show the outcome; a click selects that child. A double-press on the card expands it.
function renderGroups(cards) {
  for (const gr of S.groups) {
    const p = S.groupPos[gr.key];
    if (!p) continue;
    const g = document.createElementNS(SVGNS, "g");
    g.setAttribute("class", "card group-card s-" + gr.state + (S.selected === gr.key ? " sel" : ""));
    g.setAttribute("transform", `translate(${p.x} ${p.y})`);
    g.dataset.name = gr.key; g.dataset.group = "1";
    focusable(g, `${gr.name}, group of ${gr.kids.length}, Space opens it`);
    const sq = S.groupSq[gr.key].map((q, i) => {
      const c = gr.kids[i], m = core.childMark(c);
      return `<g class="sq s-${m.state}${S.selected === c.name ? " sel" : ""}${c.superseded_by ? " sup" : ""}" data-sq="${esc(c.name)}" tabindex="0" role="button" aria-label="${esc(c.title)}, ${esc(m.label)}${reworkNote(c, ", ")}" transform="translate(${q.x} ${q.y})"><title>${esc(c.title)} | ${esc(c.name)} | ${esc(m.label)} | ${esc(core.fmt.usd(c.spend))}${reworkNote(c, " | ")}</title><rect class="fr" x="-2.5" y="-2.5" width="25" height="25" rx="5"/><rect width="20" height="20" rx="3"/><text x="10" y="15" text-anchor="middle">${m.glyph}</text></g>`;
    }).join("");
    const mark = core.stateMark(gr.state);
    const harn = gr.harness ? core.fmt.model(gr.harness) : "";
    g.innerHTML = `<title>${esc(gr.name)}${harn ? " | " + esc(harn) : ""} | ${esc(gr.line)} | double-click to expand</title><rect width="${p.w}" height="${p.h}" rx="6"/><g clip-path="url(#card-clip)"><text class="nm" x="${TEXT.x}" y="${TEXT.name}">${esc(clipText(gr.name, CLIP.name))}</text>${harn ? `<text class="kd" x="${TEXT.x}" y="${TEXT.kind}">${esc(clipText(harn, CLIP.harness))}</text>` : ""}<text class="st" x="10" y="${gr.harness ? 47 : 34}">${esc(gr.lineTime)}</text><text class="st" x="10" y="${gr.harness ? 58 : 48}">${esc(gr.lineTokens)}</text></g><text class="gl" x="${BOX.w - GLYPH.right}" y="${GLYPH.top}" text-anchor="end">${mark.glyph}</text>${sq}<circle class="port in" cx="0" cy="30" r="5"/>`;
    wireSquares(g, gr);
    cards.appendChild(g);
  }
}
function wireSquares(g, gr) {
  g.addEventListener("pointerdown", (ev) => {
    ev.stopPropagation();
    const again = tapTwice(gr.key, ev);
    if (again) { toggleGroup(gr.key, again); return; }
    const q = ev.target.closest && ev.target.closest("g.sq");
    pick(q ? q.dataset.sq : gr.key);
  });
}

// The lines under the kind on a card. A node that never ran says so; a gate a dispatch runs says on how many children; a failed node names its cause.
function cardLines(ns, running) {
  if (ns.not_run) return `<text class="st" x="${TEXT.x}" y="${TEXT.line1}">not run</text>`;
  if (ns.via) return `<text class="st" x="${TEXT.x}" y="${TEXT.line1}">${esc(`checked ${ns.via.of} child${ns.via.of > 1 ? "ren" : ""} of ${ns.via.dispatch}`)}</text><text class="${ns.via.fail ? "bd-c" : "st"}" x="${TEXT.x}" y="${ns.via.fail ? TEXT.cause : TEXT.line2}">${esc(clipText(ns.via.fail ? `${ns.via.fail} failed · ${ns.via.pass} passed` : `${ns.via.pass} passed`, CLIP.cause))}</text>`;
  const live = running ? ns.live : null;   // a call in flight: how far the call itself says it has got
  const head = running ? runningHead(ns.running_for, live) + (ns.entries ? " · " + statLine(ns) : "") : statLine(ns);
  const top = `<text class="st" x="${TEXT.x}" y="${TEXT.line1}">${esc(head)}</text>`;
  if (live) return top + `<text class="st" x="${TEXT.x}" y="${TEXT.line2}">${esc(progressLine(live))}</text>`;
  if (ns.cause) return top + `<text class="bd-c" x="${TEXT.x}" y="${TEXT.cause}">${esc(clipText(ns.cause, CLIP.cause))}</text>`;
  return top + (tokenLine(ns) ? `<text class="st" x="${TEXT.x}" y="${TEXT.line2}">${esc(tokenLine(ns))}</text>` : "");
}

// The tooltip of a call in flight: what the record says about it, in place of the figures that need an outcome.
function runningTitle(ns) {
  const f = core.fmt, live = ns.live;
  return `running for ${f.seconds(ns.running_for)}`
    + (live ? ` · ${f.count(live.messages)} assistant messages · ${f.tokens(live.context_tokens)} context` + (live.tool ? ` · last tool ${live.tool}` : "") : "");
}

// The first line of a call in flight: how long it has run and how many assistant messages it has produced. `msg` is not
// the outcome's `turns` — one turn reaches the stream as two messages — so the two never share one label.
function runningHead(running_for, live) {
  return "▶ " + core.fmt.seconds(running_for) + (live ? " · " + core.fmt.count(live.messages) + " msg" : "");
}

// The second line of a call in flight: the context it last read and the last tool it used. A call that has reported no
// context keeps "—" for it, never 0.
function progressLine(live) {
  return `${core.fmt.tokens(live.context_tokens)} context${live.tool ? " · " + live.tool : ""}`;
}

function renderMap() {
  const cards = $("cards");
  cards.innerHTML = "";
  for (const n of nodesOf()) {
    const p = S.layout[n.name] || (S.layout[n.name] = { x: 20, y: 20 });
    const g = document.createElementNS(SVGNS, "g");
    const errs = errsFor(n.name).length, ov = overlayFor(n.name).length;
    const ns = S.stats && S.stats.nodes ? S.stats.nodes[n.name] : null;
    const oc = ns ? (ns.outcome || (ns.started ? "started" : null)) : null;
    const running = !!ns && core.isRunning(ns);
    const nmark = ns ? core.stateMark(core.nodeState(ns)) : null;   // a glyph on every node the record touched: color is never the only cue
    const sc = core.stateClass(ns);   // not-run, running, passed, failed, deadline, needs-human, started
    g.setAttribute("class", "card" + (S.selected === n.name ? " sel" : "") + (errs ? " err" : "") + (sc ? " " + sc : ""));
    g.setAttribute("transform", `translate(${p.x} ${p.y})`);
    g.dataset.name = n.name;
    const label = core.nodeLabel(n);
    focusable(g, `${label}, ${nmark ? nmark.label : n.kind}`);   // the role label on the card; the raw name stays in the tooltip
    const name = clipText(label, CLIP.name);   // the glyph sits at the top-right corner
    g.innerHTML = `<title>${esc(n.name)}${ns ? ` | ${running ? esc(runningTitle(ns)) : `${esc(oc || "no outcome")}, time ${core.fmt.seconds(ns.elapsed)}, cost ${core.fmt.usd(ns.spend)}, ${core.fmt.pct(ns.cap_pct)} of the call cap, turns ${core.fmt.count(ns.turns)}${tokenLine(ns) ? ", tokens " + tokenLine(ns) : ""}, entries ${ns.entries}${ns.cause ? ", " + esc(ns.cause) : ""}`}` : ""}</title><rect width="${BOX.w}" height="${BOX.h}" rx="6"/><g clip-path="url(#card-clip)"><text class="nm" x="${TEXT.x}" y="${TEXT.name}">${esc(name)}</text><text class="kd" x="${TEXT.x}" y="${TEXT.kind}">${esc(n.kind)}${ns && ns.routed && ns.routed !== "continue" ? " \u2192 " + esc(ns.routed) : ""}</text>${ns ? cardLines(ns, running) : ""}</g>${errs ? `<text class="bd-e" x="${BOX.w - 8}" y="34" text-anchor="end">${errs} error${errs > 1 ? "s" : ""}</text>` : ""}${ov ? `<text class="bd-o" x="${BOX.w - 26}" y="19" text-anchor="end">${ov} adapted</text>` : ""}${nmark ? `<text class="gl" x="${BOX.w - GLYPH.right}" y="${GLYPH.top}" text-anchor="end">${nmark.glyph}</text>` : ""}${n.name === S.doc.root ? "" : `<circle class="port in" cx="0" cy="${BOX.h / 2}" r="5"/>`}${["loop", "each", "dispatch"].includes(n.kind) ? `<circle class="port out" cx="${BOX.w}" cy="${BOX.h / 2}" r="5"><title>Drag to another node to connect</title></circle>` : ""}`;
    g.addEventListener("pointerdown", (ev) => { if (ev.target.classList && ev.target.classList.contains("out")) startLink(ev, n.name); else startDrag(ev, n.name, g); });
    cards.appendChild(g);
  }
  renderChildren(cards);
  renderGroups(cards);
  $("edges").innerHTML = edgeLines();
  applyView();
}

// A node that can call a model shows its tokens, a dash when the record has none. Another node shows the line only if the record has tokens for it.
function tokenLine(ns) {
  if (!ns.harness && ns.tokens_in === null && ns.tokens_out === null) return "";
  const f = core.fmt;
  return `${f.tokens(ns.tokens_in)} in \u00b7 ${f.tokens(ns.tokens_out)} out \u00b7 ${f.rate(ns.tps)}`;
}
// `ns.entries` is how many times this node has run an entry so far (a loop, a dispatch, or a retried
// gate runs more than once). Shown as `\u00d7N` on every node kind, so a node that ran five times does not read
// the same as one that ran once (found while checking the dashboard with the owner, 2026-10-01).
function statLine(ns) { return [core.fmt.seconds(ns.elapsed), core.fmt.usd(ns.spend), core.fmt.pct(ns.cap_pct), core.fmt.count(ns.turns) + (ns.turns === null ? "" : "t"), "\u00d7" + core.fmt.count(ns.entries)].join(" \u00b7 "); }

function startDrag(ev, name, g) {
  if (ev.button !== 0) return;
  ev.stopPropagation();
  // Two quick presses on one node without a drag: the rename field takes focus. The cards are rebuilt on every release, so a dblclick event would be lost.
  const again = core.isDoublePress(S.lastDown, name, ev.timeStamp);
  S.lastDown = { name, t: ev.timeStamp };
  const was = S.selected === name;   // a press on the selected node that is not a drag or a rename press clears the selection and closes the panel
  S.selected = name; S.link = null; S.dragging = true;
  const p = S.layout[name], sx = ev.clientX, sy = ev.clientY, ox = p.x, oy = p.y, k = S.view.k, before = snap();
  let moved = false;
  const move = (e) => { if (Math.abs(e.clientX - sx) + Math.abs(e.clientY - sy) > DRAG_SLOP) moved = true; p.x = ox + (e.clientX - sx) / k; p.y = oy + (e.clientY - sy) / k; g.setAttribute("transform", `translate(${p.x} ${p.y})`); $("edges").innerHTML = edgeLines(); };
  const up = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); S.dragging = false; if (moved && (p.x !== ox || p.y !== oy)) track(before); if (!moved && was && !again) { S.selected = null; setSide(false, true); } render(); if (!moved && !was) revealSelected(); if (again && !moved) focusName(); };
  window.addEventListener("pointermove", move); window.addEventListener("pointerup", up);
}

// A link drag starts on an out port and ends on any node. The pure connect() decides; a refusal shows in the status line.
function toGraph(e) { const r = $("map").getBoundingClientRect(); return { x: (e.clientX - r.left - S.view.tx) / S.view.k, y: (e.clientY - r.top - S.view.ty) / S.view.k }; }
function cardAt(e) { const el = document.elementFromPoint(e.clientX, e.clientY); return el && el.closest ? el.closest(".card") : null; }
function startLink(ev, from) {
  if (ev.button !== 0) return;
  ev.stopPropagation(); ev.preventDefault();
  const p = S.layout[from];
  const rb = document.createElementNS(SVGNS, "line");
  rb.setAttribute("id", "rubber");
  S.dragging = true;
  rb.setAttribute("x1", p.x + BOX.w); rb.setAttribute("y1", p.y + BOX.h / 2);
  rb.setAttribute("x2", p.x + BOX.w); rb.setAttribute("y2", p.y + BOX.h / 2);
  $("vp").appendChild(rb);
  let over = null;
  const move = (e) => {
    const g = toGraph(e); rb.setAttribute("x2", g.x); rb.setAttribute("y2", g.y);
    const c = cardAt(e);
    if (over && over !== c) over.classList.remove("drop");
    over = c && c.dataset.name !== from ? c : null;
    if (over) over.classList.add("drop");
  };
  const up = (e) => {
    window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up);
    S.dragging = false;
    rb.remove();
    if (over) over.classList.remove("drop");
    const c = cardAt(e);
    if (c) connectNodes(from, c.dataset.name);
  };
  window.addEventListener("pointermove", move); window.addEventListener("pointerup", up);
}
$("edges").addEventListener("pointerdown", (ev) => {
  const g = ev.target.closest && ev.target.closest("g.edge");
  if (!g || ev.button !== 0) return;
  ev.stopPropagation();
  S.link = { from: g.dataset.from, to: g.dataset.to }; S.selected = null;
  render();
});

function startPan(ev) {
  if (ev.button !== 0) return;
  if (ev.target.closest && ev.target.closest(".card")) return;
  const sx = ev.clientX, sy = ev.clientY, v0 = S.view;
  let moved = false;
  $("map").classList.add("panning");
  const move = (e) => { moved = true; S.view = core.viewPanBy(v0, e.clientX - sx, e.clientY - sy); S.fitted = false; applyView(); };
  const up = () => {
    window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up);
    $("map").classList.remove("panning");
    if (!moved && (S.selected || S.link)) clearSelection();
  };
  window.addEventListener("pointermove", move); window.addEventListener("pointerup", up);
}
$("map").addEventListener("pointerdown", startPan);
$("map").addEventListener("wheel", (e) => {
  e.preventDefault();
  const r = $("map").getBoundingClientRect();
  zoomBy(Math.exp(-e.deltaY * WHEEL_RATE), e.clientX - r.left, e.clientY - r.top);
}, { passive: false });
$("b-in").addEventListener("click", () => zoomBy(ZOOM_STEP));
$("b-out").addEventListener("click", () => zoomBy(1 / ZOOM_STEP));
$("b-fit").addEventListener("click", () => fit());
// A resize refits the graph only while the view is still the fitted one; a pan or a zoom keeps the owner's view.
window.addEventListener("resize", () => { if (S.fitted && S.doc) fit(); });

