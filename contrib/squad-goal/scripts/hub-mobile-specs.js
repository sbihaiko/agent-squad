// Specs on the phone (SPEC 1596, #1604): each open spec with its tickets' progress by column, the count done and a legend,
// from the board the phone already reads. Pure: no DOM and no network. A group's own name and its `end` flag are the hub's
// facts, and `board.done` names the delivered column; the module holds no board column of its own (ADR-0137).
// The row prints "SPEC <n>" itself, so a title that starts with the same word or a "[SPEC ...]" marker drops it.
const stripSpec = t => String(t || "").replace(/^\s*\[SPEC[^\]]*\]\s*/i, "").replace(/^\s*SPEC\s*:?\s*/i, "");
function specProgress(board) {
  const groups = (board && board.groups) || [];
  const items = groups.flatMap(g => g.items.map(i => ({ ...i, col: g.name })));
  const ends = new Set(groups.filter(g => g.end).map(g => g.name));
  const doneCol = (board && board.done) || "";
  return items.filter(s => s.sp && !ends.has(s.col)).map(s => {
    const kids = items.filter(k => k.pa === s.n);
    const total = kids.length, done = kids.filter(k => k.col === doneCol).length;
    const pct = total ? Math.round(done / total * 100) : 0;
    const cols = groups.map(g => ({ col: g.name, color: g.color || "", n: kids.filter(k => k.col === g.name).length })).filter(c => c.n > 0);
    const bar = cols.map(c => ({ col: c.col, color: c.color, w: c.n / total * 100 }));
    return { n: s.n, title: stripSpec(s.t), repo: s.r || "", labels: s.l || [], total, done, pct, count: `${done}/${total} · ${pct}%`, cols, bar, legend: cols.map(c => `${c.col} ${c.n}`).join(" · ") };
  });
}
