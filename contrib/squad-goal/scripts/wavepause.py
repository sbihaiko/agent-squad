#!/usr/bin/env python3
"""The seconds a wave's age cap is paused (#637): wave.sh expired subtracts them from the wave's age.

usage: wavepause.py <wave start epoch> <now epoch> <run folder> [<fix run folder>...]   prints whole seconds

The cap pauses while a child of the wave is in a review round or a fix entry, in the run and after it:
  in-run review round   the run's record: from the start of its first try (`review_try`, `review_progress` or `review_round`,
                        each `ts` less its `elapsed`) to its `review_round`; the pause between two tries is part of the round;
  in-run fix entry      the run's record: from the `call_start` of entry n+1 after a `fix` verdict on round n to its `outcome`;
  childcc fix run       each fix run folder auto/wave-pause-<run> names (childcc.py): from its first event to its `verdict`;
  gate wait             each gate row of $SG_STATE/gate-history and each suite row of $SG_STATE/child-suite-history whose wait
                        overlaps the wave, from the row's start less its wait to its start. The wait is a machine-wide
                        resource the child does not own (ADR-0166), and the row names no run, so the pause is the machine's:
                        it can only lengthen a wave, never shorten it past its work.
Overlapping intervals count once, and only their part inside [wave start, now] counts. An interval with no end yet ends at
now, but never later than its record's last event plus QUIET: a live run moves its record at least that often, else the
heartbeat kills it (ADR-0051), so a dead run never holds its wave for good. A record that cannot be read adds nothing.
"""
import json, os, pathlib, sys

QUIET = 300.0   # the heartbeat's quiet window: a live record moves at least this often (squad/runners/dispatch.py, REVIEW_TICK_S)


def events(folder):
    """The events of a run folder's record, those with a timestamp; [] when it cannot be read."""
    out = []
    try: lines = (pathlib.Path(folder) / "record.jsonl").read_text(errors="replace").splitlines()
    except OSError: return out
    for l in lines:
        try: d = json.loads(l)
        except ValueError: continue
        if isinstance(d, dict) and isinstance(d.get("ts"), (int, float)): out.append(d)
    return out


def elapsed(d):
    """The seconds an event says its review ran before it: a review's `elapsed`, else the event's own."""
    r = d.get("review") if isinstance(d.get("review"), dict) else d
    try: return float(r.get("elapsed") or 0)
    except (TypeError, ValueError): return 0.0


def run_intervals(evs, now):
    """The review rounds and fix entries of the run's record, as (start, end) pairs."""
    if not evs: return []
    end_open = min(now, max(d["ts"] for d in evs) + QUIET)
    out, review, fix, fixed = [], {}, {}, set()   # review: node -> start of its open round; fix: (node, entry) -> its call start
    for d in evs:
        ev, node, ts = d.get("event"), d.get("node"), d["ts"]
        if ev in ("review_try", "review_progress", "review_round"):
            s = ts - elapsed(d)
            review[node] = min(review.get(node, s), s)
            if ev == "review_round":
                out.append((review.pop(node), ts))
                if d.get("verdict") == "fix": fixed.add((node, (d.get("round") or 0) + 1))
        elif ev == "call_start" and (node, d.get("entry")) in fixed:
            fix.setdefault((node, d.get("entry")), ts)
        elif ev == "outcome" and (node, d.get("entry")) in fix:
            out.append((fix.pop((node, d.get("entry"))), ts))
    out += [(s, end_open) for s in [*review.values(), *fix.values()]]
    return out


def fix_run_interval(evs, now):
    """A childcc fix run, from its first event to its verdict, else open."""
    if not evs: return []
    v = next((d["ts"] for d in evs if d.get("event") == "verdict"), None)
    return [(evs[0]["ts"], v if v is not None else min(now, max(d["ts"] for d in evs) + QUIET))]


def gate_intervals(state):
    """The gate and suite waits the machine recorded as (start - wait, start) pairs.

    gate-history holds `<finish> <kind> <result> <sha> <start> ... <wait> ...` (gate.sh); child-suite-history holds
    `<finish> <start> <wait> ...` (child-gate.sh). A row with no readable start or wait, or a wait of 0, adds nothing.
    """
    out = []
    for name, si, wi in (("gate-history", 4, 17), ("child-suite-history", 1, 2)):
        try: lines = (pathlib.Path(state) / name).read_text(errors="replace").splitlines()
        except (OSError, TypeError): continue
        for l in lines:
            f = l.split("\t")
            if len(f) <= max(si, wi): continue
            try: s, w = int(f[si]), int(f[wi])
            except ValueError: continue
            if s > 0 and w > 0: out.append((s - w, s))
    return out


def paused(t0, now, run, fixes=(), state=None):
    """The measure of the union of every pause interval, clipped to [t0, now]."""
    spans = run_intervals(events(run), now)
    for f in fixes: spans += fix_run_interval(events(f), now)
    if state: spans += gate_intervals(state)
    total, cur = 0.0, None
    for s, e in sorted((max(s, t0), min(e, now)) for s, e in spans):
        if e <= s: continue
        if cur and s <= cur[1]: cur = (cur[0], max(cur[1], e)); continue
        if cur: total += cur[1] - cur[0]
        cur = (s, e)
    if cur: total += cur[1] - cur[0]
    return int(total)


if __name__ == "__main__":
    if len(sys.argv) < 4: print(__doc__.strip(), file=sys.stderr); sys.exit(2)
    print(paused(float(sys.argv[1]), float(sys.argv[2]), sys.argv[3], sys.argv[4:], os.environ.get("SG_STATE")))
