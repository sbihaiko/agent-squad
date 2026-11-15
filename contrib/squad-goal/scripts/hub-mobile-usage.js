// Runs › Usage on the phone (SPEC 1596, #1604): the token bars and the model pie of the desktop's /api/stats payload, laid
// out for the phone. Pure: no DOM and no network. The hub inlines this text into hub-mobile.html, and the numbers are the
// hub's own (ADR-0137): this module only shapes them.
const USAGE_PERIODS = [["hour", "Hour"], ["day", "Day"], ["month", "Month"]];
const USAGE_TOP = 5;
const usageLabel = (t, period) => period === "hour" ? t.slice(11, 16) : period === "day" ? t.slice(8, 10) : t.slice(5, 7);
const usageMillions = n => (n / 1e6).toFixed(1) + "M";
const usageCount = n => n < 1e6 ? n + " tokens" : usageMillions(n) + " tokens";
function usageBars(buckets, period) {
  const max = Math.max(1, ...buckets.map(b => (b.tokens || 0) + (b.op || 0)));
  return buckets.map(b => ({ label: usageLabel(b.t, period), tokens: b.tokens || 0, op: b.op || 0, h: ((b.tokens || 0) + (b.op || 0)) / max * 100, hLoop: (b.tokens || 0) / max * 100 }));
}
function usageSlices(models) {
  const byName = new Map();   // one slice per label: the operator session's calls are their own, never merged into the loop's model (#1756)
  for (const m of models) {
    const k = (m.consumer === "operator" ? "operator · " : "") + m.model;
    byName.set(k, (byName.get(k) || 0) + (m.month_tokens || 0));
  }
  const sorted = [...byName].sort((a, b) => b[1] - a[1]);
  const total = sorted.reduce((s, x) => s + x[1], 0) || 1;
  const slice = (model, tokens) => ({ model, tokens, pct: tokens / total * 100, legend: `${model} · ${Math.round(tokens / total * 100)}% · ${usageCount(tokens)}` });
  const out = sorted.slice(0, USAGE_TOP).map(([model, tokens]) => slice(model, tokens));
  const rest = sorted.slice(USAGE_TOP);
  if (rest.length) {
    const tokens = rest.reduce((s, x) => s + x[1], 0);
    out.push(slice(`other · ${rest.length} model${rest.length === 1 ? "" : "s"}`, tokens));
  }
  return out;
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
const usageTodayLine = totals => usageMillions(totals.tok_today || 0) + " tokens · $" + Number(totals.today || 0).toFixed(2);
