// The page chrome: a collapsible side panel, a Run | Edit mode, a verdict chip, and the toast. No run logic lives here.
const UI_KEY = "agent-squad-dashboard-ui";
const UI = (() => { try { return JSON.parse(localStorage.getItem(UI_KEY)) || {}; } catch (e) { return {}; } })();
function saveUI() { try { localStorage.setItem(UI_KEY, JSON.stringify(UI)); } catch (e) { /* no storage: the choice lasts this page only */ } }

// An explicit toggle is remembered; an automatic open (a selection, a notice) is not, so the panel starts hidden every time.
function setSide(open, auto) {
  document.body.classList.toggle("noside", !open);
  $("b-panel").setAttribute("aria-expanded", String(open));
  if (!auto) { UI.side = open; saveUI(); }
  if (S.doc) fit();   // the drawer takes its width off the map, so an open or closed panel is a fit the graph can feel
}
function sideOpen() { return !document.body.classList.contains("noside"); }
function openSide() { if (!sideOpen()) setSide(true, true); }
setSide(!!UI.side, true);
$("b-panel").addEventListener("click", () => setSide(!sideOpen()));
// Escape closes the panel; the map behind it stays the map, so a click there is a click, not a dismissal.
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && sideOpen() && !e.defaultPrevented) setSide(false); });
$("vchip").addEventListener("click", () => setSide(!sideOpen()));

// The drawer's grip. A drag on the 8px strip at its edge, or the arrows on the handle, sets --side-w between the stylesheet's
// --side-min and --side-max; a double-click puts the 420px default back. The width is page state, like the panel's open flag.
const SIDE_HIT = 8, SIDE_STEP = 24, SIDE_RESET = 420;
const sideProbe = document.createElement("div");
sideProbe.style.cssText = "position:fixed;top:-9999px;left:-9999px;height:0;pointer-events:none";
document.body.appendChild(sideProbe);
// The CSS owns the limits: --side-min and --side-max are measured through a probe, so the page and the stylesheet cannot drift.
function sidePx(tok) { sideProbe.style.width = `var(${tok})`; return Math.round(sideProbe.getBoundingClientRect().width); }
// A narrow window turns the drawer into a bottom sheet: the same variable is its height and the grip is sideways.
function sideRow() { return window.matchMedia("(max-width: 900px)").matches; }
function sideWidth() { const r = $("left").getBoundingClientRect(); return Math.round(sideRow() ? r.height : r.width); }
function sideAria(now) {
  const grip = $("side-grip");
  grip.setAttribute("aria-orientation", sideRow() ? "horizontal" : "vertical");
  grip.setAttribute("aria-valuemin", String(sidePx("--side-min"))); grip.setAttribute("aria-valuemax", String(sidePx("--side-max"))); grip.setAttribute("aria-valuenow", String(now));
}
function setSideWidth(px, store) {
  const v = Math.min(Math.max(Math.round(px), sidePx("--side-min")), sidePx("--side-max"));
  document.body.style.setProperty("--side-w", v + "px");
  sideAria(v);
  if (store) { UI.sideW = v; saveUI(); }
  if (S.doc) fit();   // every change to the drawer is a change to the room the graph has
  return v;
}
// The grip is the whole 8px strip either side of the drawer's edge, so the press is judged by where it landed.
function onGrip(e) { const r = $("left").getBoundingClientRect(); return Math.abs(sideRow() ? e.clientY - r.top : e.clientX - r.left) <= SIDE_HIT; }
let sideDrag = null;
function startSideDrag(e) { sideDrag = { x: e.clientX, y: e.clientY, w: sideWidth() }; document.body.style.cursor = sideRow() ? "row-resize" : "col-resize"; }
document.addEventListener("pointerdown", (e) => { if (sideOpen() && onGrip(e)) startSideDrag(e); });
document.addEventListener("pointermove", (e) => { if (sideDrag) setSideWidth(sideRow() ? sideDrag.w + (sideDrag.y - e.clientY) : sideDrag.w + (sideDrag.x - e.clientX), false); });
document.addEventListener("pointerup", () => { if (!sideDrag) return; sideDrag = null; document.body.style.cursor = ""; UI.sideW = sideWidth(); saveUI(); });
$("side-grip").addEventListener("pointerdown", (e) => { e.stopPropagation(); if (sideOpen()) startSideDrag(e); });
$("side-grip").addEventListener("keydown", (e) => {
  const grow = sideRow() ? "ArrowDown" : "ArrowRight", shrink = sideRow() ? "ArrowUp" : "ArrowLeft";
  if (e.key === grow || e.key === shrink) setSideWidth(sideWidth() + (e.key === grow ? SIDE_STEP : -SIDE_STEP), true);
  else if (e.key === "Home" || e.key === "End") setSideWidth(sidePx(e.key === "Home" ? "--side-min" : "--side-max"), true);
  else return;
  e.preventDefault();
});
$("side-grip").addEventListener("dblclick", () => setSideWidth(SIDE_RESET, true));
window.addEventListener("resize", () => sideAria(sideWidth()));   // the sheet's limits are in vh: the handle follows a resize
if (UI.sideW !== undefined) setSideWidth(UI.sideW, false); else sideAria(sideWidth());   // a reload lands where the drag left the drawer

