#!/usr/bin/env python3
"""The labeled set of gate runs (#1691): one row per gate of $SG_STATE/gate-history that carries a load reading.

usage: gate_load_set.py [state dir]

Each row is tab-separated: finish epoch, tree sha, kind, scope, outcome, load at the start, load at the finish, and the
outcome of the next gate of the same kind on the same tree (`-` when none ran). A red under a high load whose rerun passed
is the case a load bound would turn to unproven; this set is what supports such a bound before it enters a gate
(ADR-0012, ADR-0018). A line from before the load fields is no row.
"""
import os
import sys
from pathlib import Path

HEADER = "finish\tsha\tkind\tscope\toutcome\tload_start\tload_finish\trerun"


def rows(state):
    try:
        lines = (Path(state) / "gate-history").read_text(errors="replace").splitlines()
    except OSError:
        lines = []
    gates = [f for f in (line.split("\t") for line in lines) if len(f) >= 16]
    out = []
    for i, f in enumerate(gates):
        rerun = next((g[7] for g in gates[i + 1:] if g[3] == f[3] and g[1] == f[1]), "-")
        out.append((f[0], f[3], f[1], f[6], f[7], f[14], f[15], rerun))
    return out


def main(argv):
    state = argv[0] if argv else os.environ.get("SG_STATE", "/tmp/sg-state")
    print(HEADER)
    for r in rows(state):
        print("\t".join(r))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
