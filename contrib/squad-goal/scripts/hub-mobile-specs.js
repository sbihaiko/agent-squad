// Specs on the phone (SPEC 1596, #1604): each open spec with its tickets' progress by column, the count done and a legend,
// from the board the phone already reads. Pure: no DOM and no network. The columns and tickets are the hub's (ADR-0137).
const SPEC_DONE = "Done";
function specProgress(board) {
  const groups = (board && board.groups) || [];
  const items = groups.flatMap(g => g.items.map(i => ({ ...i, col: g.name })));
  return items.filter(s => s.sp && s.col !== SPEC_DONE).map(s => {
    const kids = items.filter(k => k.pa === s.n);
    const total = kids.length, done = kids.filter(k => k.col === SPEC_DONE).length;
    const pct = total ? Math.round(done / total * 100) : 0;
    const cols = groups.map(g => ({ col: g.name, n: kids.filter(k => k.col === g.name).length })).filter(c => c.n > 0);
    const bar = cols.map(c => ({ col: c.col, w: c.n / total * 100 }));
    return { n: s.n, title: s.t, repo: s.r || "", total, done, pct, count: `${done}/${total} · ${pct}%`, cols, bar, legend: cols.map(c => `${c.col} ${c.n}`).join(" · ") };
  });
}
