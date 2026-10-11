// --- the inspector form: fields grouped by purpose; the core builds the groups and applies each edit ---
const groupOpen = {};
for (const k of ["models", "efforts", "skills"]) $("dl-" + k).innerHTML = SERVED[k].map((v) => `<option value="${esc(v)}">`).join("");
function fieldHtml(f) {
  const id = "f-" + f.key, dis = f.readonly ? " disabled" : "", a = `id="${id}" data-key="${esc(f.key)}"${dis}`;
  let input;
  if (f.type === "area") input = `<textarea ${a} rows="6" spellcheck="false">${esc(f.value)}</textarea><div class="note"><span id="ic">${[...f.value].length}</span> characters</div>`;
  else if (f.type === "json") input = `<textarea ${a} rows="${Math.min(8, Math.max(2, f.value.split("\n").length))}" spellcheck="false" placeholder="JSON">${esc(f.value)}</textarea>`;
  else if (f.type === "bool") input = `<select ${a}>${["", "true", "false"].map((v) => `<option value="${v}"${v === f.value ? " selected" : ""}>${v || "(not set)"}</option>`).join("")}</select>`;
  else if (f.type === "enum") input = `<select ${a}>${["", ...f.enum, ...(f.value && !f.enum.includes(f.value) ? [f.value] : [])].map((v) => `<option value="${esc(v)}"${v === f.value ? " selected" : ""}>${esc(v) || "(not set)"}</option>`).join("")}</select>`;
  else input = `<input ${a} value="${esc(f.value)}" spellcheck="false"${f.suggest && f.type === "text" ? ` list="dl-${f.suggest}"` : ""}${f.type === "list" ? ` placeholder="comma separated"` : ""}>`;
  const chips = f.suggest && f.type === "list" && !f.readonly ? `<div class="chips">${SERVED[f.suggest].map((v) => `<button type="button" data-chip="${esc(f.key)}" data-val="${esc(v)}">${esc(v)}</button>`).join("")}</div>` : "";
  const rm = f.unknown ? `<div class="chips"><button type="button" data-remove="${esc(f.key)}">Remove this field</button></div>` : "";
  return `<div class="fld${f.unknown ? " unk" : ""}"><label for="${id}">${esc(f.key)}${f.readonly ? " (read-only)" : ""}${f.present ? "" : " \u00b7 unset"}</label><div>${input}</div>${chips}${rm}</div>`;
}
function fieldGroupHtml(n, g) {
  const key = n.kind + ":" + g.id, open = key in groupOpen ? groupOpen[key] : g.open;
  return `<details data-group="${key}"${open ? " open" : ""}><summary>${esc(g.title)}</summary>${g.fields.map(fieldHtml).join("")}</details>`;
}
// The node name is the one the form was drawn for. A field's change event fires when the owner clicks another node, after the selection moved.
async function setFieldFrom(name, key, text) {
  if (!S.doc || !name) return;
  const before = snap();
  const r = core.setField(S.doc, name, key, text);
  if (r.error) { setStatus(r.error, true); renderSide(); return; }
  if (r.doc === S.doc) return;
  await commit(r.doc, before);
  setStatus(`Set ${key} on ${name}.`);
}
function wireFields() {
  const side = $("side"), name = S.selected;
  for (const d of side.querySelectorAll("details[data-group]")) d.addEventListener("toggle", () => { groupOpen[d.dataset.group] = d.open; });
  for (const el of side.querySelectorAll("[data-key]")) el.addEventListener("change", () => setFieldFrom(name, el.dataset.key, el.value));
  const ins = $("f-instructions");
  if (ins) ins.addEventListener("input", () => { $("ic").textContent = [...ins.value].length; });
  for (const b of side.querySelectorAll("[data-chip]")) b.addEventListener("click", () => {
    const input = $("f-" + b.dataset.chip), have = input.value.split(",").map((x) => x.trim()).filter(Boolean);
    if (!have.includes(b.dataset.val)) setFieldFrom(name, b.dataset.chip, [...have, b.dataset.val].join(", "));
  });
  for (const b of side.querySelectorAll("[data-remove]")) b.addEventListener("click", () => setFieldFrom(name, b.dataset.remove, ""));
}

