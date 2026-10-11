// Test door for the dashboard core under node. Reads {engine, core, ops} on stdin, prints one result per op.
// `core` is the bundle text of scripts/build_dashboard.py::core_bundle, the same text the page inlines.
let make;
// One history run: apply each edit like the page does (graph and layout together), record the state before it, then undo all and redo all.
function runHistory(core, o) {
  const BOX = { w: 150, h: 50 };
  let doc = o.doc, layout = JSON.parse(JSON.stringify(o.layout)), h = core.histNew(o.limit);
  const states = [core.snapshot(doc, layout)], skipped = [];
  for (const e of o.edits) {
    const before = core.snapshot(doc, layout);
    let r;
    if (e.op === "add") { r = core.addNode(doc, e.kind, e.opts || {}); if (!r.error) layout[r.name] = core.freeSpot(layout, BOX, { x: 40, y: 40 }, 16); }
    else if (e.op === "remove") { r = core.removeNode(doc, e.name); if (!r.error) delete layout[e.name]; }
    else if (e.op === "rename") { r = core.renameNode(doc, e.name, e.to); if (!r.error) layout = core.layoutRename(layout, e.name, e.to); }
    else if (e.op === "kind") r = core.changeKind(doc, e.name, e.kind, e.opts || {});
    else if (e.op === "connect") r = core.connect(doc, e.from, e.to);
    else if (e.op === "disconnect") r = core.disconnect(doc, e.from, e.to);
    else if (e.op === "setfield") r = core.setField(doc, e.name, e.key, e.text);
    else if (e.op === "move") { layout[e.name] = { x: e.x, y: e.y }; r = { doc }; }
    else throw new Error("unknown edit " + e.op);
    if (r.error) { skipped.push(e.op); continue; }
    doc = r.doc;
    h = core.histRecord(h, before);
    states.push(core.snapshot(doc, layout));
  }
  const undone = [], redone = [];
  let cur = core.snapshot(doc, layout);
  for (;;) { const u = core.histUndo(h, cur); if (!u) break; h = u.h; cur = u.state; undone.push(cur); }
  const afterAllUndo = cur, canUndoAfterAll = !!core.histUndo(h, cur);
  for (;;) { const r = core.histRedo(h, cur); if (!r) break; h = r.h; cur = r.state; redone.push(cur); }
  // a new edit after one undo clears the redo stack
  let h2 = core.histNew(o.limit), c2 = states[0];
  for (let i = 1; i < states.length; i++) h2 = core.histRecord(h2, states[i - 1]);
  const u1 = core.histUndo(h2, states[states.length - 1]);
  const cleared = u1 ? core.histRedo(core.histRecord(u1.h, u1.state), u1.state) : null;
  return { states, undone, redone, afterAllUndo, skipped, canUndoAfterAll, clearedRedo: cleared === null, size: h.past.length + h.future.length };
}

