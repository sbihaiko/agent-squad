// Spend on the phone (SPEC 2498, #2503): the desktop panel's summary line, its token bars per hour, day or month and its
// model pie, laid out for the phone. Pure: no DOM and no network. The hub inlines this text into hub-mobile.html, and the
// numbers are the hub's own (ADR-0137): this module only shapes them.
const USAGE_PERIODS = [["hour", "Hour"], ["day", "Day"], ["month", "Month"]];
const USAGE_TOP = 3;   // the desktop pie keeps its top three models and folds the tail (#1035): the phone keeps the same three
const USAGE_COLORS = ["1", "2", "3", "4", "5", "6", "7", "8"].map(n => "var(--chart-series-" + n + ")");   // the fold's own role stays out, so a model never wears "other"'s colour (#2503)
const USAGE_OTHER = "var(--chart-series-other)";
const usageLabel = (t, period) => period === "hour" ? t.slice(11, 16) : period === "day" ? t.slice(8, 10) : t.slice(5, 7);
const usageTokens = n => n >= 1e9 ? (n / 1e9).toFixed(2) + "B" : n >= 1e6 ? (n / 1e6).toFixed(1) + "M" : n >= 1e3 ? (n / 1e3).toFixed(0) + "K" : "" + n;   // the desktop pie's own scale (#1035)
const usageMillions = n => (n / 1e6).toFixed(1) + "M";
const usageCount = n => n < 1e6 ? n + " tokens" : usageMillions(n) + " tokens";
function usageBars(buckets, period) {
  const max = Math.max(1, ...buckets.map(b => (b.tokens || 0) + (b.op || 0)));
  return buckets.map(b => ({ label: usageLabel(b.t, period), tokens: b.tokens || 0, op: b.op || 0, h: ((b.tokens || 0) + (b.op || 0)) / max * 100, hLoop: (b.tokens || 0) / max * 100 }));
}
function usageSlices(models) {
  // One slice per label, and every label names its consumer (loop or operator): a total never mixes the two in silence
  // (#1756, #2503). The operator session's calls are their own slice, never merged into the loop's model of the same name.
  const byName = new Map();
  for (const m of models) {
    const consumer = m.consumer === "operator" ? "operator" : "loop";
    const k = consumer + " · " + m.model;
    const r = byName.get(k) || { model: k, name: m.model, consumer, tokens: 0, other: false };
    r.tokens += m.month_tokens || 0;
    byName.set(k, r);
  }
  const sorted = [...byName.values()].sort((a, b) => b.tokens - a.tokens);
  const total = sorted.reduce((s, x) => s + x.tokens, 0) || 1;
  const slice = x => ({ model: x.model, name: x.name, consumer: x.consumer, other: x.other, tokens: x.tokens,
    pct: x.tokens / total * 100, legend: `${x.model} · ${Math.round(x.tokens / total * 100)}% · ${usageCount(x.tokens)}` });
  const out = sorted.slice(0, USAGE_TOP).map(slice);
  const rest = sorted.slice(USAGE_TOP);
  for (const consumer of ["loop", "operator"]) {   // the fold names its consumer too, and never merges the two (#2503)
    const tail = rest.filter(x => x.consumer === consumer);
    if (!tail.length) continue;
    out.push(slice({ model: `${consumer} · other · ${tail.length} model${tail.length === 1 ? "" : "s"}`, name: "other", consumer,
      other: true, tokens: tail.reduce((s, x) => s + x.tokens, 0) }));
  }
  return out;
}
function usageSliceColors(slices) {
  // One fill per slice, and "other"'s role never goes to a model: the largest slice and the fold are always distinct
  // (#2503). A model keeps its own role across renders; a collision takes the next free role.
  const byName = new Map();
  let used = new Set();
  return slices.map(s => {
    if (!byName.has(s.model)) {
      const hash = [...String(s.model)].reduce((h, c) => (h * 31 + c.charCodeAt(0)) >>> 0, 0);
      const prefer = s.other ? [USAGE_OTHER, ...USAGE_COLORS] : [USAGE_COLORS[hash % USAGE_COLORS.length], ...USAGE_COLORS];
      const role = prefer.find(c => !used.has(c)) || USAGE_COLORS.find(c => !used.has(c)) || USAGE_OTHER;
      used.add(role);
      byName.set(s.model, role);
    }
    return byName.get(s.model);
  });
}
function usageRing(slices, r) {
  const circle = 2 * Math.PI * r;
  let at = 0;
  return slices.map(s => { const len = s.pct / 100 * circle, out = { model: s.model, len, off: -at }; at += len; return out; });
}
function usagePeak(buckets, period) {
  const all = b => (b.tokens || 0) + (b.op || 0);   // the peak is the day's whole burn, operator included (#1756)
  const top = buckets.reduce((a, b) => all(b) > all(a) ? b : a, buckets[0]);
  return usageMillions(all(top)) + " at " + usageLabel(top.t, period);
}
function usageSummary(st) {
  // The desktop Spend panel's own summary line (its `stsr`), read above the phone's chart (#2503).
  const p = st.pace || {}, op = st.operator || {}, t = st.totals || {};
  const u = n => n == null ? "no runs" : usageTokens(n);
  return `${u(p.median_tokens)} tokens/h median, ${p.active_hours == null ? 0 : p.active_hours} of ${p.window_hours == null ? 0 : p.window_hours} h with runs (last ${p.window_hours == null ? 0 : p.window_hours} h) · ${u(p.last60_tokens)} tokens last 60 min`
    + (p.unpriced ? ` · ${p.unpriced} unpriced call${p.unpriced > 1 ? "s" : ""} (tokens counted)` : "")
    + ` · ${usageTokens(t.tok_today || 0)} loop tokens today`
    + (op.tokens_today ? ` · operator ${usageTokens(op.tokens_today)} tokens today` : "");
}
const usageTodayLine = (totals, op) => usageMillions(totals.tok_today || 0) + " tokens · $" + Number(totals.today || 0).toFixed(2)   // the dollar figure is the loop's own
  + (op && op.tokens_today ? " · +" + usageMillions(op.tokens_today) + " operator" : "");   // so a day of operator calls is named beside it, never hidden (#1756)
