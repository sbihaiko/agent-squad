// Dashboard core module: The replay cursor.. Moved from rest.js with no change.
// A factory: it takes what lower modules export and returns its own functions. No DOM here.

function replayModule({ num, orNull }) {
  // ---- The replay cursor ----
  // The cursor is one record `pos`. It folds nothing into the record and keeps no index: events are in file order, so `pos` is sorted and a
  // binary search finds the cut. A null cursor means "pinned to the end": a live run keeps the cursor there until the owner moves it.
  function countUpTo(events, pos) {
    let lo = 0, hi = events.length;
    while (lo < hi) { const mid = (lo + hi) >> 1; if (events[mid].pos <= pos) lo = mid + 1; else hi = mid; }
    return lo;
  }

  // An accepted child with no outcome event at the cursor. Not the engine's frontier (GLOSSARY.md, Unresolved child).
  function unresolvedChildren(events, cursor) {
    const seen = events.slice(0, countUpTo(events, cursor));
    const done = new Set(seen.filter((e) => e.event === "outcome").map((e) => e.node));
    return seen.filter((e) => e.event === "spawn_accepted" && !done.has(e.child))
      .map((e) => ({ child: e.child, parent: orNull(e.parent), parent_entry: orNull(e.parent_entry), depth: orNull(e.depth), pos: e.pos }));
  }

  // The record up to the cursor. A cursor that is null, past the end, or before the start lands on the end or the first event;
  // a cursor between two positions lands on the one before. The verdict shows only at or after its own pos, since it is one event.
  function replayAt(events, cursor) {
    const first = events[0].pos, last = events[events.length - 1].pos;
    const want = num(cursor) ? Math.min(Math.max(cursor, first), last) : last;
    const n = countUpTo(events, want), at = events[n - 1].pos;
    return { cursor: at, first, last, at_end: at === last, events: events.slice(0, n), unresolved: unresolvedChildren(events, at) };
  }

  // The next cursor for one move. A move that reaches the end gives null (pinned), so a live run goes on following the record.
  function moveCursor(events, cursor, move, to) {
    const first = events[0].pos, last = events[events.length - 1].pos;
    const here = replayAt(events, cursor).cursor;
    const n = countUpTo(events, here);
    const target = move === "start" ? first : move === "end" ? last : move === "back" ? events[Math.max(n - 2, 0)].pos
      : move === "forward" ? events[Math.min(n, events.length - 1)].pos : replayAt(events, to).cursor;
    return target >= last ? null : target;
  }

  return { countUpTo, unresolvedChildren, replayAt, moveCursor };
}
