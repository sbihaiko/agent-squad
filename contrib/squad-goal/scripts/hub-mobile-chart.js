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
const mltY = (y, h) => MLT_P + h - y * h;   // plot fraction to the drawing's y
const mltVal = v => v && v.text ? v.text : "?";
const mltLine = (v, col, dash, w, h) => v && v.y != null
  ? `<line x1=${MLT_P} x2=${MLT_P + w} y1=${mltY(v.y, h)} y2=${mltY(v.y, h)} stroke="${col}" stroke-width=1${dash ? ` stroke-dasharray="${dash}"` : ""}></line>`
  : "";   // one 1px reference line, patterned so it reads in grayscale (#1332)
const mltLegend = c => `<p class=mltlg><span><i class=mlthollow></i>Card</span><span><i class=mltfilled></i>Cluster</span><span><i class=mltroll></i>Rolling mean</span></p>`   // the series legend names every mark the plot draws (#1332)
  + `<p class=mltlg>${MLT_REF.map(([key, , , cls]) => `<span><i class=${cls}></i>${mltEsc((c[key] && c[key].label) || key)} <b>${mltEsc(mltVal(c[key]))}</b></span>`).join("")}</p>`;   // the reference values with their swatches sit under the plot, where the phone has no gutter (#1333)
const mltChart = c => {
  const f = mltFrame(c), w = f.width, h = f.height, base = MLT_P + h, ink = "var(--chart-axis)";
  const grid = (c.ticks || []).map(t => `<line x1=${MLT_P} y1=${mltY(t.y, h)} x2=${MLT_P + w} y2=${mltY(t.y, h)} stroke="var(--chart-grid)" stroke-dasharray="3 2"></line>` +
    `<text x=${MLT_P - 6} y=${mltY(t.y, h) + 4} text-anchor=end fill="${ink}">${mltEsc(t.text)}</text>`).join("");
  const xs = (c.xticks || []).map(t => `<text x=${mltX(t.x, w)} y=${base + 18} text-anchor=middle fill="${ink}">${mltEsc(t.text)}</text>`).join("");
  const axes = `<line x1=${MLT_P} y1=${MLT_P - 10} x2=${MLT_P} y2=${base} stroke="var(--chart-grid)"></line>` +
    `<line x1=${MLT_P} y1=${base} x2=${MLT_P + w} y2=${base} stroke="var(--chart-grid)"></line>` +
    `<text x=${MLT_P} y=${MLT_P - 24} text-anchor=start fill="${ink}">Lead time (log)</text>` +
    `<text x=${MLT_P} y=${base + 36} text-anchor=start fill="${ink}">Date closed</text>`;   // the phone names no board column: the hub's column names stay out of its page (ADR-0137)
  const refs = MLT_REF.map(([key, col, dash]) => mltLine(c[key], col, dash, w, h)).join("");
  const dots = c.dots || [];
  const roll = dots.length ? `<polyline fill=none stroke="var(--chart-bar)" stroke-width=3 points="${dots.map(d => `${mltX(d.x, w)},${mltY(d.rolling.y, h)}`).join(" ")}"></polyline>` : "";   // the trend is the strongest line (#1332)
  const marks = (c.clusters || []).map((k, i) => `<circle data-lt-k="${i}" cx=${mltX(k.x, w)} cy=${mltY(k.y, h)} r=${k.r} fill="${k.count > 1 ? "var(--chart-ink)" : "var(--chart-bg)"}" stroke="var(--chart-ink)" stroke-width=${c.mark.outline} style="cursor:pointer"></circle>`).join("");   // one hollow dot per card, one filled dot per cluster, at the payload's own sizes (#1331, #1432)
  const svg = `<svg data-lt viewBox="0 0 ${MLT_P + w + 4} ${h + 2 * MLT_P + 8}" role="img" aria-label="Lead time">${grid}${xs}${axes}${refs}${roll}${marks}</svg>`;   // no right gutter: the values moved under the plot (#1333)
  return { svg, legend: mltLegend(c) };
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
