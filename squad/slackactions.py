"""The action request of the operator session, and the pass that runs it outside the session (SPEC 2682; #2939).

The operator session never runs a command. When a message asks for an action, the session appends one request line to
its own outbox: the reserved `kind` field (`action-request`), the epoch second it wrote the line (`at`), the action id,
the inbox line id it answers (`source`), the sender, the channel and, when the action takes one, its arguments. The
outbox is the one path the kernel bound lets a session write, so a request travels the same road as an answer, and the
daemon tells the two apart by the reserved `kind` field alone, so an answer line that carries a request's fields by
accident is still an answer.

The pass reads only the outbox of a channel the daemon says a project owns, and never the DM outbox: the DM session
proposes, and a proposal is the owner's to start. A request older than `REQUEST_WINDOW` seconds never runs, so a line a
crashed pass left behind cannot act on a channel long after its message. A refused line is recorded in the audit and
dropped; it never reaches the executor.

The pass is the checked executor. It runs in the daemon's own process, which is outside the session and outside the
session's sandbox, and it runs every command itself; no text of a message ever reaches a command line. For each
request the pass:

1. reads the inbox line the request names, and refuses a request whose sender is not the sender of that line;
2. asks `squad/slackpolicy.decide` for the decision of that sender in that channel;
3. builds the executor's request and hands it to `squad/slackexecutor.execute`, which checks the identity, the project,
   the channel, the action, the grant, the arguments and the deploy environment before it runs anything;
4. runs the command the project declares for one of the four text actions (`status`, `logs`, `request_work`,
   `request_investigation`), or the deploy command of the project identity;
5. appends one reply in the thread of the message, and one audit line holding the time, the sender, the role, the
   channel, the action, the exit code and the one error that explains the exit (an empty error on a success).

The commands come from `project.actions` of the committed `slack.yaml` (`squad/slackpolicy.project_actions`), and the
deploy command from the project identity. Only the four text actions and `deploy` run through `project.actions`:
`approve_and_move` takes the board and `loop_control` takes the hub loop switch, so each keeps its own executor path
(`slackexecutor.py:340-347`) and no declared command stands in for it.

The command is the project's own declaration, reviewed in its history; the executor never builds one from the message.
The grant the executor checks is the decision `decide` returned for the inbox line: one role, its actions and its reply
path decide both what the pass asks and what the executor allows, so the request's action adds no grant of its own.

A request is run once: a ledger of the requests already handled lives in `<state>/actions-handled.jsonl`, one JSON line
per handled key, and the key names the parsed request (the action, the arguments, the sender, the channel and the
source), so two requests that share the source and the action but differ in their arguments still run once each, and
two lines that parse alike never run twice whatever else they carry. The key is written with fsync before the reply,
the audit line and the command itself. Each pass also keeps its place in every outbox in
`<state>/actions-offsets.json`, so a pass reads only the bytes appended since the last one; a pass that keeps no place,
and one whose outbox rotated below its place, reads a bounded tail of the outbox. The ledger, never the offset, is what
decides that a request already ran.

A reply write that fails never loses the record of the run: the audit line still names the failure, and the pass keeps
the reply in `<state>/actions-pending.jsonl` for its next pass to deliver, so the reply arrives without running the
command again.

The command the project declares runs as one argv with no shell. A declared value that names a shell metacharacter is
not one argv, so its source keeps the reason and the executor refuses the action as an invalid source, never as a
missing one.

Guards: `tests/test_slack_actions.py`.
"""
import glob
import hashlib
import json
import os
import shlex
import subprocess
import time
from typing import NamedTuple

from . import slackexecutor as X
from . import slackpolicy as S

