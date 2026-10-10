// --- save, draft copy, hash chip, and the changed-file stop ---
const PICKER = !!window.showOpenFilePicker;
const JSON_TYPES = [{ description: "JSON", accept: { "application/json": [".json"] } }];
const stampOf = async (h) => { const f = await h.getFile(); return { lastModified: f.lastModified, text: await f.text() }; };
const hidePanels = () => { $("stop").hidden = true; $("offer").hidden = true; };
const layoutText = () => JSON.stringify(core.layoutForSave(S.layout, nodesOf().map((n) => n.name)).layout, null, 2) + "\n";
function dropNote(dropped) { return dropped.length ? ` Dropped ${dropped.length} layout key${dropped.length > 1 ? "s" : ""} for nodes that no longer exist: ${dropped.join(", ")}.` : ""; }

function refreshMode() {
  const m = core.saveMode({ picker: PICKER, handle: !!S.handle });
  $("mode").textContent = m.label;
  $("mode").title = m.mode === "in-place" ? "Save graph writes the opened file. It stops when the file changed on disk." : "Save graph downloads a file. The page cannot see a change to the file on disk, and a download can create name (1).graph.json.";
}

// The hash chip shows the short hash of the text that Save graph writes now. With a record open, a second line compares the record's hash.
let chipFor = null;
async function updateChip() {
  const chip = $("chip");
  if (!S.doc) { chip.textContent = "no graph"; chip.disabled = true; $("rechash").textContent = ""; chipFor = null; return; }
  if (chipFor !== S.text) { chipFor = S.text; const t = S.text; let c = null; try { c = await core.hashChip(t); } catch (e) { c = null; } if (S.text === t) S.chip = c; }
  chip.textContent = S.chip ? "hash " + S.chip.short : "no canonical form";
  chip.disabled = !S.chip;
  const rec = S.events && S.events[0] && S.events[0].event === "run_start" ? S.events[0].graph_hash : null;
  const rel = core.hashRelation(rec, S.chip && S.chip.full);
  $("rechash").textContent = rel ? `record ${String(rec).slice(0, 12)} ${rel}` : "";
  $("rechash").className = rel === "does not match" ? "msg-bad" : "";
  renderReview();
}
$("chip").addEventListener("click", async () => {
  if (!S.chip) return;
  try { await navigator.clipboard.writeText(S.chip.full); setStatus("Copied the full graph hash."); } catch (e) { window.prompt("Full graph hash", S.chip.full); }
});

// The draft copy: graph text, layout, name, a time, and the hash of the last file loaded or saved. It lives in browser storage only.
const DRAFT_DB = "agent-squad-run-dashboard-draft";
const draftIdb = (mode, fn) => new Promise((ok, no) => {
  const open = indexedDB.open(DRAFT_DB, 1);
  open.onupgradeneeded = () => open.result.createObjectStore("drafts");
  open.onerror = () => no(open.error);
  open.onsuccess = () => { const tx = open.result.transaction("drafts", mode), req = fn(tx.objectStore("drafts")); tx.oncomplete = () => { open.result.close(); ok(req.result); }; tx.onerror = () => no(tx.error); };
});
let draftTimer = null, noRecoveryTold = false;
function noRecovery(e) { if (noRecoveryTold) return; noRecoveryTold = true; setStatus($("status").textContent + ` There is no recovery in this session: the browser storage is not available (${e && e.message ? e.message : e}).`); }
const isDirty = () => !!S.doc && (core.saveText(S.text) !== S.baseText || JSON.stringify(S.layout) !== S.baseLayout);
function scheduleDraft() {
  if (!S.doc || S.offerPending || !isDirty()) return;
  clearTimeout(draftTimer);
  draftTimer = setTimeout(async () => {
    if (!isDirty() || S.offerPending) return;
    try { await draftIdb("readwrite", (st) => st.put(core.draftRecord({ name: S.name, text: core.saveText(S.text), layout: S.layout, time: Date.now(), baseHash: S.baseHash }), S.name)); } catch (e) { noRecovery(e); }
  }, 500);
}
// A save clears the draft. A graph save also takes the layout as it is: a node added since the last save has an automatic position, not a saved choice.
async function settleDraft() {
  clearTimeout(draftTimer);
  try { if (isDirty()) await draftIdb("readwrite", (st) => st.put(core.draftRecord({ name: S.name, text: core.saveText(S.text), layout: S.layout, time: Date.now(), baseHash: S.baseHash }), S.name)); else await draftIdb("readwrite", (st) => st.delete(S.name)); } catch (e) { noRecovery(e); }
}
async function offerDraft() {
  let d = null;
  try { d = await draftIdb("readonly", (st) => st.get(S.name)); } catch (e) { noRecovery(e); return; }
  const o = core.draftOffer(d, S.name, S.baseText);
  if (!o) return;
  S.offerPending = o;
  const box = $("offer");
  box.hidden = false; openSide();
  box.innerHTML = `A draft copy of <b>${esc(o.name)}</b> from ${esc(new Date(o.time).toLocaleString())} was kept in this browser. It differs from the file you opened.<div class="row"><button id="b-restore">Restore the draft</button><button id="b-discard">Discard the draft</button></div>`;
  $("b-restore").onclick = async () => {
    const r = core.parseImport(o.text);
    if (r.error) { setStatus("The draft is not a graph: " + r.error, true); return; }
    S.doc = r.doc; S.text = o.text; S.layout = o.layout || core.autoLayout(S.doc); S.hist = core.histNew(100); S.selected = null; S.offerPending = null; box.hidden = true;
    setStatus(`Restored the draft of ${o.name}. It is not saved to the file yet.`);
    await recompute(); fit();
  };
  $("b-discard").onclick = async () => { S.offerPending = null; box.hidden = true; try { await draftIdb("readwrite", (st) => st.delete(S.name)); } catch (e) { noRecovery(e); } setStatus("Discarded the draft."); };
}

