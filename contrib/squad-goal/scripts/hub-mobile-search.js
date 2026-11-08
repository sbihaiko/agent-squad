// Truthful filters, the counted remainder and Search on the phone (SPEC 1596, rules 4, 5 and 6; #1600). Pure: no DOM and
// no network. The hub inlines this text into hub-mobile.html. A filter keeps the rows its own fact names, the remainder
// line counts what it hid, and a search matches a row's title, its number or one of its labels inside the current scope.
//
// The remainder is one quiet line: "2 hidden by Live", or nothing when the filter hides no row. The words are the option's
// own label, so the line never names a filter the owner cannot see.
const restLine = (total, shown, label) => {
  const hidden = Math.max(0, (Number(total) || 0) - (Number(shown) || 0));
  return hidden > 0 ? `${hidden} hidden by ${label}` : "";
};

// A row's searchable text: its title and its labels, compared with no case. A number is its own fact and compares exactly,
// so the query 42 is card 42 and never card 142; "#42" names the same card. A row that carries no labels searches on its
// title alone, and an empty query keeps every row in the list's own order.
const searchHit = (row, query) => {
  const q = String(query == null ? "" : query).trim().toLowerCase();
  if (!q) return true;
  if (!row) return false;
  const num = q.replace(/^#/, "");
  if (/^\d+$/.test(num) && String(row.n == null ? "" : row.n) === num) return true;
  const text = [row.title, ...(row.labels || [])].join(" ").toLowerCase();
  return text.includes(q);
};
const searchRows = (rows, query) => (rows || []).filter(r => searchHit(r, String(query == null ? "" : query).trim()));
