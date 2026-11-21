// The phone's lead-time chart (SPEC 1327, #1333). Pure: no DOM and no network. The hub inlines this text into
// hub-mobile.html. It draws the payload /api/chart/lead already serves — the desktop page's own numbers — and computes
// none of them (ADR-0137). The phone layout is its own: the plot first, then the legend and the three reference values
// with their swatches under it, and no right gutter. A tap pins one mark's tooltip, and zoom and reset are explicit
// buttons, never a drag.
const MLT_P = 44;   // the left margin a duration tick needs, in the plot's own units
const MLT_REF = [   // each reference line: the payload's key, its paint, its 1px pattern and its legend swatch (#1332)
  ["average", "var(--chart-series-8)", "", "mltsolid"],
  ["median", "var(--chart-ok)", "6 3", "mltdash"],
  ["p85", "var(--chart-series-5)", "1 3", "mltdot"],
];
const mltEsc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const mltFrame = c => c && c.plot_frame && c.plot_frame.width > 0 ? c.plot_frame : { width: 720, height: 240 };
const mltX = (x, w) => MLT_P + x * w;   // plot fraction to the drawing's x
const mltY = (y, base, h) => Math.min(base, Math.max(base - h, base - y * h));   // plot fraction to the drawing's y, clamped to the frame so a log-scale tick past 1 stays visible (#2504)
const mltVal = v => v && v.text ? v.text : "?";
const mltLine = (v, col, dash, w, base, h) => v && v.y != null
  ? `<line x1=${MLT_P} x2=${MLT_P + w} y1=${mltY(v.y, base, h)} y2=${mltY(v.y, base, h)} stroke="${col}" stroke-width=1${dash ? ` stroke-dasharray="${dash}"` : ""}></line>`
  : "";   // one 1px reference line, patterned so it reads in grayscale (#1332)
const mltLegend = (c, band) => `<p class=mltlg><span><i class=mlthollow></i>Card</span><span><i class=mltfilled></i>Cluster</span><span><i class=mltroll></i>Rolling mean</span>` +
  (band ? `<span><i class=mltband></i>Rolling spread ±1 SD</span>` : "") + `</p>`   // the series legend names every mark the plot draws (#1332, #2504)
  + `<p class=mltlg>${MLT_REF.map(([key, , , cls]) => `<span><i class=${cls}></i>${mltEsc((c[key] && c[key].label) || key)} <b>${mltEsc(mltVal(c[key]))}</b></span>`).join("")}</p>`;   // the reference values with their swatches sit under the plot, where the phone has no gutter (#1333)