// --- add and remove a node: pure functions in the core, thin wiring here ---
const KIND_LIST = core.KINDS;
$("s-kind").innerHTML = KIND_LIST.map((k) => `<option>${k}</option>`).join("");
function renderAddbar() {
  const gates = S.doc && Array.isArray(S.doc.gates) ? S.doc.gates : [];
  const keep = $("s-check").value;
  $("s-check").innerHTML = gates.map((g) => `<option>${esc(g.id)}</option>`).join("");
  if (gates.some((g) => g.id === keep)) $("s-check").value = keep;
  $("s-check").hidden = $("s-kind").value !== "gate";
  $("b-add").disabled = !S.doc || !Array.isArray(S.doc.nodes);
  $("b-remove").disabled = !nodesOf().some((n) => n.name === S.selected);   // a child card is run-only: nothing to remove
  $("b-unlink").disabled = !S.link;
  $("b-undo").disabled = !S.hist.past.length;
  $("b-redo").disabled = !S.hist.future.length;
}
$("s-kind").addEventListener("change", renderAddbar);

// Undo and redo. The history holds graph-and-layout snapshots, lives in this page only, and is never saved.
const snap = () => core.snapshot(S.doc, S.layout);
function track(before) { S.hist = core.histRecord(S.hist, before); renderAddbar(); }
async function stepHistory(dir) {
  if (!S.doc) return;
  const r = dir < 0 ? core.histUndo(S.hist, snap()) : core.histRedo(S.hist, snap());
  if (!r) { setStatus(dir < 0 ? "Nothing to undo." : "Nothing to redo."); return; }
  S.hist = r.h; S.doc = r.state.doc; S.layout = r.state.layout;
  S.text = JSON.stringify(S.doc, null, 2) + "\n";
  S.link = null; S.warnings = [];
  if (S.selected && !nodesOf().some((n) => n.name === S.selected)) S.selected = null;
  await recompute();
  setStatus(dir < 0 ? "Undid the last edit." : "Redid the edit.");
}

async function commit(doc, before) {
  if (before) track(before);
  S.doc = doc;
  // After an edit the text is the dashboard's own output, so a record's hash is checked against what the saved file hashes to.
  S.text = JSON.stringify(S.doc, null, 2) + "\n";
  await recompute();
}

function viewCentre() { const s = mapSize(); return { x: (s.w / 2 - S.view.tx) / S.view.k - BOX.w / 2, y: (s.h / 2 - S.view.ty) / S.view.k - BOX.h / 2 }; }

async function addNode(kind, at) {
  if (!S.doc || !Array.isArray(S.doc.nodes)) return;
  const before = snap();
  const sel = nodesOf().find((n) => n.name === S.selected);
  const opts = {};
  if (sel && ["loop", "each"].includes(sel.kind)) opts.parent = sel.name;
  if (kind === "gate" && $("s-check").value) opts.check = $("s-check").value;
  const r = core.addNode(S.doc, kind, opts);
  if (r.error) { setStatus(r.error, true); return; }
  S.layout[r.name] = core.freeSpot(S.layout, BOX, at || viewCentre(), 16);
  const owner = r.doc.nodes.find((n) => Array.isArray(n.children) && n.children.includes(r.name));
  S.selected = r.name; S.link = null; S.warnings = [];
  setStatus(`Added ${r.name}${owner ? " under " + owner.name : ""}.`);
  await commit(r.doc, before);
}

async function removeNode(name) {
  if (!S.doc || !name) return;
  const before = snap();
  const r = core.removeNode(S.doc, name);
  if (r.error) { setStatus(r.error, true); return; }
  delete S.layout[name];
  S.selected = null; S.link = null; S.warnings = r.warnings;
  setStatus(r.warnings.length ? `Removed ${name}. ${r.warnings.length} link${r.warnings.length > 1 ? "s" : ""} broke: see the side panel.` : `Removed ${name}.`);
  await commit(r.doc, before);
}

// --- rename a node and change its kind: pure functions in the core, thin wiring here ---
function nkMessage(text) { const m = $("nk-msg"); if (m) { m.textContent = text; m.className = "msg-bad"; } setStatus(text, true); }
function focusName() { const i = $("n-name"); if (i) { i.focus(); i.select(); } }

