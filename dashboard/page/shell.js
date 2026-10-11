// The core is the same file that the test runs under node.
const core = make(ENGINE);
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const SVGNS = "http://www.w3.org/2000/svg";
const BOX = { w: 200, h: 60 }, PAD = 32;
// Sizes the page shares between files. A card's top-right glyph sits GLYPH.right from the right edge and GLYPH.top from the top; the card text
// is cut to the CLIP lengths (the tooltip keeps the full text). The replay bar floats REPLAY_GAP above the footer and is REPLAY_BAR_H tall,
// so fit() and the reveal keep the graph clear of both. A selection is revealed REVEAL_INSET from the map edge.
const GLYPH = { right: 9, top: 19 }, CLIP = { name: 22, cause: 25, harness: 30 };
// An expanded group of many children is shown no smaller than READABLE_K: the part next to its dispatch stays readable and the rest is a pan away.
const READABLE_K = 0.7;
// Card text rows (y) and left edge (x) inside a card of BOX size; the one place the card layout is named. CARD_GAP is the space between cards in a column.
const TEXT = { x: 10, name: 19, kind: 34, line1: 46, line2: 57, cause: 56 }, CARD_GAP = 30;
// Pointer feel: a press that moves under DRAG_SLOP px is a click; one wheel pixel zooms by WHEEL_RATE (exponent); a zoom button steps by ZOOM_STEP; the fit never uses a map smaller than MIN_FIT px.
const DRAG_SLOP = 3, WHEEL_RATE = 0.0015, ZOOM_STEP = 1.25, MIN_FIT = 120;
const REPLAY_GAP = 24, REPLAY_BAR_H = 44, REVEAL_INSET = 16, FOOTER_PAD = 12;

const S = { recordBad: null, warnings: [], doc: null, text: "", name: "graph", layout: {}, selected: null, link: null, events: null, cursor: null, replay: null, overlay: null, stats: null, children: [], childPos: {}, expanded: [], groupOf: {}, lastTap: null, groups: [], groupPos: {}, groupPts: {}, groupSq: {}, folder: null, issueTotal: null, issueHeld: 0, errors: [], notice: "", handle: null, stamp: null, layoutHandle: null, layoutStamp: null, baseText: "", baseLayout: "{}", baseHash: null, chip: null, offerPending: null, view: { k: 1, tx: 20, ty: 20 }, hist: core.histNew(100) };

function setStatus(msg, bad) { const el = $("status"); el.textContent = msg; el.className = bad ? "msg-bad" : ""; toast(); }

const nowSec = () => Date.now() / 1000;

async function recompute(opts) {
  S.errors = S.doc ? core.validate(S.doc) : [];
  S.overlay = S.doc && S.events ? await core.overlayFor(S.text, S.events) : null;
  // The cursor cuts the record: every card, total and the verdict fold only the events up to it. A null cursor is the end, so a live run keeps following.
  S.replay = S.events ? core.replayAt(S.events, S.cursor) : null;
  S.stats = S.events ? core.runStats(S.replay.events, S.doc, !!(S.overlay && S.overlay.shown), S.replay.at_end ? nowSec() : undefined) : null;
  // The children of a dispatch are run-only cards: placed here, never written to the layout.
  const had = Object.keys(S.childPos).length + Object.keys(S.groupPos).length;
  S.children = S.stats && S.stats.children ? S.stats.children : [];
  // Children group by status: done with one outcome, or running with one harness, three or more to a group card. The keys
  // the owner expanded live in S.expanded (page state, not the record), so they survive a live update and a move of the replay cursor.
  const plan = core.childPlan(S.children, S.expanded, S.layout, BOX, CARD_GAP, S.doc);
  S.groups = plan.groups; S.groupOf = plan.groupOf; S.childPos = plan.childPos;
  S.groupPos = plan.groupPos; S.groupPts = plan.groupPts; S.groupSq = plan.groupSq;
  if (S.selected && core.isGroupKey(S.selected) && !S.groupPos[S.selected]) S.selected = null;   // the replay cursor moved to before the group existed
  if (S.fitted && S.doc && Object.keys(S.childPos).length + Object.keys(S.groupPos).length !== had) fit();
  if (opts && opts.live) renderLive(); else render();
}

// A reread must not wipe a field the owner is typing in, so the side panel redraws only when no field in it has focus.
function renderReplay() {
  const bar = $("replay"), r = S.replay;
  bar.style.display = r ? "flex" : "none";
  if (!r) return;
  const range = $("r-range");
  range.min = r.first; range.max = r.last; range.value = r.cursor;
  $("r-start").disabled = $("r-back").disabled = r.cursor === r.first;
  $("r-fwd").disabled = $("r-end").disabled = r.at_end;
  const pos = $("r-pos");
  pos.className = r.at_end ? "" : "past";
  pos.textContent = r.at_end ? `event ${r.cursor} of ${r.last} \u00b7 end` : `event ${r.cursor} of ${r.last} \u00b7 replay`;
}
async function moveReplay(move, to) { if (!S.events) return; S.cursor = core.moveCursor(S.events, S.cursor, move, to); await recompute(); }
$("r-start").addEventListener("click", () => moveReplay("start"));
$("r-back").addEventListener("click", () => moveReplay("back"));
$("r-fwd").addEventListener("click", () => moveReplay("forward"));
$("r-end").addEventListener("click", () => moveReplay("end"));
$("r-range").addEventListener("input", (e) => moveReplay("to", Number(e.target.value)));

