"""The deterministic executor and the structured request (SPEC 2682, story 65; #2689).

The operator session never acts on Slack by itself. It emits one structured request: the action, the arguments, the
project, the channel and the id of the source message. `squad/slackpolicy.decide` gives one decision per event, and
this module checks the request against that decision before it runs the action. It checks the requester identity (the
source message of the inbox line, and the role the decision gives its caller), the project, the channel, the grant (the
action the caller's role lists) and the arguments (the ones the action takes, and no other). It runs the action only
after every check passes.

A refused request is data: `execute` returns a record with the line's own fields and one `error` that names the check,
so the caller appends it beside the messages of people with no role. A success returns the reply path the decision
names (`bot`, `draft` or `send`) and the answer text; a decision with no reply path, like a `listen` channel, runs
nothing.

The `logs` action answers with the log of the project that owns the channel and of no other: the source receives that
one project, and `check` has already refused a request that names another. `scrub` masks every secret and drops every
guest line, so a client's text never reaches the reply (SPEC 2682, story 42).

The `approve_and_move` action approves the review of one card and moves it between columns after Todo (SPEC 2682,
stories 26 and 67). `squad/slackboard.py` holds its three rules, and the action writes the board only when each passes.

The module reads no clock and calls no network: the caller supplies the project status source, the log source and the
board, because the status of a project comes from the repository and the board, the log comes from the project's own
log, and the board answers the card and takes the move.

Guards: `tests/test_slack_executor.py`, `tests/test_approve_and_move.py`.
"""
import json
from typing import NamedTuple

from . import slackboard
from .redact import redact

STATUS = "status"
LOGS = "logs"
APPROVE_AND_MOVE = "approve_and_move"
ACTIONS = frozenset((STATUS, LOGS, APPROVE_AND_MOVE))
REQUEST_FIELDS = ("action", "arguments", "project", "channel", "source")
# The arguments each action takes. `status` and `logs` take none: they answer for the project the caller already named.
_ACTION_ARGUMENTS: dict[str, frozenset[str]] = {STATUS: frozenset(), LOGS: frozenset(),
                                                APPROVE_AND_MOVE: frozenset(("card", "to"))}
# The arguments an action cannot run without. Only `approve_and_move` needs one: an approval needs its card and its column.
_REQUIRED_ARGUMENTS: dict[str, tuple[str, ...]] = {APPROVE_AND_MOVE: ("card", "to")}


class Request(NamedTuple):
    """One request as the operator session emits it: the action, its arguments, the project, the channel and the id of
    the message that asked for it."""
    action: str
    arguments: dict
    project: str
    channel: str
    source: str


class Refusal(NamedTuple):
    """The one check a request failed, and the reason it failed."""
    check: str
    reason: str


class Outcome(NamedTuple):
    """What one request became: whether the action ran, the reply the decision's path carries (`path`, `channel` and
    `text`), and, for a refusal, the data record its caller appends."""
    ran: bool
    reply: dict | None
    record: dict | None


def emit(action, arguments, project, channel, source, sort_keys=True):
    """The one JSON text the operator session emits for one request. The keys are sorted, so the same request is always
    the same text and a log or a test reads it without guessing."""
    body = {"action": str(action), "arguments": dict(arguments or ()), "project": str(project),
            "channel": str(channel), "source": str(source)}
    return json.dumps(body, sort_keys=sort_keys)


def parse(text):
    """One `(Request, None)` from the text the session emitted, or `(None, reason)` when the text is not a request that
    names every field. A missing field, an arguments value that is not a mapping, and an empty field each name
    themselves in the reason."""
    if isinstance(text, (bytes, bytearray)):
        text = text.decode("utf-8", "replace")
    if not isinstance(text, str):
        return None, "the request is not a text"
    try:
        body = json.loads(text)
    except ValueError:
        return None, "the request is not JSON"
    if not isinstance(body, dict):
        return None, "the request is not one JSON object"
    missing = [key for key in REQUEST_FIELDS if key not in body]
    if missing:
        return None, f"the request does not name {', '.join(missing)}"
    for key in ("action", "project", "channel", "source"):
        value = body[key]
        if not isinstance(value, str) or not value.strip():
            return None, f"the request's {key} is not one non-empty string"
    arguments = body["arguments"]
    if not isinstance(arguments, dict):
        return None, "the request's arguments are not one mapping"
    return Request(body["action"], dict(arguments), body["project"], body["channel"], body["source"]), None