const mltChart = (c, size) => {
  // A chart screen hands the box it measured, and the drawing fills it (SPEC 2498, #2504): the viewBox is that box, so a
  // rotation that measures again draws at the new width. With no size the payload's frame sizes the chart, as before. Both
  // paths take their margins from this one place, and a reference value stands only in the legend, so no path reserves a
  // right gutter (#2504).
  const f = mltFrame(c), box = size ? mcChartBox(size) : null;
  const W = box ? box.width : MLT_P + f.width + 4, H = box ? box.height : f.height + 2 * MLT_P + 8;
  const pT = box ? 20 : MLT_P, pB = box ? 40 : MLT_P;   // the phone's short side gives the plot its room; the payload frame keeps its own margins (#2504)
  const w = Math.max(40, W - MLT_P - 4), h = Math.max(30, H - pT - pB), base = pT + h, ink = "var(--chart-axis)";
  const Y = v => mltY(v, base, h);   // one Y for both paths, clamped to the plot's own top and floor (#2504)
  const grid = (c.ticks || []).map(t => `<line x1=${MLT_P} y1=${Y(t.y)} x2=${MLT_P + w} y2=${Y(t.y)} stroke="var(--chart-grid)" stroke-dasharray="3 2"></line>` +
    `<text x=${MLT_P - 6} y=${Y(t.y) + 4} text-anchor=end fill="${ink}">${mltEsc(t.text)}</text>`).join("");
  const xs = (c.xticks || []).map((t, i, a) => {   // the end labels turn inward, so the box never cuts a date (#2504)
    const anchor = a.length === 1 ? "middle" : i === 0 ? "start" : i === a.length - 1 ? "end" : "middle";
    return `<text x=${mltX(t.x, w)} y=${base + 18} text-anchor=${anchor} fill="${ink}">${mltEsc(t.text)}</text>`;
  }).join("");
  const axes = `<line x1=${MLT_P} y1=${base - h} x2=${MLT_P} y2=${base} stroke="var(--chart-grid)"></line>` +
    `<line x1=${MLT_P} y1=${base} x2=${MLT_P + w} y2=${base} stroke="var(--chart-grid)"></line>` +
    `<text x=${MLT_P} y=${base - h - 6} text-anchor=start fill="${ink}">Lead time (log)</text>` +
    `<text x=${MLT_P} y=${base + 36} text-anchor=start fill="${ink}">Date closed</text>`;   // the phone names no board column: the hub's column names stay out of its page (ADR-0137)
  const refs = MLT_REF.map(([key, col, dash]) => mltLine(c[key], col, dash, w, base, h)).join("");
  const dots = c.dots || [];
  // The rolling spread is the payload's own per-dot high and low readings, drawn as the desktop draws it (#2504, #1334).
  const bp = box && dots.length && dots.every(d => d.rolling && d.rolling.y_high != null && d.rolling.y_low != null)
    ? [...dots.map(d => ({ x: d.x, y: d.rolling.y_high })), ...dots.slice().reverse().map(d => ({ x: d.x, y: d.rolling.y_low }))] : null;
  const band = bp ? `<polygon points="${bp.map(p => `${mltX(p.x, w)},${Y(p.y)}`).join(" ")}" fill="var(--chart-series-3)" fill-opacity=".28"><title>Rolling spread ±1 SD</title></polygon>` : "";
  const roll = dots.length ? `<polyline fill=none stroke="var(--chart-bar)" stroke-width=3 points="${dots.map(d => `${mltX(d.x, w)},${Y(d.rolling.y)}`).join(" ")}"></polyline>` : "";   // the trend is the strongest line (#1332)
  const outline = (c.mark && c.mark.outline) ?? 1.5;   // a payload from before `mark` keeps the 1.5 px ring (#1432)
  const marks = (c.clusters || []).map((k, i) => `<circle data-lt-k="${i}" cx=${mltX(k.x, w)} cy=${Y(k.y)} r=${k.r} fill="${k.count > 1 ? "var(--chart-ink)" : "var(--chart-bg)"}" stroke="var(--chart-ink)" stroke-width=${outline} style="cursor:pointer"></circle>`).join("");   // one hollow dot per card, one filled dot per cluster, at the payload's own sizes (#1331, #1432)
  const svg = `<svg data-lt viewBox="0 0 ${W} ${H}" role="img" aria-label="Lead time">${grid}${xs}${axes}${band}${refs}${roll}${marks}</svg>`;   // the reference values stand in the legend alone (#2504)
  return { svg, legend: mltLegend(c, !!bp) };
};
const mltStack = k => (k.dots || []).map(d => (d.stack || []).map(b => `${b.column} ${b.text}`).join(", ")).filter(Boolean).join(" · ");
const mltPin = k => {
  const dots = (k && k.dots) || [], m = dots.length;
  if (!m) return "";   // every mark is a cluster of dots: its cards, its ranges and its stack live there
  const head = m > 1 ? `${m} issues: ${dots.map(d => "#" + d.n).join(", ")}` : `#${dots[0].n} ${dots[0].title || ""}`.trim();
  const lead = m > 1 ? (k.lead && k.lead.text) : dots[0].text;   // a cluster carries a range; a lone card its own duration
  const closed = m > 1 ? (k.closed && k.closed.text) : dots[0].closed;
  return [head, lead ? "lead " + lead : "", closed ? "closed " + closed : "", mltStack(k)].filter(Boolean).join(" · ");
};
const mltZoom = (range, dir) => {
  if (dir === "reset" || !range || !range.start || !range.end) return null;   // reset reads the payload's own window again
  const a = Date.parse(range.start), b = Date.parse(range.end);
  if (!(a < b)) return null;
  const mid = (a + b) / 2, half = (b - a) / 2, f = dir === "out" ? 2 : 0.5;   // the buttons halve and double the window
  return { from: new Date(mid - half * f).toISOString(), to: new Date(mid + half * f).toISOString() };
};

