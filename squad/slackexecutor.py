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

The `loop_control` action (#2705) runs no loop file of its own: it calls the hub loop switch the caller supplies, with
the hub's own action, so a role holder's pause, resume or drain passes the hub's state rule and the hub's audit
(ADR-0162). The reply confirms the state the hub applied.

The module reads no clock and calls no network: the caller supplies the project status source, the log source and the
hub loop switch, because the status of a project comes from the repository and the board, the log comes from the
project's own log, and the loop is the hub's to write.

Guards: `tests/test_slack_executor.py`.
"""
import json
from typing import NamedTuple

from .redact import redact

STATUS = "status"
LOGS = "logs"
LOOP_CONTROL = "loop_control"
ACTIONS = frozenset((STATUS, LOGS, LOOP_CONTROL))
REQUEST_FIELDS = ("action", "arguments", "project", "channel", "source")
# The arguments each action takes. `status` and `logs` take none: they answer for the project the caller already named.
# `loop_control` takes one `state`, the change the role holder asks for.
_ACTION_ARGUMENTS: dict[str, frozenset[str]] = {STATUS: frozenset(), LOGS: frozenset(), LOOP_CONTROL: frozenset({"state"})}
# The hub's loop switch is the one write path (ADR-0162): a Slack state maps to the hub's own action, and the hub applies
# the state rule, the audit and the supervisor. A pause and a drain are one hub drain, because a drain pauses dispatch
# and stops once the live work ends; a resume is a start.
LOOP_STATES = {"pause": "drain", "resume": "start", "drain": "drain"}
# The reply of each hub action, so an answer confirms the state the hub applied, never one the executor guessed.
LOOP_REPLY = {
    "start": "The loop starts again: no new dispatch waits on a drain.",
    "drain": "The loop drains: it dispatches nothing new and stops when the live runs and gates finish.",
}


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
        return Refusal("arguments", f"the {request.action} action takes no argument, and the request names {', '.join(extra)}")
    if request.action == LOOP_CONTROL and request.arguments.get("state") not in LOOP_STATES:
        return Refusal("arguments", f"the loop_control action takes the state pause, resume or drain, and the request "
                                    f"names {request.arguments.get('state')!r}")
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


def _loop_control(state, who, loop):
    """One `loop_control` action through the hub loop switch, whose `(code, body)` answer it returns as a reply text or
    the one `Refusal` the hub gave. The switch is the caller's: the hub owns the state rule, the audit and the
    supervisor (ADR-0162), and the executor writes no loop file of its own."""
    action = LOOP_STATES[state]
    code, body = loop(action, who)
    if code != 200:
        body = body or {}
        return Refusal("hub", str(body.get("reason") or body.get("err") or f"the hub refused {action} with {code}"))
    applied = str((body or {}).get("action") or action)
    return LOOP_REPLY.get(applied, LOOP_REPLY.get(action, ""))


def execute(request, decision, line, project, status=None, logs=None, guest=(), loop=None):
    """One request through every check, then its action. The action runs only when `check` returns None and its source
    is there; a refusal returns its data record and no reply. The reply carries the path the decision names, the
    channel of the line, and the answer text. `status` answers for the project, `logs` answers with the project's log
    after `scrub`, and both receive the one project the check proved. `loop` is the hub loop switch: it takes the hub
    action and the asker's name, and answers the hub's `(code, body)`."""
    refusal = check(request, decision, line, project)
    if refusal is None and request.action == STATUS and status is None:
        refusal = Refusal("source", "the status action needs a status source")
    if refusal is None and request.action == LOGS and logs is None:
        refusal = Refusal("source", "the logs action needs a logs source")
    if refusal is None and request.action == LOOP_CONTROL and loop is None:
        refusal = Refusal("source", "the loop_control action needs the hub loop switch")
    if refusal is not None:
        return Outcome(False, None, data_record(line, refusal))
    if request.action == LOOP_CONTROL:
        text = _loop_control(request.arguments["state"], str((line or {}).get("user") or ""), loop)
        if isinstance(text, Refusal):
            return Outcome(False, None, data_record(line, text))
    else:
        text = status(project) if request.action == STATUS else scrub(logs(project), guest)
    return Outcome(True, {"path": decision.reply, "channel": str((line or {}).get("channel") or ""), "text": text}, None)
