// Dashboard core module: save gate, draft copy, hash chip, diff, declared caps, command template, review sheet. A factory: it takes what it uses from the modules below it, and returns its functions. No DOM here.
// scripts/build_dashboard.py bundles every file of dashboard/core/ in dependency order; nothing loads one file alone.

function reviewModule({ canonText, graphHash, cmpCodePoints }) {
  // Save, draft copy, and the changed-file stop. Pure, so node can test them.
  const saveText = (text) => (text.endsWith("\n") ? text : text + "\n");
  async function hashChip(text) { const full = await graphHash(saveText(text)); return { full, short: full.slice(0, 12) }; }
  const hashRelation = (record, chip) => (record ? (record === chip ? "matches" : "does not match") : null);
  // A draft is a recovery copy in browser storage. It is never a graph key and never written to the graph or layout file.
  const draftRecord = ({ name, text, layout, time, baseHash }) => ({ name, text, layout, time, baseHash });
  const draftOffer = (draft, name, text) => (draft && draft.name === name && draft.text !== text ? draft : null);

  // A save stops for a file conflict only. The number of validation errors never enters the decision.
  function saveGate({ mode, stored, current }) {
    if (mode !== "in-place" || !stored || !current) return "write";
    return stored.lastModified !== current.lastModified || stored.text !== current.text ? "stop" : "write";
  }
  function saveMode({ picker, handle }) { const inPlace = !!(picker && handle); return { mode: inPlace ? "in-place" : "download", label: inPlace ? "In-place save" : "Download save" }; }

  // --- review: plain text for a person. These functions start no process, write no file,
  // add no approval and compute no sum of caps. The command template is text that a person pastes into a terminal.
  const REVIEW_NOTE = "A shown cap and a matching hash approve nothing and authorize no live write. Approval is a person's act outside the graph. Launching stays a person's act in a terminal.";
  const named = (list, key) => { const m = new Map(); if (Array.isArray(list)) for (const x of list) if (x && typeof x === "object" && typeof x[key] === "string") m.set(x[key], x); return m; };
  const present = (v) => (v === undefined ? null : canonText(v));
  // The fields of two objects that differ: {key, before, after} in canonical text, null where a field is absent.
  function fieldDiff(a, b) {
    const x = a && typeof a === "object" ? a : {}, y = b && typeof b === "object" ? b : {};
    return [...new Set([...Object.keys(x), ...Object.keys(y)])].sort(cmpCodePoints).filter((k) => present(x[k]) !== present(y[k])).map((k) => ({ key: k, before: present(x[k]), after: present(y[k]) }));
  }
  function listDiff(prior, cur, key) {
    const p = named(prior, key), c = named(cur, key), added = [], removed = [], changed = [];
    for (const [name, v] of c) { if (!p.has(name)) added.push(name); else { const f = fieldDiff(p.get(name), v); if (f.length) changed.push({ name, fields: f }); } }
    for (const name of p.keys()) if (!c.has(name)) removed.push(name);
    return { added, removed, changed };
  }
  // Compare two graph documents by node name. Layout is not in a document, so it never appears.
  function diffGraphs(prior, cur) {
    const top = fieldDiff({ name: prior.name, description: prior.description, root: prior.root, schema_version: prior.schema_version }, { name: cur.name, description: cur.description, root: cur.root, schema_version: cur.schema_version });
    const out = { top, run: fieldDiff(prior.run, cur.run), nodes: listDiff(prior.nodes, cur.nodes, "name"), facts: listDiff(prior.facts, cur.facts, "name"), gates: listDiff(prior.gates, cur.gates, "id") };
    const parts = [out.top, out.run, ...["nodes", "facts", "gates"].flatMap((k) => [out[k].added, out[k].removed, out[k].changed])];
    out.empty = parts.every((x) => x.length === 0);
    return out;
  }
  // The caps the document declares, one row per node, as written. Nothing is added up: the engine enforces the run cap before each entry.
  function declaredCaps(doc) {
    const nodes = Array.isArray(doc.nodes) ? doc.nodes.filter((n) => n && typeof n.name === "string") : [];
    const rows = [], gates = [];
    for (const n of nodes) {
      if (n.kind === "gate") { gates.push({ name: n.name, check: present(n.check), params: present(n.params) }); continue; }
      const row = { name: n.name, kind: n.kind };
      for (const k of ["deadline", "budget", "max_depth", "max_children", "max_parallel"]) if (n[k] !== undefined) row[k] = canonText(n[k]);
      if (Object.keys(row).length > 2) rows.push(row);
    }
    return { run: present(doc.run), nodes: rows, gates };
  }
  const shq = (t) => (/^[A-Za-z0-9_@%+=:,./-]+$/.test(t) ? t : "'" + String(t).replace(/'/g, "'\\''") + "'");
  // The run command a person sets up: the graph file name and the engine options. An override may only go down from the run block
  //. With any error the text is null, so no wrong command is ever shown. The hash binds the command to the reviewed graph.
  function commandTemplate({ file, hash, opts, doc }) {
    const o = opts || {}, run = (doc && doc.run) || {}, errors = [], args = ["squad", "run", shq(file || "graph.json")];
    const num = (t) => (t === "" || t === undefined || t === null ? null : Number(t));
    if (!hash) errors.push("The graph has no canonical form, so it has no hash to bind.");
    if (o.adapter === "sdk") args.push("--adapter", "sdk");
    else if (o.adapter === "script" && o.script) args.push("--script", shq(o.script));
    else errors.push("Choose --adapter sdk, or --script with a file name: the run command needs one of them.");
    const names = new Set(Array.isArray(doc && doc.nodes) ? doc.nodes.map((n) => n && n.name) : []);
    if (o.stopAfter) { if (names.has(o.stopAfter)) args.push("--stop-after", shq(o.stopAfter)); else errors.push(`--stop-after names no node: ${o.stopAfter}.`); }
    const cap = (key, flag, runKey, whole) => {
      const v = num(o[key]);
      if (v === null) return;
      const bad = !Number.isFinite(v) || v <= 0 || (whole && !Number.isInteger(v));
      if (bad) errors.push(`${flag} needs a ${whole ? "whole number" : "number"} above 0.`);
      else if (typeof run[runKey] === "number" && v > run[runKey]) errors.push(`${flag} ${v} is above the run block's ${run[runKey]}: an option only goes down.`);
      else args.push(flag, String(v));
    };
    cap("spendCap", "--spend-cap", "spend_cap", false);
    cap("deadline", "--deadline", "deadline", false);
    cap("maxParallel", "--max-parallel", "max_parallel", true);
    if (o.tierPin) args.push("--tier-pin", shq(o.tierPin));
    if (hash) args.push("--expect-graph-hash", hash);
    return { text: errors.length ? null : args.join(" "), errors };
  }
  const fmtList = (d) => [...d.added.map((n) => `  + ${n}`), ...d.removed.map((n) => `  - ${n}`), ...d.changed.flatMap((c) => [`  ~ ${c.name}`, ...c.fields.map((f) => `      ${f.key}: ${f.before === null ? "(absent)" : f.before} -> ${f.after === null ? "(absent)" : f.after}`)])];
  // The review sheet: a plain-text export for a ticket or a commit message. No code reads it, and it records no approval.
  function reviewSheet({ file, hash, nodeCount, errors, caps, diff, prior, template }) {
    const L = [`Review sheet for ${file || "graph"}`, `Graph hash: ${hash || "(none: no canonical form)"}`, `Nodes: ${nodeCount}`, `Errors: ${errors.length}`, ...errors.map((e) => `  ${e}`), "", "Declared caps (as written, none added up):", `  run: ${caps.run === null ? "(none)" : caps.run}`];
    for (const r of caps.nodes) L.push(`  ${r.name} (${r.kind}): ${Object.entries(r).filter(([k]) => k !== "name" && k !== "kind").map(([k, v]) => `${k} ${v}`).join(", ")}`);
    for (const g of caps.gates) L.push(`  gate ${g.name}: check ${g.check}, params ${g.params}`);
    L.push("");
    if (!prior || !diff) L.push("Diff: no prior graph opened.");
    else {
      L.push(`Diff against ${prior.file} (hash ${prior.hash || "none"}):`);
      if (diff.empty) L.push("  no difference");
      else {
        for (const f of [...diff.top, ...diff.run.map((x) => ({ ...x, key: "run." + x.key }))]) L.push(`  ${f.key}: ${f.before === null ? "(absent)" : f.before} -> ${f.after === null ? "(absent)" : f.after}`);
        for (const k of ["nodes", "facts", "gates"]) { const lines = fmtList(diff[k]); if (lines.length) L.push(` ${k}:`, ...lines); }
      }
    }
    L.push("", "Command template:", template ? `  ${template}` : "  (none: fix the options first)", "", REVIEW_NOTE);
    return L.join("\n") + "\n";
  }

  return { REVIEW_NOTE, diffGraphs, declaredCaps, commandTemplate, reviewSheet, saveText, hashChip, hashRelation, draftRecord, draftOffer, saveGate, saveMode };
}