async function renameSelected() {
  if (!S.doc || !S.selected) return;
  const from = S.selected, to = $("n-name").value.trim();
  if (to === from) return;
  const before = snap();
  const r = core.renameNode(S.doc, from, to);
  if (r.error) { nkMessage(r.error); return; }
  S.layout = core.layoutRename(S.layout, from, to);
  S.selected = to; S.link = null; S.warnings = [];
  await commit(r.doc, before);
  setStatus(`Renamed ${from} to ${to}. Every reference now names ${to}.`);
}

async function changeSelectedKind() {
  if (!S.doc || !S.selected) return;
  const name = S.selected, kind = $("n-kind").value;
  const before = snap();
  const r = core.changeKind(S.doc, name, kind, kind === "gate" ? { check: $("n-check").value } : {});
  if (r.error) { nkMessage(r.error); return; }
  S.warnings = [];
  await commit(r.doc, before);
  const n = errsFor(name).length;
  setStatus(`Changed ${name} to ${kind}. ${n ? n + " rule error" + (n > 1 ? "s" : "") + " on the node: see the side panel." : "No rule error on the node."}`, n > 0);
}

async function connectNodes(from, to) {
  if (!S.doc || !from || !to) return;
  const before = snap();
  const r = core.connect(S.doc, from, to);
  if (r.error) { setStatus(r.error, true); return; }
  S.selected = null; S.link = { from, to }; S.warnings = r.warnings;
  setStatus(`Connected ${from} to ${to}.${r.warnings.length ? " " + r.warnings.length + " link" + (r.warnings.length > 1 ? "s" : "") + " broke: see the side panel." : ""}`);
  await commit(r.doc, before);
}

async function disconnectLink(link) {
  if (!S.doc || !link) return;
  const before = snap();
  const r = core.disconnect(S.doc, link.from, link.to);
  if (r.error) { setStatus(r.error, true); return; }
  S.link = null; S.warnings = r.warnings;
  setStatus(`Removed the link ${link.from} to ${link.to}.${r.warnings.length ? " " + r.warnings.length + " link" + (r.warnings.length > 1 ? "s" : "") + " broke: see the side panel." : ""}`);
  await commit(r.doc, before);
}

$("b-add").addEventListener("click", () => addNode($("s-kind").value));
$("b-remove").addEventListener("click", () => removeNode(S.selected));
$("b-unlink").addEventListener("click", () => disconnectLink(S.link));
$("b-undo").addEventListener("click", () => stepHistory(-1));
$("b-redo").addEventListener("click", () => stepHistory(1));
window.addEventListener("keydown", (e) => {
  if (e.key === "Escape") { closeMenu(); closeHelp(); }
  const t = e.target && e.target.tagName;
  // In a text field the browser's own undo keeps the text; elsewhere Ctrl or Cmd + Z walks the edit history.
  if ((e.ctrlKey || e.metaKey) && !e.altKey && !["TEXTAREA", "INPUT", "SELECT"].includes(t)) {
    const k = e.key.toLowerCase();
    if (k === "z") { e.preventDefault(); stepHistory(e.shiftKey ? 1 : -1); return; }
    if (k === "y") { e.preventDefault(); stepHistory(1); return; }
  }
  if ((e.key === "ArrowLeft" || e.key === "ArrowRight") && S.events && !e.metaKey && !e.ctrlKey && !e.altKey && !e.shiftKey && !["TEXTAREA", "INPUT", "SELECT"].includes(t)) { e.preventDefault(); moveReplay(e.key === "ArrowLeft" ? "back" : "forward"); }
  if (e.key === "?" && !["TEXTAREA", "INPUT", "SELECT"].includes(t)) { e.preventDefault(); toggleHelp(); return; }
  if ((e.key === "Delete" || e.key === "Backspace") && !["TEXTAREA", "INPUT", "SELECT"].includes(t)) {
    if (S.link) { e.preventDefault(); disconnectLink(S.link); }
    else if (S.selected && nodesOf().some((n) => n.name === S.selected)) { e.preventDefault(); removeNode(S.selected); }
  }
});

// Right-click: on a node the menu renames or removes it, on empty space the menu adds a node of a kind at that spot.
function closeMenu() { $("menu").style.display = "none"; }

