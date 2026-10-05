// The page chrome (ADR-0124): a collapsible side panel, a Run | Edit mode, a verdict chip, and the toast. No run logic lives here.
const UI_KEY = "agent-squad-dashboard-ui";
const UI = (() => { try { return JSON.parse(localStorage.getItem(UI_KEY)) || {}; } catch (e) { return {}; } })();
function saveUI() { try { localStorage.setItem(UI_KEY, JSON.stringify(UI)); } catch (e) { /* no storage: the choice lasts this page only */ } }

// An explicit toggle is remembered; an automatic open (a selection, a notice) is not, so the panel starts hidden every time.
function setSide(open, auto) {
  document.body.classList.toggle("noside", !open);
  $("b-panel").setAttribute("aria-expanded", String(open));
  if (!auto) { UI.side = open; saveUI(); }
}
function sideOpen() { return !document.body.classList.contains("noside"); }
function openSide() { if (!sideOpen()) setSide(true, true); }
setSide(!!UI.side, true);
$("b-panel").addEventListener("click", () => setSide(!sideOpen()));
$("vchip").addEventListener("click", () => setSide(!sideOpen()));

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

var VERDICT_CLASS = { completed: "v-ok", running: "v-live", stalled: "v-wait", "no verdict yet": "v-none" };
function renderChrome() {
  if (!modePicked && inEdit() !== !S.events) setMode(!S.events);
  const st = S.stats, v = st && st.verdict, chip = $("vchip");
  chip.hidden = !st;
  $("ident").textContent = st ? `${st.graph_name} · ${st.run_id}` : (S.doc ? S.name : "no graph");
  $("ident").title = st ? `graph ${st.graph_name}, run ${st.run_id}` : "";
  if (st) {
    const past = S.replay && !S.replay.at_end;
    chip.textContent = past ? `replay · ${st.state}` : (v ? `${st.state} · exit ${v.exit_code}` : st.state);
    chip.className = VERDICT_CLASS[st.state] || "v-bad";
    chip.title = v && v.reasons.length ? core.causeSummary(v.reasons) : "The run's verdict. Click to open the details.";
  }
  // A record cut at a bad line shows its valid prefix under a banner that names the line (ADR-0126). The banner takes height from the map.
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
