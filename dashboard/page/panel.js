function fmt(v) { return v === null || v === undefined ? "unavailable" : typeof v === "object" ? JSON.stringify(v) : String(v); }

function renderOverlayRow(r) {
  const entry = (label, e) => `${label} entry: ${e ? `#${e.entry} ${esc(e.outcome)}, spend ${fmt(e.spend)}, turns ${fmt(e.turns)}, elapsed ${fmt(e.elapsed)}${e.reasons && e.reasons.length ? ", reasons " + esc(JSON.stringify(e.reasons)) : ""}` : "none"}`;
  const facts = (r.facts || []).map((f) => `${esc(f.name || JSON.stringify(f))}${f.value !== undefined ? "=" + esc(JSON.stringify(f.value)) : ""}${f.instrument ? ` (instrument: ${esc(f.instrument)}${f.source ? ", source: " + esc(f.source) : ""}${f.timestamp ? ", at " + esc(f.timestamp) : ""})` : ""}`);
  return `<div class="ro"><b>${esc(r.proposal_id)}</b>: ${esc(r.kind)} on ${esc(r.target)}: ${esc(JSON.stringify(r.old))} → ${esc(JSON.stringify(r.requested))}
<br>range checked: ${esc(JSON.stringify(r.range))}
<br>cited facts: ${facts.length ? facts.join("; ") : "none"}
<br>${entry("previous", r.previous)}<br>${entry("next", r.next)}
<br>delta: ${Object.entries(r.delta).map(([k, v]) => `${k} ${esc(fmt(v))}`).join(", ")}
<br><span class="note">instrument: ${esc(r.instrument)}<br>${esc(r.note)}</span></div>`;
}

// One failure, as the record has it: the code, what it names, and the tail of the output when the engine kept it.
function reasonHtml(r) {
  const ev = r.evidence === null ? `<div class="note">The record keeps no output for this failure.</div>` : `<pre class="ev">${esc(r.evidence)}</pre>`;
  return `<div class="cause"><b class="bad" title="${esc(r.detail || r.raw)}">${esc(r.code)}</b>${r.gate ? ` <span class="note">(${esc(r.gate)})</span>` : ""}${r.subject ? ` \u00b7 ${esc(r.subject)}` : ""}${ev}</div>`;
}
function failuresHtml(causes) {
  if (!causes || !causes.length) return "";
  const kind = { node: "", land: " landing", rejected: " request" };
  return `<h2>Failures</h2><div class="runbox">${causes.map((c) => `<div><b>${esc(c.node)}</b>${esc(kind[c.kind] || "")} \u00b7 ${esc(c.outcome)}${c.reasons.map(reasonHtml).join("")}</div>`).join("")}</div>`;
}
function childHtml(c) {
  const f = core.fmt;
  const rows = c.rejected ? [`request rejected by code, so no child started`] : [
    `outcome <b>${esc(c.outcome || "none yet")}</b>${c.land ? ` \u00b7 landing <b>${esc(c.land.outcome)}</b>${c.land.head ? " " + esc(c.land.head.slice(0, 10)) : ""}` : ""}`,
    `model <b>${esc(f.model(c.model))}</b> \u00b7 effort <b>${esc(c.effort === null ? f.count(null) : c.effort)}</b> \u00b7 workspace ${esc(c.workspace === null ? f.count(null) : c.workspace)}`,
    `time <b>${f.seconds(c.elapsed)}</b> \u00b7 cost <b>${f.usd(c.spend)}</b> \u00b7 turns <b>${f.count(c.turns)}</b>`,
    `tokens <b>${f.tokens(c.tokens_in)}</b> in \u00b7 <b>${f.tokens(c.tokens_out)}</b> out \u00b7 <b>${f.rate(c.tps)}</b>`,
    `checks ${c.checks ? Object.entries(c.checks).map(([g, v]) => `${esc(g)} <b>${esc(v)}</b>`).join(", ") || "none" : f.count(null)}`,
    c.after.length ? `runs after request${c.after.length > 1 ? "s" : ""} ${esc(c.after.join(", "))}` : "",
  ].filter(Boolean);
  const evid = c.evidence || {};
  const why = c.outcome === "fail" ? `<h2>Why it failed</h2><div class="runbox">${c.reasons.map((r) => reasonHtml({ ...core.reasonParts(r), evidence: evid[r] !== undefined ? evid[r] : null })).join("")}</div>` : "";
  return `<h2>Child ${esc(c.name)} <span class="note">(of ${esc(c.dispatch)}${c.proposer ? ", asked by " + esc(c.proposer) : ""})</span></h2><div class="runbox">${rows.join("<br>")}</div>${why}`;
}
// The roster of one dispatch's children: one row each. A click on a row selects that child.
function rosterHtml(dispatch) {
  const rows = core.childRoster(S.children, dispatch);
  if (!rows.length) return "";
  const body = rows.map((r) => `<tr data-child="${esc(r.name)}" class="s-${esc(r.state)}${S.selected === r.name ? " sel" : ""}" title="${esc(r.name)}"><td>${r.index}</td><td>${esc(r.label)}</td><td class="m" title="${esc(r.outcome)}${r.rework ? " · " + esc(r.rework) : ""}">${r.glyph}${r.rework ? " ↻" : ""}</td><td>${esc(core.fmt.model(r.model))}</td><td>${esc(r.time)}</td><td>${esc(r.cost)}</td><td>${esc(r.turns)}</td></tr>`).join("");
  return `<h2>Children of ${esc(dispatch)} <span class="note">(${rows.length})</span></h2><table class="roster"><thead><tr><th>#</th><th>name</th><th title="outcome">out</th><th>model</th><th>time</th><th>cost</th><th>turns</th></tr></thead><tbody>${body}</tbody></table>`;
}
function groupHtml(gr) {
  return `<h2>Group ${esc(gr.name)}</h2><div class="runbox">${gr.harness ? esc(core.fmt.model(gr.harness)) + "<br>" : ""}${esc(gr.line)}<br>cost <b>${core.fmt.usd(gr.cost)}</b><br><span class="note">Time, cost and tokens are sums over the children (children run side by side, so the time can exceed the run's). Double-click the card, or press Space on it, to show one card per child.</span></div>`;
}

