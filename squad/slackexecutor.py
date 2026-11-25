"""The deterministic executor and the structured request (SPEC 2682, stories 23, 24, 29, 58 and 65; #2689, #2704).

The operator session never acts on Slack by itself. It emits one structured request: the action, the arguments, the
project, the channel and the id of the source message. `squad/slackpolicy.decide` gives one decision per event, and
this module checks the request against that decision before it runs the action. It checks the requester identity (the
source message of the inbox line, and the role the decision gives its caller), the project, the channel, the action (one
this build runs, so a role never grants another owner-only action), the grant (the action the caller's role lists), the
arguments (the ones the action takes, and no other, with the ones the action needs), and the revision (a `slack.yaml` sha
the hub registry lists, when the caller passes that set). It runs the action only after every check passes.

A refused request is data: `execute` returns a record with the line's own fields and one `error` that names the check,
so the caller appends it beside the messages of people with no role. A success returns the reply path the decision
names (`bot`, `draft` or `send`) and the answer text; a decision with no reply path, like a `listen` channel, runs
nothing.

The seven actions are the whole set. `status` and `logs` answer with the project the channel owns; `logs` masks every
secret and drops every guest line, so a client's text never reaches the reply (story 42). `request_work` starts an
issue or a run, and `request_investigation` starts an investigation or a spike: both pass the caller's text as one
argument to the source the caller supplies, so the text is a request inside the role's actions and never an instruction
of the agent. `approve_and_move` approves the review of one card and moves it between columns after Todo (stories 26
and 67): `squad/slackboard.py` holds its rules, and the action writes the board only when each passes.

A request in a `guest` channel never reaches an action: the policy gives it the kind `proposal`, and the executor refuses
it there, so `squad/slackproposal.py` can offer it to the owner before any work starts (story 61).

The `loop_control` action (#2705) runs no loop file of its own: it calls the hub loop switch the caller supplies, with
the hub's own action, so a role holder's pause, resume or drain passes the hub's state rule and the hub's audit
(ADR-0162). The hub loop switch has no pause action, so a pause is the hub's drain. Stop-now kills the live runs and
gates, so the executor refuses it by name whatever a role lists. The audit names a hub login, so the caller passes the
map of Slack user id to hub login and a request the map does not resolve is refused at the identity. The reply names
the state the hub reports in its answer; a body that names no state uses the copy of the action the hub itself reports,
and a body that names neither is a refusal.

The `deploy` action (#2724) runs the command the project identity declares, for the environment the identity declares.
The requester never supplies the command, and user text never sets the environment: a named environment is accepted
only when it is the identity's own. A role that lists the environment is the owner's standing grant for it, and a role
that does not list it is refused. A deploy to `prod` is refused by name, whether the request names it or the identity
declares it, because production stays with the owner.

The module reads no clock and calls no network: the caller supplies the status, log, work, investigation and board
sources, the hub loop switch and the deploy source, because those answers come from the repository, the board, the
project's own log, the project's own work path and its ratified deploy command, and the loop is the hub's to write.

Guards: `tests/test_slack_executor.py`, `tests/test_approve_and_move.py`.
"""
import json
from typing import NamedTuple

from . import slackboard
from . import slackpolicy as S
from .redact import redact

STATUS = "status"
LOGS = "logs"
REQUEST_WORK = "request_work"
REQUEST_INVESTIGATION = "request_investigation"
APPROVE_AND_MOVE = "approve_and_move"
LOOP_CONTROL = "loop_control"
DEPLOY = "deploy"
ACTIONS = frozenset((STATUS, LOGS, REQUEST_WORK, REQUEST_INVESTIGATION, APPROVE_AND_MOVE, LOOP_CONTROL, DEPLOY))
REQUEST_FIELDS = ("action", "arguments", "project", "channel", "source")
# The arguments each action takes. `status` and `logs` take none: they answer for the project the caller already named.
# Each request action takes the one text it acts on, and no other key, so a request never carries a grant or a setting.
# `approve_and_move` takes the card and the column, and no other key, so a request never names a spec or a breakdown.
# `loop_control` takes one `state`, the change the role holder asks for.
# `deploy` takes at most the one `environment` the requester names: the executor only accepts the environment the project
# identity declares, and the requester never names the command (SPEC 2682 S5, #2724).
_ACTION_ARGUMENTS: dict[str, frozenset[str]] = {
    STATUS: frozenset(), LOGS: frozenset(),
    REQUEST_WORK: frozenset(("title",)), REQUEST_INVESTIGATION: frozenset(("question",)),
    APPROVE_AND_MOVE: frozenset(("card", "to")), LOOP_CONTROL: frozenset({"state"}),
    DEPLOY: frozenset({"environment"}),
}
# The arguments an action cannot act without, so a request with nothing to act on starts nothing.
_ACTION_ARGUMENT = {REQUEST_WORK: ("title",), REQUEST_INVESTIGATION: ("question",),
                    APPROVE_AND_MOVE: ("card", "to")}
