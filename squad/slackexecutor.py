"""The deterministic executor and the structured request (SPEC 2682, stories 23, 24, 29, 58 and 65; #2689, #2704).

The operator session never acts on Slack by itself. It emits one structured request: the action, the arguments, the
project, the channel and the id of the source message. `squad/slackpolicy.decide` gives one decision per event, and
this module checks the request against that decision before it runs the action. It checks the requester identity (the
source message of the inbox line, and the role the decision gives its caller), the project, the channel, the action (one
this build runs, so a role never grants another owner-only action), the grant (the action the caller's role lists), the
arguments (the ones the action takes, and no other, with the one the action needs), and the revision (a `slack.yaml` sha
the hub registry lists, when the caller passes that set). It runs the action only after every check passes.

A refused request is data: `execute` returns a record with the line's own fields and one `error` that names the check,
so the caller appends it beside the messages of people with no role. A success returns the reply path the decision
names (`bot`, `draft` or `send`) and the answer text; a decision with no reply path, like a `listen` channel, runs
nothing.

The five actions are the whole set. `status` and `logs` answer with the project the channel owns; `logs` masks every
secret and drops every guest line, so a client's text never reaches the reply (story 42). `request_work` starts an
issue or a run, and `request_investigation` starts an investigation or a spike: both pass the caller's text as one
argument to the source the caller supplies, so the text is a request inside the role's actions and never an instruction
of the agent.

A request in a `guest` channel never reaches an action: the policy gives it the kind `proposal`, and the executor refuses
it there, so `squad/slackproposal.py` can offer it to the owner before any work starts (story 61).

The `loop_control` action (#2705) runs no loop file of its own: it calls the hub loop switch the caller supplies, with
the hub's own action, so a role holder's pause, resume or drain passes the hub's state rule and the hub's audit
(ADR-0162). The reply confirms the state the role holder asked for.

The module reads no clock and calls no network: the caller supplies the status, log, work and investigation sources and
the hub loop switch, because those answers come from the repository, the board, the project's own log and the project's
own work path, and the loop is the hub's to write.

Guards: `tests/test_slack_executor.py`.
"""
import json
from typing import NamedTuple

from . import slackpolicy as S
from .redact import redact