let input = "";
process.stdin.on("data", (c) => (input += c));
process.stdin.on("end", async () => {
  const { engine, core: bundle, ops } = JSON.parse(input);
  make = new Function(bundle + "\nreturn make;")();
  const core = make(engine);
  const out = [];
  for (const o of ops) {
    if (o.op === "validate") out.push(core.validate(o.doc));
    else if (o.op === "canon") out.push(core.canonFromText(o.text));
    else if (o.op === "hash") out.push(await core.graphHash(o.text));
    else if (o.op === "scriptdiagram") out.push(core.scriptDiagram(o.script));
    else if (o.op === "guirun") out.push(core.guiRun(o.events, o.cursor));
    else if (o.op === "scriptsvg") out.push(core.scriptSvg(o.script));
    else if (o.op === "edit") out.push(core.applyNodeEdit(o.doc, o.name, o.edited));
    else if (o.op === "rows") out.push(core.overlayRows(o.events));
    else if (o.op === "overlay") out.push(await core.overlayFor(o.text, o.events));
    else if (o.op === "import") out.push(core.parseImport(o.text));
    else if (o.op === "record") out.push(core.parseRecord(o.text));
    else if (o.op === "fit") out.push(core.viewFit(o.layout, o.box, o.size, o.pad));
    else if (o.op === "fitFloor") out.push(core.viewFitFloor(o.layout, o.box, o.size, o.pad, o.minK, o.anchor));
    else if (o.op === "zoom") out.push(core.viewZoomAt(o.view, o.cx, o.cy, o.factor));
    else if (o.op === "pan") out.push(core.viewPanBy(o.view, o.dx, o.dy));
    else if (o.op === "add") out.push(core.addNode(o.doc, o.kind, o.opts || {}));
    else if (o.op === "remove") out.push(core.removeNode(o.doc, o.name));
    else if (o.op === "rename") out.push(core.renameNode(o.doc, o.name, o.to));
    else if (o.op === "layoutRename") out.push(core.layoutRename(o.layout, o.name, o.to));
    else if (o.op === "kind") out.push(core.changeKind(o.doc, o.name, o.kind, o.opts || {}));
    else if (o.op === "unseen") out.push(core.unseenValues(o.node, o.served));
    else if (o.op === "groups") out.push(core.fieldGroups(o.node));
    else if (o.op === "setfield") out.push(core.setField(o.doc, o.name, o.key, o.text));
    else if (o.op === "connect") out.push(core.connect(o.doc, o.from, o.to));
    else if (o.op === "disconnect") out.push(core.disconnect(o.doc, o.from, o.to));
    else if (o.op === "spot") out.push(core.freeSpot(o.layout, o.box, o.want, o.gap));
    else if (o.op === "stats") out.push(core.runStats(o.events, o.doc, o.shown, o.now));
    else if (o.op === "totals") out.push(core.runTotals(o.events, o.cap === undefined ? null : o.cap, o.now));
    else if (o.op === "cells") out.push(core.totalsCells(core.runTotals(o.events, o.cap === undefined ? null : o.cap, o.now)));
    else if (o.op === "reason") out.push(core.reasonParts(o.text));
    else if (o.op === "cause") out.push(core.causeSummary(o.reasons));
    else if (o.op === "causes") out.push(core.failureCauses(o.events));
    else if (o.op === "children") out.push(core.runChildren(o.events, o.now));
    else if (o.op === "issues") out.push(core.issueProgress(o.events, o.total, o.held));
    else if (o.op === "issuecells") out.push(core.progressCells(core.issueProgress(o.events, o.total)));
    else if (o.op === "issuenumber") out.push(core.issueNumber(o.prompt));
    else if (o.op === "pause") out.push(core.pauseView(o.events));
    else if (o.op === "controlbody") out.push(core.controlBody(o.action, o.nodes));
    else if (o.op === "state") out.push(core.nodeState(o.ns));
    else if (o.op === "stateclass") out.push(core.stateClass(o.ns));
    else if (o.op === "childclass") out.push(core.childStateClass(o.c));
    else if (o.op === "harness") out.push(core.nodeHarness(o.doc, o.events, o.name));
    else if (o.op === "label") out.push(core.nodeLabel(o.node));
    else if (o.op === "clayout") out.push(core.childLayout(o.children, o.layout, o.box, o.gap, o.doc));
    else if (o.op === "splitkids") out.push(core.splitChildren(o.children, o.expanded));
    else if (o.op === "plan") out.push(core.childPlan(o.children, o.expanded, o.layout, o.box, o.gap, o.doc));
    else if (o.op === "toggle") out.push(core.toggleKey(o.expanded, o.key));
    else if (o.op === "double") out.push(core.isDoublePress(o.last, o.name, o.t));
    else if (o.op === "mark") out.push(core.stateMark(o.state));
    else if (o.op === "reveal") out.push(core.viewReveal(o.view, o.rect, o.size, o.insets));
    else if (o.op === "childmark") out.push(core.childMark(o.c));
    else if (o.op === "roster") out.push(core.childRoster(o.children, o.dispatch));
    else if (o.op === "grouplayout") out.push(core.groupLayout(o.groups, o.layout, o.taken, o.box, o.gap, o.doc));
    else if (o.op === "bus") out.push(core.childBus(o.anchor, o.pos, o.box));
    else if (o.op === "live") out.push(core.liveState(o.events, o.now));
    else if (o.op === "replay") out.push(core.replayAt(o.events, o.cursor));
    else if (o.op === "move") out.push(core.moveCursor(o.events, o.cursor, o.move, o.to));
    else if (o.op === "unresolved") out.push(core.unresolvedChildren(o.events, o.cursor));
    else if (o.op === "delay") out.push(core.readDelay(o.events));
    else if (o.op === "parse_live") out.push(core.parseLive(o.text));
    else if (o.op === "fmt") out.push(core.fmt[o.kind](o.value));
    else if (o.op === "diff") out.push(core.diffGraphs(o.prior, o.cur));
    else if (o.op === "caps") out.push(core.declaredCaps(o.doc));
    else if (o.op === "template") out.push(core.commandTemplate(o.args));
    else if (o.op === "sheet") out.push(core.reviewSheet(o.args));
    else if (o.op === "legend") out.push({ legend: core.LEGEND, chips: core.LEGEND_CHIPS, states: core.RUN_STATES, state_text: core.STATE_TEXT, help: core.HELP });
    else if (o.op === "note") out.push(core.REVIEW_NOTE);
    else if (o.op === "chip") out.push(await core.hashChip(o.text));
    else if (o.op === "savetext") out.push(core.saveText(o.text));
    else if (o.op === "draft") out.push(core.draftRecord(o.args));
    else if (o.op === "offer") out.push(core.draftOffer(o.draft, o.name, o.text));
    else if (o.op === "layoutsave") out.push(core.layoutForSave(o.layout, o.names));
    else if (o.op === "gate") out.push(core.saveGate(o.args));
    else if (o.op === "mode") out.push(core.saveMode(o.args));
    else if (o.op === "hashrel") out.push(core.hashRelation(o.record, o.chip));
    else if (o.op === "layout") out.push(core.autoLayout(o.doc));
    else if (o.op === "history") out.push(runHistory(core, o));
    else if (o.op === "httpsource") {
      const files = o.files;
      const src = core.httpSource(async (p) => { if (p in files && files[p] === "throw") throw new Error("net"); return p in files ? { ok: files[p] !== null, text: async () => files[p] } : { ok: false, text: async () => "" }; }, o.name);
      out.push({ name: src.name, live: src.live, graph: await src.graph(), record: await src.record() });
    }
    else if (o.op === "httpstream") {
      const opened = [];
      const src = core.httpSource(async () => ({ ok: false, text: async () => "" }), "x", o.stream ? (path, h) => { opened.push(path); return { close() {} }; } : undefined);
      if (src.stream) src.stream({});
      out.push({ hasStream: typeof src.stream === "function", opened });
    }
    else if (o.op === "push") {
      // A fake stream: the steps are the lines, ends and errors the server would send.
      const log = { changed: [], ended: 0, fail: [], status: [], closed: 0 };
      let h = null;
      const src = { stream: (hh) => { if (o.throws) throw new Error("no stream"); h = hh; return { close() { log.closed++; } }; } };
      core.followPush(src, o.events, { changed: (l) => log.changed.push(l.map((e) => e.pos)), ended: () => log.ended++, fail: (w) => log.fail.push(w), status: (t) => log.status.push(t) });
      for (const st of o.steps) { if (st.line !== undefined) h.line(typeof st.line === "string" ? st.line : JSON.stringify(st.line)); else if (st.end !== undefined) h.end(st.end); else if (st.error !== undefined) h.error(st.error); }
      out.push(log);
    }
    else throw new Error("unknown op " + o.op);
  }
  process.stdout.write(JSON.stringify(out));
});
