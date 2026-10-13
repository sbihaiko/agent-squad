"""The one writer of a killed run's verdict: a run whose process is gone, with no verdict of its own, gets one.

A run that is killed (the operator, a session restart, a crash) writes no more events, so its record has no verdict and a reader
sees a stalled run for ever. The heartbeat and the janitor call this module; nothing else writes that verdict. The verdict is the
`failed` one that `squad.verdict.killed` builds, and it goes through the record's atomic append (ADR-0030).
usage: python3 -m squad.killed <run folder> [reason]
"""
import sys
from pathlib import Path

from squad import record
from squad import verdict as V

REASON = "killed/no-process"


def mark_killed(run_dir, reason=REASON):
    """Append the killed verdict to the run's record. Return True when it wrote one; False when the folder has no record or the
    record already ends in a verdict. A corrupt record raises `RecordError`: it is not ours to rewrite."""
    path = Path(run_dir) / "record.jsonl"
    if not path.exists():
        return False
    r = record.Record(path)
    if r.events and r.events[-1]["event"] == "verdict":
        return False
    r.append("verdict", **V.killed(reason).event_fields())
    return True


def main(argv):
    if not 1 <= len(argv) <= 2:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    try:
        wrote = mark_killed(*argv)
    except record.RecordError as e:
        print(f"{argv[0]}: {e}", file=sys.stderr)
        return 1
    if wrote:
        print(f"marked {argv[0]} killed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
