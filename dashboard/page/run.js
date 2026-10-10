// --- the run folder: one read-only handle, remembered in this browser (decisions #62, #63; ticket #76) ---
// A source is {name, graph(), record()}: each returns the file text, or null when the folder has no such file.
async function loadRunSource(src) {
  const rec = await src.record();
  if (rec === null) { setStatus(`No record.jsonl in ${src.name} (looked at the folder and at run/).`, true); return false; }
  const pr = core.parseLive(rec);
  if (pr.error) { setStatus(pr.error, true); return false; }
  S.events = pr.events; S.recordBad = pr.bad || null; S.cursor = null; S.expanded = [];   // a new run starts with every group collapsed
  // The issue set lives in the run folder, not the record: count target/issues for the top bar's total.
  S.issueTotal = src.issues ? await src.issues() : null;
  if (src.live) startFollow(src, rec); else stopFollow();
  const g = await src.graph();
  if (g === null || !(await loadGraph(g, src.name + "/graph.json"))) { await recompute(); if (g !== null) return true; }
  // A short toast: the folder name is already in the top bar, and a push or a reread needs no words after the load.
  setStatus(`Opened run ${pr.events[0].run_id} (read-only).` + (src.live ? "" : " A folder list is a snapshot: connect the folder to follow a live run.") + (g === null ? " The folder has no graph.json: open the graph to see the nodes." : ""));
  return true;
}
// While a run lives, the page follows it one of two ways. A source with a `stream` is followed by push: each pushed event extends the
// record and paints. A source with none (the folder handle, the folder list) is followed by the timer: it rereads the record through
// its read-only handle. The interval is the engine's (run_start), and no control in the page sets it (tickets #80, #106).
const FOLLOW = { timer: null, text: null, gen: 0, push: null, how: "reread" };
function followNote() { return FOLLOW.how === "push" ? "Following the run by push: each event paints as it is recorded." : `Rereading the record every ${core.readDelay(S.events)} s.`; }
function stopFollow() { FOLLOW.gen++; clearTimeout(FOLLOW.timer); FOLLOW.timer = null; if (FOLLOW.push) FOLLOW.push.close(); FOLLOW.push = null; FOLLOW.how = "reread"; }
function startFollow(src, firstText) {
  stopFollow();
  if (src.stream && !S.events.filter((e) => e.event === "verdict").length) return startPush(src, firstText);
  startTimer(src, firstText);
}
function startPush(src, firstText) {
  const gen = FOLLOW.gen;
  FOLLOW.how = "push";
  const live = (t) => { if (gen === FOLLOW.gen) setStatus(t); };
  FOLLOW.push = core.followPush(src, S.events, {
    changed: async (list) => { if (gen !== FOLLOW.gen) return; S.events = list; if (!S.dragging) await recompute({ live: true }); },
    ended: () => { live("The run ended. The stream is closed."); FOLLOW.how = "reread"; },
    status: (t, bad) => { if (gen === FOLLOW.gen) setStatus(t, bad); },
    fail: (why) => { if (gen !== FOLLOW.gen) return; setStatus(`Push stopped (${why}). Rereading the record every ${core.readDelay(S.events)} s instead.`, true); startTimer(src, null, true); },
  });
}
function startTimer(src, firstText, keepPush) {
  if (keepPush) { FOLLOW.gen++; FOLLOW.push = null; }
  FOLLOW.how = "reread";
  const gen = FOLLOW.gen;
  FOLLOW.text = firstText;
  const tick = async () => {
    if (gen !== FOLLOW.gen) return;
    try {
      const t = await src.record();
      if (gen !== FOLLOW.gen) return;
      let grew = false;
      if (t !== null && t !== FOLLOW.text) { const pr = core.parseLive(t); FOLLOW.text = t; if (!pr.error) { S.events = pr.events; S.recordBad = pr.bad || null; grew = true; } }
      const over = S.events.filter((e) => e.event === "verdict").length > 0;
      if ((grew || !over) && !S.dragging) await recompute({ live: true });
    } catch (e) { /* the folder went away or one read failed: try again at the next tick */ }
    if (gen === FOLLOW.gen) FOLLOW.timer = setTimeout(tick, core.readDelay(S.events) * 1000);
  };
  FOLLOW.timer = setTimeout(tick, core.readDelay(S.events) * 1000);
}
function handleSource(dir) {
  const read = async (d, nm) => { try { return await (await (await d.getFileHandle(nm)).getFile()).text(); } catch (e) { return null; } };
  const countIssues = async () => { try { const t = await dir.getDirectoryHandle("target"); const i = await t.getDirectoryHandle("issues"); let n = 0; for await (const [nm, h] of i.entries()) if (h.kind === "file" && nm.endsWith(".md")) n++; return n || null; } catch (e) { return null; } };
  return { name: dir.name, live: true, issues: countIssues, graph: () => read(dir, "graph.json"),
    record: async () => { const r = await read(dir, "record.jsonl"); if (r !== null) return r; try { return await read(await dir.getDirectoryHandle("run"), "record.jsonl"); } catch (e) { return null; } } };
}
function fileListSource(files) {
  const rel = (f) => f.webkitRelativePath.split("/").slice(1).join("/");
  const find = (p) => files.find((f) => rel(f) === p);
  const text = async (f) => f ? await readFile(f) : null;
  const countIssues = async () => { const n = files.filter((f) => /^target\/issues\/[^/]+\.md$/.test(rel(f))).length; return n || null; };
  return { name: files.length ? files[0].webkitRelativePath.split("/")[0] : "folder", issues: countIssues, graph: () => text(find("graph.json")), record: async () => (await text(find("record.jsonl"))) ?? (await text(find("run/record.jsonl"))) };
}
const IDB_NAME = "agent-squad-run-dashboard", IDB_KEY = "run-folder";
const idb = (mode, fn) => new Promise((ok, no) => {
  const open = indexedDB.open(IDB_NAME, 1);
  open.onupgradeneeded = () => open.result.createObjectStore("handles");
  open.onerror = () => no(open.error);
  open.onsuccess = () => { const tx = open.result.transaction("handles", mode), req = fn(tx.objectStore("handles")); tx.oncomplete = () => ok(req.result); tx.onerror = () => no(tx.error); };
});
// `squad dashboard` puts the run folder in the URL (#run=PATH): this page is for that one run. The page cannot read a
// folder it was not given, so it shows the path and never restores a remembered folder of another run.
const WANT = (() => { const m = /^#run=(.+)$/.exec(location.hash); if (!m) return null; try { const path = decodeURIComponent(m[1]); return { path, name: path.split("/").filter(Boolean).pop() || path }; } catch (e) { return null; } })();
if (WANT) document.title = `${document.title} \u00b7 ${WANT.name}`;
const askForWanted = () => setStatus(`This page is for the run folder ${WANT.path}. Click "Connect run folder" and choose it.`);
async function connectFolder() {
  let dir;
  try { dir = await window.showDirectoryPicker({ mode: "read", id: "agent-squad-run" }); } catch (e) { if (e.name !== "AbortError") setStatus("Could not open the folder: " + e.message, true); return; }
  if (await loadRunSource(handleSource(dir))) { S.folder = dir.name; if (WANT && dir.name !== WANT.name && dir.name !== "run") setStatus($("status").textContent + ` This page was opened for ${WANT.name}.`, true); try { await idb("readwrite", (st) => st.put(dir, IDB_KEY)); } catch (e) { setStatus($("status").textContent + " The browser cannot remember it: " + e.message); } }
}
async function restoreFolder() {
  let dir;
  try { dir = await idb("readonly", (st) => st.get(IDB_KEY)); } catch (e) { if (WANT) askForWanted(); return; }
  if (!dir || (WANT && dir.name !== WANT.name)) { if (WANT) askForWanted(); return; }
  let perm = "prompt";
  try { perm = await dir.queryPermission({ mode: "read" }); } catch (e) { return; }
  if (perm === "granted") { await loadRunSource(handleSource(dir)); return; }
  const b = $("b-folder");
  b.textContent = `Reconnect ${dir.name}`;
  b.onclick = async () => { if ((await dir.requestPermission({ mode: "read" })) === "granted") { b.textContent = "Connect run folder"; b.onclick = connectFolder; await loadRunSource(handleSource(dir)); } };
}
// Served by `squad dashboard`: the page comes from a loopback server that holds one run (its one write is a pause or resume request), so the run loads with no click and no picker.
const FROM_SERVER = location.protocol === "http:" || location.protocol === "https:";
// The one place that knows about EventSource: it turns the browser's stream into the core's handlers.
function openStream(path, h) {
  const es = new EventSource(path);
  es.onmessage = (m) => h.line(m.data);
  es.addEventListener("end", (m) => { let r = ""; try { r = JSON.parse(m.data).reason; } catch (e) { r = "unreadable end"; } h.end(r); });
  es.onerror = () => h.error(es.readyState === 2);
  return { close: () => es.close() };
}
async function connectServed() {
  let name = "run", issues = null;
  try { const r = await fetch("run/info.json", { cache: "no-store" }); if (r.ok) { const j = await r.json(); name = j.name || name; if (typeof j.issues === "number" && j.issues > 0) issues = j.issues; S.issueHeld = typeof j.held === "number" ? j.held : 0; } } catch (e) { /* the server went away: the load below says so */ }
  document.title = `${document.title} \u00b7 ${name}`;
  $("b-folder").style.display = "none"; $("l-folder").style.display = "none"; $("l-record").style.display = "none";
  const src = core.httpSource((p) => fetch(p, { cache: "no-store" }), name, openStream);
  if (issues !== null) src.issues = async () => issues;   // the issue set the run folder holds, from the server's info
  await loadControl();
  if (await loadRunSource(src)) S.folder = name;
}
if (FROM_SERVER) connectServed();
else if (window.showDirectoryPicker) { $("b-folder").onclick = connectFolder; restoreFolder(); } else { $("b-folder").style.display = "none"; $("l-folder").style.display = ""; if (WANT) askForWanted(); }
$("f-folder").addEventListener("change", async (e) => { const fs = [...e.target.files]; if (fs.length) await loadRunSource(fileListSource(fs)); e.target.value = ""; });

