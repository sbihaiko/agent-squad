#!/usr/bin/env python3
"""Tuning table for one UTC window, read from SG_STATE and SG_CHECKOUT only; makes no GitHub call.

Cores come from SG_CORES, else os.cpu_count().
"""
import argparse
import datetime as dt
import json
import os
import re
import statistics
from pathlib import Path

UTC = dt.timezone.utc
STAMP = r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)"
IDLE = re.compile(rf"^{STAMP} FRONTIER idle: ")
NO_SLOT = re.compile(r"(\d+) no slot")
# A card the pass took and could not label: the writing of ready-for-agent was refused, so the card waits with its own
# reason (#1676). The frontier's idle line names it "triage failed", one count per pass.
TRIAGE_FAILED = re.compile(r"(\d+) triage failed")
HOLD = re.compile(rf"^{STAMP} DISPATCH HOLD: ")
GATE_HOLD = re.compile(r"promotion proof|nightly")
# The gate-history caller field (14th): who asked for the gate. A row without it, or with another word, is unknown.
CALLERS = ("child", "entry", "landing", "tip", "other", "unknown")
LOAD = re.compile(rf"^{STAMP} LOAD (\d+(?:\.\d+)?) (\d+(?:\.\d+)?) (\d+(?:\.\d+)?)$")


def gate_mode(parts):
    """The entry mode of one split gate-history row (#1798), '' for a row from before it. The mode ends a 17-field row; a row
    that carries the queue fields (#1856) ends with its reuse word, five fields after the mode, and a row that carries the
    runner fields (#1927) ends with its CI run id, two fields after that."""
    if len(parts) >= 24 and parts[-2] in ("ci", "local") and parts[-3] in ("yes", "no") and parts[-8] in ("gated", "reused", "skipped"):
        return parts[-8]
    if len(parts) >= 22 and parts[-1] in ("yes", "no") and parts[-6] in ("gated", "reused", "skipped"):
        return parts[-6]
    if len(parts) >= 17 and parts[-1] in ("gated", "reused", "skipped"):
        return parts[-1]
    return ""


def stamp(text):
    try:
        return dt.datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC).timestamp()
    except ValueError:
        return None


def lines(path):
    try:
        return path.read_text(errors="replace").splitlines()
    except OSError:
        return []


def num(x):
    return str(int(x)) if float(x).is_integer() else f"{x:.1f}"


def ratio(a, b):
    return a / b if b else 0.0


def runs(root):
    found = []
    for rec in sorted((root / "runs").glob("run-*/record.jsonl")):
        events = []
        for line in lines(rec):
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if isinstance(e, dict) and isinstance(e.get("ts"), (int, float)):
                events.append(e)
        if not events:
            continue
        starts = [e for e in events if e.get("event") == "run_start"]
        start = starts[0]["ts"] if starts else min(e["ts"] for e in events)
        end = max(e["ts"] for e in events)
        caps = starts[0].get("caps") if starts and isinstance(starts[0].get("caps"), dict) else {}
        settings = starts[0].get("project_settings") if starts else None
        sha = settings.get("sha256") if isinstance(settings, dict) else None
        found.append({"id": rec.parent.name, "start": start, "end": end, "caps": caps, "sha": sha})
    return found