AUDIT = "actions-audit.jsonl"        # one JSON line per action run, in the state directory (#2939)
HANDLED = "actions-handled.jsonl"    # one JSON line per request a pass already handled, in the state directory
OFFSETS = "actions-offsets.json"     # the byte of each outbox the last pass read through, so a pass reads only new bytes
PENDING = "actions-pending.jsonl"    # one JSON line per reply a failed write left for the next pass to deliver
DEFAULT_OUTBOXES = "outboxes"
COMMAND_TIMEOUT = 300.0              # seconds, one declared command: a bound, never a target
REQUEST_WINDOW = 300.0               # seconds, a request older than this is stale and never runs
FIRST_READ_BYTES = 1 << 20           # bytes: the tail a first pass reads of an outbox it keeps no place in
KIND = "action-request"              # the reserved field that tells a request from an answer, and nothing else does
DM = "dm"                            # the owner's DM scope: it carries answers and proposals, never an action request
# The characters that make a declared command a shell line. The command runs with no shell, so a value that names one of
# them is refused, and a project value can never chain a second command.
SHELL_METACHARACTERS = (";", "|", "$(", "`")
REQUEST_FIELDS = ("action", "arguments", "sender", "channel", "source")
# The actions whose source answers one text. `approve_and_move` and `loop_control` keep the caller's own source,
# because each answers a pair `slackexecutor.execute` unpacks (slackexecutor.py:341 and slackexecutor.py:345).
TEXT_ACTIONS = (X.STATUS, X.LOGS, X.REQUEST_WORK, X.REQUEST_INVESTIGATION)


class Request(NamedTuple):
    """One action request as the session writes it: one action id, its arguments, the sender, the channel and the id of
    the inbox line the message was answered from."""
    action: str
    arguments: dict
    sender: str
    channel: str
    source: str


class Run(NamedTuple):
    """One request after the pass: the sender, its role and its channel, the exit code of the run, the reply line the
    channel gets (None when the decision names no reply path), and the one reason a refusal names."""
    action: str
    sender: str
    role: str
    channel: str
    exit: int
    reply: dict | None
    error: str


class CommandSource:
    """One action source that runs a command the project declares, with the caller's runner. It reads the message
    nowhere: the command is fixed at build time, it runs as one argv with no shell, and no argument of a request is
    appended to it. `exit_code` holds the code of the last run, so the audit line names the command's own answer.

    `error` is None for a command that is one argv, and the reason the declared value never runs otherwise. The executor
    reads it to refuse the action as an invalid source, so a declaration that names a shell metacharacter is refused
    with its own reason and never reads as a command the project did not declare."""

    def __init__(self, command, runner=subprocess.run, timeout=COMMAND_TIMEOUT):
        self.command = str(command)
        self.argv = command_argv(self.command)
        self.error = None if self.argv else "the declared command is not one argv, so it never runs"
        self.runner = runner
        self.timeout = timeout
        self.exit_code = None

    def __call__(self, *args):
        if not self.argv:
            self.exit_code = 1
            return self.error
        try:
            done = self.runner(self.argv, shell=False, capture_output=True, text=True, timeout=self.timeout)
        except (OSError, subprocess.SubprocessError) as e:
            self.exit_code = 1
            return f"the command did not run: {type(e).__name__}: {e}"
        self.exit_code = int(getattr(done, "returncode", 0) or 0)
        text = (getattr(done, "stdout", "") or "").strip()
        if text:
            return text
        return f"the command exited {self.exit_code} with no output"


def command_argv(command):
    """The one argv of a declared command, or None when the value names a shell metacharacter or cannot be split. The
    command runs with no shell, so this refusal is what keeps a declared value from chaining a second command."""
    text = str(command)
    if not text.strip() or any(mark in text for mark in SHELL_METACHARACTERS):
        return None
    try:
        argv = shlex.split(text)
    except ValueError:
        return None
    return argv or None


def request_record(action, channel, sender, source, arguments=None, at=None, **extra):
    """The one request line the session appends to its outbox. `emit` writes it, and `parse` reads it back. `at` is the
    epoch second the line was written, so the pass can tell a fresh request from one a crashed pass left behind; it
    takes the caller's clock, else the moment the record is built."""
    body = {"kind": KIND, "at": float(time.time() if at is None else at), "action": str(action),
            "arguments": dict(arguments or ()), "sender": str(sender), "channel": str(channel),
            "source": str(source)}
    body.update(extra)
    return body


