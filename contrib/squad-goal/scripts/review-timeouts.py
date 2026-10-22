"""Omit a review model that times out in a row, for a window, by time (#749).

One JSON file in the state directory holds, per review model, its streak of consecutive timeouts and, once the bound is
reached, the epoch the omission ends. A read past that epoch drops the streak, so the model is tried again with no state
edit. Reads and writes hold a lock on the directory; the file is replaced by a rename (ADR-0030).
usage: review-timeouts.py <state-file> omitted|timeout|clear <model> --bound N --window S [--now T]
omitted: prints the reason and exits 0 while the model is omitted, else exits 1. timeout: counts one; prints the reason
when this one omits the model. clear: an answered review or a turn with another outcome ends the streak.
"""
import argparse
import fcntl
import json
import os
import pathlib
import time


def _load(path):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return {}


def _store(path, data):
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, sort_keys=True) + "\n")
    os.replace(tmp, path)


def _live(entry, now):
    until = entry.get("until")
    return {} if until is not None and now >= until else entry


def _reason(entry):
    until = time.strftime("%F %T", time.localtime(entry["until"]))
    return f"{entry['streak']} consecutive timeouts; omitted until {until}"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Omit a review model that times out in a row (#749).")
    ap.add_argument("state")
    ap.add_argument("command", choices=("omitted", "timeout", "clear"))
    ap.add_argument("model")
    ap.add_argument("--bound", type=int, required=True)
    ap.add_argument("--window", type=int, required=True)
    ap.add_argument("--now", type=int)
    a = ap.parse_args(argv)
    now = int(time.time()) if a.now is None else a.now
    path = pathlib.Path(a.state)
    lock = os.open(path.parent, os.O_RDONLY)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = _load(path)
        entry = _live(data.get(a.model, {}), now)
        status = 0
        if a.command == "omitted":
            status = 0 if "until" in entry else 1
            if status == 0:
                print(_reason(entry))
        elif a.command == "timeout":
            entry = {"streak": entry.get("streak", 0) + 1}
            if entry["streak"] >= a.bound:
                entry["until"] = now + a.window
                print(_reason(entry))
        else:
            entry = {}
        new = dict(data)
        if entry:
            new[a.model] = entry
        else:
            new.pop(a.model, None)
        if new != data:
            _store(path, new)
        return status
    finally:
        os.close(lock)


if __name__ == "__main__":
    raise SystemExit(main())