// What the record says of one graph node: how it ended, and why when it failed.
function nodeRunHtml(name) {
  const ns = S.stats && S.stats.nodes ? S.stats.nodes[name] : null;
  if (!ns) return "";
  if (ns.not_run) return `<h2>Run result</h2><div class="runbox">not run: the record has no call for this node, and no dispatch checked it.</div>`;
  if (ns.via) return `<h2>Run result</h2><div class="runbox">checked ${ns.via.of} child${ns.via.of > 1 ? "ren" : ""} of <b>${esc(ns.via.dispatch)}</b>: <b>${ns.via.pass}</b> passed, <b>${ns.via.fail}</b> failed. A child that fails a check fails; see the child cards.</div>`;
  if (!ns.cause) return "";
  const ev = ns.evidence || {};
  return `<h2>Why it failed</h2><div class="runbox">${ns.reasons.map((r) => reasonHtml({ ...core.reasonParts(r), evidence: ev[r] !== undefined ? ev[r] : null })).join("")}</div>`;
}

// The available-vs-used harness of the selected node: derived from the record and the graph, never authored.
// A dispatch child's available harness is its resolved `definition`; its ceiling is the dispatch envelope's `allow` set
//. A plain agent declares its own. A gate or a loop carries none, said plainly, never an empty list.
const HARNESS_NONE = {
  gate: "A gate runs a deterministic check and carries no harness of its own.",
  loop: "A loop repeats its children and carries no harness of its own.",
  each: "An each fans its children out and carries no harness of its own.",
  action: "An action runs deterministic code and carries no harness of its own.",
  wait: "A wait pauses for a person and carries no harness of its own.",
  dispatch: "A dispatch carries no harness of its own: its children inherit its envelope. Select a child to see its available vs used.",
};
function harnessHtml(h) {
  if (!h) return "";
  const f = core.fmt;
  if (h.kind === "none") return `<h2>Available vs used</h2><div class="ro">${esc(HARNESS_NONE[h.nodeKind] || "This node carries no harness of its own.")}</div>`;
  const listText = (arr) => (arr === null ? f.count(null) : arr.length ? arr.join(", ") : "none");
  const capText = (v) => (v === null ? f.count(null) : typeof v === "object" ? `${v.min}–${v.max}` : String(v));
  const row = (label, a) => {
    const line = [
      a.skill ? `skill <b>${esc(a.skill)}</b>` : "",
      `model <b>${esc(f.model(a.model))}</b>`,
      `effort <b>${esc(a.effort === null ? f.count(null) : a.effort)}</b>`,
      `tools ${esc(listText(a.tools))}`,
      `stall cap ${esc(capText(a.stalls))}`,
      `spend cap ${esc(capText(a.spend))}`,
      `deadline ${esc(f.seconds(a.deadline))}`,
    ].filter(Boolean).join(" · ");
    return `<div class="ro"><b>${label}</b> · ${line}</div>`;
  };
  const parts = [row("Available", h.available)];
  if (h.allow) {
    const a = h.allow;
    const line = [
      `allowed models ${esc(listText(a.models === null ? null : a.models.map((m) => f.model(m))))}`,
      `allowed efforts ${esc(listText(a.efforts))}`,
      `allowed tools ${esc(listText(a.tools))}`,
      `allowed skills ${esc(listText(a.skills))}`,
      `stall cap ${esc(capText(a.stalls))}`,
      `spend cap ${esc(capText(a.spend))}`,
      `deadline ${esc(f.seconds(a.deadline))}`,
    ].join(" · ");
    parts.push(`<div class="ro note">Inherited from the <b>${esc(h.dispatch)}</b> envelope · ${line}</div>`);
  }
  const u = h.used;
  const usedLine = [
    u.skill ? `skill <b>${esc(u.skill)}</b>` : "",
    `model <b>${esc(f.model(u.model))}</b>`,
    `effort <b>${esc(u.effort === null ? f.count(null) : u.effort)}</b>`,
    `turns <b>${f.count(u.turns)}</b>`,
    `tokens ${f.tokens(u.tokens_in)} in · ${f.tokens(u.tokens_out)} out`,
    `spend <b>${f.usd(u.spend)}</b>`,
    `elapsed <b>${f.seconds(u.elapsed)}</b>`,
  ].filter(Boolean).join(" · ");
  parts.push(`<div class="ro"><b>Used</b> · ${usedLine}</div>`);
  return `<h2>Available vs used</h2>${parts.join("")}`;
}