// One save path for the graph file and the layout file. A save stops for a file conflict only, never for a validation error.
const TARGETS = {
  graph: { label: "graph", handle: () => S.handle, stamp: () => S.stamp, setStamp: (v) => { S.stamp = v; }, text: () => core.saveText(S.text), copyName: () => S.name + ".copy.graph.json", done: async (text) => { S.baseText = text; S.baseLayout = JSON.stringify(S.layout); try { S.baseHash = (await core.hashChip(text)).full; } catch (e) { S.baseHash = null; } },
    reload: async (cur, h) => { const keep = S.layout; try { await draftIdb("readwrite", (st) => st.delete(S.name)); } catch (e) { noRecovery(e); } if (await loadGraph(cur.text, h.name)) { S.handle = h; S.stamp = cur; S.layout = keep && Object.keys(keep).length ? { ...S.layout, ...keep } : S.layout; refreshMode(); render(); } } },
  layout: { label: "layout", handle: () => S.layoutHandle, stamp: () => S.layoutStamp, setStamp: (v) => { S.layoutStamp = v; }, text: layoutText, copyName: () => S.name + ".copy.layout.json", done: async () => { S.baseLayout = JSON.stringify(S.layout); },
    reload: async (cur) => { try { S.layout = { ...S.layout, ...JSON.parse(cur.text) }; S.layoutStamp = cur; S.baseLayout = JSON.stringify(S.layout); setStatus("Reloaded the layout from disk."); render(); } catch (x) { setStatus("The layout on disk is not JSON: " + x.message, true); } } },
};
async function writeInPlace(t, text) {
  const h = t.handle();
  try { const w = await h.createWritable(); await w.write(text); await w.close(); } catch (e) { setStatus(`In-place save of the ${t.label} failed: ${e.message}. Use Export for a copy.`, true); return false; }
  t.setStamp(await stampOf(h)); await t.done(text); await settleDraft();
  return true;
}
function showStop(t, cur, why) {
  const box = $("stop");
  box.hidden = false; openSide();
  box.innerHTML = `<b>The ${t.label} file changed on disk.</b> ${esc(why)} Nothing was written.<div class="row"><button id="b-reload">Reload from disk</button><button id="b-copy">Save a copy</button><button id="b-over">Overwrite anyway</button></div>`;
  $("b-reload").onclick = async () => { if (!cur) { setStatus("The file cannot be read, so there is nothing to reload.", true); return; } if (!window.confirm("Reload from disk? Your edits to the " + t.label + " are dropped.")) return; box.hidden = true; await t.reload(cur, t.handle()); };
  $("b-copy").onclick = () => { download(t.copyName(), t.text()); box.hidden = true; setStatus(`Saved a copy as ${t.copyName()}. The file on disk is unchanged.`); };
  $("b-over").onclick = async () => { box.hidden = true; const text = t.text(); if (await writeInPlace(t, text)) setStatus(`Overwrote the ${t.label} file.`); };
}
async function saveTarget(key) {
  const t = TARGETS[key];
  if (!S.doc) return;
  const text = t.text();
  if (!(PICKER && t.handle())) {
    download(key === "graph" ? (S.fileName || S.name + ".graph.json") : S.name + ".layout.json", text); await t.done(text); await settleDraft();
    const note = key === "graph" && !S.downloadNoted ? " In download mode the page cannot see a change to the file on disk, and the browser can create name (1).graph.json if the name exists." : "";
    if (key === "graph") S.downloadNoted = true;
    setStatus((key === "graph" && S.errors.length ? `Saved with ${S.errors.length} error(s). The engine refuses an invalid graph at load.` : `Saved the ${t.label}.`) + note + (key === "layout" ? dropNote(core.layoutForSave(S.layout, nodesOf().map((n) => n.name)).dropped) : ""));
    return;
  }
  let cur = null, why = "";
  try { cur = await stampOf(t.handle()); } catch (e) { why = "The file cannot be read any more: " + e.message + "."; }
  if (!why && core.saveGate({ mode: "in-place", stored: t.stamp(), current: cur }) === "stop") why = "It is newer than the copy you opened or last saved.";
  if (why) { showStop(t, cur, why); return; }
  if (await writeInPlace(t, text)) setStatus(key === "graph" && S.errors.length ? `Saved in place with ${S.errors.length} error(s). The engine refuses an invalid graph at load.` : `Saved the ${t.label} in place.` + (key === "layout" ? dropNote(core.layoutForSave(S.layout, nodesOf().map((n) => n.name)).dropped) : ""));
}

