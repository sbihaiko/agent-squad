#!/usr/bin/env python3
"""Token burners: who spent the most tokens in a window. Stdlib only, no network, never crashes on bad input.

usage: tokens.py burners <state-dir> [--checkout DIR] [--hours N] [--threshold N] [--json]
Sources: <state-dir>/token-usage.tsv (ts, consumer, model, in, out, cache_read[, mark]) and
<checkout>/runs/*/record.jsonl (and worktrees/{run,child}/*/runs/*) `outcome` events (consumer run:<folder>[:<node|child>]).
Total tokens = in + out + cache_read. Threshold: --threshold, else $SG_TOKEN_ALERT, else DEFAULT_THRESHOLD.
The total is a FLOOR, never a measure: a ledger line whose 7th field is set (the word review.sh writes is `no-usage`)
carried no token count from a model that reports one -- a refused call, a zeroed usage -- so it weighs 0 and the burn
understates the true spend. `uncounted` names them: the window total in the JSON, and per consumer in the JSON rows and
in the text row's 5th field. A model that never reports (grok, agy, codex) carries no mark and its plain path holds no count,
so a ledger line of a non-claude model with 0 0 0 and no mark is `unreported`: the call is named and counted, its tokens are
not. Both kinds make the total a floor (`floor` in the JSON, a `# floor` line in the text), never a measure.

usage: tokens.py verdict <state-dir> [--checkout DIR] [--hours N] [--cap N] [--cache-weight W]
The burn-fix criterion as one answer on stdout (JSON) and in the exit code: `pass` (0) when the total is under the cap
(default 1500000) or the top burner is not a review call (consumer `review:*`); `fail` (1) otherwise; `not-proven` (2)
for an empty window, which is never a pass.
"""
import argparse
import glob
import json
import os
import sys
import time
from datetime import datetime

DEFAULT_THRESHOLD = 5000000


def _epoch(v):
    if isinstance(v, (int, float)):
        return float(v)
    return datetime.fromisoformat(str(v)).timestamp()


def _n(v):
    return int(float(v))


def _rows(state, checkout, cache_weight=1.0):
    """Yields (ts, consumer, model, weighted tokens, uncounted 0/1, unreported 0/1).
    cache_weight: what one cache-read token counts as; the guard passes the provider's cache-read price ratio (#495)."""
    try:
        with open(os.path.join(state, "token-usage.tsv"), errors="replace") as f:
            for line in f:
                p = line.rstrip("\n").split("\t")
                try:
                    if len(p) >= 6:
                        unc = 1 if len(p) > 6 and p[6].strip() else 0   # a 7th field is the mark of a call with no count
                        raw = _n(p[3]) + _n(p[4]) + _n(p[5])
                        unr = 1 if raw == 0 and not unc and not p[2].startswith("claude") else 0
                        yield _epoch(p[0]), p[1], p[2], _n(p[3]) + _n(p[4]) + int(round(_n(p[5]) * cache_weight)), unc, unr
                except (ValueError, OverflowError):
                    pass
    except OSError:
        pass
    if not checkout:
        return
    co = glob.escape(checkout)   # a run folder is <checkout>/runs/<run>, or in a run or child worktree (#580, #585)
    for rec in sorted(r for rel in (("runs",), ("worktrees", "run", "*", "runs"), ("worktrees", "child", "*", "runs"))
                      for r in glob.glob(os.path.join(co, *rel, "*", "record.jsonl"))):
        folder = os.path.basename(os.path.dirname(rec))
        try:
            with open(rec, errors="replace") as f:
                for line in f:
                    try:
                        e = json.loads(line)
                        if not isinstance(e, dict) or e.get("event") != "outcome":
                            continue
                        who = e.get("node") or e.get("child")
                        c = f"run:{folder}" + (f":{who}" if who else "")
                        cr = _n(e.get("cache_read") or 0)   # an outcome's tokens_in holds its cache reads (squad/sdk_adapter.py); a ledger line's does not
                        t = _n(e.get("tokens_in") or 0) - cr + _n(e.get("tokens_out") or 0) + int(round(cr * cache_weight))
                        yield _epoch(e["ts"]), c, str(e.get("model") or "?"), t, 0, 0
                    except (ValueError, TypeError, KeyError, OverflowError):
                        pass
        except OSError:
            pass