def emit(action, channel, sender, source, arguments=None, at=None, **extra):
    """The request as the one JSON text the session appends, its keys sorted, so the same request is always one text."""
    return json.dumps(request_record(action, channel, sender, source, arguments, at, **extra), sort_keys=True)


def is_request(record):
    """True when one outbox line is an action request: the reserved `kind` field is the sole discriminator, so an answer
    line that carries a request's fields by accident is still an answer."""
    return isinstance(record, dict) and record.get("kind") == KIND


def parse(text):
    """One `(Request, None)` from a request line, or `(None, reason)` when a field the request needs is missing or is
    not one non-empty string. The shape alone is checked here; the executor decides what the action means."""
    if isinstance(text, dict):
        body = text
    else:
        try:
            body = json.loads(text)
        except (TypeError, ValueError):
            return None, "the request is not JSON"
    if not isinstance(body, dict):
        return None, "the request is not one JSON object"
    missing = [key for key in REQUEST_FIELDS if key not in body]
    if missing:
        return None, f"the request does not name {', '.join(missing)}"
    for key in ("action", "sender", "channel", "source"):
        value = body[key]
        if not isinstance(value, str) or not value.strip():
            return None, f"the request's {key} is not one non-empty string"
    arguments = body["arguments"]
    if not isinstance(arguments, dict):
        return None, "the request's arguments are not one mapping"
    return Request(body["action"], dict(arguments), body["sender"], body["channel"], body["source"]), None


def refusal_text(action, error, decision=None):
    """The one reply a refused request gets, naming the grant the sender lacks. The error the executor returns is one
    `check: reason` pair, so the reply names the check that failed."""
    check, _, reason = str(error or "").partition(": ")
    if check == "grant":
        role = getattr(decision, "role", None)
        return (f"You have no grant for the {action} action in this channel: your role {role!r} does not list it.")
    if check == "identity":
        return (f"You have no grant for the {action} action in this channel: this channel gives you no role, so it "
                "grants no action.")
    if check == "action":
        return f"The {action} action was refused: {reason}"
    if check == "source_invalid":
        # The project declares a command for the action, but the value is not one argv, so it never runs. The refusal
        # names that reason, never a missing command.
        return f"The {action} action was refused: {reason}"
    if check == "source":
        # `approve_and_move` and `loop_control` keep their own executor path, so no `project.actions` command stands in
        # for the source they need, and the reply says so.
        return (f"The {action} action was refused: {reason}. The {action} action never runs through project.actions, "
                "so the project declares no command for it.")
    return f"The {action} action was refused ({check or 'request'}): {reason or 'the executor gave no reason'}"


def sources_for(committed, local, runner=subprocess.run):
    """The action sources of one project: the command the project declares for every text action, and the deploy
    command of the project identity. An action the project declares no command for has no source, so the executor
    refuses it by name and never guesses a command. `approve_and_move` and `loop_control` never run through
    `project.actions`: each answers a pair the executor unpacks from its own source (the board and the hub loop
    switch, `slackexecutor.py:340-347`), so this function builds none and the executor refuses them as a missing
    source here."""
    declared = S.project_actions(committed, local)
    # A declared value that is not one argv keeps its source, and that source carries the reason: the executor refuses
    # it as an invalid source, so a refusal never reads as a command the project did not declare.
    out = {action: CommandSource(command, runner) for action, command in declared.items() if action in TEXT_ACTIONS}
    _, command = S.deploy_target(S.project_identity(committed, local))
    if command:
        out[X.DEPLOY] = CommandSource(command, runner)
    return out


def _answers(committed, local, channel):
    """True when the channel row takes a post: its mode is `listen+speak`. A `listen` channel receives no post, not
    even a refusal (story 68), so a refusal there is recorded in the audit and never sent."""
    for doc in (committed or {}, local or {}):
        for ws in doc.get("workspaces") or []:
            for row in ws.get("channels") or []:
                row = row or {}
                if str(row.get("id")) == str(channel):
                    return str(row.get("mode") or "listen") == "listen+speak"
    return False


