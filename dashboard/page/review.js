// --- review panel (ticket #84, decision #64, ADR-0117): it shows what the graph declares and prepares a command; it starts nothing ---
const R = { open: false, prior: null, opts: { adapter: "sdk", script: "", stopAfter: "", spendCap: "", deadline: "", maxParallel: "", tierPin: "" } };
const divs = (arr) => arr.map((x) => `<div>${x}</div>`).join("");
const dText = (v) => (v === null ? "(absent)" : esc(v));
function diffHtml(d) {
  if (d.empty) return `<div class="msg-ok">no difference</div>`;
  const fl = (f) => `<div class="ro">${esc(f.key)}: ${dText(f.before)} &rarr; ${dText(f.after)}</div>`;
  const part = (title, x) => (x.added.length || x.removed.length || x.changed.length ? `<div><b>${title}</b>${divs([...x.added.map((n) => "+ " + esc(n)), ...x.removed.map((n) => "&minus; " + esc(n))])}${x.changed.map((c) => `<div>~ ${esc(c.name)}</div>${c.fields.map(fl).join("")}`).join("")}</div>` : "");
  return `${d.top.length ? `<div><b>graph</b>${d.top.map(fl).join("")}</div>` : ""}${d.run.length ? `<div><b>run block</b>${d.run.map(fl).join("")}</div>` : ""}${part("nodes", d.nodes)}${part("facts", d.facts)}${part("gates", d.gates)}`;
}
function reviewTemplate() { return core.commandTemplate({ file: S.fileName || S.name + ".json", hash: S.chip ? S.chip.full : null, opts: R.opts, doc: S.doc }); }
function refreshTemplate() {
  const t = reviewTemplate();
  $("r-cmd").value = t.text || "";
  $("r-cmd-err").innerHTML = t.errors.map((e) => `<li>${esc(e)}</li>`).join("");
  $("b-copy-cmd").disabled = !t.text;
}
function renderReview() {
  const el = $("review");
  el.hidden = !(R.open && S.doc);
  $("b-review").setAttribute("aria-expanded", String(R.open));
  if (el.hidden) return;
  if (el.contains(document.activeElement) && document.activeElement.tagName !== "BUTTON") return;
  const caps = core.declaredCaps(S.doc), nodes = nodesOf();
  const unsaved = core.saveText(S.text) !== S.baseText;
  const priorDiff = R.prior && R.prior.doc ? core.diffGraphs(R.prior.doc, S.doc) : null;
  const o = R.opts, opt = (id, label, v, extra) => `<label for="${id}">${label}</label><input id="${id}" value="${esc(v)}" ${extra || ""} spellcheck="false">`;
  el.innerHTML = `<h2>Review</h2>
<div class="ro">hash ${S.chip ? esc(S.chip.full) : "(none: no canonical form)"}<br>${nodes.length} nodes · ${S.errors.length} error${S.errors.length === 1 ? "" : "s"}</div>
${unsaved ? `<div class="msg-bad">Unsaved edits. The hash is the hash of the graph on screen: save the graph, then run the command on that file.</div>` : ""}
<h3>Errors (never block a save)</h3>
${S.errors.length ? `<ul class="errs">${S.errors.map((e) => `<li>${e.node ? `<button class="lnk" data-node="${esc(e.node)}">${esc(e.node)}</button>: ` : ""}${esc(e.message)}</li>`).join("")}</ul>` : `<div class="msg-ok">no error</div>`}
<h3>Declared caps (as written, none added up)</h3>
<div class="ro">run: ${caps.run === null ? "(none)" : esc(caps.run)}${caps.nodes.map((r) => `<br>${esc(r.name)} (${esc(r.kind)}): ${Object.entries(r).filter(([k]) => k !== "name" && k !== "kind").map(([k, v]) => esc(k + " " + v)).join(", ")}`).join("")}</div>
<h3>Gates (read-only)</h3>
<div class="ro">${caps.gates.length ? caps.gates.map((g) => `${esc(g.name)}: check ${esc(g.check)}, params ${esc(g.params)}`).join("<br>") : "(no gate)"}</div>
<h3>Compare with a prior graph</h3>
<div class="row"><label class="btn">Open prior graph<input id="f-prior" type="file" accept=".json,application/json"></label></div>
${R.prior ? (R.prior.error ? `<div class="msg-bad">${esc(R.prior.error)}</div>` : `<div class="note">prior ${esc(R.prior.file)} · hash ${esc(R.prior.hash || "none")}<br>current ${esc(S.fileName || S.name)} · hash ${S.chip ? esc(S.chip.full) : "none"}</div>${priorDiff ? diffHtml(priorDiff) : ""}`) : `<div class="note">Open an earlier version of this graph to see what changed, by node name. Layout never counts.</div>`}
<h3>Command template (text only)</h3>
<div class="opts">
<label for="r-adapter">adapter</label><span id="r-adapter"><label><input type="radio" name="r-adapter" value="sdk" ${o.adapter === "sdk" ? "checked" : ""}> sdk</label> <label><input type="radio" name="r-adapter" value="script" ${o.adapter === "script" ? "checked" : ""}> script</label></span>
${opt("r-script", "script file", o.script, 'placeholder="used with script"')}
<label for="r-stop">stop after</label><select id="r-stop"><option value=""></option>${nodes.map((n) => `<option ${n.name === o.stopAfter ? "selected" : ""}>${esc(n.name)}</option>`).join("")}</select>
${opt("r-spend", "spend cap (USD)", o.spendCap, 'inputmode="decimal"')}
${opt("r-deadline", "deadline (s)", o.deadline, 'inputmode="decimal"')}
${opt("r-par", "max parallel", o.maxParallel, 'inputmode="numeric"')}
${opt("r-tier", "tier pin", o.tierPin)}
</div>
<ul class="errs" id="r-cmd-err"></ul>
<textarea id="r-cmd" readonly spellcheck="false" aria-label="Command template"></textarea>
<div class="row"><button id="b-copy-cmd">Copy command</button><button id="b-sheet">Export review sheet</button></div>
<div class="note">${esc(core.REVIEW_NOTE)} An option can only lower the run block.</div>`;
  refreshTemplate();
}
$("b-review").addEventListener("click", () => { R.open = !R.open; if (R.open) openSide(); renderReview(); });
$("review").addEventListener("input", (e) => {
  const m = { "r-script": "script", "r-spend": "spendCap", "r-deadline": "deadline", "r-par": "maxParallel", "r-tier": "tierPin" }[e.target.id];
  if (m) { R.opts[m] = e.target.value.trim(); refreshTemplate(); }
});
$("review").addEventListener("change", async (e) => {
  const t = e.target;
  if (t.name === "r-adapter") { R.opts.adapter = t.value; refreshTemplate(); }
  else if (t.id === "r-stop") { R.opts.stopAfter = t.value; refreshTemplate(); }
  else if (t.id === "f-prior") {
    const f = t.files[0];
    if (f) {
      const text = await readFile(f), r = core.parseImport(text);
      if (r.error) R.prior = { file: f.name, error: r.error };
      else { let h = null; try { h = await core.graphHash(text); } catch (err) { h = null; } R.prior = { file: f.name, doc: r.doc, hash: h }; }
    }
    t.value = ""; renderReview();
  }
});
$("review").addEventListener("click", async (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  if (b.dataset.node) { S.selected = b.dataset.node; S.link = null; render(); }
  else if (b.id === "b-copy-cmd") {
    const t = reviewTemplate().text;
    if (!t) return;
    try { await navigator.clipboard.writeText(t); setStatus("Copied the command. Run it yourself in a terminal; this page never starts a run."); } catch (err) { window.prompt("Command template", t); }
  } else if (b.id === "b-sheet") {
    const t = reviewTemplate();
    const prior = R.prior && R.prior.doc ? R.prior : null;
    const text = core.reviewSheet({ file: S.fileName || S.name + ".json", hash: S.chip ? S.chip.full : null, nodeCount: nodesOf().length, errors: S.errors.map((x) => (x.node ? x.node + ": " : "") + x.message), caps: core.declaredCaps(S.doc), diff: prior ? core.diffGraphs(prior.doc, S.doc) : null, prior: prior ? { file: prior.file, hash: prior.hash } : null, template: t.text });
    download(S.name + ".review.txt", text, "text/plain");
    setStatus("Exported the review sheet: text for a ticket or a commit message. It records no approval.");
  }
});

