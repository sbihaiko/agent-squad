"""GitHub issue #177: a process table read keeps the whole command line.

`/bin/ps` cuts the command column at the output width. Linux cuts it, so a reader there receives a truncated argv, the
pattern it matches is no longer in it, and the reader concludes the process is gone: the janitor's dashboard ceiling
counts no dashboard of this loop and bounds nothing, and `watchd.sh` reads its own live driver as dead. The owner's
machine does not cut the column, so every one of those reads works there and the defect hides until the same code runs
on a Linux host. Every read that asks for a command therefore asks for the wide form (`-ww`), on both platforms.

Host-free: the test reads the loop's sources. It starts no process and reads no process table of its own.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib" / "squad-goal" / "scripts"
WIDE = "-ww"
COMMAND_FORMATS = ("command=", "args=")
# The readers the issue names. Each one must still be seen by the guard below, so the guard cannot pass by seeing nothing.
NAMED_READERS = ("janitor.py", "watchd.sh", "health.sh", "serial.sh", "heartbeat.sh", "autopilot.sh", "hub.py")


def command_reads():
    """Every source line beside the scripts that reads a command out of the process table, as (path, number, line)."""
    for path in sorted(SCRIPTS.iterdir()):
        if not path.is_file():
            continue
        for number, line in enumerate(path.read_text(errors="replace").splitlines(), start=1):
            if "/bin/ps" in line and any(f in line for f in COMMAND_FORMATS):
                yield path.name, number, line


def test_every_command_read_asks_for_the_wide_form():
    """A reader that matches a command cannot silently receive a truncated argv."""
    offenders = [f"{name}:{number} {line.strip()}" for name, number, line in command_reads() if WIDE not in line]
    assert offenders == [], "a ps read of a command must ask for the wide form (-ww):\n" + "\n".join(offenders)


def test_the_guard_sees_every_reader_the_issue_names():
    """The guard above fails closed: it also fails when a named reader stops reading the process table."""
    seen = {name for name, _, _ in command_reads()}
    missing = [name for name in NAMED_READERS if name not in seen]
    assert missing == [], "these readers no longer read a command out of the process table: " + ", ".join(missing)
