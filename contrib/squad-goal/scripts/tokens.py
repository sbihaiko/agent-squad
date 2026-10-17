#!/usr/bin/env python3
"""Token burners: who spent the most tokens in a window. Stdlib only, no network, never crashes on bad input.

usage: tokens.py burners <state-dir> [--checkout DIR] [--hours N] [--threshold N] [--json]
Sources: <state-dir>/token-usage.tsv (ts, consumer, model, in, out, cache_read[, mark]) and
<checkout>/runs/*/record.jsonl `outcome` events (consumer run:<folder>[:<node|child>]).
Total tokens = in + out + cache_read. Threshold: --threshold, else $SG_TOKEN_ALERT, else DEFAULT_THRESHOLD.
The total is a FLOOR, never a measure: a ledger line whose 7th field is set (the word review.sh writes is `no-usage`)
carried no token count from a model that reports one -- a refused call, a zeroed usage -- so it weighs 0 and the burn
understates the true spend. `uncounted` names them: the window total in the JSON, and per consumer in the JSON rows and
in the text row's 5th field. A model that never reports (grok, agy, codex) carries no mark: its silence is not a floor.
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
    """cache_weight: what one cache-read token counts as; the guard passes the provider's cache-read price ratio (#495)."""
    try:
        with open(os.path.join(state, "token-usage.tsv"), errors="replace") as f:
            for line in f:
                p = line.rstrip("\n").split("\t")
                try:
                    if len(p) >= 6:
                        unc = 1 if len(p) > 6 and p[6].strip() else 0   # a 7th field is the mark of a call with no count
                        yield _epoch(p[0]), p[1], p[2], _n(p[3]) + _n(p[4]) + int(round(_n(p[5]) * cache_weight)), unc
                except (ValueError, OverflowError):
                    pass
    except OSError:
        pass
    if not checkout:
        return
    for rec in sorted(glob.glob(os.path.join(glob.escape(checkout), "runs", "*", "record.jsonl"))):
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
                        t = _n(e.get("tokens_in") or 0) + _n(e.get("tokens_out") or 0) + int(round(_n(e.get("cache_read") or 0) * cache_weight))
                        yield _epoch(e["ts"]), c, str(e.get("model") or "?"), t, 0
                    except (ValueError, TypeError, KeyError, OverflowError):
                        pass
        except OSError:
            pass


def burners(state, checkout, hours, cache_weight=1.0):
    cut = time.time() - hours * 3600
    agg = {}
    for ts, c, m, t, unc in _rows(state, checkout, cache_weight):
        if ts < cut:
            continue
        a = agg.setdefault(c, {"consumer": c, "tokens": 0, "calls": 0, "uncounted": 0, "models": []})
        a["tokens"] += t
        a["calls"] += 1
        a["uncounted"] += unc
        if m not in a["models"]:
            a["models"].append(m)
    return sorted(agg.values(), key=lambda a: (-a["tokens"], a["consumer"]))


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
    a = ap.parse_args(argv)
    thr = a.threshold
    if thr is None:
        try:
            thr = int(os.environ.get("SG_TOKEN_ALERT", ""))
        except ValueError:
            thr = DEFAULT_THRESHOLD
    rows = burners(a.state, a.checkout, a.hours, a.cache_weight)
    total = sum(r["tokens"] for r in rows)
    uncounted = sum(r["uncounted"] for r in rows)
    if a.json:
        over = total >= thr
        brief = ""
        if over:
            top = ", ".join(f"{r['consumer']} {r['tokens']}" for r in rows[:3])
            brief = f"Token use {total} in {a.hours:g}h is over {thr}: top {top}"
            if uncounted:
                brief += f"; the total is a floor, {uncounted} calls carried no count"
        h = int(a.hours) if a.hours == int(a.hours) else a.hours
        print(json.dumps({"window_hours": h, "threshold": thr, "total": total, "over": over,
                          "uncounted": uncounted, "burners": rows, "brief": brief}))
    elif not rows:
        print("-")
    else:
        for r in rows:
            print(f"{r['tokens']}\t{r['consumer']}\t{','.join(r['models'])}\t{r['calls']}\t{r['uncounted']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