def _actions(decision):
    return tuple(getattr(decision, "actions", ()) or ())


def check(request, decision, line, project):
    """The one `Refusal` of a request against its decision and its inbox line, or None when every check passes. The
    checks run in one order: identity, project, channel, grant, arguments, and the reply path of the decision."""
    line = line or {}
    mid = str(line.get("id") or "")
    if str(request.source) != mid:
        return Refusal("identity", f"the request names source {request.source!r}, not the source message of the line {mid!r}")
    if not getattr(decision, "role", None):
        return Refusal("identity", "the caller has no role in this channel")
    if str(request.project) != str(project):
        return Refusal("project", f"the request names project {request.project!r}, not {project!r}")
    channel = str(line.get("channel") or "")
    if str(request.channel) != channel:
        return Refusal("channel", f"the request names channel {request.channel!r}, not the channel of the line {channel!r}")
    if request.action not in _actions(decision):
        return Refusal("grant", f"the role {decision.role!r} does not list action {request.action!r}")
    allowed = _ACTION_ARGUMENTS.get(request.action, frozenset())
    extra = sorted(set(request.arguments) - set(allowed))
    if extra:
        takes = ", ".join(sorted(allowed)) if allowed else "no argument"
        return Refusal("arguments", f"the {request.action} action takes {takes}, and the request names {', '.join(extra)}")
    missing = [name for name in _REQUIRED_ARGUMENTS.get(request.action, ()) if not request.arguments.get(name)]
    if missing:
        return Refusal("arguments", f"the {request.action} action needs {', '.join(missing)}")
    if not getattr(decision, "reply", None):
        return Refusal("reply", "the decision names no reply path, so no action runs in this channel")
    return None


def data_record(line, refusal):
    """The data record of one refusal: the fields of the inbox line, so the person and the text are kept, and one
    `error` that names the check and its reason."""
    line = line or {}
    record = {key: line.get(key) for key in ("id", "ts", "user", "channel", "language", "persona", "role", "text")}
    record["error"] = f"{refusal.check}: {refusal.reason}"
    return record


def scrub(text, guest=()):
    """The answer of the `logs` action: every guest line dropped, then every secret masked. A line is guest content when
    it holds a guest team id or a guest channel id. The caller reads those ids from the local Slack file, where a guest
    id stays (ADR-0177), and passes them in `guest`."""
    keep = [line for line in str(text).splitlines(keepends=True)
            if not any(str(g) and str(g) in line for g in guest)]
    return redact("".join(keep))


def execute(request, decision, line, project, status=None, logs=None, guest=(), board=None):
    """One request through every check, then its action. The action runs only when `check` returns None and its source
    is there; a refusal returns its data record and no reply. The reply carries the path the decision names, the
    channel of the line, and the answer text. `status` answers for the project, `logs` answers with the project's log
    after `scrub`, and both receive the one project the check proved. `board` answers the card and takes the approval
    and the move of `approve_and_move`; its rules live in `slackboard`."""
    refusal = check(request, decision, line, project)
    if refusal is None and request.action == STATUS and status is None:
        refusal = Refusal("source", "the status action needs a status source")
    if refusal is None and request.action == LOGS and logs is None:
        refusal = Refusal("source", "the logs action needs a logs source")
    if refusal is None and request.action == APPROVE_AND_MOVE and board is None:
        refusal = Refusal("source", "the approve_and_move action needs a board source")
    if refusal is not None:
        return Outcome(False, None, data_record(line, refusal))
    if request.action == APPROVE_AND_MOVE:
        text, why = slackboard.approve_and_move(board, request.arguments["card"], request.arguments["to"])
        if why is not None:
            return Outcome(False, None, data_record(line, Refusal("board", why)))
    elif request.action == STATUS:
        text = status(project)
    else:
        text = scrub(logs(project), guest)
    return Outcome(True, {"path": decision.reply, "channel": str((line or {}).get("channel") or ""), "text": text}, None)