STATUS = "status"
LOGS = "logs"
REQUEST_WORK = "request_work"
REQUEST_INVESTIGATION = "request_investigation"
LOOP_CONTROL = "loop_control"
ACTIONS = frozenset((STATUS, LOGS, REQUEST_WORK, REQUEST_INVESTIGATION, LOOP_CONTROL))
REQUEST_FIELDS = ("action", "arguments", "project", "channel", "source")
# The arguments each action takes. `status` and `logs` take none: they answer for the project the caller already named.
# Each request action takes the one text it acts on, and no other key, so a request never carries a grant or a setting.
# `loop_control` takes one `state`, the change the role holder asks for.
_ACTION_ARGUMENTS: dict[str, frozenset[str]] = {
    STATUS: frozenset(), LOGS: frozenset(),
    REQUEST_WORK: frozenset(("title",)), REQUEST_INVESTIGATION: frozenset(("question",)),
    LOOP_CONTROL: frozenset({"state"}),
}
# The one argument an action needs, so a request with nothing to act on starts nothing.
_ACTION_ARGUMENT = {REQUEST_WORK: "title", REQUEST_INVESTIGATION: "question"}
# The hub's loop switch is the one write path (ADR-0162): a Slack state maps to the hub's own action, and the hub applies
# the state rule, the audit and the supervisor. A pause and a drain are one hub drain, because a drain pauses dispatch
# and stops once the live work ends; a resume is a start. The two states stay distinct in the reply, so a person reads
# the state they asked for, and a future hub `pause` action changes this one map.
LOOP_STATES = {"pause": "drain", "resume": "start", "drain": "drain"}
# The reply of each state the role holder asks for, so an answer confirms the state the hub applied, never one the
# executor guessed and never the reply of a different state.
LOOP_REPLY = {
    "pause": "The loop pauses: it dispatches nothing new until a resume.",
    "resume": "The loop starts again: no new dispatch waits on a drain.",
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


def check(request, decision, line, project, listed=None):
    """The one `Refusal` of a request against its decision and its inbox line, or None when every check passes. The
    checks run in one order: revision, identity, project, channel, action, grant, the proposal kind, arguments, and the
    reply path of the decision.

    `listed` is the set of `slack.yaml` shas the hub registry lists (#2695), and the line names the revision its decision
    came from. A request on a revision no save lists grants nothing. None leaves the check out of force, as it is on a
    machine that names no registry."""
    line = line or {}
    if listed is not None:
        revision = str(line.get("revision") or "")
        if revision not in {str(sha) for sha in listed}:
            named = repr(revision) if revision else "no revision"
            return Refusal("revision", f"the line names {named}, which the hub registry does not list")
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
    if request.action not in ACTIONS:
        return Refusal("action", f"the {request.action!r} action exists for no role: a role grants only "
                                 f"{', '.join(sorted(ACTIONS))}, so an owner-only action stays with the owner")
    if request.action not in _actions(decision):
        return Refusal("grant", f"the role {decision.role!r} does not list action {request.action!r}")
    if getattr(decision, "kind", None) == S.PROPOSAL:
        return Refusal("proposal", "a request in a guest channel becomes a proposal to the owner: no action runs "
                                   "before the owner approves it (squad/slackproposal.py)")
    allowed = _ACTION_ARGUMENTS.get(request.action, frozenset())
    # The state is checked first, so one request that names both a bad state and an extra key reports the state that
    # decides the action does not apply, and the caller fixes the reading in one round-trip.
    if request.action == LOOP_CONTROL and request.arguments.get("state") not in LOOP_STATES:
        return Refusal("arguments", "the loop_control action takes the state pause, resume or drain, and the request "
                                    f"names {request.arguments.get('state')!r}")
    extra = sorted(set(request.arguments) - set(allowed))
    if extra:
        takes = f"takes only {', '.join(sorted(allowed))}" if allowed else "takes no argument"
        return Refusal("arguments", f"the {request.action} action {takes}, and the request names {', '.join(extra)}")
    needed = _ACTION_ARGUMENT.get(request.action)
    if needed and not str(request.arguments.get(needed) or "").strip():
        return Refusal("arguments", f"the {request.action} action needs one non-empty {needed}")
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
    # The hub's answer is read defensively: a body that is not one mapping refuses the action with the hub's answer and
    # never raises inside the request path.
    body = body if isinstance(body, dict) else {}
    if code != 200:
        return Refusal("hub", str(body.get("reason") or body.get("err") or f"the hub refused {action} with {code}"))
    return LOOP_REPLY[state]


def execute(request, decision, line, project, status=None, logs=None, guest=(), work=None, investigate=None,
            loop=None, listed=None):
    """One request through every check, then its action. The action runs only when `check` returns None and its source
    is there; a refusal returns its data record and no reply. The reply carries the path the decision names, the
    channel of the line, and the answer text.

    `status` answers for the project, `logs` answers with the project's log after `scrub`, and both receive the one
    project the check proved. `work` starts an issue or a run from `(project, title)` and `investigate` answers
    `(project, question)`: each receives the caller's text as one argument, so it acts only as a request. `loop` is the
    hub loop switch: it takes the hub action and the asker's name, and answers the hub's `(code, body)`."""
    refusal = check(request, decision, line, project, listed)
    sources = ((STATUS, status, "status"), (LOGS, logs, "logs"), (REQUEST_WORK, work, "work"),
               (REQUEST_INVESTIGATION, investigate, "investigation"), (LOOP_CONTROL, loop, "loop switch"))
    for action, source, name in sources:
        if refusal is None and request.action == action and source is None:
            refusal = Refusal("source", f"the {action} action needs a {name} source")
    if refusal is not None:
        return Outcome(False, None, data_record(line, refusal))
    if request.action == STATUS:
        text = status(project)
    elif request.action == LOGS:
        text = scrub(logs(project), guest)
    elif request.action == REQUEST_WORK:
        text = work(project, str(request.arguments["title"]))
    elif request.action == REQUEST_INVESTIGATION:
        text = investigate(project, str(request.arguments["question"]))
    else:
        text = _loop_control(request.arguments["state"], str((line or {}).get("user") or ""), loop)
        if isinstance(text, Refusal):
            return Outcome(False, None, data_record(line, text))
    return Outcome(True, {"path": decision.reply, "channel": str((line or {}).get("channel") or ""), "text": text}, None)
