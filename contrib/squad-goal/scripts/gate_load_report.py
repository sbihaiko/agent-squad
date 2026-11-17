#!/usr/bin/env python3
"""Report the labeled gate-load set (#1691, #1757) from its input file alone. It chooses no load bound.

usage: gate_load_report.py <gate-load-set file>

The input file is written by gate_load_set_write.sh: two header lines (# source-command, # written), then the
gate_load_set.py rows. A red row is an outcome of fail. Its kind is red-passed-on-rerun when the next gate of the same
kind on the same tree passed, and red-stayed-red when it failed again. A row without a load reading or without a rerun
is no row of the set.
"""
import sys
from pathlib import Path

HEADER = "finish\tsha\tkind\tscope\toutcome\tload_start\tload_finish\trerun"
MIN_ROWS = 5
NO_INPUT = "no input file: no load is named and no bound is chosen"


def _is_load(text):
    try:
        float(text)
    except ValueError:
        return False
    return True


def report(path):
    result = {
        "source": None,
        "written": None,
        "set_size": 0,
        "red_passed_on_rerun": 0,
        "red_stayed_red": 0,
        "passed_loads": [],
        "sufficient": False,
        "reason": "",
    }
    try:
        lines = Path(path).read_text(errors="replace").splitlines()
    except OSError:
        result["reason"] = NO_INPUT
        return result
    passed_loads = set()
    for line in lines:
        if line.startswith("# source-command: "):
            result["source"] = line[len("# source-command: "):]
        elif line.startswith("# written: "):
            result["written"] = line[len("# written: "):]
        elif line.startswith("#") or not line or line == HEADER:
            continue
        else:
            fields = line.split("\t")
            if len(fields) != 8 or fields[4] != "fail" or not _is_load(fields[5]) or fields[7] not in ("pass", "fail"):
                continue
            if fields[7] == "pass":
                result["red_passed_on_rerun"] += 1
                passed_loads.add(fields[5])
            else:
                result["red_stayed_red"] += 1
    result["set_size"] = result["red_passed_on_rerun"] + result["red_stayed_red"]
    result["passed_loads"] = sorted(passed_loads, key=float)
    result["sufficient"] = result["red_passed_on_rerun"] >= MIN_ROWS and result["red_stayed_red"] >= MIN_ROWS
    if not result["sufficient"]:
        result["reason"] = f"fewer than {MIN_ROWS} rows of one kind"
    return result


def main(argv):
    if len(argv) != 1:
        print("usage: gate_load_report.py <gate-load-set file>", file=sys.stderr)
        return 2
    result = report(argv[0])
    print(f"source: {result['source'] or '-'}")
    print(f"written: {result['written'] or '-'}")
    print(f"set_size: {result['set_size']}")
    print(f"red_passed_on_rerun: {result['red_passed_on_rerun']}")
    print(f"red_stayed_red: {result['red_stayed_red']}")
    print(f"passed_loads: {' '.join(result['passed_loads']) or '-'}")
    print(f"sufficient: {'yes' if result['sufficient'] else 'no'}")
    if result["reason"]:
        print(f"reason: {result['reason']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
