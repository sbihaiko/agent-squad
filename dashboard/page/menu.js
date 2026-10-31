// --- the legend and the help overlay: rows come from the core; a swatch is a card group, so the card CSS paints it ---
function swatches() {
  const cards = core.LEGEND.map((r) => `<span class="lg"><svg width="24" height="14" viewBox="0 0 24 14"><g class="card ${r.cls}"><rect x="1" y="1" width="22" height="12" rx="3"/>${r.glyph ? `<text class="gl" x="12" y="11" text-anchor="middle" style="font-size:var(--fs-min)">${r.glyph}</text>` : ""}</g></svg>${esc(r.label)}</span>`);
  const chips = core.LEGEND_CHIPS.map((r) => `<span class="lg"><span class="chip-sw ${r.cls}">${esc(r.label)}</span></span>`);
  return cards.concat(chips).join("");
}
// The side strip's list: two headed groups (node outcomes, interaction). Always expanded.
// The run-state vocabulary is not a legend row: the owner removed that group (2026-10-11). The states live in the core's one table
// (RUN_STATES) and the surfaces that print a state draw their pill from it; the help overlay still lists the chips.
const LEGEND_GROUPS = [["node", "Node outcomes"], ["interaction", "Interaction"]];
function legendList() {
  const sw = (r) => `<svg width="24" height="14" viewBox="0 0 24 14"><g class="card ${r.cls}"><rect x="1" y="1" width="22" height="12" rx="3"/>${r.glyph ? `<text class="gl" x="12" y="11" text-anchor="middle" style="font-size:var(--fs-min)">${r.glyph}</text>` : ""}</g></svg>`;
  const rows = {
    node: core.LEGEND.filter((r) => r.group === "node").map((r) => [sw(r), r.label, ""]),
    interaction: core.LEGEND.filter((r) => r.group === "interaction").map((r) => [sw(r), r.label, ""]),
  };
  return LEGEND_GROUPS.map(([g, title]) => `<div class="lh">${title}</div>` + rows[g].map(([s, l, cls]) => `<span class="lg${cls}">${s}<span class="lt">${esc(l)}</span></span>`).join("")).join("");
}
$("legend-list").innerHTML = legendList();
function buttonRows() {
  const els = [...document.querySelectorAll("#left .tools button[title], #left .tools label.btn, #view button[title], #replay button[title]")].filter((b) => b.offsetParent !== null);
  return els.map((b) => `<tr><td>${esc(b.firstChild && b.firstChild.nodeType === 3 ? b.firstChild.textContent.trim() : b.textContent.trim())}</td><td>${esc(b.getAttribute("title") || "")}</td></tr>`).join("");
}
function openHelp() {
  $("help-rows").innerHTML = core.HELP.map((r) => `<tr><td>${esc(r.how)}</td><td>${esc(r.what)}</td></tr>`).join("");
  $("help-btns").innerHTML = buttonRows();
  $("help-legend").innerHTML = swatches();
  $("help").hidden = false; $("b-help").setAttribute("aria-expanded", "true");
}
function closeHelp() { $("help").hidden = true; $("b-help").setAttribute("aria-expanded", "false"); }
function toggleHelp() { if ($("help").hidden) openHelp(); else closeHelp(); }
// #footer (the hint and the replay bar's row) sets the floor; keep the side legend above the row, at any width. The row's
// own top is measured rather than its height, so the bottom sheet that lifts the row lifts the legend's floor too.
(function trackFooterHeight() {
  const footer = $("footer");
  const sync = () => {
    const m = $("map").getBoundingClientRect(), f = footer.getBoundingClientRect();
    $("legend").style.setProperty("--legend-bottom", (m.bottom - f.top + REPLAY_GAP) + "px");
  };
  new ResizeObserver(sync).observe(footer);
  window.addEventListener("resize", sync);
  sync();
})();
$("b-help").addEventListener("click", toggleHelp);
$("b-help-close").addEventListener("click", closeHelp);
$("help").addEventListener("pointerdown", (e) => { if (e.target === $("help")) closeHelp(); });
function openMenu(x, y, items) {
  const m = $("menu");
  m.innerHTML = "";
  for (const it of items) {
    if (it.sep) { const d = document.createElement("div"); d.className = "sep"; d.textContent = it.sep; m.appendChild(d); continue; }
    const b = document.createElement("button"); b.textContent = it.label; b.addEventListener("click", () => { closeMenu(); it.run(); }); m.appendChild(b);
  }
  m.style.display = "flex";
  m.style.left = Math.max(0, Math.min(x, window.innerWidth - m.offsetWidth - 4)) + "px";
  m.style.top = Math.max(0, Math.min(y, window.innerHeight - m.offsetHeight - 4)) + "px";
}
$("map").addEventListener("contextmenu", (e) => {
  e.preventDefault();
  if (!S.doc) return;
  const edge = e.target.closest && e.target.closest("g.edge");
  if (edge) { const link = { from: edge.dataset.from, to: edge.dataset.to }; S.link = link; S.selected = null; render(); openMenu(e.clientX, e.clientY, [{ label: `Remove link ${link.from} to ${link.to}`, run: () => disconnectLink(link) }]); return; }
  const card = e.target.closest && e.target.closest(".card");
  if (card && card.dataset.child) return;   // a child card has no menu: it is not in the graph
  if (card) { S.selected = card.dataset.name; S.link = null; render(); openMenu(e.clientX, e.clientY, [{ label: `Rename ${card.dataset.name}`, run: () => { S.selected = card.dataset.name; render(); focusName(); } }, { label: `Remove ${card.dataset.name}`, run: () => removeNode(card.dataset.name) }]); return; }
  const r = $("map").getBoundingClientRect();
  const at = { x: (e.clientX - r.left - S.view.tx) / S.view.k, y: (e.clientY - r.top - S.view.ty) / S.view.k };
  openMenu(e.clientX, e.clientY, [{ sep: "Add a node here" }, ...KIND_LIST.map((k) => ({ label: k, run: () => addNode(k, at) }))]);
});
window.addEventListener("pointerdown", (e) => { if (!e.target.closest || !e.target.closest("#menu")) closeMenu(); });

