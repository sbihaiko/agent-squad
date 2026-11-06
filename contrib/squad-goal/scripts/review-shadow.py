#!/usr/bin/env python3
"""The sample rule and the ledger line of a grok shadow review (#1403); it reads and appends no other state."""

from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

FULL_SAMPLE = 30        # every grok verdict with a Blocking finding is shadowed until this many samples exist
RATE_ONE_IN = 4         # after that, one such verdict in four is shadowed (25%)
CAP_PER_DAY = 10        # a day's shadow runs, never more: a shadow is a whole extra review call


def _rows(path: Path) -> list[dict]:
    """The ledger's readable rows; a missing file is no row, and a broken line is skipped."""
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return []
    rows = []
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _shadows(rows: list[dict]) -> list[dict]:
    return [r for r in rows if r.get("shadow") is True]


def _today() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")


def decide(shadows: list[dict], blocking: int, today: str) -> tuple[bool, str]:
    """(run, why): the sample rule over the shadow lines already in the ledger."""
    if blocking < 1:
        return False, "skip: the grok verdict has no Blocking finding, so no shadow is spent on it"
    runs_today = sum(1 for r in shadows if str(r.get("ts", "")).startswith(today))
    if runs_today >= CAP_PER_DAY:
        return False, f"skip: {runs_today} shadow runs today, the cap is {CAP_PER_DAY}"
    samples = len(shadows)
    if samples < FULL_SAMPLE:
        return True, f"run: every Blocking verdict until {FULL_SAMPLE} samples ({samples} so far)"
    if (samples - FULL_SAMPLE) % RATE_ONE_IN == 0:
        return True, f"run: one in {RATE_ONE_IN} at {samples} samples"
    return False, f"skip: one in {RATE_ONE_IN} after {FULL_SAMPLE} samples ({samples} so far)"


def record(path: Path, key: str, head: str, model: str, verdict: str, blocking: int, debt: int, effort: str) -> None:
    """Append one shadow line: the next entry's answer on grok's head, never a published review."""
    row = {"ts": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "key": key, "head": head, "model": model, "effort": effort, "verdict": verdict,
           "shadow": True, "blocking": blocking, "debt": debt}
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="The sampling rule and ledger line of a grok shadow review.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("decide", help="exit 0 to run a shadow, 1 to skip")
    d.add_argument("ledger", type=Path)
    d.add_argument("--blocking", type=int, required=True)
    r = sub.add_parser("record", help="append one shadow line")
    r.add_argument("ledger", type=Path)
    r.add_argument("--key", required=True)
    r.add_argument("--head", required=True)
    r.add_argument("--model", required=True)
    r.add_argument("--verdict", required=True)
    r.add_argument("--blocking", type=int, required=True)
    r.add_argument("--debt", type=int, required=True)
    r.add_argument("--effort", default="")
    args = ap.parse_args(argv)
    if args.cmd == "decide":
        run, why = decide(_shadows(_rows(args.ledger)), args.blocking, _today())
        print(why)
        return 0 if run else 1
    record(args.ledger, args.key, args.head, args.model, args.verdict, args.blocking, args.debt, args.effort)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
