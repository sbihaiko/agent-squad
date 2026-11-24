"""The run record: one append-only JSONL file, one atomic write per event.

A write forms the whole new content, writes a temporary file in the same directory, and renames it.
A reader sees the old file or the new file, never a partial line. The record is the only run truth.
"""
import json
import os
import tempfile
import time
import uuid
from pathlib import Path

# `outcome.cap` is the spend granted to the call, in US dollars (`res.amount`); it is not a turn cap.
# A `call_start` also states `chain` (the ordered registry ids the call may answer on) and `chain_id` (their hash). Neither
# is required: a record written before the field stays readable, and `chain_of` reads it as `unknown` (SPEC 1035).
REQUIRED = {
    "run_start": ["run_id", "graph_name", "graph_hash", "engine_options", "root", "plan"],
    "call_start": ["node", "entry", "session", "pid", "limit", "started_at"],
    "gate_start": ["node", "entry", "item", "pid", "limit", "started_at"],   # #680: a gate began its check; the dashboard reads it as in flight until its outcome
    "gate_result": ["node", "entry", "item", "sha", "dirt", "suite", "outcome", "source"],   # one gate stage's verdict for one tree and suite
    "gate_reuse": ["node", "entry", "item", "sha", "suite", "source"],   # a gate stage reused an earlier green verdict of this run instead of running its suite again
    "progress": ["node", "entry", "item", "session", "messages", "context_tokens", "tool"],
    "compaction": ["node", "entry", "session", "trigger"],
    "sandbox_deny": ["node", "entry", "session", "tool", "reason"],   # a child's tool call the sandbox hook denied (squad/sandbox.py)
    "message_over_ceiling": ["node", "entry", "item", "session", "tool", "size", "ceiling"],   # #2267: one message past the transport ceiling; the call goes on
    "outcome": ["node", "entry", "outcome", "reasons", "artifact", "spend", "turns", "cap", "elapsed"],
    "wait": ["node", "owner", "deadline_at", "default"],
    "proposed": ["proposal_id", "kind", "target", "requested", "proposer"],
    "validated": ["proposal_id", "kind", "target", "old", "requested", "range", "facts"],
    "applied": ["proposal_id", "kind", "target", "old", "requested", "range", "facts", "proposer", "session"],
    "rejected": ["proposal_id", "kind", "target", "reason"],
    "spawn_requested": ["parent", "parent_entry", "depth", "proposer_session", "requested"],
    "spawn_accepted": ["request_key", "child", "parent", "depth", "envelope", "reservation", "parent_entry", "definition"],
    "spawn_rejected": ["request_key", "parent", "envelope", "reason"],
    "model_refused": ["node", "entry", "model", "error"],   # one chain entry the provider refused before it served (#494)
    "model_retried": ["node", "entry", "model", "error"],   # one chain entry whose transport did not deliver: its one fresh call follows (#1293)
    "model_skipped": ["node", "entry", "model", "reason"],   # one chain entry left out before it was tried: outside its hours window
    "answer_repair": ["node", "entry", "shape", "reason"],   # #2336: a dispatch entry's answer was outside the spawns-list shape, and one repair turn follows
    "land": ["node", "parent", "entry", "outcome", "reasons", "effect"],
    "review_round": ["node", "round", "head", "verdict", "offered", "review"],   # #570: one review of a child's head; `fix` starts its next entry
    "review_try": ["node", "round", "try", "review"],   # #635: one review try of a round that got no verdict; the round tries again in the gate's deadline
    "review_progress": ["node", "gate", "elapsed"],   # #599: a dispatch child's review gate is still running; the record moves so the heartbeat never kills it
    "paused": ["node", "scope", "held", "completed"],
    "resume": ["run_id", "graph_hash", "frontier", "invoker", "engine_options", "preflight"],
    "autonomy_resume": ["count", "source"],   # ticket #182: one per automatic resume; `count` is its number, `source` who decided (the autonomy skill)
    "diagnostic": ["source", "node", "entry", "payload"],
    "wave_ceiling_lost": ["source", "path", "error"],   # #2840: the run named a wave ceiling file it could not read
    "ceiling_wait": ["ceiling", "cap", "live", "holder"],   # a run or a dispatch child waits for a slot of the machine-wide ceiling (squad/caps.py)
    "workspace_commit": ["node", "entry", "workspace", "path", "status"],   # #84: the engine committed a passing node's dirty workspace (`head`), or refused (`reason`)
    "signal": ["signal", "pid", "ppid", "pgid"],   # a SIGTERM, SIGHUP or SIGINT ended the engine; the time is `ts` (squad/killed.py)
    "verdict": ["verdict", "exit_code", "reasons", "remains", "folded"],
}
OUTCOMES = {"pass", "fail", "deadline", "needs-human", "unproven"}   # `unproven`: a check could not run, so nothing was measured (ADR-0164)
CHAIN_UNKNOWN = "unknown"    # the chain reading of a record written before a call stated its chain (SPEC 1035)


