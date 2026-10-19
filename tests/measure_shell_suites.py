"""Measure each shell suite, so `tests/shell_suites_seconds.txt` holds a measured time for every suite (#629).

Each suite runs serially, `--runs` times (default 3), through `shell_suites.run`, the same seam the runner uses. A failed
run is rejected; the row is the median of the green runs. With no name it measures every suite; with names, only those.
It prints sorted `name seconds` rows. `--write` puts them into the list, and keeps the rows of suites it did not measure.
Measure on a quiet machine: under load a suite takes longer, and the list then marks it heavier than it is.

    python3 -m tests.measure_shell_suites [--runs N] [--write] [test-x.sh ...]
"""
import argparse
import statistics
import subprocess
import sys
import time

from tests import shell_suites

HEADER = (
    "# Measured run time in seconds of each shell suite, one 'name seconds' per line; the runner reads it (tests/shell_suites.py).\n"
    f"# {shell_suites.HEAVY_SECONDS:g} s or more: heavy. Over {shell_suites.SLOW_SECONDS:g} s: also a slow reason. "
    "Every suite is listed; a test fails on one that is not.\n"
    "# Rows come from `python3 -m tests.measure_shell_suites --write [name ...]`, run serially on a quiet machine.\n"
)


def measure(suite, runs):
    """The median seconds of the green runs of one suite, or None when no run was green."""
    times = []
    for _ in range(runs):
        start = time.perf_counter()
        try:
            done = shell_suites.run(suite)
        except subprocess.TimeoutExpired:
            continue
        elapsed = time.perf_counter() - start
        if done.returncode == 0 and not any(line.startswith("FAIL") for line in done.stdout.splitlines()):
            times.append(elapsed)
    return statistics.median(times) if times else None


def render(rows):
    return HEADER + "".join(f"{name} {seconds:.2f}\n" for name, seconds in sorted(rows.items()))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("names", nargs="*", help="suites to measure; none measures every suite")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--write", action="store_true", help="write the rows into tests/shell_suites_seconds.txt")
    args = parser.parse_args(argv)
    suites = shell_suites.discover()
    if args.names:
        known = {p.name: p for p in suites}
        unknown = [n for n in args.names if n not in known]
        if unknown:
            parser.error("no such suite: " + " ".join(unknown))
        suites = [known[n] for n in args.names]
    rows, failed = {}, []
    for suite in suites:
        seconds = measure(suite, args.runs)
        if seconds is None:
            failed.append(suite.name)
            print(f"{suite.name} FAILED every run", file=sys.stderr, flush=True)
        else:
            rows[suite.name] = seconds
            print(f"{suite.name} {seconds:.2f}", flush=True)
    if args.write:
        merged = {**shell_suites.measured(), **rows}
        present = {p.name for p in shell_suites.discover()}
        merged = {name: s for name, s in merged.items() if name in present}
        tmp = shell_suites.SECONDS_LIST.with_suffix(".tmp")
        tmp.write_text(render(merged))
        tmp.replace(shell_suites.SECONDS_LIST)   # atomic, so a stopped run never leaves half a list
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