// The chart screens in landscape (SPEC 2498, #2504). The same rules serve every chart: the frame is the box the page
// measured at draw time, so a rotation measures again and redraws at the new width; portrait is the rotate hint alone.
const MC_MIN_W = 120, MC_MIN_H = 80;
const mcLandscape = (w, h) => Number(w) > Number(h);   // the phone's wider side is the landscape side
const mcChartBox = size => {   // the drawing frame: the measured box, never a fixed one the layout has not met
  const w = Math.round(Number(size && size.width) || 0), h = Math.round(Number(size && size.height) || 0);
  return { width: w >= MC_MIN_W ? w : MC_MIN_W, height: h >= MC_MIN_H ? h : MC_MIN_H };
};
// The cumulative flow draws the payload /api/chart/cfd serves, at the measured box: the desktop's stack of bands, its
// axis and its per-day reading, and no number this page derives (ADR-0137). A band's paint is the board's own color role.
const MC_CFD_L = 44, MC_CFD_B = 34, MC_CFD_T = 14, MC_CFD_R = 10;
const MC_SERIES = ["var(--chart-series-1)", "var(--chart-series-2)", "var(--chart-series-3)", "var(--chart-series-4)",
  "var(--chart-series-5)", "var(--chart-series-6)", "var(--chart-series-7)", "var(--chart-series-8)"];
