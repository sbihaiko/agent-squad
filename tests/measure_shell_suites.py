"""Measure each shell suite, so every suite carries a measured time in its own header (#629).

Each suite runs serially, `--runs` times (default 3), through `shell_suites.run`, the same seam the runner uses. A failed
run is rejected; the row is the median of the green runs. With no name it measures every suite; with names, only those.
It prints sorted `name seconds` rows. `--write` writes each row into that suite's own header, `# seconds: <n>`, so it
rewrites no file but the ones it measured. Measure on a quiet machine: under load a suite takes longer, and the header
then marks it heavier than it is.

    python3 -m tests.measure_shell_suites [--runs N] [--write] [test-x.sh ...]
"""
import argparse
import statistics
import subprocess
import sys
import time

from tests import shell_suites


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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("names", nargs="*", help="suites to measure; none measures every suite")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--write", action="store_true", help="write each row into the header of the suite it measured")
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
        by_name = {suite.name: suite for suite in suites}
        for name, seconds in rows.items():
            shell_suites.set_seconds(by_name[name], seconds)   # one suite's own file, never a shared line
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
