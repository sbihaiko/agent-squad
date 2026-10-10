
// Dashboard core module: a gui-test/1 test script as a step diagram. No DOM here; the page puts the markup in a view.
// Nothing runs: the diagram reads the script file only. The format check mirrors squad/gui_test_render.py.

function scriptModule() {
  const SCRIPT_FORMATS = ["gui-test/1"];
  const SECTIONS = ["setup", "steps", "teardown"];
  const STEP = { w: 280, h: 74, gap: 26, colGap: 40, head: 34, pad: 16, line: 14, lines: 2, chars: 40 };
  const escXml = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  // The model: batches in order, each with its setup, steps and teardown sections; one node per step, in script order.
  // A step's variants read as `axis=value` tags; a step with none runs under every variant, tagged "all".
  function scriptDiagram(script) {
    if (!script || typeof script !== "object" || !SCRIPT_FORMATS.includes(script.format)) {
      return { error: `unknown format ${JSON.stringify(script && script.format)}; the dashboard draws ${JSON.stringify(SCRIPT_FORMATS)}` };
    }
    const node = (s) => ({ id: s.id, text: s.expect, tags: s.variants && Object.keys(s.variants).length ? Object.entries(s.variants).map(([k, v]) => `${k}=${v}`) : ["all"],
      manual: s.mode === "manual", sensitive: s.sensitive === true, severity: s.severity });
    return { name: script.name, batches: (script.batches || []).map((b) => ({ id: b.id,
      sections: SECTIONS.filter((k) => (b[k] || []).length).map((k) => ({ label: k, nodes: b[k].map(node) })) })) };
  }

  function wrap(text) {
    const words = String(text).split(/\s+/), rows = [""];
    for (const w of words) {
      const cur = rows[rows.length - 1];
      if (cur && (cur + " " + w).length > STEP.chars) rows.push(w); else rows[rows.length - 1] = cur ? cur + " " + w : w;
    }
    if (rows.length > STEP.lines) { rows.length = STEP.lines; rows[STEP.lines - 1] = rows[STEP.lines - 1].slice(0, STEP.chars - 1) + "…"; }
    return rows;
  }

  // One column per batch, left to right; its steps run top to bottom with an arrow between neighbors. The box keeps the clipped text; <title> keeps all of it.
  function scriptSvg(script) {
    const m = scriptDiagram(script);
    if (m.error) return m;
    let out = "", x = STEP.pad, height = 0;
    for (const b of m.batches) {
      let y = STEP.pad + STEP.head, prev = null;
      out += `<g class="batch" data-batch="${escXml(b.id)}"><text class="batch-name" x="${x}" y="${STEP.pad + 16}">${escXml(b.id)}</text>`;
      for (const sec of b.sections) {
        for (const n of sec.nodes) {
          if (prev !== null) out += `<path class="flow" d="M${x + STEP.w / 2} ${prev} V${y}" marker-end="url(#arrow)"/>`;
          const cls = (n.manual ? "step manual" : "step auto") + (n.sensitive ? " sensitive" : "");
          out += `<g class="${cls}" data-step="${escXml(n.id)}" data-section="${sec.label}"><title>${escXml(n.id)}: ${escXml(n.text)}</title>`
            + `<rect x="${x}" y="${y}" width="${STEP.w}" height="${STEP.h}" rx="6"/>`
            + `<text class="step-id" x="${x + 8}" y="${y + 14}">${escXml(sec.label === "steps" ? "" : sec.label + " · ")}${escXml(n.id)}${n.manual ? " · manual" : ""}</text>`
            + wrap(n.text).map((r, i) => `<text class="step-text" x="${x + 8}" y="${y + 30 + i * STEP.line}">${escXml(r)}</text>`).join("")
            + `<text class="step-tags" x="${x + 8}" y="${y + STEP.h - 8}">${escXml(n.tags.join(" "))}</text></g>`;
          prev = y + STEP.h; y += STEP.h + STEP.gap;
        }
      }
      out += "</g>";
      height = Math.max(height, y);
      x += STEP.w + STEP.colGap;
    }
    const w = x - STEP.colGap + STEP.pad;
    return `<svg class="script-diagram" viewBox="0 0 ${w} ${height}" width="${w}" height="${height}">`
      + `<defs><marker id="arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0 L8 4 L0 8 z"/></marker></defs>${out}</svg>`;
  }

  // A GUI test run as nodes, one per step in record order: the runner's gui_step, gui_capture and gui_verdict events up to the replay cursor
  // (null is the end). A step shows `executed` once its wait was met, its capture was taken and its verdict is in; a verdict with no
  // capture is `failed` (its wait timed out); a step with no verdict yet is `running`.
  function guiRun(events, cursor) {
    const nodes = new Map(), sources = new Map();
    for (const e of events) {
      if (cursor != null && e.pos > cursor) break;
      if (!String(e.event).startsWith("gui_")) continue;
      if (!nodes.has(e.node)) nodes.set(e.node, { id: e.node, status: "running", verdict: null, capture: null, held: false, pos: e.pos });
      const n = nodes.get(e.node);
      // A held capture (a step marked sensitive) shows no path until a gui_approve event names its sha256.
      if (e.event === "gui_capture") { n.held = e.held === true; n.capture = { path: n.held ? null : e.path, size: e.size, sha256: e.sha256 }; sources.set(e.node, e.path); }
      if (e.event === "gui_approve" && n.capture && n.capture.sha256 === e.sha256) { n.held = false; n.capture.path = sources.get(e.node); }
      if (e.event === "gui_verdict") { n.verdict = e.verdict; n.status = n.capture ? "executed" : "failed"; }
    }
    return [...nodes.values()];
  }

  return { scriptDiagram, scriptSvg, guiRun };
}