// Run mode reads a run; Edit mode shows every control that changes the graph. A loaded run opens in Run mode, a bare page in Edit mode, until the person picks.
var modePicked = false;
function setMode(edit, picked) {
  if (picked) modePicked = true;
  document.body.classList.toggle("edit", edit);
  $("m-edit").classList.toggle("on", edit); $("m-run").classList.toggle("on", !edit);
  $("m-edit").setAttribute("aria-pressed", String(edit)); $("m-run").setAttribute("aria-pressed", String(!edit));
  if (S.doc) renderSide();
}
$("m-run").addEventListener("click", () => setMode(false, true));
$("m-edit").addEventListener("click", () => setMode(true, true));
function inEdit() { return document.body.classList.contains("edit"); }

var VERDICT_CLASS = { completed: "v-ok", failed: "v-bad", "needs-human": "v-human", running: "v-live", stalled: "v-wait", "no verdict yet": "v-none" };
function renderChrome() {
  if (!modePicked && inEdit() !== !S.events) setMode(!S.events);
  const st = S.stats, v = st && st.verdict, chip = $("vchip");
  chip.hidden = !st;
  $("ident").textContent = st ? `${st.graph_name} · ${st.run_id}` : (S.doc ? S.name : "no graph");
  $("ident").title = st ? `graph ${st.graph_name}, run ${st.run_id}` : "";
  if (st) {
    const past = S.replay && !S.replay.at_end;
    const words = core.STATE_TEXT[st.state] || st.state;   // the sidebar badges the same run with these words, from the one state table
    chip.textContent = past ? `replay · ${words}` : (v ? `${words} · exit ${v.exit_code}` : words);
    chip.className = VERDICT_CLASS[st.state] || "v-bad";
    chip.title = v && v.reasons.length ? core.causeSummary(v.reasons) : "The run's verdict. Click to open the details.";
  }
  // A record cut at a bad line shows its valid prefix under a banner that names the line. The banner takes height from the map.
  const rb = $("recbad"), bad = S.events ? S.recordBad : null, shown = !rb.hidden;
  rb.hidden = !bad;
  if (bad) rb.textContent = `Record line ${bad.line} is not JSON (${bad.message}). Showing the ${bad.shown} events before it; the line and everything after it are not shown.`;
  if (shown !== !!bad && S.fitted && S.doc) fit();
  // One delete button at a time: the one that can act.
  const rm = $("b-remove"), ul = $("b-unlink");
  rm.hidden = rm.disabled && !ul.disabled; ul.hidden = ul.disabled;
  followSelection();
}

// A selection opens the panel once; closing it again stays closed until the next new selection.
var lastSel = null;
function followSelection() { if (S.selected && S.selected !== lastSel) openSide(); lastSel = S.selected; }

// File menu: closes on a choice, on a click elsewhere and on Esc.
const fmenu = $("fmenu");
fmenu.addEventListener("click", (e) => { if (e.target.closest(".drop button, .drop label.btn")) setTimeout(() => { fmenu.open = false; }, 0); });
document.addEventListener("pointerdown", (e) => { if (fmenu.open && !fmenu.contains(e.target)) fmenu.open = false; });
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && fmenu.open) fmenu.open = false;
  const tag = (e.target.tagName || "").toLowerCase();
  if (e.key === "[" && !e.metaKey && !e.ctrlKey && tag !== "input" && tag !== "textarea" && tag !== "select") setSide(!sideOpen());
});

// The status line is a toast: a plain message fades, an error stays until clicked.
var toastTimer = null;
function toast() {
  const el = $("status");
  clearTimeout(toastTimer);
  if (el.textContent && el.className !== "msg-bad") toastTimer = setTimeout(() => { el.textContent = ""; }, 8000);
}
$("status").addEventListener("click", () => { $("status").textContent = ""; });
