"""The release events the promotion pass and a project's deploy step emit, and `voiced` reads (issue #2702).

usage: python3 contrib/voice/release_events.py emit --event <id> --item <text> [--item <text>...] [--key <key>] [--state DIR]
       python3 contrib/voice/release_events.py read [--state DIR]

The promotion pass emits `stg_release` when the `dev` to `stg` pull request merges, and `prod_release` when `main` moves.
A project with its own deploy step emits the event by this command, after its deploy and its green e2e, in place of the
pass: `--event stg_release --item "<sha> <subject>"`. A subscribed channel can then announce the release.

One event is one JSON line in `$VOICE_STATE/release-events.jsonl` (`--state` names the directory instead), holding:

- `id`: the event id the project declares, as `stg_release` or `prod_release`;
- `key`: the one emission this record belongs to. A repeated emission of the same key appends nothing, so a pass that runs
  again over one release announces it once. Without `--key`, the key is the event id and a digest of its items, so two
  emissions of the same content still make one record;
- `items`: the list of items the release carries, one line of text each;
- `ts`: when the event was emitted, in ISO 8601 UTC.

The write appends the whole line under one exclusive lock and fsyncs it, so a reader sees a whole record or none. A line
this build cannot read is skipped and the records around it stay readable. `voiced` reads the same file through
`State.release_events()`, so the daemon and the command share one directory and one shape.
"""
import datetime
import fcntl
import hashlib
import json
import os
import sys

DEFAULT_STATE = os.path.expanduser("~/.local/state/agent-squad/voiced")
RECORD = "release-events.jsonl"


def path(state_dir=None):
    """The one record file: the state directory given, else `$VOICE_STATE`, else the daemon's default."""
    directory = state_dir or os.environ.get("VOICE_STATE") or DEFAULT_STATE
    return os.path.join(directory, RECORD)


def _records(handle):
    """Every readable record of one open record file, oldest first. A malformed line is skipped, never raised."""
    out = []
    for line in handle:
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict) and record.get("id"):
            out.append(record)
    return out


def read(state_dir=None):
    """Every release event this machine recorded, oldest first."""
    try:
        with open(path(state_dir), encoding="utf-8") as f:
            return _records(f)
    except FileNotFoundError:
        return []


def _digest(items):
    return hashlib.sha1("\n".join(items).encode("utf-8")).hexdigest()[:12]


def emit(event, items, key=None, state_dir=None):
    """Append one event record, unless its key is already recorded. Returns `(record, created)`."""
    items = list(items)
    key = key or f"{event}:{_digest(items)}"
    record = {"id": event, "key": key, "items": items,
              "ts": datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}
    target = path(state_dir)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "a+", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)      # one emitter at a time: the read and the append are one step
        try:
            f.seek(0)
            for old in _records(f):
                if old.get("key") == key:
                    return old, False
            f.write(json.dumps(record, sort_keys=True) + "\n")
            f.flush()
            os.fsync(f.fileno())
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    return record, True


def _args(argv):
    """The `emit` and `read` shapes, read by hand so the command needs no module path."""
    opts = {"event": None, "key": None, "state": None, "items": []}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--event", "--key", "--item", "--state") and i + 1 < len(argv):
            if a == "--item":
                opts["items"].append(argv[i + 1])
            else:
                opts[a[2:]] = argv[i + 1]
            i += 2
            continue
        raise SystemExit(f"release_events: unknown argument {a!r}")
    return opts


def main(argv):
    if not argv or argv[0] not in ("emit", "read"):
        print(__doc__.strip().splitlines()[2].strip(), file=sys.stderr)
        return 2
    opts = _args(argv[1:])
    if argv[0] == "read":
        for record in read(opts["state"]):
            print(json.dumps(record, sort_keys=True))
        return 0
    if not opts["event"] or not opts["items"]:
        print("release_events: emit needs --event and at least one --item", file=sys.stderr)
        return 2
    record, created = emit(opts["event"], opts["items"], key=opts["key"], state_dir=opts["state"])
    state = "already emitted" if not created else "emitted"
    print(f"event {record['id']} {state}: {len(record['items'])} item(s), key {record['key']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