// The band's paint: the role the payload carries, else the caller's map, else the series palette by position. The phone
// page names no board column itself, so the hub sends the role with the band (#2504, ADR-0137).
const mcCfdColor = (colors, name, i, band) => (band && band.color) || (colors && colors[name]) || MC_SERIES[i % MC_SERIES.length];
const mcCfdStack = (c, hidden = []) => {   // the bands the payload stacks, minus the states the reader hid
  const all = (c.bands || []).filter(b => b && Array.isArray(b.upper));
  const keep = all.filter(b => !hidden.includes(b.name));
  if (keep.length === all.length || !all.every(b => b.lower && b.upper)) return keep;
  const base = {}; let at = null;   // the rest re-stack from their counts, with no gap where a hidden band stood (#1356)
  for (let i = keep.length - 1; i >= 0; i--) {
    const b = keep[i], zero = b.count.map(() => 0);
    base[b.name] = at === null ? zero : at;
    at = (at === null ? zero : at).map((v, k) => v + (b.count[k] || 0));
  }
  return keep.map(b => ({ ...b, lower: base[b.name], upper: base[b.name].map((v, k) => v + (b.count[k] || 0)),
    held_upper: base[b.name].map((v, k) => v + (b.held[k] || 0)) }));
};
const mcCfdRead = (c, i) => (c.reads || [])[i] || null;
const mcCfdText = (r, names) => {
  if (!r) return "";
  const vis = new Set(names || []);   // a hidden state is not named, as on the desktop (#1356); WIP and throughput stay whole-board (#1149)
  const counts = (r.counts || []).filter(k => vis.has(k.name)).map(k => `${k.name} ${k.count == null ? "none" : k.count}`).join(", ");
  const fig = v => (v == null ? "none" : v);
  return `${r.day}: ${counts} · WIP ${fig(r.wip)} (whole board) · throughput ${fig(r.throughput)} (whole board)`;
};
const mcCfd = (c, size, colors, hidden) => {
  const shown = mcCfdStack(c, hidden || []);
  const box = mcChartBox(size), W = box.width, H = box.height;
  const x0 = MC_CFD_L, x1 = Math.max(x0 + 20, W - MC_CFD_R), y0 = MC_CFD_T, y1 = Math.max(y0 + 20, H - MC_CFD_B);
  const days = c.days || [], n = days.length, ymax = Number(c.ymax) || 0;
  const X = i => (n < 2 ? x0 : x0 + i * (x1 - x0) / (n - 1));
  const Y = v => (ymax ? y1 - v * (y1 - y0) / ymax : y1);
  const axis = "var(--chart-axis)";
  const grid = (c.yticks || []).map(v => `<line x1=${x0 - 4} y1=${Y(v)} x2=${x0} y2=${Y(v)} stroke=${axis}></line>` +
    `<text x=${x0 - 7} y=${Y(v) + 4} text-anchor=end fill=${axis}>${v}</text>`).join("");
  const xlab = (c.xticks || []).map((t, i, a) => {   // the end labels turn inward, so the box never cuts a date (#2504)
    const anchor = a.length === 1 ? "middle" : i === 0 ? "start" : i === a.length - 1 ? "end" : "middle";
    return `<line x1=${X(t.i)} y1=${y1} x2=${X(t.i)} y2=${y1 + 4} stroke=${axis}></line>`
      + `<text x=${X(t.i)} y=${y1 + 17} text-anchor=${anchor} fill=${axis}>${mltEsc(t.day)}</text>`;
  }).join("");
  const lines = `<line x1=${x0} y1=${y0} x2=${x0} y2=${y1} stroke="var(--chart-grid)"></line>` +
    `<line x1=${x0} y1=${y1} x2=${x1} y2=${y1} stroke="var(--chart-grid)"></line>` +
    `<text x=${x0 + 6} y=${y0 + 4} text-anchor=start fill=${axis}>cards</text>` +   // beside the top tick, never over it (#2504)
    `<text x=${x1} y=${y1 + 31} text-anchor=end fill=${axis}>day (${mltEsc(c.zone || "")})</text>`;
  const runs = b => { const out = []; let cur = null; days.forEach((_, i) => { if (b.upper[i] == null) { cur = null; return; } if (!cur) { cur = []; out.push(cur); } cur.push(i); }); return out; };
  const poly = (b, run) => {
    const one = run.length === 1, hwd = 3;
    const up = run.flatMap(i => (one ? [[X(i) - hwd, b.upper[i]], [X(i) + hwd, b.upper[i]]] : [[X(i), b.upper[i]]]));
    const low = [...run].reverse().flatMap(i => (one ? [[X(i) + hwd, b.lower[i]], [X(i) - hwd, b.lower[i]]] : [[X(i), b.lower[i]]]));
    return [...up, ...low].map(([x, v]) => `${x.toFixed(1)},${Y(v).toFixed(1)}`).join(" ");
  };
  const bands = shown.map((b, i) => runs(b).map(run => `<polygon points="${poly(b, run)}" fill="${mcCfdColor(colors, b.name, i, b)}" fill-opacity=".85"><title>${mltEsc(b.name)}</title></polygon>`).join("")).join("");
  const held = shown.map(b => (b.held && b.held.some(v => v > 0)
    ? runs(b).map(run => `<polygon points="${poly({ upper: b.held_upper || b.upper, lower: b.lower }, run)}" fill="var(--chart-held)" fill-opacity=".45"><title>${mltEsc(b.name)} held</title></polygon>`).join("") : "")).join("");
  const cw = (x1 - x0) / (n < 2 ? 1 : n);
  const names = shown.map(b => b.name);
  const taps = (c.reads || []).map((r, i) => `<rect data-cfd-day="${i}" x=${(X(i) - cw / 2).toFixed(1)} y=${y0} width=${cw.toFixed(1)} height=${(y1 - y0).toFixed(1)} fill="transparent" style="cursor:pointer"><title>${mltEsc(mcCfdText(r, names))}</title></rect>`).join("");
  const svg = `<svg data-cfd viewBox="0 0 ${W} ${H}" role="img" aria-label="Cumulative Flow">${bands}${held}${lines}${grid}${xlab}${taps}</svg>`;
  const legend = `<p class=mltlg>${shown.map((b, i) => `<span><i class=mltcdot style="background:${mcCfdColor(colors, b.name, i, b)}"></i>${mltEsc(b.name)}</span>`).join("")}</p>`;
  return { svg, legend };
};
const mcCfdPin = (c, i, hidden) => mcCfdText(mcCfdRead(c, i), mcCfdStack(c, hidden || []).map(b => b.name));   // a tap on a point shows its value, with the states the reader kept