def _workspace(committed, local, channel):
    """The Slack workspace of one channel, from the committed file then the local one, or `""` for a channel no file
    names. The policy needs the workspace with the channel to decide an event."""
    for doc in (committed or {}, local or {}):
        for ws in doc.get("workspaces") or []:
            for row in ws.get("channels") or []:
                if str((row or {}).get("id")) == str(channel):
                    return str(ws.get("id") or "")
    return ""


class _Inbox:
    """One pass's index of the inbox lines of each channel it reads: it reads a channel's inbox once, on the first
    lookup, so each request is one lookup and never one read of the whole file. A line of another channel never matches,
    because one channel's inbox names its own lines. The inbox is read with the daemon's own rule: one JSON object per
    line, a broken line is skipped, and the first line of an id wins."""

    def __init__(self, state):
        self._state = state
        self._rows = {}

    def line(self, channel, source):
        rows = self._rows.get(channel)
        if rows is None:
            rows = self._rows[channel] = self._read(channel)
        return rows.get(str(source))

    def _read(self, channel):
        path = os.path.join(self._state, "inboxes", f"{channel}.jsonl")
        out = {}
        try:
            with open(path, encoding="utf-8") as f:
                rows = f.read().splitlines()
        except OSError:
            return out
        for row in rows:
            try:
                record = json.loads(row)
            except ValueError:
                continue
            if isinstance(record, dict) and record.get("id") is not None:
                out.setdefault(str(record["id"]), record)
        return out


def _inbox_line(state, channel, source):
    """The inbox line with one id, from the inbox of the request's own channel, or None. One lookup through a fresh
    index, so a caller outside the pass reads one line and never re-reads the file per request."""
    return _Inbox(state).line(channel, source)


def _stale(record, now_ts):
    """The reason a request line is too old to run, or None. A line with no usable `at`, or one older than
    `REQUEST_WINDOW`, never runs: a request a crashed pass left behind cannot act on a channel long after its message."""
    try:
        at = float(record.get("at"))
    except (TypeError, ValueError):
        return "the request names no time it was written, so it is older than the window"
    if now_ts - at > REQUEST_WINDOW:
        return f"the request is older than the {REQUEST_WINDOW:.0f} s window"
    return None


def _load_offsets(path):
    """The byte each outbox was read through, by path, or an empty mapping for a missing or unreadable file."""
    try:
        with open(path, encoding="utf-8") as f:
            got = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(got, dict):
        return {}
    out = {}
    for key, value in got.items():
        try:
            out[str(key)] = int(value)
        except (TypeError, ValueError):
            continue
    return out


def _save_offsets(path, offsets):
    """Write the kept places atomically. The offsets only bound the cost of a pass: the ledger decides what already ran,
    so a write that fails is no defect and the next pass reads more bytes than it needed."""
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(json.dumps(offsets, sort_keys=True))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except OSError:
        return


def _tail_start(path, limit=None):
    """The byte a first read starts at: the start of the file when it is short, else the first line boundary at or after
    `size - limit`. A pass that keeps no place in an outbox reads a bounded tail, so a long answer history costs one
    bounded read and never one read of the whole file. A rotated outbox reads the same bounded tail, never the whole
    file its old place no longer bounds."""
    if limit is None:
        limit = FIRST_READ_BYTES
    try:
        size = os.path.getsize(path)
    except OSError:
        return 0
    if size <= limit:
        return 0
    start = size - limit
    try:
        with open(path, "rb") as f:
            f.seek(start)
            data = f.read(limit + 1)
    except OSError:
        return 0
    cut = data.find(b"\n")
    return start + cut + 1 if cut >= 0 else size