# The hub's loop switch is the one write path (ADR-0162): a Slack state maps to the hub's own action, and the hub applies
# the state rule, the audit and the supervisor. A resume is a start, and a drain is a drain. The hub switch has no pause
# action, so a pause is a drain (the owner's ruling on #2705).
LOOP_STATES = {"pause": "drain", "resume": "start", "drain": "drain"}
# The states the executor refuses by name, before the map above. Stop-now kills the live runs and the gates, so no Slack
# request reaches it whatever a role lists: the owner alone stops the loop there (ADR-0162).
FORBIDDEN_STATES = frozenset({"stop-now"})
# A deploy request never reaches production, whatever the role lists and whatever the project identity declares: the
# owner alone deploys there (SPEC 2682 S5, #2724).
FORBIDDEN_ENVIRONMENTS = frozenset({"prod"})
# The fallback reply of a hub action, used only when the hub's 200 body names the action it applied and no state. The
# reply names the hub's state whenever the body carries one, so an answer never states a change the hub did not report.
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


def hub_login(line, logins):
    """The hub login one inbox line's Slack user id maps to, or `""` when the caller's map names none. The hub audit
    records a hub login, so a loop_control request is refused at the identity when the map resolves no login."""
    user = str((line or {}).get("user") or "")
    return str((logins or {}).get(user) or "")


def _deploy_check(request, decision, identity):
    """The one `Refusal` of a `deploy` request, or None. The environment and the command come from the project identity,
    never from the request: a named environment is accepted only when it is the one the identity declares, and the
    request never names the command, which the arguments check already refuses. A deploy to `prod` is refused by name,
    whether the request names it or the identity declares it, so production stays with the owner. The role must list the
    environment the identity declares; a role that does not is refused at the grant (#2724)."""
    named = str(request.arguments.get("environment") or "").strip()
    if named.lower() in FORBIDDEN_ENVIRONMENTS:
        return Refusal("environment", f"a deploy to {named.lower()} is refused: production stays with the owner")
    environment, command = S.deploy_target(identity)
    if not environment:
        return Refusal("environment", "the project identity declares no deploy environment, and user text never sets it")
    if environment.lower() in FORBIDDEN_ENVIRONMENTS:
        return Refusal("environment", f"the project identity declares {environment!r}, and a deploy there is refused: "
                                      "production stays with the owner")
    if not command:
        return Refusal("command", "the project identity declares no deploy command, and the requester never supplies one")
    if named and named != environment:
        return Refusal("environment", f"the request names environment {named!r}, and the project identity declares "
                                      f"{environment!r}: user text never sets the environment")
    if environment not in tuple(getattr(decision, "deploy", ()) or ()):
        return Refusal("grant", f"the role {decision.role!r} does not list environment {environment!r}")
    return None