def burners(state, checkout, hours, cache_weight=1.0):
    return window(state, checkout, hours, cache_weight)[0]


def window(state, checkout, hours, cache_weight=1.0):
    """`(burners, unreported)`: the sorted consumers, and how many calls of a model that never reports carried no count."""
    cut = time.time() - hours * 3600
    agg = {}
    unreported = 0
    for ts, c, m, t, unc, unr in _rows(state, checkout, cache_weight):
        if ts < cut:
            continue
        unreported += unr
        a = agg.setdefault(c, {"consumer": c, "tokens": 0, "calls": 0, "uncounted": 0, "models": []})
        a["tokens"] += t
        a["calls"] += 1
        a["uncounted"] += unc
        if m not in a["models"]:
            a["models"].append(m)
    return sorted(agg.values(), key=lambda a: (-a["tokens"], a["consumer"])), unreported


DEFAULT_CAP = 1500000


def verdict(rows, unreported, cap=DEFAULT_CAP):
    """The burn-fix criterion over one window: pass, fail or not-proven, with the facts it rests on."""
    total = sum(r["tokens"] for r in rows)
    top = rows[0]["consumer"] if rows else None
    floor = bool(unreported or sum(r["uncounted"] for r in rows))
    if not rows:
        v, why = "not-proven", "the ledger holds no call in the window, so nothing was measured"
    elif total < cap:
        v, why = "pass", f"total {total} is under the cap {cap}"
    elif not top.startswith("review:"):
        v, why = "pass", f"the top burner {top} is not a review call"
    else:
        v, why = "fail", f"total {total} is at or over the cap {cap} and the top burner {top} is a review call"
    if floor and rows:
        why += "; the total is a floor, not a measure"
    return {"verdict": v, "total": total, "cap": cap, "top": top, "floor": floor, "unreported": unreported, "reason": why}


def _floor_note(rows, unreported):
    n = unreported + sum(r["uncounted"] for r in rows)
    return f"floor, not a measure: {n} calls carried no token count" if n else ""


def main(argv):
    ap = argparse.ArgumentParser(prog="tokens.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("burners")
    b.add_argument("state")
    b.add_argument("--checkout")
    b.add_argument("--hours", type=float, default=24)
    b.add_argument("--threshold", type=int)
    b.add_argument("--json", action="store_true")
    b.add_argument("--cache-weight", type=float, default=1.0, help="what one cache-read token counts as (the guard passes 0.1)")
    v = sub.add_parser("verdict")
    v.add_argument("state")
    v.add_argument("--checkout")
    v.add_argument("--hours", type=float, default=24)
    v.add_argument("--cap", type=int, default=DEFAULT_CAP)
    v.add_argument("--cache-weight", type=float, default=1.0)
    a = ap.parse_args(argv)
    if a.cmd == "verdict":
        out = verdict(*window(a.state, a.checkout, a.hours, a.cache_weight), a.cap)
        print(json.dumps(out))
        return {"pass": 0, "fail": 1}.get(out["verdict"], 2)
    thr = a.threshold
    if thr is None:
        try:
            thr = int(os.environ.get("SG_TOKEN_ALERT", ""))
        except ValueError:
            thr = DEFAULT_THRESHOLD
    rows, unreported = window(a.state, a.checkout, a.hours, a.cache_weight)
    total = sum(r["tokens"] for r in rows)
    uncounted = sum(r["uncounted"] for r in rows)
    if a.json:
        over = total >= thr
        brief = ""
        if over:
            top = ", ".join(f"{r['consumer']} {r['tokens']}" for r in rows[:3])
            brief = f"Token use {total} in {a.hours:g}h is over {thr}: top {top}"
            if uncounted or unreported:
                brief += f"; the total is a floor, {uncounted + unreported} calls carried no count"
        h = int(a.hours) if a.hours == int(a.hours) else a.hours
        print(json.dumps({"window_hours": h, "threshold": thr, "total": total, "over": over,
                          "uncounted": uncounted, "unreported": unreported, "floor": bool(uncounted or unreported),
                          "burners": rows, "brief": brief}))
    elif not rows:
        print("-")
    else:
        for r in rows:
            print(f"{r['tokens']}\t{r['consumer']}\t{','.join(r['models'])}\t{r['calls']}\t{r['uncounted']}")
        if _floor_note(rows, unreported):
            print("# " + _floor_note(rows, unreported))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
