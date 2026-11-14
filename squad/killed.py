"""The one writer of a killed run's verdict: a run whose process is gone, with no verdict of its own, gets one.

A run that is killed (the operator, a session restart, a crash) writes no more events, so its record has no verdict and a reader
sees a stalled run for ever. The heartbeat and the janitor call this module; nothing else writes that verdict. The verdict is the
`failed` one that `squad.verdict.killed` builds, and it goes through the record's atomic append (ADR-0030).

A SIGTERM, SIGHUP or SIGINT ends an engine that records a `signal` event first (`recording_signals`), so the verdict names the
signal. A run that ends with no such event keeps `killed/no-process`.
usage: python3 -m squad.killed <run folder> [reason]
"""
import contextlib
import os
import signal
import sys
import threading
from pathlib import Path

from squad import record
from squad import verdict as V

REASON = "killed/no-process"
SIGNALS = (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)


def _reason(events):
    last = events[-1] if events else {}
    if last.get("event") == "signal":
        return f"killed/signal:{last['signal']}"
    return REASON


def mark_killed(run_dir, reason=None):
    """Append the killed verdict to the run's record. Return True when it wrote one; False when the folder has no record or the
    record already ends in a verdict. A corrupt record raises `RecordError`: it is not ours to rewrite."""
    path = Path(run_dir) / "record.jsonl"
    if not path.exists():
        return False
    r = record.Record(path)
    if r.events and r.events[-1]["event"] == "verdict":
        return False
    r.append("verdict", **V.killed(reason or _reason(r.events)).event_fields())
    return True


def _on_signal(path, signum):
    try:
        record.Record(path).append("signal", signal=signal.Signals(signum).name, pid=os.getpid(), ppid=os.getppid(),
                                   pgid=os.getpgid(0))
    finally:
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)


@contextlib.contextmanager
def recording_signals(path):
    """Record a `signal` event before SIGTERM, SIGHUP or SIGINT ends this process, then let the signal end it. Only the main
    thread may set a handler, so any other thread runs unrecorded."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    previous = {s: signal.getsignal(s) for s in SIGNALS}
    for s in SIGNALS:
        signal.signal(s, lambda signum, _frame: _on_signal(path, signum))
    try:
        yield
    finally:
        for s, handler in previous.items():
            signal.signal(s, handler if handler is not None else signal.SIG_DFL)


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