// The totals bar under the graph: the cells are core data (core.totalsCells), cut at the replay cursor like every other figure.
// The bar takes height from the map, so a fitted graph is fitted again when the bar appears or goes.
function renderTotals() {
  const bar = $("totals"), r = S.replay, was = bar.classList.contains("on");
  bar.classList.toggle("on", !!r);
  if (!r) { bar.innerHTML = ""; if (was && S.fitted && S.doc) fit(); return; }
  const cells = core.totalsCells(core.runTotals(r.events, core.runCap(r.events[0], S.doc), r.at_end ? nowSec() : undefined));
  bar.innerHTML = cells.map((c) => `<span class="cell" title="${esc(c.title)}">${esc(c.label)} <b${c.text === "unknown" ? ' class="unk"' : ""}>${esc(c.text)}</b></span>`).join("")
    + (r.at_end ? "" : `<span class="cell past" title="The bar shows the run up to the replay cursor, not the whole run.">replay: up to event ${r.cursor} of ${r.last}</span>`);
  if (!was && S.fitted && S.doc) fit();
}
setInterval(() => { if (S.replay && S.replay.at_end && S.stats && S.stats.state === "running") renderTotals(); }, 1000);   // a running run's time keeps counting between rereads

// The top bar above the graph: how many issues the run landed, of the issue set in the run folder. The
// cells are core data (core.progressCells), and the replay cursor cuts them like every other figure.
function renderProgress() {
  const bar = $("progress"), r = S.replay, was = bar.classList.contains("on");
  const cells = r ? core.progressCells(core.issueProgress(r.events, S.issueTotal, S.issueHeld)) : [];
  bar.classList.toggle("on", cells.length > 0);
  bar.innerHTML = cells.map((c) => `<span class="cell" title="${esc(c.title)}">${esc(c.label)} <b>${esc(c.text)}</b></span>`).join("")
    + (cells.length && !r.at_end ? `<span class="cell past" title="The bar shows the run up to the replay cursor, not the whole run.">replay: up to event ${r.cursor} of ${r.last}</span>` : "");
  if (!was && cells.length && S.fitted && S.doc) fit();
}

// A single-card page paints that one card and nothing else: it still reads the run (the same push stream or reread
// timer), so the card is live, but the map, the totals and the panel are never built for a second view.
function renderLive() { if (CARD) return renderCard(); renderReplay(); renderProgress(); renderTotals(); renderMap(); if (!$("side").contains(document.activeElement)) renderSide(); renderChrome(); }

// A gui-test script opens as a step diagram in its own view; nothing runs. Any other file goes on to the graph reader.
function scriptFormat(text) { try { const d = JSON.parse(text); return d && typeof d.format === "string" && d.format.startsWith("gui-test/") ? d : null; } catch (e) { return null; } }
function showScript(d, name) {
  const svg = core.scriptSvg(d);
  if (svg.error) { setStatus(svg.error, true); return false; }
  $("scriptview").innerHTML = svg; $("scriptview").hidden = false; document.body.classList.add("script-open");
  setStatus(`Opened test script ${name} as a step diagram. Nothing runs.`);
  return true;
}
function hideScript() { $("scriptview").hidden = true; $("scriptview").innerHTML = ""; document.body.classList.remove("script-open"); }
async function loadGraph(text, name) {
  const script = scriptFormat(text);
  if (script) return showScript(script, name);
  hideScript();
  const r = core.parseImport(text);
  if (r.error) { setStatus(r.error, true); return false; }
  S.warnings = []; S.doc = r.doc; S.text = text; S.name = name.replace(/\.graph\.json$|\.json$/, ""); S.fileName = name; S.selected = null; S.link = null;
  S.handle = null; S.stamp = null; S.layoutHandle = null; S.layoutStamp = null; S.offerPending = null; hidePanels();
  S.layout = Array.isArray(S.doc.nodes) ? core.autoLayout(S.doc) : {};
  S.baseText = core.saveText(text); S.baseLayout = JSON.stringify(S.layout);
  try { S.baseHash = (await core.hashChip(text)).full; } catch (e) { S.baseHash = null; }
  S.hist = core.histNew(100);
  setStatus(`Loaded ${name}.`);
  fit();
  await recompute();
  refreshMode();
  await offerDraft();
  return true;
}

function nodesOf() { return S.doc && Array.isArray(S.doc.nodes) ? S.doc.nodes.filter((n) => n && typeof n.name === "string") : []; }
function errsFor(name) { return S.errors.filter((e) => e.node === name); }
function overlayFor(name) { return S.overlay && S.overlay.shown ? S.overlay.rows.filter((r) => r.target === name) : []; }

function render() { if (CARD) return renderCard(); renderReplay(); renderProgress(); renderTotals(); renderMap(); renderSide(); renderAddbar(); renderChrome(); updateChip(); scheduleDraft(); }

