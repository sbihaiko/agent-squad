// --- the legend and the help overlay: rows come from the core; a swatch is a card group, so the card CSS paints it ---
function swatches() {
  const cards = core.LEGEND.map((r) => `<span class="lg"><svg width="24" height="14" viewBox="0 0 24 14"><g class="card ${r.cls}"><rect x="1" y="1" width="22" height="12" rx="3"/>${r.glyph ? `<text class="gl" x="12" y="11" text-anchor="middle" style="font-size:10px">${r.glyph}</text>` : ""}</g></svg>${esc(r.label)}</span>`);
  const chips = core.LEGEND_CHIPS.map((r) => `<span class="lg"><span class="chip-sw ${r.cls}">${esc(r.label)}</span></span>`);
  return cards.concat(chips).join("");
}
// A run-state row draws the pill the hub's sidebar draws for that run: the same words, the same colour and the same border, from the one table.
const runPill = (r) => `<span class="b ${r.cls}">${esc(r.text)}</span>`;
// The side strip's list: three headed groups (node outcomes, run states, interaction). Always expanded.
// A run row is the pill above its short description, in the hub's table order, so every state a surface can show a pill for is named here.
const LEGEND_GROUPS = [["node", "Node outcomes"], ["run", "Run states"], ["interaction", "Interaction"]];
function legendList() {
  const sw = (r) => `<svg width="24" height="14" viewBox="0 0 24 14"><g class="card ${r.cls}"><rect x="1" y="1" width="22" height="12" rx="3"/>${r.glyph ? `<text class="gl" x="12" y="11" text-anchor="middle" style="font-size:10px">${r.glyph}</text>` : ""}</g></svg>`;
  const rows = {
    node: core.LEGEND.filter((r) => r.group === "node").map((r) => [sw(r), r.label, ""]),
    run: core.RUN_LEGEND.map((r) => [runPill(r), r.label, " rlg"]),
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
// #footer (the hint) sets the floor; keep the floating #replay bar clear of it, and the side legend above both, at any width.
(function trackFooterHeight() {
  const footer = $("footer"), bar = $("replay");
  const sync = () => {
    bar.style.bottom = (footer.offsetHeight + REPLAY_GAP) + "px";
    const barH = bar.offsetHeight ? bar.offsetHeight + REPLAY_GAP : 0;
    $("legend").style.setProperty("--legend-bottom", (footer.offsetHeight + REPLAY_GAP + barH + REPLAY_GAP) + "px");
  };
  new ResizeObserver(sync).observe(footer);
  new ResizeObserver(sync).observe(bar);
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

$("f-graph").addEventListener("change", async (e) => { const f = e.target.files[0]; if (f) await loadGraph(await readFile(f), f.name); e.target.value = ""; });
$("b-example").addEventListener("click", () => loadGraph(JSON.stringify(EXAMPLE), "five-phases.graph.json"));
$("f-record").addEventListener("change", async (e) => {
  const f = e.target.files[0]; if (!f) return;
  const r = core.parseRecord(await readFile(f));
  if (r.error) setStatus(r.error, true); else { stopFollow(); S.events = r.events; S.recordBad = r.bad || null; S.cursor = null; S.issueTotal = null; setStatus(`Opened record ${f.name} (read-only).`); await recompute(); }
  e.target.value = "";
});