def check(request, decision, line, project, listed=None, logins=None, identity=None):
    """The one `Refusal` of a request against its decision and its inbox line, or None when every check passes. The
    checks run in one order: revision, identity, project, channel, action, grant, the proposal kind, arguments, the
    deploy rules, and the reply path of the decision.

    `identity` is the project row `slackpolicy.project_identity` returns: the `deploy` action reads its environment and
    its command from it, never from the request (#2724).

    `listed` is the set of `slack.yaml` shas the hub registry lists (#2695), and the line names the revision its decision
    came from. A request on a revision no save lists grants nothing. None leaves the check out of force, as it is on a
    machine that names no registry.

    `logins` maps a Slack user id to a hub login. The no-user and no-login refusals are loop-only on purpose.
    `loop_control` writes the loop switch, whose audit names the hub login of the person who asked, so the action is
    refused when the line names no user and when the map resolves no login for it. Every other action takes its actor
    from the role of the decision and writes no audit of its own, so a user-less line still runs it."""
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
    if request.action == LOOP_CONTROL and not str(line.get("user") or "").strip():
        return Refusal("identity", "the line names no user, and the hub audits the person who asks")
    if request.action == LOOP_CONTROL and not hub_login(line, logins):
        return Refusal("identity", f"the Slack user {str(line.get('user'))!r} maps to no hub login, and the hub audits "
                                   "a hub login")
    if str(request.project) != str(project):
        return Refusal("project", f"the request names project {request.project!r}, not {project!r}")
    channel = str(line.get("channel") or "")
    if str(request.channel) != channel:
        return Refusal("channel", f"the request names channel {request.channel!r}, not the channel of the line {channel!r}")
    if request.action not in ACTIONS:
        return Refusal("action", f"the {request.action!r} action exists for no role: a role grants only "
                                 f"{', '.join(sorted(ACTIONS))}, so an owner-only action stays with the owner")
    # The request's action must be one the decision's role grants. The request comes from the session's outbox line, so
    # a session that writes an action the sender's role never granted is refused here, before any command source runs.
    if request.action not in _actions(decision):
        return Refusal("grant", f"the role {decision.role!r} does not list action {request.action!r}")
    if getattr(decision, "kind", None) == S.PROPOSAL:
        return Refusal("proposal", "a request in a guest channel becomes a proposal to the owner: no action runs "
                                   "before the owner approves it (squad/slackproposal.py)")
    allowed = _ACTION_ARGUMENTS.get(request.action, frozenset())
    # One state check, before the extra-key and missing-key checks: one request that names both a bad state and an extra
    # key reports the state, which decides that the action does not apply, so the caller fixes it in one round-trip.
    # The forbidden state comes first and names itself: stop-now is a valid hub action that no Slack request takes, so
    # its refusal says why instead of listing the states the action does take.
    if request.action == LOOP_CONTROL:
        state = request.arguments.get("state")
        if isinstance(state, str) and state in FORBIDDEN_STATES:
            return Refusal("arguments", "the loop_control action never takes stop-now from Slack: it kills the live "
                                        "runs and gates, and only the owner stops the loop there")
        if not isinstance(state, str) or state not in LOOP_STATES:
            return Refusal("arguments", "the loop_control action takes the state pause, resume or drain, and the "
                                        f"request names {state!r}")
    extra = sorted(set(request.arguments) - set(allowed))
    if extra:
        takes = f"takes only {', '.join(sorted(allowed))}" if allowed else "takes no argument"
        return Refusal("arguments", f"the {request.action} action {takes}, and the request names {', '.join(extra)}")
    missing = [name for name in _ACTION_ARGUMENT.get(request.action, ())
               if not str(request.arguments.get(name) or "").strip()]
    if missing:
        return Refusal("arguments", f"the {request.action} action needs one non-empty {', '.join(missing)}")
    if request.action == DEPLOY:
        refusal = _deploy_check(request, decision, identity)
        if refusal is not None:
            return refusal
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
    the one `Refusal` the hub gave.

    `who` is the hub login the caller's map resolves for the line's Slack user id, never the raw Slack id, because the
    hub records a hub login as the person who asked in its audit. The switch is the caller's: the hub owns the state
    rule, the audit and the supervisor (ADR-0162), and the executor writes no loop file of its own."""
    action = LOOP_STATES[state]
    code, body = loop(action, who)
    # The hub's answer is read defensively: a body that is not one mapping refuses the action with the hub's answer and
    # never raises inside the request path.
    body = body if isinstance(body, dict) else {}
    if code != 200:
        return Refusal("hub", str(body.get("reason") or body.get("err") or f"the hub refused {action} with {code}"))
    # The reply names the state the hub's own body reports, verbatim, so an answer never states a change the hub did not
    # report. Only a body that names no state falls back to the copy of the action the hub itself reports, and a body
    # that names neither is a refusal: the requested action never stands in for the hub's answer.
    named = str(body.get("state") or "").strip()
    if named:
        return f"The loop is now {named}."
    applied = str(body.get("action") or "").strip()
    if not applied:
        return Refusal("hub", "the hub answered 200 with no loop state")
    return LOOP_REPLY.get(applied) or f"The hub accepted the {applied} action."


def execute(request, decision, line, project, status=None, logs=None, guest=(), work=None, investigate=None,
            listed=None, board=None, loop=None, logins=None, identity=None, deploy=None):
    """One request through every check, then its action. The action runs only when `check` returns None and its source
    is there; a refusal returns its data record and no reply. The reply carries the path the decision names, the
    channel of the line, and the answer text.

    `status` answers for the project, `logs` answers with the project's log after `scrub`, and both receive the one
    project the check proved. `work` starts an issue or a run from `(project, title)` and `investigate` answers
    `(project, question)`: each receives the caller's text as one argument, so it acts only as a request. `board`
    answers the card and takes the approval and the move of `approve_and_move`; its rules live in `slackboard`.
    `loop` is the hub loop switch: it takes the hub action and the asker's hub login, and answers the hub's
    `(code, body)`. `logins` maps a Slack user id to that hub login, and `loop_control` is refused when it resolves
    none. `identity` is the project row, and `deploy` answers `(project, environment, command)` with the environment and
    the command the identity declares, never ones the requester supplies (#2724)."""
    refusal = check(request, decision, line, project, listed, logins, identity)
    sources = ((STATUS, status, "status"), (LOGS, logs, "logs"), (REQUEST_WORK, work, "work"),
               (REQUEST_INVESTIGATION, investigate, "investigation"), (APPROVE_AND_MOVE, board, "board"),
               (LOOP_CONTROL, loop, "loop switch"), (DEPLOY, deploy, "deploy"))
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
    elif request.action == APPROVE_AND_MOVE:
        text, why = slackboard.approve_and_move(board, request.arguments["card"], request.arguments["to"])
        if why is not None:
            return Outcome(False, None, data_record(line, Refusal("board", why)))
    elif request.action == LOOP_CONTROL:
        text = _loop_control(str(request.arguments["state"]), hub_login(line, logins), loop)
        if isinstance(text, Refusal):
            return Outcome(False, None, data_record(line, text))
    elif request.action == DEPLOY:
        environment, command = S.deploy_target(identity)
        text = deploy(project, environment, command)
    else:
        # An action this build adds with no branch here is a refusal, never a call into the last source.
        return Outcome(False, None, data_record(line, Refusal("action", f"the {request.action!r} action has no handler "
                                                                        "in this build")))
    return Outcome(True, {"path": decision.reply, "channel": str((line or {}).get("channel") or ""), "text": text}, None)