async function openGraphPicked() {
  let h;
  try { [h] = await window.showOpenFilePicker({ types: JSON_TYPES }); } catch (e) { if (e.name !== "AbortError") pickerFailed("graph", e); return; }
  let cur; try { cur = await stampOf(h); } catch (e) { setStatus("Could not read the file: " + e.message, true); return; }
  if (await loadGraph(cur.text, h.name)) { S.handle = h; S.stamp = cur; refreshMode(); }
}
async function openLayoutPicked() {
  let h;
  try { [h] = await window.showOpenFilePicker({ types: JSON_TYPES }); } catch (e) { if (e.name !== "AbortError") pickerFailed("layout", e); return; }
  try { const cur = await stampOf(h); S.layout = { ...S.layout, ...JSON.parse(cur.text) }; S.layoutHandle = h; S.layoutStamp = cur; S.baseLayout = JSON.stringify(S.layout); setStatus(`Opened layout ${h.name} (in-place save).`); render(); } catch (x) { setStatus("Layout is not JSON: " + x.message, true); }
}
// With a picker, the two Open buttons use it. Without one, the file inputs stay the way in, and Save downloads.
// A picker that fails (a browser can refuse it at file://) hands the button back to the file input, and the page says so.
const onOpenGraph = (e) => { e.preventDefault(); openGraphPicked(); };
const onOpenLayout = (e) => { e.preventDefault(); openLayoutPicked(); };
function pickerFailed(which, e) {
  $(which === "graph" ? "l-open" : "l-open-layout").removeEventListener("click", which === "graph" ? onOpenGraph : onOpenLayout);
  $(which === "graph" ? "f-graph" : "f-layout").click();
  setStatus(`The browser refused the file picker (${e.message}). Choose the file in the plain dialog; Save graph downloads in this session.`, true);
}
if (PICKER) { $("l-open").addEventListener("click", onOpenGraph); $("l-open-layout").addEventListener("click", onOpenLayout); }
$("f-layout").addEventListener("change", async (e) => {
  const f = e.target.files[0]; if (!f) return;
  try { const l = JSON.parse(await readFile(f)), before = S.doc ? snap() : null; S.layout = { ...S.layout, ...l }; if (before) track(before); S.layoutHandle = null; S.layoutStamp = null; S.baseLayout = JSON.stringify(S.layout); setStatus(`Opened layout ${f.name}.`); render(); } catch (x) { setStatus("Layout is not JSON: " + x.message, true); }
  e.target.value = "";
});
$("b-save").addEventListener("click", () => saveTarget("graph"));
$("b-save-layout").addEventListener("click", () => saveTarget("layout"));
// Export is a download of a copy, in every browser, even where a handle exists. It changes no handle and clears no draft.
$("b-export").addEventListener("click", () => { if (S.doc) { download(S.fileName || S.name + ".graph.json", core.saveText(S.text)); setStatus("Exported a copy of the graph."); } });
$("b-layout").addEventListener("click", () => { if (S.doc) { const r = core.layoutForSave(S.layout, nodesOf().map((n) => n.name)); download(S.name + ".layout.json", JSON.stringify(r.layout, null, 2) + "\n"); setStatus("Exported a copy of the layout." + dropNote(r.dropped)); } });
refreshMode();

render();