async function applyEdit() {
  let edited;
  try { edited = JSON.parse($("node-json").value); } catch (e) { $("apply-msg").textContent = "Not JSON: " + e.message; $("apply-msg").className = "msg-bad"; return; }
  const before = snap();
  const r = core.applyNodeEdit(S.doc, S.selected, edited);
  if (r.error) { $("apply-msg").textContent = r.error; $("apply-msg").className = "msg-bad"; return; }
  // After an edit the text is the dashboard's own output, so a record's hash is checked against what the saved file hashes to.
  await commit(r.doc, before);
}

function download(name, text, type) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], { type: type || "application/json" }));
  a.download = name; a.click(); URL.revokeObjectURL(a.href);
}

const readFile = (f) => new Promise((ok, no) => { const r = new FileReader(); r.onload = () => ok(r.result); r.onerror = () => no(r.error); r.readAsText(f); });

// A file the owner opens is a read like any other: the panel says it is reading, and a failed read names the file.
async function readTextOf(f) {
  readStart("graph", f.name); renderSide();
  try { const text = await readFile(f); readSettle("graph"); return text; } catch (e) { readFail("graph", f.name, e.message || String(e)); setStatus(`Could not read ${f.name}: ${e.message}`, true); renderSide(); return null; }
}
$("f-graph").addEventListener("change", async (e) => { const f = e.target.files[0]; if (f) { const text = await readTextOf(f); if (text !== null) await loadGraph(text, f.name); } e.target.value = ""; });
const loadExample = () => loadGraph(JSON.stringify(EXAMPLE), "five-phases.graph.json");
$("b-example").addEventListener("click", loadExample);
$("side").addEventListener("click", (e) => {
  const b = e.target.closest("[data-start]");
  if (!b) return;
  if (b.dataset.start === "open") $("l-open").click();
  else if (b.dataset.start === "example") loadExample();
});
// `squad editor <id>` opens this page with the named workflow in its fragment: a page opened from a file cannot read a file of its own.
const OPENED = core.openedWorkflow(location.hash);
if (OPENED && OPENED.error) setStatus(OPENED.error, true);
else if (OPENED) loadGraph(OPENED.text, OPENED.name);
$("f-record").addEventListener("change", async (e) => {
  const f = e.target.files[0]; if (!f) return;
  readStart("record", f.name); renderSide();
  try {
    const r = core.parseRecord(await readFile(f));
    if (r.error) { readFail("record", f.name, r.error); setStatus(r.error, true); }
    else { readSettle("record"); stopFollow(); S.events = r.events; S.recordBad = r.bad || null; S.cursor = null; S.issueTotal = null; setStatus(`Opened record ${f.name} (read-only).`); await recompute(); }
  } catch (err) { readFail("record", f.name, err.message || String(err)); setStatus(`Could not read ${f.name}: ${err.message}`, true); }
  renderSide();
  e.target.value = "";
});