// --- pause and resume: the one write this page makes, to the run server's control route ---
// The page asks; the engine decides at its next node entry. It never starts a run: a paused run is continued by `squad resume`.
const CONTROL = { state: null };   // the server's control.json: what was asked, not what the engine did
async function loadControl() {
  if (!FROM_SERVER) return;
  try { const r = await fetch("run/control.json", { cache: "no-store" }); if (r.ok) CONTROL.state = await r.json(); } catch (e) { /* the server went away: the next request says so */ }
}
async function sendControl(action, nodes) {
  try {
    const r = await fetch("run/control", { method: "POST", headers: { "Content-Type": "application/json" }, body: core.controlBody(action, nodes) });
    if (!r.ok) { setStatus(r.status === 409 ? "The run has ended: there is nothing to pause or resume." : `The server refused the request (${r.status}).`, true); return; }
    await loadControl();
    setStatus(`${action === "pause" ? "Pause" : "Resume"} requested for ${nodes && nodes.length ? nodes.join(", ") : "the run"}. The run acts on it at its next node entry.`);
  } catch (e) { setStatus("The server did not answer: " + e.message, true); }
  render();
}
// The tasks the selection names: one node or child, or every child of a selected group. Empty when nothing is selected.
function controlTargets() {
  const gr = S.groups.find((x) => x.key === S.selected);
  if (gr) return { names: gr.kids.map((k) => k.name), label: `the ${gr.kids.length} children of ${gr.name}` };
  return S.selected ? { names: [S.selected], label: S.selected } : { names: [], label: "" };
}
function pauseBoxHtml() {
  if (!FROM_SERVER || !S.events) return "";
  const pv = core.pauseView(S.events);
  if (pv.state === "ended") return "";
  const c = CONTROL.state, asked = c && (c.whole || c.nodes.length) ? `<br>asked: ${c.whole ? "pause the whole run" : ""}${c.whole && c.nodes.length ? "; " : ""}${c.nodes.length ? "pause " + esc(c.nodes.join(", ")) : ""}${c.released.length ? "; release " + esc(c.released.join(", ")) : ""}` : "";
  const t = controlTargets(), act = pv.state === "paused" ? "resume" : "pause", word = act === "pause" ? "Pause" : "Resume";
  const btn = (names, text) => `<button data-ctl="${act}" data-nodes="${esc(JSON.stringify(names))}" title="${word} ${esc(text)}. The run acts on it at its next node entry.">${word} ${esc(text)}</button>`;
  const where = pv.state === "paused"
    ? `<b>Paused</b> at <b>${esc(pv.node)}</b>. Held: ${esc(pv.held.join(", "))}. Done: ${pv.completed.length ? esc(pv.completed.join(", ")) : "nothing yet"}.<br><span class="note">The page never starts a run. After the resume request, type <code>${esc(pv.resume)}</code> in a terminal.</span>`
    : `<span class="note">A pause holds a task before it starts. A call that is running finishes first.</span>`;
  return `<h2>Pause and resume</h2><div class="runbox">${where}${asked}<div class="row">${btn([], "the run")}${t.names.length ? " " + btn(t.names, t.label) : ""}</div></div>`;
}
function wireControl(side) {
  side.querySelectorAll("button[data-ctl]").forEach((b) => b.addEventListener("click", () => sendControl(b.dataset.ctl, JSON.parse(b.dataset.nodes))));
}