def main():
    p = argparse.ArgumentParser(description="Tuning table for one UTC window.")
    p.add_argument("since", type=dt.datetime.fromisoformat)
    p.add_argument("until", nargs="?", type=dt.datetime.fromisoformat)
    args = p.parse_args()
    state, checkout = os.environ.get("SG_STATE"), os.environ.get("SG_CHECKOUT")
    if not state or not checkout:
        p.error("SG_STATE and SG_CHECKOUT must be set")

    since = args.since.replace(tzinfo=args.since.tzinfo or UTC).timestamp()
    until_dt = args.until or dt.datetime.now(UTC)
    until = until_dt.replace(tzinfo=until_dt.tzinfo or UTC).timestamp()
    st = Path(state)

    def inside(t):
        return t is not None and since <= t < until

    print(f"window: {dt.datetime.fromtimestamp(since, UTC):%F %T} to "
          f"{dt.datetime.fromtimestamp(until, UTC):%F %T} UTC")

    landings = 0
    for line in lines(st / "landed.tsv"):
        parts = line.split("\t")
        if len(parts) >= 4 and inside(_float(parts[0])):
            landings += 1

    live = []
    for r in runs(Path(checkout)):
        if r["start"] < until and r["end"] > since:
            live.append(r)
    hours = sum(min(r["end"], until) - max(r["start"], since) for r in live) / 3600

    print(f"landings: {landings}")
    print(f"live-run hours: {hours:.2f}")
    print(f"landings per live-run hour: {ratio(landings, hours):.2f}")

    passes = held_slot = held_gate = held_triage = 0
    for line in lines(st / "cycle.log"):
        m = IDLE.match(line)
        if m and inside(stamp(m.group(1))):
            passes += 1
            n = NO_SLOT.search(line)
            if n and int(n.group(1)) > 0:
                held_slot += 1
            t = TRIAGE_FAILED.search(line)
            if t and int(t.group(1)) > 0:
                held_triage += 1
            continue
        m = HOLD.match(line)
        if m and inside(stamp(m.group(1))) and GATE_HOLD.search(line):
            held_gate += 1

    print(f"dispatch passes: {passes}")
    print(f"passes held for a run slot: {held_slot}")
    print(f"share held for a run slot: {ratio(held_slot, passes):.2f}")
    print(f"passes held for the gate: {held_gate}")
    print(f"passes held for a triage write: {held_triage}")

    durations = []
    by_caller = {c: 0.0 for c in CALLERS}
    entry_last = {}  # sha -> (finish, verdict) of its last entry row in the window
    for line in lines(st / "gate-history"):
        parts = line.split("\t")
        if len(parts) < 5:
            continue
        if gate_mode(parts) in ("reused", "skipped"):
            continue   # a gate that ran no battery (#1798) is no gate time and no entry attempt
        finish, start = _float(parts[0]), _float(parts[4])
        if start is not None and inside(finish):
            durations.append(finish - start)
            caller = parts[13] if len(parts) > 13 and parts[13] in CALLERS else "unknown"
            by_caller[caller] += finish - start
            if caller == "entry":
                verdict = parts[7] if len(parts) > 7 and parts[7] else parts[2]
                if parts[3] not in entry_last or finish >= entry_last[parts[3]][0]:
                    entry_last[parts[3]] = (finish, verdict)
    entry_shas = set(entry_last)
    entry_red = {k for k, v in entry_last.items() if v[1] == "fail"}
    print(f"gate runs: {len(durations)}")
    print(f"gate total seconds: {num(sum(durations))}")
    print(f"gate median seconds: {num(statistics.median(durations)) if durations else 0}")
    for c in CALLERS:
        print(f"gate lock seconds, {c}: {num(by_caller[c])}")
    print(f"entry gate refused share: {ratio(len(entry_red), len(entry_shas)):.2f} "
          f"({len(entry_red)} of {len(entry_shas)} children)")

    cooldown = 0
    try:
        cd = json.loads((st / "gh-cooldown.json").read_text())
        at, cd_until = float(cd["at"]), float(cd["until"])
    except (OSError, ValueError, KeyError, TypeError):
        pass
    else:
        cooldown = max(0, min(cd_until, until) - max(at, since))
    print(f"cooldown seconds: {num(cooldown)}")

    cores = int(os.environ.get("SG_CORES") or os.cpu_count() or 1)
    valid = high = 0
    for line in lines(st / "load.log"):
        m = LOAD.match(line)
        if not m or not inside(stamp(m.group(1))):
            continue
        valid += 1
        if float(m.group(3)) >= cores:
            high += 1
    print(f"share of five-minute load samples at or above the core count: {ratio(high, valid):.2f}")

    sets, hashes = [], []
    for r in live:
        caps = " ".join(f"caps.{k}={v}" for k, v in sorted(r["caps"].items()))
        if caps and caps not in sets:
            sets.append(caps)
        if isinstance(r["sha"], str) and r["sha"][:12] not in hashes:
            hashes.append(r["sha"][:12])
    hashes.sort()
    print(f"caps in force: {' | '.join(sets) if sets else 'none'}")
    print(f"settings hashes seen: {' '.join(hashes) if hashes else 'none'}")
    if len(hashes) > 1:
        print(f"FLAG: window spans {len(hashes)} settings hashes; its numbers are not one configuration")


def _float(text):
    try:
        return float(text)
    except ValueError:
        return None


if __name__ == "__main__":
    main()
