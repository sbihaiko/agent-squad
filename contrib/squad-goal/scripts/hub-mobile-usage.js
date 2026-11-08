// Runs › Usage on the phone (SPEC 1596, #1604): the token bars and the model pie of the desktop's /api/stats payload, laid
// out for the phone. Pure: no DOM and no network. The hub inlines this text into hub-mobile.html, and the numbers are the
// hub's own (ADR-0137): this module only shapes them.
const USAGE_PERIODS = [["hour", "Hour"], ["day", "Day"], ["month", "Month"]];
const USAGE_TOP = 5;
const usageLabel = (t, period) => period === "hour" ? t.slice(11, 16) : period === "day" ? t.slice(8, 10) : t.slice(5, 7);
const usageMillions = n => (n / 1e6).toFixed(1) + "M";
const usageCount = n => n < 1e6 ? n + " tokens" : usageMillions(n) + " tokens";
function usageBars(buckets, period) {
  const max = Math.max(1, ...buckets.map(b => b.tokens || 0));
  return buckets.map(b => ({ label: usageLabel(b.t, period), tokens: b.tokens || 0, h: (b.tokens || 0) / max * 100 }));
}
function usageSlices(models) {
  const sorted = [...models].sort((a, b) => b.month_tokens - a.month_tokens);
  const total = sorted.reduce((s, m) => s + m.month_tokens, 0) || 1;
  const slice = (model, tokens) => ({ model, tokens, pct: tokens / total * 100, legend: `${model} · ${Math.round(tokens / total * 100)}% · ${usageCount(tokens)}` });
  const out = sorted.slice(0, USAGE_TOP).map(m => slice(m.model, m.month_tokens));
  const rest = sorted.slice(USAGE_TOP);
  if (rest.length) {
    const tokens = rest.reduce((s, m) => s + m.month_tokens, 0);
    out.push(slice(`other · ${rest.length} model${rest.length === 1 ? "" : "s"}`, tokens));
  }
  return out;
}
function usagePeak(buckets, period) {
  const top = buckets.reduce((a, b) => (b.tokens || 0) > (a.tokens || 0) ? b : a, buckets[0]);
  return usageMillions(top.tokens || 0) + " at " + usageLabel(top.t, period);
}
const usageTodayLine = totals => usageMillions(totals.tok_today || 0) + " tokens · $" + Number(totals.today || 0).toFixed(2);