function renderRunPanel() {
  const st = S.stats, f = core.fmt, v = st.verdict;
  const cls = { completed: "state-completed", running: "state-running", stalled: "state-stalled", "no verdict yet": "state-none" }[st.state] || "msg-bad";
  const rp = S.replay, past = rp && !rp.at_end;
  const state = past ? `<span class="state-none">replay at event ${rp.cursor} of ${rp.last}</span> <span class="note">(${esc(st.state)})</span>` : `<span class="${cls}">${esc(st.state)}</span>`;
  const unres = rp && rp.unresolved.length ? `<br>unresolved children: ${rp.unresolved.map((c) => `<b>${esc(c.child)}</b> <span class="note">(${esc(c.parent === null ? "?" : c.parent)}, accepted at ${c.pos})</span>`).join(", ")}` : "";
  const lv = st.live, flight = lv ? lv.in_progress : [];
  const liveLines = !lv || v ? "" : `<br>last event <b>${f.seconds(lv.since_last)}</b> ago${flight.length ? "<br>in progress: " + flight.map((c) => `<b>${esc(c.node)}</b> ${f.seconds(c.since)}`).join(", ") : ""}${st.state === "stalled" ? `<br><span class="note">No new event for over ${f.seconds(lv.stall_after)}. A quiet gate command can look like this.</span>` : ""}`;
  const head = `run ${esc(st.run_id)} \u00b7 graph ${esc(st.graph_name)}<br>state: ${state}${v ? ` (exit ${esc(v.exit_code)}${v.reasons.length ? ", " + esc(core.causeSummary(v.reasons)) + " (see Failures)" : ""})` : ""}${liveLines}${unres}`;
  if (!st.totals) {
    const rows = st.timeline.map((t) => `<tr><td>${f.seconds(t.at)}</td><td>${esc(t.event)}</td><td>${esc(t.node === null ? "" : t.node)}</td><td>${esc(t.outcome === null ? "" : t.outcome)}</td></tr>`).join("");
    return `<h2>Run</h2><div class="runbox">${head}<br><span class="note">Timeline and verdict only: ${S.doc ? "the record's graph hash differs from the loaded graph's" : "no graph is loaded"}.</span></div><h2>Timeline</h2><table class="tl">${rows}</table>`;
  }
  const t = st.totals, e = st.engine;
  const agents = nodesOf().filter((n) => st.nodes[n.name] && st.nodes[n.name].harness).map((n) => {
    const h = st.nodes[n.name].harness, c = h.configured, u = h.used;
    const model = u.model || c.model, effort = u.effort || c.effort;
    return `<b>${esc(n.name)}</b>: ${esc(f.model(model))} \u00b7 ${esc(effort === null ? f.count(null) : effort)} \u00b7 tools ${esc(c.tools === null ? f.count(null) : c.tools.join(", "))}${u.model ? "" : ` <span class="note">(graph setting; the record has no model)</span>`}`;
  });
  const skillsLine = e.skills === null ? "unrecorded" : e.skills.length ? e.skills.map((k) => `<span title="${esc(k.name)} ${esc(k.hash)}">${esc(k.name)} ${esc(String(k.hash).slice(0, 8))}</span>`).join(", ") : "none";
  const commit = e.skills_commit === null ? "unrecorded" : `<span title="${esc(e.skills_commit)}">${esc(String(e.skills_commit).slice(0, 8))}</span>`;
  const list = (x) => x === null ? f.count(null) : esc(x.join(", "));
  return `<h2>Run</h2><div class="runbox">${head}<br>time <b>${f.seconds(t.wall)}</b> \u00b7 cost <b>${f.usd(t.spend)}</b> of ${t.cap === null ? f.count(null) : "$" + t.cap} (${f.pct(t.cap_pct)}) \u00b7 turns <b>${f.count(t.turns)}</b><br>tokens <b>${f.tokens(t.tokens_in)}</b> in \u00b7 <b>${f.tokens(t.tokens_out)}</b> out \u00b7 <b>${f.rate(t.tps)}</b></div>${failuresHtml(st.causes)}
<h2>Harness</h2><div class="ro">${agents.length ? agents.join("<br>") : "no agent node ran"}<br><span class="note">Engine: permission ${esc(e.permission_mode === null ? f.count(null) : e.permission_mode)} \u00b7 sources ${list(e.setting_sources)} \u00b7 blocked tools ${list(e.disallowed_tools)} \u00b7 max output ${f.count(e.max_output_tokens)} \u00b7 context ${f.count(e.context_tokens)} \u00b7 auth ${esc(e.auth === null ? f.count(null) : e.auth)}<br>Skills: ${skillsLine} \u00b7 vendor commit ${commit}</span></div>`;
}