def chain_of(ev):
    """The chain one `call_start` states: {"ids": [registry ids in order], "sha256": hash of that order}. A record written
    before the field reads {"ids": "unknown", "sha256": "unknown"}: it is never rebuilt from the configuration in force now.
    The hash is `squad.modelreg.chain_sha256`: sha256 over the ids joined by a newline."""
    ids, sha = ev.get("chain"), ev.get("chain_id")
    if not isinstance(ids, list) or not isinstance(sha, str):
        return {"ids": CHAIN_UNKNOWN, "sha256": CHAIN_UNKNOWN}
    return {"ids": list(ids), "sha256": sha}


def new_run_id():
    """The one source of a run id: the launch module and the engine default both call it."""
    return f"run-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"


class RecordError(Exception):
    pass


def validate_event(ev):
    """Raise RecordError unless the event carries every field its kind requires."""
    if not isinstance(ev, dict) or "event" not in ev:
        raise RecordError(f"not an event: {ev!r}")
    kind = ev["event"]
    if kind not in REQUIRED:
        raise RecordError(f"unknown event kind {kind!r}")
    missing = [f for f in REQUIRED[kind] if f not in ev]
    if missing:
        raise RecordError(f"{kind} event lacks {', '.join(missing)}")
    if kind == "outcome":
        if ev["outcome"] not in OUTCOMES:
            raise RecordError(f"outcome {ev['outcome']!r} is not one of {sorted(OUTCOMES)}")
        if not isinstance(ev["reasons"], list):
            raise RecordError("outcome reasons must be a list")
    if kind == "verdict" and not isinstance(ev["reasons"], list):
        raise RecordError("verdict reasons must be a list")
    if kind == "resume" and not isinstance(ev["frontier"], list):
        raise RecordError("resume frontier must be a list")


def read(path):
    """Read and check a record. Return its events. A position gap or a bad event raises RecordError."""
    events = []
    text = Path(path).read_text() if Path(path).exists() else ""
    for i, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError as e:
            raise RecordError(f"line {i}: not JSON: {e}") from e
        validate_event(ev)
        if ev.get("pos") != len(events) + 1:
            raise RecordError(f"line {i}: pos {ev.get('pos')} differs from the line number")
        events.append(ev)
    return events


def atomic_write(path, content):
    """Replace a file with new content so that a reader never sees a partial file."""
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


class Record:
    """An append-only run record. Every append is one atomic replace of the file."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.events = read(self.path)

    def append(self, event, **fields):
        ev = {"event": event, **fields}
        ev["pos"] = len(self.events) + 1
        ev.setdefault("ts", time.time())
        validate_event(ev)
        content = "".join(json.dumps(e, sort_keys=False) + "\n" for e in [*self.events, ev])
        atomic_write(self.path, content)
        self.events.append(ev)
        return ev