def _new_rows(path, offset):
    """The complete lines written after `offset`, and the byte after the last of them. A file shorter than its offset
    was rotated, so it is read from a bounded tail, the same tail a first read takes, and never from the start of a long
    file. A last line with no newline is not yet written, so it stays unread."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return [], offset
    if offset > size:
        offset = _tail_start(path)
    try:
        with open(path, "rb") as f:
            f.seek(offset)
            data = f.read()
    except OSError:
        return [], offset
    end = offset + data.rfind(b"\n") + 1
    return data[:data.rfind(b"\n") + 1].splitlines(), end


def _request_lines(state, project_of, now, offsets=None):
    """Every new request line of every outbox in file order, as `(path, line index, record, reason)`, where `reason`
    names why a line never runs and is None for a line the pass runs. Returns `(lines, ends)`, where `ends` is the byte
    each outbox was read through, so the caller keeps its place once every line is handled.

    A pass reads only the bytes appended since the last one (`offsets`), so its cost follows the new requests, never the
    whole history. The pass reads only the outbox of a channel a project owns, so a request line in any other outbox
    never runs. The DM outbox never carries a request, and a line older than the window is stale. A line whose channel
    is not the channel of its outbox is refused too, so a request never acts on a channel other than the one its
    session can write."""
    out = []
    ends = {}
    now_ts = now.timestamp()
    for path in sorted(glob.glob(os.path.join(state, DEFAULT_OUTBOXES, "*.jsonl"))):
        key = os.path.splitext(os.path.basename(path))[0]
        if key == DM:
            reason = "the DM outbox never carries an action request"
        elif not project_of(key):
            reason = f"the channel {key!r} is owned by no project"
        else:
            reason = None
        kept = (offsets or {}).get(path)
        rows, ends[path] = _new_rows(path, _tail_start(path) if kept is None else kept)
        for i, row in enumerate(rows):
            try:
                record = json.loads(row.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                continue
            if not is_request(record):
                continue
            why = reason
            if why is None and str(record.get("channel") or "") != key:
                why = f"the request names channel {record.get('channel')!r}, not the channel of its outbox {key!r}"
            if why is None:
                why = _stale(record, now_ts)
            out.append((path, i, record, why))
    return out, ends


class Ledger:
    """The requests a pass already ran, one JSON line per handled key. The pass appends one line and keeps the set in
    memory, so one action costs one append and never a rewrite of the whole ledger. A line a crash cut is skipped and an
    unreadable file reads as empty, so a broken ledger repeats one action at worst, never loses one."""

    def __init__(self, path):
        self.path = str(path)
        self._values = None

    def values(self):
        if self._values is None:
            self._values = set()
            try:
                with open(self.path, encoding="utf-8") as f:
                    rows = f.read().splitlines()
            except OSError:
                rows = []
            for row in rows:
                try:
                    got = json.loads(row)
                except ValueError:
                    continue
                if isinstance(got, dict) and got.get("key"):
                    self._values.add(str(got["key"]))
        return self._values

    def has(self, key):
        return str(key) in self.values()

    def add(self, key):
        if self.has(key):
            return False
        _append_line(self.path, {"key": str(key)})
        self._values.add(str(key))
        return True


def _key(record):
    """One handled key: the parsed request, never the raw line. The key holds the action, the arguments, the sender, the
    channel and the source, so two requests that name the same thing run once each, and two lines that carry the same
    request with another field (a thread ts, say) never run the declared command twice. A malformed line keeps a key too,
    so a refusal is recorded once. The key is the digest of those fields' canonical text."""
    fields = {name: record.get(name) for name in REQUEST_FIELDS}
    return hashlib.sha256(json.dumps(fields, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _append_line(path, record):
    """Append one JSON line and fsync it, so a crash after the write never loses it."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())


def _write_lines(path, records):
    """Replace one JSON-lines file atomically, so a rewrite a crash cuts leaves the old file whole."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _send_pending(path, outbox_for):
    """Deliver every reply a failed write left behind, then clear it. A reply that cannot be written stays for the next
    pass, so a full disk delays a reply and never loses it."""
    if not os.path.exists(path):
        return
    try:
        with open(path, encoding="utf-8") as f:
            rows = f.read().splitlines()
    except OSError:
        return
    left = []
    for row in rows:
        try:
            got = json.loads(row)
        except ValueError:
            continue
        if not isinstance(got, dict) or not isinstance(got.get("reply"), dict):
            continue
        try:
            _append_line(outbox_for(str(got.get("channel") or "")), got["reply"])
        except OSError:
            left.append(got)
    try:
        _write_lines(path, left)
    except OSError:
        return


def _exit_code(outcome, source):
    """The code the audit line carries: a refusal is 1, a run is the source's own exit code, and a run with no source is
    a wiring bug, so it reads as 1 and never as a success."""
    if not outcome.ran:
        return 1
    if source is None:
        return 1
    code = getattr(source, "exit_code", None)
    return 0 if code is None else int(code)


def _run_one(record, state, project_of, documents, now, runner, outbox_for, inbox=None):
    """One request through the pass: the identity, the decision, the executor and the run. Returns a `Run`; the caller
    records the handled key, appends the reply and the audit line. `inbox` is the pass's one index of the inbox lines;
    None reads a fresh one, so a caller outside the pass still gets the line."""
    channel = str(record.get("channel") or "")
    sender = str(record.get("sender") or "")
    parsed, why = parse(record)
    if parsed is None:
        return Run(str(record.get("action") or ""), sender, "", channel, 1, None, f"malformed request: {why}")
    committed, local = documents(parsed.channel) or ({}, {})
    committed, local = committed or {}, local or {}
    project = str(project_of(parsed.channel) or "")
    if not project:
        # No project owns the channel, so no session writes its outbox and no reader is there for a reply: the refusal
        # is silent and the audit is the only record.
        return Run(parsed.action, parsed.sender, "", parsed.channel, 1, None,
                   "project: no project owns this channel, so no action runs")
    line = (inbox or _Inbox(state)).line(parsed.channel, parsed.source)
    thread_ts = str((line or {}).get("ts") or record.get("thread_ts") or "")
    if line is None:
        return Run(parsed.action, parsed.sender, "", parsed.channel, 1, None,
                   f"identity: the request names no inbox line {parsed.source!r}")
    line_sender = str(line.get("user") or "")
    if line_sender != parsed.sender:
        return Run(parsed.action, parsed.sender, "", parsed.channel, 1, None,
                   f"identity: the request names sender {parsed.sender!r}, not the sender of the line {line_sender!r}")
    workspace = _workspace(committed, local, parsed.channel)
    event = {"kind": "message", "workspace": workspace, "channel": parsed.channel, "user": line_sender,
             "text": str(line.get("text") or "")}
    decision = S.decide(committed, local, event, now)
    # The decision comes from the inbox line's own sender and text, and the executor's `check` reads that same decision
    # for the role, the granted action and the reply path (slackexecutor.py:215, 230, 260). So the grant the executor
    # enforces is exactly the one the policy granted this line, and the request's action adds no grant of its own. A
    # channel that answers only an addressed message names no reply path for an unaddressed line, so it runs nothing.
    request = X.Request(parsed.action, parsed.arguments, project, parsed.channel, parsed.source)
    sources = sources_for(committed, local, runner)
    outcome = X.execute(request, decision, line, project,
                        status=sources.get(X.STATUS), logs=sources.get(X.LOGS),
                        work=sources.get(X.REQUEST_WORK), investigate=sources.get(X.REQUEST_INVESTIGATION),
                        deploy=sources.get(X.DEPLOY), identity=S.project_identity(committed, local))
    role = str(getattr(decision, "role", None) or "")
    if outcome.ran:
        text = str((outcome.reply or {}).get("text") or "")
    else:
        text = refusal_text(parsed.action, (outcome.record or {}).get("error"), decision)
    reply = None
    # A channel whose decision names no reply path (`listen`, or a channel that answers only an addressed message)
    # receives no post, so a refusal there is recorded and never sent (story 68). A sender with no role is `data` for
    # the policy, and the refusal still reaches an answering channel: the grant is named, and the message stays data.
    # A channel no project owns returned above, so its refusal never reaches an outbox no session writes.
    telling = getattr(decision, "reply", None) or (getattr(decision, "kind", None) == S.DATA
                                                   and _answers(committed, local, parsed.channel))
    if telling:
        reply = {"thread_ts": thread_ts, "channel": parsed.channel, "text": text}
    return Run(parsed.action, parsed.sender, role, parsed.channel, _exit_code(outcome, sources.get(parsed.action)),
               reply, "" if outcome.ran else str((outcome.record or {}).get("error") or ""))


def run_pass(state, project_of, documents, now, runner=None, outbox_for=None):
    """One pass: run every request line no earlier pass ran or refused, then record it.

    `project_of(channel)` names the project that owns a channel; the pass reads the outbox of a channel it names and no
    other. `documents(channel)` answers the `(committed, local)` pair of that project's Slack files. `runner` is the
    command runner the pass uses, `subprocess.run` by default, so the pass runs each declared command itself, in the
    daemon's process and outside every session. `outbox_for(channel)` names the outbox file the reply goes to,
    `<state>/outboxes/<channel>.jsonl` by default.

    A line that never runs is recorded in the audit with its reason and is not returned. Returns one `Run` per request
    this pass ran, in file order."""
    state = os.path.abspath(state)
    runner = runner or subprocess.run
    outbox_for = outbox_for or (lambda channel: os.path.join(state, DEFAULT_OUTBOXES, f"{channel}.jsonl"))
    ledger = Ledger(os.path.join(state, HANDLED))
    audit_path = os.path.join(state, AUDIT)
    offsets_path = os.path.join(state, OFFSETS)
    pending_path = os.path.join(state, PENDING)
    _send_pending(pending_path, outbox_for)   # a reply a failed write left behind goes out before any new request
    offsets = _load_offsets(offsets_path)
    lines, ends = _request_lines(state, project_of, now, offsets)
    inbox = _Inbox(state)
    runs = []
    for _, _, record, why in lines:
        key = _key(record)
        if ledger.has(key):
            continue
        if why is not None:
            # The handled key is written before the audit line, the same order the run branch uses, so a crash between
            # the two writes never repeats the audit row on the next pass.
            ledger.add(key)
            _append_line(audit_path, {"time": now.isoformat(), "sender": str(record.get("sender") or ""),
                                      "role": "", "channel": str(record.get("channel") or ""),
                                      "action": str(record.get("action") or ""), "exit": 1, "error": why})
            continue
        # The handled key is written with fsync before the reply, the audit line and the command itself, so a crash
        # after any of them never runs the request a second time (#2939 review, finding 1).
        ledger.add(key)
        run = _run_one(record, state, project_of, documents, now, runner, outbox_for, inbox)
        error = run.error
        exit_code = run.exit
        if not error and exit_code != 0:
            error = f"the declared command exited {exit_code}"
        try:
            if run.reply is not None:
                _append_line(outbox_for(run.channel), run.reply)
        except OSError as e:
            # The reply write failed: the audit still records the run, and the reply waits for the next pass, so a full
            # disk delays a reply and never loses it (#2939 review, findings 2 and 4).
            exit_code = 1
            error = f"the reply was not written: {type(e).__name__}: {e}"
            _append_line(pending_path, {"channel": run.channel, "reply": run.reply})
        finally:
            # The audit line is written whatever the reply write did, so a crash after the reply never loses the record.
            _append_line(audit_path, {"time": now.isoformat(), "sender": run.sender, "role": run.role,
                                      "channel": run.channel, "action": run.action, "exit": exit_code,
                                      "error": error})
        runs.append(run)
    _save_offsets(offsets_path, ends)   # only the outboxes this pass read, so a deleted one leaves no stale entry
    return tuple(runs)