function renderSide() {
  const side = $("side");
  if (!S.doc) { side.innerHTML = `<h2>Start</h2><p>Open a graph document, or load the five-phase example.</p><p class="note">The graph file stays as authored. A run record is read-only. Layout lives in a sibling file and never moves the graph hash.</p>`; return; }
  const parts = [];
  if (inEdit() || S.errors.length) parts.push(`<h2>Graph</h2><div>${esc(S.doc.name || "(no name)")} · ${nodesOf().length} nodes · ${S.errors.length ? `<span class="msg-bad">${S.errors.length} error${S.errors.length > 1 ? "s" : ""}</span>` : `<span class="msg-ok">valid</span>`}</div>`);
  const top = S.errors.filter((e) => !e.node);
  if (top.length) parts.push(`<h2>Errors on the graph</h2><ul class="errs">${top.map((e) => `<li>${esc(e.message)}</li>`).join("")}</ul>`);
  if (S.warnings.length) parts.push(`<h2>Links the last change broke</h2><ul class="errs">${S.warnings.map((w) => `<li>${esc(w.message)}</li>`).join("")}</ul>`);
  if (S.selected) {
    const o = cardOrder(), i = o.indexOf(S.selected);
    if (o.length > 1 && i >= 0) parts.unshift(`<div class="pnav"><button data-step="-1" title="previous card (←)"${i > 0 ? "" : " disabled"}>‹</button><span class="note">${i + 1} / ${o.length}</span><button data-step="1" title="next card (→)"${i < o.length - 1 ? "" : " disabled"}>›</button></div>`);
    const n = nodesOf().find((x) => x.name === S.selected), ch = S.children.find((x) => x.name === S.selected);
    const gr = S.groups.find((x) => x.key === S.selected);
    if (gr) { parts.push(groupHtml(gr)); parts.push(rosterHtml(gr.dispatch)); }
    if (ch) { parts.push(childHtml(ch)); parts.push(rosterHtml(ch.dispatch)); }
    if (n) {
      parts.push(`<h2>Node ${esc(n.name)} (${esc(n.kind)})</h2>`);
      parts.push(nodeRunHtml(n.name));
      const errs = errsFor(n.name), ns = S.stats && S.stats.nodes ? S.stats.nodes[n.name] : null;
      // "no error on this node" speaks of the graph's rules; it is left out where the record shows the node failed, so the panel never says both.
      if (errs.length) parts.push(`<ul class="errs">${errs.map((e) => `<li>${esc(e.message)}</li>`).join("")}</ul>`);
      else if (!(ns && ns.cause !== null && ns.cause !== undefined)) parts.push(`<div class="msg-ok">no error on this node</div>`);
      const unseen = core.unseenValues(n, SERVED);
      if (unseen.length) parts.push(`<div class="note">Not in the served snapshot (allowed, never blocked): ${esc(unseen.join(", "))}.</div>`);
      if (n.kind === "gate") parts.push(`<h2>Read-only: check and thresholds</h2><div class="ro">check: <b>${esc(n.check)}</b><br>params: ${esc(JSON.stringify(n.params))}</div>`);
      if (inEdit()) {
      const gates = Array.isArray(S.doc.gates) ? S.doc.gates : [];
      const kinds = core.KINDS.filter((k) => k !== n.kind);
      parts.push(`<h2>Rename and kind</h2><div class="row"><input id="n-name" value="${esc(n.name)}" spellcheck="false" title="The new name replaces the old one in every reference"> <button id="b-rename">Rename</button></div>`
        + (n.kind === "gate"
          ? `<div class="note">A gate keeps its kind: its check id and thresholds are read-only. Remove it and add the node you want.</div>`
          : `<div class="row"><select id="n-kind" title="The new kind; every field stays and the rules judge the result">${kinds.map((k) => `<option>${k}</option>`).join("")}</select> <select id="n-check" title="Registered check for the gate" hidden>${gates.map((g) => `<option>${esc(g.id)}</option>`).join("")}</select> <button id="b-kind">Change kind</button></div>`)
        + `<div id="nk-msg"></div>`);
      parts.push(`<h2>Fields</h2>${core.fieldGroups(n).map((g) => fieldGroupHtml(n, g)).join("")}`);
      parts.push(`<details><summary>Edit the whole node as JSON</summary><textarea id="node-json" spellcheck="false">${esc(JSON.stringify(n, null, 2))}</textarea><div><button id="b-apply">Apply to node</button> <span id="apply-msg"></span></div></details>`);
      }
      const ov = overlayFor(n.name);
      if (ov.length) parts.push(`<h2>Run-only overlay</h2>${ov.map(renderOverlayRow).join("")}`);
    }
    if (S.replay && !gr && !(ch && ch.rejected)) parts.push(harnessHtml(core.nodeHarness(S.doc, S.replay.events, S.selected)));
  }
  if (S.stats) parts.push(renderRunPanel());
  parts.push(pauseBoxHtml());
  if (S.events) {
    const start = S.events[0];
    const counts = {};
    for (const e of S.events) counts[e.event] = (counts[e.event] || 0) + 1;
    const verdict = S.events.filter((e) => e.event === "verdict").pop();
    parts.push(`<details class="ref"><summary>Run record (read-only)</summary><div class="ro">run ${esc(start.run_id)} · graph ${esc(start.graph_name)} · ${S.events.length} events<br>${Object.entries(counts).map(([k, v]) => `${esc(k)} ${v}`).join(" · ")}<br>verdict: ${verdict ? esc(verdict.verdict) : "none yet"}</div></details>`);
    parts.push(S.overlay && !S.overlay.shown ? `<div class="msg-bad">${esc(S.overlay.reason)}</div>` : `<div class="note">The record's graph hash equals the loaded graph's: ${S.overlay && S.overlay.rows.length ? S.overlay.rows.length + " applied proposal" + (S.overlay.rows.length > 1 ? "s" : "") + " shown on the cards" : "no applied proposal in this run"}.</div>`);
  }
  if (inEdit()) parts.push(`<details class="ref"><summary>Served lists (suggestions only)</summary><div class="ro">${["tools", "skills", "models", "efforts"].map((k) => `<b>${k}</b> (${SERVED[k].length}): ${esc(SERVED[k].join(", "))}`).join("<br>")}<br><span class="note">Snapshot ${esc(SERVED.hash.slice(0, 12))} from ${esc(SERVED.sources.join(", "))}. A snapshot is stale once those sources change: rebuild with scripts/build_dashboard.py. An unknown value is never blocked. A model id reads here as the field takes it, with its <code>claude-</code> prefix; every model the page presents elsewhere hides that prefix.</span></div></details>`);
  if (inEdit()) parts.push(`<details class="ref"><summary>Read-only registries</summary><div class="ro"><b>facts</b> (${S.doc.facts ? S.doc.facts.length : 0}): ${esc((S.doc.facts || []).map((f) => f.name).join(", "))}<br><b>gates</b> (${S.doc.gates ? S.doc.gates.length : 0}): ${esc((S.doc.gates || []).map((g) => g.id + "(" + (g.params || []).join(",") + ")").join(", "))}<br><span class="note">The dashboard picks a registered name and never adds one.</span></div></details>`);
  side.innerHTML = parts.join("");
  const apply = $("b-apply");
  if (apply) apply.addEventListener("click", applyEdit);
  side.querySelectorAll("tr[data-child]").forEach((tr) => tr.addEventListener("click", () => { S.selected = tr.dataset.child; S.link = null; render(); revealSelected(); }));
  wireFields();
  wireControl(side);
  if ($("b-rename")) {
    $("b-rename").addEventListener("click", renameSelected);
    $("n-name").addEventListener("keydown", (e) => { if (e.key === "Enter") renameSelected(); });
  }
  if ($("b-kind")) {
    $("b-kind").addEventListener("click", changeSelectedKind);
    const showCheck = () => { $("n-check").hidden = $("n-kind").value !== "gate"; };
    $("n-kind").addEventListener("change", showCheck);
    showCheck();
  }
}

