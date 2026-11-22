"""The private state of `guest` content and its 10-day purge (SPEC 2682, story 69; #2710).

The inbox, the queue, and the transcripts of a `guest` session live in one state folder. The folder sits outside any
repository and outside the project `.claude` folder, and only the session model reads what it holds: the folder is mode
0700 and every entry is mode 0600. `GuestState` refuses a folder under a repository or under a `.claude` folder, so a
misconfiguration never puts client text into a tree a commit can reach.

Every entry carries the time the code stored it. `purge` deletes each entry older than 10 days, and an entry it cannot
read, because a broken entry keeps unreadable client data. The caller passes the clock value, and the module reads no
clock of its own, so a test proves the purge at an exact age.

The module never hands client text to a log: `note` writes a line of the kind, the name and the event only, an `Entry`
describes itself without its text, and `error_reply` names no cause from the message it receives. `get` is the one reader
of the text. The checkpoint of the poller (#2707) lives in the same folder, at `checkpoint_path()`.

Guards: `tests/test_guest_state.py`.
"""
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

from .record import atomic_write

KINDS = ("inbox", "queue", "transcripts")
RETENTION_DAYS = 10
RETENTION_SECONDS = RETENTION_DAYS * 86400
DIR_MODE = 0o700
FILE_MODE = 0o600
LOG_DIR = "logs"
LOG_NAME = "guest.log"
CHECKPOINT_NAME = "guest-checkpoint.json"
SCHEDULE_NAME = "guest-schedule.json"
DEFAULT_ENV = "SQUAD_GUEST_STATE"
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")   # never climbs out of its kind folder

# One fixed reply for every failure. It names no cause from the message, so a client text the error carries never leaves.
ERROR_REPLY = "The reply could not be prepared. The owner was notified and will answer."


def default_root(env=None):
    """The machine's `guest` state folder: `SQUAD_GUEST_STATE` when set, else `~/.cache/agent-squad/guest`."""
    env = os.environ if env is None else env
    raw = env.get(DEFAULT_ENV)
    return Path(raw).expanduser() if raw else Path.home() / ".cache" / "agent-squad" / "guest"


def is_inside(path, parent):
    """True when `path` is `parent` or sits under it, with both resolved through symlinks."""
    try:
        Path(path).resolve().relative_to(Path(parent).resolve())
        return True
    except ValueError:
        return False


def _check_location(root, repo=None, claude=None):
    for public, label in ((repo, "repository"), (claude, "`.claude` folder")):
        if public is not None and is_inside(root, public):
            raise ValueError(f"the guest state folder stays outside the {label}: {root}")
    if claude is None and repo is not None and is_inside(root, Path(repo) / ".claude"):
        raise ValueError(f"the guest state folder stays outside the `.claude` folder: {root}")


def _check_kind(kind):
    if kind not in KINDS:
        raise ValueError(f"{kind!r} is not a guest state kind: one of {', '.join(KINDS)}")


def _check_name(name):
    if not NAME.match(str(name)):
        raise ValueError(f"{name!r} is not a guest state name: letters, digits, dot, hyphen, underscore; at most 64")


class Entry(NamedTuple):
    """One stored entry: its kind, its name, and the time the code stored it. It never holds the client text."""
    kind: str
    name: str
    created: float

    def describe(self):
        return f"{self.kind}/{self.name}"

    def __str__(self):
        return self.describe()


class GuestState:
    """One private state folder. `now` is the caller's clock value, in epoch seconds."""

    def __init__(self, root, now, repo=None, claude=None):
        root = Path(root)
        _check_location(root, repo, claude)
        self.root = root
        self._now = now

    # --- paths ---

    def kind_dir(self, kind):
        _check_kind(kind)
        return self.root / kind

    def path(self, kind, name):
        _check_name(name)
        return self.kind_dir(kind) / f"{name}.json"

    def checkpoint_path(self):
        """The poller's read cursor, one file for the machine (#2707). It sits in this folder, outside any repository."""
        return self.root / CHECKPOINT_NAME

    def schedule_path(self):
        """The polling schedule of the guest channels, one file for the machine (#2709). It sits in this folder,
        outside any repository."""
        return self.root / SCHEDULE_NAME

    def _private_dir(self, path):
        path.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, DIR_MODE)
        if path != self.root:
            os.chmod(path, DIR_MODE)

    # --- entries ---

    def put(self, kind, name, text):
        """Store `text` as one entry and return it. The file is atomic (ADR-0030) and mode 0600."""
        path = self.path(kind, name)
        self._private_dir(path.parent)
        created = float(self._now())
        atomic_write(path, json.dumps({"created": created, "text": str(text)}) + "\n")
        os.chmod(path, FILE_MODE)
        return Entry(kind, str(name), created)

    def _read(self, path):
        """(created, text) of one entry, or None when the file is unreadable or malformed."""
        try:
            doc = json.loads(path.read_text())
            return float(doc["created"]), str(doc["text"])
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def get(self, kind, name):
        """The client text of one entry, or None. This is the one reader of the content."""
        read = self._read(self.path(kind, name))
        return None if read is None else read[1]

    def entries(self, kind):
        """Every readable entry of one kind, oldest first."""
        directory = self.kind_dir(kind)
        out = []
        if not directory.is_dir():
            return out
        for path in sorted(directory.glob("*.json")):
            read = self._read(path)
            if read is not None:
                out.append(Entry(kind, path.stem, read[0]))
        return sorted(out, key=lambda e: e.created)

    def purge(self):
        """Delete every entry older than `RETENTION_DAYS`, and every entry it cannot read, and return the deleted ones."""
        cutoff = float(self._now()) - RETENTION_SECONDS
        deleted = []
        for kind in KINDS:
            directory = self.kind_dir(kind)
            if not directory.is_dir():
                continue
            for path in sorted(directory.glob("*.json")):
                read = self._read(path)
                if read is not None and read[0] >= cutoff:
                    continue
                created = read[0] if read is not None else 0.0
                try:
                    path.unlink()
                except FileNotFoundError:
                    continue
                deleted.append(Entry(kind, path.stem, created))
        return deleted

    # --- the log ---

    def note(self, event, kind, name):
        """Append one log line: the time, the event, the kind and the name only. It never carries the client text."""
        _check_kind(kind)
        _check_name(name)
        logs = self.root / LOG_DIR
        self._private_dir(logs)
        when = datetime.fromtimestamp(float(self._now()), UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        line = f"{when} {event} {kind} {name}\n"
        fd = os.open(logs / LOG_NAME, os.O_WRONLY | os.O_CREAT | os.O_APPEND, FILE_MODE)
        try:
            os.write(fd, line.encode())
        finally:
            os.close(fd)
        return line


def error_reply(error=None):
    """The one reply to a failed `guest` step. It names no cause from `error`, so no client text leaves."""
    return ERROR_REPLY


__all__ = ["KINDS", "RETENTION_DAYS", "Entry", "GuestState", "default_root", "error_reply", "is_inside",
           "CHECKPOINT_NAME", "LOG_DIR"]
